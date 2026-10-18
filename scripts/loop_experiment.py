"""Run the frozen native loop-policy discovery and escalation experiment."""

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
        counterfactual,
        experiment,
        jev,
        loop_build,
        loop_corpus,
        quality_analysis,
        quality_experiment,
        validate,
    )
except ImportError:  # Running as `python scripts/loop_experiment.py`.
    import benchmark
    import codex_advisor
    import counterfactual
    import experiment
    import jev
    import loop_build
    import loop_corpus
    import quality_analysis
    import quality_experiment
    import validate


SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "loop-policy-v1.json"
PARTITIONS = ("discovery", "calibrate", "test")
DISCOVERY_BLOCK_COUNT = 2
TARGET_FACTS = {
    "target_cpu": "apple-m3",
    "vector_isa": "NEON",
    "vector_register_bits": 128,
    "sve": 0,
    "architecture_bits": 64,
}
TARGET_NUMERIC_FEATURES = {
    "vector_register_bits": 128,
    "sve": 0,
    "architecture_bits": 64,
}
FEATURE_DESCRIPTIONS = {
    "element_bits": "bits in each source array element",
    "arithmetic_bits": "bits used by the loop arithmetic",
    "trip_count": "compile-time loop trip count",
    "loads": "source-level loads per loop iteration",
    "stores": "source-level stores per loop iteration",
    "multiplies": "source-level multiplies per loop iteration",
    "shifts": "source-level shifts per loop iteration",
    "branches": "conditional branches in the loop body",
    "reduction": "one when the loop has a scalar reduction",
    "stride": "source array access stride",
    "vectorize_width": "requested LLVM loop vectorization width; zero means stock policy",
    "interleave_count": "requested LLVM loop interleave count; zero means stock policy",
    **{
        name: f"fixed target fact: {value}"
        for name, value in TARGET_NUMERIC_FEATURES.items()
    },
}
ACTION_BY_NAME = {
    name: (name, width, interleave)
    for name, width, interleave in loop_build.DEFAULT_ACTIONS
}


class LoopExperimentError(RuntimeError):
    """A frozen loop experiment cannot safely proceed or resume."""


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
        raise LoopExperimentError(f"cannot read JSON report {path}: {error}") from error


def _design(path: Path) -> dict[str, Any]:
    design = _load(path)
    if not isinstance(design, dict) or design.get("schema_version") != 1:
        raise LoopExperimentError("unsupported or malformed loop-policy design")
    families = design.get("families")
    if not isinstance(families, dict) or any(
        not isinstance(families.get(partition), list) or not families[partition]
        for partition in PARTITIONS
    ):
        raise LoopExperimentError(
            "design must define non-empty discovery/calibrate/test families"
        )
    flattened = [family for partition in PARTITIONS for family in families[partition]]
    if len(flattened) != len(set(flattened)):
        raise LoopExperimentError("family partitions must be disjoint")
    trip_counts = design.get("trip_counts")
    if (
        not isinstance(trip_counts, list)
        or not trip_counts
        or any(
            type(count) is not int
            or not loop_corpus.MIN_N <= count <= loop_corpus.MAX_N
            for count in trip_counts
        )
    ):
        raise LoopExperimentError("design trip_counts are invalid")
    if design.get("actions") != list(ACTION_BY_NAME):
        raise LoopExperimentError(
            "design action menu does not match the implemented action menu"
        )
    if (
        type(design.get("benchmark_rounds")) is not int
        or design["benchmark_rounds"] <= 0
        or type(design.get("target_sample_ms")) is not int
        or design["target_sample_ms"] <= 0
        or type(design.get("seed")) is not int
    ):
        raise LoopExperimentError("design benchmark settings are invalid")
    if design.get("target_cpu") != loop_build.TARGET_CPU:
        raise LoopExperimentError("design target CPU differs from the loop builder")
    if design.get("llvm_revision") is None:
        raise LoopExperimentError("design must pin the LLVM revision")
    for key in (
        "discovery_gap_fraction",
        "noise_floor_fraction",
        "calibration_escalation_budget",
    ):
        value = design.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise LoopExperimentError(f"design {key} must be finite")
    if (
        not 0 < design["discovery_gap_fraction"] < 1
        or not 0 <= design["noise_floor_fraction"] < 1
    ):
        raise LoopExperimentError("design discovery thresholds are invalid")
    if not 0 <= design["calibration_escalation_budget"] <= 1:
        raise LoopExperimentError("design calibration budget is invalid")
    return design


