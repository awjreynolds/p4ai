"""Pin and validate the LLVM Test Suite SQLite application on two Clang toolchains.

The validation phase builds and runs the upstream SMALL_PROBLEM_SIZE test only.
It records correctness and provenance; it does not collect performance timings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "sqlite-feasibility-v1.json"
DEFAULT_SOURCE = ROOT / ".cache" / "llvm-test-suite"
DEFAULT_OUTPUT = ROOT / "artifacts" / "sqlite-feasibility-v1"
PINNED_REVISION = "2834619c5f535a36498daffc470cf3f4ae12a4f9"
REMOTE = "https://github.com/llvm/llvm-test-suite.git"
SUBTREE = "MultiSource/Applications/sqlite3"
SPARSE_PATHS = (SUBTREE, "cmake", "tools", "litsupport", "utils")
GENERATED_INPUTS = tuple(f"test{index}.sql" for index in range(1, 16))
UPSTREAM_DEFINITIONS = (
    "STDC_HEADERS=1",
    "HAVE_SYS_TYPES_H=1",
    "HAVE_SYS_STAT_H=1",
    "HAVE_STDLIB_H=1",
    "HAVE_STRING_H=1",
    "HAVE_MEMORY_H=1",
    "HAVE_STRINGS_H=1",
    "HAVE_INTTYPES_H=1",
    "HAVE_STDINT_H=1",
    "HAVE_UNISTD_H=1",
    "SQLITE_OMIT_LOAD_EXTENSION=1",
    "SQLITE_THREADSAFE=0",
)
STATIC_INPUTS = (
    "commands",
    "sqlite3rc",
    "sqlite3.reference_output.small",
)
SOURCE_ROOT_FILES = ("CMakeLists.txt", "lit.cfg", "lit.site.cfg.in", "LICENSE.TXT")
HARNESS_TREES = ("cmake", "tools", "litsupport", "utils")
TIMEOUT_SECONDS = 1800


class FeasibilityError(RuntimeError):
    """The pinned application or its build provenance cannot be validated."""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return _sha256_bytes(payload.encode("utf-8"))


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FeasibilityError(f"cannot read JSON from {path}: {error}") from error


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _design(path: Path) -> dict[str, Any]:
    design = _load_json(path)
    if not isinstance(design, dict) or design.get("schema_version") != 1:
        raise FeasibilityError("unsupported SQLite feasibility design")
    source = design.get("source", {})
    build = design.get("build", {})
    target = design.get("target", {})
    toolchain = design.get("toolchain", {})
    verification = design.get("verification", {})
    sanitizers = design.get("sanitizers", {})
    if (
        source.get("repository") != REMOTE
        or source.get("revision") != PINNED_REVISION
        or source.get("subtree") != SUBTREE
        or source.get("sqlite_version") != "3.5.7"
    ):
        raise FeasibilityError("design source pin or SQLite version changed")
    if not set(("sqlite3.c", "shell.c", "smalltest.tcl", "commands", "sqlite3rc")) <= set(source.get("required_paths", [])):
        raise FeasibilityError("design omits a required application input")
    if not set(("LICENSE.TXT", f"{SUBTREE}/copyright-release.html")) <= set(source.get("license_paths", [])):
        raise FeasibilityError("design does not preserve both license notices")
    if (
        build.get("generator") != "Ninja"
        or build.get("configuration") != "Release"
        or build.get("release_cache") != "cmake/caches/O3.cmake"
        or build.get("subdirectories") != SUBTREE
        or build.get("small_problem_size") is not True
        or build.get("collect_compile_time") is not False
        or build.get("optimization_profile") != "upstream O3 without LTO"
        or not build.get("profile_deviation")
        or build.get("release_flags_are_from_upstream_cache") is not True
        or tuple(build.get("definitions_from_upstream_cmake", ())) != UPSTREAM_DEFINITIONS
    ):
        raise FeasibilityError("design changed the upstream small O3/no-LTO build contract")
    if (
        target.get("triple") != "arm64-apple-darwin"
        or target.get("architecture") != "arm64"
        or target.get("sdk") != "macosx"
        or target.get("minimum_os_version") != "14.0"
        or target.get("linker_resolution") != "xcrun --find ld"
    ):
        raise FeasibilityError("design native target settings are invalid")
    if (
        verification.get("reference_output") != "sqlite3.reference_output.small"
        or verification.get("comparator") != "upstream fpcmp-target"
        or verification.get("relative_tolerance") != "1.0e-9"
        or verification.get("stdin_file") != "commands"
        or verification.get("test_file") != "sqlite3.test"
        or verification.get("run_options") != ["-init", "sqlite3rc", ":memory:"]
        or verification.get("must_match_upstream_test_lines") is not True
    ):
        raise FeasibilityError("design changed the original small reference test")
    if (
        sanitizers.get("optional") is not True
        or sanitizers.get("flags") != ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        or sanitizers.get("run_same_small_workload") is not True
        or not isinstance(sanitizers.get("runtime_options"), dict)
        or sanitizers.get("failure_policy")
        != "record compiler/runtime failure as evidence; never patch upstream sources"
    ):
        raise FeasibilityError("design changed the optional sanitizer evidence contract")
    if (
        toolchain.get("pinned_clang") != ".cache/llvm-build/bin/clang"
        or toolchain.get("system_clang_resolution") != "xcrun --find clang"
        or toolchain.get("system_cxx_resolution") != "xcrun --find clang++"
        or toolchain.get("sdk_path_resolution") != "xcrun --sdk macosx --show-sdk-path"
        or toolchain.get("lit") != ".cache/llvm-build/bin/llvm-lit"
        or toolchain.get("ninja") != ".venv/bin/ninja"
    ):
        raise FeasibilityError("design changed the two requested compiler toolchains")
    if design.get("scope") != "correctness-only substrate qualification; no repair interventions or performance timing":
        raise FeasibilityError("design must remain a correctness-only feasibility gate")
    return design


def _run_capture(
    argv: list[str], *, cwd: Path | None = None, timeout: int = 30
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FeasibilityError(f"could not run {argv[0]}: {error}") from error


def _run_command(
    argv: list[str],
    *,
    cwd: Path,
    log_dir: Path,
    name: str,
    timeout: int = TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{name}.stdout"
    stderr_path = log_dir / f"{name}.stderr"
    return_code: int | None = None
    error: str | None = None
    stdout = b""
    stderr = b""
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            error = f"timeout after {timeout} seconds"
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            return_code = process.returncode
    except OSError as os_error:
        error = f"could not execute command: {os_error}"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    return {
        "argv": argv,
        "cwd": str(cwd),
        "return_code": return_code,
        "error": error,
        "stdout_path": str(stdout_path),
        "stdout_sha256": _sha256_bytes(stdout),
        "stderr_path": str(stderr_path),
        "stderr_sha256": _sha256_bytes(stderr),
    }


def _command_output(argv: list[str], *, cwd: Path | None = None) -> str:
    result = _run_capture(argv, cwd=cwd)
    output = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        reason = result.stderr.decode("utf-8", errors="replace").strip()
        raise FeasibilityError(f"command failed ({result.returncode}): {argv!r}: {reason}")
    return output


def _git(source: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = _run_capture(["git", "-C", str(source), *args], timeout=120)
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise FeasibilityError(f"git {' '.join(args)} failed: {stderr}")
    return result


def _tree_entries(source: Path, revision: str, path: str) -> list[dict[str, str]]:
    result = _git(source, "ls-tree", "-r", "-z", "--full-tree", revision, "--", path)
    entries = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        header, relpath = raw.split(b"\t", 1)
        mode, kind, object_id = header.decode("ascii").split()
        if kind != "blob":
            continue
        entries.append({"path": relpath.decode("utf-8"), "mode": mode, "git_blob": object_id})
    return entries


def _git_blob_oid(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _read_tracked_file(root: Path, entry: dict[str, str]) -> bytes:
    path = root / entry["path"]
    if entry["mode"] == "120000":
        if not path.is_symlink():
            raise FeasibilityError(f"pinned symlink is missing: {entry['path']}")
        content = os.fsencode(os.readlink(path))
    else:
        if not path.is_file():
            raise FeasibilityError(f"pinned source file is missing: {entry['path']}")
        content = path.read_bytes()
    if _git_blob_oid(content) != entry["git_blob"]:
        raise FeasibilityError(f"source file differs from the pinned Git blob: {entry['path']}")
    return content


def _snapshot_source(source: Path, design: dict[str, Any]) -> dict[str, Any]:
    source_row = design["source"]
    revision = source_row["revision"]
    if _command_output(["git", "-C", str(source), "rev-parse", "HEAD"]) != revision:
        raise FeasibilityError(f"test-suite checkout is not pinned at {revision}")
    status = _command_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"])
    if status:
        raise FeasibilityError("test-suite checkout has local changes; preserving and refusing to use it")

    app_entries = _tree_entries(source, revision, SUBTREE)
    if not app_entries:
        raise FeasibilityError("pinned SQLite subtree is empty")
    paths = {entry["path"] for entry in app_entries}
    required = {f"{SUBTREE}/{name}" for name in source_row["required_paths"]}
    if not required <= paths:
        raise FeasibilityError(f"pinned SQLite subtree is missing files: {sorted(required - paths)}")

    root_license = _git(source, "ls-tree", "-z", revision, "--", "LICENSE.TXT")
    if root_license.returncode != 0 or not root_license.stdout.strip():
        raise FeasibilityError("pinned test suite has no root LICENSE.TXT")
    license_header, license_path = root_license.stdout.rstrip(b"\0").split(b"\t", 1)
    mode, kind, object_id = license_header.decode("ascii").split()
    if kind != "blob" or license_path.decode("utf-8") != "LICENSE.TXT":
        raise FeasibilityError("pinned root license record is malformed")
    entries = [*app_entries, {"path": "LICENSE.TXT", "mode": mode, "git_blob": object_id}]

    harness_entries: list[dict[str, str]] = []
    for path in SOURCE_ROOT_FILES:
        if path == "LICENSE.TXT":
            continue
        row = _git(source, "ls-tree", "-z", revision, "--", path)
        if row.returncode != 0 or not row.stdout.strip():
            raise FeasibilityError(f"pinned build harness file is missing: {path}")
        header, name = row.stdout.rstrip(b"\0").split(b"\t", 1)
        mode, kind, object_id = header.decode("ascii").split()
        if kind != "blob" or name.decode("utf-8") != path:
            raise FeasibilityError(f"pinned build harness record is malformed: {path}")
        harness_entries.append({"path": path, "mode": mode, "git_blob": object_id})
    for tree in HARNESS_TREES:
        harness_entries.extend(_tree_entries(source, revision, tree))

    all_entries = {entry["path"]: entry for entry in [*entries, *harness_entries]}
    records = []
    for path, entry in sorted(all_entries.items()):
        content = _read_tracked_file(source, entry)
        records.append({
            **entry,
            "size_bytes": len(content),
            "sha256": _sha256_bytes(content),
        })

    for license_path in source_row["license_paths"]:
        if license_path not in all_entries:
            raise FeasibilityError(f"required license notice is missing: {license_path}")
    version = (source / SUBTREE / "VERSION").read_text(encoding="utf-8").strip()
    if version != source_row["sqlite_version"]:
        raise FeasibilityError(f"source SQLite version is {version}, expected {source_row['sqlite_version']}")

    return {
        "repository": REMOTE,
        "revision": revision,
        "subtree": SUBTREE,
        "sqlite_version": version,
        "files": records,
        "file_count": len(records),
        "tree_sha256": _fingerprint(records),
        "license_paths": list(source_row["license_paths"]),
    }


def _sparse_checkout(source: Path) -> None:
    source.mkdir(parents=True, exist_ok=True)
    if not (source / ".git").exists():
        if any(source.iterdir()):
            raise FeasibilityError(f"source path is nonempty but not a Git checkout: {source}")
        _run_required(["git", "init", "--quiet"], cwd=source)
    top = _command_output(["git", "-C", str(source), "rev-parse", "--show-toplevel"])
    if Path(top).resolve() != source.resolve():
        raise FeasibilityError("source cache is nested inside another Git checkout")
    origin = _git(source, "remote", "get-url", "origin", check=False)
    if origin.returncode:
        _run_required(["git", "-C", str(source), "remote", "add", "origin", REMOTE], cwd=ROOT)
    elif origin.stdout.decode("utf-8", errors="replace").strip() != REMOTE:
        raise FeasibilityError("source cache has a different Git origin")
    head = _git(source, "rev-parse", "--verify", "HEAD", check=False)
    current = head.stdout.decode("ascii", errors="replace").strip() if head.returncode == 0 else ""
    if current and current != PINNED_REVISION:
        raise FeasibilityError(f"source cache is at {current}, expected {PINNED_REVISION}; refusing to reset it")
    if current:
        status = _command_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"])
        if status:
            raise FeasibilityError("source cache has local changes; refusing to update it")
    _run_required(["git", "-C", str(source), "config", "core.sparseCheckout", "true"], cwd=ROOT)
    sparse = _git(source, "sparse-checkout", "init", "--cone", "--sparse-index", check=False)
    if sparse.returncode not in (0,):
        # Older Git versions may not support --sparse-index.
        _run_required(["git", "-C", str(source), "sparse-checkout", "init", "--cone"], cwd=ROOT)
    _run_required(["git", "-C", str(source), "sparse-checkout", "set", *SPARSE_PATHS], cwd=ROOT)
    if not current:
        _run_required(
            ["git", "-C", str(source), "fetch", "--depth=1", "--filter=blob:none", "origin", PINNED_REVISION],
            cwd=ROOT,
            timeout=600,
        )
        _run_required(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"], cwd=ROOT, timeout=600)


def _run_required(argv: list[str], *, cwd: Path, timeout: int = 120) -> str:
    result = _run_capture(argv, cwd=cwd, timeout=timeout)
    stdout = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise FeasibilityError(f"command failed ({result.returncode}): {argv!r}: {stderr}")
    return stdout.strip()


def _resolve_tools(design: dict[str, Any], root: Path) -> dict[str, Any]:
    toolchain = design["toolchain"]
    pinned = (root / toolchain["pinned_clang"]).resolve()
    lit = (root / toolchain["lit"]).resolve()
    ninja = (root / toolchain["ninja"]).resolve()
    cmake = shutil.which("cmake")
    xcrun = shutil.which("xcrun")
    if not cmake:
        raise FeasibilityError("CMake is not on PATH")
    if not xcrun:
        raise FeasibilityError("xcrun is not on PATH")
    if not ninja.is_file():
        raise FeasibilityError(f"pinned Ninja is missing: {ninja}")
    if not pinned.is_file():
        raise FeasibilityError(f"pinned LLVM Clang is missing: {pinned}")
    if not lit.is_file():
        raise FeasibilityError(f"pinned llvm-lit is missing: {lit}")

    sdk_name = design["target"]["sdk"]
    system_clang = Path(_command_output([xcrun, "--find", "clang"])).resolve()
    system_cxx = Path(_command_output([xcrun, "--find", "clang++"])).resolve()
    sdk = Path(_command_output([xcrun, "--sdk", sdk_name, "--show-sdk-path"])).resolve()
    sdk_version = _command_output([xcrun, "--sdk", sdk_name, "--show-sdk-version"])
    linker = Path(_command_output([xcrun, "--find", "ld"])).resolve()
    if not sdk.is_dir() or not linker.is_file() or not system_clang.is_file() or not system_cxx.is_file():
        raise FeasibilityError("Apple SDK or system toolchain path is unavailable")

    tools = {
        "cmake": str(Path(cmake).resolve()),
        "xcrun": str(Path(xcrun).resolve()),
        "ninja": str(ninja),
        "lit": str(lit),
        "sdk": str(sdk),
        "sdk_version": sdk_version,
        "linker": str(linker),
        "host_cc": str(system_clang),
        "host_cxx": str(system_cxx),
        "compilers": {
            "pinned_llvm": str(pinned),
            "system_apple": str(system_clang),
        },
    }
    for name, path_text in (
        ("cmake", tools["cmake"]),
        ("xcrun", tools["xcrun"]),
        ("ninja", tools["ninja"]),
        ("lit", tools["lit"]),
        ("linker", tools["linker"]),
        ("host_cc", tools["host_cc"]),
        ("host_cxx", tools["host_cxx"]),
        ("sdk", tools["sdk"]),
        ("pinned_llvm", tools["compilers"]["pinned_llvm"]),
        ("system_apple", tools["compilers"]["system_apple"]),
    ):
        path = Path(path_text)
        tools.setdefault("files", {})[name] = {
            "path": str(path),
            "sha256": _sha256_file(path) if path.is_file() else None,
        }
    for name, path_text in tools["compilers"].items():
        version = _command_output([path_text, "--version"])
        tools["files"][name]["version"] = version
    tools["files"]["sdk"]["version"] = sdk_version
    tools["files"]["host_cxx"]["version"] = _command_output([tools["host_cxx"], "--version"])
    linker_version = _run_capture([tools["linker"], "-v"])
    tools["files"]["linker"]["version"] = (
        (linker_version.stdout + linker_version.stderr).decode("utf-8", errors="replace").strip()
    )
    tools["files"]["linker"]["version_return_code"] = linker_version.returncode
    if "Apple clang version" not in tools["files"]["system_apple"]["version"]:
        raise FeasibilityError("xcrun did not resolve an Apple Clang compiler")
    return tools


def _cmake_command(
    design: dict[str, Any],
    *,
    source: Path,
    build_dir: Path,
    compiler: Path,
    tools: dict[str, Any],
    sanitizer: bool,
) -> list[str]:
    build = design["build"]
    target = design["target"]
    argv = [
        tools["cmake"],
        "-S",
        str(source),
        "-B",
        str(build_dir),
        "-G",
        build["generator"],
        "-C",
        str(source / build["release_cache"]),
        f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}",
        f"-DCMAKE_C_COMPILER={compiler}",
        f"-DCMAKE_CXX_COMPILER={tools['host_cxx']}",
        f"-DTEST_SUITE_HOST_CC={tools['host_cc']}",
        f"-DTEST_SUITE_LIT={tools['lit']}",
        f"-DTEST_SUITE_SUBDIRS={build['subdirectories']}",
        f"-DSMALL_PROBLEM_SIZE={'ON' if build['small_problem_size'] else 'OFF'}",
        f"-DTEST_SUITE_COLLECT_COMPILE_TIME={'ON' if build['collect_compile_time'] else 'OFF'}",
        "-DTEST_SUITE_RUN_BENCHMARKS=ON",
        f"-DCMAKE_OSX_ARCHITECTURES={target['architecture']}",
        f"-DCMAKE_OSX_SYSROOT={tools['sdk']}",
        f"-DCMAKE_OSX_DEPLOYMENT_TARGET={target['minimum_os_version']}",
        f"-DCMAKE_C_COMPILER_TARGET={target['triple']}",
        f"-DCMAKE_LINKER={tools['linker']}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    if sanitizer:
        sanitizer_flags = " ".join(design["sanitizers"]["flags"])
        argv.extend((
            f"-DCMAKE_C_FLAGS={sanitizer_flags}",
            "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined",
        ))
    return argv


def _verify_upstream_test(path: Path, design: dict[str, Any]) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    verify = design["verification"]
    expected_run = "RUN: cd %S ; %b/sqlite3 -init sqlite3rc :memory: < commands"
    expected_verify = (
        f"VERIFY: %b/fpcmp-target -r {verify['relative_tolerance']} %o "
        f"%S/{verify['reference_output']}"
    )
    lines = text.splitlines()
    missing = [expected for expected in (expected_run, expected_verify) if expected not in lines]
    return {
        "status": "passed" if not missing else "failed",
        "path": str(path),
        "sha256": _sha256_file(path),
        "expected_lines": [expected_run, expected_verify],
        "missing_lines": missing,
    }


def _generated_input_records(build_dir: Path) -> dict[str, Any]:
    app_build = build_dir / SUBTREE
    records = []
    missing = []
    for name in GENERATED_INPUTS:
        path = app_build / name
        if not path.is_file():
            missing.append(name)
            continue
        records.append({"path": name, "size_bytes": path.stat().st_size, "sha256": _sha256_file(path)})
    extra = sorted(
        path.name
        for path in app_build.glob("test*.sql")
        if path.name not in GENERATED_INPUTS
    )
    return {
        "status": "passed" if not missing and not extra else "failed",
        "expected_count": len(GENERATED_INPUTS),
        "files": records,
        "missing": missing,
        "unexpected": extra,
    }


def _compare_generated_inputs(variants: list[dict[str, Any]]) -> dict[str, Any]:
    if len(variants) < 2:
        return {"status": "failed", "reason": "at least two compiler variants are required"}
    by_variant: dict[str, dict[str, str]] = {}
    errors = []
    for variant in variants:
        generated = variant.get("generated_inputs", {})
        if generated.get("status") != "passed":
            errors.append({"variant": variant.get("name"), "reason": "generated inputs did not validate"})
            continue
        by_variant[variant.get("name", "unknown")] = {
            row["path"]: row["sha256"] for row in generated.get("files", [])
        }
    if len(by_variant) != len(variants):
        return {"status": "failed", "errors": errors}
    reference_name = variants[0].get("name", "unknown")
    reference = by_variant[reference_name]
    for name, inputs in by_variant.items():
        if set(inputs) != set(reference):
            errors.append({"variant": name, "reason": "generated input file set differs"})
            continue
        for path, expected_hash in reference.items():
            if inputs[path] != expected_hash:
                errors.append({
                    "variant": name,
                    "path": path,
                    "expected_sha256": expected_hash,
                    "actual_sha256": inputs[path],
                })
    return {
        "status": "passed" if not errors else "failed",
        "reference_variant": reference_name,
        "compared_files": sorted(reference),
        "errors": errors,
    }


def _copied_input_records(source_root: Path, build_dir: Path) -> dict[str, Any]:
    app_build = build_dir / SUBTREE
    records = []
    errors = []
    for name in STATIC_INPUTS:
        source = source_root / SUBTREE / name
        copied = app_build / name
        if not source.is_file() or not copied.is_file():
            errors.append({"path": name, "reason": "missing source or build input"})
            continue
        source_hash = _sha256_file(source)
        copied_hash = _sha256_file(copied)
        records.append({"path": name, "source_sha256": source_hash, "build_sha256": copied_hash})
        if source_hash != copied_hash:
            errors.append({"path": name, "reason": "build input differs from pinned source"})
    return {"status": "passed" if not errors else "failed", "files": records, "errors": errors}


def _compile_command_records(build_dir: Path, design: dict[str, Any], tools: dict[str, Any]) -> dict[str, Any]:
    path = build_dir / "compile_commands.json"
    if not path.is_file():
        return {"status": "failed", "reason": "compile_commands.json is missing", "path": str(path)}
    commands = _load_json(path)
    if not isinstance(commands, list):
        return {"status": "failed", "reason": "compile_commands.json root is not a list", "path": str(path)}
    entries = []
    needed_definitions = set(design["build"]["definitions_from_upstream_cmake"])
    for item in commands:
        file_path = Path(item.get("file", ""))
        if file_path.name not in {"sqlite3.c", "shell.c"}:
            continue
        arguments = item.get("arguments")
        command = item.get("command")
        if isinstance(arguments, list):
            text = " ".join(arguments)
        elif isinstance(command, str):
            text = command
        else:
            text = ""
        missing = [
            flag for flag in (
                "-O3", "-DNDEBUG", "-w", "-Werror=date-time",
                "-I.",
                f"--target={design['target']['triple']}",
                f"-arch {design['target']['architecture']}",
                f"-mmacosx-version-min={design['target']['minimum_os_version']}",
            ) if flag not in text
        ]
        for excluded in ("-flto", "-fomit-frame-pointer"):
            if excluded in text:
                missing.append(f"upstream O3 profile without {excluded}")
        missing.extend(f"-D{definition}" for definition in sorted(needed_definitions) if f"-D{definition}" not in text)
        if tools["sdk"] not in text:
            missing.append("selected macOS SDK path")
        entries.append({
            "file": str(file_path),
            "directory": item.get("directory"),
            "command": command if isinstance(command, str) else arguments,
            "missing_expected_flags": missing,
        })
    status = "passed" if len(entries) == 2 and all(not entry["missing_expected_flags"] for entry in entries) else "failed"
    return {"status": status, "path": str(path), "sha256": _sha256_file(path), "translation_units": entries}


def _variant(
    *,
    name: str,
    compiler: Path,
    design: dict[str, Any],
    source: Path,
    output: Path,
    tools: dict[str, Any],
    sanitizer: bool = False,
) -> dict[str, Any]:
    suffix = "-asan-ubsan" if sanitizer else ""
    variant_id = f"{name}{suffix}"
    build_dir = output / "build" / variant_id
    logs = output / "commands" / variant_id
    build_dir.mkdir(parents=True, exist_ok=True)
    compiler_version = tools["files"][name]["version"]
    result: dict[str, Any] = {
        "name": variant_id,
        "compiler": {
            "path": str(compiler),
            "sha256": _sha256_file(compiler),
            "version": compiler_version,
            "distribution": "Apple Clang" if name == "system_apple" else "pinned LLVM Clang",
        },
        "target": {
            "triple": design["target"]["triple"],
            "architecture": design["target"]["architecture"],
            "sdk_path": tools["sdk"],
            "sdk_version": tools["sdk_version"],
            "sdk_sha256": tools["files"]["sdk"]["sha256"],
            "linker_path": tools["linker"],
            "linker_sha256": tools["files"]["linker"]["sha256"],
            "minimum_os_version": design["target"]["minimum_os_version"],
        },
        "sanitizer": sanitizer,
        "sanitizer_runtime_options": (
            design["sanitizers"]["runtime_options"] if sanitizer else {}
        ),
        "build_directory": str(build_dir),
        "commands": {},
    }
    configure = _cmake_command(
        design,
        source=source,
        build_dir=build_dir,
        compiler=compiler,
        tools=tools,
        sanitizer=sanitizer,
    )
    result["commands"]["configure"] = _run_command(
        configure, cwd=ROOT, log_dir=logs, name="configure"
    )
    if result["commands"]["configure"]["return_code"] != 0:
        result["status"] = "failed"
        return result

    build_command = [
        tools["cmake"], "--build", str(build_dir), "--target", design["build"]["target"],
        "--parallel", "2", "--verbose",
    ]
    result["commands"]["build"] = _run_command(
        build_command, cwd=ROOT, log_dir=logs, name="build"
    )
    result["generated_inputs"] = _generated_input_records(build_dir)
    result["copied_inputs"] = _copied_input_records(source, build_dir)
    app_build = build_dir / SUBTREE
    binary = app_build / "sqlite3"
    test_file = app_build / design["verification"]["test_file"]
    result["compile_commands"] = _compile_command_records(build_dir, design, tools)
    result["upstream_test_contract"] = (
        _verify_upstream_test(test_file, design) if test_file.is_file()
        else {"status": "failed", "reason": "generated upstream test file is missing"}
    )
    result["artifacts"] = {
        "binary": {
            "path": str(binary),
            "sha256": _sha256_file(binary) if binary.is_file() else None,
        },
        "test_file": result["upstream_test_contract"].get("sha256"),
        "compile_commands": result["compile_commands"].get("sha256"),
        "release_cache_sha256": _sha256_file(source / design["build"]["release_cache"]),
    }
    if (
        result["commands"]["build"]["return_code"] != 0
        or result["generated_inputs"]["status"] != "passed"
        or result["copied_inputs"]["status"] != "passed"
        or result["compile_commands"]["status"] != "passed"
        or result["upstream_test_contract"]["status"] != "passed"
        or not binary.is_file()
    ):
        result["status"] = "failed"
        return result

    env = os.environ.copy()
    if sanitizer:
        env.update(design["sanitizers"]["runtime_options"])
    lit_command = [tools["lit"], "-sv", "--timeout=300", str(test_file)]
    result["commands"]["reference_test"] = _run_command(
        lit_command,
        cwd=build_dir,
        log_dir=logs,
        name="reference-test",
        timeout=TIMEOUT_SECONDS,
        env=env,
    )
    result["status"] = (
        "passed" if result["commands"]["reference_test"]["return_code"] == 0 else "failed"
    )
    return result


def prepare(
    design_path: Path = DEFAULT_DESIGN,
    source: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    design = _design(design_path)
    source = source.resolve()
    output = output.resolve()
    _sparse_checkout(source)
    source_snapshot = _snapshot_source(source, design)
    design_sha256 = _sha256_file(design_path)
    manifest = {
        "schema_version": 1,
        "status": "prepared",
        "design_path": str(design_path.resolve()),
        "design_sha256": design_sha256,
        "implementation_sha256": _sha256_file(Path(__file__).resolve()),
        "source_path": str(source),
        "source": source_snapshot,
        "build_scope": design["scope"],
    }
    manifest["preparation_sha256"] = _fingerprint(manifest)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "source-manifest.json"
    if manifest_path.exists():
        existing = _load_json(manifest_path)
        if existing != manifest:
            raise FeasibilityError(
                "existing source manifest has different inputs or implementation; use a new output directory"
            )
    else:
        _save_json(manifest_path, manifest)
    return manifest


def validate(
    design_path: Path = DEFAULT_DESIGN,
    source: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
    *,
    sanitizers: bool = False,
) -> dict[str, Any]:
    design = _design(design_path)
    prepared = prepare(design_path, source, output)
    source = source.resolve()
    output = output.resolve()
    tools = _resolve_tools(design, ROOT)
    run_key = _fingerprint({
        "design_sha256": prepared["design_sha256"],
        "source_sha256": prepared["source"]["tree_sha256"],
        "implementation_sha256": prepared["implementation_sha256"],
        "tool_hashes": tools["files"],
        "sanitizers": sanitizers,
    })[:16]
    run_root = output / "runs" / run_key
    run_root.mkdir(parents=True, exist_ok=True)
    variants = []
    for name, compiler_path in tools["compilers"].items():
        variants.append(_variant(
            name=name,
            compiler=Path(compiler_path),
            design=design,
            source=source,
            output=run_root,
            tools=tools,
        ))
    sanitizer_variants = []
    if sanitizers:
        for name, compiler_path in tools["compilers"].items():
            sanitizer_variants.append(_variant(
                name=name,
                compiler=Path(compiler_path),
                design=design,
                source=source,
                output=run_root,
                tools=tools,
                sanitizer=True,
            ))
    baseline_passed = all(row.get("status") == "passed" for row in variants)
    input_comparison = _compare_generated_inputs(variants)
    baseline_passed = baseline_passed and input_comparison["status"] == "passed"
    sanitizer_input_comparison = (
        _compare_generated_inputs([*variants, *sanitizer_variants]) if sanitizers else None
    )
    sanitizer_passed = (
        all(row.get("status") == "passed" for row in sanitizer_variants)
        and sanitizer_input_comparison["status"] == "passed"
        if sanitizers
        else None
    )
    report = {
        "schema_version": 1,
        "run_key": run_key,
        "status": "passed" if baseline_passed else "failed",
        "sanitizer_status": (
            "passed" if sanitizer_passed is True else "failed_or_unsupported" if sanitizers else "not_requested"
        ),
        "design_sha256": prepared["design_sha256"],
        "source_manifest_sha256": _sha256_file(output / "source-manifest.json"),
        "implementation_sha256": prepared["implementation_sha256"],
        "source": prepared["source"],
        "toolchain": tools,
        "baseline_variants": variants,
        "generated_input_comparison": input_comparison,
        "sanitizer_variants": sanitizer_variants,
        "sanitizer_input_comparison": sanitizer_input_comparison,
        "performance_measurements": 0,
    }
    report_path = run_root / "feasibility-report.json"
    report["report_path"] = str(report_path)
    _save_json(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "validate"), default="prepare")
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sanitizers", action="store_true", help="also build and run the same workload under ASan+UBSan")
    args = parser.parse_args(argv)
    try:
        result = (
            prepare(args.design.resolve(), args.source.resolve(), args.output.resolve())
            if args.phase == "prepare"
            else validate(
                args.design.resolve(),
                args.source.resolve(),
                args.output.resolve(),
                sanitizers=args.sanitizers,
            )
        )
    except (FeasibilityError, OSError, ValueError) as error:
        print(f"SQLite feasibility gate failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if result["status"] in {"prepared", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
