"""Adapt a frozen loop-order policy to fresh workload labels."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

try:
    from . import (
        compiler_feedback_experiment as feedback,
    )
    from . import (
        contraction_experiment as contraction,
    )
    from . import (
        feedback_replication_experiment as replication,
    )
    from . import (
        harness_policy,
    )
    from . import (
        policy_shift_experiment as shift,
    )
except ImportError:  # Running as ``python scripts/policy_repair_experiment.py``.
    import compiler_feedback_experiment as feedback
    import contraction_experiment as contraction
    import feedback_replication_experiment as replication
    import harness_policy
    import policy_shift_experiment as shift


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "workload-policy-repair-v1.json"
SCHEMA_VERSION = 1
ORDERS = tuple(contraction.ORDERS)
ACTION_NAMES = ("stock", *ORDERS)
CHOICE_METHODS = ("unchanged_source_forest", "stride_selector", "target_refit", "direct_patch")
COMPARES = (
    ("target_refit_vs_unchanged_source_forest", "target_refit", "unchanged_source_forest"),
    ("direct_patch_vs_unchanged_source_forest", "direct_patch", "unchanged_source_forest"),
    ("target_refit_vs_direct_patch", "target_refit", "direct_patch"),
)


class PolicyRepairError(RuntimeError):
    """The workload-policy repair evidence is incomplete or inconsistent."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyRepairError(f"cannot read JSON from {path}: {error}") from error


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _save_frozen(path: Path, value: Any, label: str) -> None:
    if path.is_file():
        if _load(path) != value:
            raise PolicyRepairError(f"frozen {label} differs from existing evidence")
    else:
        _save(path, value)


def _repo_path(value: str) -> Path:
    path = Path(value)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise PolicyRepairError("design paths must remain inside the repository")
    return resolved


def _design(path: Path) -> dict[str, Any]:
    design = _load(path)
    if not isinstance(design, dict) or design.get("schema_version") != SCHEMA_VERSION:
        raise PolicyRepairError("unsupported workload-policy repair design")
    if design.get("name") != "workload-policy-repair-v1":
        raise PolicyRepairError("unexpected workload-policy repair design")
    required_hashes = (
        ("shift_design", "shift_design_sha256"),
        ("audit_report", "audit_report_sha256"),
        ("prepared_audit", "prepared_audit_sha256"),
    )
    for path_key, hash_key in required_hashes:
        value, digest = design.get(path_key), design.get(hash_key)
        if not isinstance(value, str) or not isinstance(digest, str) or len(digest) != 64:
            raise PolicyRepairError(f"design {path_key} and {hash_key} must be pinned")
    bundle = design.get("public_audit_bundle")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("path"), str):
        raise PolicyRepairError("public audit evidence bundle is not pinned")
    if not isinstance(bundle.get("sha256"), str) or len(bundle["sha256"]) != 64:
        raise PolicyRepairError("public audit evidence bundle hash is invalid")
    if design.get("benchmark_rounds") != 11 or design.get("benchmark_blocks") != 2:
        raise PolicyRepairError("repair measurements require two independent eleven-round blocks")
    training = design.get("training")
    if (
        not isinstance(training, dict)
        or training.get("compiler") != "target"
        or training.get("historical_partition") != "audit"
        or training.get("new_partition") != "adaptation"
        or training.get("recipe_count") != 6
        or training.get("label_block") != 1
        or training.get("case_count") != 12
        or training.get("action_rows") != 72
    ):
        raise PolicyRepairError("frozen target-only training contract changed")
    if design.get("forest") != {
        "n_estimators": 64,
        "max_depth": 6,
        "min_samples_leaf": 2,
        "random_state": 20261020,
        "n_jobs": 1,
    }:
        raise PolicyRepairError("fixed forest parameters changed")
    if design.get("sklearn_version") != "1.9.1":
        raise PolicyRepairError("fixed scikit-learn version changed")
    if design.get("minimum_positive_test_groups") != 4:
        raise PolicyRepairError("positive test-group threshold changed")
    if (
        design.get("noise_floor_fraction") != 0.02
        or design.get("minimum_repair_improvement") != 0.05
        or design.get("seed") != 20261022
        or design.get("target_sample_ms") != 20
    ):
        raise PolicyRepairError("repair timing or success thresholds changed")
    direct_patch = design.get("direct_patch")
    if (
        not isinstance(direct_patch, dict)
        or direct_patch.get("candidate_limit") != 1
        or direct_patch.get("maximum_bespoke_predicates") != 4
        or direct_patch.get("case_identity_rules") is not False
        or direct_patch.get("test_outcome_access") is not False
    ):
        raise PolicyRepairError("direct patch scope changed")
    jev_reference = design.get("jev_reference")
    if (
        not isinstance(jev_reference, dict)
        or jev_reference.get("replicates") != replication.REPLICATES
        or jev_reference.get("max_attempts_per_slot") != 3
    ):
        raise PolicyRepairError("Jev reference replication contract changed")
    shift_path = _repo_path(design["shift_design"])
    if not shift_path.is_file() or _sha256(shift_path) != design["shift_design_sha256"]:
        raise PolicyRepairError("pinned workload case design changed")
    shift_design = shift._design(shift_path)
    if len(shift._case_rows(shift_design, "audit")) != 6:
        raise PolicyRepairError("historical audit partition must contain six cases")
    if len(shift._case_rows(shift_design, "adaptation")) != 6:
        raise PolicyRepairError("adaptation partition must contain six cases")
    if len(shift._case_rows(shift_design, "test")) != 12:
        raise PolicyRepairError("fresh test partition must contain twelve cases")
    return design


