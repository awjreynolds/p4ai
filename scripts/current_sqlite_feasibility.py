"""Qualify the pinned SQLite 3.54.1 amalgamation on the historical workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from scripts import sqlite_feasibility as historical

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "current-sqlite-feasibility-v1.json"
DEFAULT_ARCHIVE = ROOT / ".cache" / "current-sqlite" / "sqlite-autoconf-3540100.tar.gz"
DEFAULT_SOURCE = ROOT / ".cache" / "current-sqlite" / "source"
DEFAULT_OUTPUT = ROOT / "artifacts" / "current-sqlite-feasibility-v1"
EXPECTED_ARCHIVE_SHA3 = "b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4"
EXPECTED_SOURCE_ID = "2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76"
INPUT_FILES = tuple(["commands", "sqlite3rc", *(f"test{i}.sql" for i in range(1, 16))])
SOURCE_UNITS = ("sqlite3.c", "shell.c")
PINNED_NATIVE_HELPERS = {
    "timeit_target": "45f9310f83afe6426e88a3f5b40448c4273054013df12a2eddb37c34e10d804a",
    "fpcmp_target": "142297341afa73b9a03257b2de1e4615d20b20f18ca61673410fe186a74b9e6c",
}


class CurrentSQLiteError(RuntimeError):
    """Current-source evidence failed a frozen provenance or correctness gate."""


def _sha3(data: bytes) -> str:
    return hashlib.sha3_256(data).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fingerprint(value: Any) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CurrentSQLiteError(f"cannot read {path}: {error}") from error


def _save(path: Path, value: Any) -> None:
    historical._save_json(path, value)


def _select_historical_report(
    design: dict[str, Any],
    report_override: Path | None,
    output: Path,
    *,
    allow_prepared_override: bool = False,
) -> tuple[Path, str, dict[str, Any]]:
    pinned = (ROOT / design["historical_evidence"]["report"]).resolve()
    if report_override is not None and report_override.is_symlink():
        raise CurrentSQLiteError(f"historical SQLite report must not be a symlink: {report_override}")
    report_path = (report_override or pinned).resolve()
    if report_path.is_symlink() or not report_path.is_file():
        raise CurrentSQLiteError(f"historical SQLite report is missing or unsafe: {report_path}")
    report_hash = historical._sha256_file(report_path)
    explicit_override = report_override is not None
    if not explicit_override and (report_path != pinned or report_hash != design["historical_evidence"]["report_sha256"]):
        raise CurrentSQLiteError("pinned historical SQLite report is missing or changed")

    effective = json.loads(json.dumps(design))
    effective_row = effective["historical_evidence"]
    effective_row["report"] = (
        str(report_path) if explicit_override else design["historical_evidence"]["report"]
    )
    effective_row["report_sha256"] = report_hash
    effective_row["report_selection"] = {
        "mode": "explicit_override" if explicit_override else "pinned_default",
        "path": str(report_path),
        "sha256": report_hash,
    }

    if explicit_override:
        output_path = output.resolve()
        if output_path == DEFAULT_OUTPUT.resolve():
            raise CurrentSQLiteError("an explicit historical report requires a new --output directory")
        if output_path.exists() and any(output_path.iterdir()):
            manifest_path = output_path / "prepared-manifest.json"
            if not allow_prepared_override or not manifest_path.is_file():
                raise CurrentSQLiteError("an explicit historical report requires a new, empty --output directory")
            prior = _json(manifest_path)
            selected = prior.get("effective_design", {}).get("historical_evidence", {}).get("report_selection", {})
            if selected != effective_row["report_selection"]:
                raise CurrentSQLiteError("existing output was prepared for a different historical report")
    return report_path, report_hash, effective


def _check_helper_pins(helper_records: dict[str, Any], *, explicit_override: bool) -> None:
    if explicit_override:
        return
    for name, expected in PINNED_NATIVE_HELPERS.items():
        if helper_records[name]["sha256"] != expected:
            raise CurrentSQLiteError(f"historical reference helper differs from its pinned hash: {name}")


def _verified_native_helpers(reference_variant: dict[str, Any], *, explicit_override: bool) -> dict[str, Any]:
    records: dict[str, Any] = {}
    for name in ("timeit_target", "fpcmp_target"):
        entry = reference_variant["artifacts"][name]
        path = Path(entry["path"])
        digest = historical._sha256_file(path) if path.is_file() else None
        if digest != entry["sha256"]:
            raise CurrentSQLiteError(f"historical native helper changed: {name}")
        records[name] = {"path": str(path), "sha256": digest}
    _check_helper_pins(records, explicit_override=explicit_override)
    return records


def _freeze_input(target: Path, data: bytes, name: str) -> None:
    if target.is_symlink():
        raise CurrentSQLiteError(f"frozen workload input must not be a symlink: {name}")
    if target.exists() and target.read_bytes() != data:
        raise CurrentSQLiteError(f"previously frozen workload input changed: {name}")
    if not target.exists():
        target.write_bytes(data)


def _exit_code(phase: str, result: dict[str, Any]) -> int:
    if phase == "prepare":
        return 0 if result.get("status") == "prepared" else 1
    return 0 if result.get("qualified_for_repair") is True else 1


def _design(path: Path) -> dict[str, Any]:
    design = _json(path)
    source = design.get("source", {})
    historical_row = design.get("historical_evidence", {})
    workload = design.get("workload", {})
    verification = design.get("verification", {})
    build = design.get("build", {})
    toolchains = design.get("toolchains", {})
    if (
        design.get("schema_version") != 1
        or design.get("experiment") != "current-sqlite-substrate-feasibility"
        or design.get("scope") != "qualify SQLite 3.54.1 source compatibility and sanitizer behavior for a bounded compiler-repair experiment; no optimization claim or performance measurement"
    ):
        raise CurrentSQLiteError("unsupported current-SQLite feasibility design")
    if (
        source.get("version") != "3.54.1"
        or source.get("archive_url") != "https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz"
        or source.get("source_id") != EXPECTED_SOURCE_ID
        or source.get("archive_sha3_256") != EXPECTED_ARCHIVE_SHA3
        or set(source.get("members", {})) != {"VERSION", "sqlite3.c", "sqlite3.h", "shell.c", "README.txt", "notices/autosetup-LICENSE"}
        or source.get("source_id_is_required_in") != ["sqlite3.c", "sqlite3.h"]
        or source.get("preserve_embedded_sqlite_notices") is not True
    ):
        raise CurrentSQLiteError("design changed the official SQLite source pins")
    if (
        historical_row.get("reference_variant") != "pinned_llvm"
        or historical_row.get("report") != "artifacts/sqlite-feasibility-v1-timeit-direct/runs/ce9b9faec60990af/feasibility-report.json"
        or historical_row.get("report_sha256") != "cd9af4922247abade8af9adbd126caa4dd11c166ad80f30773b341cf97afa9aa"
        or historical_row.get("require_baseline_variants") != ["pinned_llvm", "system_apple"]
        or historical_row.get("require_historical_status") != "passed"
        or historical_row.get("reuse_compile_flags") != "historical compile_commands.json after verifying both compiler rows have identical flags"
        or historical_row.get("reuse_native_helpers") is not True
    ):
        raise CurrentSQLiteError("design changed the historical evidence contract")
    if (
        workload.get("input_files") != ["commands", "sqlite3rc", *(f"test{i}.sql" for i in range(1, 16))]
        or workload.get("commands_sha256") != "5ce3a4d87a56565a81e0ff4365cd164b2744d1a446b3e629a3020f9358f4f519"
        or workload.get("initialization_sha256") != historical._sha256_bytes(b"")
        or workload.get("run_options") != ["-init", "sqlite3rc", ":memory:"]
        or workload.get("stdin_file") != "commands"
    ):
        raise CurrentSQLiteError("design changed the verified historical workload")
    if (
        verification.get("reference_output") != "sqlite3.reference_output.small"
        or verification.get("reference_sha256") != "70abd085bdbb8b60cfe400bf34b91d05ef1c2af923100a109c757ce51489578c"
        or verification.get("comparator") != "historical fpcmp-target"
        or verification.get("relative_tolerance") != "1.0e-9"
        or verification.get("preserve_exact_output") is not True
        or verification.get("require_application_exit_zero") is not True
    ):
        raise CurrentSQLiteError("design changed the frozen reference gate")
    if (
        build.get("source_pair") != list(SOURCE_UNITS)
        or build.get("matching_header") != "sqlite3.h"
        or build.get("reuse_shared_historical_flags") is not True
        or build.get("required_definitions") != ["-DSQLITE_OMIT_LOAD_EXTENSION=1", "-DSQLITE_THREADSAFE=0"]
        or build.get("required_optimization_flags") != ["-DNDEBUG", "-O3"]
        or build.get("forbidden_flags") != ["-flto", "-fomit-frame-pointer"]
        or build.get("link_flags") != ["-Wl,-search_paths_first", "-Wl,-headerpad_max_install_names"]
        or build.get("execute_configure") is not False
    ):
        raise CurrentSQLiteError("design changed the bounded direct-build contract")
    if (
        toolchains.get("baseline") != ["pinned_llvm", "system_apple"]
        or toolchains.get("sanitizer") != "system_apple"
        or toolchains.get("sanitizer_flags") != ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        or toolchains.get("sanitizer_runtime_options") != {"ASAN_OPTIONS": "halt_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"}
        or toolchains.get("pinned_llvm_sanitizer", {}).get("status") != "known_unavailable"
    ):
        raise CurrentSQLiteError("design changed the compiler and sanitizer matrix")
    return design


def _stage_verified_archive(archive_path: Path, source_dir: Path, design: dict[str, Any]) -> dict[str, Any]:
    if not archive_path.is_file():
        raise CurrentSQLiteError(f"verified SQLite archive is missing: {archive_path}")
    archive_bytes = archive_path.read_bytes()
    archive_hash = _sha3(archive_bytes)
    if archive_hash != design["source"]["archive_sha3_256"]:
        raise CurrentSQLiteError(f"SQLite archive SHA3-256 mismatch: {archive_hash}")
    extracted: dict[str, bytes] = {}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for relative, spec in design["source"]["members"].items():
                safe = PurePosixPath(relative)
                if safe.is_absolute() or ".." in safe.parts or not safe.parts:
                    raise CurrentSQLiteError(f"unsafe staging path in design: {relative}")
                try:
                    member = archive.getmember(spec["member"])
                except KeyError as error:
                    raise CurrentSQLiteError(f"archive member is missing: {spec['member']}") from error
                if not member.isfile():
                    raise CurrentSQLiteError(f"archive member is not a regular file: {spec['member']}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise CurrentSQLiteError(f"cannot read archive member: {spec['member']}")
                data = stream.read()
                digest = _sha3(data)
                if digest != spec["sha3_256"]:
                    raise CurrentSQLiteError(f"SQLite archive member SHA3-256 mismatch: {relative}")
                extracted[relative] = data
    except (tarfile.TarError, OSError) as error:
        raise CurrentSQLiteError(f"cannot inspect verified SQLite archive: {error}") from error

    if extracted["VERSION"].decode("ascii").strip() != design["source"]["version"]:
        raise CurrentSQLiteError("SQLite VERSION does not match the pinned release")
    for name in design["source"]["source_id_is_required_in"]:
        if design["source"]["source_id"].encode() not in extracted[name]:
            raise CurrentSQLiteError(f"SQLite source ID is missing from {name}")
    for name in SOURCE_UNITS + ("sqlite3.h",):
        if b"disclaims copyright" not in extracted[name][:8192].lower():
            raise CurrentSQLiteError(f"embedded SQLite notice is missing from {name}")

    records = {
        relative: {"size_bytes": len(data), "sha3_256": _sha3(data), "sha256": _sha256(data)}
        for relative, data in sorted(extracted.items())
    }
    manifest = {
        "archive_path": str(archive_path.resolve()),
        "archive_size_bytes": len(archive_bytes),
        "archive_sha3_256": archive_hash,
        "version": design["source"]["version"],
        "source_id": design["source"]["source_id"],
        "files": records,
        "embedded_notices_checked": list(SOURCE_UNITS + ("sqlite3.h",)),
    }
    manifest["source_manifest_sha256"] = _fingerprint(manifest)
    if source_dir.is_symlink():
        raise CurrentSQLiteError(f"source staging path must not be a symlink: {source_dir}")
    if source_dir.exists():
        prior_path = source_dir / "source-manifest.json"
        if not prior_path.is_file() or _json(prior_path) != manifest:
            raise CurrentSQLiteError(f"existing source staging differs; refusing to overwrite: {source_dir}")
        staged = [path.relative_to(source_dir).as_posix() for path in source_dir.rglob("*") if path.is_file()]
        expected_staged = sorted([*records, "source-manifest.json"])
        if sorted(staged) != expected_staged or any(path.is_symlink() for path in source_dir.rglob("*")):
            raise CurrentSQLiteError("existing source staging contains unexpected or unsafe paths")
        for relative, spec in records.items():
            path = source_dir / relative
            if not path.is_file() or _sha3(path.read_bytes()) != spec["sha3_256"]:
                raise CurrentSQLiteError(f"staged source file changed: {relative}")
        return manifest

    temporary = source_dir.with_name(source_dir.name + ".staging")
    if temporary.exists():
        raise CurrentSQLiteError(f"stale source staging exists; inspect before continuing: {temporary}")
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True)
    for relative, data in extracted.items():
        path = temporary.joinpath(*PurePosixPath(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    historical._save_json(temporary / "source-manifest.json", manifest)
    temporary.rename(source_dir)
    return manifest


def _variant_rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in report.get("baseline_variants", [])}


def _command_argv(row: dict[str, Any]) -> list[str]:
    value = row.get("command") or row.get("arguments")
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return value
    raise CurrentSQLiteError("historical compiler command is malformed")


def _normalized_compile_signature(argv: list[str]) -> list[str]:
    normalized = list(argv[1:])
    try:
        normalized[normalized.index("-o") + 1] = "<object>"
        normalized[normalized.index("-c") + 1] = "<source>"
    except (ValueError, IndexError) as error:
        raise CurrentSQLiteError("historical compiler command cannot be normalized") from error
    return normalized


def _historical_evidence(
    design: dict[str, Any], output: Path, report_path: Path, expected_report_hash: str, *, report_override: bool
) -> dict[str, Any]:
    hist_design = historical._design(historical.DEFAULT_DESIGN)
    if not report_path.is_file() or historical._sha256_file(report_path) != expected_report_hash:
        raise CurrentSQLiteError("latest historical SQLite report is missing or changed")
    report = historical._load_json(report_path)
    if report.get("status") != design["historical_evidence"]["require_historical_status"]:
        raise CurrentSQLiteError("historical baseline gate did not pass")
    if historical._sha256_file(historical.DEFAULT_DESIGN) != report.get("design_sha256"):
        raise CurrentSQLiteError("historical design no longer matches its report")
    if historical._sha256_file(Path(historical.__file__)) != report.get("implementation_sha256"):
        raise CurrentSQLiteError("historical runner changed after its report")
    source_snapshot = historical._snapshot_source(historical.DEFAULT_SOURCE, hist_design)
    if source_snapshot["tree_sha256"] != report.get("source", {}).get("tree_sha256"):
        raise CurrentSQLiteError("historical source tree changed after its report")
    tools = historical._resolve_tools(hist_design, ROOT)
    if tools["files"] != report.get("toolchain", {}).get("files"):
        raise CurrentSQLiteError("historical compiler, SDK, linker, or build tools changed")
    variants = _variant_rows(report)
    expected_variants = design["historical_evidence"]["require_baseline_variants"]
    if sorted(variants) != sorted(expected_variants):
        raise CurrentSQLiteError("historical report has an unexpected baseline set")
    compile_commands: dict[str, dict[str, list[str]]] = {}
    for name in expected_variants:
        variant = variants[name]
        build = Path(variant["build_directory"])
        if variant.get("status") != "passed" or variant.get("generated_inputs", {}).get("status") != "passed" or variant.get("copied_inputs", {}).get("status") != "passed" or variant.get("compile_commands", {}).get("status") != "passed":
            raise CurrentSQLiteError(f"historical baseline evidence failed for {name}")
        if historical._generated_input_records(build) != variant["generated_inputs"]:
            raise CurrentSQLiteError(f"historical generated SQL changed for {name}")
        if historical._copied_input_records(historical.DEFAULT_SOURCE, build) != variant["copied_inputs"]:
            raise CurrentSQLiteError(f"historical static inputs changed for {name}")
        compile_path = Path(variant["compile_commands"]["path"])
        if historical._sha256_file(compile_path) != variant["compile_commands"]["sha256"]:
            raise CurrentSQLiteError(f"historical compile_commands.json changed for {name}")
        raw = json.loads(compile_path.read_text(encoding="utf-8"))
        compile_commands[name] = {}
        for item in raw:
            source_name = Path(item["file"]).name
            if source_name in SOURCE_UNITS:
                argv = _command_argv(item)
                if argv[0] != tools["compilers"][name]:
                    raise CurrentSQLiteError(f"compiler executable differs from historical report for {name}")
                compile_commands[name][source_name] = argv
        if set(compile_commands[name]) != set(SOURCE_UNITS):
            raise CurrentSQLiteError(f"historical compile commands omit a source unit for {name}")
    if historical._compare_generated_inputs([variants[name] for name in expected_variants])["status"] != "passed":
        raise CurrentSQLiteError("historical compiler variants generated different SQL inputs")
    signatures = {
        name: {source_name: _normalized_compile_signature(argv) for source_name, argv in rows.items()}
        for name, rows in compile_commands.items()
    }
    if signatures[expected_variants[0]] != signatures[expected_variants[1]]:
        raise CurrentSQLiteError("historical compiler variants do not share compile flags")
    if len({_fingerprint(signatures[expected_variants[0]][unit]) for unit in SOURCE_UNITS}) != 1:
        raise CurrentSQLiteError("historical source units do not share compile flags")
    flags = next(iter(compile_commands[expected_variants[0]].values()))[1:]
    # Validate the shared compile contract before adapting only paths and compiler executable.
    if not all(flag in flags for flag in design["build"]["required_optimization_flags"] + design["build"]["required_definitions"]):
        raise CurrentSQLiteError("historical compile command lacks required optimization or SQLite definitions")
    if any(any(arg.startswith(flag) for arg in flags) for flag in design["build"]["forbidden_flags"]):
        raise CurrentSQLiteError("historical compile command contains an excluded LTO/frame-pointer flag")
    reference_variant = variants[design["historical_evidence"]["reference_variant"]]
    target = reference_variant["target"]
    expected_target_args = [
        f"--target={target['triple']}", "-arch", target["architecture"],
        "-isysroot", target["sdk_path"], f"-mmacosx-version-min={target['minimum_os_version']}",
    ]
    for expected in expected_target_args:
        if expected not in flags:
            raise CurrentSQLiteError(f"historical compile command lacks native target argument: {expected}")
    helper_records = _verified_native_helpers(reference_variant, explicit_override=report_override)
    frozen_inputs = output / "frozen-inputs"
    if frozen_inputs.is_symlink():
        raise CurrentSQLiteError("frozen workload directory must not be a symlink")
    frozen_inputs.mkdir(parents=True, exist_ok=True)
    input_hashes: dict[str, dict[str, Any]] = {}
    ref_hash = design["verification"]["reference_sha256"]
    for file_name in INPUT_FILES + (design["verification"]["reference_output"],):
        origins = [Path(variants[name]["build_directory"]) / historical.SUBTREE / file_name for name in expected_variants]
        data = origins[0].read_bytes()
        if any(origin.read_bytes() != data for origin in origins[1:]):
            raise CurrentSQLiteError(f"historical input differs across compiler builds: {file_name}")
        digest = historical._sha256_bytes(data)
        if file_name == "commands" and digest != design["workload"]["commands_sha256"]:
            raise CurrentSQLiteError("historical commands file hash differs from the design")
        if file_name == "sqlite3rc" and digest != design["workload"]["initialization_sha256"]:
            raise CurrentSQLiteError("historical sqlite3rc hash differs from the design")
        if file_name == design["verification"]["reference_output"] and digest != ref_hash:
            raise CurrentSQLiteError("historical reference output hash differs from the design")
        target_path = frozen_inputs / file_name
        _freeze_input(target_path, data, file_name)
        input_hashes[file_name] = {"sha256": digest, "size_bytes": len(data)}
    if any(path.is_symlink() or not path.is_file() for path in frozen_inputs.iterdir()) or sorted(path.name for path in frozen_inputs.iterdir()) != sorted(INPUT_FILES + (design["verification"]["reference_output"],)):
        raise CurrentSQLiteError("frozen workload directory contains missing or unexpected files")
    return {
        "report_path": str(report_path.resolve()),
        "report_sha256": historical._sha256_file(report_path),
        "historical_source_tree_sha256": source_snapshot["tree_sha256"],
        "historical_tools": tools,
        "variants": variants,
        "sanitizer_variants": report.get("sanitizer_variants", []),
        "shared_compile_argv": next(iter(compile_commands[expected_variants[0]].values())),
        "target": target,
        "compile_signatures_sha256": _fingerprint(signatures),
        "native_helpers": helper_records,
        "frozen_inputs": input_hashes,
    }


def prepare(
    design_path: Path = DEFAULT_DESIGN,
    archive_path: Path = DEFAULT_ARCHIVE,
    source_dir: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
    historical_report: Path | None = None,
    *,
    allow_prepared_override: bool = False,
) -> dict[str, Any]:
    design = _design(design_path)
    if source_dir.is_symlink():
        raise CurrentSQLiteError(f"source staging path must not be a symlink: {source_dir}")
    source_dir = source_dir.resolve()
    output = output.resolve()
    report_path, report_hash, effective_design = _select_historical_report(
        design, historical_report, output, allow_prepared_override=allow_prepared_override
    )
    source_manifest = _stage_verified_archive(archive_path.resolve(), source_dir, design)
    historical_evidence = _historical_evidence(
        design, output, report_path, report_hash, report_override=historical_report is not None
    )
    values = {
        "schema_version": 1,
        "status": "prepared",
        "design_path": str(design_path.resolve()),
        "design_sha256": historical._sha256_file(design_path),
        "effective_design": effective_design,
        "effective_design_sha256": _fingerprint(effective_design),
        "implementation_sha256": historical._sha256_file(Path(__file__).resolve()),
        "source_directory": str(source_dir),
        "source_manifest": source_manifest,
        "historical_evidence": historical_evidence,
        "frozen_inputs_directory": str(output / "frozen-inputs"),
    }
    values["preparation_key"] = _fingerprint(values)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "prepared-manifest.json"
    if manifest_path.exists() and _json(manifest_path) != values:
        raise CurrentSQLiteError("prepared manifest changed; use a new output directory to preserve evidence")
    if not manifest_path.exists():
        _save(manifest_path, values)
    return values


def _compile_argv(
    historical_argv: list[str], *, compiler: Path, source: Path, output: Path, sanitizer_flags: list[str] | None = None
) -> list[str]:
    argv = list(historical_argv)
    if not argv or "-o" not in argv or "-c" not in argv:
        raise CurrentSQLiteError("historical compiler argv has no standard compile shape")
    argv[0] = str(compiler)
    out_index = argv.index("-o")
    compile_index = argv.index("-c")
    if out_index + 1 >= len(argv) or compile_index + 1 >= len(argv):
        raise CurrentSQLiteError("historical compiler argv is incomplete")
    argv[out_index + 1] = str(output)
    argv[compile_index + 1] = str(source)
    if sanitizer_flags:
        argv.extend(sanitizer_flags)
    return argv


def _link_argv(
    compiler: Path,
    *,
    design: dict[str, Any],
    target: dict[str, Any],
    object_files: list[Path],
    output: Path,
    sanitizer: bool,
) -> list[str]:
    argv = [str(compiler), f"--target={target['triple']}", "-O3", "-arch", target["architecture"], "-isysroot", target["sdk_path"], f"-mmacosx-version-min={target['minimum_os_version']}"]
    argv.extend(design["build"]["link_flags"])
    if sanitizer:
        argv.append("-fsanitize=address,undefined")
    argv.extend(str(path) for path in object_files)
    argv.extend(["-o", str(output)])
    return argv


def _run_variant(
    name: str,
    *,
    sanitizer: bool,
    design: dict[str, Any],
    prepared: dict[str, Any],
    run_root: Path,
    tools: dict[str, Any],
) -> dict[str, Any]:
    run_root.mkdir(parents=True, exist_ok=True)
    variant_id = f"{name}-asan-ubsan" if sanitizer else name
    build_dir = run_root / "build" / variant_id
    build_dir.mkdir(parents=True, exist_ok=True)
    logs = run_root / "commands" / variant_id
    source_dir = Path(prepared["source_directory"])
    compiler = Path(tools["compilers"][name])
    old_argv = prepared["historical_evidence"]["shared_compile_argv"]
    sanitizer_flags = design["toolchains"]["sanitizer_flags"] if sanitizer else []
    result: dict[str, Any] = {
        "name": variant_id,
        "compiler": tools["files"][name],
        "sanitizer": sanitizer,
        "target": tools["target"],
        "build_directory": str(build_dir),
        "source_files": {key: prepared["source_manifest"]["files"][key] for key in SOURCE_UNITS + ("sqlite3.h",)},
        "commands": {},
    }
    objects = []
    compile_ok = True
    for unit in SOURCE_UNITS:
        obj = build_dir / f"{unit}.o"
        unit_argv = _compile_argv(
            old_argv,
            compiler=compiler,
            source=source_dir / unit,
            output=obj,
            sanitizer_flags=sanitizer_flags,
        )
        command = historical._run_command(unit_argv, cwd=source_dir, log_dir=logs, name=f"compile-{unit}")
        result["commands"][f"compile_{unit}"] = command
        objects.append(obj)
        compile_ok = compile_ok and command["return_code"] == 0
    binary = build_dir / "sqlite3"
    if compile_ok:
        link_argv = _link_argv(compiler, design=design, target=tools["target"], object_files=objects, output=binary, sanitizer=sanitizer)
        result["commands"]["link"] = historical._run_command(link_argv, cwd=build_dir, log_dir=logs, name="link")
    else:
        result["commands"]["link"] = {"status": "skipped_after_compile_failure"}
    build_ok = compile_ok and result["commands"]["link"].get("return_code") == 0 and binary.is_file()
    result["artifacts"] = {
        "binary": {"path": str(binary), "sha256": historical._sha256_file(binary) if binary.is_file() else None},
        "objects": {path.name: {"path": str(path), "sha256": historical._sha256_file(path) if path.is_file() else None} for path in objects},
    }
    if not build_ok:
        result["status"] = "failed_build"
        return result
    helper = prepared["historical_evidence"]["native_helpers"]["timeit_target"]
    comparator = prepared["historical_evidence"]["native_helpers"]["fpcmp_target"]
    workdir = Path(prepared["frozen_inputs_directory"])
    stdout_path = logs / "upstream-run.output"
    summary_path = logs / "upstream-run.summary"
    stdout_path.unlink(missing_ok=True)
    summary_path.unlink(missing_ok=True)
    run_argv = historical._upstream_timeit_command(
        timeit=Path(helper["path"]),
        binary=binary,
        app_build=workdir,
        input_path=workdir / design["workload"]["stdin_file"],
        output_path=stdout_path,
        summary_path=summary_path,
        run_options=design["workload"]["run_options"],
    )
    env = os.environ.copy()
    if sanitizer:
        env.update(design["toolchains"]["sanitizer_runtime_options"])
    run_result = historical._run_command(
        run_argv, cwd=workdir, log_dir=logs, name="upstream-run", timeout=historical.UPSTREAM_RUN_TIMEOUT_SECONDS, env=env
    )
    run_result.update({
        "target_exit_code": historical._timeit_target_exit_code(summary_path),
        "stdin_path": str(workdir / design["workload"]["stdin_file"]),
        "stdin_sha256": historical._sha256_file(workdir / design["workload"]["stdin_file"]),
        "target_output_path": str(stdout_path),
        "target_output_sha256": historical._sha256_file(stdout_path) if stdout_path.is_file() else None,
        "target_output_size_bytes": stdout_path.stat().st_size if stdout_path.is_file() else None,
        "timeit_summary_path": str(summary_path),
        "timeit_summary_sha256": historical._sha256_file(summary_path) if summary_path.is_file() else None,
    })
    result["commands"]["upstream_run"] = run_result
    verify_argv = [comparator["path"], "-r", design["verification"]["relative_tolerance"], str(stdout_path), str(workdir / design["verification"]["reference_output"])]
    comparison = historical._run_command(verify_argv, cwd=workdir, log_dir=logs, name="reference-compare")
    comparison["reference_sha256"] = historical._sha256_file(workdir / design["verification"]["reference_output"])
    result["commands"]["reference_compare"] = comparison
    result["artifacts"].update({
        "historical_timeit_target": helper,
        "historical_fpcmp_target": comparator,
        "stdin_commands_sha256": run_result["stdin_sha256"],
        "reference_output_sha256": comparison["reference_sha256"],
        "captured_output_sha256": run_result["target_output_sha256"],
        "timeit_summary_sha256": run_result["timeit_summary_sha256"],
    })
    result["status"] = "passed" if historical._reference_test_passed(run_result, comparison) else "failed_reference"
    return result


def validate(
    design_path: Path = DEFAULT_DESIGN,
    archive_path: Path = DEFAULT_ARCHIVE,
    source_dir: Path = DEFAULT_SOURCE,
    output: Path = DEFAULT_OUTPUT,
    historical_report: Path | None = None,
) -> dict[str, Any]:
    design = _design(design_path)
    prepared = prepare(
        design_path, archive_path, source_dir, output,
        historical_report, allow_prepared_override=True,
    )
    old_tools = prepared["historical_evidence"]["historical_tools"]
    tools = {
        "compilers": old_tools["compilers"],
        "files": old_tools["files"],
        "target": prepared["historical_evidence"]["target"],
    }
    run_key = _fingerprint({
        "design_sha256": prepared["design_sha256"],
        "preparation_key": prepared["preparation_key"],
        "implementation_sha256": prepared["implementation_sha256"],
        "compiler_hashes": {name: old_tools["files"][name]["sha256"] for name in ("pinned_llvm", "system_apple")},
        "input_hashes": prepared["historical_evidence"]["frozen_inputs"],
    })[:16]
    run_root = output.resolve() / "runs" / run_key
    result_variants = [
        _run_variant(name, sanitizer=False, design=design, prepared=prepared, run_root=run_root, tools=tools)
        for name in design["toolchains"]["baseline"]
    ]
    result_variants.append(
        _run_variant(design["toolchains"]["sanitizer"], sanitizer=True, design=design, prepared=prepared, run_root=run_root, tools=tools)
    )
    historical_as = next((row for row in prepared["historical_evidence"]["sanitizer_variants"] if row.get("name") == "pinned_llvm-asan-ubsan"), None)
    if historical_as is None:
        raise CurrentSQLiteError("historical pinned-LLVM sanitizer-unavailable evidence is missing")
    sanitizer_config = historical_as["commands"]["configure"]
    if historical_as.get("compiler", {}).get("sha256") != old_tools["files"]["pinned_llvm"]["sha256"]:
        raise CurrentSQLiteError("historical pinned LLVM sanitizer failure uses a different compiler")
    for stream in ("stdout", "stderr"):
        path = Path(sanitizer_config[f"{stream}_path"])
        if not path.is_file() or historical._sha256_file(path) != sanitizer_config[f"{stream}_sha256"]:
            raise CurrentSQLiteError(f"historical pinned LLVM sanitizer {stream} log is missing or changed")
    failure_text = "\n".join(Path(sanitizer_config[field]).read_text(errors="replace") for field in ("stdout_path", "stderr_path"))
    missing_runtime = "libclang_rt.asan_osx_dynamic.dylib" in failure_text and sanitizer_config.get("return_code") != 0
    if not missing_runtime:
        raise CurrentSQLiteError("historical pinned LLVM ASan unavailability evidence no longer matches")
    pinned_unavailable = {
        "status": "known_unavailable_not_retried",
        "compiler_sha256": old_tools["files"]["pinned_llvm"]["sha256"],
        "historical_return_code": sanitizer_config["return_code"],
        "historical_stdout_sha256": sanitizer_config["stdout_sha256"],
        "historical_stderr_sha256": sanitizer_config["stderr_sha256"],
        "reason": "libclang_rt.asan_osx_dynamic.dylib missing from pinned LLVM runtime",
    }
    report = {
        "schema_version": 1,
        "status": "passed" if all(row["status"] == "passed" for row in result_variants[:2]) else "failed",
        "sanitizer_status": result_variants[2]["status"],
        "qualified_for_repair": all(row["status"] == "passed" for row in result_variants),
        "pinned_llvm_sanitizer": pinned_unavailable,
        "run_key": run_key,
        "design_sha256": prepared["design_sha256"],
        "effective_design": prepared["effective_design"],
        "effective_design_sha256": prepared["effective_design_sha256"],
        "implementation_sha256": prepared["implementation_sha256"],
        "preparation_key": prepared["preparation_key"],
        "source_manifest": prepared["source_manifest"],
        "historical_evidence": {
            "report_path": prepared["historical_evidence"]["report_path"],
            "report_sha256": prepared["historical_evidence"]["report_sha256"],
            "compile_signatures_sha256": prepared["historical_evidence"]["compile_signatures_sha256"],
            "frozen_inputs": prepared["historical_evidence"]["frozen_inputs"],
            "native_helpers": prepared["historical_evidence"]["native_helpers"],
        },
        "toolchain": tools,
        "variants": result_variants,
        "performance_measurements": 0,
    }
    report_path = run_root / "current-sqlite-feasibility-report.json"
    report["report_path"] = str(report_path)
    _save(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "validate"), default="prepare")
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--historical-report", type=Path,
        help="explicitly select a different passed historical report; requires a new --output directory",
    )
    args = parser.parse_args(argv)
    try:
        result = (
            prepare(args.design.resolve(), args.archive.resolve(), args.source_dir, args.output.resolve(), args.historical_report)
            if args.phase == "prepare"
            else validate(args.design.resolve(), args.archive.resolve(), args.source_dir, args.output.resolve(), args.historical_report)
        )
    except (CurrentSQLiteError, historical.FeasibilityError, OSError, ValueError, KeyError) as error:
        print(f"current SQLite feasibility failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "status": result["status"],
        "qualified_for_repair": result.get("qualified_for_repair"),
        "preparation_key": result.get("preparation_key"),
        "report_path": result.get("report_path"),
    }, sort_keys=True))
    return _exit_code(args.phase, result)


if __name__ == "__main__":
    raise SystemExit(main())
