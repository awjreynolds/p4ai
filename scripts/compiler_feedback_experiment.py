"""Evaluate source-only versus compiler-informed decisions for loop lowering."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
from pathlib import Path
from typing import Any

try:
    from . import (
        benchmark,
        codex_advisor,
        compiler_evidence,
        contraction_build,
        contraction_corpus,
        experiment,
        feedback_policy,
        jev,
        loop_experiment,
        quality_analysis,
    )
    from . import (
        contraction_experiment as contraction,
    )
except ImportError:  # Running as ``python scripts/compiler_feedback_experiment.py``.
    import benchmark
    import codex_advisor
    import compiler_evidence
    import contraction_build
    import contraction_corpus
    import contraction_experiment as contraction
    import experiment
    import feedback_policy
    import jev
    import loop_experiment
    import quality_analysis


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "compiler-feedback-v1.json"
PARTITIONS = ("discovery", "calibrate", "test")
ORDERS = tuple(contraction_corpus.ORDERS)
ACTION_NAMES = ("stock", *ORDERS)
PROVIDER_NAMES = ("jev", "codex")
COMPILER_FEATURES = tuple(compiler_evidence.FEATURE_KEYS)

class CompilerFeedbackError(RuntimeError):
    """The frozen compiler-feedback experiment cannot safely proceed or resume."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompilerFeedbackError(f"cannot read {path}: {error}") from error


def _design(path: Path) -> dict[str, Any]:
    design = _load(path)
    if not isinstance(design, dict) or design.get("schema_version") != 1:
        raise CompilerFeedbackError("unsupported compiler-feedback design")
    if design.get("actions") != list(ACTION_NAMES):
        raise CompilerFeedbackError("design action menu differs from the frozen builder")
    if design.get("target_cpu") != contraction_build.TARGET_CPU:
        raise CompilerFeedbackError("design target CPU differs from the builder")
    if design.get("source_features") != sorted(contraction.FEATURE_DESCRIPTIONS):
        raise CompilerFeedbackError("design source feature schema changed")
    compiler_descriptions = design.get("compiler_features")
    if (
        not isinstance(compiler_descriptions, dict)
        or set(compiler_descriptions) != set(COMPILER_FEATURES)
        or any(not isinstance(value, str) or not value for value in compiler_descriptions.values())
    ):
        raise CompilerFeedbackError("compiler evidence extractor schema changed")

    cases = design.get("cases")
    if not isinstance(cases, list) or len(cases) != 36:
        raise CompilerFeedbackError("design must contain the frozen 36-case corpus")
    expected_counts = {"discovery": 16, "calibrate": 8, "test": 12}
    seen_ids: set[str] = set()
    groups_by_partition: dict[str, set[str]] = {partition: set() for partition in PARTITIONS}
    group_cases: dict[str, list[dict[str, Any]]] = {}
    for row in cases:
        if not isinstance(row, dict) or row.get("partition") not in PARTITIONS:
            raise CompilerFeedbackError("design contains an invalid case partition")
        case_id, group, spec = row.get("case_id"), row.get("group"), row.get("spec")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise CompilerFeedbackError("case IDs must be unique non-empty strings")
        if not isinstance(group, str) or not group:
            raise CompilerFeedbackError(f"case {case_id} has no structural group")
        if not isinstance(spec, dict) or set(spec) != contraction_corpus.REQUIRED_SPEC_KEYS | set(contraction_corpus.PADDING_KEYS):
            raise CompilerFeedbackError(f"case {case_id} has an invalid corpus spec")
        try:
            contraction_corpus._validate(spec, "ijk")
        except (TypeError, ValueError) as error:
            raise CompilerFeedbackError(f"case {case_id} has an invalid corpus spec: {error}") from error
        seen_ids.add(case_id)
        groups_by_partition[row["partition"]].add(group)
        group_cases.setdefault(group, []).append(row)
    if {partition: sum(row["partition"] == partition for row in cases) for partition in PARTITIONS} != expected_counts:
        raise CompilerFeedbackError("design case counts changed")
    expected_group_counts = {"discovery": 4, "calibrate": 2, "test": 3}
    if {partition: len(groups) for partition, groups in groups_by_partition.items()} != expected_group_counts:
        raise CompilerFeedbackError("design structural group counts changed")
    all_groups = [group for groups in groups_by_partition.values() for group in groups]
    if len(all_groups) != len(set(all_groups)):
        raise CompilerFeedbackError("structural groups must not cross partitions")
    for group, rows in group_cases.items():
        if len(rows) != 4:
            raise CompilerFeedbackError(f"structural group {group} must contain four layouts")
        specs = [row["spec"] for row in rows]
        shared = {key: specs[0][key] for key in ("m", "n", "k", *contraction_corpus.PADDING_KEYS)}
        layouts = set()
        for spec in specs:
            if any(spec[key] != value for key, value in shared.items()):
                raise CompilerFeedbackError(f"structural group {group} mixes shapes or padding")
            layouts.add(tuple(spec[key] for key in (
                "a_column_major", "b_column_major", "c_column_major"
            )))
        if len(layouts) != 4:
            raise CompilerFeedbackError(f"structural group {group} repeats a layout")

    for name in ("benchmark_rounds", "target_sample_ms", "seed"):
        if type(design.get(name)) is not int or design[name] <= 0:
            raise CompilerFeedbackError(f"design {name} must be a positive integer")
    for name in ("discovery_gap_fraction", "noise_floor_fraction", "calibration_escalation_budget"):
        value = design.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise CompilerFeedbackError(f"design {name} must be finite")
    if not 0 < design["discovery_gap_fraction"] < 1 or not 0 <= design["noise_floor_fraction"] < 1:
        raise CompilerFeedbackError("design gap and noise thresholds are invalid")
    if not 0 <= design["calibration_escalation_budget"] <= 1:
        raise CompilerFeedbackError("design escalation budget is invalid")
    return design


