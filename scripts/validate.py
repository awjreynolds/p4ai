#!/usr/bin/env python3
"""Validate a bounded LLVM register-eviction decision on native ARM64."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from . import advisor
except ImportError:  # Running as `python scripts/validate.py`.
    import advisor


ROOT = Path(__file__).resolve().parents[1]
LLVM_SOURCE_DEFAULT = ROOT / ".cache/iree/third_party/llvm-project"
if not LLVM_SOURCE_DEFAULT.is_dir():
    LLVM_SOURCE_DEFAULT = ROOT / ".cache/llvm-project"
PINNED_LLVM_REVISION = "32d702efa29d0fdd02cff7b79dea8fdbbcf059d4"
NATIVE = ROOT / "fixtures/native"
KERNELS_C = NATIVE / "kernels.c"
DRIVER_C = NATIVE / "driver.c"
TRIPLE = "arm64-apple-darwin"
PATCH_FILE = ROOT / "patches/llvm-regalloc-action-guard.patch"
REVISION_PATTERN = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", re.IGNORECASE)
EXPECTED_GUARDS = {
    "negative": r"Regalloc: ML eviction advisor returned negative candidate position -?\d+",
    "out_of_range": r"Regalloc: ML eviction advisor returned candidate position \d+ outside the candidate columns \[0, \d+\)",
    "masked": r"Regalloc: ML eviction advisor selected masked candidate position \d+",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str | None:
    return sha256(path.read_bytes()) if path.is_file() else None


def check_reported_revision(output: str, expected: str) -> dict[str, Any]:
    """Require every source revision emitted by a tool to match the pin."""
    revisions = sorted(
        {revision.lower() for revision in REVISION_PATTERN.findall(output)}
    )
    mismatches = [revision for revision in revisions if revision != expected.lower()]
    return {
        "status": "failed" if mismatches else ("passed" if revisions else "unreported"),
        "reported_revisions": revisions,
        "mismatched_revisions": mismatches,
    }


def _resolved_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().resolve()


def _manifest_path(value: Any, relative_to: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (relative_to / path).resolve()


def _normalized_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {
            part.strip().casefold()
            for part in value.replace(",", ";").split(";")
            if part.strip()
        }
    if isinstance(value, list):
        return {str(part).strip().casefold() for part in value if str(part).strip()}
    return set()


def verify_build_manifest(
    manifest_path: Path,
    *,
    source: Path,
    source_revision: str | None,
    guard_source: Path,
    patch_file: Path,
    llc: Path,
    clang: Path,
    filecheck: Path,
) -> dict[str, Any]:
    """Verify the build agent's source-bound manifest against current files."""
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        return {"status": "blocked", "errors": [f"manifest unavailable: {error}"]}
    except json.JSONDecodeError as error:
        return {"status": "failed", "errors": [f"manifest is invalid JSON: {error}"]}
    if not isinstance(manifest, dict):
        return {"status": "failed", "errors": ["manifest root must be an object"]}

    if manifest.get("schema_version") != 1:
        errors.append("manifest schema_version must be 1")
    if (
        manifest.get("source_revision") != PINNED_LLVM_REVISION
        or source_revision != PINNED_LLVM_REVISION
    ):
        errors.append(
            "manifest and checked-out LLVM source must match the pinned revision"
        )
    if _resolved_path(manifest.get("source_path")) != source.resolve():
        errors.append("manifest source_path does not match the selected LLVM source")
    if (
        _manifest_path(manifest.get("guard_source_path"), source)
        != guard_source.resolve()
    ):
        errors.append("manifest guard_source_path does not match the LLVM guard source")
    if manifest.get("guard_source_sha256") != file_sha256(guard_source):
        errors.append(
            "manifest guard source hash does not match the current source file"
        )
    if manifest.get("patch_sha256") != file_sha256(patch_file):
        errors.append("manifest patch hash does not match the selected guard patch")

    binaries = manifest.get("binaries")
    if not isinstance(binaries, dict):
        errors.append("manifest binaries must be an object")
        binaries = {}
    for name, tool in (("llc", llc), ("clang", clang), ("FileCheck", filecheck)):
        entry = binaries.get(name)
        if not isinstance(entry, dict):
            errors.append(f"manifest is missing the {name} binary record")
            continue
        if _resolved_path(entry.get("path")) != tool.resolve():
            errors.append(f"manifest {name} path does not match the selected binary")
        actual_hash = file_sha256(tool)
        if entry.get("sha256") != actual_hash or actual_hash is None:
            errors.append(
                f"manifest {name} binary hash does not match the selected binary"
            )
    build_config = manifest.get("build_config")
    if not isinstance(build_config, dict):
        errors.append("manifest build_config must be an object")
        build_config = {}
    expected_config = {
        "generator": {"ninja"},
        "build_type": {"release"},
        "llvm_projects": {"clang"},
        "llvm_targets": {"aarch64", "bpf"},
    }
    for field, expected_values in expected_config.items():
        if _normalized_values(build_config.get(field)) != expected_values:
            errors.append(
                f"manifest build_config.{field} does not match the native LLVM build"
            )
    assertions = build_config.get("assertions")
    if assertions not in (True, "ON", "on", "TRUE", "true", 1):
        errors.append("manifest build_config.assertions must be enabled")

    return {
        "status": "failed" if errors else "passed",
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_sha256(manifest_path),
        "errors": errors,
        "source_revision": manifest.get("source_revision"),
        "llc_sha256": binaries.get("llc", {}).get("sha256")
        if isinstance(binaries.get("llc"), dict)
        else None,
        "clang_sha256": binaries.get("clang", {}).get("sha256")
        if isinstance(binaries.get("clang"), dict)
        else None,
        "patch_sha256": manifest.get("patch_sha256"),
        "guard_source_sha256": manifest.get("guard_source_sha256"),
    }


