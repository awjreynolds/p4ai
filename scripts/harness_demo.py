"""Replay a frozen local policy and verify repeatable native output."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

try:
    from . import (
        experiment,
        harness_policy,
        policy_shift_experiment,
        quality_experiment,
    )
except ImportError:  # Running as ``python scripts/harness_demo.py``.
    sys.path.insert(0, str(ROOT))
    from scripts import (
        experiment,
        harness_policy,
        policy_shift_experiment,
        quality_experiment,
    )


ORDERS = harness_policy.ACTION_ORDERS
ARMS = ("source", "target")
_CHECKSUM = re.compile(r"^[0-9a-f]{16}$")


class HarnessDemoError(RuntimeError):
    """The prepared policy demo inputs or native replay are not trustworthy."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _same_bytes(left: Path, right: Path) -> bool:
    """Compare files directly without loading large executables into memory."""
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            left_chunk, right_chunk = first.read(1024 * 1024), second.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _fingerprint(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise HarnessDemoError(f"cannot fingerprint non-JSON input: {error}") from error
    return _sha256_bytes(payload.encode("utf-8"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value} is not allowed")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HarnessDemoError(f"cannot read JSON from {path}: {error}") from error


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _manifest_path(prepared_path: Path | None, design_path: Path | None) -> Path:
    if prepared_path is not None:
        path = prepared_path.expanduser().resolve()
        if path.is_dir():
            path = path / "prepared-audit.json"
        return path
    if design_path is None:
        raise HarnessDemoError("provide either --prepared or --design")
    design = _read_json(design_path.expanduser().resolve())
    name = design.get("name") if isinstance(design, dict) else None
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise HarnessDemoError("design must have a safe name for locating its prepared audit")
    return ROOT / "artifacts" / name / "prepared-audit.json"


def _inside(root: Path, path_text: Any, label: str) -> Path:
    if not isinstance(path_text, str) or not path_text:
        raise HarnessDemoError(f"{label} path is missing")
    path = Path(path_text).expanduser().resolve()
    root = root.expanduser().resolve()
    if not path.is_relative_to(root):
        raise HarnessDemoError(f"{label} must remain inside the prepared artifact root")
    return path


def _file_reference(root: Path, reference: Any, label: str) -> Path:
    if not isinstance(reference, dict):
        raise HarnessDemoError(f"{label} reference is missing")
    path = _inside(root, reference.get("path"), label)
    expected = reference.get("sha256")
    if not path.is_file() or not isinstance(expected, str) or _sha256(path) != expected:
        raise HarnessDemoError(f"{label} is missing or its SHA-256 changed")
    return path


def _external_binary(value: Any, label: str) -> Path:
    if not isinstance(value, dict):
        raise HarnessDemoError(f"{label} identity is missing")
    path_text, expected = value.get("path"), value.get("sha256")
    if not isinstance(path_text, str) or not path_text:
        raise HarnessDemoError(f"{label} path is missing")
    path = Path(path_text).expanduser().resolve()
    if not path.is_file() or not isinstance(expected, str) or _sha256(path) != expected:
        raise HarnessDemoError(f"{label} binary is missing or its SHA-256 changed")
    return path


def _host_identity(prepared: dict[str, Any]) -> dict[str, str]:
    host = {"system": platform.system(), "machine": platform.machine()}
    if host["system"] != "Darwin" or host["machine"] not in {"arm64", "aarch64"}:
        raise HarnessDemoError("native replay requires an Apple Silicon host")
    prior = prepared.get("provenance", {}).get("platform", {})
    if any(prior.get(key) != value for key, value in host.items()):
        raise HarnessDemoError("native host differs from the prepared compiler audit host")
    return host


def _load_inputs(
    prepared_path: Path,
    case_id: str,
    compiler_arm: str,
    design_path: Path | None,
) -> dict[str, Any]:
    prepared_path = prepared_path.expanduser().resolve()
    prepared = _read_json(prepared_path)
    if (
        not isinstance(prepared, dict)
        or prepared.get("schema_version") != 1
        or prepared.get("status") != "prepared"
        or prepared.get("native_measurements_started") is not False
    ):
        raise HarnessDemoError("prepared audit is missing or has an unsupported state")
    artifact_root = Path(prepared.get("output", "")).expanduser().resolve()
    if not artifact_root.is_dir() or not prepared_path.is_relative_to(artifact_root):
        raise HarnessDemoError("prepared manifest does not reside under its declared output root")

    recorded_design = Path(prepared.get("design", "")).expanduser().resolve()
    if not recorded_design.is_file():
        raise HarnessDemoError("prepared design file is missing")
    if design_path is not None and recorded_design != design_path.expanduser().resolve():
        raise HarnessDemoError("prepared audit references a different design")
    design_sha = _sha256(recorded_design)
    if prepared.get("provenance", {}).get("design_sha256") != design_sha:
        raise HarnessDemoError("prepared design hash differs from its provenance")
    design = _read_json(recorded_design)
    if not isinstance(design, dict) or design.get("name") != "policy-shift-v1":
        raise HarnessDemoError("prepared design is not the frozen policy-shift design")
    if case_id not in prepared.get("audit_case_ids", []):
        raise HarnessDemoError(f"case {case_id!r} is not part of the prepared audit partition")
    case = next(
        (
            row for row in design.get("cases", [])
            if isinstance(row, dict) and row.get("case_id") == case_id
        ),
        None,
    )
    if not isinstance(case, dict) or case.get("partition") != "audit":
        raise HarnessDemoError(f"case {case_id!r} is not an audit case in the frozen design")
    if compiler_arm not in ARMS:
        raise HarnessDemoError("compiler must be source or target")

    build_ref = prepared.get("builds_by_compiler", {}).get(compiler_arm, {}).get(case_id)
    build_path = _file_reference(
        artifact_root,
        {
            "path": build_ref.get("build_report_path") if isinstance(build_ref, dict) else None,
            "sha256": build_ref.get("build_report_sha256") if isinstance(build_ref, dict) else None,
        },
        f"{compiler_arm} build report",
    )
    build = _read_json(build_path)
    if (
        not isinstance(build, dict)
        or build.get("status") != "passed"
        or build.get("case_id") != case_id
        or build.get("partition") != "audit"
        or build.get("group") != case.get("group")
        or build.get("spec") != case.get("spec")
        or build.get("compiler_arm") != compiler_arm
        or build.get("case_fingerprint") != build_ref.get("case_fingerprint")
    ):
        raise HarnessDemoError("prepared build report does not match the frozen audit case")

    artifact_ref = prepared.get("policy_artifacts", {}).get(compiler_arm)
    artifact_file = _file_reference(artifact_root, artifact_ref, f"{compiler_arm} policy artifact")
    wrapper = _read_json(artifact_file)
    if not isinstance(wrapper, dict) or wrapper.get("schema_version") != 1:
        raise HarnessDemoError("policy artifact wrapper has an unsupported schema")
    artifact = wrapper.get("artifact")
    identity = artifact.get("compiler_identity") if isinstance(artifact, dict) else None
    if not isinstance(identity, dict):
        raise HarnessDemoError("policy artifact has no compiler identity")
    if artifact.get("training_provenance") != prepared.get("source_policy_training_provenance"):
        raise HarnessDemoError("policy training provenance differs from the prepared manifest")
    try:
        harness_policy._validate_model(artifact.get("model"))
    except (KeyError, TypeError, ValueError) as error:
        raise HarnessDemoError(f"frozen model is invalid: {error}") from error

    toolchain = build.get("toolchain")
    if not isinstance(toolchain, dict):
        raise HarnessDemoError("build report has no toolchain identity")
    report_identity = toolchain.get("compiler_identity")
    if report_identity != identity:
        raise HarnessDemoError("policy compiler identity differs from the selected build arm")
    if (
        identity.get("sha256") != toolchain.get("sha256")
        or identity.get("target") != toolchain.get("target")
        or toolchain.get("target") != design.get("target")
        or toolchain.get("target_cpu") != design.get("target_cpu")
    ):
        raise HarnessDemoError("compiler identity does not match the frozen target configuration")
    compiler_path = _external_binary(
        {"path": toolchain.get("path"), "sha256": toolchain.get("sha256")},
        "selected compiler",
    )
    linker_info = toolchain.get("linker")
    linker_path = _external_binary(linker_info, "shared linker")
    if linker_info != {
        "path": str(Path(design["linker"]).expanduser().resolve()),
        "sha256": design.get("linker_sha256"),
    }:
        raise HarnessDemoError("build linker differs from the frozen design")
    sdk_info = toolchain.get("sdk")
    if (
        not isinstance(sdk_info, dict)
        or not isinstance(sdk_info.get("path"), str)
        or not isinstance(sdk_info.get("version"), str)
        or not sdk_info["version"]
    ):
        raise HarnessDemoError("build report has no SDK identity")
    sdk_path = Path(sdk_info["path"]).expanduser().resolve()
    if not sdk_path.is_dir() or sdk_path != Path(design["sdk"]).expanduser().resolve():
        raise HarnessDemoError("build SDK differs from the frozen design")

    validation_path = _file_reference(artifact_root, build.get("validation_report"), "toolchain validation")
    validation = _read_json(validation_path)
    kernel_flags = [*design["kernel_flags"], "-isysroot", str(sdk_path)]
    driver_flags = [*design["driver_flags"], "-isysroot", str(sdk_path)]
    if (
        not isinstance(validation, dict)
        or validation.get("compiler_arm") != compiler_arm
        or validation.get("toolchain") != toolchain
        or validation.get("kernel_flags") != kernel_flags
        or validation.get("driver_flags") != driver_flags
        or build.get("kernel_flags") != kernel_flags
        or build.get("driver_flags") != driver_flags
    ):
        raise HarnessDemoError("stored compiler flags or validation record changed")

    build_artifacts = build.get("build_artifacts", {})
    if not isinstance(build_artifacts, dict):
        raise HarnessDemoError("build report has malformed artifact indexes")
    # The frozen choice is the sole authority for the selected action.
    frozen_choices = prepared.get("frozen_source_policy_choices", {}).get(compiler_arm, {})
    frozen_choice = frozen_choices.get(case_id)
    if not isinstance(frozen_choice, dict) or frozen_choice.get("action") not in ORDERS:
        raise HarnessDemoError("prepared source policy choice is missing or invalid")
    action = frozen_choice["action"]
    source_ref = build_artifacts.get("kernel_sources", {}).get(action)
    source_path = _file_reference(artifact_root, source_ref, "selected kernel source")
    driver_object = _file_reference(
        artifact_root, build_artifacts.get("driver_object"), "shared driver object"
    )
    baseline_object = _file_reference(
        artifact_root,
        build_artifacts.get("kernel_objects", {}).get(action),
        "prepared selected kernel object",
    )
    baseline_executable = _file_reference(
        artifact_root,
        build_artifacts.get("executables", {}).get(action),
        "prepared selected executable",
    )
    actions = build.get("actions", {})
    action_report = actions.get(action) if isinstance(actions, dict) else None
    stock_checksum = build.get("stock_checksum")
    correctness = action_report.get("correctness", {}) if isinstance(action_report, dict) else {}
    measurements = correctness.get("runtime", {}).get("measurements", [])
    first_measurement = measurements[0] if isinstance(measurements, list) and measurements else {}
    build_identity = build.get("identity", {})
    correctness_iterations = build.get("correctness_iterations")
    identity_iterations = (
        build_identity.get("correctness_iterations")
        if isinstance(build_identity, dict)
        else None
    )
    if correctness_iterations is None:
        correctness_iterations = identity_iterations
    if (
        not isinstance(action_report, dict)
        or action_report.get("status") != "passed"
        or correctness.get("link", {}).get("status") != "passed"
        or correctness.get("runtime", {}).get("status") != "passed"
        or first_measurement.get("correct") is not True
        or first_measurement.get("checksum_matches_stock") is not True
        or first_measurement.get("checksum") != stock_checksum
        or type(correctness_iterations) is not int
        or correctness_iterations < 1
        or (
            identity_iterations is not None
            and identity_iterations != correctness_iterations
        )
        or first_measurement.get("iterations") != correctness_iterations
        or not isinstance(stock_checksum, str)
        or _CHECKSUM.fullmatch(stock_checksum) is None
    ):
        raise HarnessDemoError("selected prepared action lacks a passing correctness baseline")
    baseline_identity = quality_experiment.code_identity(baseline_object)
    if baseline_identity != action_report.get("code_identity"):
        raise HarnessDemoError("prepared object code identity does not match its build report")

    packet = prepared.get("packets_by_compiler", {}).get(compiler_arm, {}).get(case_id)
    if not isinstance(packet, dict) or _fingerprint(packet) != frozen_choice.get("packet_sha256"):
        raise HarnessDemoError("prepared packet differs from the frozen source-policy choice")
    try:
        decision = harness_policy.choose(artifact, packet, identity)
        repeated_decision = harness_policy.choose(artifact, packet, identity)
    except (KeyError, TypeError, ValueError) as error:
        raise HarnessDemoError(f"offline policy replay failed: {error}") from error
    if (
        decision != repeated_decision
        or decision.get("action") != action
        or decision.get("action_id") != frozen_choice.get("action_id")
        or decision.get("scores") != frozen_choice.get("scores")
    ):
        raise HarnessDemoError("offline replay does not reproduce the prepared frozen choice")

    host = _host_identity(prepared)
    return {
        "prepared_path": prepared_path,
        "prepared": prepared,
        "artifact_root": artifact_root,
        "design": design,
        "design_path": recorded_design,
        "case": case,
        "compiler_arm": compiler_arm,
        "compiler_path": compiler_path,
        "linker_path": linker_path,
        "toolchain": toolchain,
        "sdk_path": sdk_path,
        "identity": identity,
        "artifact": artifact,
        "artifact_path": artifact_file,
        "packet": packet,
        "decision": decision,
        "packet_sha256": _fingerprint(packet),
        "artifact_sha256": _sha256(artifact_file),
        "build_path": build_path,
        "build": build,
        "source_path": source_path,
        "source_sha256": _sha256(source_path),
        "driver_object": driver_object,
        "driver_object_sha256": _sha256(driver_object),
        "baseline_object": baseline_object,
        "baseline_object_sha256": _sha256(baseline_object),
        "baseline_identity": baseline_identity,
        "baseline_executable": baseline_executable,
        "baseline_executable_sha256": _sha256(baseline_executable),
        "stock_checksum": stock_checksum,
        "correctness_iterations": correctness_iterations,
        "kernel_flags": kernel_flags,
        "host": host,
    }


def _compile_repeat(
    inputs: dict[str, Any], output_dir: Path, repeat: int, kernel_source: Path
) -> dict[str, Any]:
    repeat_dir = output_dir / f"repeat-{repeat}"
    repeat_dir.mkdir(parents=True, exist_ok=False)
    object_path = repeat_dir / "selected-kernel.o"
    executable_path = repeat_dir / "selected-kernel"
    command = [
        str(inputs["compiler_path"]),
        *inputs["kernel_flags"],
        "-c",
        str(kernel_source),
        "-o",
        str(object_path),
    ]
    compiled = experiment._run_command(
        f"repeat_{repeat}_object", command, repeat_dir, timeout_seconds=300
    )
    if not compiled.get("passed") or not object_path.is_file():
        raise HarnessDemoError(f"independent kernel compile {repeat} failed")
    code_identity = quality_experiment.code_identity(object_path)
    native = policy_shift_experiment.native_result_with_sdk(
        f"repeat_{repeat}",
        object_path,
        executable_path,
        driver_object=inputs["driver_object"],
        linker=inputs["linker_path"],
        sdk=inputs["sdk_path"],
        iterations=inputs["correctness_iterations"],
        artifacts_dir=repeat_dir,
        timeout_seconds=300,
        expected_checksum=inputs["stock_checksum"],
    )
    measurements = native.get("runtime", {}).get("measurements", [])
    correctness = measurements[0] if measurements else {}
    if (
        native.get("link", {}).get("status") != "passed"
        or native.get("runtime", {}).get("status") != "passed"
        or correctness.get("correct") is not True
        or correctness.get("iterations") != inputs["correctness_iterations"]
        or correctness.get("checksum_matches_stock") is not True
        or correctness.get("checksum") != inputs["stock_checksum"]
    ):
        raise HarnessDemoError(f"independent kernel correctness check {repeat} failed")
    return {
        "repeat": repeat,
        "compile": {
            "status": "passed",
            "return_code": compiled.get("return_code"),
            "stderr_sha256": compiled.get("stderr_sha256"),
        },
        "object": {
            "path": str(object_path),
            "sha256": _sha256(object_path),
            "bytes": object_path.stat().st_size,
            "code_identity": code_identity,
        },
        "link": {
            "status": native["link"]["status"],
            "return_code": native["link"].get("return_code"),
        },
        "executable": {
            "path": str(executable_path),
            "sha256": _sha256(executable_path),
            "bytes": executable_path.stat().st_size,
        },
        "correctness": {
            "status": native["runtime"]["status"],
            "correct": correctness["correct"],
            "iterations": correctness["iterations"],
            "checksum": correctness["checksum"],
            "checksum_matches_stock": correctness["checksum_matches_stock"],
        },
    }


def run_demo(
    *,
    case_id: str,
    compiler_arm: str,
    output: Path,
    prepared_path: Path | None = None,
    design_path: Path | None = None,
) -> dict[str, Any]:
    if (prepared_path is None) == (design_path is None):
        raise HarnessDemoError("provide exactly one of --prepared or --design")
    if design_path is not None:
        design_path = design_path.expanduser().resolve()
    manifest_path = _manifest_path(prepared_path, design_path)
    inputs = _load_inputs(manifest_path, case_id, compiler_arm, design_path)
    output = output.expanduser().resolve()
    if manifest_path.is_relative_to(output):
        raise HarnessDemoError("demo output must not overwrite the prepared audit manifest")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise HarnessDemoError("demo output directory must be empty")

    selected_kernel = output / "selected-kernel.c"
    shutil.copyfile(inputs["source_path"], selected_kernel)
    if _sha256(selected_kernel) != inputs["source_sha256"]:
        raise HarnessDemoError("selected kernel copy differs from the prepared source")
    decision_record = {
        "schema_version": 1,
        "case_id": case_id,
        "compiler_arm": compiler_arm,
        "compiler_identity": inputs["identity"],
        "artifact_sha256": inputs["artifact_sha256"],
        "packet_sha256": inputs["packet_sha256"],
        "action": inputs["decision"]["action"],
        "action_id": inputs["decision"]["action_id"],
        "scores": inputs["decision"]["scores"],
        "decision_repeat_matches": True,
    }
    decision_path = output / "decision.json"
    _write_json(decision_path, decision_record)

    repeats = [
        _compile_repeat(inputs, output, repeat, selected_kernel)
        for repeat in (1, 2)
    ]
    first, second = repeats
    baseline_exec_size = inputs["baseline_executable"].stat().st_size
    comparisons = {
        "decision_repeated": True,
        "rebuild_object_sha256_equal": first["object"]["sha256"] == second["object"]["sha256"],
        "rebuild_code_identity_equal": first["object"]["code_identity"] == second["object"]["code_identity"],
        "rebuild_executable_sha256_equal": first["executable"]["sha256"] == second["executable"]["sha256"],
        "rebuild_executable_content_equal": _same_bytes(
            Path(first["executable"]["path"]), Path(second["executable"]["path"])
        ),
        "rebuild_executable_size_equal": first["executable"]["bytes"] == second["executable"]["bytes"],
        "prepared_object_sha256_equal": first["object"]["sha256"] == inputs["baseline_object_sha256"],
        "prepared_code_identity_equal": first["object"]["code_identity"] == inputs["baseline_identity"],
        "prepared_executable_sha256_equal": first["executable"]["sha256"] == inputs["baseline_executable_sha256"],
        "prepared_executable_content_equal": _same_bytes(
            Path(first["executable"]["path"]), inputs["baseline_executable"]
        ),
        "prepared_executable_size_equal": first["executable"]["bytes"] == baseline_exec_size,
    }
    repeatability_passed = all(
        comparisons[key]
        for key in (
            "decision_repeated",
            "rebuild_object_sha256_equal",
            "rebuild_code_identity_equal",
            "rebuild_executable_sha256_equal",
            "rebuild_executable_content_equal",
        )
    )
    report = {
        "schema_version": 1,
        "status": "passed" if repeatability_passed else "not_repeatable",
        "correctness_status": "passed",
        "repeatability_status": "passed" if repeatability_passed else "failed",
        "correctness_iterations": inputs["correctness_iterations"],
        "case_id": case_id,
        "group": inputs["case"]["group"],
        "compiler_arm": compiler_arm,
        "target_identity": {
            "compiler_identity": inputs["identity"],
            "target_cpu": inputs["toolchain"]["target_cpu"],
            "sdk": inputs["toolchain"]["sdk"],
            "link_driver": inputs["toolchain"]["linker"],
            "host": inputs["host"],
        },
        "prepared_manifest_sha256": _sha256(manifest_path),
        "build_report_sha256": _sha256(inputs["build_path"]),
        "source_kernel": {
            "path": str(selected_kernel),
            "sha256": _sha256(selected_kernel),
        },
        "driver_object_sha256": inputs["driver_object_sha256"],
        "decision": decision_record,
        "repeats": repeats,
        "comparisons": comparisons,
        "interpretation": (
            "Repeatability is observed only for these outputs on this host and recorded compiler, "
            "link-driver, and SDK identity; the SDK is identified by path and version, not by a "
            "whole-SDK content hash. This is not a hermetic or cross-machine reproducibility "
            "guarantee. The driver ran once per repeat for correctness; no performance benchmark "
            "was collected."
        ),
    }
    report_path = output / "harness-demo.json"
    _write_json(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prepared", type=Path)
    source.add_argument("--design", type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--compiler", choices=ARMS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_demo(
            case_id=args.case_id,
            compiler_arm=args.compiler,
            output=args.output,
            prepared_path=args.prepared,
            design_path=args.design,
        )
    except (HarnessDemoError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"harness demo failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