def _normalize_repo_paths(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(str(ROOT), "$REPO")
    if isinstance(value, list):
        return [_normalize_repo_paths(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_repo_paths(item) for key, item in value.items()}
    return value


def _public_bundle(design: dict[str, Any]) -> dict[str, Any]:
    reference = design["public_audit_bundle"]
    path = _repo_path(reference["path"])
    if not path.is_file() or _sha256(path) != reference["sha256"]:
        raise PolicyRepairError("pinned public audit bundle is missing or changed")
    bundle = _load(path)
    if not isinstance(bundle, dict) or not isinstance(bundle.get("files"), dict):
        raise PolicyRepairError("public audit bundle has an invalid schema")
    return bundle


def _bundle_evidence(
    bundle: dict[str, Any], relative_path: str, expected_hash: str, raw_root: Path
) -> tuple[Any, str]:
    entry = bundle["files"].get(relative_path)
    if (
        not isinstance(entry, dict)
        or entry.get("raw_sha256") != expected_hash
        or "evidence" not in entry
    ):
        raise PolicyRepairError(f"public audit bundle evidence differs for {relative_path}")
    evidence = entry["evidence"]
    raw_path = raw_root / relative_path
    if raw_path.is_file():
        if _sha256(raw_path) != expected_hash:
            raise PolicyRepairError(f"local historical raw evidence changed for {relative_path}")
        if _normalize_repo_paths(_load(raw_path)) != evidence:
            raise PolicyRepairError(f"local and public historical evidence differ for {relative_path}")
        return _load(raw_path), "local_raw_and_public_bundle"
    return evidence, "public_bundle_normalized_evidence"


def _bundle_relative_path(value: str, raw_root: Path) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyRepairError("historical artifact reference is missing")
    if value.startswith("$REPO/"):
        candidate = ROOT / value[len("$REPO/") :]
    else:
        path = Path(value)
        candidate = ROOT / path if not path.is_absolute() else path
    try:
        return candidate.resolve().relative_to(raw_root.resolve()).as_posix()
    except ValueError:
        raise PolicyRepairError("historical artifact reference is outside the pinned audit output") from None


def _pinned_inputs(design: dict[str, Any]) -> dict[str, Any]:
    shift_path = _repo_path(design["shift_design"])
    shift_design = shift._design(shift_path)
    toolchains = {"target": _target_toolchain(shift_design)}
    bundle = _public_bundle(design)
    raw_root = _repo_path(str(Path(design["audit_report"]).parent))
    audit, audit_source = _bundle_evidence(
        bundle, "audit-report.json", design["audit_report_sha256"], raw_root
    )
    prepared, prepared_source = _bundle_evidence(
        bundle, "prepared-audit.json", design["prepared_audit_sha256"], raw_root
    )
    if audit.get("audit_gate", {}).get("passed") is not False:
        raise PolicyRepairError("historical compiler-shift negative result is not the pinned result")
    if prepared.get("status") != "prepared" or prepared.get("native_measurements_started") is not False:
        raise PolicyRepairError("historical prepared audit checkpoint changed")
    if prepared.get("audit_case_ids") != [case["case_id"] for case in shift._case_rows(shift_design, "audit")]:
        raise PolicyRepairError("historical prepared audit case list changed")
    target_ref = prepared.get("policy_artifacts", {}).get("target", {})
    policy_path = _bundle_relative_path(target_ref.get("path", ""), raw_root)
    source_policy, policy_source = _bundle_evidence(
        bundle, policy_path, target_ref.get("sha256", ""), raw_root
    )
    if not isinstance(source_policy, dict) or not isinstance(source_policy.get("artifact"), dict):
        raise PolicyRepairError("public frozen target source-policy artifact is malformed")
    identity = toolchains["target"]["compiler_identity"]
    artifact = source_policy["artifact"]
    if artifact.get("compiler_identity") != identity:
        raise PolicyRepairError("historical source forest is not bound to the target compiler")
    return {
        "shift_path": shift_path,
        "shift_design": shift_design,
        "toolchains": toolchains,
        "target_identity": identity,
        "bundle": bundle,
        "raw_root": raw_root,
        "audit": audit,
        "audit_source": audit_source,
        "prepared": prepared,
        "prepared_source": prepared_source,
        "source_policy": artifact,
        "source_policy_source": policy_source,
    }


def _target_toolchain(shift_design: dict[str, Any]) -> dict[str, Any]:
    """Validate only the compiler used by this target-specific repair run."""
    compiler = shift._compiler_path(shift_design, "target")
    if not compiler.is_file() or shift._sha256(compiler) != shift_design["target_clang_sha256"]:
        raise PolicyRepairError("pinned target compiler is missing or changed")
    linker = Path(shift_design["linker"]).expanduser().resolve()
    if not linker.is_file() or shift._sha256(linker) != shift_design["linker_sha256"]:
        raise PolicyRepairError("pinned shared linker is missing or changed")
    sdk = Path(shift_design["sdk"]).expanduser().resolve()
    if not sdk.is_dir():
        raise PolicyRepairError("pinned macOS SDK is missing")
    xcrun = Path("/usr/bin/xcrun")
    if not xcrun.is_file():
        raise PolicyRepairError("xcrun is required to validate the pinned SDK")
    active_sdk = shift._run_identity_command([str(xcrun), "--sdk", "macosx", "--show-sdk-path"])["stdout"]
    if Path(active_sdk).resolve() != sdk:
        raise PolicyRepairError("active macOS SDK differs from the pinned target SDK")
    sdk_version = shift._run_identity_command([str(xcrun), "--sdk", "macosx", "--show-sdk-version"])["stdout"]
    compiler_hash = shift._sha256(compiler)
    return {
        "path": str(compiler),
        "sha256": compiler_hash,
        "version": shift._run_identity_command([str(compiler), "--version"]),
        "default_target": shift._run_identity_command([str(compiler), "-dumpmachine"]),
        "compiler_identity": {"sha256": compiler_hash, "target": shift_design["target"]},
        "sdk": {"path": str(sdk), "version": sdk_version},
        "linker": {"path": str(linker), "sha256": shift_design["linker_sha256"]},
        "target": shift_design["target"],
        "target_cpu": shift_design["target_cpu"],
    }


def _case_rows(shift_design: dict[str, Any], partition: str) -> list[dict[str, Any]]:
    return shift._case_rows(shift_design, partition)


def _provenance(
    design_path: Path, design: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    modules = (
        feedback,
        feedback.benchmark,
        feedback.codex_advisor,
        feedback.contraction_build,
        feedback.experiment,
        feedback.feedback_policy,
        feedback.jev,
        feedback.loop_experiment,
        feedback.quality_analysis,
        contraction,
        shift.contraction_corpus,
        replication,
        replication.loop_experiment,
        harness_policy,
        shift,
        shift.benchmark,
        shift.compiler_evidence,
        shift.experiment,
        shift.loop_build,
        shift.quality_experiment,
        shift.validate,
    )
    return {
        "design_sha256": _sha256(design_path),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "implementation_sha256": {
            str(Path(module.__file__).resolve().relative_to(ROOT)): _sha256(Path(module.__file__).resolve())
            for module in modules
        },
        "shift_design_sha256": design["shift_design_sha256"],
        "audit_report_sha256": design["audit_report_sha256"],
        "prepared_audit_sha256": design["prepared_audit_sha256"],
        "public_audit_bundle_sha256": design["public_audit_bundle"]["sha256"],
        "historical_evidence_source": {
            "audit_report": inputs["audit_source"],
            "prepared_audit": inputs["prepared_source"],
            "source_policy": inputs["source_policy_source"],
        },
        "target_compiler_identity": inputs["target_identity"],
    }


def _build_reference(
    output: Path, case_id: str, identity: dict[str, str]
) -> dict[str, Any]:
    path = output / "cases" / case_id / "compilers" / "target" / "build-report.json"
    if not path.is_file():
        raise PolicyRepairError(f"target build report is missing for {case_id}")
    report = _load(path)
    if (
        report.get("status") != "passed"
        or report.get("compiler_arm") != "target"
        or report.get("toolchain", {}).get("compiler_identity") != identity
    ):
        raise PolicyRepairError(f"target build did not pass for {case_id}")
    shift._validate_artifacts(report)
    return {
        "path": str(path.resolve()),
        "sha256": _sha256(path),
        "case_fingerprint": report.get("case_fingerprint"),
    }


def _load_local_build(
    reference: dict[str, Any], case: dict[str, Any], identity: dict[str, str]
) -> dict[str, Any]:
    path = Path(reference.get("path", ""))
    if not path.is_file() or _sha256(path) != reference.get("sha256"):
        raise PolicyRepairError(f"build report changed for {case['case_id']}")
    report = _load(path)
    if (
        report.get("status") != "passed"
        or report.get("case_id") != case["case_id"]
        or report.get("group") != case["group"]
        or report.get("spec") != case["spec"]
        or report.get("compiler_arm") != "target"
        or report.get("toolchain", {}).get("compiler_identity") != identity
        or report.get("action_order") != list(shift.ACTION_NAMES)
        or report.get("case_fingerprint") != reference.get("case_fingerprint")
    ):
        raise PolicyRepairError(f"prepared build identity changed for {case['case_id']}")
    shift._validate_artifacts(report)
    return report


def _adapted_measurement_case(case: dict[str, Any], build: dict[str, Any]) -> dict[str, Any]:
    adapted = shift._measurement_case(case, build)
    adapted["partition"] = "discovery"
    return adapted


def _restore_measured_metadata(
    measured: list[dict[str, Any]], frozen_cases: list[dict[str, Any]], partition: str
) -> list[dict[str, Any]]:
    original_by_id = {case["case_id"]: case for case in frozen_cases}
    measured_ids = [case.get("case_id") for case in measured]
    if len(measured_ids) != len(set(measured_ids)) or set(measured_ids) != set(original_by_id):
        raise PolicyRepairError(f"{partition} measurement case set changed")
    return [
        {
            **case,
            "partition": partition,
            "family": original_by_id[case["case_id"]]["family"],
            "group": original_by_id[case["case_id"]]["group"],
            "spec": original_by_id[case["case_id"]]["spec"],
        }
        for case in measured
    ]


def prepare_adaptation(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    design = _design(design_path)
    inputs = _pinned_inputs(design)
    provenance = _provenance(design_path, design, inputs)
    cases = _case_rows(inputs["shift_design"], "adaptation")
    builds: dict[str, Any] = {}
    policy_path = output / "policies" / "unchanged-source-forest.json"
    policy_value = {"schema_version": SCHEMA_VERSION, "artifact": inputs["source_policy"]}
    _save_frozen(policy_path, policy_value, "unchanged source forest")
    for case in cases:
        build = shift.build_case(
            case,
            "target",
            output / "cases" / case["case_id"],
            inputs["shift_design"],
            inputs["toolchains"]["target"],
            inputs["shift_path"],
        )
        prediction = shift._prediction(case, build, inputs["source_policy"], inputs["target_identity"])
        builds[case["case_id"]] = {
            **_build_reference(output, case["case_id"], inputs["target_identity"]),
            "unchanged_source_forest_choice": prediction["action"],
            "stride_selector_choice": contraction.stride_choice(build["source_features"]),
            "packet_sha256": prediction["packet_sha256"],
        }
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared_adaptation",
        "provenance": provenance,
        "output": str(output),
        "partition": "adaptation",
        "case_ids": [case["case_id"] for case in cases],
        "source_policy": {"path": str(policy_path), "sha256": _sha256(policy_path)},
        "builds_by_case": builds,
        "native_measurements_started": False,
    }
    _save_frozen(output / "prepared-adaptation.json", report, "adaptation manifest")
    return report


def _validate_prepared_adaptation(
    design_path: Path, output: Path, design: dict[str, Any], inputs: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    prepared_path = output / "prepared-adaptation.json"
    if not prepared_path.is_file():
        raise PolicyRepairError("measure-adaptation requires prepare-adaptation")
    prepared = _load(prepared_path)
    provenance = _provenance(design_path, design, inputs)
    cases = _case_rows(inputs["shift_design"], "adaptation")
    if (
        prepared.get("status") != "prepared_adaptation"
        or prepared.get("provenance") != provenance
        or prepared.get("case_ids") != [case["case_id"] for case in cases]
        or prepared.get("native_measurements_started") is not False
    ):
        raise PolicyRepairError("prepared adaptation provenance or case set changed")
    policy_ref = prepared.get("source_policy", {})
    policy_path = Path(policy_ref.get("path", ""))
    if not policy_path.is_file() or _sha256(policy_path) != policy_ref.get("sha256"):
        raise PolicyRepairError("frozen source policy copy changed")
    if _load(policy_path) != {"schema_version": SCHEMA_VERSION, "artifact": inputs["source_policy"]}:
        raise PolicyRepairError("frozen source policy copy content changed")
    builds = {}
    for case in cases:
        ref = prepared.get("builds_by_case", {}).get(case["case_id"])
        if not isinstance(ref, dict):
            raise PolicyRepairError(f"prepared adaptation build missing for {case['case_id']}")
        builds[case["case_id"]] = _load_local_build(ref, case, inputs["target_identity"])
        prediction = shift._prediction(case, builds[case["case_id"]], inputs["source_policy"], inputs["target_identity"])
        if (
            prepared["builds_by_case"][case["case_id"]].get("unchanged_source_forest_choice") != prediction["action"]
            or prepared["builds_by_case"][case["case_id"]].get("packet_sha256") != prediction["packet_sha256"]
        ):
            raise PolicyRepairError(f"frozen source policy decision changed for {case['case_id']}")
    return prepared, cases, builds


def measure_adaptation(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    design = _design(design_path)
    inputs = _pinned_inputs(design)
    provenance = _provenance(design_path, design, inputs)
    _prepared, cases, builds = _validate_prepared_adaptation(design_path, output, design, inputs)
    adapted = [_adapted_measurement_case(case, builds[case["case_id"]]) for case in cases]
    measured = contraction._measure_cases(
        adapted,
        {
            "benchmark_rounds": design["benchmark_rounds"],
            "seed": design["seed"],
            "target_sample_ms": design["target_sample_ms"],
        },
        output / "timing" / "adaptation",
        blocks=(1, 2),
    )
    measured = _restore_measured_metadata(measured, cases, "adaptation")
    result_cases = []
    for case in measured:
        by_block = {str(block): [] for block in (1, 2)}
        for row in case["benchmark"]["round_records"]:
            by_block[str(row["block"])].append(row)
        if any(len(rows) != design["benchmark_rounds"] for rows in by_block.values()):
            raise PolicyRepairError(f"adaptation rounds incomplete for {case['case_id']}")
        caches = case.get("timing_caches", [])
        if len(caches) != 2:
            raise PolicyRepairError(f"adaptation timing cache count changed for {case['case_id']}")
        cache_checksums = []
        for reference in caches:
            cache_path = Path(reference["path"])
            if not cache_path.is_file() or _sha256(cache_path) != reference.get("sha256"):
                raise PolicyRepairError(f"adaptation timing cache changed for {case['case_id']}")
            raw = _load(cache_path).get("benchmark", {})
            if raw.get("status") != "measured":
                raise PolicyRepairError(f"adaptation raw timing evidence invalid for {case['case_id']}")
            cache_checksums.append(raw.get("stock_checksum"))
        if len(set(cache_checksums)) != 1:
            raise PolicyRepairError(f"adaptation correctness checksums differ by block for {case['case_id']}")
        result_cases.append(case)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "measured_adaptation",
        "provenance": provenance,
        "prepared_adaptation_sha256": _sha256(output / "prepared-adaptation.json"),
        "case_ids": [case["case_id"] for case in cases],
        "cases": result_cases,
        "label_block": 1,
        "fitting_uses_block_two": False,
    }
    _save_frozen(output / "adaptation-report.json", report, "adaptation timing report")
    return report


def _expand_action_measurements(
    measurements: dict[str, Any], aliases: dict[str, str], case_id: str
) -> dict[str, Any]:
    if (
        not isinstance(measurements, dict)
        or not isinstance(aliases, dict)
        or set(aliases) != set(ACTION_NAMES)
        or any(alias not in ACTION_NAMES for alias in aliases.values())
    ):
        raise PolicyRepairError(f"historical action aliases are invalid for {case_id}")
    expanded = {}
    for action in ACTION_NAMES:
        sample = measurements.get(aliases[action])
        elapsed = sample.get("elapsed_ns") if isinstance(sample, dict) else None
        if type(elapsed) is not int or elapsed <= 0:
            raise PolicyRepairError(f"historical timing sample is missing for {case_id}/{action}")
        expanded[action] = sample
    return expanded


def _historical_training_cases(
    design: dict[str, Any], inputs: dict[str, Any]
) -> list[dict[str, Any]]:
    expected_cases = _case_rows(inputs["shift_design"], "audit")
    actual = {case.get("case_id"): case for case in inputs["audit"].get("cases", [])}
    if set(actual) != {case["case_id"] for case in expected_cases}:
        raise PolicyRepairError("historical audit case IDs differ from the frozen design")
    prepared_builds = inputs["prepared"]["builds_by_compiler"]["target"]
    cases = []
    for case in expected_cases:
        audit_case = actual[case["case_id"]]
        if audit_case.get("group") != case["group"] or audit_case.get("spec") != case["spec"]:
            raise PolicyRepairError(f"historical audit case identity changed for {case['case_id']}")
        build_ref = prepared_builds.get(case["case_id"])
        if not isinstance(build_ref, dict) or build_ref.get("case_fingerprint") is None:
            raise PolicyRepairError(f"historical target build reference missing for {case['case_id']}")
        relative = _bundle_relative_path(build_ref.get("build_report_path", ""), inputs["raw_root"])
        build, _source = _bundle_evidence(
            inputs["bundle"], relative, build_ref.get("build_report_sha256", ""), inputs["raw_root"]
        )
        if (
            build.get("status") != "passed"
            or build.get("case_id") != case["case_id"]
            or build.get("compiler_arm") != "target"
            or build.get("case_fingerprint") != build_ref["case_fingerprint"]
            or build.get("toolchain", {}).get("compiler_identity") != inputs["target_identity"]
        ):
            raise PolicyRepairError(f"historical target build identity changed for {case['case_id']}")
        aliases = audit_case.get("measurements", {}).get("target", {}).get("code_aliases")
        actions = build.get("actions", {})
        if (
            not isinstance(aliases, dict)
            or set(aliases) != set(ACTION_NAMES)
            or any(alias not in ACTION_NAMES for alias in aliases.values())
            or set(actions) != set(ACTION_NAMES)
            or any(not isinstance(actions[action].get("code_identity"), str) for action in ACTION_NAMES)
            or any(
                actions.get(action, {}).get("code_identity")
                != actions.get(aliases[action], {}).get("code_identity")
                for action in ACTION_NAMES
            )
        ):
            raise PolicyRepairError(f"historical action aliases are invalid for {case['case_id']}")
        block = audit_case.get("measurements", {}).get("target", {}).get("blocks", {}).get("1", {})
        benchmark = block.get("benchmark", {})
        rows = benchmark.get("round_records")
        if (
            benchmark.get("status") != "measured"
            or not isinstance(rows, list)
            or len(rows) != design["benchmark_rounds"]
            or [row.get("round") for row in rows]
            != list(range(1, design["benchmark_rounds"] + 1))
        ):
            raise PolicyRepairError(f"historical target block-one labels missing for {case['case_id']}")
        cases.append(
            {
                **case,
                "source_features": build["source_features"],
                "build": build,
                "benchmark": {
                    "round_records": [
                        {
                            "block": 1,
                            "round": row["round"],
                            "measurements": _expand_action_measurements(
                                row.get("measurements"), aliases, case["case_id"]
                            ),
                        }
                        for row in rows
                    ]
                },
            }
        )
    return cases


def _verify_adaptation_timing_case(case: dict[str, Any], output: Path, rounds: int) -> None:
    references = case.get("timing_caches")
    aliases = case.get("code_aliases")
    if not isinstance(references, list) or len(references) != 2:
        raise PolicyRepairError(f"adaptation timing cache index is incomplete for {case['case_id']}")
    if not isinstance(aliases, dict) or set(aliases) != set(ACTION_NAMES):
        raise PolicyRepairError(f"adaptation code aliases are malformed for {case['case_id']}")
    records = case.get("benchmark", {}).get("round_records", [])
    if len(records) != 2 * rounds:
        raise PolicyRepairError(f"adaptation timing rounds are incomplete for {case['case_id']}")
    for block in (1, 2):
        expected_path = (
            output / "timing" / "adaptation" / "cases" / "discovery" / case["case_id"]
            / "timings" / f"block-{block}.json"
        ).resolve()
        reference = next(
            (item for item in references if item.get("path") == str(expected_path)), None
        )
        if reference is None or not expected_path.is_file() or _sha256(expected_path) != reference.get("sha256"):
            raise PolicyRepairError(f"adaptation timing cache changed for {case['case_id']} block {block}")
        cached = _load(expected_path)
        identity = cached.get("identity", {})
        benchmark = cached.get("benchmark", {})
        raw_records = benchmark.get("round_records")
        if (
            cached.get("schema_version") != 1
            or cached.get("identity_sha256") != _fingerprint(identity)
            or reference.get("identity_sha256") != cached.get("identity_sha256")
            or identity.get("case_id") != case["case_id"]
            or identity.get("partition") != "discovery"
            or identity.get("block") != block
            or identity.get("rounds") != rounds
            or identity.get("action_aliases") != aliases
            or benchmark.get("status") != "measured"
            or benchmark.get("rounds") != rounds
            or not isinstance(raw_records, list)
            or len(raw_records) != rounds
            or [row.get("round") for row in raw_records] != list(range(1, rounds + 1))
        ):
            raise PolicyRepairError(f"adaptation timing cache identity changed for {case['case_id']} block {block}")
        expected_records = [
            {
                "round": (block - 1) * rounds + row["round"],
                "block": block,
                "block_round": row["round"],
                "canonical_order": row["order"],
                "measurements": _expand_action_measurements(
                    row.get("measurements"), aliases, case["case_id"]
                ),
            }
            for row in raw_records
        ]
        measured_records = [row for row in records if row.get("block") == block]
        if measured_records != expected_records:
            raise PolicyRepairError(f"adaptation timing rows differ from block cache for {case['case_id']}")


def _adaptation_training_cases(
    design_path: Path,
    design: dict[str, Any],
    inputs: dict[str, Any],
    output: Path,
    prepared: dict[str, Any],
) -> list[dict[str, Any]]:
    report_path = output / "adaptation-report.json"
    if not report_path.is_file():
        raise PolicyRepairError("fit requires the completed adaptation measurement report")
    report = _load(report_path)
    if (
        report.get("status") != "measured_adaptation"
        or report.get("label_block") != 1
        or report.get("fitting_uses_block_two") is not False
        or report.get("prepared_adaptation_sha256") != _sha256(output / "prepared-adaptation.json")
        or report.get("provenance") != _provenance(design_path, design, inputs)
    ):
        raise PolicyRepairError("adaptation training labels are not frozen block-one results")
    expected_ids = prepared["case_ids"]
    measured = report.get("cases", [])
    if [case.get("case_id") for case in measured] != expected_ids:
        raise PolicyRepairError("adaptation training case order differs from the frozen manifest")
    shift_design = shift._design(_repo_path(design["shift_design"]))
    case_specs = {case["case_id"]: case for case in _case_rows(shift_design, "adaptation")}
    builds = prepared["builds_by_case"]
    cases = []
    for measured_case in measured:
        case_id = measured_case["case_id"]
        case = case_specs[case_id]
        _verify_adaptation_timing_case(measured_case, output, design["benchmark_rounds"])
        if measured_case.get("group") != case["group"] or measured_case.get("spec") != case["spec"]:
            raise PolicyRepairError(f"adaptation case metadata changed for {case_id}")
        build = _load_local_build(builds[case_id], case, inputs["target_identity"])
        if measured_case.get("source_features") != build.get("source_features"):
            raise PolicyRepairError(f"adaptation source features changed for {case_id}")
        cases.append({**measured_case, "build": build, "partition": "adaptation"})
    return cases


def fit_repair(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    design = _design(design_path)
    inputs = _pinned_inputs(design)
    provenance = _provenance(design_path, design, inputs)
    prepared_path = output / "prepared-adaptation.json"
    if not prepared_path.is_file():
        raise PolicyRepairError("fit requires prepare-adaptation")
    prepared = _load(prepared_path)
    if prepared.get("provenance") != provenance:
        raise PolicyRepairError("adaptation preparation provenance changed before fit")
    historical = _historical_training_cases(design, inputs)
    adaptation = _adaptation_training_cases(design_path, design, inputs, output, prepared)
    training_cases = [*historical, *adaptation]
    if len(training_cases) != 12 or len({case["group"] for case in training_cases}) != 6:
        raise PolicyRepairError("target-only training requires twelve cases from six recipes")
    if any(sum(case["group"] == group for case in training_cases) != 2 for group in {case["group"] for case in training_cases}):
        raise PolicyRepairError("each training recipe must contribute two cases")
    rows = feedback._training_rows(training_cases, block=1, informed=True)
    if len(rows) != 72:
        raise PolicyRepairError("target-only forest requires exactly 72 action labels")
    model_provenance = {
        "design_sha256": _sha256(design_path),
        "historical_audit_sha256": design["audit_report_sha256"],
        "public_audit_bundle_sha256": design["public_audit_bundle"]["sha256"],
        "adaptation_report_sha256": _sha256(output / "adaptation-report.json"),
        "training_case_ids": [case["case_id"] for case in training_cases],
        "training_block": 1,
        "compiler_identity": inputs["target_identity"],
    }
    fit_path = output / "fit-report.json"
    if fit_path.is_file():
        existing, artifact = _load_frozen_fit(design_path, output, design, inputs)
        if (
            existing.get("training_provenance") != model_provenance
            or existing.get("training_case_ids") != model_provenance["training_case_ids"]
            or existing.get("training_groups") != sorted({case["group"] for case in training_cases})
            or existing.get("training_rows") != rows
            or existing.get("training_rows_sha256") != _fingerprint(rows)
            or existing.get("feature_names") != artifact.get("feature_schema")
            or existing.get("parameters") != design["forest"]
        ):
            raise PolicyRepairError("frozen fit checkpoint differs from the validated training data")
        return existing
    started = time.perf_counter()
    _model, exported = replication._fit_forest(
        rows,
        {"forest": design["forest"], "sklearn_version": design["sklearn_version"]},
    )
    elapsed_seconds = time.perf_counter() - started
    artifact = harness_policy.make_artifact(exported, inputs["target_identity"], model_provenance)
    artifact_path = output / "policies" / "target-refit.json"
    _save_frozen(artifact_path, {"schema_version": SCHEMA_VERSION, "artifact": artifact}, "target refit policy")
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "fit_frozen",
        "provenance": provenance,
        "training_provenance": model_provenance,
        "training_case_ids": [case["case_id"] for case in training_cases],
        "training_groups": sorted({case["group"] for case in training_cases}),
        "training_rows": rows,
        "training_rows_sha256": _fingerprint(rows),
        "feature_names": exported["features"],
        "target_model_sha256": _fingerprint(exported),
        "target_policy": {"path": str(artifact_path), "sha256": _sha256(artifact_path)},
        "fit_elapsed_seconds": elapsed_seconds,
        "parameters": exported["parameters"],
        "training_block": 1,
        "test_outcomes_used": False,
    }
    _save_frozen(fit_path, report, "target refit checkpoint")
    return report


def _load_frozen_fit(
    design_path: Path, output: Path, design: dict[str, Any], inputs: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    adaptation_path = output / "adaptation-report.json"
    prepared_adaptation_path = output / "prepared-adaptation.json"
    report_path = output / "fit-report.json"
    if not report_path.is_file():
        raise PolicyRepairError("prepare-test requires the frozen target refit")
    if not adaptation_path.is_file() or not prepared_adaptation_path.is_file():
        raise PolicyRepairError("frozen fit is missing its adaptation evidence")
    adaptation = _load(adaptation_path)
    shift_design = inputs["shift_design"]
    training_case_ids = [
        case["case_id"]
        for partition in ("audit", "adaptation")
        for case in _case_rows(shift_design, partition)
    ]
    training_groups = sorted(
        {case["group"] for partition in ("audit", "adaptation") for case in _case_rows(shift_design, partition)}
    )
    expected_training_provenance = {
        "design_sha256": _sha256(design_path),
        "historical_audit_sha256": design["audit_report_sha256"],
        "public_audit_bundle_sha256": design["public_audit_bundle"]["sha256"],
        "adaptation_report_sha256": _sha256(adaptation_path),
        "training_case_ids": training_case_ids,
        "training_block": 1,
        "compiler_identity": inputs["target_identity"],
    }
    report = _load(report_path)
    if (
        report.get("status") != "fit_frozen"
        or report.get("training_block") != 1
        or report.get("test_outcomes_used") is not False
        or report.get("training_provenance") != expected_training_provenance
        or report.get("training_case_ids") != training_case_ids
        or report.get("training_groups") != training_groups
        or len(report.get("training_rows", [])) != design["training"]["action_rows"]
        or report.get("training_rows_sha256") != _fingerprint(report.get("training_rows"))
        or report.get("parameters") != design["forest"]
        or adaptation.get("status") != "measured_adaptation"
        or adaptation.get("label_block") != 1
        or adaptation.get("fitting_uses_block_two") is not False
        or adaptation.get("prepared_adaptation_sha256") != _sha256(prepared_adaptation_path)
        or adaptation.get("provenance") != _provenance(design_path, design, inputs)
        or report.get("provenance")
        != _provenance(design_path, design, inputs)
    ):
        raise PolicyRepairError("target refit checkpoint provenance changed")
    policy_ref = report.get("target_policy", {})
    if not isinstance(policy_ref, dict):
        raise PolicyRepairError("frozen target policy reference is malformed")
    policy_path = Path(policy_ref.get("path", ""))
    if not policy_path.is_file() or _sha256(policy_path) != policy_ref.get("sha256"):
        raise PolicyRepairError("frozen target refit policy changed")
    policy_wrapper = _load(policy_path)
    artifact = policy_wrapper.get("artifact") if isinstance(policy_wrapper, dict) else None
    if (
        not isinstance(policy_wrapper, dict)
        or policy_wrapper.get("schema_version") != SCHEMA_VERSION
        or not isinstance(artifact, dict)
        or artifact.get("compiler_identity") != inputs["target_identity"]
        or _fingerprint(artifact.get("model")) != report.get("target_model_sha256")
        or artifact.get("training_provenance") != report.get("training_provenance")
    ):
        raise PolicyRepairError("target refit artifact differs from its fit checkpoint")
    return report, artifact


def _validated_fit_for_test(
    design_path: Path, output: Path, design: dict[str, Any], inputs: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not (output / "fit-report.json").is_file():
        raise PolicyRepairError("test preparation requires an existing frozen fit checkpoint")
    fit_repair(design_path, output)
    return _load_frozen_fit(design_path, output, design, inputs)


def _workload_heuristic(design: dict[str, Any]):
    reference = design.get("direct_patch")
    if not isinstance(reference, dict) or not isinstance(reference.get("module"), str):
        raise PolicyRepairError("direct workload patch is not pinned")
    module_path = _repo_path(reference["module"])
    if not module_path.is_file():
        raise PolicyRepairError("direct workload patch module is missing")
    spec = importlib.util.spec_from_file_location("p4ai_workload_heuristic", module_path)
    if spec is None or spec.loader is None:
        raise PolicyRepairError("cannot load the direct workload patch")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    chooser = getattr(module, "choose", None) or getattr(module, "choice", None)
    if not callable(chooser):
        raise PolicyRepairError("direct workload patch must expose choose(packet)")
    return module_path, module, chooser


def _patch_action(chooser: Any, packet: dict[str, Any], case_id: str) -> str:
    try:
        value = chooser(packet)
    except Exception as error:  # Surface a safe failure without hiding the candidate.
        raise PolicyRepairError(f"direct workload patch failed for {case_id}: {type(error).__name__}") from error
    action = value.get("action") if isinstance(value, dict) else value
    if not isinstance(action, str) or action not in ORDERS:
        raise PolicyRepairError(f"direct workload patch returned an invalid action for {case_id}")
    return action


def _partial_provider_slots(
    case: dict[str, Any], packet: dict[str, Any], output: Path, count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    successes = []
    incomplete = []
    for replicate in range(1, count + 1):
        cache_path = output / "provider-cache" / case["case_id"] / f"replicate-{replicate}.json"
        if not cache_path.is_file():
            incomplete.append({"replicate": replicate, "status": "not_attempted", "attempt_count": 0})
            continue
        entry, attempts = replication._cached_provider_entry(
            packet, cache_path, case["case_id"], replicate
        )
        if entry is None:
            cache = feedback._load(cache_path)
            packet_key = replication.loop_experiment._packet_fingerprint(packet)
            cached = cache["packets"][packet_key]
            incomplete.append(
                {
                    "replicate": replicate,
                    "status": "unavailable_or_rejected",
                    "attempt_count": attempts,
                    "attempt_statuses": [item["status"] for item in cached["attempts"]],
                    "cache_path": str(cache_path.resolve()),
                    "cache_sha256": _sha256(cache_path),
                }
            )
        else:
            successes.append(replication._slot_record(
                case["case_id"], packet, cache_path, replicate, entry
            ))
    return successes, incomplete


def _verify_provider_slot(
    case: dict[str, Any], packet: dict[str, Any], output: Path, slot: dict[str, Any]
) -> None:
    replicate = slot.get("replicate")
    if type(replicate) is not int or not 1 <= replicate <= replication.REPLICATES:
        raise PolicyRepairError(f"invalid Jev replicate slot for {case['case_id']}")
    cache_path = output / "provider-cache" / case["case_id"] / f"replicate-{replicate}.json"
    if (
        slot.get("cache_path") != str(cache_path.resolve())
        or not cache_path.is_file()
        or slot.get("cache_sha256") != _sha256(cache_path)
    ):
        raise PolicyRepairError(f"frozen Jev provider slot changed for {case['case_id']}")
    entry, _attempts = replication._cached_provider_entry(
        packet, cache_path, case["case_id"], replicate
    )
    if entry is None or replication._slot_record(
        case["case_id"], packet, cache_path, replicate, entry
    ) != slot:
        raise PolicyRepairError(f"frozen Jev provider response changed for {case['case_id']}")


def _verify_incomplete_slot(
    case: dict[str, Any],
    packet: dict[str, Any],
    output: Path,
    slot: dict[str, Any],
    max_attempts: int,
) -> None:
    replicate = slot.get("replicate")
    if type(replicate) is not int or not 1 <= replicate <= replication.REPLICATES:
        raise PolicyRepairError(f"invalid incomplete Jev slot for {case['case_id']}")
    cache_path = output / "provider-cache" / case["case_id"] / f"replicate-{replicate}.json"
    if slot.get("status") == "not_attempted":
        if slot.get("attempt_count") != 0 or cache_path.exists() or "cache_path" in slot:
            raise PolicyRepairError(f"unattempted Jev slot unexpectedly has cache data for {case['case_id']}")
        return
    if (
        slot.get("status") != "unavailable_or_rejected"
        or slot.get("cache_path") != str(cache_path.resolve())
        or not cache_path.is_file()
        or slot.get("cache_sha256") != _sha256(cache_path)
    ):
        raise PolicyRepairError(f"failed Jev slot evidence changed for {case['case_id']}")
    entry, attempts = replication._cached_provider_entry(
        packet, cache_path, case["case_id"], replicate
    )
    cache = feedback._load(cache_path)
    packet_key = replication.loop_experiment._packet_fingerprint(packet)
    cached = cache["packets"].get(packet_key, {})
    statuses = [attempt.get("status") for attempt in cached.get("attempts", [])]
    if (
        entry is not None
        or attempts != slot.get("attempt_count")
        or attempts != max_attempts
        or statuses != slot.get("attempt_statuses")
    ):
        raise PolicyRepairError(f"failed Jev attempt history changed for {case['case_id']}")


def _jev_summary(provider_by_case: dict[str, dict[str, Any]]) -> dict[str, Any]:
    slots = [
        slot
        for case in provider_by_case.values()
        for slot in case.get("slots", [])
    ]
    responses = [slot["response"] for slot in slots]
    latency_responses = [response for response in responses if type(response.get("latency_ms")) in (int, float)]
    def token_count(response: dict[str, Any], field: str) -> int | None:
        usage = response.get("usage")
        count = usage.get(field) if isinstance(usage, dict) else None
        return count if type(count) is int else None

    attempts = sum(
        len(slot.get("attempts", [])) for slot in slots
    ) + sum(
        slot.get("attempt_count", 0)
        for case in provider_by_case.values()
        for slot in case.get("incomplete_slots", [])
    )
    return {
        "response_count": len(responses),
        "input_tokens_observed": sum(
            count for response in responses
            if (count := token_count(response, "input_tokens")) is not None
        ),
        "output_tokens_observed": sum(
            count for response in responses
            if (count := token_count(response, "output_tokens")) is not None
        ),
        "responses_with_input_token_count": sum(
            token_count(response, "input_tokens") is not None for response in responses
        ),
        "responses_with_output_token_count": sum(
            token_count(response, "output_tokens") is not None for response in responses
        ),
        "responses_with_latency": len(latency_responses),
        "observed_response_latency_ms": sum(response["latency_ms"] for response in latency_responses),
        "recorded_attempts": attempts,
        "failed_or_unattempted_slots": sum(
            len(case.get("incomplete_slots", [])) for case in provider_by_case.values()
        ) + replication.REPLICATES * sum(
            case.get("status") == "not_attempted_prior_failure"
            for case in provider_by_case.values()
        ),
        "usage_scope": "successful response usage only; failed call usage may be unavailable",
        "model_ids": sorted({response.get("model") for response in responses if response.get("model")}),
    }


def prepare_test(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    design = _design(design_path)
    inputs = _pinned_inputs(design)
    provenance = _provenance(design_path, design, inputs)
    fit_report, target_artifact = _validated_fit_for_test(
        design_path, output, design, inputs
    )
    patch_path, _patch_module, chooser = _workload_heuristic(design)
    patch_hash = _sha256(patch_path)
    cases = _case_rows(inputs["shift_design"], "test")
    builds: dict[str, Any] = {}
    packets: dict[str, Any] = {}
    choices: dict[str, Any] = {}
    for case in cases:
        build = shift.build_case(
            case,
            "target",
            output / "cases" / case["case_id"],
            inputs["shift_design"],
            inputs["toolchains"]["target"],
            inputs["shift_path"],
        )
        compiled_case = {**case, "source_features": build["source_features"], "build": build}
        packet = shift.policy_packet(compiled_case)
        source_choice = harness_policy.choose(
            inputs["source_policy"], packet, inputs["target_identity"]
        )["action"]
        refit_choice = harness_policy.choose(
            target_artifact, packet, inputs["target_identity"]
        )["action"]
        packets[case["case_id"]] = packet
        choices[case["case_id"]] = {
            "unchanged_source_forest": source_choice,
            "stride_selector": contraction.stride_choice(build["source_features"]),
            "target_refit": refit_choice,
            "direct_patch": _patch_action(chooser, packet, case["case_id"]),
        }
        builds[case["case_id"]] = _build_reference(
            output, case["case_id"], inputs["target_identity"]
        )

    provider_output: dict[str, Any] = {}
    provider_status = "available"
    failure: str | None = None
    provider_config = design.get("jev_reference", {})
    count = provider_config.get("replicates")
    max_attempts = provider_config.get("max_attempts_per_slot")
    if count != replication.REPLICATES or max_attempts not in (1, 3):
        raise PolicyRepairError("Jev reference replicate contract differs from the frozen helper")
    for case in cases:
        if provider_status == "unavailable":
            provider_output[case["case_id"]] = {"status": "not_attempted_prior_failure", "slots": []}
            continue
        try:
            slots = replication._query_replicates(
                case,
                packets[case["case_id"]],
                output,
                count=count,
                max_attempts=max_attempts,
            )
        except feedback.CompilerFeedbackError as error:
            if str(error) not in {
                "jev returned no valid decision; failed attempt is cached",
                "jev returned no valid decision; cached provider attempts are exhausted",
            }:
                raise
            provider_status = "unavailable"
            failure = str(error)
            partial, incomplete = _partial_provider_slots(
                case, packets[case["case_id"]], output, count
            )
            provider_output[case["case_id"]] = {
                "status": "unavailable",
                "reason": failure,
                "slots": partial,
                "incomplete_slots": incomplete,
            }
        else:
            provider_output[case["case_id"]] = {"status": "available", "slots": slots}
    provider_files = []
    cache_root = output / "provider-cache"
    if cache_root.is_dir():
        for cache_path in sorted(cache_root.rglob("*.json")):
            provider_files.append({"path": str(cache_path.resolve()), "sha256": _sha256(cache_path)})
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared_test",
        "provenance": provenance,
        "fit_report_sha256": _sha256(output / "fit-report.json"),
        "target_policy_sha256": fit_report["target_policy"]["sha256"],
        "direct_patch_sha256": patch_hash,
        "case_ids": [case["case_id"] for case in cases],
        "builds_by_case": builds,
        "packets_by_case": packets,
        "choices_by_case": choices,
        "jev_reference": {
            "status": provider_status,
            "reason": failure,
            "replicates": count,
            "max_attempts_per_slot": max_attempts,
            "responses_by_case": provider_output,
            "usage_and_latency": _jev_summary(provider_output),
            "cache_files": provider_files,
        },
        "test_outcomes_used": False,
        "native_measurements_started": False,
    }
    _save_frozen(output / "prepared-test.json", report, "test preparation")
    return report


def _validate_prepared_test(
    design_path: Path, output: Path, design: dict[str, Any], inputs: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    prepared_path = output / "prepared-test.json"
    if not prepared_path.is_file():
        raise PolicyRepairError("measure-test requires prepare-test")
    prepared = _load(prepared_path)
    provenance = _provenance(design_path, design, inputs)
    fit_report, target_artifact = _validated_fit_for_test(
        design_path, output, design, inputs
    )
    patch_path, _module, chooser = _workload_heuristic(design)
    cases = _case_rows(inputs["shift_design"], "test")
    case_ids = [case["case_id"] for case in cases]
    if (
        prepared.get("status") != "prepared_test"
        or prepared.get("provenance") != provenance
        or prepared.get("fit_report_sha256") != _sha256(output / "fit-report.json")
        or prepared.get("target_policy_sha256") != fit_report["target_policy"]["sha256"]
        or prepared.get("direct_patch_sha256") != _sha256(patch_path)
        or prepared.get("case_ids") != case_ids
        or prepared.get("test_outcomes_used") is not False
        or prepared.get("native_measurements_started") is not False
    ):
        raise PolicyRepairError("prepared test checkpoint provenance changed")
    provider = prepared.get("jev_reference", {})
    if provider.get("status") not in {"available", "unavailable"}:
        raise PolicyRepairError("Jev reference status is malformed")
    if (
        provider.get("replicates") != replication.REPLICATES
        or provider.get("max_attempts_per_slot") != design["jev_reference"]["max_attempts_per_slot"]
    ):
        raise PolicyRepairError("frozen Jev retry contract changed")
    builds: dict[str, dict[str, Any]] = {}
    expected_packets: dict[str, Any] = {}
    expected_choices: dict[str, Any] = {}
    for case in cases:
        case_id = case["case_id"]
        reference = prepared.get("builds_by_case", {}).get(case_id)
        if not isinstance(reference, dict):
            raise PolicyRepairError(f"test build reference missing for {case_id}")
        build = _load_local_build(reference, case, inputs["target_identity"])
        builds[case_id] = build
        compiled_case = {**case, "source_features": build["source_features"], "build": build}
        packet = shift.policy_packet(compiled_case)
        expected_packets[case_id] = packet
        expected_choices[case_id] = {
            "unchanged_source_forest": harness_policy.choose(
                inputs["source_policy"], packet, inputs["target_identity"]
            )["action"],
            "stride_selector": contraction.stride_choice(build["source_features"]),
            "target_refit": harness_policy.choose(
                target_artifact, packet, inputs["target_identity"]
            )["action"],
            "direct_patch": _patch_action(chooser, packet, case_id),
        }
    if prepared.get("packets_by_case") != expected_packets:
        raise PolicyRepairError("frozen test packets changed")
    if prepared.get("choices_by_case") != expected_choices:
        raise PolicyRepairError("frozen test policy choices changed")
    cache_root = output / "provider-cache"
    actual_cache_files = [
        {"path": str(path.resolve()), "sha256": _sha256(path)}
        for path in sorted(cache_root.rglob("*.json"))
    ] if cache_root.is_dir() else []
    if provider.get("cache_files") != actual_cache_files:
        raise PolicyRepairError("frozen Jev cache file index changed")
    for cache_file in provider.get("cache_files", []):
        path = Path(cache_file.get("path", ""))
        if not path.is_file() or _sha256(path) != cache_file.get("sha256"):
            raise PolicyRepairError("frozen Jev cache file changed")
    by_case = provider.get("responses_by_case", {})
    if set(by_case) != set(case_ids):
        raise PolicyRepairError("Jev reference case index is incomplete")
    failure_seen = False
    for case in cases:
        case_id = case["case_id"]
        record = by_case[case_id]
        if record.get("status") == "available":
            if failure_seen or len(record.get("slots", [])) != provider.get("replicates"):
                raise PolicyRepairError("Jev reference has invalid complete-slot ordering")
            replication._verify_frozen_slots(
                case, expected_packets[case_id], output, record["slots"]
            )
            continue
        if provider.get("status") != "unavailable":
            raise PolicyRepairError("Jev reference is missing a required response")
        if record.get("status") == "unavailable":
            if failure_seen or not isinstance(record.get("reason"), str) or not record["reason"]:
                raise PolicyRepairError("Jev unavailable transition is malformed")
            failure_seen = True
            incomplete = record.get("incomplete_slots", [])
            if len(record.get("slots", [])) + len(incomplete) != provider.get("replicates"):
                raise PolicyRepairError("Jev failure slot evidence is incomplete")
            for slot in record["slots"]:
                _verify_provider_slot(case, expected_packets[case_id], output, slot)
            for slot in incomplete:
                _verify_incomplete_slot(
                    case,
                    expected_packets[case_id],
                    output,
                    slot,
                    provider["max_attempts_per_slot"],
                )
        elif record.get("status") == "not_attempted_prior_failure":
            if not failure_seen or record.get("slots") != []:
                raise PolicyRepairError("Jev post-failure attempt state is malformed")
        else:
            raise PolicyRepairError("unknown Jev case response state")
    if provider.get("status") == "unavailable" and not failure_seen:
        raise PolicyRepairError("Jev reference claims unavailability without a failed slot")
    if provider.get("usage_and_latency") != _jev_summary(by_case):
        raise PolicyRepairError("Jev usage/latency accounting changed")
    return prepared, cases, builds


def _round_runtime(case: dict[str, Any], action: str, block: int, round_number: int) -> int:
    matches = []
    for row in case.get("benchmark", {}).get("round_records", []):
        row_round = row.get("block_round", row.get("round"))
        if row.get("block") == block and row_round == round_number:
            matches.append(row)
    if len(matches) != 1:
        raise PolicyRepairError(f"missing unique timing round {block}/{round_number} for {case.get('case_id')}")
    measurement = matches[0].get("measurements", {}).get(action, {})
    elapsed = measurement.get("elapsed_ns") if isinstance(measurement, dict) else None
    if type(elapsed) is not int or elapsed <= 0:
        raise PolicyRepairError(f"invalid runtime sample for {case.get('case_id')}/{action}")
    return elapsed


def _median_absolute_deviation(values: list[float]) -> float:
    center = statistics.median(values)
    return float(statistics.median(abs(value - center) for value in values))


def _compare_policies(
    cases: list[dict[str, Any]],
    choices: dict[str, dict[str, Any]],
    candidate: str,
    reference: str,
    design: dict[str, Any],
) -> dict[str, Any]:
    groups = sorted({case["group"] for case in cases})
    if len(cases) != 12 or len(groups) != 6 or any(
        sum(case["group"] == group for case in cases) != 2 for group in groups
    ):
        raise PolicyRepairError("test comparisons require twelve cases across six balanced recipes")
    blocks: dict[str, Any] = {}
    for block in (1, 2):
        per_round_logs: list[float] = []
        per_case_logs: dict[str, list[float]] = {case["case_id"]: [] for case in cases}
        for round_number in range(1, design["benchmark_rounds"] + 1):
            group_logs: dict[str, list[float]] = {group: [] for group in groups}
            for case in cases:
                case_id = case["case_id"]
                candidate_choice = choices[case_id][candidate]
                reference_choice = choices[case_id][reference]
                if candidate == "informed_jev_reference":
                    candidate_choices = candidate_choice
                    if not isinstance(candidate_choices, list) or len(candidate_choices) != 3:
                        raise PolicyRepairError("Jev comparison requires three frozen choices per case")
                    candidate_runtime = sum(
                        _round_runtime(case, action, block, round_number)
                        for action in candidate_choices
                    ) / len(candidate_choices)
                else:
                    candidate_runtime = _round_runtime(case, candidate_choice, block, round_number)
                reference_runtime = _round_runtime(case, reference_choice, block, round_number)
                log_ratio = math.log(candidate_runtime / reference_runtime)
                group_logs[case["group"]].append(log_ratio)
                per_case_logs[case_id].append(log_ratio)
            per_group = [sum(group_logs[group]) / len(group_logs[group]) for group in groups]
            per_round_logs.append(sum(per_group) / len(per_group))
        mean_log_ratio = sum(per_round_logs) / len(per_round_logs)
        mad = _median_absolute_deviation(per_round_logs)
        threshold = max(math.log1p(design["noise_floor_fraction"]), 3 * mad)
        group_means = {
            group: sum(
                value
                for case in cases
                if case["group"] == group
                for value in per_case_logs[case["case_id"]]
            )
            / sum(
                len(per_case_logs[case["case_id"]])
                for case in cases
                if case["group"] == group
            )
            for group in groups
        }
        positive_groups = sum(value < 0 for value in group_means.values())
        ratio = math.exp(mean_log_ratio)
        requirements = {
            "geometric_runtime_ratio_below_0_95": ratio < 1 - design["minimum_repair_improvement"],
            "paired_log_improvement_exceeds_dispersion": -mean_log_ratio > threshold,
            "at_least_four_of_six_groups_faster": positive_groups >= design["minimum_positive_test_groups"],
        }
        blocks[str(block)] = {
            "passed": all(requirements.values()),
            "geometric_runtime_ratio": ratio,
            "mean_group_balanced_log_ratio": mean_log_ratio,
            "mean_group_balanced_log_improvement": -mean_log_ratio,
            "round_level_group_balanced_log_ratios": per_round_logs,
            "round_level_mad": mad,
            "dispersion_threshold": threshold,
            "group_mean_log_ratios": group_means,
            "positive_groups": positive_groups,
            "requirements": requirements,
        }
    return {
        "candidate": candidate,
        "reference": reference,
        "passed": all(report["passed"] for report in blocks.values()),
        "blocks": blocks,
    }


def repair_gate(
    cases: list[dict[str, Any]],
    choices: dict[str, dict[str, Any]],
    design: dict[str, Any],
    *,
    jev_available: bool,
) -> dict[str, Any]:
    expected = {case["case_id"] for case in cases}
    if len(cases) != 12 or set(choices) != expected:
        raise PolicyRepairError("repair gate input case set is incomplete")
    declared = {
        name: _compare_policies(cases, choices, candidate, reference, design)
        for name, candidate, reference in COMPARES
    }
    stride_control = _compare_policies(
        cases, choices, "stride_selector", "unchanged_source_forest", design
    )
    stride_control["evaluation_role"] = "descriptive_control_not_repair_acceptance"
    jev_comparison = (
        _compare_policies(cases, choices, "informed_jev_reference", "target_refit", design)
        if jev_available
        else {"candidate": "informed_jev_reference", "reference": "target_refit", "status": "unavailable"}
    )
    return {
        "primary": declared["target_refit_vs_unchanged_source_forest"],
        "comparisons": declared,
        "stride_control_vs_unchanged_source_forest": stride_control,
        "jev_vs_target_refit": jev_comparison,
        "passed": declared["target_refit_vs_unchanged_source_forest"]["passed"],
        "all_local_comparisons_passed": all(item["passed"] for item in declared.values()),
        "jev_available": jev_available,
    }


def measure_test(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    design = _design(design_path)
    inputs = _pinned_inputs(design)
    provenance = _provenance(design_path, design, inputs)
    prepared, cases, builds = _validate_prepared_test(design_path, output, design, inputs)
    adapted = [_adapted_measurement_case(case, builds[case["case_id"]]) for case in cases]
    measured = contraction._measure_cases(
        adapted,
        {
            "benchmark_rounds": design["benchmark_rounds"],
            "seed": design["seed"],
            "target_sample_ms": design["target_sample_ms"],
        },
        output / "timing" / "test",
        blocks=(1, 2),
    )
    measured = _restore_measured_metadata(measured, cases, "test")
    results = []
    choices: dict[str, dict[str, Any]] = {}
    for case in measured:
        case_id = case["case_id"]
        by_block = {
            str(block): [row for row in case["benchmark"]["round_records"] if row["block"] == block]
            for block in (1, 2)
        }
        if any(len(rows) != design["benchmark_rounds"] for rows in by_block.values()):
            raise PolicyRepairError(f"test timing rounds incomplete for {case_id}")
        caches = case.get("timing_caches", [])
        if len(caches) != 2:
            raise PolicyRepairError(f"test timing cache count changed for {case_id}")
        cache_checksums = []
        for reference in caches:
            cache_path = Path(reference["path"])
            if not cache_path.is_file() or _sha256(cache_path) != reference.get("sha256"):
                raise PolicyRepairError(f"test timing cache changed for {case_id}")
            raw = _load(cache_path).get("benchmark", {})
            if raw.get("status") != "measured":
                raise PolicyRepairError(f"test raw timing evidence invalid for {case_id}")
            cache_checksums.append(raw.get("stock_checksum"))
        if len(set(cache_checksums)) != 1:
            raise PolicyRepairError(f"test correctness checksums differ by block for {case_id}")
        choices[case_id] = dict(prepared["choices_by_case"][case_id])
        jev = prepared["jev_reference"]["responses_by_case"].get(case_id, {})
        if prepared["jev_reference"]["status"] == "available":
            choices[case_id]["informed_jev_reference"] = [slot["action"] for slot in jev["slots"]]
        results.append(case)
    jev_available = prepared["jev_reference"]["status"] == "available"
    gate = repair_gate(results, choices, design, jev_available=jev_available)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "repair_improved" if gate["passed"] else "repair_gate_failed",
        "provenance": provenance,
        "prepared_test_sha256": _sha256(output / "prepared-test.json"),
        "cases": results,
        "choices_by_case": choices,
        "jev_reference": prepared["jev_reference"],
        "repair_gate": gate,
        "performance_claim": False,
        "test_labels_used_for_fit_or_choice": False,
    }
    _save_frozen(output / "test-report.json", report, "test report")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=("prepare-adaptation", "measure-adaptation", "fit", "prepare-test", "measure-test"),
    )
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    phases = {
        "prepare-adaptation": prepare_adaptation,
        "measure-adaptation": measure_adaptation,
        "fit": fit_repair,
        "prepare-test": prepare_test,
        "measure-test": measure_test,
    }
    try:
        report = phases[args.phase](args.design, args.output)
    except (
        PolicyRepairError,
        feedback.CompilerFeedbackError,
        replication.FeedbackReplicationError,
        shift.PolicyShiftError,
        contraction.ContractionExperimentError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"workload-policy repair failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