def find_divergent_decision(
    first: list[dict[str, Any]], last: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find a matching decision state where the two mechanical policies differ."""
    first_by_state = {
        (
            item["context"],
            item["observation"],
            item["features_sha256"],
            tuple(item["eligible_actions"]),
        ): item
        for item in first
    }
    for item in last:
        key = (
            item["context"],
            item["observation"],
            item["features_sha256"],
            tuple(item["eligible_actions"]),
        )
        other = first_by_state.get(key)
        if (
            other is not None
            and len(item["eligible_actions"]) > 1
            and item["action_index"] != other["action_index"]
        ):
            return {
                "context": item["context"],
                "observation": item["observation"],
                "features_sha256": item["features_sha256"],
                "eligible_actions": item["eligible_actions"],
                "first_action": other["action_index"],
                "last_action": item["action_index"],
            }
    return None


def source_revision(
    source: Path, recorder: Recorder
) -> tuple[str | None, dict[str, Any]]:
    result = recorder.run("llvm_source_revision", ["git", "rev-parse", "HEAD"], source)
    revision = result["stdout"].decode("utf-8", errors="replace").strip()
    return (revision if result["return_code"] == 0 else None), result


class Recorder:
    """Run and retain command output plus a hash summary for the JSON report."""

    def __init__(self, artifacts_dir: Path, timeout_seconds: int):
        self.artifacts_dir = artifacts_dir
        self.timeout_seconds = timeout_seconds
        self.commands: list[dict[str, Any]] = []

    def run(
        self,
        name: str,
        argv: list[str],
        cwd: Path,
        *,
        expected_exit: int = 0,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        stdout_path = self.artifacts_dir / "commands" / f"{name}.stdout"
        stderr_path = self.artifacts_dir / "commands" / f"{name}.stderr"
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        timeout = timeout_seconds or self.timeout_seconds
        started = time.monotonic()
        stdout = b""
        stderr = b""
        return_code: int | None = None
        error: str | None = None
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr, return_code = (
                result.stdout,
                result.stderr,
                result.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            error = f"timed out after {timeout} seconds"
        except OSError as exc:
            error = f"could not start command: {exc}"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        record = {
            "name": name,
            "argv": argv,
            "cwd": str(cwd),
            "return_code": return_code,
            "expected_exit_code": expected_exit,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "stdout_path": str(stdout_path.relative_to(self.artifacts_dir)),
            "stdout_bytes": len(stdout),
            "stdout_sha256": sha256(stdout),
            "stderr_path": str(stderr_path.relative_to(self.artifacts_dir)),
            "stderr_bytes": len(stderr),
            "stderr_sha256": sha256(stderr),
            "error": error,
            "passed": return_code == expected_exit and error is None,
        }
        self.commands.append(record)
        return {**record, "stdout": stdout, "stderr": stderr}


def _run_llc_command(
    llc: Path,
    ir_path: Path,
    output_path: Path,
    *,
    target: str = TRIPLE,
) -> list[str]:
    return [
        str(llc),
        "-O=2",
        f"-mtriple={target}",
        "-regalloc=greedy",
        "-verify-machineinstrs",
        "-filetype=obj",
        str(ir_path),
        "-o",
        str(output_path),
    ]


def _interactive_llc_command(llc: Path, ir_path: Path, output_path: Path) -> list[str]:
    return [
        str(llc),
        "-O=2",
        f"-mtriple={TRIPLE}",
        "-regalloc=greedy",
        "-verify-machineinstrs",
        "-filetype=obj",
        "-regalloc-enable-advisor=release",
        str(ir_path),
        "-o",
        str(output_path),
    ]


def _replay_identity(
    command: list[str],
    *,
    ir_sha256: str | None,
    llc_sha256: str | None,
    llc_version_sha256: str | None,
    source_revision: str | None,
    guard_source_sha256: str | None,
    patch_sha256: str | None,
    build_manifest_sha256: str | None,
) -> dict[str, Any]:
    """Bind replay to compiler inputs while normalizing only the output path."""
    arguments = list(command)
    try:
        output_flag = arguments.index("-o")
        arguments[output_flag + 1] = "<object-output>"
    except (ValueError, IndexError):
        raise ValueError(
            "compiler replay identity requires an -o output argument"
        ) from None
    return {
        "ir_sha256": ir_sha256,
        "llc_sha256": llc_sha256,
        "llc_version_stdout_sha256": llc_version_sha256,
        "llvm_source_revision": source_revision,
        "mlgo_guard_source_sha256": guard_source_sha256,
        "guard_patch_sha256": patch_sha256,
        "build_manifest_sha256": build_manifest_sha256,
        "compiler_args": arguments,
    }


def _read_driver_result(stdout: bytes, iterations: int) -> dict[str, Any] | None:
    lines = stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) != 1:
        return None
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    if (
        result.get("correct") is not True
        or type(result.get("iterations")) is not int
        or result["iterations"] != iterations
    ):
        return None
    if not isinstance(result.get("checksum"), str) or not re.fullmatch(
        r"[0-9a-f]{16}", result["checksum"]
    ):
        return None
    if type(result.get("elapsed_ns")) is not int or result["elapsed_ns"] < 0:
        return None
    return result


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _add_interactive_command(
    report_commands: list[dict[str, Any]], result: dict[str, Any]
) -> None:
    stdout_path = Path(result["stdout_path"])
    stderr_path = Path(result["stderr_path"])
    report_commands.append(
        {
            "name": result["policy"],
            "argv": result["compiler_command"],
            "return_code": result["return_code"],
            "duration_ms": result["duration_ms"],
            "stdout_path": result["stdout_path"],
            "stdout_bytes": stdout_path.stat().st_size,
            "stdout_sha256": result["stdout_sha256"],
            "stderr_path": result["stderr_path"],
            "stderr_bytes": stderr_path.stat().st_size,
            "stderr_sha256": result["stderr_sha256"],
            "communication_error": result["communication_error"],
            "timed_out": result["timed_out"],
        }
    )


def _successful_interactive(result: dict[str, Any], output_path: Path) -> bool:
    return (
        result["return_code"] == 0
        and result["communication_error"] is None
        and not result["timed_out"]
        and result["decision_count"] > 0
        and output_path.is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llvm-source", type=Path, default=LLVM_SOURCE_DEFAULT)
    parser.add_argument("--build-dir", type=Path, default=ROOT / ".cache/llvm-build")
    parser.add_argument("--build-manifest", type=Path)
    parser.add_argument("--llc", type=Path)
    parser.add_argument("--clang", type=Path)
    parser.add_argument("--linker", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--timeout-seconds", type=positive_int, default=180)
    parser.add_argument("--iterations", type=positive_int, default=3)
    args = parser.parse_args()

    source = args.llvm_source.expanduser().resolve()
    build_dir = args.build_dir.expanduser().resolve()
    build_manifest = (
        args.build_manifest.expanduser().resolve()
        if args.build_manifest
        else build_dir / "p4ai-build.json"
    )
    llc = args.llc.expanduser().resolve() if args.llc else build_dir / "bin/llc"
    clang = args.clang.expanduser().resolve() if args.clang else build_dir / "bin/clang"
    filecheck = build_dir / "bin/FileCheck"
    linker = args.linker.expanduser().resolve() if args.linker else clang
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifacts_dir = (
        args.artifacts_dir.expanduser().resolve()
        if args.artifacts_dir
        else ROOT / "artifacts" / f"llvm-regalloc-{timestamp}"
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("commands", "ir", "objects", "executables", "advisor"):
        (artifacts_dir / subdir).mkdir(exist_ok=True)

    recorder = Recorder(artifacts_dir, args.timeout_seconds)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "incomplete",
        "compiler_validation": "incomplete",
        "llvm": {
            "source": str(source),
            "required_revision": PINNED_LLVM_REVISION,
            "build_dir": str(build_dir),
            "build_manifest": str(build_manifest),
            "llc": str(llc),
            "llc_sha256": file_sha256(llc),
            "clang": str(clang),
            "clang_sha256": file_sha256(clang),
            "filecheck": str(filecheck),
            "filecheck_sha256": file_sha256(filecheck),
            "linker": str(linker),
            "linker_sha256": file_sha256(linker),
            "target": TRIPLE,
            "guard_source": str(source / "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp"),
            "guard_source_sha256": file_sha256(
                source / "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp"
            ),
        },
        "corpus": {
            "kernels": str(KERNELS_C),
            "kernels_sha256": file_sha256(KERNELS_C),
            "driver": str(DRIVER_C),
            "driver_sha256": file_sha256(DRIVER_C),
            "iterations": args.iterations,
            "reference": "C scalar reference in driver.c validates three pressure kernels and boundary/random vectors",
        },
        "model_calls": False,
        "policy_scope": "mechanical first/last eligible physical-register candidate; no Jev calls",
        "checks": {},
        "commands": [],
    }
    checks: dict[str, dict[str, Any]] = report["checks"]

    revision, revision_command = source_revision(source, recorder)
    report["llvm"]["source_revision"] = revision
    checks["pinned_source"] = {
        "status": "passed" if revision == PINNED_LLVM_REVISION else "failed",
        "revision": revision,
        "revision_command": {
            k: v for k, v in revision_command.items() if k not in {"stdout", "stderr"}
        },
    }

    required_inputs = (
        source / "llvm/lib/Analysis/models/log_reader.py",
        source / "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp",
        PATCH_FILE,
        KERNELS_C,
        DRIVER_C,
    )
    missing_inputs = [str(path) for path in required_inputs if not path.is_file()]
    if missing_inputs:
        checks["inputs"] = {"status": "blocked", "missing": missing_inputs}
        report["commands"] = recorder.commands
        report["compiler_validation"] = "incomplete"
        report["status"] = "incomplete_missing_inputs"
        output_path = artifacts_dir / "report.json"
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"Validation status: {report['status']}")
        print(f"Report: {output_path}")
        return 2

    missing = [
        str(path) for path in (llc, clang, filecheck, linker) if not path.is_file()
    ]
    if missing:
        checks["toolchain"] = {"status": "blocked", "missing": missing}
        report["commands"] = recorder.commands
        report["compiler_validation"] = "incomplete"
        report["status"] = "incomplete_missing_toolchain"
        output_path = artifacts_dir / "report.json"
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"Validation status: {report['status']}")
        print(f"Report: {output_path}")
        return 2
    checks["toolchain"] = {"status": "passed"}

    version_results: dict[str, Any] = {}
    version_identity_ok = True
    for name, tool in (("llc", llc), ("clang", clang)):
        version = recorder.run(f"{name}_version", [str(tool), "--version"], ROOT)
        version_text = version["stdout"].decode("utf-8", errors="replace")
        revision_check = check_reported_revision(version_text, PINNED_LLVM_REVISION)
        version_ok = (
            version["return_code"] == 0 and revision_check["status"] != "failed"
        )
        version_identity_ok = version_identity_ok and version_ok
        version_results[name] = {
            "return_code": version["return_code"],
            "stdout": version_text.splitlines()[:4],
            "stdout_sha256": version["stdout_sha256"],
            "reported_revision": revision_check["reported_revisions"],
            "revision_check": revision_check["status"],
            "revision_evidence": revision_check,
        }
    report["llvm"]["tool_versions"] = version_results
    for name, version in version_results.items():
        version_ok = (
            version["return_code"] == 0 and version["revision_check"] != "failed"
        )
        checks[f"{name}_version"] = {
            "status": "passed" if version_ok else "failed",
            "stdout_sha256": version["stdout_sha256"],
            "revision_check": version["revision_evidence"],
        }

    manifest_check = verify_build_manifest(
        build_manifest,
        source=source,
        source_revision=revision,
        guard_source=source / "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp",
        patch_file=PATCH_FILE,
        llc=llc,
        clang=clang,
        filecheck=filecheck,
    )
    provenance_ok = version_identity_ok and manifest_check["status"] == "passed"
    provenance_status = (
        "passed"
        if provenance_ok
        else "failed"
        if not version_identity_ok
        else manifest_check["status"]
    )
    checks["compiler_provenance"] = {
        "status": provenance_status,
        "version_revisions_match_when_emitted": version_identity_ok,
        "build_manifest": manifest_check,
    }
    report["llvm"]["build_manifest_evidence"] = manifest_check
    if not provenance_ok:
        report["commands"] = recorder.commands
        report["compiler_validation"] = "failed"
        report["status"] = "failed_unverified_compiler"
        output_path = artifacts_dir / "report.json"
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"Validation status: {report['status']}")
        print(f"Report: {output_path}")
        return 1

    ir_path = artifacts_dir / "ir/native_corpus.ll"
    ir_compile = recorder.run(
        "clang_emit_llvm",
        [
            str(clang),
            "-std=c11",
            "-O2",
            f"--target={TRIPLE}",
            "-S",
            "-emit-llvm",
            str(KERNELS_C),
            "-o",
            str(ir_path),
        ],
        ROOT,
    )
    ir_ok = ir_compile["passed"] and ir_path.is_file()
    checks["build_llvm_ir"] = {
        "status": "passed" if ir_ok else "failed",
        "command": {
            k: v for k, v in ir_compile.items() if k not in {"stdout", "stderr"}
        },
        "ir_path": str(ir_path.relative_to(artifacts_dir)),
        "ir_sha256": file_sha256(ir_path),
    }
    if not ir_ok:
        report["commands"] = recorder.commands
        report["compiler_validation"] = "failed"
        report["status"] = "failed"
        output_path = artifacts_dir / "report.json"
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"Validation status: {report['status']}")
        print(f"Report: {output_path}")
        return 1

    base_interactive_command = _interactive_llc_command(
        llc, ir_path, artifacts_dir / "objects/output.o"
    )
    run_identity = _replay_identity(
        base_interactive_command,
        ir_sha256=file_sha256(ir_path),
        llc_sha256=file_sha256(llc),
        llc_version_sha256=version_results["llc"]["stdout_sha256"],
        source_revision=revision,
        guard_source_sha256=report["llvm"]["guard_source_sha256"],
        patch_sha256=file_sha256(PATCH_FILE),
        build_manifest_sha256=manifest_check["manifest_sha256"],
    )
    report["replay_identity"] = run_identity

    object_paths = {
        name: artifacts_dir / f"objects/{name}.o"
        for name in ("stock", "first", "last", "replay", "replay_mismatch")
    }
    stock = recorder.run(
        "compile_stock",
        _run_llc_command(llc, ir_path, object_paths["stock"]),
        ROOT,
    )
    checks["compile_stock"] = {
        "status": "passed"
        if stock["passed"] and object_paths["stock"].is_file()
        else "failed",
        "command": {k: v for k, v in stock.items() if k not in {"stdout", "stderr"}},
        "object_sha256": file_sha256(object_paths["stock"]),
        "machine_verifier": "-verify-machineinstrs",
    }

    def run_policy(name: str, policy: advisor.Policy) -> dict[str, Any]:
        command = _interactive_llc_command(llc, ir_path, object_paths[name])
        result = advisor.run_interactive(
            command,
            llvm_source=source,
            policy=policy,
            policy_name=name,
            timeout_seconds=args.timeout_seconds,
            stdout_path=artifacts_dir / f"advisor/{name}.stdout",
            stderr_path=artifacts_dir / f"advisor/{name}.stderr",
            run_identity=run_identity,
        )
        _add_interactive_command(report["commands"], result)
        return result

    first = run_policy("first", advisor.first_allowed)
    last = run_policy("last", advisor.last_allowed)
    for name, result in (("first", first), ("last", last)):
        checks[f"compile_{name}"] = {
            "status": "passed"
            if _successful_interactive(result, object_paths[name])
            else "failed",
            "result": result,
            "object_path": str(object_paths[name].relative_to(artifacts_dir)),
            "object_sha256": file_sha256(object_paths[name]),
            "machine_verifier": "-verify-machineinstrs",
        }

    divergence = find_divergent_decision(first["decisions"], last["decisions"])
    checks["two_distinct_legal_actions"] = {
        "status": "passed" if divergence else "failed",
        "first_decision_count": first["decision_count"],
        "last_decision_count": last["decision_count"],
        "divergent_shared_state": divergence,
    }

    replay = run_policy("replay", advisor.replay_policy(first["decisions"]))
    first_state_sequence = [
        (
            d["run_identity"],
            d["context"],
            d["observation"],
            d["eligible_actions"],
            d["features_sha256"],
            d["action_index"],
            d["action_sha256"],
        )
        for d in first["decisions"]
    ]
    replay_state_sequence = [
        (
            d["run_identity"],
            d["context"],
            d["observation"],
            d["eligible_actions"],
            d["features_sha256"],
            d["action_index"],
            d["action_sha256"],
        )
        for d in replay["decisions"]
    ]
    exact_replay = (
        _successful_interactive(replay, object_paths["replay"])
        and first_state_sequence == replay_state_sequence
        and file_sha256(object_paths["first"]) == file_sha256(object_paths["replay"])
    )
    checks["exact_state_replay"] = {
        "status": "passed" if exact_replay else "failed",
        "state_and_action_sequence_identical": first_state_sequence
        == replay_state_sequence,
        "object_byte_identical": file_sha256(object_paths["first"])
        == file_sha256(object_paths["replay"]),
        "first_object_sha256": file_sha256(object_paths["first"]),
        "replay_object_sha256": file_sha256(object_paths["replay"]),
        "result": replay,
    }

    tampered_records = [dict(item) for item in first["decisions"]]
    if tampered_records:
        tampered_records[0]["features_sha256"] = "0" * 64
        mismatch = run_policy(
            "replay_mismatch", advisor.replay_policy(tampered_records)
        )
        refusal = (
            mismatch["communication_error"] is not None
            and "state fingerprint, or legal actions changed"
            in mismatch["communication_error"]
            and mismatch["decision_count"] == 0
            and not object_paths["replay_mismatch"].exists()
        )
    else:
        mismatch = None
        refusal = False
    checks["mismatched_replay_refused"] = {
        "status": "passed" if refusal else "failed",
        "no_action_sent_for_mismatch": bool(
            mismatch and mismatch["decision_count"] == 0
        ),
        "result": mismatch,
    }

    guard_checks: dict[str, Any] = {}
    for case_name, action in (
        ("negative", -1),
        ("out_of_range", 1_000_000),
        ("masked", "masked"),
    ):
        case_runs = []
        for attempt in (1, 2):
            output_path = artifacts_dir / f"objects/guard_{case_name}_{attempt}.o"
            raw_result = advisor.run_interactive(
                _interactive_llc_command(llc, ir_path, output_path),
                llvm_source=source,
                policy=advisor.first_allowed,
                policy_name=f"raw_{case_name}",
                timeout_seconds=args.timeout_seconds,
                stdout_path=artifacts_dir
                / f"advisor/guard_{case_name}_{attempt}.stdout",
                stderr_path=artifacts_dir
                / f"advisor/guard_{case_name}_{attempt}.stderr",
                run_identity=run_identity,
                allow_raw_action=True,
                raw_action=action,
            )
            _add_interactive_command(report["commands"], raw_result)
            stderr = Path(raw_result["stderr_path"]).read_text(errors="replace")
            diagnostic = next(
                (
                    line
                    for line in stderr.splitlines()
                    if re.search(EXPECTED_GUARDS[case_name], line)
                ),
                None,
            )
            case_runs.append(
                {
                    "attempt": attempt,
                    "result": raw_result,
                    "expected_diagnostic": EXPECTED_GUARDS[case_name],
                    "diagnostic": diagnostic,
                }
            )
        diagnostics = [
            re.sub(r"\d+", "<number>", run["diagnostic"])
            if run["diagnostic"] is not None
            else None
            for run in case_runs
        ]
        rejected = all(
            run["result"]["return_code"] not in (None, 0)
            and run["result"]["communication_error"] is None
            and not run["result"]["timed_out"]
            and run["result"]["decision_count"] >= 1
            and run["diagnostic"] is not None
            for run in case_runs
        )
        deterministic = diagnostics[0] == diagnostics[1] and diagnostics[0] is not None
        guard_checks[case_name] = {
            "status": "passed" if rejected and deterministic else "failed",
            "rejected_by_llc_guard": rejected,
            "diagnostic_deterministic": deterministic,
            "attempts": case_runs,
        }
    checks["release_mode_action_guards"] = {
        "status": "passed"
        if all(case["status"] == "passed" for case in guard_checks.values())
        else "failed",
        "cases": guard_checks,
        "host_validation_bypassed": True,
    }

    link_checks: dict[str, Any] = {}
    runtime_checks: dict[str, Any] = {}
    expected_checksum: str | None = None
    for name in ("stock", "first", "last", "replay"):
        executable = artifacts_dir / f"executables/{name}"
        link = recorder.run(
            f"link_{name}",
            [
                str(linker),
                "-std=c11",
                "-O2",
                str(DRIVER_C),
                str(object_paths[name]),
                "-o",
                str(executable),
            ],
            ROOT,
        )
        linked = link["passed"] and executable.is_file()
        link_checks[name] = {
            "status": "passed" if linked else "failed",
            "command": {k: v for k, v in link.items() if k not in {"stdout", "stderr"}},
            "executable_sha256": file_sha256(executable),
        }
        if not linked:
            runtime_checks[name] = {"status": "blocked", "reason": "link failed"}
            continue
        run = recorder.run(
            f"run_{name}",
            [str(executable), str(args.iterations)],
            ROOT,
        )
        parsed = _read_driver_result(run["stdout"], args.iterations)
        if parsed and expected_checksum is None:
            expected_checksum = parsed["checksum"]
        expected = parsed is not None and parsed["checksum"] == expected_checksum
        runtime_checks[name] = {
            "status": "passed" if run["passed"] and expected else "failed",
            "command": {k: v for k, v in run.items() if k not in {"stdout", "stderr"}},
            "reference_checks_passed": bool(parsed and parsed["correct"] is True),
            "checksum": parsed["checksum"] if parsed else None,
            "checksum_matches_stock": expected,
            "elapsed_ns_recorded_but_not_compared": parsed["elapsed_ns"]
            if parsed
            else None,
        }
    checks["link_objects"] = {
        "status": "passed"
        if all(check["status"] == "passed" for check in link_checks.values())
        else "failed",
        "cases": link_checks,
    }
    checks["native_correctness"] = {
        "status": "passed"
        if all(check["status"] == "passed" for check in runtime_checks.values())
        else "failed",
        "cases": runtime_checks,
        "checksums_compared": True,
        "timings_used_for_claims": False,
    }

    report["commands"].extend(recorder.commands)
    required_checks = [
        "pinned_source",
        "toolchain",
        "llc_version",
        "clang_version",
        "compiler_provenance",
        "build_llvm_ir",
        "compile_stock",
        "compile_first",
        "compile_last",
        "two_distinct_legal_actions",
        "exact_state_replay",
        "mismatched_replay_refused",
        "release_mode_action_guards",
        "link_objects",
        "native_correctness",
    ]
    states = [checks[name]["status"] for name in required_checks]
    report["compiler_validation"] = (
        "passed" if all(state == "passed" for state in states) else "failed"
    )
    report["status"] = report["compiler_validation"]
    report_path = artifacts_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Validation status: {report['status']}")
    print(f"Compiler validation: {report['compiler_validation']}")
    print("Model calls: none")
    print(f"Report: {report_path}")
    return 0 if report["compiler_validation"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
