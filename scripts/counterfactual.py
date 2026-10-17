"""Collect pinned-LLVM single-state register-allocation counterfactuals."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from . import advisor, experiment, validate
except ImportError:  # Running as `python scripts/counterfactual.py`.
    import advisor
    import experiment
    import validate


SCHEMA_VERSION = 1
DEFAULT_SITE_QUANTILES = (0.25, 0.75)


class CounterfactualError(RuntimeError):
    """A counterfactual could not be collected from the validated compiler."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _public_command(result: dict[str, Any]) -> dict[str, Any]:
    return experiment._public_command_result(result)


def _validated_toolchain(validation_report: Path) -> dict[str, Any]:
    inputs = experiment._load_validation_inputs(validation_report)
    llvm = inputs["report"]["llvm"]
    clang = Path(llvm["clang"]).expanduser().resolve()
    clang_hash = llvm.get("clang_sha256")
    manifest_clang_hash = inputs["report"]["checks"]["compiler_provenance"][
        "build_manifest"
    ].get("clang_sha256")
    if (
        not clang.is_file()
        or not isinstance(clang_hash, str)
        or _sha256_file(clang) != clang_hash
        or manifest_clang_hash != clang_hash
    ):
        raise experiment.ExperimentError(
            "validated pinned clang binary is missing or has changed"
        )
    return {**inputs, "clang": clang}


