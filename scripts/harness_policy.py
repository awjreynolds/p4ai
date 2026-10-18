"""Dependency-free replay for frozen harness compiler policies."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
POLICY_TYPE = "harness-random-forest"
ACTION_ORDERS = ("ijk", "ikj", "jik", "jki", "kij", "kji")
ACTION_IDS = tuple(str(index) for index in range(len(ACTION_ORDERS)))
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_KEYS = {
    "schema_version",
    "library",
    "library_version",
    "parameters",
    "features",
    "training_rows",
    "training_targets",
    "estimators",
}


def _json_value(value: Any, label: str) -> Any:
    """Copy JSON values while rejecting ambiguous keys and non-finite numbers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{label} must contain only finite numbers")
        return value
    if isinstance(value, list):
        return [_json_value(item, label) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} object keys must be strings")
        return {key: _json_value(item, label) for key, item in value.items()}
    raise ValueError(f"{label} must contain only JSON values")


def canonical_sha256(value: Any) -> str:
    """Hash canonical UTF-8 JSON, independent of whitespace and key order."""
    checked = _json_value(value, "value")
    payload = json.dumps(
        checked,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def _float32(value: Any, label: str) -> float:
    number = _number(value, label)
    try:
        rounded = struct.unpack("!f", struct.pack("!f", number))[0]
    except (OverflowError, struct.error) as error:
        raise ValueError(f"{label} is outside the supported float32 range") from error
    if not math.isfinite(rounded):
        raise ValueError(f"{label} is outside the supported float32 range")
    return rounded


def _compiler_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"sha256", "target"}:
        raise ValueError("compiler identity must contain exactly sha256 and target")
    digest = value.get("sha256")
    target = value.get("target")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ValueError("compiler identity sha256 must be 64 lowercase hexadecimal characters")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("compiler identity target must be a non-empty string")
    return {"sha256": digest, "target": target}


def _integer_array(value: Any, label: str, length: int | None = None) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty array")
    if length is not None and len(value) != length:
        raise ValueError("tree arrays must have equal lengths")
    if any(type(item) is not int for item in value):
        raise ValueError(f"{label} must contain integers")
    return value


def _finite_array(value: Any, label: str, length: int) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("tree arrays must have equal lengths")
    return [_number(item, f"{label}[{index}]") for index, item in enumerate(value)]


def _validate_tree(tree: Any, feature_count: int, tree_index: int) -> dict[str, list[Any]]:
    label = f"estimator {tree_index}"
    if not isinstance(tree, dict) or set(tree) != {
        "children_left", "children_right", "feature", "threshold", "value"
    }:
        raise ValueError(f"{label} has an invalid tree schema")
    left = _integer_array(tree["children_left"], f"{label} children_left")
    right = _integer_array(tree["children_right"], f"{label} children_right", len(left))
    features = _integer_array(tree["feature"], f"{label} feature", len(left))
    thresholds = _finite_array(tree["threshold"], f"{label} threshold", len(left))
    values = _finite_array(tree["value"], f"{label} value", len(left))

    node_count = len(left)
    for node, (left_child, right_child, feature) in enumerate(zip(left, right, features, strict=True)):
        leaf = left_child == -1 and right_child == -1
        if (left_child == -1) != (right_child == -1):
            raise ValueError(f"{label} node {node} has only one child")
        if leaf:
            continue
        if not (0 <= left_child < node_count and 0 <= right_child < node_count):
            raise ValueError(f"{label} node {node} has an out-of-bounds child")
        if not 0 <= feature < feature_count:
            raise ValueError(f"{label} node {node} has an out-of-bounds feature")

    # An iterative color walk rejects cycles and shared children without relying
    # on Python's recursion limit, then confirms every serialized node is used.
    state = [0] * node_count
    stack = [(0, False)]
    while stack:
        node, exiting = stack.pop()
        if exiting:
            state[node] = 2
            continue
        if state[node] == 1:
            raise ValueError(f"{label} contains a cycle")
        if state[node] == 2:
            raise ValueError(f"{label} contains a shared child")
        state[node] = 1
        stack.append((node, True))
        if left[node] != -1:
            stack.append((right[node], False))
            stack.append((left[node], False))
    if any(mark == 0 for mark in state):
        raise ValueError(f"{label} contains unreachable nodes")
    return {
        "children_left": left,
        "children_right": right,
        "feature": features,
        "threshold": thresholds,
        "value": values,
    }


def _validate_model(model: Any) -> tuple[dict[str, Any], list[str], list[dict[str, list[Any]]]]:
    if not isinstance(model, dict) or set(model) != _MODEL_KEYS:
        raise ValueError("model has an unsupported random-forest schema")
    if type(model.get("schema_version")) is not int or model["schema_version"] != 1:
        raise ValueError("unsupported random-forest model version")
    if model.get("library") != "scikit-learn":
        raise ValueError("model library must be scikit-learn")
    if not isinstance(model.get("library_version"), str) or not model["library_version"]:
        raise ValueError("model library version is missing")
    if not isinstance(model.get("parameters"), dict):
        raise ValueError("model parameters must be an object")
    if model["parameters"].get("n_jobs") != 1:
        raise ValueError("model must use the frozen single-worker prediction order")
    if type(model.get("training_rows")) is not int or model["training_rows"] <= 0:
        raise ValueError("model training_rows must be positive")
    if not isinstance(model.get("training_targets"), str) or not model["training_targets"]:
        raise ValueError("model training_targets must be described")
    names = model.get("features")
    if (
        not isinstance(names, list)
        or not names
        or any(not isinstance(name, str) or not name for name in names)
        or names != sorted(set(names))
    ):
        raise ValueError("model features must be non-empty, sorted, unique names")
    estimators = model.get("estimators")
    if not isinstance(estimators, list) or not estimators:
        raise ValueError("model estimators must be a non-empty array")
    expected_count = model["parameters"].get("n_estimators")
    if type(expected_count) is not int or expected_count != len(estimators):
        raise ValueError("estimator count differs from model parameters")
    trees = [_validate_tree(tree, len(names), index) for index, tree in enumerate(estimators)]
    copied = _json_value(model, "model")
    return copied, names, trees