def _implementation_hashes() -> dict[str, str]:
    paths = (
        Path(__file__),
        Path(contraction.__file__),
        Path(contraction_build.__file__),
        Path(contraction_corpus.__file__),
        Path(compiler_evidence.__file__),
        Path(feedback_policy.__file__),
        Path(loop_experiment.__file__),
        Path(loop_experiment.loop_build.__file__),
        Path(loop_experiment.quality_experiment.__file__),
        Path(benchmark.__file__),
        Path(quality_analysis.__file__),
        Path(experiment.__file__),
        Path(jev.__file__),
        Path(codex_advisor.__file__),
        Path(contraction.counterfactual.__file__),
        Path(contraction.validate.__file__),
    )
    return {str(path.resolve().relative_to(ROOT)): _sha256(path) for path in paths}


def _provenance(design_path: Path, validation_path: Path) -> dict[str, Any]:
    return {
        "design_sha256": _sha256(design_path),
        "validation_report_sha256": _sha256(validation_path),
        "implementation_sha256": _implementation_hashes(),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }


def _cases(design: dict[str, Any], partitions: tuple[str, ...] = PARTITIONS) -> list[dict[str, Any]]:
    return [
        {
            "partition": row["partition"],
            "family": row["group"],
            "group": row["group"],
            "case_id": row["case_id"],
            "spec": dict(row["spec"]),
        }
        for row in design["cases"]
        if row["partition"] in partitions
    ]


def _check_compiler_evidence(build: dict[str, Any], design: dict[str, Any], case_id: str) -> None:
    if (
        build.get("status") != "passed"
        or build.get("action_order") != list(ACTION_NAMES)
        or build.get("stock_ijk_code_identity_equal") is not True
        or not contraction._artifact_hashes_match(build)
    ):
        raise CompilerFeedbackError(f"build or correctness failed for {case_id}")
    for action in ACTION_NAMES:
        row = build.get("actions", {}).get(action, {})
        evidence = row.get("compiler_evidence")
        status = row.get("compiler_evidence_status", {})
        if (
            row.get("status") != "passed"
            or not isinstance(row.get("code_identity"), str)
            or not isinstance(evidence, dict)
            or set(evidence) != set(design["compiler_features"])
            or any(type(value) is not int or value < 0 for value in evidence.values())
            or not isinstance(status, dict)
            or status.get("status") != "complete"
        ):
            raise CompilerFeedbackError(f"compiler evidence is incomplete for {case_id}/{action}")


def _build_case(
    case: dict[str, Any], design: dict[str, Any], validation_path: Path, output: Path
) -> dict[str, Any]:
    case_dir = output / "cases" / case["partition"] / case["case_id"]
    feature_case = contraction_corpus.generate_case(
        case_dir / "source-features", case["spec"], order="ijk"
    )
    build = contraction_build.build_case(
        validation_path, case["spec"], case_dir / "build"
    )
    _check_compiler_evidence(build, design, case["case_id"])
    if sorted(contraction._action_features({"source_features": feature_case["features"]}, "ijk")) != design["source_features"]:
        raise CompilerFeedbackError(f"source feature schema changed for {case['case_id']}")
    feature_kernel, feature_driver = Path(feature_case["kernel"]), Path(feature_case["driver"])
    return {
        **case,
        "source_features": feature_case["features"],
        "feature_kernel_sha256": _sha256(feature_kernel),
        "feature_driver_sha256": _sha256(feature_driver),
        "build_report_path": build["report_path"],
        "build_report_sha256": _sha256(Path(build["report_path"])),
        "distinct_code_identities": len({build["actions"][name]["code_identity"] for name in ORDERS}),
        "build": build,
    }


def _build_all(
    design: dict[str, Any], validation_path: Path, output: Path
) -> list[dict[str, Any]]:
    return [_build_case(case, design, validation_path, output) for case in _cases(design)]


