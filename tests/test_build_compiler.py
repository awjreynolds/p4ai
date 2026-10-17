from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/build_compiler.py"
MODULE_SPEC = importlib.util.spec_from_file_location("build_compiler", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
build_compiler = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(build_compiler)


TARGET = build_compiler.PATCH_TARGETS[0]


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def make_checkout(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "llvm"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "config", "user.email", "compiler-test@example.invalid")
    git(source, "config", "user.name", "Compiler Test")
    target = source / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("int original_value = 1;\n", encoding="utf-8")
    git(source, "add", TARGET)
    git(source, "commit", "--quiet", "-m", "base")

    target.write_text(
        "int original_value = 1;\nint guarded_value = 2;\n", encoding="utf-8"
    )
    patch_text = git(source, "diff", "--binary", "HEAD", "--", TARGET)
    patch = tmp_path / "guard.patch"
    patch.write_text(patch_text, encoding="utf-8")
    git(source, "restore", TARGET)
    return source, patch


def test_source_selection_prefers_existing_iree_submodule(tmp_path: Path) -> None:
    preferred = tmp_path / ".cache/iree/third_party/llvm-project"
    preferred.mkdir(parents=True)
    assert build_compiler.default_source_path(tmp_path) == preferred.resolve()


def test_source_selection_uses_dedicated_clone_path_when_missing(
    tmp_path: Path,
) -> None:
    assert (
        build_compiler.default_source_path(tmp_path)
        == (tmp_path / ".cache/llvm-project").resolve()
    )


def test_explicit_relative_source_is_project_relative(tmp_path: Path) -> None:
    assert (
        build_compiler.resolve_source(tmp_path, Path("vendor/llvm"))
        == (tmp_path / "vendor/llvm").resolve()
    )


def test_new_nested_source_gets_its_own_git_repository(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "--quiet")
    source = project / ".cache/llvm-project"

    build_compiler.prepare_checkout_directory(source)

    assert git(source, "rev-parse", "--show-toplevel").strip() == str(source)


def test_configure_command_matches_native_build_contract(tmp_path: Path) -> None:
    command = build_compiler.configure_command(
        tmp_path / "src", tmp_path / "build", "/project/.venv/bin/ninja", "cmake"
    )
    assert command[:7] == [
        "cmake",
        "-S",
        str(tmp_path / "src/llvm"),
        "-B",
        str(tmp_path / "build"),
        "-G",
        "Ninja",
    ]
    assert "-DCMAKE_MAKE_PROGRAM=/project/.venv/bin/ninja" in command
    assert "-DCMAKE_BUILD_TYPE=Release" in command
    assert "-DLLVM_ENABLE_PROJECTS=clang" in command
    assert "-DLLVM_TARGETS_TO_BUILD=AArch64;BPF" in command
    assert "-DLLVM_ENABLE_ASSERTIONS=ON" in command
    assert "-DLLVM_INCLUDE_TESTS=ON" in command
    assert "-DLLVM_BUILD_TESTS=OFF" in command
    assert "-DLLVM_BUILD_BENCHMARKS=OFF" in command
    assert "-DLLVM_BUILD_EXAMPLES=OFF" in command
    assert "-DLLVM_ENABLE_ZLIB=OFF" in command
    assert "-DLLVM_ENABLE_ZSTD=OFF" in command


def test_build_targets_and_parallelism(tmp_path: Path) -> None:
    assert build_compiler.build_command(tmp_path / "build", 7, "cmake") == [
        "cmake",
        "--build",
        str(tmp_path / "build"),
        "--target",
        "clang",
        "llc",
        "FileCheck",
        "--parallel",
        "7",
    ]
    with pytest.raises(ValueError, match="positive integer"):
        build_compiler.build_command(tmp_path / "build", 0, "cmake")


def test_build_manifest_binds_pinned_source_patch_and_binary_hashes(
    tmp_path: Path, monkeypatch
) -> None:
    source, patch = make_checkout(tmp_path)
    revision = git(source, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(build_compiler, "LLVM_COMMIT", revision)
    assert build_compiler.apply_guard_patch(source, patch) is True

    build_dir = tmp_path / "build"
    bin_dir = build_dir / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("clang", "llc", "FileCheck"):
        (bin_dir / name).write_bytes(f"fake {name} binary".encode())

    manifest_path = build_compiler.write_build_manifest(source, build_dir, patch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_path == build_dir / "p4ai-build.json"
    assert manifest["schema_version"] == 1
    assert manifest["source_revision"] == revision
    assert manifest["source_path"] == str(source.resolve())
    assert manifest["guard_source_path"] == TARGET
    assert (
        manifest["guard_source_sha256"]
        == hashlib.sha256((source / TARGET).read_bytes()).hexdigest()
    )
    assert manifest["patch_sha256"] == hashlib.sha256(patch.read_bytes()).hexdigest()
    for name in ("clang", "llc", "FileCheck"):
        binary = bin_dir / name
        assert manifest["binaries"][name] == {
            "path": str(binary.resolve()),
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
    assert manifest["build_config"] == {
        "generator": "Ninja",
        "build_type": "Release",
        "llvm_projects": ["clang"],
        "llvm_targets": ["AArch64", "BPF"],
        "assertions": True,
    }
    assert list(build_dir.glob(".p4ai-build.json.*.tmp")) == []


def test_build_manifest_rejects_unpinned_source_and_missing_tools(
    tmp_path: Path, monkeypatch
) -> None:
    source, patch = make_checkout(tmp_path)
    build_dir = tmp_path / "build"
    build_dir.mkdir()

    with pytest.raises(build_compiler.BootstrapError, match="expected"):
        build_compiler.write_build_manifest(source, build_dir, patch)
    assert not (build_dir / "p4ai-build.json").exists()

    revision = git(source, "rev-parse", "HEAD").strip()
    monkeypatch.setattr(build_compiler, "LLVM_COMMIT", revision)
    assert build_compiler.apply_guard_patch(source, patch) is True
    with pytest.raises(build_compiler.BootstrapError, match="tool is missing"):
        build_compiler.write_build_manifest(source, build_dir, patch)
    assert not (build_dir / "p4ai-build.json").exists()


def test_build_records_manifest_only_after_successful_tool_build(
    tmp_path: Path, monkeypatch
) -> None:
    events: list[str] = []

    def fake_run(command, **_kwargs):
        assert command[1:3] == ["--build", str(tmp_path / "build")]
        events.append("build")

    monkeypatch.setattr(build_compiler, "setup", lambda *_args: events.append("setup"))
    monkeypatch.setattr(build_compiler, "run", fake_run)
    monkeypatch.setattr(build_compiler, "find_cmake", lambda: "cmake")
    monkeypatch.setattr(
        build_compiler,
        "write_build_manifest",
        lambda *_args: events.append("manifest") or tmp_path / "manifest.json",
    )

    build_compiler.build(tmp_path / "source", tmp_path / "build", 2, tmp_path)
    assert events == ["setup", "build", "manifest"]

    events.clear()

    def failed_run(_command, **_kwargs):
        events.append("build")
        raise subprocess.CalledProcessError(1, "cmake --build")

    monkeypatch.setattr(build_compiler, "run", failed_run)
    with pytest.raises(subprocess.CalledProcessError):
        build_compiler.build(tmp_path / "source", tmp_path / "build", 2, tmp_path)
    assert events == ["setup", "build"]


def test_default_jobs_caps_cpu_count_at_ten() -> None:
    assert build_compiler.default_jobs(16) == 10
    assert build_compiler.default_jobs(4) == 4
    assert build_compiler.default_jobs(0) >= 1


def test_invalid_existing_head_is_reported_without_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    calls: list[list[str]] = []

    def fake_run(command, *, cwd=None, check=True, capture_output=False):
        command = list(command)
        calls.append(command)
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(command, 0, str(source) + "\n", "")
        if command[-3:] == ["rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, "deadbeef\n", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(build_compiler, "run", fake_run)
    with pytest.raises(build_compiler.BootstrapError, match="will not reset"):
        build_compiler.ensure_pinned_checkout(source)
    assert not any("checkout" in call or "fetch" in call for call in calls)


def test_missing_checkout_fetches_only_the_pinned_commit(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    calls: list[list[str]] = []
    initialized = False
    checked_out = False

    def fake_run(command, *, cwd=None, check=True, capture_output=False):
        nonlocal initialized
        nonlocal checked_out
        command = list(command)
        calls.append(command)
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            if not initialized:
                return subprocess.CompletedProcess(command, 128, "", "not a repo")
            return subprocess.CompletedProcess(command, 0, str(source) + "\n", "")
        if command[-3:] == ["rev-parse", "--verify", "HEAD"]:
            if checked_out:
                return subprocess.CompletedProcess(
                    command, 0, f"{build_compiler.LLVM_COMMIT}\n", ""
                )
            return subprocess.CompletedProcess(command, 128, "", "no HEAD")
        if command[-3:] == ["remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(command, 2, "", "no origin")
        if command[-2:] == ["init", "--quiet"]:
            initialized = True
            return subprocess.CompletedProcess(command, 0, "", "")
        if "status" in command and "--porcelain" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[-4:-1] == ["fetch", "--depth=1", "origin"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[-3:] == ["checkout", "--detach", "FETCH_HEAD"]:
            checked_out = True
            (source / "llvm").mkdir()
            (source / "llvm/CMakeLists.txt").touch()
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(build_compiler, "run", fake_run)
    build_compiler.ensure_pinned_checkout(source)
    assert any(call[-2:] == ["init", "--quiet"] for call in calls)
    assert any(call[-4:-1] == ["remote", "add", "origin"] for call in calls)
    assert any(
        call[-4:] == ["fetch", "--depth=1", "origin", build_compiler.LLVM_COMMIT]
        for call in calls
    )
    assert not any("reset" in call for call in calls)


def test_patch_is_applied_once_and_recognized_afterward(tmp_path: Path) -> None:
    source, patch = make_checkout(tmp_path)
    assert build_compiler.apply_guard_patch(source, patch) is True
    assert build_compiler.apply_guard_patch(source, patch) is False
    assert "guarded_value" in (source / TARGET).read_text(encoding="utf-8")


def test_patch_refuses_extra_dirty_hunks_in_target_file(tmp_path: Path) -> None:
    source, patch = make_checkout(tmp_path)
    assert build_compiler.apply_guard_patch(source, patch) is True
    target = source / TARGET
    target.write_text(
        target.read_text(encoding="utf-8") + "int unreviewed_value = 3;\n",
        encoding="utf-8",
    )
    with pytest.raises(build_compiler.BootstrapError, match="unknown dirty hunks"):
        build_compiler.apply_guard_patch(source, patch)


def test_patch_refuses_unrelated_dirty_files_before_applying(tmp_path: Path) -> None:
    source, patch = make_checkout(tmp_path)
    other = source / "llvm/lib/CodeGen/Other.cpp"
    other.write_text("unreviewed\n", encoding="utf-8")
    git(source, "add", "llvm/lib/CodeGen/Other.cpp")
    with pytest.raises(
        build_compiler.BootstrapError, match="unrecognized tracked changes"
    ):
        build_compiler.apply_guard_patch(source, patch)
    assert "guarded_value" not in (source / TARGET).read_text(encoding="utf-8")


def test_patch_context_mismatch_fails_without_modifying_source(tmp_path: Path) -> None:
    source, patch = make_checkout(tmp_path)
    patch.write_text(
        patch.read_text(encoding="utf-8").replace("original_value", "different_name"),
        encoding="utf-8",
    )
    with pytest.raises(build_compiler.BootstrapError, match="does not apply"):
        build_compiler.apply_guard_patch(source, patch)
    assert "guarded_value" not in (source / TARGET).read_text(encoding="utf-8")
