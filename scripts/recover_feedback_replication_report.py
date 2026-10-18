"""Reassemble cached results, allowing only the pinned LLVM revision check."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

try:
    from . import feedback_replication_experiment as replication
except ImportError:  # Running as ``python scripts/recover_feedback_replication_report.py``.
    import feedback_replication_experiment as replication


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "feedback-replication-v1-recovery.json"
DEFAULT_SOURCE_OUTPUT = replication.DEFAULT_SOURCE_OUTPUT
RECOVERY_MANIFEST = "report-assembly-recovery.json"
RECOVERED_REPORT = "report.json"
BLOCKS = (1, 2)


class ReportRecoveryError(RuntimeError):
    """Frozen report evidence is incomplete or failed cache-only recovery."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReportRecoveryError(f"cannot read {path}: {error}") from error


def _save(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _timing_cache_hashes(design: dict[str, Any], output: Path) -> dict[str, str]:
    cases = replication._cases(design)
    if len(cases) != 24:
        raise ReportRecoveryError("cache-only recovery requires all 24 frozen cases")
    hashes: dict[str, str] = {}
    for case in cases:
        case_dir = output / "cases" / "discovery" / case["case_id"]
        paths = [
            case_dir / "timings" / "pilot.json",
            case_dir / "timing-calibration.json",
            *(case_dir / "timings" / f"block-{block}.json" for block in BLOCKS),
        ]
        for path in paths:
            if not path.is_file():
                raise ReportRecoveryError(f"cache-only recovery refuses missing timing evidence: {path}")
            hashes[path.relative_to(output).as_posix()] = _sha256(path)
    pilot_count = sum(path.endswith("/pilot.json") for path in hashes)
    calibration_count = sum(path.endswith("/timing-calibration.json") for path in hashes)
    block_count = sum("/block-" in path for path in hashes)
    if (pilot_count, calibration_count, block_count) != (24, 24, 48):
        raise ReportRecoveryError("cache-only recovery requires 24 pilots, 24 calibrations, and 48 timing blocks")
    return hashes


def _file_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReportRecoveryError(f"evidence tree contains a symlink: {path}")
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or relative in (RECOVERED_REPORT, RECOVERY_MANIFEST):
            continue
        hashes[relative] = _sha256(path)
    return hashes


def _input_hashes(paths: dict[str, Path]) -> dict[str, str]:
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ReportRecoveryError(f"frozen input files are missing: {', '.join(missing)}")
    return {name: _sha256(path) for name, path in paths.items()}


def _restore_group_identity(
    attached: list[dict[str, Any]], built: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    group_by_id: dict[str, str] = {}
    for case in built:
        case_id, group, family = case.get("case_id"), case.get("group"), case.get("family")
        if not isinstance(case_id, str) or not isinstance(group, str) or group != family:
            raise ReportRecoveryError("frozen build group does not match its family")
        if case_id in group_by_id:
            raise ReportRecoveryError(f"frozen build repeats case {case_id}")
        group_by_id[case_id] = group
    seen = set()
    for case in attached:
        case_id = case.get("case_id")
        if case_id not in group_by_id or case_id in seen:
            raise ReportRecoveryError("measured case identity differs from frozen build mapping")
        group = group_by_id[case_id]
        if case.get("family") != group or case.get("group", group) != group:
            raise ReportRecoveryError(f"measured family/group identity changed for {case_id}")
        case["group"] = group
        seen.add(case_id)
    if seen != set(group_by_id):
        raise ReportRecoveryError("measured cases do not cover the frozen group mapping")
    return attached


def _blocked(operation: str):
    def reject(*_args, **_kwargs):
        raise ReportRecoveryError(f"cache-only recovery blocked {operation}")

    return reject


def _validation_llvm_source(validation_path: Path) -> Path:
    validation = _load(validation_path)
    llvm = validation.get("llvm") if isinstance(validation, dict) else None
    source = llvm.get("source") if isinstance(llvm, dict) else None
    if not isinstance(source, str) or not source:
        raise ReportRecoveryError("validation report has no LLVM source path")
    return Path(source).expanduser().resolve()


@contextmanager
def _cache_only_guards(
    llvm_source: Path | None = None,
    executed_validation_commands: list[dict[str, Any]] | None = None,
):
    with ExitStack() as guards:
        launch = {"active": False}
        expected_command = (
            ["git", "-C", str(llvm_source.resolve()), "rev-parse", "HEAD"]
            if llvm_source is not None
            else None
        )
        original_run = subprocess.run
        original_popen = subprocess.Popen

        def guarded_run(command: Any, *args: Any, **kwargs: Any):
            safe_kwargs = {"capture_output", "check", "timeout"}
            shell_safe = "shell" not in kwargs or kwargs.get("shell") is False
            if (
                expected_command is None
                or not isinstance(command, list)
                or command != expected_command
                or args
                or set(kwargs) - (safe_kwargs | {"shell"})
                or not shell_safe
                or kwargs.get("capture_output") is not True
                or kwargs.get("check") is not False
                or type(kwargs.get("timeout")) is not int
                or kwargs.get("timeout") != 10
                or launch["active"]
            ):
                return _blocked("unapproved subprocess execution")()
            launch["active"] = True
            try:
                result = original_run(command, *args, **kwargs)
            except BaseException:
                if executed_validation_commands is not None:
                    executed_validation_commands.append(
                        {
                            "command": list(command),
                            "shell": False,
                            "capture_output": True,
                            "check": False,
                            "timeout_seconds": 10,
                            "completed": False,
                        }
                    )
                raise
            finally:
                launch["active"] = False
            stdout = result.stdout
            stderr = result.stderr
            stdout_bytes = stdout if isinstance(stdout, bytes) else str(stdout or "").encode()
            stderr_bytes = stderr if isinstance(stderr, bytes) else str(stderr or "").encode()
            if executed_validation_commands is not None:
                executed_validation_commands.append(
                    {
                        "command": list(command),
                        "shell": False,
                        "capture_output": True,
                        "check": False,
                        "timeout_seconds": 10,
                        "return_code": result.returncode,
                        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
                        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
                    }
                )
            return result

        def guarded_popen(*args: Any, **kwargs: Any):
            if (
                not launch["active"]
                or expected_command is None
                or args != (expected_command,)
                or kwargs != {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
            ):
                return _blocked("unapproved subprocess execution")()
            return original_popen(*args, **kwargs)

        guards.enter_context(mock.patch.object(subprocess, "run", side_effect=guarded_run))
        guards.enter_context(mock.patch.object(subprocess, "Popen", side_effect=guarded_popen))
        if hasattr(os, "posix_spawn"):
            original_posix_spawn = os.posix_spawn

            def guarded_posix_spawn(*args: Any, **kwargs: Any):
                if not launch["active"]:
                    return _blocked("unapproved process execution")()
                return original_posix_spawn(*args, **kwargs)

            guards.enter_context(mock.patch.object(os, "posix_spawn", side_effect=guarded_posix_spawn))
        for target, operation in (
            ("os.system", "shell execution"),
            ("socket.socket.connect", "network access"),
            ("socket.socket.connect_ex", "network access"),
            ("socket.create_connection", "network access"),
            ("urllib.request.urlopen", "network access"),
        ):
            guards.enter_context(mock.patch(target, side_effect=_blocked(operation)))
        for name in ("spawnv", "spawnve", "spawnvp", "spawnvpe"):
            if hasattr(os, name):
                guards.enter_context(mock.patch.object(os, name, side_effect=_blocked("process execution")))
        original_query = replication.feedback._query_provider

        def cache_only_query(packet: dict[str, Any], provider: str, cache_path: Path, *, allow_call: bool):
            if allow_call:
                return _blocked("provider request")()
            return original_query(packet, provider, cache_path, allow_call=False)

        guards.enter_context(mock.patch.object(replication.feedback, "_query_provider", cache_only_query))
        guards.enter_context(
            mock.patch.object(replication.feedback.jev, "choose", side_effect=_blocked("Jev request"))
        )
        guards.enter_context(
            mock.patch.object(
                replication.feedback.codex_advisor, "choose", side_effect=_blocked("Codex request")
            )
        )
        benchmark = replication.contraction.benchmark
        guards.enter_context(mock.patch.object(benchmark, "_run_one", side_effect=_blocked("benchmark run")))
        guards.enter_context(
            mock.patch.object(benchmark, "run_benchmark", side_effect=_blocked("benchmark run"))
        )
        yield


def _run_cached_report(design: dict[str, Any], *args: Any) -> dict[str, Any]:
    original_attach = replication.feedback._attach_compiler_evidence

    def restore(measured: list[dict[str, Any]], built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _restore_group_identity(original_attach(measured, built), built)

    with mock.patch.object(replication.feedback, "_attach_compiler_evidence", side_effect=restore):
        report = replication.run_measure(*args)
    expected_groups = {case["case_id"]: case["group"] for case in replication._cases(design)}
    actual_groups = {case["case_id"]: case.get("group") for case in report.get("cases", [])}
    if actual_groups != expected_groups:
        raise ReportRecoveryError("assembled report does not contain the frozen group mapping")
    return report


def _run_recovery_cache_only(
    design_path: Path,
    validation_path: Path,
    output: Path,
    source_output: Path = DEFAULT_SOURCE_OUTPUT,
    executed_validation_commands: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    design_path, validation_path, output, source_output = (
        path.expanduser().resolve() for path in (design_path, validation_path, output, source_output)
    )
    source_design = (ROOT / "experiments" / "compiler-feedback-v1.json").resolve()
    design = replication._design(design_path, source_design)
    if design.get("provider_max_attempts") != 3:
        raise ReportRecoveryError("report recovery requires the hash-pinned bounded-retry design")
    replication._preflight_build_reports(design, output)
    timing_hashes = _timing_cache_hashes(design, output)

    report_path = output / RECOVERED_REPORT
    manifest_path = output / RECOVERY_MANIFEST
    if report_path.exists() != manifest_path.exists():
        raise ReportRecoveryError("report and recovery manifest must either both exist or both be absent")
    wrapper_hash = _sha256(Path(__file__).resolve())
    frozen_inputs = {
        "design": design_path,
        "source_design": source_design,
        "source_bundle": source_output,
        "validation_report": validation_path,
    }
    frozen_inputs.update({
        f"implementation:{name}": ROOT / name
        for name in replication._implementation_hashes()
    })
    prepared_state = replication._load(output / "prepared.json")
    training_source = prepared_state.get("training_source", {})
    if training_source.get("audit_mode") == "raw_and_public":
        raw_root = (ROOT / design["training_source"]["raw_root"]).resolve()
        for relative, expected_hash in training_source.get("raw_file_sha256", {}).items():
            raw_path = (raw_root / relative).resolve()
            raw_path.relative_to(raw_root)
            if not raw_path.is_file() or _sha256(raw_path) != expected_hash:
                raise ReportRecoveryError(f"source training evidence changed: {relative}")
            frozen_inputs[f"source_raw:{relative}"] = raw_path
    before_inputs = _input_hashes(frozen_inputs)
    before_files = _file_hashes(output)

    prior_manifest = _load(manifest_path) if manifest_path.exists() else None
    expected_group_mapping = {case["case_id"]: case["group"] for case in replication._cases(design)}
    if prior_manifest is not None:
        old = prior_manifest.get("unchanged_hash_evidence", {})
        if (
            prior_manifest.get("status") != "recovered"
            or prior_manifest.get("wrapper_sha256") != wrapper_hash
            or prior_manifest.get("report_sha256") != _sha256(report_path)
            or prior_manifest.get("timing_cache_sha256") != timing_hashes
            or prior_manifest.get("group_mapping") != expected_group_mapping
            or prior_manifest.get("original_error") != {
                "type": "KeyError", "key": "group", "stage": "report serialization"
            }
            or old.get("before") != before_files
            or old.get("after") != before_files
            or old.get("unchanged") is not True
            or old.get("inventory_sha256") != _json_hash(before_files)
            or prior_manifest.get("frozen_input_sha256") != before_inputs
        ):
            raise ReportRecoveryError("prior report recovery manifest or frozen evidence changed")

    report = _run_cached_report(
        design, design_path, validation_path, output, source_output
    )
    if executed_validation_commands is None or len(executed_validation_commands) != 24:
        raise ReportRecoveryError("recovery did not perform the 24 pinned LLVM revision checks")
    if any(command.get("return_code") != 0 for command in executed_validation_commands):
        raise ReportRecoveryError("a pinned LLVM revision check failed")
    report_hash = _sha256(report_path)
    after_files = _file_hashes(output)
    after_inputs = _input_hashes(frozen_inputs)
    if before_files != after_files or before_inputs != after_inputs or timing_hashes != _timing_cache_hashes(design, output):
        raise ReportRecoveryError("cache-only recovery changed existing frozen evidence")
    if prior_manifest is not None:
        if prior_manifest.get("executed_read_only_validation_commands") != executed_validation_commands:
            raise ReportRecoveryError("repeated Git revision checks differ from the recovery manifest")
        if _sha256(report_path) != prior_manifest["report_sha256"] or report != _load(report_path):
            raise ReportRecoveryError("repeated report recovery did not reproduce the exact saved report")
        return prior_manifest

    group_mapping = expected_group_mapping
    if len(group_mapping) != 24:
        raise ReportRecoveryError("frozen recovery group map does not cover 24 cases")
    manifest = {
        "schema_version": 1,
        "status": "recovered",
        "repair_scope": "report_assembly_only",
        "original_error": {"type": "KeyError", "key": "group", "stage": "report serialization"},
        "group_mapping": group_mapping,
        "group_mapping_rule": "Restore group from frozen build metadata after asserting group == family.",
        "report_path": str(report_path),
        "report_sha256": report_hash,
        "wrapper_sha256": wrapper_hash,
        "frozen_runner_sha256": _sha256(Path(replication.__file__).resolve()),
        "timing_cache_sha256": timing_hashes,
        "executed_read_only_validation_commands": executed_validation_commands,
        "unchanged_hash_evidence": {
            "before": before_files,
            "after": after_files,
            "unchanged": True,
            "file_count": len(before_files),
            "inventory_sha256": _json_hash(before_files),
        },
        "frozen_input_sha256": before_inputs,
        "timing_reexecution_allowed": False,
        "provider_requests_allowed": False,
        "source_report_schema_unchanged": True,
        "report_provenance": report["provenance"],
    }
    _save(manifest_path, manifest)
    return manifest


def run_recovery(
    design_path: Path,
    validation_path: Path,
    output: Path,
    source_output: Path = DEFAULT_SOURCE_OUTPUT,
) -> dict[str, Any]:
    """Recover cached evidence; allow only the exact pinned Git revision check."""
    llvm_source = _validation_llvm_source(validation_path)
    executed_validation_commands: list[dict[str, Any]] = []
    with _cache_only_guards(llvm_source, executed_validation_commands):
        return _run_recovery_cache_only(
            design_path,
            validation_path,
            output,
            source_output,
            executed_validation_commands,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--source-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_recovery(args.design, args.validation_report, args.output, args.source_output)
    except (
        ReportRecoveryError,
        replication.FeedbackReplicationError,
        replication.feedback.CompilerFeedbackError,
        replication.contraction.ContractionExperimentError,
        replication.contraction_build.ContractionBuildError,
        OSError,
        ValueError,
    ) as error:
        print(f"feedback-replication report recovery failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "report": result["report_path"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