def _validate_builds(
    design: dict[str, Any], validation_path: Path, output: Path, recorded: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = _cases(design)
    if len(recorded) != len(expected):
        raise CompilerFeedbackError("build manifest is missing cases")
    refreshed = []
    for want, have in zip(expected, recorded, strict=True):
        if any(have.get(key) != want[key] for key in ("partition", "group", "family", "case_id", "spec")):
            raise CompilerFeedbackError("build manifest case identity changed")
        case = _build_case(want, design, validation_path, output)
        for key in (
            "source_features", "feature_kernel_sha256", "feature_driver_sha256",
            "build_report_path", "build_report_sha256", "distinct_code_identities",
        ):
            if case.get(key) != have.get(key):
                raise CompilerFeedbackError(f"cached build evidence changed for {want['case_id']}")
        refreshed.append(case)
    return refreshed


def _action_features(case: dict[str, Any], order: str, *, informed: bool) -> dict[str, int]:
    features = contraction._action_features(case, order)
    if informed:
        evidence_by_action = case.get("compiler_evidence_by_action")
        if not isinstance(evidence_by_action, dict):
            build = case.get("build")
            evidence_by_action = {
                name: build["actions"][name]["compiler_evidence"]
                for name in ORDERS
            } if isinstance(build, dict) else {}
        evidence = evidence_by_action.get(order)
        if not isinstance(evidence, dict) or set(evidence) != set(COMPILER_FEATURES):
            raise CompilerFeedbackError(f"missing compiler evidence in {case['case_id']}/{order}")
        features = {**features, **evidence}
    if any(type(value) is not int or value < 0 for value in features.values()):
        raise CompilerFeedbackError(f"non-numeric model feature in {case['case_id']}/{order}")
    return features


def _attach_compiler_evidence(
    measured: list[dict[str, Any]], built: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {case["case_id"]: case for case in built}
    if (
        len(by_id) != len(built)
        or len(measured) != len(by_id)
        or {case.get("case_id") for case in measured} != set(by_id)
    ):
        raise CompilerFeedbackError("measured cases do not match the validated build manifest")
    attached = []
    for case in measured:
        compiled = by_id[case["case_id"]]
        if (
            case.get("partition") != compiled["partition"]
            or case.get("family") != compiled["family"]
            or case.get("spec") != compiled["spec"]
            or case.get("source_features") != compiled["source_features"]
        ):
            raise CompilerFeedbackError(f"measurement/build identity changed for {case['case_id']}")
        evidence = {
            order: dict(compiled["build"]["actions"][order]["compiler_evidence"])
            for order in ORDERS
        }
        existing = case.get("compiler_evidence_by_action")
        if existing is not None and existing != evidence:
            raise CompilerFeedbackError(f"cached compiler evidence changed for {case['case_id']}")
        attached.append({**case, "compiler_evidence_by_action": evidence})
    return attached


def _provider_packet(
    case: dict[str, Any], design: dict[str, Any], *, informed: bool
) -> dict[str, Any]:
    rows = [_action_features(case, order, informed=informed) for order in ORDERS]
    names = sorted(rows[0])
    expected = sorted(design["source_features"] + (list(design["compiler_features"]) if informed else []))
    if names != expected:
        raise CompilerFeedbackError(f"model packet feature schema changed for {case['case_id']}")
    descriptions = {name: contraction.FEATURE_DESCRIPTIONS[name] for name in design["source_features"]}
    if informed:
        descriptions.update(design["compiler_features"])
    feature_prefix = (
        "Choose a loop order for C_ij=sum_k((A_ik XOR uint32(salt))*B_kj) modulo 2^32. "
        "C starts at zero; A, B and C are disjoint uint32 buffers. Preserve the exact result. "
        "Minimize native runtime. The target is Apple M3 with a 128-bit NEON vector unit. Numeric axes are i=0,j=1,k=2; "
        "action fields encode outer, middle and inner loop axes."
    )
    task = feature_prefix
    if informed:
        task += " The compiler fields are static evidence from optimized LLVM IR and the compiled kernel object for each action; they are not runtime measurements."
    return {
        "task": task,
        "features": {name: [row[name] for row in rows] for name in names},
        "feature_descriptions": descriptions,
        "actions": [
            {
                "id": str(index),
                "description": (
                    f"Traverse numeric axes outer={contraction.AXIS_IDS[order[0]]}, "
                    f"middle={contraction.AXIS_IDS[order[1]]}, inner={contraction.AXIS_IDS[order[2]]}"
                ),
            }
            for index, order in enumerate(ORDERS)
        ],
    }


def _training_rows(cases: list[dict[str, Any]], *, block: int, informed: bool) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        baseline = contraction._block_median(case, "stock", block)
        for order in ORDERS:
            rows.append({
                "features": _action_features(case, order, informed=informed),
                "loss": contraction._block_median(case, order, block) / baseline,
            })
    return rows


def _fit_informed_models(cases: list[dict[str, Any]], *, block: int = 1) -> dict[str, Any]:
    rows = _training_rows(cases, block=block, informed=True)
    return {
        "nearest_neighbor": quality_analysis.fit_nearest_neighbor(rows),
        "ridge": feedback_policy.fit_ridge(rows, alpha=1.0),
        "training_case_ids": [case["case_id"] for case in cases],
        "training_block": block,
    }


def _predict_models(models: dict[str, Any], case: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    packet = _provider_packet(case, design, informed=True)
    nn = quality_analysis.score_actions(
        models["nearest_neighbor"], packet["features"], packet["actions"]
    )
    ridge = feedback_policy.score_packet(models["ridge"], packet)
    return {
        "nearest_neighbor": ORDERS[int(nn["action_id"])],
        "ridge": ORDERS[int(ridge["action_id"])],
        "nearest_neighbor_scores": nn,
        "ridge_predicted_log_losses": {
            ORDERS[int(action_id)]: value
            for action_id, value in ridge["predicted_log_losses"].items()
        },
    }


def _fit_discovery(first_block: list[dict[str, Any]], design: dict[str, Any]) -> dict[str, Any]:
    discovery = [case for case in first_block if case["partition"] == "discovery"]
    base = contraction._fit_discovery(discovery)
    out_of_group: dict[str, dict[str, Any]] = {}
    folds: dict[str, dict[str, list[str]]] = {}
    groups = sorted({case["family"] for case in discovery})
    for group in groups:
        train = [case for case in discovery if case["family"] != group]
        held = [case for case in discovery if case["family"] == group]
        models = _fit_informed_models(train, block=1)
        folds[group] = {
            "training_case_ids": [case["case_id"] for case in train],
            "held_out_case_ids": [case["case_id"] for case in held],
        }
        for case in held:
            out_of_group[case["case_id"]] = _predict_models(models, case, design)
    if set(out_of_group) != {case["case_id"] for case in discovery}:
        raise CompilerFeedbackError("out-of-group predictions omitted discovery cases")
    return {**base, "out_of_group_predictions": out_of_group, "folds": folds}


def _comparison(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, str]],
    candidate: str, reference: str, block: int,
) -> dict[str, Any]:
    return contraction._comparison(cases, choices, candidate, reference, block)


def discovery_gate(
    cases: list[dict[str, Any]], design: dict[str, Any], fit: dict[str, Any]
) -> dict[str, Any]:
    discovery = [case for case in cases if case["partition"] == "discovery"]
    choices = {
        case["case_id"]: {
            "oracle": fit["case_oracle_actions"][case["case_id"]],
            "best_constant": fit["best_constant"],
            "stride_selector": fit["stride_actions"][case["case_id"]],
            "nearest_neighbor": fit["out_of_group_predictions"][case["case_id"]]["nearest_neighbor"],
            "ridge": fit["out_of_group_predictions"][case["case_id"]]["ridge"],
        }
        for case in discovery
    }
    comparisons = {
        name: _comparison(discovery, choices, "oracle", name, block=2)
        for name in ("best_constant", "stride_selector", "nearest_neighbor", "ridge")
    }
    requirements: dict[str, bool] = {}
    noise: dict[str, float] = {}
    group_positive = {}
    for name, result in comparisons.items():
        floor = max(
            math.log1p(design["noise_floor_fraction"]),
            3 * result["maximum_state_mad_log_ratio"],
        )
        noise[name] = floor
        requirements[f"oracle_at_least_five_percent_better_vs_{name}"] = (
            result["geometric_mean_ratio"] <= 1 - design["discovery_gap_fraction"]
        )
        requirements[f"oracle_beyond_noise_vs_{name}"] = (
            math.log(result["geometric_mean_ratio"]) < -floor
        )
        group_positive[name] = {
            group: row["geometric_mean_ratio"] < 1
            for group, row in result["families"].items()
        }
        requirements[f"oracle_positive_in_two_groups_vs_{name}"] = sum(group_positive[name].values()) >= 2
    distinct = sum(case.get("distinct_code_identities", 0) > 1 for case in discovery)
    requirements["distinct_code_in_half_cases"] = distinct >= math.ceil(len(discovery) / 2)
    return {
        "passed": all(requirements.values()),
        "comparisons_oracle_vs_controls": comparisons,
        "noise_floor_log_ratio": noise,
        "family_positive_direction": group_positive,
        "distinct_code_cases": distinct,
        "discovery_case_count": len(discovery),
        "requirements": requirements,
    }


def _read_timing_file(path: Path, provenance: dict[str, Any]) -> list[dict[str, Any]]:
    value = _load(path)
    if not isinstance(value, dict) or value.get("provenance") != provenance or not isinstance(value.get("cases"), list):
        raise CompilerFeedbackError(f"timing checkpoint provenance changed: {path}")
    cases = value["cases"]
    contraction._verify_cache_refs(cases)
    return cases


def run_discovery(design_path: Path, validation_path: Path, output: Path) -> dict[str, Any]:
    design = _design(design_path)
    provenance = _provenance(design_path, validation_path)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "builds.json"
    if manifest_path.exists():
        manifest = _load(manifest_path)
        if manifest.get("provenance") != provenance:
            raise CompilerFeedbackError("build manifest provenance changed")
        built = _validate_builds(design, validation_path, output, manifest.get("cases", []))
    else:
        built = _build_all(design, validation_path, output)
        _save(manifest_path, {"schema_version": 1, "provenance": provenance, "cases": built})

    discovery_built = [case for case in built if case["partition"] == "discovery"]
    expected_discovery_ids = {case["case_id"] for case in discovery_built}
    first_path = output / "discovery-block-1.json"
    if first_path.exists():
        first = _read_timing_file(first_path, provenance)
    else:
        first = contraction._measure_cases(discovery_built, design, output, blocks=(1,))
        first = _attach_compiler_evidence(first, discovery_built)
        _save(first_path, {"schema_version": 1, "provenance": provenance, "cases": first})
    first = _attach_compiler_evidence(first, discovery_built)
    if {case.get("case_id") for case in first} != expected_discovery_ids:
        raise CompilerFeedbackError("discovery block one case set changed")
    fit = _fit_discovery(first, design)
    fit_path = output / "discovery-fit.json"
    fit_checkpoint = {
        "schema_version": 1,
        "provenance": provenance,
        "block_one_sha256": _sha256(first_path),
        "fit": fit,
    }
    if fit_path.exists() and _load(fit_path) != fit_checkpoint:
        raise CompilerFeedbackError("frozen block-one control fit changed")
    if not fit_path.exists():
        _save(fit_path, fit_checkpoint)

    second_path = output / "discovery-block-2.json"
    if second_path.exists():
        second = _read_timing_file(second_path, provenance)
    else:
        second = contraction._measure_cases(discovery_built, design, output, blocks=(2,))
        second = _attach_compiler_evidence(second, discovery_built)
        _save(second_path, {"schema_version": 1, "provenance": provenance, "cases": second})
    second = _attach_compiler_evidence(second, discovery_built)
    if {case.get("case_id") for case in second} != expected_discovery_ids:
        raise CompilerFeedbackError("discovery confirmation case set changed")
    complete = contraction._merge_blocks(first, second)
    gate = discovery_gate(complete, design, fit)
    report = {
        "schema_version": 1,
        "status": "passed" if gate["passed"] else "stopped_no_confirmed_gap",
        "provenance": provenance,
        "design": str(design_path.resolve()),
        "validation_report": str(validation_path.resolve()),
        "build_manifest": {"path": str(manifest_path), "sha256": _sha256(manifest_path)},
        "first_block": {"path": str(first_path), "sha256": _sha256(first_path)},
        "fit_checkpoint": {"path": str(fit_path), "sha256": _sha256(fit_path)},
        "confirmation_block": {"path": str(second_path), "sha256": _sha256(second_path)},
        "discovery_gate": gate,
        "discovery_cases": complete,
        "model_calls": 0,
    }
    _save(output / "discovery.json", report)
    return report


def _validate_discovery_report(
    report_path: Path, design_path: Path, validation_path: Path, output: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    report = _load(report_path)
    design = _design(design_path)
    provenance = _provenance(design_path, validation_path)
    if report.get("provenance") != provenance:
        raise CompilerFeedbackError("discovery provenance changed")
    for key in ("build_manifest", "first_block", "fit_checkpoint", "confirmation_block"):
        reference = report.get(key, {})
        path = Path(reference.get("path", ""))
        if not path.is_file() or _sha256(path) != reference.get("sha256"):
            raise CompilerFeedbackError(f"frozen discovery evidence changed: {key}")
    manifest_path = Path(report["build_manifest"]["path"])
    manifest = _load(manifest_path)
    if manifest.get("provenance") != provenance:
        raise CompilerFeedbackError("build manifest provenance changed")
    built = _validate_builds(design, validation_path, output, manifest.get("cases", []))
    discovery_built = [case for case in built if case["partition"] == "discovery"]
    first = _attach_compiler_evidence(
        _read_timing_file(Path(report["first_block"]["path"]), provenance), discovery_built
    )
    second = _attach_compiler_evidence(
        _read_timing_file(Path(report["confirmation_block"]["path"]), provenance), discovery_built
    )
    expected_discovery_ids = {case["case_id"] for case in built if case["partition"] == "discovery"}
    if any({case.get("case_id") for case in rows} != expected_discovery_ids for rows in (first, second)):
        raise CompilerFeedbackError("frozen discovery timing case set changed")
    checkpoint = _load(Path(report["fit_checkpoint"]["path"]))
    fit = _fit_discovery(first, design)
    if checkpoint.get("fit") != fit or checkpoint.get("block_one_sha256") != report["first_block"]["sha256"]:
        raise CompilerFeedbackError("frozen discovery fit does not match block one")
    complete = contraction._merge_blocks(first, second)
    gate = discovery_gate(complete, design, fit)
    if report.get("discovery_gate") != gate or report.get("discovery_cases") != complete:
        raise CompilerFeedbackError("discovery gate or merged timing evidence changed")
    if report.get("status") != ("passed" if gate["passed"] else "stopped_no_confirmed_gap"):
        raise CompilerFeedbackError("discovery status does not match the frozen gate")
    return report, design, {"provenance": provenance, "built": built, "fit": fit}


def _predict_final_controls(
    models: dict[str, Any], fit: dict[str, Any], case: dict[str, Any], design: dict[str, Any]
) -> dict[str, Any]:
    score = _predict_models(models, case, design)
    return {
        "best_constant": fit["best_constant"],
        "stride_selector": contraction.stride_choice(case["source_features"]),
        **score,
    }


def _query_provider(
    packet: dict[str, Any], provider: str, cache_path: Path, *, allow_call: bool
) -> dict[str, Any]:
    if provider not in PROVIDER_NAMES:
        raise CompilerFeedbackError(f"unknown provider {provider}")
    fingerprint = loop_experiment._packet_fingerprint(packet)
    cache = _load(cache_path) if cache_path.exists() else {"schema_version": 2, "packets": {}}
    if not isinstance(cache, dict) or cache.get("schema_version") != 2 or not isinstance(cache.get("packets"), dict):
        raise CompilerFeedbackError("provider cache is malformed")
    entry = cache["packets"].get(fingerprint)
    if entry is None:
        if not allow_call:
            raise CompilerFeedbackError("frozen model choice has no matching provider cache entry")
        entry = {
            "packet_sha256": fingerprint,
            "packet": packet,
            "responses": {},
            "response_sha256": {},
            "attempts": [],
        }
        cache["packets"][fingerprint] = entry
    if entry.get("packet_sha256") != fingerprint or entry.get("packet") != packet:
        raise CompilerFeedbackError("cached provider packet changed")
    responses = entry.get("responses")
    response_hashes = entry.get("response_sha256")
    if not isinstance(responses, dict) or not isinstance(response_hashes, dict):
        raise CompilerFeedbackError("provider response manifest is malformed")
    if not isinstance(entry.get("attempts"), list):
        raise CompilerFeedbackError("provider attempts are malformed")
    for name, response in responses.items():
        if name not in PROVIDER_NAMES or response_hashes.get(name) != loop_experiment._packet_fingerprint(response):
            raise CompilerFeedbackError(f"cached {name} response changed")
    if provider in responses:
        return entry
    if not allow_call:
        raise CompilerFeedbackError(f"frozen {provider} response is missing from provider cache")

    choose = jev.choose if provider == "jev" else codex_advisor.choose
    policy = experiment.OneInterventionPolicy("jev")
    response = policy._ask(provider, choose, packet, packet["actions"])
    entry["attempts"].extend(policy.provider_attempts)
    if response is not None:
        entry["responses"][provider] = response
        entry["response_sha256"][provider] = loop_experiment._packet_fingerprint(response)
    _save(cache_path, cache)
    if response is None:
        raise CompilerFeedbackError(f"{provider} returned no valid decision; failed attempt is cached")
    return entry


def _provider_action(entry: dict[str, Any], provider: str) -> str:
    response = entry["responses"][provider]
    return loop_experiment._response_action(response, list(ORDERS))


def _policy_comparisons(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, str]], block: int = 1
) -> dict[str, Any]:
    policies = list(next(iter(choices.values())))
    return {
        f"{candidate}_vs_{reference}": contraction._comparison(
            cases, choices, candidate, reference, block
        )
        for candidate in policies
        for reference in policies
        if candidate != reference
    }


