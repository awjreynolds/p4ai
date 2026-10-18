"""Checks for offline replay of an exported random-forest policy."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import harness_policy as policy

IDENTITY = {"sha256": "a" * 64, "target": "arm64-apple-darwin/apple-m3"}
PROVENANCE = {"training_rows_sha256": "b" * 64, "training_rows": 12}
ROOT = Path(__file__).resolve().parents[1]


def _model() -> dict:
    return {
        "schema_version": 1,
        "library": "scikit-learn",
        "library_version": "test",
        "parameters": {"n_estimators": 1, "n_jobs": 1},
        "features": ["x"],
        "training_rows": 12,
        "training_targets": "log(runtime/canonical stock)",
        "estimators": [{
            "children_left": [1, -1, -1],
            "children_right": [2, -1, -1],
            "feature": [0, -2, -2],
            "threshold": [0.5, -2.0, -2.0],
            "value": [0.0, 3.0, 1.0],
        }],
    }


def _packet(values: list[int | float] | None = None) -> dict:
    return {
        "features": {"x": values if values is not None else [1, 0, 1, 0, 1, 0]},
        "actions": [{"id": str(index), "description": order} for index, order in enumerate(policy.ACTION_ORDERS)],
    }


def _artifact(model: dict | None = None) -> dict:
    return policy.make_artifact(model or _model(), IDENTITY, PROVENANCE)


def test_offline_choice_matches_hand_computed_tree_scores() -> None:
    result = policy.choose(_artifact(), _packet(), IDENTITY)

    assert result == {
        "action": "ijk",
        "action_id": "0",
        "scores": {order: (1.0 if index % 2 == 0 else 3.0)
                   for index, order in enumerate(policy.ACTION_ORDERS)},
    }


def test_exported_sklearn_forest_prediction_matches_offline_replay() -> None:
    sklearn = pytest.importorskip("sklearn")
    np = pytest.importorskip("numpy")
    from sklearn.ensemble import RandomForestRegressor

    features = ["x", "y"]
    training_x = np.asarray([
        [16_777_216, 0], [16_777_218, 0], [16_777_216, 1], [16_777_218, 1],
        [16_777_216, 2], [16_777_218, 2], [16_777_216, 3], [16_777_218, 3],
    ], dtype=np.float64)
    targets = np.asarray([0.0, 4.0, 1.0, 5.0, 0.5, 4.5, 1.5, 5.5])
    forest = RandomForestRegressor(
        n_estimators=7, max_depth=3, random_state=17, n_jobs=1
    ).fit(training_x, targets)
    exported = {
        "schema_version": 1,
        "library": "scikit-learn",
        "library_version": sklearn.__version__,
        "parameters": forest.get_params(),
        "features": features,
        "training_rows": len(training_x),
        "training_targets": "log(runtime/canonical stock)",
        "estimators": [
            {
                "children_left": tree.tree_.children_left.tolist(),
                "children_right": tree.tree_.children_right.tolist(),
                "feature": tree.tree_.feature.tolist(),
                "threshold": tree.tree_.threshold.tolist(),
                "value": tree.tree_.value[:, 0, 0].tolist(),
            }
            for tree in forest.estimators_
        ],
    }
    packet = {
        "features": {
            "x": [16_777_217, 16_777_216, 16_777_218, 16_777_217, 16_777_218, 16_777_216],
            "y": [0, 0, 1, 1, 2, 3],
        },
        "actions": [{"id": str(index)} for index in range(6)],
    }
    expected = forest.predict(np.asarray([
        [packet["features"][name][index] for name in features]
        for index in range(6)
    ], dtype=np.float64))

    result = policy.choose(_artifact(exported), packet, IDENTITY)

    assert list(result["scores"].values()) == pytest.approx(expected.tolist(), abs=1e-12)
    expected_index = min(range(6), key=lambda index: (expected[index], index))
    assert result["action"] == policy.ACTION_ORDERS[expected_index]
    assert result["action_id"] == str(expected_index)


def test_compiler_identity_and_feature_schema_are_bound_to_artifact() -> None:
    artifact = _artifact()
    with pytest.raises(ValueError, match="compiler identity"):
        policy.choose(artifact, _packet(), {"sha256": "c" * 64, "target": IDENTITY["target"]})

    artifact["feature_schema"] = ["other"]
    with pytest.raises(ValueError, match="feature schema"):
        policy.choose(artifact, _packet(), IDENTITY)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("cycle", "cycle"),
        ("unreachable", "unreachable"),
        ("threshold", "finite number"),
    ],
)
def test_malformed_or_tampered_tree_is_rejected(mutation: str, message: str) -> None:
    artifact = _artifact()
    tree = artifact["model"]["estimators"][0]
    if mutation == "cycle":
        tree["children_left"][0] = 0
    elif mutation == "unreachable":
        for key, value in (("children_left", -1), ("children_right", -1),
                           ("feature", -2), ("threshold", -2.0), ("value", 2.0)):
            tree[key].append(value)
    else:
        tree["threshold"][0] = float("nan")

    with pytest.raises(ValueError, match=message):
        policy.choose(artifact, _packet(), IDENTITY)


@pytest.mark.parametrize(
    "packet",
    [
        {"features": {"x": [1] * 6}, "actions": [{"id": str(i)} for i in range(5)]},
        {"features": {"x": [1] * 6}, "actions": [{"id": str(i)} for i in range(5)] + [{"id": "8"}]},
        {"features": {"x": [1, 1, True, 1, 1, 1]}, "actions": [{"id": str(i)} for i in range(6)]},
        {"features": {"x": [1, 1, float("inf"), 1, 1, 1]}, "actions": [{"id": str(i)} for i in range(6)]},
        {"features": {"x": [1e100] * 6}, "actions": [{"id": str(i)} for i in range(6)]},
        {"features": {"x": [1] * 6, "extra": [0] * 6}, "actions": [{"id": str(i)} for i in range(6)]},
        {"features": {"x": [1] * 6}, "actions": [{"id": str(i)} for i in reversed(range(6))]},
    ],
)
def test_invalid_packets_fail_closed(packet: dict) -> None:
    with pytest.raises(ValueError):
        policy.choose(_artifact(), packet, IDENTITY)


def test_cli_replay_runs_without_site_packages(tmp_path: Path) -> None:
    artifact = _artifact()
    packet = _packet()
    artifact_path = tmp_path / "policy.json"
    packet_path = tmp_path / "packet.json"
    identity_path = tmp_path / "compiler.json"
    output_path = tmp_path / "replay.json"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    identity_path.write_text(json.dumps(IDENTITY), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts" / "harness_policy.py"),
            "--artifact", str(artifact_path),
            "--packet", str(packet_path),
            "--compiler-identity", str(identity_path),
            "--output", str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    replay = json.loads(output_path.read_text(encoding="utf-8"))
    assert replay["action"] == "ijk"
    assert replay["artifact_sha256"] == policy.canonical_sha256(artifact)
    assert replay["packet_sha256"] == policy.canonical_sha256(packet)
