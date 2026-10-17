"""Bootstrap the pinned native LLVM compiler used by the project."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LLVM_REMOTE = "https://github.com/iree-org/llvm-project.git"
LLVM_COMMIT = "32d702efa29d0fdd02cff7b79dea8fdbbcf059d4"
PATCH_RELATIVE_PATH = Path("patches/llvm-regalloc-action-guard.patch")
PATCH_TARGETS = ("llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp",)
DEFAULT_BUILD_DIR = Path(".cache/llvm-build")
LOCAL_NINJA = Path(".venv/bin/ninja")


class BootstrapError(RuntimeError):
    """Raised when setup cannot proceed without risking existing work."""


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an argument-vector command without invoking a shell."""
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def _git(
    source: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", "-C", str(source), *arguments],
        check=check,
        capture_output=True,
    )


def resolve_project_path(root: Path, value: Path | None) -> Path:
    if value is None:
        raise ValueError("a path value is required")
    path = value.expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def default_source_path(root: Path) -> Path:
    """Prefer the already initialized IREE LLVM submodule; otherwise clone locally."""
    iree_source = root / ".cache/iree/third_party/llvm-project"
    if iree_source.exists():
        return iree_source.resolve()
    return (root / ".cache/llvm-project").resolve()


def resolve_source(root: Path, source: Path | None) -> Path:
    return resolve_project_path(root, source) if source else default_source_path(root)


def default_jobs(cpu_count: int | None = None) -> int:
    return min(cpu_count or os.cpu_count() or 1, 10)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def find_ninja(root: Path) -> str:
    """Use the project's uv-managed Ninja before any system installation."""
    local_ninja = root / LOCAL_NINJA
    if local_ninja.is_file() and os.access(local_ninja, os.X_OK):
        return str(local_ninja.resolve())
    found = shutil.which("ninja")
    if found:
        return found
    raise BootstrapError(
        f"Ninja was not found; install the project dependency with `uv sync` "
        f"or provide it on PATH (expected {local_ninja})"
    )


def find_cmake() -> str:
    found = shutil.which("cmake")
    if not found:
        raise BootstrapError("CMake was not found on PATH")
    return found


def _read_git_head(source: Path) -> str | None:
    result = _git(source, "rev-parse", "--verify", "HEAD", check=False)
    if result.returncode:
        return None
    return result.stdout.strip()


def prepare_checkout_directory(source: Path) -> None:
    """Ensure source is its own repository, even inside another Git worktree."""
    if source.exists() and not source.is_dir():
        raise BootstrapError(f"LLVM source path is not a directory: {source}")
    source.mkdir(parents=True, exist_ok=True)

    top = _git(source, "rev-parse", "--show-toplevel", check=False)
    if not (source / ".git").exists():
        if any(source.iterdir()):
            if (
                not top.returncode
                and Path(top.stdout.strip()).resolve() != source.resolve()
            ):
                raise BootstrapError(
                    f"Source path is inside another Git checkout, not its own root: {source}"
                )
            raise BootstrapError(
                f"LLVM source path is non-empty but is not a Git checkout: {source}"
            )
        run(["git", "-C", str(source), "init", "--quiet"], capture_output=True)
        top = _git(source, "rev-parse", "--show-toplevel", check=False)
    if top.returncode:
        raise BootstrapError(f"Could not read Git checkout root at {source}")
    if Path(top.stdout.strip()).resolve() != source.resolve():
        raise BootstrapError(
            f"Source path must be the root of the LLVM checkout: {source}"
        )