def _implementation_hashes() -> dict[str, str]:
    paths = (
        Path(__file__),
        Path(loop_build.__file__),
        Path(loop_corpus.__file__),
        Path(benchmark.__file__),
        Path(quality_analysis.__file__),
        Path(quality_experiment.__file__),
        Path(experiment.__file__),
        Path(jev.__file__),
        Path(codex_advisor.__file__),
        Path(counterfactual.__file__),
        Path(validate.__file__),
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


def _cases(
    design: dict[str, Any], partitions: tuple[str, ...] = PARTITIONS
) -> list[dict[str, Any]]:
    cases = []
    for partition in partitions:
        for family in design["families"][partition]:
            for trip_count in design["trip_counts"]:
                cases.append(
                    {
                        "partition": partition,
                        "family": family,
                        "trip_count": trip_count,
                        "case_id": f"{family}-n{trip_count}",
                    }
                )
    return cases


def _build_all(
    design: dict[str, Any], validation_report: Path, output: Path
) -> list[dict[str, Any]]:
    """Generate and build every partition before any case is timed or modeled."""
    built: list[dict[str, Any]] = []
    for case in _cases(design):
        case_dir = output / "cases" / case["partition"] / case["case_id"]
        sources_dir = case_dir / "source"
        generated = loop_corpus.generate_case(
            sources_dir, case["family"], case["trip_count"]
        )
        build = loop_build.build_case(
            validation_report,
            generated["kernel"],
            generated["driver"],
            case_dir / "build",
            actions=[ACTION_BY_NAME[name] for name in design["actions"]],
        )
        if build.get("status") != "passed":
            raise LoopExperimentError(
                f"native build or correctness failed for {case['case_id']}"
            )
        action_rows = build.get("actions")
        if (
            not isinstance(action_rows, dict)
            or set(action_rows) != set(design["actions"])
            or build.get("action_order") != design["actions"]
        ):
            raise LoopExperimentError(
                f"build action order changed for {case['case_id']}"
            )
        for name, row in action_rows.items():
            if row.get("status") != "passed" or not isinstance(
                row.get("code_identity"), str
            ):
                raise LoopExperimentError(
                    f"invalid compiled action {name} for {case['case_id']}"
                )
        built.append(
            {
                **case,
                "source_features": generated["features"],
                "kernel_sha256": _sha256(generated["kernel"]),
                "driver_sha256": _sha256(generated["driver"]),
                "build_report_path": build["report_path"],
                "build_report_sha256": _sha256(Path(build["report_path"])),
                "build": build,
            }
        )
    return built


def _validate_builds(
    design: dict[str, Any],
    validation_report: Path,
    output: Path,
    recorded: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected = _cases(design)
    if len(recorded) != len(expected):
        raise LoopExperimentError(
            "discovery report is missing one or more compiled cases"
        )
    refreshed = []
    for expected_case, stored in zip(expected, recorded, strict=True):
        if any(
            stored.get(key) != expected_case[key]
            for key in ("partition", "family", "trip_count", "case_id")
        ):
            raise LoopExperimentError("discovery build case order or identity changed")
        case_dir = output / "cases" / stored["partition"] / stored["case_id"]
        source_dir = case_dir / "source"
        generated = loop_corpus.generate_case(
            source_dir, stored["family"], stored["trip_count"]
        )
        if _sha256(generated["kernel"]) != stored.get("kernel_sha256") or _sha256(
            generated["driver"]
        ) != stored.get("driver_sha256"):
            raise LoopExperimentError(
                f"generated source changed for {stored['case_id']}"
            )
        build = loop_build.build_case(
            validation_report,
            generated["kernel"],
            generated["driver"],
            case_dir / "build",
            actions=[ACTION_BY_NAME[name] for name in design["actions"]],
        )
        report_path = Path(build["report_path"])
        if (
            build.get("status") != "passed"
            or _sha256(report_path) != stored.get("build_report_sha256")
            or build.get("action_order") != design["actions"]
        ):
            raise LoopExperimentError(
                f"cached build evidence changed for {stored['case_id']}"
            )
        refreshed.append({**stored, "build": build})
    return refreshed


def _identity_actions(
    case: dict[str, Any], action_names: list[str]
) -> tuple[list[tuple[str, Path]], dict[str, str]]:
    by_identity: dict[str, list[str]] = {}
    for name in action_names:
        identity = case["build"]["actions"][name]["code_identity"]
        by_identity.setdefault(identity, []).append(name)
    stock_identity = case["build"]["actions"]["stock"]["code_identity"]
    canonical: dict[str, str] = {}
    executables = []
    for identity, aliases in by_identity.items():
        label = "stock" if identity == stock_identity else aliases[0]
        canonical[label] = identity
        action = case["build"]["actions"][label]
        executables.append((label, Path(action["artifacts"]["executable"]["path"])))
        for alias in aliases:
            canonical[alias] = label
    if "stock" not in {name for name, _path in executables}:
        raise LoopExperimentError(
            "stock action was not retained during code deduplication"
        )
    return executables, canonical


def _measure_cases(
    cases: list[dict[str, Any]],
    design: dict[str, Any],
    output: Path,
    *,
    blocks: tuple[int, ...],
) -> list[dict[str, Any]]:
    measured = []
    for case in cases:
        case_dir = output / "cases" / case["partition"] / case["case_id"]
        stock_exe = Path(
            case["build"]["actions"]["stock"]["artifacts"]["executable"]["path"]
        )
        iterations = quality_experiment.case_iterations(
            case_dir, stock_exe, design["target_sample_ms"]
        )
        executables, aliases = _identity_actions(case, design["actions"])
        action_rounds = []
        block_summaries = {}
        block_seeds = {}
        timing_caches = []
        for block in blocks:
            if block not in (1, 2) or (block == 2 and case["partition"] != "discovery"):
                raise LoopExperimentError(
                    "only discovery cases may have a second benchmark block"
                )
            material = f"{design['seed']}:{case['case_id']}:{block}".encode()
            seed = int.from_bytes(hashlib.sha256(material).digest()[:4], "big")
            block_seeds[str(block)] = seed
            identity = {
                "phase": case["partition"],
                "case_id": case["case_id"],
                "block": block,
                "seed": seed,
                "rounds": design["benchmark_rounds"],
                "iterations": iterations,
                "target_sample_ms": design["target_sample_ms"],
                "action_aliases": aliases,
                "executables": {label: _sha256(path) for label, path in executables},
                "build_fingerprint": case["build"]["case_fingerprint"],
            }
            identity_sha256 = _packet_fingerprint(identity)
            cache_path = case_dir / "timings" / f"block-{block}.json"
            if cache_path.exists():
                cached = _load(cache_path)
                if (
                    cached.get("identity") != identity
                    or cached.get("identity_sha256") != identity_sha256
                    or not isinstance(cached.get("benchmark"), dict)
                ):
                    raise LoopExperimentError(
                        f"cached timing identity changed for {case['case_id']} block {block}"
                    )
                raw = cached["benchmark"]
            else:
                raw = benchmark.run_benchmark(
                    executables,
                    iterations=iterations,
                    rounds=design["benchmark_rounds"],
                    seed=seed,
                )
                _save(
                    cache_path,
                    {
                        "schema_version": 1,
                        "identity": identity,
                        "identity_sha256": identity_sha256,
                        "benchmark": raw,
                    },
                )
            timing_caches.append(
                {
                    "path": str(cache_path),
                    "sha256": _sha256(cache_path),
                    "identity_sha256": identity_sha256,
                }
            )
            block_summaries[str(block)] = raw["summaries"]
            for record in raw["round_records"]:
                mapped = {
                    name: record["measurements"][aliases[name]]
                    for name in design["actions"]
                }
                action_rounds.append(
                    {
                        "round": (block - 1) * design["benchmark_rounds"]
                        + record["round"],
                        "block": block,
                        "block_round": record["round"],
                        "canonical_order": record["order"],
                        "measurements": mapped,
                    }
                )
        action_medians = {
            name: float(
                statistics.median(
                    row["measurements"][name]["elapsed_ns"] for row in action_rounds
                )
            )
            for name in design["actions"]
        }
        measured.append(
            {
                **{
                    key: case[key]
                    for key in ("partition", "family", "trip_count", "case_id")
                },
                "source_features": case["source_features"],
                "iterations": iterations,
                "code_aliases": aliases,
                "distinct_code_identities": len(set(aliases.values())),
                "timing_caches": timing_caches,
                "benchmark": {
                    "block_seeds": block_seeds,
                    "rounds_per_block": design["benchmark_rounds"],
                    "round_records": action_rounds,
                    "median_ns": action_medians,
                    "canonical_summaries_by_block": block_summaries,
                },
            }
        )
    return measured


def _merge_measurement_blocks(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if [case["case_id"] for case in first] != [case["case_id"] for case in second]:
        raise LoopExperimentError(
            "discovery case order changed between independent blocks"
        )
    merged = []
    for first_case, second_case in zip(first, second, strict=True):
        first_benchmark = first_case["benchmark"]
        second_benchmark = second_case["benchmark"]
        combined_rows = sorted(
            first_benchmark["round_records"] + second_benchmark["round_records"],
            key=lambda row: row["round"],
        )
        medians = {
            name: float(
                statistics.median(
                    row["measurements"][name]["elapsed_ns"] for row in combined_rows
                )
            )
            for name in first_benchmark["median_ns"]
        }
        merged.append(
            {
                **first_case,
                "timing_caches": first_case["timing_caches"]
                + second_case["timing_caches"],
                "benchmark": {
                    "block_seeds": {
                        **first_benchmark["block_seeds"],
                        **second_benchmark["block_seeds"],
                    },
                    "rounds_per_block": first_benchmark["rounds_per_block"],
                    "round_records": combined_rows,
                    "median_ns": medians,
                    "canonical_summaries_by_block": {
                        **first_benchmark["canonical_summaries_by_block"],
                        **second_benchmark["canonical_summaries_by_block"],
                    },
                },
            }
        )
    return merged


def _verify_timing_caches(cases: list[dict[str, Any]]) -> None:
    for case in cases:
        for reference in case.get("timing_caches", []):
            path = Path(reference["path"])
            if not path.is_file() or _sha256(path) != reference.get("sha256"):
                raise LoopExperimentError(f"timing cache changed or is missing: {path}")
            cached = _load(path)
            if cached.get("identity_sha256") != reference.get("identity_sha256"):
                raise LoopExperimentError(f"timing cache identity changed: {path}")


def _frozen_discovery_fit(
    output: Path,
    discovery: dict[str, Any],
    design: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    fit_path = output / "discovery-fit.json"
    if not fit_path.is_file() or _sha256(fit_path) != discovery.get(
        "fit_checkpoint_sha256"
    ):
        raise LoopExperimentError(
            "frozen discovery selection checkpoint changed or is missing"
        )
    checkpoint = _load(fit_path)
    fit = checkpoint.get("fit")
    if checkpoint.get("provenance") != provenance or not isinstance(fit, dict):
        raise LoopExperimentError("frozen discovery selection provenance changed")
    gate = discovery_gate(discovery.get("discovery_cases", []), design, frozen_fit=fit)
    if gate != discovery.get("discovery_gate"):
        raise LoopExperimentError(
            "discovery gate does not match its frozen measurements and selection"
        )
    return fit


def _block_median(
    case: dict[str, Any],
    action: str,
    start: int,
    stop: int,
    *,
    block: int | None = None,
) -> float:
    values = [
        row["measurements"][action]["elapsed_ns"]
        for row in case["benchmark"]["round_records"]
        if start <= row["block_round"] <= stop
        and (block is None or row["block"] == block)
    ]
    if not values:
        raise LoopExperimentError(
            f"no timing samples for {case['case_id']} block {start}:{stop}"
        )
    return float(statistics.median(values))


def _balanced_ratio(
    cases: list[dict[str, Any]], action: str, start: int, stop: int, *, block: int
) -> float:
    family_logs: dict[str, list[float]] = {}
    for case in cases:
        ratio = _block_median(case, action, start, stop, block=block) / _block_median(
            case, "stock", start, stop, block=block
        )
        family_logs.setdefault(case["family"], []).append(math.log(ratio))
    return math.exp(
        sum(sum(values) / len(values) for values in family_logs.values())
        / len(family_logs)
    )


def _fit_discovery(
    cases: list[dict[str, Any]], action_names: list[str], block_stop: int
) -> dict[str, Any]:
    action_ratios = {
        action: _balanced_ratio(cases, action, 1, block_stop, block=1)
        for action in action_names
    }
    constant = min(
        action_names, key=lambda name: (action_ratios[name], action_names.index(name))
    )
    oracle = {}
    for case in cases:
        oracle[case["case_id"]] = min(
            action_names,
            key=lambda name: (
                _block_median(case, name, 1, block_stop, block=1),
                action_names.index(name),
            ),
        )
    return {
        "best_constant": constant,
        "first_block_gm_ratio_to_stock": action_ratios,
        "case_oracle_actions": oracle,
    }


def _paired_action_reports(
    cases: list[dict[str, Any]],
    constant: str,
    oracle: dict[str, str],
    start: int,
    stop: int,
) -> list[dict[str, Any]]:
    reports = []
    for case in cases:
        rounds = []
        for row in case["benchmark"]["round_records"]:
            if start <= row["round"] <= stop:
                rounds.append(
                    {
                        "round": row["round"],
                        "measurements": {
                            "constant": row["measurements"][constant],
                            "oracle": row["measurements"][oracle[case["case_id"]]],
                        },
                    }
                )
        reports.append(
            {
                "family": case["family"],
                "state": case["case_id"],
                "round_records": rounds,
            }
        )
    return reports


def discovery_gate(
    cases: list[dict[str, Any]],
    design: dict[str, Any],
    *,
    frozen_fit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the preregistered opportunity gate using block two only."""
    discovery = [case for case in cases if case["partition"] == "discovery"]
    action_names = design["actions"]
    if not discovery:
        raise LoopExperimentError("discovery gate requires discovery cases")
    fit = frozen_fit or _fit_discovery(
        discovery, action_names, design["benchmark_rounds"]
    )
    second_start = design["benchmark_rounds"] + 1
    second_stop = 2 * design["benchmark_rounds"]
    metrics = quality_analysis.paired_metrics(
        _paired_action_reports(
            discovery,
            fit["best_constant"],
            fit["case_oracle_actions"],
            second_start,
            second_stop,
        ),
        reference="constant",
        candidate="oracle",
    )
    noise_floor_log = max(
        math.log1p(design["noise_floor_fraction"]),
        3 * metrics["maximum_state_mad_log_ratio"],
    )
    distinct_cases = sum(case["distinct_code_identities"] > 1 for case in discovery)
    family_positive = {
        family: values["geometric_mean_ratio"] < 1
        for family, values in metrics["families"].items()
    }
    requirements = {
        "oracle_at_least_discovery_gap_better": metrics["geometric_mean_ratio"]
        <= 1 - design["discovery_gap_fraction"],
        "gain_beyond_noise_floor": math.log(metrics["geometric_mean_ratio"])
        < -noise_floor_log,
        "positive_direction_in_two_families": sum(family_positive.values()) >= 2,
        "distinct_code_in_at_least_half_cases": distinct_cases
        >= math.ceil(len(discovery) / 2),
    }
    return {
        "passed": all(requirements.values()),
        "best_constant": fit["best_constant"],
        "first_block_gm_ratio_to_stock": fit["first_block_gm_ratio_to_stock"],
        "case_oracle_actions": fit["case_oracle_actions"],
        "block_one_rounds": [1, design["benchmark_rounds"]],
        "confirmation_rounds": [second_start, second_stop],
        "confirmation_metrics_oracle_vs_constant": metrics,
        "noise_floor_log_ratio": noise_floor_log,
        "noise_floor_fraction": math.expm1(noise_floor_log),
        "distinct_code_cases": distinct_cases,
        "discovery_case_count": len(discovery),
        "family_positive_direction": family_positive,
        "requirements": requirements,
    }


def _case_features(
    case: dict[str, Any], target: dict[str, Any] | None = None
) -> dict[str, int]:
    features = dict(case["source_features"])
    features.update(TARGET_NUMERIC_FEATURES if target is None else target)
    return features


def _action_features(case: dict[str, Any], action_name: str) -> dict[str, int]:
    _name, width, interleave = ACTION_BY_NAME[action_name]
    return {
        **_case_features(case),
        "vectorize_width": width or 0,
        "interleave_count": interleave or 0,
    }


def _provider_packet(case: dict[str, Any], action_names: list[str]) -> dict[str, Any]:
    """Construct a blind packet with source facts and requested action metadata only."""
    rows = [_action_features(case, name) for name in action_names]
    feature_names = sorted(rows[0])
    features = {name: [row[name] for row in rows] for name in feature_names}
    actions = [
        {
            "id": str(index),
            "description": (
                "stock LLVM-selected vectorization and interleaving policy"
                if name == "stock"
                else f"request vectorize_width={ACTION_BY_NAME[name][1]}, "
                f"interleave_count={ACTION_BY_NAME[name][2]}"
            ),
        }
        for index, name in enumerate(action_names)
    ]
    return {
        "task": (
            "Choose the loop-local vectorization request most likely to reduce runtime on "
            "Apple M3 using its NEON vector unit, while preserving LLVM legality and downstream optimization."
        ),
        "features": features,
        "feature_descriptions": {
            name: FEATURE_DESCRIPTIONS[name] for name in feature_names
        },
        "actions": actions,
    }


def _packet_fingerprint(packet: dict[str, Any]) -> str:
    encoded = json.dumps(
        packet, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def query_provider_cache(packet: dict[str, Any], cache_path: Path) -> dict[str, Any]:
    """Query both providers once per identical packet, retaining failed attempts."""
    fingerprint = _packet_fingerprint(packet)
    cache = (
        _load(cache_path)
        if cache_path.exists()
        else {"schema_version": 2, "packets": {}}
    )
    if (
        not isinstance(cache, dict)
        or cache.get("schema_version") != 2
        or not isinstance(cache.get("packets"), dict)
    ):
        raise LoopExperimentError("provider cache is malformed")
    entry = cache["packets"].get(fingerprint)
    if entry is None:
        entry = {
            "packet_sha256": fingerprint,
            "packet": packet,
            "responses": {},
            "response_sha256": {},
            "attempts": [],
        }
        cache["packets"][fingerprint] = entry
    elif entry.get("packet_sha256") != fingerprint or entry.get("packet") != packet:
        raise LoopExperimentError("provider cache packet changed")
    if not isinstance(entry.get("responses"), dict) or not isinstance(
        entry.get("response_sha256"), dict
    ):
        raise LoopExperimentError("provider cache response manifest is malformed")
    for provider, response in entry["responses"].items():
        if entry["response_sha256"].get(provider) != _packet_fingerprint(response):
            raise LoopExperimentError(f"cached {provider} response changed")

    policy = experiment.OneInterventionPolicy("jev")
    for provider, choose in (("jev", jev.choose), ("codex", codex_advisor.choose)):
        if provider in entry["responses"]:
            continue
        policy.provider_attempts = []
        response = policy._ask(provider, choose, packet, packet["actions"])
        entry["attempts"].extend(policy.provider_attempts)
        if response is not None:
            entry["responses"][provider] = response
            entry["response_sha256"][provider] = _packet_fingerprint(response)
        _save(cache_path, cache)
    if set(entry["responses"]) != {"jev", "codex"}:
        unavailable = sorted({"jev", "codex"} - set(entry["responses"]))
        raise LoopExperimentError(
            f"provider decisions incomplete for packet {fingerprint}: {unavailable}"
        )
    return entry


def _response_action(response: dict[str, Any], action_names: list[str]) -> str:
    action_id = response.get("chosen_action")
    if not isinstance(action_id, str) or not action_id.isdigit():
        raise LoopExperimentError("provider returned an invalid menu index")
    index = int(action_id)
    if index < 0 or index >= len(action_names):
        raise LoopExperimentError("provider selected an action outside the menu")
    return action_names[index]


def _fit_nn(
    discovery: list[dict[str, Any]], action_names: list[str], block_stop: int
) -> dict[str, Any]:
    rows = []
    for case in discovery:
        baseline = _block_median(case, "stock", 1, block_stop, block=1)
        for action in action_names:
            rows.append(
                {
                    "features": _action_features(case, action),
                    "loss": _block_median(case, action, 1, block_stop, block=1)
                    / baseline,
                }
            )
    return quality_analysis.fit_nearest_neighbor(rows)


def _policy_metrics(
    cases: list[dict[str, Any]],
    actions: dict[str, dict[str, str]],
    *,
    block: int = 1,
) -> dict[str, Any]:
    policy_names = list(next(iter(actions.values())))
    reports = {name: [] for name in policy_names}
    for case in cases:
        selected = actions[case["case_id"]]
        for policy_name, action in selected.items():
            reports[policy_name].append(
                {
                    "family": case["family"],
                    "state": case["case_id"],
                    "round_records": [
                        {
                            "round": row["round"],
                            "measurements": {
                                "selected": row["measurements"][action],
                            },
                        }
                        for row in case["benchmark"]["round_records"]
                        if row["block"] == block
                    ],
                }
            )
    baseline_names = [name for name in policy_names if name != "stock"]
    comparisons = {}
    for candidate in policy_names:
        for reference in baseline_names:
            if candidate != reference:
                comparisons[f"{candidate}_vs_{reference}"] = (
                    quality_analysis.paired_metrics(
                        _merge_policy_reports(reports[candidate], reports[reference]),
                        reference="reference",
                        candidate="candidate",
                    )
                )
    # Stock is also a separately reported baseline for every adaptive/control policy.
    for candidate in policy_names:
        if candidate != "stock":
            comparisons[f"{candidate}_vs_stock"] = quality_analysis.paired_metrics(
                _merge_policy_reports(reports[candidate], reports["stock"]),
                reference="reference",
                candidate="candidate",
            )
    return comparisons


def _merge_policy_reports(
    candidate_reports: list[dict[str, Any]], reference_reports: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = []
    for candidate, reference in zip(candidate_reports, reference_reports, strict=True):
        if (candidate["family"], candidate["state"]) != (
            reference["family"],
            reference["state"],
        ):
            raise LoopExperimentError("policy timing state alignment changed")
        rounds = []
        for c_row, r_row in zip(
            candidate["round_records"], reference["round_records"], strict=True
        ):
            rounds.append(
                {
                    "round": c_row["round"],
                    "family": candidate["family"],
                    "state": candidate["state"],
                    "measurements": {
                        "candidate": {
                            "elapsed_ns": c_row["measurements"]["selected"][
                                "elapsed_ns"
                            ]
                        },
                        "reference": {
                            "elapsed_ns": r_row["measurements"]["selected"][
                                "elapsed_ns"
                            ]
                        },
                    },
                }
            )
        merged.append(
            {
                "family": candidate["family"],
                "state": candidate["state"],
                "round_records": rounds,
            }
        )
    return merged


def run_discovery(
    design_path: Path, validation_path: Path, output: Path
) -> dict[str, Any]:
    design = _design(design_path)
    validation_path = validation_path.expanduser().resolve()
    design_path = design_path.expanduser().resolve()
    output = output.expanduser().resolve()
    if not validation_path.is_file() or not design_path.is_file():
        raise LoopExperimentError(
            "design and native validation report must be existing files"
        )
    provenance = _provenance(design_path, validation_path)
    report_path = output / "discovery.json"
    if report_path.exists():
        existing = _load(report_path)
        if existing.get("provenance") != provenance:
            raise LoopExperimentError(
                "discovery provenance changed; use a fresh output directory"
            )
        _validate_builds(
            design, validation_path, output, existing.get("all_builds", [])
        )
        _verify_timing_caches(existing.get("discovery_cases", []))
        _frozen_discovery_fit(output, existing, design, provenance)
        return existing
    output.mkdir(parents=True, exist_ok=True)
    built = _build_all(design, validation_path, output)
    # The complete discovery/calibration/test build matrix exists before the first timing.
    discovery_built = [case for case in built if case["partition"] == "discovery"]
    block_one = _measure_cases(discovery_built, design, output, blocks=(1,))
    frozen_fit = _fit_discovery(
        block_one, design["actions"], design["benchmark_rounds"]
    )
    fit_checkpoint = {
        "provenance": provenance,
        "block_one_rounds": [1, design["benchmark_rounds"]],
        "fit": frozen_fit,
    }
    fit_path = output / "discovery-fit.json"
    if fit_path.exists() and _load(fit_path) != fit_checkpoint:
        raise LoopExperimentError(
            "frozen discovery fit changed; use a fresh output directory"
        )
    if not fit_path.exists():
        _save(fit_path, fit_checkpoint)
    block_two = _measure_cases(discovery_built, design, output, blocks=(2,))
    discovery_cases = _merge_measurement_blocks(block_one, block_two)
    gate = discovery_gate(discovery_cases, design, frozen_fit=frozen_fit)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed_gate" if gate["passed"] else "stopped_gate_failed",
        "phase": "discovery",
        "design": design,
        "provenance": provenance,
        "fit_checkpoint_sha256": _sha256(fit_path),
        "all_builds": [
            {
                "partition": case["partition"],
                "family": case["family"],
                "trip_count": case["trip_count"],
                "case_id": case["case_id"],
                "source_features": case["source_features"],
                "kernel_sha256": case["kernel_sha256"],
                "driver_sha256": case["driver_sha256"],
                "build_report_path": case["build_report_path"],
                "build_report_sha256": case["build_report_sha256"],
            }
            for case in built
        ],
        "discovery_gate": gate,
        "discovery_cases": discovery_cases,
        "model_calls": 0,
    }
    _save(report_path, report)
    return report


def run_models(
    design_path: Path,
    validation_path: Path,
    output: Path,
    discovery_report_path: Path,
) -> dict[str, Any]:
    design_path = design_path.expanduser().resolve()
    validation_path = validation_path.expanduser().resolve()
    output = output.expanduser().resolve()
    discovery_report_path = discovery_report_path.expanduser().resolve()
    design = _design(design_path)
    discovery = _load(discovery_report_path)
    provenance = _provenance(design_path, validation_path)
    if discovery.get("provenance") != provenance:
        raise LoopExperimentError(
            "frozen discovery report provenance does not match current inputs"
        )
    if discovery.get("status") != "passed_gate" or not discovery.get(
        "discovery_gate", {}
    ).get("passed"):
        raise LoopExperimentError(
            "discovery opportunity gate failed; model calls are not authorized by this design"
        )
    recorded_builds = discovery.get("all_builds")
    if not isinstance(recorded_builds, list):
        raise LoopExperimentError("frozen discovery report has no build manifest")
    validated = _validate_builds(design, validation_path, output, recorded_builds)
    _verify_timing_caches(discovery.get("discovery_cases", []))
    _frozen_discovery_fit(output, discovery, design, provenance)
    model_path = output / "models.json"
    if model_path.exists():
        existing = _load(model_path)
        if existing.get("provenance") != provenance or existing.get(
            "discovery_report_sha256"
        ) != _sha256(discovery_report_path):
            raise LoopExperimentError(
                "model report provenance changed; use a fresh output directory"
            )
        _verify_timing_caches(existing.get("calibration_cases", []))
        _verify_timing_caches(existing.get("test_cases", []))
        cache_path = Path(existing.get("provider_cache", ""))
        if not cache_path.is_file() or _sha256(cache_path) != existing.get(
            "provider_cache_sha256"
        ):
            raise LoopExperimentError("provider cache changed or is missing")
        return existing

    timed_by_id = {case["case_id"]: case for case in discovery["discovery_cases"]}
    measured_discovery = []
    for case in validated:
        if case["partition"] == "discovery":
            timed = timed_by_id.get(case["case_id"])
            if not isinstance(timed, dict):
                raise LoopExperimentError(
                    f"discovery measurements missing for {case['case_id']}"
                )
            measured_discovery.append({**case, "benchmark": timed["benchmark"]})
    non_discovery = [case for case in validated if case["partition"] != "discovery"]

    # All calibration and heldout executables were built in discovery. Freeze all decisions
    # before recording any calibration or test performance.
    output.mkdir(parents=True, exist_ok=True)
    model = _fit_nn(measured_discovery, design["actions"], design["benchmark_rounds"])
    # Create blind packets from numeric source features before obtaining any timing labels.
    blind_decisions = {}
    provider_entries = {}
    packet_cache = output / "providers" / "cache.json"
    for case in non_discovery:
        packet = _provider_packet(case, design["actions"])
        entry = query_provider_cache(packet, packet_cache)
        fingerprint = entry["packet_sha256"]
        provider_entries[fingerprint] = entry
        blind_decisions[case["case_id"]] = {
            "packet_sha256": fingerprint,
            "jev_action": _response_action(
                entry["responses"]["jev"], design["actions"]
            ),
            "jev_confidence": entry["responses"]["jev"]["confidence"],
            "codex_action": _response_action(
                entry["responses"]["codex"], design["actions"]
            ),
        }
        score = quality_analysis.score_actions(
            model,
            {
                name: [
                    _action_features(case, action)[name] for action in design["actions"]
                ]
                for name in model["features"]
            },
            [
                {"id": str(index), "description": action}
                for index, action in enumerate(design["actions"])
            ],
        )
        blind_decisions[case["case_id"]]["nearest_neighbor_action"] = design["actions"][
            int(score["action_id"])
        ]
        blind_decisions[case["case_id"]]["nearest_neighbor_predictions"] = score[
            "predictions"
        ]

    measured_other = _measure_cases(non_discovery, design, output, blocks=(1,))
    by_id = {case["case_id"]: case for case in measured_other}
    calibration = [
        by_id[case["case_id"]]
        for case in non_discovery
        if case["partition"] == "calibrate"
    ]
    test = [
        by_id[case["case_id"]] for case in non_discovery if case["partition"] == "test"
    ]
    model_result = _evaluate_models_with_blind_choices(
        measured_discovery,
        calibration,
        test,
        design,
        blind_decisions,
        provider_entries,
        packet_cache,
        model,
    )
    model_result.update(
        {
            "provenance": provenance,
            "discovery_report_sha256": _sha256(discovery_report_path),
            "provider_cache_sha256": _sha256(packet_cache),
            "design": design,
            "phase": "models",
        }
    )
    _save(model_path, model_result)
    return model_result


def _evaluate_models_with_blind_choices(
    discovery: list[dict[str, Any]],
    calibration: list[dict[str, Any]],
    test: list[dict[str, Any]],
    design: dict[str, Any],
    decisions: dict[str, dict[str, Any]],
    provider_entries: dict[str, dict[str, Any]],
    cache_path: Path,
    model: dict[str, Any],
) -> dict[str, Any]:
    action_names = design["actions"]
    calibration_rows = []
    for case in calibration:
        decision = decisions[case["case_id"]]
        calibration_rows.append(
            {
                "confidence": decision["jev_confidence"],
                "jev_ns": _block_median(
                    case, decision["jev_action"], 1, design["benchmark_rounds"], block=1
                ),
                "codex_ns": _block_median(
                    case,
                    decision["codex_action"],
                    1,
                    design["benchmark_rounds"],
                    block=1,
                ),
            }
        )
    routing = quality_analysis.calibrate_threshold(
        calibration_rows,
        max_escalation_fraction=design["calibration_escalation_budget"],
    )
    best_constant = _fit_discovery(discovery, action_names, design["benchmark_rounds"])[
        "best_constant"
    ]
    test_actions = {}
    for case in test:
        decision = decisions[case["case_id"]]
        route = quality_analysis.select_route(
            routing["chosen"], decision["jev_confidence"]
        )
        test_actions[case["case_id"]] = {
            "stock": "stock",
            "best_constant": best_constant,
            "nearest_neighbor": decision["nearest_neighbor_action"],
            "jev": decision["jev_action"],
            "codex": decision["codex_action"],
            "cascade": decision["codex_action"] if route else decision["jev_action"],
        }
    comparisons = _policy_metrics(test, test_actions, block=1)
    test_escalation_rate = sum(
        quality_analysis.select_route(
            routing["chosen"], decisions[case["case_id"]]["jev_confidence"]
        )
        for case in test
    ) / len(test)
    constant = comparisons["cascade_vs_best_constant"]
    jev_metrics = comparisons["cascade_vs_jev"]
    nn_metrics = comparisons["cascade_vs_nearest_neighbor"]
    constant_noise_log = max(
        math.log1p(design["noise_floor_fraction"]),
        3 * constant["maximum_state_mad_log_ratio"],
    )
    jev_noise_log = max(
        math.log1p(design["noise_floor_fraction"]),
        3 * jev_metrics["maximum_state_mad_log_ratio"],
    )
    nn_noise_log = max(
        math.log1p(design["noise_floor_fraction"]),
        3 * nn_metrics["maximum_state_mad_log_ratio"],
    )
    requirements = {
        "improves_over_constant_beyond_noise": math.log(
            constant["geometric_mean_ratio"]
        )
        < -constant_noise_log,
        "improves_over_jev_beyond_noise": math.log(jev_metrics["geometric_mean_ratio"])
        < -jev_noise_log,
        "improves_over_nearest_neighbor_beyond_noise": math.log(
            nn_metrics["geometric_mean_ratio"]
        )
        < -nn_noise_log,
        "positive_in_two_of_three_families_vs_constant": sum(
            value["geometric_mean_ratio"] < 1 for value in constant["families"].values()
        )
        >= 2,
        "positive_in_two_of_three_families_vs_jev": sum(
            value["geometric_mean_ratio"] < 1
            for value in jev_metrics["families"].values()
        )
        >= 2,
        "positive_in_two_of_three_families_vs_nearest_neighbor": sum(
            value["geometric_mean_ratio"] < 1
            for value in nn_metrics["families"].values()
        )
        >= 2,
        "calibration_escalation_within_budget": routing["chosen"]["escalation_fraction"]
        <= design["calibration_escalation_budget"],
        "heldout_escalation_within_budget": test_escalation_rate
        <= design["calibration_escalation_budget"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "measured",
        "scope": design["scope"],
        "target": TARGET_FACTS,
        "decisions": decisions,
        "provider_cache": str(cache_path),
        "provider_packets": list(provider_entries.values()),
        "nearest_neighbor_model": model,
        "calibration": {
            "rows": calibration_rows,
            "routing": routing,
            "escalation_budget": design["calibration_escalation_budget"],
            "observed_escalation_rate": routing["chosen"]["escalation_fraction"],
        },
        "calibration_cases": calibration,
        "test_cases": test,
        "test_actions": test_actions,
        "test_metrics": comparisons,
        "cascade_escalation_rate": test_escalation_rate,
        "noise_floor_log_ratios": {
            "constant": constant_noise_log,
            "jev": jev_noise_log,
            "nearest_neighbor": nn_noise_log,
        },
        "requirements": requirements,
        "heldout_screening_success": all(requirements.values()),
        "interpretation": "Synthetic single-loop native screening on one CPU; does not establish production performance or maintenance savings.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("discovery", "models"), default="discovery")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--discovery-report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.phase == "discovery":
            report = run_discovery(args.design, args.validation_report, args.output)
        else:
            if args.discovery_report is None:
                raise LoopExperimentError(
                    "--discovery-report is required for models phase"
                )
            report = run_models(
                args.design, args.validation_report, args.output, args.discovery_report
            )
    except (
        LoopExperimentError,
        benchmark.BenchmarkError,
        ValueError,
        OSError,
    ) as error:
        print(f"loop experiment failed: {error}", file=sys.stderr)
        return 2
    report_path = args.output / (
        "discovery.json" if args.phase == "discovery" else "models.json"
    )
    print(f"{report_path} ({report['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