def make_artifact(
    model: dict[str, Any],
    compiler_identity: dict[str, Any],
    training_provenance: dict[str, Any],
) -> dict[str, Any]:
    """Bind an exported forest to its feature/action and compiler contract."""
    checked_model, names, _trees = _validate_model(model)
    identity = _compiler_identity(compiler_identity)
    if not isinstance(training_provenance, dict):
        raise ValueError("training provenance must be an object")
    provenance = _json_value(training_provenance, "training provenance")
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_type": POLICY_TYPE,
        "model": checked_model,
        "feature_schema": list(names),
        "actions": list(ACTION_ORDERS),
        "compiler_identity": identity,
        "training_provenance": provenance,
    }


def _validate_artifact(
    artifact: Any, compiler_identity: dict[str, Any]
) -> tuple[dict[str, Any], list[str], list[dict[str, list[Any]]]]:
    if not isinstance(artifact, dict) or set(artifact) != {
        "schema_version",
        "policy_type",
        "model",
        "feature_schema",
        "actions",
        "compiler_identity",
        "training_provenance",
    }:
        raise ValueError("policy artifact has an unsupported schema")
    if type(artifact.get("schema_version")) is not int or artifact["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported policy artifact version")
    if artifact.get("policy_type") != POLICY_TYPE:
        raise ValueError("unsupported policy type")
    expected_identity = _compiler_identity(compiler_identity)
    if _compiler_identity(artifact.get("compiler_identity")) != expected_identity:
        raise ValueError("compiler identity does not match the frozen policy")
    model, names, trees = _validate_model(artifact.get("model"))
    if artifact.get("feature_schema") != names:
        raise ValueError("policy feature schema differs from the frozen model")
    if artifact.get("actions") != list(ACTION_ORDERS):
        raise ValueError("policy action mapping differs from the canonical loop orders")
    if not isinstance(artifact.get("training_provenance"), dict):
        raise ValueError("policy training provenance must be an object")
    _json_value(artifact["training_provenance"], "training provenance")
    return model, names, trees


def _packet_rows(packet: Any, feature_names: list[str]) -> tuple[list[str], list[list[float]]]:
    if not isinstance(packet, dict):
        raise ValueError("packet must be an object")
    actions = packet.get("actions")
    if not isinstance(actions, list) or len(actions) != len(ACTION_ORDERS):
        raise ValueError("packet must contain exactly six actions")
    action_ids: list[str] = []
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get("id"), str):
            raise ValueError("each packet action must have a string id")
        action_ids.append(action["id"])
    if action_ids != list(ACTION_IDS):
        raise ValueError("packet action ids must be canonical 0-through-5 order")

    columns = packet.get("features")
    if not isinstance(columns, dict) or set(columns) != set(feature_names):
        raise ValueError("packet features must match the frozen feature schema exactly")
    rows = [[0.0] * len(feature_names) for _ in ACTION_ORDERS]
    for feature_index, name in enumerate(feature_names):
        values = columns[name]
        if not isinstance(values, (list, tuple)) or len(values) != len(ACTION_ORDERS):
            raise ValueError(f"packet feature {name!r} must align with all six actions")
        for action_index, value in enumerate(values):
            rows[action_index][feature_index] = _float32(
                value, f"packet feature {name!r}[{action_index}]"
            )
    return action_ids, rows


def choose(
    artifact: dict[str, Any], packet: dict[str, Any], compiler_identity: dict[str, Any]
) -> dict[str, Any]:
    """Replay a frozen forest using sklearn's float32 routing semantics."""
    model, feature_names, trees = _validate_artifact(artifact, compiler_identity)
    _action_ids, rows = _packet_rows(packet, feature_names)
    totals = [0.0] * len(ACTION_ORDERS)
    for tree in trees:
        left = tree["children_left"]
        right = tree["children_right"]
        feature = tree["feature"]
        threshold = tree["threshold"]
        value = tree["value"]
        for action_index, row in enumerate(rows):
            node = 0
            while left[node] != -1:
                node = left[node] if row[feature[node]] <= threshold[node] else right[node]
            totals[action_index] += value[node]
    scores = [total / len(trees) for total in totals]
    if any(not math.isfinite(score) for score in scores):
        raise ValueError("forest prediction is not finite")
    selected_index = min(range(len(scores)), key=lambda index: (scores[index], index))
    return {
        "action": ACTION_ORDERS[selected_index],
        "action_id": ACTION_IDS[selected_index],
        "scores": dict(zip(ACTION_ORDERS, scores, strict=True)),
    }


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is not supported")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--compiler-identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        artifact = _read_json(args.artifact)
        packet = _read_json(args.packet)
        compiler_identity = _read_json(args.compiler_identity)
        result = choose(artifact, packet, compiler_identity)
        result.update({
            "schema_version": 1,
            "artifact_sha256": canonical_sha256(artifact),
            "packet_sha256": canonical_sha256(packet),
            "compiler_identity": _compiler_identity(compiler_identity),
        })
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, allow_nan=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        print(f"harness policy replay failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"action": result["action"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