def ensure_pinned_checkout(source: Path) -> None:
    """Create a shallow checkout when absent; never move an existing HEAD."""
    prepare_checkout_directory(source)

    current_head = _read_git_head(source)
    if current_head:
        if current_head != LLVM_COMMIT:
            raise BootstrapError(
                f"LLVM checkout at {source} is {current_head}, expected {LLVM_COMMIT}; "
                "the script will not reset or change an existing checkout"
            )
        if not (source / "llvm/CMakeLists.txt").is_file():
            raise BootstrapError(
                f"Pinned checkout is missing llvm/CMakeLists.txt: {source}"
            )
        return

    status = _git(source, "status", "--porcelain", "--untracked-files=all")
    if status.stdout.strip():
        raise BootstrapError(
            f"Git checkout at {source} has no HEAD and contains user files; "
            "refusing to fetch over it"
        )
    origin = _git(source, "remote", "get-url", "origin", check=False)
    if origin.returncode:
        run(
            ["git", "-C", str(source), "remote", "add", "origin", LLVM_REMOTE],
            capture_output=True,
        )
    elif origin.stdout.strip() != LLVM_REMOTE:
        raise BootstrapError(
            f"Unborn checkout at {source} has unexpected origin "
            f"{origin.stdout.strip()!r}; expected {LLVM_REMOTE!r}"
        )

    run(
        ["git", "-C", str(source), "fetch", "--depth=1", "origin", LLVM_COMMIT],
        capture_output=True,
    )
    run(
        ["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
        capture_output=True,
    )
    current_head = _read_git_head(source)
    if current_head != LLVM_COMMIT:
        raise BootstrapError(
            f"Fetched LLVM revision {current_head!r}, expected {LLVM_COMMIT}"
        )
    if not (source / "llvm/CMakeLists.txt").is_file():
        raise BootstrapError(
            f"Pinned checkout is missing llvm/CMakeLists.txt: {source}"
        )


def _changed_tracked_paths(source: Path) -> set[str]:
    result = _git(source, "diff", "--name-only", "-z", "HEAD")
    return {path for path in result.stdout.split("\0") if path}


def _patch_targets(patch: Path) -> set[str]:
    targets = {
        line.removeprefix("+++ b/")
        for line in patch.read_text(encoding="utf-8").splitlines()
        if line.startswith("+++ b/")
    }
    if not targets:
        raise BootstrapError(f"Patch contains no ordinary file targets: {patch}")
    return targets


def _assert_guard_patch_applied(source: Path, patch: Path) -> set[str]:
    """Verify the checkout contains exactly the tracked guard patch."""
    targets = _patch_targets(patch)
    changed_paths = _changed_tracked_paths(source)
    if not changed_paths.issubset(targets):
        extra = ", ".join(sorted(changed_paths - targets))
        raise BootstrapError(f"LLVM checkout has unrelated tracked changes: {extra}")

    reverse_check = run(
        ["git", "apply", "--reverse", "--check", str(patch.resolve())],
        cwd=source,
        check=False,
        capture_output=True,
    )
    if reverse_check.returncode:
        raise BootstrapError("The required LLVM guard patch is not applied")

    current_diff = _git(source, "diff", "--binary", "HEAD", "--", *sorted(targets))
    expected_diff = patch.read_text(encoding="utf-8")
    if current_diff.stdout != expected_diff:
        raise BootstrapError(
            "The guard patch appears applied, but the LLVM checkout also has "
            "unknown changes in a patch target; refusing to continue"
        )
    return targets


def apply_guard_patch(source: Path, patch: Path) -> bool:
    """Apply the action guard once, rejecting unexpected tracked modifications.

    Returns True when the patch was applied during this invocation.
    """
    if not patch.is_file():
        raise BootstrapError(f"Required LLVM guard patch is missing: {patch}")
    _patch_targets(patch)
    changed_paths = _changed_tracked_paths(source)

    reverse_check = run(
        ["git", "apply", "--reverse", "--check", str(patch.resolve())],
        cwd=source,
        check=False,
        capture_output=True,
    )
    if reverse_check.returncode == 0:
        _assert_guard_patch_applied(source, patch)
        return False

    if changed_paths:
        paths = ", ".join(sorted(changed_paths))
        raise BootstrapError(
            f"LLVM checkout has unknown dirty hunks or unrecognized tracked changes: {paths}"
        )

    forward_check = run(
        ["git", "apply", "--check", str(patch.resolve())],
        cwd=source,
        check=False,
        capture_output=True,
    )
    if forward_check.returncode:
        details = (forward_check.stderr or forward_check.stdout).strip()
        raise BootstrapError(
            f"Guard patch does not apply to the pinned LLVM source: {details}"
        )
    run(
        ["git", "apply", str(patch.resolve())],
        cwd=source,
        capture_output=True,
    )
    reverse_after_apply = run(
        ["git", "apply", "--reverse", "--check", str(patch.resolve())],
        cwd=source,
        check=False,
        capture_output=True,
    )
    if reverse_after_apply.returncode:
        raise BootstrapError(
            "Guard patch application did not produce the expected diff"
        )
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_build_manifest(source: Path, build_dir: Path, patch: Path) -> Path:
    """Atomically record the pinned source, guard patch, and built tool hashes."""
    source = source.resolve()
    build_dir = build_dir.resolve()
    patch = patch.resolve()

    current_revision = _read_git_head(source)
    if current_revision != LLVM_COMMIT:
        raise BootstrapError(
            f"Cannot record build: LLVM checkout is {current_revision!r}, "
            f"expected {LLVM_COMMIT}"
        )
    targets = _assert_guard_patch_applied(source, patch)
    if targets != set(PATCH_TARGETS):
        raise BootstrapError(
            f"Unexpected guard patch targets: {', '.join(sorted(targets))}"
        )

    guard_source = source / PATCH_TARGETS[0]
    binaries: dict[str, dict[str, str]] = {}
    for name in ("clang", "llc", "FileCheck"):
        binary = build_dir / "bin" / name
        if not binary.is_file():
            raise BootstrapError(f"Built LLVM tool is missing: {binary}")
        binaries[name] = {"path": str(binary.resolve()), "sha256": _sha256(binary)}

    manifest = {
        "schema_version": 1,
        "source_revision": current_revision,
        "source_path": str(source),
        "guard_source_path": PATCH_TARGETS[0],
        "guard_source_sha256": _sha256(guard_source),
        "patch_sha256": _sha256(patch),
        "binaries": binaries,
        "build_config": {
            "generator": "Ninja",
            "build_type": "Release",
            "llvm_projects": ["clang"],
            "llvm_targets": ["AArch64", "BPF"],
            "assertions": True,
        },
    }

    if _read_git_head(source) != current_revision:
        raise BootstrapError("LLVM source revision changed while recording the build")
    _assert_guard_patch_applied(source, patch)
    if (
        _sha256(guard_source) != manifest["guard_source_sha256"]
        or _sha256(patch) != manifest["patch_sha256"]
    ):
        raise BootstrapError(
            "LLVM guard source or patch changed while recording the build"
        )

    manifest_path = build_dir / "p4ai-build.json"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=build_dir,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(manifest, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, manifest_path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return manifest_path


def configure_command(
    source: Path, build_dir: Path, ninja: str, cmake: str
) -> list[str]:
    return [
        cmake,
        "-S",
        str(source / "llvm"),
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={ninja}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DLLVM_ENABLE_PROJECTS=clang",
        "-DLLVM_TARGETS_TO_BUILD=AArch64;BPF",
        "-DLLVM_ENABLE_ASSERTIONS=ON",
        "-DLLVM_INCLUDE_TESTS=ON",
        "-DLLVM_BUILD_TESTS=OFF",
        "-DLLVM_INCLUDE_BENCHMARKS=OFF",
        "-DLLVM_BUILD_BENCHMARKS=OFF",
        "-DLLVM_INCLUDE_EXAMPLES=OFF",
        "-DLLVM_BUILD_EXAMPLES=OFF",
        "-DLLVM_ENABLE_ZLIB=OFF",
        "-DLLVM_ENABLE_ZSTD=OFF",
    ]


def build_command(build_dir: Path, jobs: int, cmake: str) -> list[str]:
    if jobs < 1:
        raise ValueError("jobs must be a positive integer")
    return [
        cmake,
        "--build",
        str(build_dir),
        "--target",
        "clang",
        "llc",
        "FileCheck",
        "--parallel",
        str(jobs),
    ]


def setup(source: Path, build_dir: Path, root: Path = ROOT) -> None:
    ensure_pinned_checkout(source)
    apply_guard_patch(source, root / PATCH_RELATIVE_PATH)
    command = configure_command(source, build_dir, find_ninja(root), find_cmake())
    print(f"Configuring LLVM {LLVM_COMMIT} from {source}", flush=True)
    run(command)


def build(source: Path, build_dir: Path, jobs: int, root: Path = ROOT) -> None:
    setup(source, build_dir, root)
    print(f"Building clang, llc, and FileCheck with {jobs} jobs", flush=True)
    run(build_command(build_dir, jobs, find_cmake()))
    manifest = write_build_manifest(source, build_dir, root / PATCH_RELATIVE_PATH)
    print(f"Wrote build evidence to {manifest}", flush=True)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "build"):
        subparser = commands.add_parser(name)
        subparser.add_argument(
            "--source",
            type=Path,
            help=(
                "LLVM checkout path (defaults to .cache/iree/third_party/llvm-project "
                "when present, otherwise .cache/llvm-project)"
            ),
        )
        subparser.add_argument(
            "--build-dir",
            type=Path,
            default=DEFAULT_BUILD_DIR,
            help="CMake build directory",
        )
        if name == "build":
            subparser.add_argument(
                "--jobs",
                type=positive_int,
                default=default_jobs(),
                help="parallel build jobs",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    source = resolve_source(ROOT, args.source)
    build_dir = resolve_project_path(ROOT, args.build_dir)
    try:
        if args.command == "setup":
            setup(source, build_dir)
        else:
            build(source, build_dir, args.jobs)
    except (BootstrapError, OSError, subprocess.CalledProcessError) as error:
        print(f"build_compiler.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
