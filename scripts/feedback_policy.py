"""Small, dependency-free ridge policy for numeric action packets."""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _feature_row(features: Any, names: tuple[str, ...] | list[str], label: str) -> dict[str, float]:
    if not isinstance(features, dict) or set(features) != set(names):
        raise ValueError(f"{label} feature names must match the fitted model")
    return {name: _number(features[name], f"{label} feature {name!r}") for name in names}


def _scaled(value: float, lower: float, upper: float, label: str) -> float:
    if upper == lower:
        return 0.0
    span = upper - lower
    offset = value - lower
    if not math.isfinite(span) or not math.isfinite(offset):
        span = upper / 2.0 - lower / 2.0
        offset = value / 2.0 - lower / 2.0
    if span <= 0 or not math.isfinite(span) or not math.isfinite(offset):
        raise ValueError(f"{label} cannot be min-max scaled safely")
    result = offset / span
    if not math.isfinite(result):
        raise ValueError(f"{label} scaled value must be finite")
    return result


def _cholesky_solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """Solve a positive-definite system using Cholesky factorization."""
    size = len(vector)
    lower = [[0.0] * size for _ in range(size)]
    for row in range(size):
        for column in range(row + 1):
            value = matrix[row][column] - sum(
                lower[row][inner] * lower[column][inner]
                for inner in range(column)
            )
            if row == column:
                if value <= 0 or not math.isfinite(value):
                    raise ValueError("ridge system is not numerically positive definite")
                lower[row][column] = math.sqrt(value)
            else:
                lower[row][column] = value / lower[column][column]

    intermediate = [0.0] * size
    for row in range(size):
        intermediate[row] = (
            vector[row]
            - sum(lower[row][column] * intermediate[column] for column in range(row))
        ) / lower[row][row]

    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        solution[row] = (
            intermediate[row]
            - sum(
                lower[column][row] * solution[column]
                for column in range(row + 1, size)
            )
        ) / lower[row][row]
    if any(not math.isfinite(value) for value in solution):
        raise ValueError("ridge solution is not finite")
    return solution


