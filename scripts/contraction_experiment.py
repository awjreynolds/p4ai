"""Run the frozen loop-order decision experiment for unsigned contractions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
from pathlib import Path
from typing import Any

try:
    from . import (
        benchmark,
        codex_advisor,
        contraction_build,
        contraction_corpus,
        counterfactual,
        experiment,
        jev,
        loop_experiment,
        quality_analysis,
        validate,
    )
except ImportError:  # Running as ``python scripts/contraction_experiment.py``.
    import benchmark
    import codex_advisor
    import contraction_build
    import contraction_corpus
    import counterfactual
    import experiment
    import jev
    import loop_experiment
    import quality_analysis
    import validate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "contraction-policy-v1.json"
PARTITIONS = ("discovery", "calibrate", "test")
ORDERS = tuple(contraction_corpus.ORDERS)
ACTION_NAMES = ("stock", *ORDERS)
AXIS_IDS = {axis: index for index, axis in enumerate("ijk")}
TARGET_FEATURES = {
    "target_architecture_bits": 64,
    "target_vector_register_bits": 128,
    "target_has_sve": 0,
}
FEATURE_DESCRIPTIONS = {
    "m": "compile-time output row count",
    "n": "compile-time output column count",
    "k": "compile-time reduction length",
    "trip_count": "total multiply-accumulate operations",
    "element_bits": "bits in each array element",
    "arithmetic_bits": "bits in unsigned modular arithmetic",
    **{
        f"{array}_{axis}_stride_bytes": f"{array.upper()} physical byte stride for axis {axis}"
        for array, axes in (("a", "ik"), ("b", "kj"), ("c", "ij"))
        for axis in axes
    },
    "target_architecture_bits": "fixed target architecture width",
    "target_vector_register_bits": "fixed target vector register width",
    "target_has_sve": "fixed target fact: SVE availability",
    "inner_axis_id": "numeric axis identifier traversed by the innermost loop: i=0,j=1,k=2",
    "middle_axis_id": "numeric axis identifier traversed by the middle loop: i=0,j=1,k=2",
    "outer_axis_id": "numeric axis identifier traversed by the outermost loop: i=0,j=1,k=2",
    **{
        f"{position}_{array}_stride_bytes": f"{array.upper()} byte stride on the {position} loop axis"
        for position in ("inner", "middle", "outer")
        for array in ("a", "b", "c")
    },
    **{
        f"{position}_stride_cost_bytes": f"stride-selector cost of the {position} loop axis in bytes"
        for position in ("inner", "middle", "outer")
    },
}


class ContractionExperimentError(RuntimeError):
    """The frozen contraction experiment cannot safely proceed or resume."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractionExperimentError(f"cannot read report {path}: {error}") from error


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _design(path: Path) -> dict[str, Any]:
    design = _load(path)
    if not isinstance(design, dict) or design.get("schema_version") != 1:
        raise ContractionExperimentError("unsupported contraction experiment design")
    families = design.get("families")
    if not isinstance(families, dict) or any(
        not isinstance(families.get(partition), list) or not families[partition]
        for partition in PARTITIONS
    ):
        raise ContractionExperimentError("design must define all three layout partitions")
    groups = [group for partition in PARTITIONS for group in families[partition]]
    if len(groups) != len(set(groups)) or set(groups) != {
        "rrc", "ccr", "crr", "crc", "ccc", "rrr", "rcr", "rcc"
    }:
        raise ContractionExperimentError("design layout partitions changed")
    shapes = design.get("shapes")
    required = {"m", "n", "k"}
    if not isinstance(shapes, list) or not shapes or any(
        not isinstance(shape, dict)
        or set(shape) != required
        or any(type(shape[name]) is not int or shape[name] <= 0 for name in required)
        for shape in shapes
    ):
        raise ContractionExperimentError("design shapes are invalid")
    if design.get("actions") != list(ACTION_NAMES):
        raise ContractionExperimentError("design action menu changed")
    for name in ("benchmark_rounds", "target_sample_ms", "seed"):
        if type(design.get(name)) is not int or design[name] <= 0:
            raise ContractionExperimentError(f"design {name} must be positive")
    if design.get("target_cpu") != contraction_build.TARGET_CPU:
        raise ContractionExperimentError("design target CPU differs from the builder")
    for name in ("discovery_gap_fraction", "noise_floor_fraction", "calibration_escalation_budget"):
        value = design.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ContractionExperimentError(f"design {name} must be finite")
    if not 0 < design["discovery_gap_fraction"] < 1 or not 0 <= design["noise_floor_fraction"] < 1:
        raise ContractionExperimentError("design discovery thresholds are invalid")
    if not 0 <= design["calibration_escalation_budget"] <= 1:
        raise ContractionExperimentError("design escalation budget is invalid")
    if design.get("stride_selector") is None or design.get("semantic_contract") is None:
        raise ContractionExperimentError("design is missing a frozen selector or contract")
    return design


