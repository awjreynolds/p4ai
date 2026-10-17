"""Pure-Python policies and descriptive analysis for quality experiments."""

from __future__ import annotations

import math
from statistics import median
from typing import Any

THRESHOLDS = (0.0, 0.25, 0.5, 0.75, 1.0)
TIE_IMPROVEMENT = 0.02
WIN_LOSS_MARGIN = 0.02


def fit_nearest_neighbor(training_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fit a JSON-safe 1-nearest-neighbor loss model using training rows only."""
    if not isinstance(training_rows, list) or not training_rows:
        raise ValueError("training_rows must be a non-empty list")

    names: tuple[str, ...] | None = None
    examples: list[dict[str, Any]] = []
    for index, row in enumerate(training_rows):
        if not isinstance(row, dict) or not isinstance(row.get("features"), dict):
            raise ValueError(f"training row {index} must contain a feature mapping")
        features = row["features"]
        if not features or any(not isinstance(name, str) or not name for name in features):
            raise ValueError(f"training row {index} must contain named scalar features")
        row_names = tuple(sorted(features))
        if names is None:
            names = row_names
        elif row_names != names:
            raise ValueError("training rows must use the same feature names")
        normalized = {name: _number(features[name], f"feature {name!r}") for name in names}
        loss = _number(row.get("loss"), f"training row {index} loss")
        if loss <= 0:
            raise ValueError("normalized runtime loss must be positive")
        examples.append({"features": normalized, "loss": loss})

    assert names is not None
    ranges = {
        name: {
            "min": min(example["features"][name] for example in examples),
            "max": max(example["features"][name] for example in examples),
        }
        for name in names
    }
    for bounds in ranges.values():
        bounds["scale"] = bounds["max"] - bounds["min"] or 1.0
    return {"schema_version": 1, "features": list(names), "ranges": ranges, "examples": examples}


def score_actions(
    model: dict[str, Any],
    features: dict[str, list[int | float]],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Predict each menu action's loss from its nearest training example."""
    names, ranges, examples = _read_model(model)
    if not isinstance(features, dict) or set(features) != set(names):
        raise ValueError("candidate feature names must match the fitted feature names")
    if not isinstance(actions, list) or not actions:
        raise ValueError("actions must be a non-empty menu")

    action_ids: set[str] = set()
    candidate_rows: list[dict[str, float]] = [dict() for _action in actions]
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("each action must be a mapping with an id")
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("action ids must be non-empty strings")
        if action_id in action_ids:
            raise ValueError("action ids must be unique")
        action_ids.add(action_id)

    for name in names:
        values = features[name]
        if not isinstance(values, (list, tuple)) or len(values) != len(actions):
            raise ValueError(f"feature {name!r} must align with the action menu")
        for index, value in enumerate(values):
            candidate_rows[index][name] = _number(value, f"candidate feature {name!r}")

    predictions: list[dict[str, Any]] = []
    for action, row in zip(actions, candidate_rows, strict=True):
        neighbor_index, neighbor = min(
            enumerate(examples),
            key=lambda pair: (_distance(row, pair[1]["features"], names, ranges), pair[0]),
        )
        distance = _distance(row, neighbor["features"], names, ranges)
        predictions.append(
            {
                "action_id": action["id"],
                "predicted_loss": neighbor["loss"],
                "nearest_distance": distance,
                "training_example_index": neighbor_index,
            }
        )

    selected_index = min(
        range(len(predictions)),
        key=lambda index: (predictions[index]["predicted_loss"], index),
    )
    selected = predictions[selected_index]
    return {
        "action": actions[selected_index],
        "action_id": selected["action_id"],
        "predicted_loss": selected["predicted_loss"],
        "nearest_distance": selected["nearest_distance"],
        "predictions": predictions,
    }


def _distance(
    left: dict[str, float],
    right: dict[str, float],
    names: list[str],
    ranges: dict[str, dict[str, float]],
) -> float:
    squared = sum(
        ((left[name] - right[name]) / ranges[name]["scale"]) ** 2 for name in names
    )
    return math.sqrt(squared / len(names))


def _read_model(
    model: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, float]], list[dict[str, Any]]]:
    if not isinstance(model, dict) or model.get("schema_version") != 1:
        raise ValueError("unsupported nearest-neighbor model")
    names = model.get("features")
    ranges = model.get("ranges")
    examples = model.get("examples")
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
        or names != sorted(set(names))
        or not isinstance(ranges, dict)
        or set(ranges) != set(names)
        or not isinstance(examples, list)
        or not examples
    ):
        raise ValueError("malformed nearest-neighbor model")
    checked_ranges: dict[str, dict[str, float]] = {}
    for name in names:
        bounds = ranges[name]
        if not isinstance(bounds, dict):
            raise ValueError("malformed nearest-neighbor feature range")
        lower = _number(bounds.get("min"), f"range {name!r} min")
        upper = _number(bounds.get("max"), f"range {name!r} max")
        scale = _number(bounds.get("scale"), f"range {name!r} scale")
        if upper < lower or scale <= 0:
            raise ValueError("malformed nearest-neighbor feature range")
        checked_ranges[name] = {"min": lower, "max": upper, "scale": scale}
    checked_examples: list[dict[str, Any]] = []
    for example in examples:
        if not isinstance(example, dict) or not isinstance(example.get("features"), dict):
            raise ValueError("malformed nearest-neighbor example")
        raw_features = example["features"]
        if set(raw_features) != set(names):
            raise ValueError("malformed nearest-neighbor example features")
        loss = _number(example.get("loss"), "example loss")
        if loss <= 0:
            raise ValueError("example loss must be positive")
        checked_examples.append(
            {
                "features": {
                    name: _number(raw_features[name], f"example feature {name!r}")
                    for name in names
                },
                "loss": loss,
            }
        )
    return names, checked_ranges, checked_examples


