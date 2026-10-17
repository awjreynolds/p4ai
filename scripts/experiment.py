"""Run one provider intervention against a previously validated LLVM build."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from . import advisor, codex_advisor, jev, validate
except ImportError:  # Running as `python scripts/experiment.py`.
    import advisor
    import codex_advisor
    import jev
    import validate


ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_FIELDS = {
    "provider",
    "chosen_action",
    "probabilities",
    "confidence",
    "confidence_type",
    "confidence_semantics",
    "model",
    "reasoning_effort",
    "usage",
    "latency_ms",
    "reason",
}


class ExperimentError(RuntimeError):
    """The experiment cannot proceed with the supplied validation evidence."""


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def confidence_threshold(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1") from error
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1")
    return parsed


def _provider_provenance(response: dict[str, Any]) -> dict[str, Any]:
    provenance = {key: response[key] for key in PROVENANCE_FIELDS if key in response}
    try:
        json.dumps(provenance, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("provider response is not safe JSON metadata") from error
    return provenance


class OneInterventionPolicy:
    """Use one provider choice at the first multi-action state, then first-allowed."""

    def __init__(
        self,
        method: str,
        threshold: float = 0.5,
        *,
        jev_choose: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        codex_choose: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        if method not in {"jev", "codex", "cascade"}:
            raise ValueError(f"unsupported method: {method}")
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("confidence threshold must be between 0 and 1")
        self.method = method
        self.threshold = threshold
        self.jev_choose = jev_choose or jev.choose
        self.codex_choose = codex_choose or codex_advisor.choose
        self.attempted_intervention = False
        self.singleton_skips = 0
        self.provider_attempts: list[dict[str, Any]] = []
        self.first_packet: dict[str, Any] | None = None
        self.first_state: dict[str, Any] | None = None
        self.selected_action: int | None = None
        self.selected_action_id: str | None = None
        self.selection_source: str | None = None

    def __call__(self, observation: dict[str, Any]) -> int | dict[str, Any]:
        actions = observation["actions"]
        first_action = int(actions[0]["id"])
        if self.attempted_intervention:
            return first_action
        if len(actions) < 2:
            self.singleton_skips += 1
            return first_action

        self.attempted_intervention = True
        packet = observation["provider_packet"]
        self.first_packet = json.loads(json.dumps(packet, allow_nan=False))
        self.first_state = {
            "context": observation["context"],
            "observation": observation["observation"],
            "features_sha256": observation["features_sha256"],
            "eligible_actions": [int(action["id"]) for action in actions],
        }

        decision = self._choose(packet, actions)
        if decision is None:
            self.selected_action = first_action
            self.selected_action_id = str(first_action)
            self.selection_source = "mechanical_fallback"
            return first_action

        self.selected_action_id = decision["chosen_action"]
        self.selected_action = int(self.selected_action_id)
        self.selection_source = decision["provider"]
        return decision

    def _ask(
        self,
        provider: str,
        choose: Callable[[dict[str, Any]], dict[str, Any]],
        packet: dict[str, Any],
        actions: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        try:
            response = choose(packet)
        except (jev.JevUnavailable, codex_advisor.CodexUnavailable) as error:
            self.provider_attempts.append(
                {"provider": provider, "status": "unavailable", "reason": error.reason}
            )
            return None

        try:
            if not isinstance(response, dict):
                raise TypeError("provider response must be an object")
            if response.get("provider") != provider:
                raise ValueError(
                    "provider provenance does not match the requested provider"
                )
            action_id = response.get("chosen_action")
            eligible_ids = {action["id"] for action in actions}
            if not isinstance(action_id, str) or action_id not in eligible_ids:
                raise ValueError("provider chose an action outside the eligible menu")
            if provider == "jev":
                confidence = response.get("confidence")
                if (
                    isinstance(confidence, bool)
                    or not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1
                ):
                    raise ValueError("Jev response has invalid confidence")
            provenance = _provider_provenance(response)
        except (TypeError, ValueError):
            self.provider_attempts.append(
                {
                    "provider": provider,
                    "status": "rejected",
                    "reason": "invalid_response",
                }
            )
            return None

        attempt = {"provider": provider, "status": "available", "response": provenance}
        self.provider_attempts.append(attempt)
        return provenance

    def _choose(
        self, packet: dict[str, Any], actions: list[dict[str, str]]
    ) -> dict[str, Any] | None:
        if self.method == "jev":
            return self._ask("jev", self.jev_choose, packet, actions)
        if self.method == "codex":
            return self._ask("codex", self.codex_choose, packet, actions)

        jev_response = self._ask("jev", self.jev_choose, packet, actions)
        if jev_response is not None:
            confidence = jev_response.get("confidence")
            if (
                isinstance(confidence, (int, float))
                and not isinstance(confidence, bool)
                and math.isfinite(confidence)
                and confidence >= self.threshold
            ):
                return jev_response
            self.provider_attempts[-1]["status"] = "available_routed_low_confidence"
            self.provider_attempts[-1]["routing_threshold"] = self.threshold
        return self._ask("codex", self.codex_choose, packet, actions)

    def report(self) -> dict[str, Any]:
        return {
            "intervention_attempted": self.attempted_intervention,
            "singleton_states_skipped": self.singleton_skips,
            "first_packet": self.first_packet,
            "first_state": self.first_state,
            "provider_attempts": self.provider_attempts,
            "selected_action": self.selected_action,
            "selected_action_id": self.selected_action_id,
            "selection_source": self.selection_source,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_validation_inputs(report_path: Path) -> dict[str, Any]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExperimentError(f"cannot read validation report: {error}") from error
    if not isinstance(report, dict):
        raise ExperimentError("validation report must be a JSON object")
    if (
        report.get("status") != "passed"
        or report.get("compiler_validation") != "passed"
    ):
        raise ExperimentError("pinned compiler validation has not passed")
    if report.get("model_calls") is not False:
        raise ExperimentError(
            "validation report must confirm that it made no model calls"
        )

    llvm = report.get("llvm")
    checks = report.get("checks")
    corpus = report.get("corpus")
    if (
        not isinstance(llvm, dict)
        or not isinstance(checks, dict)
        or not isinstance(corpus, dict)
    ):
        raise ExperimentError(
            "validation report is missing LLVM, checks, or corpus data"
        )
    if llvm.get("source_revision") != llvm.get("required_revision"):
        raise ExperimentError(
            "validation report does not identify the required pinned source"
        )
    for name in (
        "pinned_source",
        "toolchain",
        "compiler_provenance",
        "build_llvm_ir",
        "compile_stock",
        "compile_first",
        "compile_last",
        "two_distinct_legal_actions",
        "exact_state_replay",
        "release_mode_action_guards",
        "native_correctness",
    ):
        check = checks.get(name)
        if not isinstance(check, dict) or check.get("status") != "passed":
            raise ExperimentError(f"validation prerequisite {name!r} did not pass")

    report_dir = report_path.resolve().parent
    ir_record = checks["build_llvm_ir"]
    if not isinstance(ir_record.get("ir_path"), str):
        raise ExperimentError("validation report has no LLVM IR path")
    ir_path = (report_dir / ir_record["ir_path"]).resolve()
    if not ir_path.is_relative_to(report_dir) or not ir_path.is_file():
        raise ExperimentError(
            "validated LLVM IR is missing from its artifact directory"
        )
    ir_sha256 = _file_sha256(ir_path)
    if ir_sha256 != ir_record.get("ir_sha256"):
        raise ExperimentError("validated LLVM IR hash does not match its report")

    stock = checks["native_correctness"].get("cases", {}).get("stock", {})
    expected_checksum = stock.get("checksum")
    if (
        stock.get("status") != "passed"
        or not isinstance(expected_checksum, str)
        or re.fullmatch(r"[0-9a-f]{16}", expected_checksum) is None
    ):
        raise ExperimentError("stock native correctness result is missing")
    iterations = corpus.get("iterations")
    if type(iterations) is not int or iterations <= 0:
        raise ExperimentError(
            "validation report has no positive native iteration count"
        )

    paths = {
        "llvm_source": Path(llvm["source"]).expanduser().resolve(),
        "llc": Path(llvm["llc"]).expanduser().resolve(),
        "linker": Path(llvm["linker"]).expanduser().resolve(),
        "driver": Path(corpus["driver"]).expanduser().resolve(),
        "ir": ir_path,
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise ExperimentError(f"validated inputs are missing: {', '.join(missing)}")
    expected_hashes = (
        (paths["llc"], llvm.get("llc_sha256"), "llc"),
        (paths["linker"], llvm.get("linker_sha256"), "linker"),
        (paths["driver"], corpus.get("driver_sha256"), "native driver"),
    )
    for path, expected_hash, label in expected_hashes:
        if not isinstance(expected_hash, str) or _file_sha256(path) != expected_hash:
            raise ExperimentError(f"validated {label} hash does not match its report")
    provenance = checks["compiler_provenance"].get("build_manifest")
    manifest_path = Path(llvm.get("build_manifest", "")).expanduser().resolve()
    if (
        not isinstance(provenance, dict)
        or provenance.get("status") != "passed"
        or not manifest_path.is_file()
        or _file_sha256(manifest_path) != provenance.get("manifest_sha256")
    ):
        raise ExperimentError("validated compiler build manifest has changed")
    patch_path = validate.PATCH_FILE.resolve()
    if not patch_path.is_file() or _file_sha256(patch_path) != provenance.get(
        "patch_sha256"
    ):
        raise ExperimentError("validated LLVM guard patch has changed")
    identity = report.get("replay_identity")
    if not isinstance(identity, dict) or (
        identity.get("ir_sha256") != ir_sha256
        or identity.get("llc_sha256") != llvm.get("llc_sha256")
        or identity.get("llvm_source_revision") != llvm.get("required_revision")
        or identity.get("mlgo_guard_source_sha256") != llvm.get("guard_source_sha256")
        or identity.get("guard_patch_sha256") != provenance.get("patch_sha256")
        or identity.get("build_manifest_sha256") != provenance.get("manifest_sha256")
    ):
        raise ExperimentError("validation replay identity is missing or inconsistent")
    if llvm.get("target") != validate.TRIPLE:
        raise ExperimentError(
            "validation report target does not match the native host target"
        )
    try:
        source_revision = subprocess.run(
            ["git", "-C", str(paths["llvm_source"]), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExperimentError("could not verify the LLVM source checkout") from error
    if (
        source_revision.returncode != 0
        or source_revision.stdout.decode("utf-8", errors="replace").strip()
        != llvm["required_revision"]
    ):
        raise ExperimentError(
            "LLVM source checkout no longer matches the validated revision"
        )
    guard_path = paths["llvm_source"] / "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp"
    if not guard_path.is_file() or _file_sha256(guard_path) != llvm.get(
        "guard_source_sha256"
    ):
        raise ExperimentError("validated LLVM action-guard source has changed")
    return {
        "report": report,
        "report_path": report_path.resolve(),
        "paths": paths,
        "expected_checksum": expected_checksum,
        "iterations": iterations,
        "replay_identity": identity,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _run_command(
    name: str, command: list[str], artifacts_dir: Path, timeout_seconds: int
) -> dict[str, Any]:
    stdout_path = artifacts_dir / "commands" / f"{name}.stdout"
    stderr_path = artifacts_dir / "commands" / f"{name}.stderr"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout, stderr, return_code = result.stdout, result.stderr, result.returncode
        error = None
    except subprocess.TimeoutExpired as exception:
        stdout = exception.stdout or b""
        stderr = exception.stderr or b""
        return_code = None
        error = f"timed out after {timeout_seconds} seconds"
    except OSError as exception:
        stdout = b""
        stderr = b""
        return_code = None
        error = f"could not start command: {exception}"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    return {
        "command": command,
        "return_code": return_code,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "stdout_path": str(stdout_path),
        "stdout_sha256": _sha256(stdout),
        "stderr_path": str(stderr_path),
        "stderr_sha256": _sha256(stderr),
        "error": error,
        "passed": return_code == 0 and error is None,
        "_stdout": stdout,
        "_stderr": stderr,
    }


def _public_command_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if not key.startswith("_")}


def _decision_trace_hash(decisions: list[dict[str, Any]]) -> str:
    trace = [
        {
            "context": item.get("context"),
            "observation": item.get("observation"),
            "eligible_actions": item.get("eligible_actions"),
            "features_sha256": item.get("features_sha256"),
            "action_index": item.get("action_index"),
        }
        for item in decisions
    ]
    return _sha256(json.dumps(trace, sort_keys=True, separators=(",", ":")).encode())


def run_experiment(
    validation_report_path: Path,
    *,
    method: str,
    artifacts_dir: Path,
    confidence: float = 0.5,
    timeout_seconds: int = 300,
    measurements: int = 5,
) -> dict[str, Any]:
    inputs = _load_validation_inputs(validation_report_path)
    report_data = inputs["report"]
    paths = inputs["paths"]
    artifacts_dir.mkdir(parents=True, exist_ok=False)
    for subdir in ("advisor", "commands", "objects", "executables"):
        (artifacts_dir / subdir).mkdir()

    experiment: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "report_path": str(artifacts_dir / "report.json"),
        "method": method,
        "routing": {
            "confidence_threshold": confidence,
            "interpretation": "UNCALIBRATED_ROUTING_DEMONSTRATION",
            "performance_claims": False,
        },
        "validation": {
            "report_path": str(inputs["report_path"]),
            "compiler_validation": report_data["compiler_validation"],
            "source_revision": report_data["llvm"]["source_revision"],
            "replay_identity": inputs["replay_identity"],
            "ir_path": str(paths["ir"]),
            "ir_sha256": _file_sha256(paths["ir"]),
            "expected_stock_checksum": inputs["expected_checksum"],
            "iterations_per_measurement": inputs["iterations"],
        },
        "provider_calls_attempted": 0,
        "model_responses_received": 0,
        "claim_scope": "This run records one provider intervention and validates compiler/native correctness; elapsed times are recorded without a performance comparison.",
        "first_intervention": {},
        "compile": {},
        "link": {},
        "runtime": {},
    }
    output_path = artifacts_dir / "report.json"
    _write_report(output_path, experiment)

    controller = OneInterventionPolicy(method, confidence)
    object_path = artifacts_dir / "objects" / f"{method}.o"
    command = validate._interactive_llc_command(paths["llc"], paths["ir"], object_path)
    compiler_result = advisor.run_interactive(
        command,
        llvm_source=paths["llvm_source"],
        policy=controller,
        policy_name=f"one_intervention_{method}",
        timeout_seconds=timeout_seconds,
        stdout_path=artifacts_dir / "advisor/compiler.stdout",
        stderr_path=artifacts_dir / "advisor/compiler.stderr",
        run_identity=inputs["replay_identity"],
    )
    experiment["provider_calls_attempted"] = len(controller.provider_attempts)
    experiment["model_responses_received"] = sum(
        "response" in attempt for attempt in controller.provider_attempts
    )
    experiment["first_intervention"] = controller.report()
    decisions = compiler_result["decisions"]
    selected_row = None
    if controller.first_state is not None:
        selected_row = next(
            (
                item
                for item in decisions
                if item["context"] == controller.first_state["context"]
                and item["observation"] == controller.first_state["observation"]
                and item["features_sha256"] == controller.first_state["features_sha256"]
            ),
            None,
        )
    intervention_delivered = (
        selected_row is not None
        and selected_row["action_index"] == controller.selected_action
        and selected_row["eligible_actions"]
        == controller.first_state["eligible_actions"]
    )
    compile_ok = (
        compiler_result["return_code"] == 0
        and compiler_result["communication_error"] is None
        and not compiler_result["timed_out"]
        and compiler_result["decision_count"] > 0
        and object_path.is_file()
    )
    experiment["compile"] = {
        "status": "passed" if compile_ok and intervention_delivered else "failed",
        "command": compiler_result["compiler_command"],
        "return_code": compiler_result["return_code"],
        "timed_out": compiler_result["timed_out"],
        "communication_error": compiler_result["communication_error"],
        "decision_count": compiler_result["decision_count"],
        "decision_trace_sha256": _decision_trace_hash(decisions),
        "intervention_delivered": intervention_delivered,
        "object_path": str(object_path),
        "object_sha256": _sha256(object_path.read_bytes())
        if object_path.is_file()
        else None,
        "advisor_stdout_path": compiler_result["stdout_path"],
        "advisor_stdout_sha256": compiler_result["stdout_sha256"],
        "advisor_stderr_path": compiler_result["stderr_path"],
        "advisor_stderr_sha256": compiler_result["stderr_sha256"],
    }
    if selected_row is not None:
        experiment["first_intervention"]["action_delivered"] = selected_row[
            "action_index"
        ]
        experiment["first_intervention"]["action_sha256"] = selected_row[
            "action_sha256"
        ]

    _write_report(output_path, experiment)
    if not compile_ok:
        experiment["status"] = "compiler_failed"
        _write_report(output_path, experiment)
        return experiment

    executable = artifacts_dir / "executables" / method
    link = _run_command(
        "link",
        [
            str(paths["linker"]),
            "-std=c11",
            "-O2",
            str(paths["driver"]),
            str(object_path),
            "-o",
            str(executable),
        ],
        artifacts_dir,
        timeout_seconds,
    )
    experiment["link"] = {
        "status": "passed" if link["passed"] and executable.is_file() else "failed",
        **_public_command_result(link),
        "executable_path": str(executable),
        "executable_sha256": _sha256(executable.read_bytes())
        if executable.is_file()
        else None,
    }
    _write_report(output_path, experiment)
    if experiment["link"]["status"] != "passed":
        experiment["status"] = "link_failed"
        _write_report(output_path, experiment)
        return experiment

    runs: list[dict[str, Any]] = []
    for index in range(measurements):
        result = _run_command(
            f"run_{index + 1}",
            [str(executable), str(inputs["iterations"])],
            artifacts_dir,
            timeout_seconds,
        )
        parsed = validate._read_driver_result(result["_stdout"], inputs["iterations"])
        checksum_matches = bool(
            parsed and parsed["checksum"] == inputs["expected_checksum"]
        )
        runs.append(
            {
                "status": "passed"
                if result["passed"] and parsed is not None and checksum_matches
                else "failed",
                **_public_command_result(result),
                "correct": parsed["correct"] if parsed else False,
                "iterations": parsed["iterations"] if parsed else None,
                "checksum": parsed["checksum"] if parsed else None,
                "checksum_matches_stock": checksum_matches,
                "elapsed_ns": parsed["elapsed_ns"] if parsed else None,
            }
        )
        experiment["runtime"] = {
            "status": "passed"
            if all(run["status"] == "passed" for run in runs)
            else "failed",
            "measurements": list(runs),
            "timings_used_for_claims": False,
        }
        _write_report(output_path, experiment)

    runtime_ok = all(run["status"] == "passed" for run in runs)
    provider_selected = controller.selection_source in {"jev", "codex"}
    if not runtime_ok:
        experiment["status"] = "native_validation_failed"
    elif not provider_selected:
        experiment["status"] = (
            "unresolved_provider_unavailable"
            if controller.attempted_intervention
            else "unresolved_no_multi_action_state"
        )
    elif not intervention_delivered:
        experiment["status"] = "intervention_not_delivered"
    else:
        experiment["status"] = "completed"
    _write_report(output_path, experiment)
    return experiment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--method", choices=("jev", "codex", "cascade"), required=True)
    parser.add_argument(
        "--confidence-threshold", type=confidence_threshold, default=0.5
    )
    parser.add_argument("--timeout-seconds", type=positive_int, default=300)
    parser.add_argument("--measurements", type=positive_int, default=5)
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args(argv)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    artifacts_dir = (
        args.artifacts_dir.expanduser().resolve()
        if args.artifacts_dir
        else ROOT / "artifacts" / f"llvm-regalloc-experiment-{timestamp}"
    )
    try:
        experiment = run_experiment(
            args.validation_report.expanduser().resolve(),
            method=args.method,
            artifacts_dir=artifacts_dir,
            confidence=args.confidence_threshold,
            timeout_seconds=args.timeout_seconds,
            measurements=args.measurements,
        )
    except (ExperimentError, FileExistsError, OSError, ValueError) as error:
        print(f"Experiment blocked: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": experiment["status"],
                "method": experiment["method"],
                "report_path": experiment["report_path"],
                "selection_source": experiment["first_intervention"].get(
                    "selection_source"
                ),
                "selected_action": experiment["first_intervention"].get(
                    "selected_action"
                ),
                "runtime_status": experiment["runtime"].get("status"),
                "measurement_count": len(experiment["runtime"].get("measurements", [])),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if experiment["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