def _implementation_hashes() -> dict[str, str]:
    modules = (
        Path(__file__),
        Path(contraction_build.__file__),
        Path(contraction_corpus.__file__),
        Path(benchmark.__file__),
        Path(quality_analysis.__file__),
        Path(experiment.__file__),
        Path(jev.__file__),
        Path(codex_advisor.__file__),
        Path(loop_experiment.__file__),
        Path(loop_experiment.loop_build.__file__),
        Path(loop_experiment.quality_experiment.__file__),
        Path(counterfactual.__file__),
        Path(validate.__file__),
    )
    return {str(path.resolve().relative_to(ROOT)): _sha256(path) for path in modules}


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
    cases = []
    for partition in partitions:
        for group in design["families"][partition]:
            for shape in design["shapes"]:
                spec = {
                    **shape,
                    "a_column_major": group[0] == "c",
                    "b_column_major": group[1] == "c",
                    "c_column_major": group[2] == "c",
                }
                case_id = f"{group}-m{shape['m']}-n{shape['n']}-k{shape['k']}"
                cases.append(
                    {
                        "partition": partition,
                        "family": group,
                        "spec": spec,
                        "case_id": case_id,
                    }
                )
    return cases


def _artifact_hashes_match(build: dict[str, Any]) -> bool:
    for row in build.get("actions", {}).values():
        for artifact in row.get("artifacts", {}).values():
            if not isinstance(artifact, dict):
                return False
            path, digest = artifact.get("path"), artifact.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                return False
            candidate = Path(path)
            if not candidate.is_file() or _sha256(candidate) != digest:
                return False
    return True


def _build_all(
    design: dict[str, Any], validation_report: Path, output: Path
) -> list[dict[str, Any]]:
    built = []
    for case in _cases(design):
        case_dir = output / "cases" / case["partition"] / case["case_id"]
        feature_source = contraction_corpus.generate_case(
            case_dir / "source-features", case["spec"], order="ijk"
        )
        build = contraction_build.build_case(
            validation_report, case["spec"], case_dir / "build"
        )
        if build.get("status") != "passed" or build.get("action_order") != list(ACTION_NAMES):
            raise ContractionExperimentError(f"native build or correctness failed for {case['case_id']}")
        if build.get("stock_ijk_code_identity_equal") is not True:
            raise ContractionExperimentError(f"stock and canonical ijk differ in {case['case_id']}")
        if any(
            row.get("status") != "passed" or not isinstance(row.get("code_identity"), str)
            for row in build["actions"].values()
        ):
            raise ContractionExperimentError(f"invalid compiled action in {case['case_id']}")
        if not _artifact_hashes_match(build):
            raise ContractionExperimentError(f"build artifact changed for {case['case_id']}")
        built.append(
            {
                **case,
                "source_features": feature_source["features"],
                "feature_kernel_sha256": _sha256(feature_source["kernel"]),
                "feature_driver_sha256": _sha256(feature_source["driver"]),
                "build_report_path": build["report_path"],
                "build_report_sha256": _sha256(Path(build["report_path"])),
                "build": build,
            }
        )
    return built


