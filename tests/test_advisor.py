from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ADVISOR_PATH = Path(__file__).resolve().parents[1] / "scripts/advisor.py"
ADVISOR_SPEC = importlib.util.spec_from_file_location("p4ai_advisor", ADVISOR_PATH)
assert ADVISOR_SPEC is not None and ADVISOR_SPEC.loader is not None
advisor = importlib.util.module_from_spec(ADVISOR_SPEC)
sys.modules[ADVISOR_SPEC.name] = advisor
ADVISOR_SPEC.loader.exec_module(advisor)


def _decision(*, identity: dict[str, str] | None = None) -> dict[str, object]:
    actions = [
        {"id": "0", "description": "first eligible"},
        {"id": "2", "description": "second eligible"},
    ]
    return {
        "run_identity": identity,
        "context": "eviction",
        "observation": 4,
        "actions": actions,
        "features_sha256": "features-digest",
        "eligible_actions": [0, 2],
        "action_index": 2,
    }


def test_feature_fingerprint_is_stable_and_distinguishes_float_values() -> None:
    features = {"z": [1.25, 0], "a": [3]}

    assert advisor.feature_fingerprint(features) == advisor.feature_fingerprint(
        {"a": [3], "z": [1.25, 0]}
    )
    assert advisor.feature_fingerprint(features) != advisor.feature_fingerprint(
        {"a": [3], "z": [1.5, 0]}
    )


def test_scalar_feature_is_broadcast_and_candidate_features_are_filtered() -> None:
    packet_features = advisor._eligible_feature_vectors(
        {
            "mask": [1, 0, 1, 0],
            "progress": [0.5],
            "register_cost": [10, 20, 30, 40],
        },
        [0, 2],
    )

    assert packet_features == {"progress": [0.5, 0.5], "register_cost": [10, 30]}


def test_final_candidate_slot_is_described_as_no_eviction_sentinel() -> None:
    actions = advisor._describe_actions([0, 2, 3], candidate_count=4)

    assert (
        actions[0]["description"]
        == "Evict the physical register at candidate column 0."
    )
    assert (
        actions[1]["description"]
        == "Evict the physical register at candidate column 2."
    )
    assert actions[2] == {
        "id": "3",
        "description": "Do not evict a physical register; leave the current virtual register for allocator continuation.",
    }
    assert "final candidate position is a sentinel action" in advisor.ADVISOR_TASK


def test_bad_candidate_feature_shape_is_rejected() -> None:
    with pytest.raises(
        advisor.AdvisorError, match="feature 'bad' has 2 values for 3 candidate columns"
    ):
        advisor._eligible_feature_vectors({"mask": [1, 0, 1], "bad": [4, 5]}, [0, 2])


def test_replay_checks_run_identity_state_and_full_trace() -> None:
    identity = {"ir_sha256": "abc", "compiler_args": ["-O2"]}
    saved = _decision(identity=identity)
    replay = advisor.replay_policy([saved])
    current = {
        "run_identity": identity,
        "context": saved["context"],
        "observation": saved["observation"],
        "actions": saved["actions"],
        "features_sha256": saved["features_sha256"],
    }

    assert replay(current) == 2
    replay.finalize()

    mismatch = advisor.replay_policy([saved])
    with pytest.raises(advisor.ReplayMismatch, match="run identity"):
        mismatch({**current, "run_identity": {"ir_sha256": "different"}})

    incomplete = advisor.replay_policy([saved, {**saved, "observation": 5}])
    incomplete(current)
    with pytest.raises(advisor.ReplayMismatch, match="consumed 1 of 2"):
        incomplete.finalize()


def test_host_policy_cannot_choose_a_masked_column() -> None:
    with pytest.raises(advisor.AdvisorError, match="selected ineligible action 1"):
        advisor._resolve_policy_result({"chosen_action": 1}, [0, 2])


def test_candidate_mask_must_have_eligible_columns() -> None:
    with pytest.raises(advisor.AdvisorError, match="no eligible eviction candidates"):
        advisor._legal_actions({"mask": [0, 0]})
