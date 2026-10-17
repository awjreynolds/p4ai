from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_advisor  # noqa: E402
import experiment  # noqa: E402
import jev  # noqa: E402


def _observation(action_ids=("2", "5")):
    actions = [
        {"id": action_id, "description": f"eligible candidate {action_id}"}
        for action_id in action_ids
    ]
    return {
        "context": "pressure_poly",
        "observation": 7,
        "features_sha256": "feature-digest",
        "actions": actions,
        "provider_packet": {
            "task": "Pick an eligible candidate.",
            "features": {"cost": [3.0, 2.0][: len(actions)]},
            "feature_descriptions": {"cost": "Lower is better"},
            "actions": actions,
        },
    }


def _jev_response(action="5", confidence=0.8):
    return {
        "provider": "jev",
        "chosen_action": action,
        "probabilities": {"2": 0.1, "5": 0.9},
        "confidence": confidence,
        "confidence_type": "distribution-derived",
        "confidence_semantics": "not correctness probability",
        "model": "jev-1.14.0",
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "latency_ms": 4.0,
    }


def _codex_response(action="2"):
    return {
        "provider": "codex",
        "chosen_action": action,
        "reason": "The supplied cost is lower.",
        "model": "gpt-6-luna",
        "reasoning_effort": "max",
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "latency_ms": 5.0,
    }


def test_provider_called_once_and_later_decisions_use_first_allowed():
    calls = []

    def choose(packet):
        calls.append(packet)
        return _jev_response()

    policy = experiment.OneInterventionPolicy("jev", jev_choose=choose)
    first = policy(_observation())
    later = policy({**_observation(), "observation": 8})

    assert first["chosen_action"] == "5"
    assert later == 2
    assert len(calls) == 1
    assert calls[0] == policy.first_packet
    assert policy.report()["selected_action"] == 5
    assert policy.report()["selection_source"] == "jev"


def test_singleton_state_skips_provider_until_multiple_actions_exist():
    calls = []
    policy = experiment.OneInterventionPolicy(
        "codex", codex_choose=lambda packet: calls.append(packet) or _codex_response()
    )

    assert policy(_observation(("2",))) == 2
    assert calls == []
    assert policy(_observation())["chosen_action"] == "2"
    assert len(calls) == 1
    assert policy.singleton_skips == 1


def test_provider_action_outside_menu_is_rejected_and_uses_mechanical_fallback():
    policy = experiment.OneInterventionPolicy(
        "jev", jev_choose=lambda _packet: _jev_response(action="99")
    )

    assert policy(_observation()) == 2
    assert policy.report()["selected_action"] == 2
    assert policy.report()["selection_source"] == "mechanical_fallback"
    assert policy.report()["provider_attempts"] == [
        {"provider": "jev", "status": "rejected", "reason": "invalid_response"}
    ]


def test_cascade_routes_low_confidence_to_codex_but_accepts_threshold_boundary():
    jev_calls = []
    codex_calls = []
    low = experiment.OneInterventionPolicy(
        "cascade",
        0.5,
        jev_choose=lambda packet: (
            jev_calls.append(packet) or _jev_response(confidence=0.49)
        ),
        codex_choose=lambda packet: codex_calls.append(packet) or _codex_response(),
    )
    selected_low = low(_observation())
    assert selected_low["chosen_action"] == "2"
    assert len(jev_calls) == len(codex_calls) == 1
    assert (
        low.report()["provider_attempts"][0]["status"]
        == "available_routed_low_confidence"
    )

    codex_calls.clear()
    boundary = experiment.OneInterventionPolicy(
        "cascade",
        0.5,
        jev_choose=lambda _packet: _jev_response(confidence=0.5),
        codex_choose=lambda packet: codex_calls.append(packet) or _codex_response(),
    )
    assert boundary(_observation())["chosen_action"] == "5"
    assert codex_calls == []


def test_unavailable_providers_leave_run_unresolved_and_use_first_allowed():
    jev_calls = []
    codex_calls = []

    def jev_unavailable(packet):
        jev_calls.append(packet)
        raise jev.JevUnavailable("missing_api_key", "Jev API key is unavailable")

    def codex_unavailable(packet):
        codex_calls.append(packet)
        raise codex_advisor.CodexUnavailable("missing_cli", "Codex CLI is unavailable")

    policy = experiment.OneInterventionPolicy(
        "cascade", jev_choose=jev_unavailable, codex_choose=codex_unavailable
    )

    assert policy(_observation()) == 2
    assert len(jev_calls) == len(codex_calls) == 1
    assert policy.report()["selection_source"] == "mechanical_fallback"
    assert [item["status"] for item in policy.report()["provider_attempts"]] == [
        "unavailable",
        "unavailable",
    ]


def test_incomplete_validation_blocks_before_creating_experiment_artifacts(tmp_path):
    validation_report = tmp_path / "validation.json"
    validation_report.write_text('{"status":"incomplete"}', encoding="utf-8")
    artifacts_dir = tmp_path / "experiment"

    with pytest.raises(experiment.ExperimentError, match="validation has not passed"):
        experiment.run_experiment(
            validation_report,
            method="jev",
            artifacts_dir=artifacts_dir,
        )

    assert not artifacts_dir.exists()