def _validate_builds(
    design: dict[str, Any], validation_report: Path, output: Path, recorded: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = _cases(design)
    if len(recorded) != len(expected):
        raise ContractionExperimentError("build manifest is missing cases")
    refreshed = []
    for want, have in zip(expected, recorded, strict=True):
        if any(have.get(key) != want[key] for key in ("partition", "family", "spec", "case_id")):
            raise ContractionExperimentError("build manifest case identity changed")
        case_dir = output / "cases" / have["partition"] / have["case_id"]
        generated = contraction_corpus.generate_case(
            case_dir / "source-features", have["spec"], order="ijk"
        )
        if _sha256(generated["kernel"]) != have.get("feature_kernel_sha256") or _sha256(
            generated["driver"]
        ) != have.get("feature_driver_sha256"):
            raise ContractionExperimentError(f"corpus sources changed for {have['case_id']}")
        build = contraction_build.build_case(validation_report, have["spec"], case_dir / "build")
        report_path = Path(build["report_path"])
        if (
            build.get("status") != "passed"
            or build.get("action_order") != list(ACTION_NAMES)
            or _sha256(report_path) != have.get("build_report_sha256")
            or build.get("stock_ijk_code_identity_equal") is not True
            or not _artifact_hashes_match(build)
        ):
            raise ContractionExperimentError(f"cached build evidence changed for {have['case_id']}")
        refreshed.append({**have, "build": build})
    return refreshed


def _identity_actions(case: dict[str, Any]) -> tuple[list[tuple[str, Path]], dict[str, str]]:
    rows = case["build"]["actions"]
    by_identity: dict[str, list[str]] = {}
    for name in ACTION_NAMES:
        identity = rows[name]["code_identity"]
        by_identity.setdefault(identity, []).append(name)
    stock_identity = rows["stock"]["code_identity"]
    executable_rows = []
    aliases: dict[str, str] = {}
    for identity, names in by_identity.items():
        label = "stock" if identity == stock_identity else next(name for name in names if name != "stock")
        action = rows[label]
        executable_rows.append((label, Path(action["artifacts"]["executable"]["path"])))
        for name in names:
            aliases[name] = label
    if aliases.get("ijk") != "stock" or "stock" not in {name for name, _ in executable_rows}:
        raise ContractionExperimentError("canonical ijk baseline was lost during code deduplication")
    return executable_rows, aliases


def _pilot_iterations(case: dict[str, Any], design: dict[str, Any], case_dir: Path) -> tuple[int, dict[str, Any]]:
    executables, aliases = _identity_actions(case)
    hashes = {name: _sha256(path) for name, path in executables}
    identity = {
        "case_fingerprint": case["build"]["case_fingerprint"],
        "target_sample_ms": design["target_sample_ms"],
        "pilot_iterations": 32,
        "action_aliases": aliases,
        "executables": hashes,
    }
    path = case_dir / "timings" / "pilot.json"
    if path.exists():
        cached = _load(path)
        if cached.get("identity") != identity or cached.get("identity_sha256") != _fingerprint(identity):
            raise ContractionExperimentError(f"timing pilot identity changed for {case['case_id']}")
        raw = cached.get("pilots")
        if not isinstance(raw, dict) or set(raw) != set(hashes):
            raise ContractionExperimentError(f"timing pilot cache is malformed for {case['case_id']}")
    else:
        raw: dict[str, Any] = {}
        checksum = None
        for name, executable in executables:
            result = benchmark._run_one(name, executable, 32, "iteration calibration")
            if checksum is None:
                checksum = result["checksum"]
            elif result["checksum"] != checksum:
                raise ContractionExperimentError(f"pilot checksum mismatch in {case['case_id']}")
            raw[name] = {
                "elapsed_ns": result["elapsed_ns"],
                "checksum": result["checksum"],
            }
        _save(path, {"schema_version": 1, "identity": identity, "identity_sha256": _fingerprint(identity), "pilots": raw})
    if any(
        not isinstance(item, dict)
        or type(item.get("elapsed_ns")) is not int
        or item["elapsed_ns"] <= 0
        or not isinstance(item.get("checksum"), str)
        for item in raw.values()
    ) or len({item["checksum"] for item in raw.values()}) != 1:
        raise ContractionExperimentError(f"timing pilot values are invalid for {case['case_id']}")
    fastest = min(item["elapsed_ns"] for item in raw.values())
    slowest = max(item["elapsed_ns"] for item in raw.values())
    numerator = 32 * design["target_sample_ms"] * 1_000_000
    iterations = max(1, (numerator + fastest - 1) // fastest)
    if iterations * slowest / 32 > 20_000_000_000:
        raise ContractionExperimentError(f"predicted sample exceeds 20 seconds for {case['case_id']}")
    calibration = {
        "identity": identity,
        "iterations": iterations,
        "pilot_evidence": raw,
        "predicted_max_sample_ns": math.ceil(iterations * slowest / 32),
        "pilot_cache": str(path),
        "pilot_cache_sha256": _sha256(path),
    }
    calibration_path = case_dir / "timing-calibration.json"
    if calibration_path.exists() and _load(calibration_path) != calibration:
        raise ContractionExperimentError(f"frozen timing iterations changed for {case['case_id']}")
    if not calibration_path.exists():
        _save(calibration_path, calibration)
    return iterations, calibration


def _measure_cases(
    cases: list[dict[str, Any]], design: dict[str, Any], output: Path, *, blocks: tuple[int, ...]
) -> list[dict[str, Any]]:
    measured = []
    for case in cases:
        case_dir = output / "cases" / case["partition"] / case["case_id"]
        executables, aliases = _identity_actions(case)
        iterations, calibration = _pilot_iterations(case, design, case_dir)
        records = []
        summaries_by_block = {}
        seeds = {}
        cache_refs = []
        for block in blocks:
            if block not in (1, 2) or (block == 2 and case["partition"] != "discovery"):
                raise ContractionExperimentError("only discovery cases may use block two")
            material = f"{design['seed']}:{case['case_id']}:{block}".encode()
            seed = int.from_bytes(hashlib.sha256(material).digest()[:4], "big")
            seeds[str(block)] = seed
            identity = {
                "partition": case["partition"],
                "case_id": case["case_id"],
                "block": block,
                "seed": seed,
                "rounds": design["benchmark_rounds"],
                "iterations": iterations,
                "action_aliases": aliases,
                "executables": {name: _sha256(path) for name, path in executables},
                "build_fingerprint": case["build"]["case_fingerprint"],
                "pilot_cache_sha256": calibration["pilot_cache_sha256"],
            }
            cache_path = case_dir / "timings" / f"block-{block}.json"
            if cache_path.exists():
                cached = _load(cache_path)
                if cached.get("identity") != identity or cached.get("identity_sha256") != _fingerprint(identity):
                    raise ContractionExperimentError(f"timing cache identity changed for {case['case_id']} block {block}")
                raw = cached.get("benchmark")
            else:
                raw = benchmark.run_benchmark(
                    executables, iterations=iterations, rounds=design["benchmark_rounds"], seed=seed
                )
                _save(cache_path, {"schema_version": 1, "identity": identity, "identity_sha256": _fingerprint(identity), "benchmark": raw})
            if not isinstance(raw, dict) or raw.get("status") != "measured":
                raise ContractionExperimentError(f"timing cache is invalid for {case['case_id']}")
            timed_names = {name for name, _path in executables}
            round_records = raw.get("round_records")
            if (
                raw.get("iterations") != iterations
                or raw.get("rounds") != design["benchmark_rounds"]
                or not isinstance(round_records, list)
                or len(round_records) != design["benchmark_rounds"]
                or not isinstance(raw.get("summaries"), dict)
                or set(raw["summaries"]) != timed_names
                or any(
                    not isinstance(row, dict)
                    or not isinstance(row.get("measurements"), dict)
                    or set(row["measurements"]) != timed_names
                    or any(
                        not isinstance(measurement, dict)
                        or type(measurement.get("elapsed_ns")) is not int
                        or measurement["elapsed_ns"] <= 0
                        for measurement in row["measurements"].values()
                    )
                    for row in round_records
                )
            ):
                raise ContractionExperimentError(f"timing samples are malformed for {case['case_id']}")
            cache_refs.append({"path": str(cache_path), "sha256": _sha256(cache_path), "identity_sha256": _fingerprint(identity)})
            summaries_by_block[str(block)] = raw["summaries"]
            for row in raw["round_records"]:
                records.append(
                    {
                        "round": (block - 1) * design["benchmark_rounds"] + row["round"],
                        "block": block,
                        "block_round": row["round"],
                        "canonical_order": row["order"],
                        "measurements": {name: row["measurements"][aliases[name]] for name in ACTION_NAMES},
                    }
                )
        medians = {
            name: float(statistics.median(row["measurements"][name]["elapsed_ns"] for row in records))
            for name in ACTION_NAMES
        }
        measured.append(
            {
                **{key: case[key] for key in ("partition", "family", "spec", "case_id")},
                "source_features": case["source_features"],
                "iterations": iterations,
                "timing_calibration": calibration,
                "code_aliases": aliases,
                "distinct_code_identities": len(set(case["build"]["actions"][name]["code_identity"] for name in ORDERS)),
                "timing_caches": cache_refs,
                "benchmark": {
                    "block_seeds": seeds,
                    "rounds_per_block": design["benchmark_rounds"],
                    "round_records": records,
                    "median_ns": medians,
                    "canonical_summaries_by_block": summaries_by_block,
                },
            }
        )
    return measured


def _merge_blocks(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return loop_experiment._merge_measurement_blocks(first, second)


def _block_median(case: dict[str, Any], action: str, block: int) -> float:
    values = [
        row["measurements"][action]["elapsed_ns"]
        for row in case["benchmark"]["round_records"]
        if row["block"] == block
    ]
    if not values:
        raise ContractionExperimentError(f"missing {action} timing block {block} for {case['case_id']}")
    return float(statistics.median(values))


def stride_choice(source_features: dict[str, int]) -> str:
    """Choose by the frozen byte-stride cost, with stable action-order ties."""
    strides = {
        "a": {"i": source_features.get("a_i_stride", 0) * 4, "j": 0, "k": source_features.get("a_k_stride", 0) * 4},
        "b": {"i": 0, "j": source_features.get("b_j_stride", 0) * 4, "k": source_features.get("b_k_stride", 0) * 4},
        "c": {"i": source_features.get("c_i_stride", 0) * 4, "j": source_features.get("c_j_stride", 0) * 4, "k": 0},
    }
    costs = {
        axis: strides["a"][axis] + strides["b"][axis] + 2 * strides["c"][axis]
        for axis in "ijk"
    }
    return min(ORDERS, key=lambda order: (costs[order[-1]], costs[order[-2]], ORDERS.index(order)))


def _fit_discovery(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_action: dict[str, list[tuple[str, float]]] = {action: [] for action in ORDERS}
    for case in cases:
        baseline = _block_median(case, "stock", 1)
        for action in ORDERS:
            by_action[action].append((case["family"], _block_median(case, action, 1) / baseline))
    action_scores = {}
    for action, rows in by_action.items():
        family_logs: dict[str, list[float]] = {}
        for family, ratio in rows:
            family_logs.setdefault(family, []).append(math.log(ratio))
        action_scores[action] = math.exp(
            sum(sum(values) / len(values) for values in family_logs.values()) / len(family_logs)
        )
    constant = min(ORDERS, key=lambda action: (action_scores[action], ORDERS.index(action)))
    oracle = {
        case["case_id"]: min(ORDERS, key=lambda action: (_block_median(case, action, 1), ORDERS.index(action)))
        for case in cases
    }
    return {
        "best_constant": constant,
        "first_block_gm_ratio_to_ijk": action_scores,
        "case_oracle_actions": oracle,
        "stride_actions": {case["case_id"]: stride_choice(case["source_features"]) for case in cases},
    }


def _comparison(cases: list[dict[str, Any]], choices: dict[str, dict[str, str]], candidate: str, reference: str, block: int) -> dict[str, Any]:
    reports = []
    for case in cases:
        candidate_action = choices[case["case_id"]][candidate]
        reference_action = choices[case["case_id"]][reference]
        reports.append(
            {
                "family": case["family"],
                "state": case["case_id"],
                "round_records": [
                    {
                        "round": row["round"],
                        "measurements": {
                            "reference": row["measurements"][reference_action],
                            "candidate": row["measurements"][candidate_action],
                        },
                    }
                    for row in case["benchmark"]["round_records"]
                    if row["block"] == block
                ],
            }
        )
    return quality_analysis.paired_metrics(reports, reference="reference", candidate="candidate")


def discovery_gate(
    cases: list[dict[str, Any]], design: dict[str, Any], *, frozen_fit: dict[str, Any] | None = None
) -> dict[str, Any]:
    discovery = [case for case in cases if case["partition"] == "discovery"]
    if not discovery:
        raise ContractionExperimentError("discovery gate requires discovery cases")
    fit = frozen_fit or _fit_discovery(discovery)
    choices: dict[str, dict[str, str]] = {}
    for case in discovery:
        case_id = case["case_id"]
        choices[case_id] = {
            "oracle": fit["case_oracle_actions"][case_id],
            "constant": fit["best_constant"],
            "stride": fit["stride_actions"][case_id],
        }
    metrics = {
        baseline: _comparison(discovery, choices, "oracle", baseline, block=2)
        for baseline in ("constant", "stride")
    }
    noise = {
        baseline: max(
            math.log1p(design["noise_floor_fraction"]),
            3 * metrics[baseline]["maximum_state_mad_log_ratio"],
        )
        for baseline in metrics
    }
    requirements = {}
    family_positive = {}
    for baseline in ("constant", "stride"):
        key = baseline
        comparison = metrics[baseline]
        requirements[f"oracle_five_percent_better_than_{key}"] = comparison["geometric_mean_ratio"] <= 1 - design["discovery_gap_fraction"]
        requirements[f"oracle_beyond_noise_vs_{key}"] = math.log(comparison["geometric_mean_ratio"]) < -noise[key]
        positive = {group: result["geometric_mean_ratio"] < 1 for group, result in comparison["families"].items()}
        family_positive[key] = positive
        requirements[f"positive_in_two_groups_vs_{key}"] = sum(positive.values()) >= 2
    distinct = sum(case.get("distinct_code_identities", 0) > 1 for case in discovery)
    requirements["distinct_code_in_half_cases"] = distinct >= math.ceil(len(discovery) / 2)
    return {
        "passed": all(requirements.values()),
        "best_constant": fit["best_constant"],
        "first_block_gm_ratio_to_ijk": fit["first_block_gm_ratio_to_ijk"],
        "case_oracle_actions": fit["case_oracle_actions"],
        "stride_actions": fit["stride_actions"],
        "confirmation_block": 2,
        "confirmation_metrics_oracle_vs_constant_and_stride": metrics,
        "noise_floor_log_ratio": noise,
        "noise_floor_fraction": {key: math.expm1(value) for key, value in noise.items()},
        "distinct_code_cases": distinct,
        "discovery_case_count": len(discovery),
        "family_positive_direction": family_positive,
        "requirements": requirements,
    }


def _source_model_features(case: dict[str, Any]) -> dict[str, int]:
    source = case["source_features"]
    values = {
        "m": source["m"],
        "n": source["n"],
        "k": source["k"],
        "trip_count": source["trip_count"],
        "element_bits": source["element_bits"],
        "arithmetic_bits": source["arithmetic_bits"],
        **TARGET_FEATURES,
    }
    for array, axes in (("a", "ik"), ("b", "kj"), ("c", "ij")):
        for axis in axes:
            values[f"{array}_{axis}_stride_bytes"] = source.get(f"{array}_{axis}_stride", 0) * 4
    return values


def _action_features(case: dict[str, Any], order: str) -> dict[str, int]:
    source = _source_model_features(case)
    strides = {
        array: {axis: source.get(f"{array}_{axis}_stride_bytes", 0) for axis in "ijk"}
        for array in ("a", "b", "c")
    }
    result = dict(source)
    for position, axis in zip(("outer", "middle", "inner"), order, strict=True):
        result[f"{position}_axis_id"] = AXIS_IDS[axis]
        for array in ("a", "b", "c"):
            result[f"{position}_{array}_stride_bytes"] = strides[array][axis]
        result[f"{position}_stride_cost_bytes"] = (
            strides["a"][axis] + strides["b"][axis] + 2 * strides["c"][axis]
        )
    return result


def _provider_packet(case: dict[str, Any]) -> dict[str, Any]:
    rows = [_action_features(case, order) for order in ORDERS]
    names = sorted(rows[0])
    return {
        "task": (
            "Choose a loop traversal order for the exact operation C_ij = sum_k "
            "((A_ik XOR uint32(salt)) * B_kj) modulo 2^32. C is initialized to zero; "
            "A, B, and C are disjoint uint32 buffers. A zero byte stride means that array "
            "does not depend on that axis. The target is Apple M3 with a 128-bit NEON vector "
            "unit. Minimize native runtime while preserving the result. Numeric axes are "
            "i=0, j=1, k=2; candidate fields encode outermost, middle, and innermost traversal. "
            "Use only these fixed target and source facts."
        ),
        "features": {name: [row[name] for row in rows] for name in names},
        "feature_descriptions": {name: FEATURE_DESCRIPTIONS[name] for name in names},
        "actions": [
            {"id": str(index), "description": f"Traverse numeric axis IDs outer={AXIS_IDS[order[0]]}, middle={AXIS_IDS[order[1]]}, inner={AXIS_IDS[order[2]]}"}
            for index, order in enumerate(ORDERS)
        ],
    }


def _nn_choice(model: dict[str, Any], case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    packet = _provider_packet(case)
    scored = quality_analysis.score_actions(model, packet["features"], packet["actions"])
    return ORDERS[int(scored["action_id"])], scored


def _fit_nn(discovery: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case in discovery:
        baseline = _block_median(case, "stock", 1)
        for action in ORDERS:
            rows.append(
                {
                    "features": _action_features(case, action),
                    "loss": _block_median(case, action, 1) / baseline,
                }
            )
    return quality_analysis.fit_nearest_neighbor(rows)


def _provider_choices(case: dict[str, Any], cache_path: Path) -> dict[str, Any]:
    packet = _provider_packet(case)
    entry = loop_experiment.query_provider_cache(packet, cache_path)
    return {
        "packet_sha256": entry["packet_sha256"],
        "responses": entry["responses"],
        "attempts": entry["attempts"],
        "jev_action": ORDERS[int(entry["responses"]["jev"]["chosen_action"])],
        "codex_action": ORDERS[int(entry["responses"]["codex"]["chosen_action"])],
    }


def _policy_comparisons(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, str]], block: int
) -> dict[str, Any]:
    policies = list(next(iter(choices.values())))
    return {
        f"{candidate}_vs_{reference}": _comparison(cases, choices, candidate, reference, block)
        for candidate in policies
        for reference in policies
        if candidate != reference
    }


def _success_gate(
    comparisons: dict[str, Any], design: dict[str, Any],
    calibration_route_fraction: float, test_route_fraction: float,
) -> dict[str, Any]:
    baselines = ("best_constant", "stride_selector", "nearest_neighbor", "jev")
    requirements: dict[str, bool] = {}
    floors = {}
    group_positive = {}
    for baseline in baselines:
        result = comparisons[f"cascade_vs_{baseline}"]
        noise = max(
            math.log1p(design["noise_floor_fraction"]),
            3 * result["maximum_state_mad_log_ratio"],
        )
        floors[baseline] = {"log_ratio": noise, "fraction": math.expm1(noise)}
        requirements[f"cascade_beyond_noise_vs_{baseline}"] = math.log(result["geometric_mean_ratio"]) < -noise
        group_positive[baseline] = {
            group: metric["geometric_mean_ratio"] < 1
            for group, metric in result["families"].items()
        }
        requirements[f"cascade_positive_in_two_groups_vs_{baseline}"] = sum(group_positive[baseline].values()) >= 2
    requirements["calibration_escalation_within_budget"] = calibration_route_fraction <= design["calibration_escalation_budget"]
    requirements["test_escalation_within_budget"] = test_route_fraction <= design["calibration_escalation_budget"]
    return {
        "passed": all(requirements.values()),
        "requirements": requirements,
        "noise_floors": floors,
        "group_positive_direction": group_positive,
        "calibration_escalation_fraction": calibration_route_fraction,
        "test_escalation_fraction": test_route_fraction,
    }


def _verify_cache_refs(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        calibration = case.get("timing_calibration", {})
        pilot_path = Path(calibration.get("pilot_cache", ""))
        if (
            not pilot_path.is_file()
            or _sha256(pilot_path) != calibration.get("pilot_cache_sha256")
            or _load(pilot_path).get("pilots") != calibration.get("pilot_evidence")
        ):
            raise ContractionExperimentError(f"timing pilot cache changed for {case['case_id']}")
        calibration_path = pilot_path.parent.parent / "timing-calibration.json"
        if not calibration_path.is_file() or _load(calibration_path) != calibration:
            raise ContractionExperimentError(f"timing calibration changed for {case['case_id']}")
        for reference in case.get("timing_caches", []):
            path = Path(reference["path"])
            if not path.is_file() or _sha256(path) != reference.get("sha256"):
                raise ContractionExperimentError(f"timing cache changed or missing: {path}")
            cached = _load(path)
            if cached.get("identity_sha256") != reference.get("identity_sha256"):
                raise ContractionExperimentError(f"timing cache identity changed: {path}")


def run_discovery(
    design_path: Path, validation_path: Path, output: Path
) -> dict[str, Any]:
    design = _design(design_path)
    provenance = _provenance(design_path, validation_path)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "builds.json"
    if manifest_path.exists():
        manifest = _load(manifest_path)
        if manifest.get("provenance") != provenance:
            raise ContractionExperimentError("build manifest provenance changed")
        built = _validate_builds(design, validation_path, output, manifest.get("cases", []))
    else:
        built = _build_all(design, validation_path, output)
        _save(manifest_path, {"schema_version": 1, "provenance": provenance, "cases": built})
    # All discovery, calibration and test cases are built before this first timing.
    discovery_built = [case for case in built if case["partition"] == "discovery"]
    first_path = output / "discovery-block-1.json"
    if first_path.exists():
        first_file = _load(first_path)
        if first_file.get("provenance") != provenance:
            raise ContractionExperimentError("first-block provenance changed")
        first = first_file.get("cases", [])
        _verify_cache_refs(first)
    else:
        first = _measure_cases(discovery_built, design, output, blocks=(1,))
        _save(first_path, {"schema_version": 1, "provenance": provenance, "cases": first})
    fit_path = output / "discovery-fit.json"
    first_sha = _sha256(first_path)
    fit = _fit_discovery(first)
    if fit_path.exists():
        checkpoint = _load(fit_path)
        if checkpoint.get("provenance") != provenance or checkpoint.get("block_one_sha256") != first_sha or checkpoint.get("fit") != fit:
            raise ContractionExperimentError("frozen first-block fit changed")
    else:
        _save(fit_path, {"schema_version": 1, "provenance": provenance, "block_one_sha256": first_sha, "fit": fit})
    second_path = output / "discovery-block-2.json"
    if second_path.exists():
        second_file = _load(second_path)
        if second_file.get("provenance") != provenance:
            raise ContractionExperimentError("confirmation-block provenance changed")
        second = second_file.get("cases", [])
        _verify_cache_refs(second)
    else:
        second = _measure_cases(discovery_built, design, output, blocks=(2,))
        _save(second_path, {"schema_version": 1, "provenance": provenance, "cases": second})
    complete = _merge_blocks(first, second)
    gate = discovery_gate(complete, design, frozen_fit=fit)
    report = {
        "schema_version": 1,
        "status": "passed" if gate["passed"] else "stopped_no_confirmed_gap",
        "provenance": provenance,
        "design": str(design_path.resolve()),
        "validation_report": str(validation_path.resolve()),
        "build_manifest": {"path": str(manifest_path), "sha256": _sha256(manifest_path)},
        "first_block": {"path": str(first_path), "sha256": first_sha},
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
    if report.get("provenance") != provenance or report.get("status") != "passed" or not report.get("discovery_gate", {}).get("passed"):
        raise ContractionExperimentError("models require a matching passed discovery report")
    for key in ("build_manifest", "first_block", "fit_checkpoint", "confirmation_block"):
        reference = report.get(key, {})
        path = Path(reference.get("path", ""))
        if not path.is_file() or _sha256(path) != reference.get("sha256"):
            raise ContractionExperimentError(f"frozen discovery evidence changed: {key}")
    manifest = _load(Path(report["build_manifest"]["path"]))
    if manifest.get("provenance") != provenance:
        raise ContractionExperimentError("build manifest provenance changed")
    built = _validate_builds(design, validation_path, output, manifest.get("cases", []))
    checkpoint = _load(Path(report["fit_checkpoint"]["path"]))
    fit = checkpoint.get("fit")
    if not isinstance(fit, dict):
        raise ContractionExperimentError("frozen discovery selection is malformed")
    complete = report.get("discovery_cases", [])
    _verify_cache_refs(complete)
    if discovery_gate(complete, design, frozen_fit=fit) != report.get("discovery_gate"):
        raise ContractionExperimentError("discovery gate does not match frozen evidence")
    return report, design, {"provenance": provenance, "built": built, "fit": fit}


def run_models(
    design_path: Path,
    validation_path: Path,
    output: Path,
    discovery_report_path: Path | None = None,
) -> dict[str, Any]:
    discovery_path = discovery_report_path or output / "discovery.json"
    discovery, design, context = _validate_discovery_report(
        discovery_path, design_path, validation_path, output
    )
    provenance, fit = context["provenance"], context["fit"]
    built = context["built"]
    discovery_cases = discovery["discovery_cases"]
    nn_model = _fit_nn(discovery_cases)
    model_choices_path = output / "model-choices.json"
    existing = _load(model_choices_path) if model_choices_path.exists() else None
    choices: dict[str, dict[str, Any]] = {}
    packets: dict[str, Any] = {}
    provider_cache = output / "provider-cache.json"
    for case in built:
        if case["partition"] not in ("calibrate", "test"):
            continue
        nn_action, nn_scores = _nn_choice(nn_model, case)
        response = _provider_choices(case, provider_cache)
        packet_sha = response["packet_sha256"]
        packets[case["case_id"]] = packet_sha
        choices[case["case_id"]] = {
            "best_constant": fit["best_constant"],
            "stride_selector": stride_choice(case["source_features"]),
            "nearest_neighbor": nn_action,
            "nn_predictions": nn_scores,
            "jev": response["jev_action"],
            "codex": response["codex_action"],
            "jev_confidence": response["responses"]["jev"]["confidence"],
            "provider_responses": response["responses"],
            "provider_attempts": response["attempts"],
        }
    choices_evidence = {
        "schema_version": 1,
        "provenance": provenance,
        "discovery_sha256": _sha256(discovery_path),
        "nearest_neighbor_model": nn_model,
        "packet_sha256_by_case": packets,
        "choices": choices,
    }
    if existing is not None and existing != choices_evidence:
        raise ContractionExperimentError("cached model choices differ from current frozen inputs")
    if existing is None:
        _save(model_choices_path, choices_evidence)
    # No calibration or held-out workload is benchmarked before every blind choice is frozen.
    cal_cases = [case for case in built if case["partition"] == "calibrate"]
    test_cases = [case for case in built if case["partition"] == "test"]
    cal_measured = _measure_cases(cal_cases, design, output, blocks=(1,))
    test_measured = _measure_cases(test_cases, design, output, blocks=(1,))
    cal_rows = []
    for case in cal_measured:
        choice = choices[case["case_id"]]
        jev_ns = _block_median(case, choice["jev"], 1)
        codex_ns = _block_median(case, choice["codex"], 1)
        cal_rows.append({"confidence": choice["jev_confidence"], "jev_ns": jev_ns, "codex_ns": codex_ns})
    routing = quality_analysis.calibrate_threshold(
        cal_rows, max_escalation_fraction=design["calibration_escalation_budget"]
    )
    route_rule = routing["chosen"]
    routed_choices: dict[str, dict[str, str]] = {}
    calibration_escalations = 0
    test_escalations = 0
    for case in [*cal_measured, *test_measured]:
        case_id = case["case_id"]
        choice = choices[case_id]
        escalate = quality_analysis.select_route(route_rule, choice["jev_confidence"])
        if case["partition"] == "calibrate":
            calibration_escalations += int(escalate)
        else:
            test_escalations += int(escalate)
        routed_choices[case_id] = {
            **{name: choice[name] for name in ("best_constant", "stride_selector", "nearest_neighbor", "jev", "codex")},
            "canonical_ijk": "ijk",
            "cascade": choice["codex"] if escalate else choice["jev"],
        }
    cal_fraction = calibration_escalations / len(cal_measured)
    test_fraction = test_escalations / len(test_measured)
    cal_comparisons = _policy_comparisons(cal_measured, routed_choices, block=1)
    test_comparisons = _policy_comparisons(test_measured, routed_choices, block=1)
    success = _success_gate(test_comparisons, design, cal_fraction, test_fraction)
    model_report = {
        "schema_version": 1,
        "status": "passed" if success["passed"] else "failed_model_success_gate",
        "provenance": provenance,
        "discovery_sha256": _sha256(discovery_path),
        "model_choices": {"path": str(model_choices_path), "sha256": _sha256(model_choices_path)},
        "provider_cache": {"path": str(provider_cache), "sha256": _sha256(provider_cache)},
        "nearest_neighbor_model": nn_model,
        "routing": routing,
        "policy_actions": routed_choices,
        "calibration_cases": cal_measured,
        "test_cases": test_measured,
        "calibration_comparisons": cal_comparisons,
        "test_comparisons": test_comparisons,
        "success_gate": success,
        "model_call_case_count": len(choices),
    }
    _save(output / "models.json", model_report)
    return model_report


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
            report = run_discovery(args.design, args.validation_report, args.output)
        else:
            report = run_models(args.design, args.validation_report, args.output, args.discovery_report)
    except (ContractionExperimentError, OSError, ValueError, benchmark.BenchmarkError) as error:
        print(f"contraction experiment failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