def fit_ridge(
    rows: list[dict[str, Any]], alpha: float = 1.0
) -> dict[str, Any]:
    """Fit ridge regression to ``log(loss)`` with an unpenalized intercept.

    Features are min-max scaled using only the supplied training rows. A
    constant feature maps to zero for both training and prediction.
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows must be a non-empty list")
    penalty = _number(alpha, "alpha")
    if penalty <= 0:
        raise ValueError("alpha must be positive")

    names: tuple[str, ...] | None = None
    raw_features: list[dict[str, float]] = []
    targets: list[float] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("features"), dict):
            raise ValueError(f"row {index} must contain a feature mapping")
        features = row["features"]
        if not features or any(not isinstance(name, str) or not name for name in features):
            raise ValueError(f"row {index} must contain named features")
        row_names = tuple(sorted(features))
        if names is None:
            names = row_names
        elif row_names != names:
            raise ValueError("training rows must use the same feature names")
        raw_features.append(
            {name: _number(features[name], f"row {index} feature {name!r}") for name in names}
        )
        loss = _number(row.get("loss"), f"row {index} loss")
        if loss <= 0:
            raise ValueError(f"row {index} loss must be positive")
        targets.append(math.log(loss))

    assert names is not None
    ranges = {
        name: {
            "min": min(row[name] for row in raw_features),
            "max": max(row[name] for row in raw_features),
        }
        for name in names
    }
    scaled_rows = [
        {
            name: _scaled(
                row[name], ranges[name]["min"], ranges[name]["max"], f"feature {name!r}"
            )
            for name in names
        }
        for row in raw_features
    ]
    count = len(rows)
    means = {
        name: sum(row[name] for row in scaled_rows) / count for name in names
    }
    target_mean = sum(targets) / count
    centered_features = [
        [row[name] - means[name] for name in names] for row in scaled_rows
    ]
    centered_targets = [target - target_mean for target in targets]
    width = len(names)
    gram = [
        [
            sum(row[left_index] * row[right_index] for row in centered_features)
            + (penalty if left_index == right_index else 0.0)
            for right_index in range(width)
        ]
        for left_index in range(width)
    ]
    right_hand_side = [
        sum(
            row[index] * target
            for row, target in zip(centered_features, centered_targets, strict=True)
        )
        for index in range(width)
    ]
    coefficients_list = _cholesky_solve(gram, right_hand_side)
    coefficients = dict(zip(names, coefficients_list, strict=True))
    intercept = target_mean - sum(means[name] * coefficients[name] for name in names)
    if not math.isfinite(intercept):
        raise ValueError("ridge intercept is not finite")
    return {
        "schema_version": 1,
        "alpha": penalty,
        "features": list(names),
        "ranges": ranges,
        "means": means,
        "intercept": intercept,
        "coefficients": coefficients,
    }


def _read_model(model: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    if not isinstance(model, dict) or type(model.get("schema_version")) is not int or model[
        "schema_version"
    ] != 1:
        raise ValueError("unsupported ridge model")
    names = model.get("features")
    ranges = model.get("ranges")
    means = model.get("means")
    coefficients = model.get("coefficients")
    penalty = _number(model.get("alpha"), "model alpha")
    intercept = _number(model.get("intercept"), "model intercept")
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
        or names != sorted(set(names))
        or not isinstance(ranges, dict)
        or set(ranges) != set(names)
        or not isinstance(means, dict)
        or set(means) != set(names)
        or not isinstance(coefficients, dict)
        or set(coefficients) != set(names)
        or penalty <= 0
    ):
        raise ValueError("malformed ridge model")
    checked_ranges = {}
    for name in names:
        bounds = ranges[name]
        if not isinstance(bounds, dict):
            raise ValueError("malformed ridge feature range")
        lower = _number(bounds.get("min"), f"range {name!r} min")
        upper = _number(bounds.get("max"), f"range {name!r} max")
        if upper < lower:
            raise ValueError("malformed ridge feature range")
        checked_ranges[name] = {"min": lower, "max": upper}
    return names, {
        "ranges": checked_ranges,
        "means": {
            name: _number(means[name], f"feature {name!r} mean") for name in names
        },
        "coefficients": {
            name: _number(coefficients[name], f"feature {name!r} coefficient")
            for name in names
        },
        "intercept": intercept,
    }


def predict_ridge(model: dict[str, Any], features: dict[str, float]) -> float:
    """Predict the log loss for one row using the fitted training ranges."""
    names, checked = _read_model(model)
    row = _feature_row(features, names, "prediction")
    result = checked["intercept"]
    for name in names:
        bounds = checked["ranges"][name]
        scaled = _scaled(row[name], bounds["min"], bounds["max"], f"feature {name!r}")
        result += scaled * checked["coefficients"][name]
    if not math.isfinite(result):
        raise ValueError("predicted log loss is not finite")
    return result


def score_packet(model: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Score the existing column-oriented numeric action packet format."""
    names, _checked = _read_model(model)
    if not isinstance(packet, dict):
        raise ValueError("packet must be a mapping")
    actions = packet.get("actions")
    columns = packet.get("features")
    if not isinstance(actions, list) or not actions:
        raise ValueError("packet actions must be a non-empty list")
    if not isinstance(columns, dict) or set(columns) != set(names):
        raise ValueError("packet feature names must match the fitted model")
    action_ids: set[str] = set()
    numeric_action_ids: set[int] = set()
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("each packet action must be a mapping")
        action_id = action.get("id")
        if (
            not isinstance(action_id, str)
            or not action_id.isascii()
            or not action_id.isdigit()
        ):
            raise ValueError("packet action ids must be numeric strings")
        if action_id in action_ids:
            raise ValueError("packet action ids must be unique")
        numeric_id = int(action_id)
        if numeric_id in numeric_action_ids:
            raise ValueError("packet action ids must be numerically unique")
        action_ids.add(action_id)
        numeric_action_ids.add(numeric_id)

    numeric_ids = sorted(action_ids, key=int)
    vectors: dict[str, list[float]] = {}
    for name in names:
        values = columns[name]
        if not isinstance(values, (list, tuple)) or len(values) != len(actions):
            raise ValueError(f"packet feature {name!r} must align with its actions")
        vectors[name] = [_number(value, f"packet feature {name!r}") for value in values]
    vector_index = {action["id"]: index for index, action in enumerate(actions)}
    predicted = {}
    for action_id in numeric_ids:
        index = vector_index[action_id]
        row = {name: vectors[name][index] for name in names}
        predicted[action_id] = predict_ridge(model, row)
    selected = min(numeric_ids, key=lambda action_id: (predicted[action_id], int(action_id)))
    return {"action_id": selected, "predicted_log_losses": predicted}