def _source_path(path: Path, name: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise CounterfactualError(f"{name} source is not a file: {resolved}")
    return resolved


def _state(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_identity": observation.get("run_identity"),
        "context": observation["context"],
        "observation": observation["observation"],
        "features_sha256": observation["features_sha256"],
        "eligible_actions": [int(action["id"]) for action in observation["actions"]],
    }


def _state_from_decision(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_identity": decision.get("run_identity"),
        "context": decision["context"],
        "observation": decision["observation"],
        "features_sha256": decision["features_sha256"],
        "eligible_actions": [int(action) for action in decision["eligible_actions"]],
    }


def _trace_hash(trace: list[dict[str, Any]]) -> str:
    encoded = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _successful_interactive(result: dict[str, Any], object_path: Path) -> bool:
    return (
        result["return_code"] == 0
        and result["communication_error"] is None
        and not result["timed_out"]
        and result["decision_count"] > 0
        and object_path.is_file()
    )


def _native_result(
    name: str,
    object_path: Path,
    executable_path: Path,
    *,
    driver: Path,
    linker: Path,
    iterations: int,
    artifacts_dir: Path,
    timeout_seconds: int,
    expected_checksum: str | None = None,
) -> dict[str, Any]:
    link = experiment._run_command(
        f"{name}_link",
        [
            str(linker),
            "-std=c11",
            "-O2",
            str(driver),
            str(object_path),
            "-o",
            str(executable_path),
        ],
        artifacts_dir,
        timeout_seconds,
    )
    linked = link["passed"] and executable_path.is_file()
    link_record = {
        "status": "passed" if linked else "failed",
        **_public_command(link),
        "executable_path": str(executable_path),
        "executable_sha256": _sha256_file(executable_path)
        if executable_path.is_file()
        else None,
    }
    runtime_record: dict[str, Any] = {
        "status": "blocked",
        "reason": "link failed",
        "measurements": [],
    }
    if linked:
        run = experiment._run_command(
            f"{name}_run",
            [str(executable_path), str(iterations)],
            artifacts_dir,
            timeout_seconds,
        )
        parsed = validate._read_driver_result(run["_stdout"], iterations)
        checksum_matches = bool(
            parsed
            and (expected_checksum is None or parsed["checksum"] == expected_checksum)
        )
        runtime_record = {
            "status": "passed"
            if run["passed"] and parsed and checksum_matches
            else "failed",
            "measurements": [
                {
                    **_public_command(run),
                    "correct": parsed["correct"] if parsed else False,
                    "iterations": parsed["iterations"] if parsed else None,
                    "checksum": parsed["checksum"] if parsed else None,
                    "checksum_matches_stock": checksum_matches,
                    "elapsed_ns": parsed["elapsed_ns"] if parsed else None,
                }
            ],
        }
    return {"link": link_record, "runtime": runtime_record}


class _FirstCapture:
    def __init__(self) -> None:
        self.observations: list[dict[str, Any]] = []

    def __call__(self, observation: dict[str, Any]) -> int:
        actions = [int(action["id"]) for action in observation["actions"]]
        item: dict[str, Any] = {"state": _state(observation)}
        if len(actions) > 1:
            item["provider_packet"] = json.loads(
                json.dumps(observation["provider_packet"], allow_nan=False)
            )
        self.observations.append(item)
        return actions[0]


def _select_sites(
    ambiguous_count: int,
    *,
    site_quantiles: Sequence[float] | None,
    site_indices: Sequence[int] | None,
) -> tuple[list[int], dict[str, Any]]:
    if site_quantiles is not None and site_indices is not None:
        raise ValueError("choose site_quantiles or site_indices, not both")
    if site_indices is not None:
        if any(type(index) is not int for index in site_indices):
            raise ValueError("site indices must be integers")
        indices = list(site_indices)
        if any(index < 0 or index >= ambiguous_count for index in indices):
            raise ValueError("site index is outside the ambiguous-state sequence")
        if len(set(indices)) != len(indices):
            raise ValueError("site indices must be unique")
        return indices, {"kind": "ordinal_indices", "indices": indices}
    if site_quantiles is None:
        return list(range(ambiguous_count)), {"kind": "all_ambiguous_states"}

    if any(isinstance(q, bool) for q in site_quantiles):
        raise ValueError("site quantiles must be numbers, not booleans")
    quantiles = [float(q) for q in site_quantiles]
    if any(not math.isfinite(q) or not 0 <= q <= 1 for q in quantiles):
        raise ValueError("site quantiles must be finite values between 0 and 1")
    if not ambiguous_count and quantiles:
        raise CounterfactualError("kernel has no multi-action states to select")
    indices = [
        min(ambiguous_count - 1, max(0, math.ceil(q * ambiguous_count) - 1))
        for q in quantiles
    ]
    if len(set(indices)) != len(indices):
        raise CounterfactualError(
            "site quantiles resolve to duplicate states; use fewer sites or a kernel with more multi-action states"
        )
    return indices, {
        "kind": "nearest_rank_quantiles",
        "quantiles": quantiles,
        "ordinal_rule": "ceil(q * ambiguous_state_count) - 1, clamped to valid ordinals",
        "indices": indices,
    }


def collect_case(
    validation_report: Path,
    kernel: Path,
    driver: Path,
    output_dir: Path,
    *,
    site_quantiles: Sequence[float] | None = None,
    site_indices: Sequence[int] | None = None,
    iterations: int | None = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Compile a C kernel with the pinned LLVM toolchain and save baseline states.

    Site ordinals count every multi-action state. Packets stay in memory during
    tracing, then only selected packets are saved.
    """
    inputs = _validated_toolchain(validation_report.expanduser().resolve())
    kernel_path = _source_path(kernel, "kernel")
    driver_path = _source_path(driver, "driver")
    run_iterations = inputs["iterations"] if iterations is None else iterations
    if type(run_iterations) is not int or run_iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")

    case_dir = output_dir.expanduser().resolve()
    case_dir.mkdir(parents=True, exist_ok=False)
    for name in ("commands", "advisor", "objects", "executables", "ir"):
        (case_dir / name).mkdir()
    ir_path = case_dir / "ir" / "kernel.ll"
    clang_command = [
        str(inputs["clang"]),
        "-std=c11",
        "-O2",
        f"--target={validate.TRIPLE}",
        "-S",
        "-emit-llvm",
        str(kernel_path),
        "-o",
        str(ir_path),
    ]
    ir_compile = experiment._run_command(
        "clang_emit_llvm", clang_command, case_dir, timeout_seconds
    )
    ir_ok = ir_compile["passed"] and ir_path.is_file()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "report_path": str(case_dir / "report.json"),
        "provider_calls_attempted": 0,
        "kernel": {"path": str(kernel_path), "sha256": _sha256_file(kernel_path)},
        "driver": {"path": str(driver_path), "sha256": _sha256_file(driver_path)},
        "iterations": run_iterations,
        "validation": {
            "report_path": str(inputs["report_path"]),
            "source_revision": inputs["report"]["llvm"]["source_revision"],
            "replay_identity": inputs["replay_identity"],
            "build_manifest_sha256": inputs["report"]["checks"]["compiler_provenance"][
                "build_manifest"
            ]["manifest_sha256"],
        },
        "toolchain": {
            name: {
                "path": str(path),
                "sha256": _sha256_file(path),
            }
            for name, path in (
                ("clang", inputs["clang"]),
                ("llc", inputs["paths"]["llc"]),
                ("linker", inputs["paths"]["linker"]),
            )
        },
        "ir": {
            "path": str(ir_path),
            "sha256": _sha256_file(ir_path) if ir_ok else None,
            "command": _public_command(ir_compile),
            "machine_verifier": "-verify-machineinstrs is enabled for llc object compilation",
        },
        "baseline": {},
    }
    stock_object = case_dir / "objects" / "stock.o"
    stock_compile_command = validate._run_llc_command(
        inputs["paths"]["llc"], ir_path, stock_object
    )
    stock_compile: dict[str, Any] = {
        "command": stock_compile_command,
        "passed": False,
        "duration_ms": None,
    }
    if ir_ok:
        stock_result = experiment._run_command(
            "stock_llc", stock_compile_command, case_dir, timeout_seconds
        )
        stock_compile = _public_command(stock_result)
        stock_compile["passed"] = stock_result["passed"] and stock_object.is_file()
    stock_executable = case_dir / "executables" / "stock"
    stock_native = (
        _native_result(
            "stock",
            stock_object,
            stock_executable,
            driver=driver_path,
            linker=inputs["paths"]["linker"],
            iterations=run_iterations,
            artifacts_dir=case_dir,
            timeout_seconds=timeout_seconds,
        )
        if stock_compile["passed"]
        else {"link": {"status": "blocked"}, "runtime": {"status": "blocked"}}
    )

    capture = _FirstCapture()
    first_object = case_dir / "objects" / "first.o"
    first_compile_command = validate._interactive_llc_command(
        inputs["paths"]["llc"], ir_path, first_object
    )
    run_identity = validate._replay_identity(
        first_compile_command,
        ir_sha256=report["ir"]["sha256"],
        llc_sha256=inputs["report"]["llvm"]["llc_sha256"],
        llc_version_sha256=inputs["replay_identity"]["llc_version_stdout_sha256"],
        source_revision=inputs["replay_identity"]["llvm_source_revision"],
        guard_source_sha256=inputs["replay_identity"]["mlgo_guard_source_sha256"],
        patch_sha256=inputs["replay_identity"]["guard_patch_sha256"],
        build_manifest_sha256=inputs["replay_identity"]["build_manifest_sha256"],
    )
    first_result: dict[str, Any] | None = None
    first_compile_ok = False
    if ir_ok:
        first_result = advisor.run_interactive(
            first_compile_command,
            llvm_source=inputs["paths"]["llvm_source"],
            policy=capture,
            policy_name="first",
            timeout_seconds=timeout_seconds,
            stdout_path=case_dir / "advisor" / "first.stdout",
            stderr_path=case_dir / "advisor" / "first.stderr",
            run_identity=run_identity,
        )
        first_compile_ok = _successful_interactive(first_result, first_object)

    trace: list[dict[str, Any]] = []
    trace_consistent = first_result is not None and len(capture.observations) == len(
        first_result["decisions"]
    )
    if first_result is not None:
        for captured, decision in zip(
            capture.observations, first_result["decisions"], strict=False
        ):
            state = captured["state"]
            action = int(decision["action_index"])
            if (
                state != _state_from_decision(decision)
                or action != state["eligible_actions"][0]
            ):
                trace_consistent = False
            trace.append({**state, "first_action": action})
    trace_consistent = bool(trace_consistent)
    ambiguous = [
        {
            "trace_index": index,
            "trace": item,
            "provider_packet": captured["provider_packet"],
        }
        for index, (item, captured) in enumerate(
            zip(trace, capture.observations, strict=False)
        )
        if len(item["eligible_actions"]) > 1 and "provider_packet" in captured
    ]
    selectable = ambiguous
    sites: list[dict[str, Any]] = []
    selection: dict[str, Any] = {"kind": "not_selected", "indices": []}
    selection_error: str | None = None
    try:
        if site_quantiles is None and site_indices is None:
            site_quantiles = DEFAULT_SITE_QUANTILES
        selected, selection = _select_sites(
            len(selectable), site_quantiles=site_quantiles, site_indices=site_indices
        )
        sites = [
            {
                "site_index": ordinal,
                "trace_index": selectable[ordinal]["trace_index"],
                "candidate_count": len(
                    selectable[ordinal]["trace"]["eligible_actions"]
                ),
                "state": {
                    key: selectable[ordinal]["trace"][key]
                    for key in (
                        "run_identity",
                        "context",
                        "observation",
                        "features_sha256",
                        "eligible_actions",
                    )
                },
                "provider_packet": selectable[ordinal]["provider_packet"],
            }
            for ordinal in selected
        ]
    except (CounterfactualError, ValueError) as error:
        selection_error = str(error)

    first_executable = case_dir / "executables" / "first"
    stock_measurements = stock_native.get("runtime", {}).get("measurements", [])
    stock_checksum = (
        stock_measurements[0].get("checksum") if stock_measurements else None
    )
    first_native = (
        _native_result(
            "first",
            first_object,
            first_executable,
            driver=driver_path,
            linker=inputs["paths"]["linker"],
            iterations=run_iterations,
            artifacts_dir=case_dir,
            timeout_seconds=timeout_seconds,
            expected_checksum=stock_checksum,
        )
        if first_compile_ok and trace_consistent
        else {"link": {"status": "blocked"}, "runtime": {"status": "blocked"}}
    )
    baseline_ok = (
        ir_ok
        and stock_compile["passed"]
        and stock_native["link"]["status"] == "passed"
        and stock_native["runtime"]["status"] == "passed"
        and first_compile_ok
        and trace_consistent
        and first_native["link"]["status"] == "passed"
        and first_native["runtime"]["status"] == "passed"
    )
    report["ir"]["sha256"] = _sha256_file(ir_path) if ir_path.is_file() else None
    report["baseline"] = {
        "stock": {
            "compile": stock_compile,
            "object_path": str(stock_object),
            "object_sha256": _sha256_file(stock_object)
            if stock_object.is_file()
            else None,
            **stock_native,
        },
        "first": {
            "compile": {
                "command": first_result["compiler_command"]
                if first_result
                else first_compile_command,
                "return_code": first_result["return_code"] if first_result else None,
                "timed_out": first_result["timed_out"] if first_result else False,
                "communication_error": first_result["communication_error"]
                if first_result
                else None,
                "duration_ms": first_result["duration_ms"] if first_result else None,
                "decision_count": first_result["decision_count"] if first_result else 0,
                "stdout_path": first_result["stdout_path"]
                if first_result
                else str(case_dir / "advisor/first.stdout"),
                "stdout_sha256": first_result["stdout_sha256"]
                if first_result
                else None,
                "stderr_path": first_result["stderr_path"]
                if first_result
                else str(case_dir / "advisor/first.stderr"),
                "stderr_sha256": first_result["stderr_sha256"]
                if first_result
                else None,
                "passed": first_compile_ok and trace_consistent,
            },
            "object_path": str(first_object),
            "object_sha256": _sha256_file(first_object)
            if first_object.is_file()
            else None,
            "trace": trace,
            "trace_sha256": _trace_hash(trace),
            "trace_consistent": trace_consistent,
            "ambiguous_state_count": len(ambiguous),
            "selectable_state_count": len(selectable),
            "site_selection": selection,
            "sites": sites,
            **first_native,
        },
    }
    report["paths"] = {
        "ir": str(ir_path),
        "stock_object": str(stock_object),
        "stock_executable": stock_native["link"].get("executable_path"),
        "first_object": str(first_object),
        "first_executable": first_native["link"].get("executable_path"),
    }
    report["status"] = "passed" if baseline_ok and selection_error is None else "failed"
    if selection_error is not None:
        report["failure"] = selection_error
    _write_report(case_dir / "report.json", report)
    return report


class _OneChoicePolicy:
    def __init__(
        self,
        baseline_trace: list[dict[str, Any]],
        target_trace_index: int,
        target_state: dict[str, Any],
        action_index: int,
    ) -> None:
        self.baseline_trace = baseline_trace
        self.target_trace_index = target_trace_index
        self.target_state = target_state
        self.action_index = action_index
        self.observed_states: list[dict[str, Any]] = []
        self.target_positions: list[int] = []

    def __call__(self, observation: dict[str, Any]) -> int:
        current = _state(observation)
        index = len(self.observed_states)
        self.observed_states.append(current)
        if current == self.target_state:
            self.target_positions.append(index)
        if index < self.target_trace_index:
            if index >= len(self.baseline_trace):
                raise advisor.ReplayMismatch(
                    "counterfactual prefix exceeds baseline trace"
                )
            expected = self.baseline_trace[index]
            if (
                current != {key: expected[key] for key in current}
                or int(observation["actions"][0]["id"]) != expected["first_action"]
            ):
                raise advisor.ReplayMismatch(
                    f"baseline state/action prefix diverged before target at trace index {index}"
                )
            return int(expected["first_action"])
        if index == self.target_trace_index:
            if current != self.target_state:
                raise advisor.ReplayMismatch(
                    "selected counterfactual target state or legal actions changed"
                )
            if self.action_index not in current["eligible_actions"]:
                raise advisor.ReplayMismatch(
                    "counterfactual action is no longer eligible"
                )
            return self.action_index
        return int(observation["actions"][0]["id"])


def _read_case(case_or_report: dict[str, Any] | Path) -> dict[str, Any]:
    if isinstance(case_or_report, dict):
        case = case_or_report
    else:
        try:
            case = json.loads(Path(case_or_report).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CounterfactualError(
                f"cannot read counterfactual case report: {error}"
            ) from error
    if (
        not isinstance(case, dict)
        or case.get("schema_version") != SCHEMA_VERSION
        or case.get("status") != "passed"
    ):
        raise CounterfactualError("counterfactual case report is not a passed baseline")
    return case


def compile_choice(
    case_or_report: dict[str, Any] | Path,
    site_index: int,
    action_index: int,
    output_dir: Path,
    *,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Compile one legal changed action at a collected baseline state."""
    case = _read_case(case_or_report)
    validation_report = Path(case["validation"]["report_path"]).resolve()
    inputs = _validated_toolchain(validation_report)
    if type(site_index) is not int or type(action_index) is not int:
        raise ValueError("site_index and action_index must be integers")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    kernel_path = _source_path(Path(case["kernel"]["path"]), "kernel")
    driver_path = _source_path(Path(case["driver"]["path"]), "driver")
    if (
        _sha256_file(kernel_path) != case["kernel"]["sha256"]
        or _sha256_file(driver_path) != case["driver"]["sha256"]
    ):
        raise CounterfactualError("kernel or driver changed after baseline collection")
    ir_path = Path(case["ir"]["path"]).resolve()
    if not ir_path.is_file() or _sha256_file(ir_path) != case["ir"]["sha256"]:
        raise CounterfactualError("collected LLVM IR is missing or has changed")
    baseline = case["baseline"]
    recorded_validation = case["validation"]
    if (
        recorded_validation.get("source_revision")
        != inputs["report"]["llvm"]["source_revision"]
        or recorded_validation.get("build_manifest_sha256")
        != inputs["report"]["checks"]["compiler_provenance"]["build_manifest"][
            "manifest_sha256"
        ]
        or recorded_validation.get("replay_identity") != inputs["replay_identity"]
    ):
        raise CounterfactualError(
            "validated compiler provenance changed after collection"
        )
    trace = baseline["first"]["trace"]
    sites = [
        site for site in baseline["first"]["sites"] if site["site_index"] == site_index
    ]
    if len(sites) != 1:
        raise CounterfactualError(
            f"baseline site index {site_index} is missing or duplicated"
        )
    site = sites[0]
    target_state = site["state"]
    trace_index = site["trace_index"]
    state_fields = (
        "run_identity",
        "context",
        "observation",
        "features_sha256",
        "eligible_actions",
    )
    if (
        type(trace_index) is not int
        or trace_index < 0
        or trace_index >= len(trace)
        or target_state != {key: trace[trace_index].get(key) for key in state_fields}
    ):
        raise CounterfactualError("selected site no longer matches its baseline trace")
    packet_action_ids = [
        int(action["id"]) for action in site["provider_packet"]["actions"]
    ]
    if packet_action_ids != target_state["eligible_actions"]:
        raise CounterfactualError(
            "selected provider packet actions differ from baseline state"
        )
    if action_index not in target_state["eligible_actions"]:
        raise ValueError("action_index is outside the selected state's eligible menu")
    baseline_action = trace[trace_index]["first_action"]
    if action_index == baseline_action:
        raise ValueError(
            "counterfactual action must differ from the baseline first choice"
        )
    stock_executable = Path(baseline["stock"]["link"]["executable_path"])
    stock_executable_hash = baseline["stock"]["link"]["executable_sha256"]
    if (
        not stock_executable.is_file()
        or _sha256_file(stock_executable) != stock_executable_hash
    ):
        raise CounterfactualError("baseline stock executable is missing or has changed")
    stock_measurements = baseline["stock"]["runtime"].get("measurements", [])
    if (
        not stock_measurements
        or stock_measurements[0].get("status") == "failed"
        or not isinstance(stock_measurements[0].get("checksum"), str)
    ):
        raise CounterfactualError("baseline stock checksum is missing")

    result_dir = output_dir.expanduser().resolve()
    result_dir.mkdir(parents=True, exist_ok=False)
    for name in ("commands", "advisor", "objects", "executables"):
        (result_dir / name).mkdir()
    object_path = result_dir / "objects" / "counterfactual.o"
    command = validate._interactive_llc_command(
        inputs["paths"]["llc"], ir_path, object_path
    )
    identity = validate._replay_identity(
        command,
        ir_sha256=case["ir"]["sha256"],
        llc_sha256=inputs["report"]["llvm"]["llc_sha256"],
        llc_version_sha256=inputs["replay_identity"]["llc_version_stdout_sha256"],
        source_revision=inputs["replay_identity"]["llvm_source_revision"],
        guard_source_sha256=inputs["replay_identity"]["mlgo_guard_source_sha256"],
        patch_sha256=inputs["replay_identity"]["guard_patch_sha256"],
        build_manifest_sha256=inputs["replay_identity"]["build_manifest_sha256"],
    )
    policy = _OneChoicePolicy(trace, trace_index, target_state, action_index)
    compiler = advisor.run_interactive(
        command,
        llvm_source=inputs["paths"]["llvm_source"],
        policy=policy,
        policy_name=f"counterfactual_site_{site_index}_action_{action_index}",
        timeout_seconds=timeout_seconds,
        stdout_path=result_dir / "advisor" / "compiler.stdout",
        stderr_path=result_dir / "advisor" / "compiler.stderr",
        run_identity=identity,
    )
    key_occurrences = sum(state == target_state for state in policy.observed_states)
    delivered_once_at_site = (
        policy.target_positions == [trace_index]
        and key_occurrences == 1
        and len(compiler["decisions"]) > trace_index
        and compiler["decisions"][trace_index]["action_index"] == action_index
    )
    prefix_matches = (
        len(policy.observed_states) > trace_index
        and len(compiler["decisions"]) > trace_index
        and all(
            state == {key: trace[index][key] for key in state}
            and compiler["decisions"][index]["action_index"]
            == trace[index]["first_action"]
            for index, state in enumerate(policy.observed_states[:trace_index])
            if index < len(compiler["decisions"])
        )
    )
    compile_ok = _successful_interactive(compiler, object_path)
    compile_passed = compile_ok and prefix_matches and delivered_once_at_site
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "report_path": str(result_dir / "report.json"),
        "site_index": site_index,
        "trace_index": trace_index,
        "target_state": target_state,
        "baseline_first_action": baseline_action,
        "counterfactual_action": action_index,
        "provider_calls_attempted": 0,
        "validation": case["validation"],
        "ir": case["ir"],
        "compile": {
            "status": "passed" if compile_passed else "failed",
            "command": compiler["compiler_command"],
            "return_code": compiler["return_code"],
            "timed_out": compiler["timed_out"],
            "communication_error": compiler["communication_error"],
            "duration_ms": compiler["duration_ms"],
            "decision_count": compiler["decision_count"],
            "decision_trace_sha256": experiment._decision_trace_hash(
                compiler["decisions"]
            ),
            "prefix_matches_baseline": prefix_matches,
            "target_reached_once_at_expected_index": delivered_once_at_site,
            "target_key_occurrences": key_occurrences,
            "object_path": str(object_path),
            "object_sha256": _sha256_file(object_path)
            if object_path.is_file()
            else None,
            "stdout_path": compiler["stdout_path"],
            "stdout_sha256": compiler["stdout_sha256"],
            "stderr_path": compiler["stderr_path"],
            "stderr_sha256": compiler["stderr_sha256"],
        },
        "link": {"status": "blocked"},
        "runtime": {"status": "blocked"},
        "paths": {
            "object": str(object_path),
            "executable": str(result_dir / "executables" / "counterfactual"),
        },
    }
    if compile_passed:
        native = _native_result(
            "counterfactual",
            object_path,
            result_dir / "executables" / "counterfactual",
            driver=driver_path,
            linker=inputs["paths"]["linker"],
            iterations=case["iterations"],
            artifacts_dir=result_dir,
            timeout_seconds=timeout_seconds,
            expected_checksum=baseline["stock"]["runtime"]["measurements"][0][
                "checksum"
            ],
        )
        report.update(native)
    successful = (
        compile_passed
        and report["link"]["status"] == "passed"
        and report["runtime"]["status"] == "passed"
    )
    report["status"] = "passed" if successful else "failed"
    _write_report(result_dir / "report.json", report)
    return report