def calibrate_threshold(
    rows: list[dict[str, Any]], *, max_escalation_fraction: float = 0.25
) -> dict[str, Any]:
    """Choose a confidence routing rule under a held-out escalation budget."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("calibration rows must be a non-empty list")
    budget_fraction = _number(max_escalation_fraction, "max escalation fraction")
    if not 0 <= budget_fraction <= 1:
        raise ValueError("max escalation fraction must be between 0 and 1")

    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"calibration row {index} must be a mapping")
        confidence = _number(row.get("confidence"), f"row {index} confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        normalized.append(
            {
                "confidence": confidence,
                "jev_ns": _timing(row.get("jev_ns"), f"row {index} jev_ns"),
                "codex_ns": _timing(row.get("codex_ns"), f"row {index} codex_ns"),
            }
        )

    max_escalations = int(len(rows) * budget_fraction + 1e-12)
    rules = [
        {"mode": "never", "threshold": None},
        *({"mode": "threshold", "threshold": value} for value in THRESHOLDS),
        {"mode": "always", "threshold": None},
    ]
    candidates = []
    for rule in rules:
        if rule["mode"] == "always":
            route = [True] * len(normalized)
        elif rule["mode"] == "never":
            route = [False] * len(normalized)
        else:
            route = [row["confidence"] < rule["threshold"] for row in normalized]
        log_ratios = [
            math.log(row["codex_ns"]) - math.log(row["jev_ns"])
            if should_route
            else 0.0
            for row, should_route in zip(normalized, route, strict=True)
        ]
        log_ratios = [
            0.0 if math.log(1 - TIE_IMPROVEMENT) <= value < 0 else value
            for value in log_ratios
        ]
        raw_log_ratios = [
            math.log(row["codex_ns"]) - math.log(row["jev_ns"])
            if should_route
            else 0.0
            for row, should_route in zip(normalized, route, strict=True)
        ]
        escalations = sum(route)
        average_log_ratio = sum(log_ratios) / len(log_ratios)
        candidates.append(
            {
                **rule,
                "escalations": escalations,
                "escalation_fraction": escalations / len(normalized),
                "max_escalations": max_escalations,
                "within_budget": escalations <= max_escalations,
                "average_log_ratio": average_log_ratio,
                "geometric_mean_ratio": math.exp(average_log_ratio),
                "raw_average_log_ratio": sum(raw_log_ratios) / len(raw_log_ratios),
            }
        )

    eligible = [candidate for candidate in candidates if candidate["within_budget"]]
    mode_priority = {"never": 0, "threshold": 1, "always": 2}
    chosen = min(
        eligible,
        key=lambda candidate: (
            candidate["average_log_ratio"],
            candidate["escalations"],
            mode_priority[candidate["mode"]],
            candidate["threshold"] if candidate["threshold"] is not None else -1,
        ),
    )
    return {"chosen": dict(chosen), "candidates": candidates}


def select_route(chosen: dict[str, Any], confidence: int | float) -> bool:
    """Return whether the calibrated rule should escalate this decision."""
    value = _number(confidence, "confidence")
    if not 0 <= value <= 1 or not isinstance(chosen, dict):
        raise ValueError("route requires a rule and confidence between 0 and 1")
    mode = chosen.get("mode")
    if mode == "never":
        return False
    if mode == "always":
        return True
    if mode == "threshold":
        threshold = _number(chosen.get("threshold"), "routing threshold")
        if not 0 <= threshold <= 1:
            raise ValueError("routing threshold must be between 0 and 1")
        return value < threshold
    raise ValueError("routing rule mode must be never, threshold, or always")


def _timing(value: Any, label: str) -> float:
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError(f"{label} must contain at least one timing")
        result = median(_number(item, label) for item in value)
    else:
        result = _number(value, label)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return float(result)


def paired_metrics(
    report_or_reports: dict[str, Any] | list[dict[str, Any]],
    *,
    reference: str = "jev",
    candidate: str = "codex",
) -> dict[str, Any]:
    """Summarize paired benchmark rounds, equally weighting states and families.

    Ratios are candidate/reference, so values below one favor the candidate.
    Each round is paired within its state; state mean log ratios are equally
    weighted within a family, and family mean log ratios are equally weighted
    for the overall descriptive geometric mean. The maximum state MAD preserves
    localized timing noise; identical-sample counts include only states with at
    least two rounds.
    """
    if not isinstance(reference, str) or not reference or not isinstance(candidate, str) or not candidate:
        raise ValueError("reference and candidate names must be non-empty strings")
    reports = _benchmark_reports(report_or_reports)
    state_logs: dict[str, dict[str, list[float]]] = {}
    for report_index, report in enumerate(reports):
        if not isinstance(report, dict) or not isinstance(report.get("round_records"), list):
            raise ValueError("each report must contain a round_records list")
        report_family = report.get("family", "unknown")
        report_state = report.get("state", report.get("state_id", f"report-{report_index}"))
        if not isinstance(report_family, str) or not report_family:
            raise ValueError("family names must be non-empty strings")
        if not isinstance(report_state, (str, int)) or isinstance(report_state, bool):
            raise ValueError("state identifiers must be strings or integers")
        for round_record in report["round_records"]:
            if not isinstance(round_record, dict):
                raise ValueError("each round record must be a mapping")
            family = round_record.get("family", report_family)
            state = round_record.get("state", round_record.get("state_id", report_state))
            if not isinstance(family, str) or not family:
                raise ValueError("family names must be non-empty strings")
            if not isinstance(state, (str, int)) or isinstance(state, bool):
                raise ValueError("state identifiers must be strings or integers")
            measurements = round_record.get("measurements")
            if not isinstance(measurements, dict):
                raise ValueError("round record must contain measurements")
            try:
                reference_ns = _number(
                    measurements[reference]["elapsed_ns"], f"{reference} elapsed_ns"
                )
                candidate_ns = _number(
                    measurements[candidate]["elapsed_ns"], f"{candidate} elapsed_ns"
                )
            except (KeyError, TypeError) as error:
                raise ValueError("round record is missing a paired timing") from error
            if reference_ns <= 0 or candidate_ns <= 0:
                raise ValueError("paired benchmark timings must be positive")
            state_key = str(state)
            state_logs.setdefault(family, {}).setdefault(state_key, []).append(
                math.log(candidate_ns) - math.log(reference_ns)
            )

    if not state_logs:
        raise ValueError("at least one paired benchmark round is required")

    families: dict[str, Any] = {}
    family_logs: list[float] = []
    state_mads: list[float] = []
    no_difference_states = 0
    all_identical_sample_states = 0
    wins = losses = ties = 0
    for family in sorted(state_logs):
        states = {}
        state_mean_logs = []
        family_state_mads = []
        family_no_difference_states = 0
        family_all_identical_sample_states = 0
        for state in sorted(state_logs[family]):
            values = state_logs[family][state]
            mean_log = sum(values) / len(values)
            mad = _median_absolute_deviation(values)
            no_difference = all(value == 0 for value in values)
            all_samples_identical = len(values) > 1 and max(values) - min(values) <= 1e-12
            state_mean_logs.append(mean_log)
            family_state_mads.append(mad)
            state_mads.append(mad)
            family_no_difference_states += int(no_difference)
            family_all_identical_sample_states += int(all_samples_identical)
            states[state] = {
                "geometric_mean_ratio": math.exp(mean_log),
                "paired_rounds": len(values),
                "median_absolute_deviation_log_ratio": mad,
                "no_difference": no_difference,
                "all_samples_identical": all_samples_identical,
            }
        no_difference_states += family_no_difference_states
        all_identical_sample_states += family_all_identical_sample_states
        family_mean_log = sum(state_mean_logs) / len(state_mean_logs)
        family_ratio = math.exp(family_mean_log)
        family_logs.append(family_mean_log)
        if family_ratio < 1 - WIN_LOSS_MARGIN:
            wins += 1
        elif family_ratio > 1 + WIN_LOSS_MARGIN:
            losses += 1
        else:
            ties += 1
        families[family] = {
            "geometric_mean_ratio": family_ratio,
            "state_count": len(states),
            "median_absolute_deviation_log_ratio": median(family_state_mads),
            "maximum_state_mad_log_ratio": max(family_state_mads),
            "no_difference_state_count": family_no_difference_states,
            "all_identical_sample_state_count": family_all_identical_sample_states,
            "states": states,
        }

    overall_log = sum(family_logs) / len(family_logs)
    return {
        "reference": reference,
        "candidate": candidate,
        "geometric_mean_ratio": math.exp(overall_log),
        "family_count": len(families),
        "wins_over_2pct": wins,
        "losses_over_2pct": losses,
        "within_2pct": ties,
        "median_absolute_deviation_log_ratio": median(state_mads),
        "maximum_state_mad_log_ratio": max(state_mads),
        "no_difference_state_count": no_difference_states,
        "all_identical_sample_state_count": all_identical_sample_states,
        "families": families,
    }


def _benchmark_reports(
    report_or_reports: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(report_or_reports, dict):
        return [report_or_reports]
    if not isinstance(report_or_reports, list) or not report_or_reports:
        raise ValueError("provide one benchmark report or a non-empty list of reports")
    if all(isinstance(record, dict) and "measurements" in record for record in report_or_reports):
        return [{"round_records": report_or_reports}]
    return report_or_reports


def _median_absolute_deviation(values: list[float]) -> float:
    center = median(values)
    return median(abs(value - center) for value in values)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number