def _freeze_choices(
    built: list[dict[str, Any]], discovery: dict[str, Any], fit: dict[str, Any],
    design: dict[str, Any], provenance: dict[str, Any], output: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    discovery_path = output / "discovery.json"
    choices_path = output / "model-choices.json"
    cache_path = output / "provider-cache.json"
    existing = _load(choices_path) if choices_path.exists() else None
    allow_call = existing is None and discovery["discovery_gate"]["passed"]
    discovery_cases = [case for case in discovery["discovery_cases"] if case["partition"] == "discovery"]
    models = _fit_informed_models(discovery_cases, block=1)
    choices: dict[str, dict[str, Any]] = {}
    for case in built:
        if case["partition"] not in ("calibrate", "test"):
            continue
        row = _predict_final_controls(models, fit, case, design)
        source_packet = _provider_packet(case, design, informed=False)
        info_packet = _provider_packet(case, design, informed=True)
        row["packet_sha256"] = {
            "source": loop_experiment._packet_fingerprint(source_packet),
            "compiler_informed": loop_experiment._packet_fingerprint(info_packet),
        }
        row["provider_responses"] = {}
        if allow_call or existing is not None and discovery["discovery_gate"]["passed"]:
            cached_only = existing is not None
            source_jev = _query_provider(source_packet, "jev", cache_path, allow_call=not cached_only)
            informed_jev = _query_provider(info_packet, "jev", cache_path, allow_call=not cached_only)
            informed_codex = _query_provider(info_packet, "codex", cache_path, allow_call=not cached_only)
            row.update({
                "source_jev": _provider_action(source_jev, "jev"),
                "informed_jev": _provider_action(informed_jev, "jev"),
                "informed_codex": _provider_action(informed_codex, "codex"),
            })
            row["provider_responses"] = {
                "source_jev": source_jev["responses"]["jev"],
                "informed_jev": informed_jev["responses"]["jev"],
                "informed_codex": informed_codex["responses"]["codex"],
            }
        choices[case["case_id"]] = row
    cache_sha = _sha256(cache_path) if cache_path.exists() else None
    evidence = {
        "schema_version": 1,
        "provenance": provenance,
        "discovery_sha256": _sha256(discovery_path),
        "informed_models": models,
        "provider_cache_sha256": cache_sha,
        "choices": choices,
        "provider_case_count": len(choices) if discovery["discovery_gate"]["passed"] else 0,
        "provider_decision_count": 3 * len(choices) if discovery["discovery_gate"]["passed"] else 0,
    }
    if existing is not None:
        if existing != evidence:
            raise CompilerFeedbackError("cached model choices differ from frozen inputs")
    else:
        _save(choices_path, evidence)
    return evidence, choices


def _case_runtime(case: dict[str, Any], action: str, block: int = 1) -> float:
    return contraction._block_median(case, action, block)


def _router_regret(choice: dict[str, Any]) -> tuple[bool, float]:
    disagreement = choice["stride_selector"] != choice["ridge"]
    losses = choice["ridge_predicted_log_losses"]
    minimum = min(losses.values())
    regret = max(0.0, losses[choice.get("informed_jev", choice["ridge"])] - minimum)
    return disagreement, regret


def _fit_router(
    calibration: list[dict[str, Any]], choices: dict[str, dict[str, Any]], design: dict[str, Any]
) -> dict[str, Any]:
    regrets = sorted({
        _router_regret(choices[case["case_id"]])[1]
        for case in calibration
        if _router_regret(choices[case["case_id"]])[0]
    })
    thresholds = sorted({0.0, *((left + right) / 2 for left, right in zip(regrets, regrets[1:]))})
    rules: list[dict[str, Any]] = [{"mode": "never", "threshold": None}]
    rules.extend({"mode": "regret_threshold", "threshold": value} for value in thresholds)
    limit = int(len(calibration) * design["calibration_escalation_budget"] + 1e-12)
    candidates = []
    for rule in rules:
        routed = []
        objective_logs = []
        raw_logs = []
        for case in calibration:
            case_id = case["case_id"]
            choice = choices[case_id]
            disagreement, regret = _router_regret(choice)
            route = rule["mode"] == "regret_threshold" and disagreement and regret > rule["threshold"]
            routed.append(case_id if route else None)
            if route:
                log_ratio = (
                    math.log(_case_runtime(case, choice["informed_codex"]))
                    - math.log(_case_runtime(case, choice["informed_jev"]))
                )
                raw_logs.append(log_ratio)
                if math.log(0.98) <= log_ratio < 0:
                    log_ratio = 0.0
            else:
                raw_logs.append(0.0)
            objective_logs.append(log_ratio if route else 0.0)
        count = sum(case_id is not None for case_id in routed)
        candidates.append({
            **rule,
            "escalations": count,
            "escalation_fraction": count / len(calibration),
            "within_budget": count <= limit,
            "average_log_ratio_vs_no_codex": sum(objective_logs) / len(calibration),
            "raw_geometric_mean_ratio_vs_no_codex": math.exp(sum(raw_logs) / len(calibration)),
            "routed_case_ids": [case_id for case_id in routed if case_id is not None],
        })
    eligible = [candidate for candidate in candidates if candidate["within_budget"]]
    chosen = min(eligible, key=lambda row: (
        row["average_log_ratio_vs_no_codex"],
        row["escalations"],
        row["mode"] != "never",
        -(row["threshold"] if row["threshold"] is not None else math.inf),
    ))
    return {
        "chosen": dict(chosen),
        "max_escalations": limit,
        "candidates": candidates,
    }


def _apply_route(choice: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    disagreement, regret = _router_regret(choice)
    route = (
        disagreement
        and rule["mode"] == "regret_threshold"
        and regret > rule["threshold"]
    )
    no_codex = choice["informed_jev"] if disagreement else choice["ridge"]
    return {
        "disagreement": disagreement,
        "estimated_jev_regret": regret,
        "route_to_codex": route,
        "no_codex_cascade": no_codex,
        "cascade": choice["informed_codex"] if route else no_codex,
    }


def _success_gate(
    comparisons: dict[str, Any], design: dict[str, Any],
    calibration_route_fraction: float, test_route_fraction: float,
) -> dict[str, Any]:
    baselines = (
        "best_constant", "stride_selector", "nearest_neighbor", "ridge",
        "informed_jev", "no_codex_cascade", "ridge_repair_cascade",
    )
    requirements: dict[str, bool] = {}
    noise_floors = {}
    positive = {}
    for baseline in baselines:
        result = comparisons[f"cascade_vs_{baseline}"]
        floor = max(math.log1p(design["noise_floor_fraction"]), 3 * result["maximum_state_mad_log_ratio"])
        noise_floors[baseline] = {"log_ratio": floor, "fraction": math.expm1(floor)}
        requirements[f"cascade_beyond_noise_vs_{baseline}"] = math.log(result["geometric_mean_ratio"]) < -floor
        positive[baseline] = {
            group: metric["geometric_mean_ratio"] < 1
            for group, metric in result["families"].items()
        }
        requirements[f"cascade_positive_in_two_groups_vs_{baseline}"] = sum(positive[baseline].values()) >= 2
    requirements["calibration_escalation_within_budget"] = calibration_route_fraction <= design["calibration_escalation_budget"]
    requirements["test_escalation_within_budget"] = test_route_fraction <= design["calibration_escalation_budget"]
    return {
        "passed": all(requirements.values()),
        "requirements": requirements,
        "noise_floors": noise_floors,
        "group_positive_direction": positive,
        "calibration_escalation_fraction": calibration_route_fraction,
        "test_escalation_fraction": test_route_fraction,
    }


def _information_gate(cases: list[dict[str, Any]], choices: dict[str, dict[str, str]], design: dict[str, Any]) -> dict[str, Any]:
    comparison = _comparison(cases, choices, "informed_jev", "source_jev", 1)
    floor = max(math.log1p(design["noise_floor_fraction"]), 3 * comparison["maximum_state_mad_log_ratio"])
    positive = {group: row["geometric_mean_ratio"] < 1 for group, row in comparison["families"].items()}
    requirements = {
        "informed_jev_beyond_noise_vs_source_jev": math.log(comparison["geometric_mean_ratio"]) < -floor,
        "positive_in_two_groups": sum(positive.values()) >= 2,
    }
    return {
        "passed": all(requirements.values()),
        "comparison": comparison,
        "noise_floor_log_ratio": floor,
        "group_positive_direction": positive,
        "requirements": requirements,
    }


def _load_or_measure(
    cases: list[dict[str, Any]], design: dict[str, Any], output: Path,
    provenance: dict[str, Any], label: str,
) -> list[dict[str, Any]]:
    path = output / f"{label}-cases.json"
    expected_ids = {case["case_id"] for case in cases}
    if path.exists():
        measured = _read_timing_file(path, provenance)
        if {case.get("case_id") for case in measured} != expected_ids:
            raise CompilerFeedbackError(f"{label} timing checkpoint case set changed")
        return _attach_compiler_evidence(measured, cases)
    measured = contraction._measure_cases(cases, design, output, blocks=(1,))
    if {case.get("case_id") for case in measured} != expected_ids:
        raise CompilerFeedbackError(f"{label} measurement omitted cases")
    measured = _attach_compiler_evidence(measured, cases)
    _save(path, {
        "schema_version": 1,
        "provenance": provenance,
        "cases": measured,
    })
    return measured


def run_models(
    design_path: Path, validation_path: Path, output: Path,
    discovery_report_path: Path | None = None,
) -> dict[str, Any]:
    discovery_path = discovery_report_path or output / "discovery.json"
    discovery, design, context = _validate_discovery_report(
        discovery_path, design_path, validation_path, output
    )
    provenance, fit = context["provenance"], context["fit"]
    built = context["built"]
    choice_evidence, choices = _freeze_choices(built, discovery, fit, design, provenance, output)
    choice_path = output / "model-choices.json"
    cal_built = [case for case in built if case["partition"] == "calibrate"]
    test_built = [case for case in built if case["partition"] == "test"]

    # Every blind model choice is saved before the calibration pilots or timings.
    calibration = _load_or_measure(cal_built, design, output, provenance, "calibration")
    gate_passed = discovery["discovery_gate"]["passed"]
    routing = None
    if gate_passed:
        routing = _fit_router(calibration, choices, design)
        route_path = output / "routing.json"
        route_evidence = {
            "schema_version": 1,
            "provenance": provenance,
            "model_choices_sha256": _sha256(choice_path),
            "calibration_cases_sha256": _sha256(output / "calibration-cases.json"),
            "routing": routing,
        }
        if route_path.exists() and _load(route_path) != route_evidence:
            raise CompilerFeedbackError("frozen calibration routing changed")
        if not route_path.exists():
            _save(route_path, route_evidence)
    test = _load_or_measure(test_built, design, output, provenance, "test")

    measured = {case["case_id"]: case for case in [*calibration, *test]}
    policy_actions: dict[str, dict[str, str]] = {}
    route_details: dict[str, dict[str, Any]] = {}
    for case_id, choice in choices.items():
        baseline = {
            name: choice[name]
            for name in ("best_constant", "stride_selector", "nearest_neighbor", "ridge")
        }
        if gate_passed:
            baseline.update({
                "source_jev": choice["source_jev"],
                "informed_jev": choice["informed_jev"],
                "informed_codex": choice["informed_codex"],
            })
            route = _apply_route(choice, routing["chosen"])
            baseline.update({
                "no_codex_cascade": route["no_codex_cascade"],
                "ridge_repair_cascade": (
                    choice["ridge"] if route["route_to_codex"] else route["no_codex_cascade"]
                ),
                "cascade": route["cascade"],
            })
            route_details[case_id] = route
        policy_actions[case_id] = baseline

    cal_choices = {case["case_id"]: policy_actions[case["case_id"]] for case in calibration}
    test_choices = {case["case_id"]: policy_actions[case["case_id"]] for case in test}
    calibration_comparisons = _policy_comparisons(calibration, cal_choices)
    test_comparisons = _policy_comparisons(test, test_choices)
    test_call_fraction = (
        sum(item["route_to_codex"] for case_id, item in route_details.items() if measured[case_id]["partition"] == "test")
        / len(test)
        if gate_passed else 0.0
    )
    cal_call_fraction = routing["chosen"]["escalation_fraction"] if gate_passed else 0.0
    success = None
    information = None
    if gate_passed:
        success = _success_gate(test_comparisons, design, cal_call_fraction, test_call_fraction)
        information = _information_gate(
            test,
            {case_id: {"source_jev": choices[case_id]["source_jev"], "informed_jev": choices[case_id]["informed_jev"]} for case_id in choices},
            design,
        )
    status = (
        "passed" if success and success["passed"] and information and information["passed"]
        else "cascade_passed_information_test_failed" if success and success["passed"]
        else "failed_model_success_gate" if gate_passed
        else "opportunity_gate_failed_cheap_controls_evaluated"
    )
    provider_cache = output / "provider-cache.json"
    report = {
        "schema_version": 1,
        "status": status,
        "provenance": provenance,
        "discovery_sha256": _sha256(discovery_path),
        "model_choices": {"path": str(choice_path), "sha256": _sha256(choice_path)},
        "provider_cache": {
            "path": str(provider_cache),
            "sha256": _sha256(provider_cache) if provider_cache.exists() else None,
        },
        "discovery_gate_passed": gate_passed,
        "informed_models": choice_evidence["informed_models"],
        "routing": routing,
        "route_details": route_details,
        "policy_actions": policy_actions,
        "calibration_cases": calibration,
        "test_cases": test,
        "calibration_comparisons": calibration_comparisons,
        "test_comparisons": test_comparisons,
        "success_gate": success,
        "information_test": information,
        "model_call_case_count": choice_evidence["provider_case_count"],
        "model_call_decision_count": choice_evidence["provider_decision_count"],
    }
    report_path = output / "models.json"
    if report_path.exists() and _load(report_path) != report:
        raise CompilerFeedbackError("frozen model report changed on resume")
    if not report_path.exists():
        _save(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("discovery", "models"), default="discovery")
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discovery-report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.phase == "discovery":
            report = run_discovery(args.design.resolve(), args.validation_report.resolve(), args.output.resolve())
        else:
            report = run_models(
                args.design.resolve(), args.validation_report.resolve(), args.output.resolve(), args.discovery_report
            )
    except (
        CompilerFeedbackError,
        contraction.ContractionExperimentError,
        contraction_build.ContractionBuildError,
        benchmark.BenchmarkError,
        OSError,
        ValueError,
    ) as error:
        print(f"compiler-feedback experiment failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
