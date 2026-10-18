from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import loop_experiment as loop  # noqa: E402


def _gate_cases(design: dict, *, gap: bool = True) -> list[dict]:
    families = design["families"]["discovery"]
    winners = ("vf2", "vf4", "vf8")
    cases = []
    for family_index, family in enumerate(families):
        winner = winners[family_index]
        for trip_count in design["trip_counts"]:
            case_id = f"{family}-n{trip_count}"
            records = []
            for block in (1, 2):
                for block_round in range(1, design["benchmark_rounds"] + 1):
                    measurements = {}
                    for action in design["actions"]:
                        elapsed = 800 if gap and action == winner else 1000
                        measurements[action] = {"elapsed_ns": elapsed}
                    global_round = (block - 1) * design[
                        "benchmark_rounds"
                    ] + block_round
                    records.append(
                        {
                            "round": global_round,
                            "block": block,
                            "block_round": block_round,
                            "measurements": measurements,
                        }
                    )
            cases.append(
                {
                    "partition": "discovery",
                    "family": family,
                    "trip_count": trip_count,
                    "case_id": case_id,
                    "distinct_code_identities": 8,
                    "benchmark": {"round_records": records},
                }
            )
    return cases


def test_discovery_gate_freezes_first_block_and_confirms_gap_in_second():
    design = json.loads(loop.DEFAULT_DESIGN.read_text(encoding="utf-8"))
    cases = _gate_cases(design)
    frozen = loop._fit_discovery(cases, design["actions"], design["benchmark_rounds"])

    result = loop.discovery_gate(cases, design, frozen_fit=frozen)

    assert result["passed"] is True
    assert result["confirmation_rounds"] == [12, 22]
    assert set(result["case_oracle_actions"].values()) == {"vf2", "vf4", "vf8"}
    assert result["best_constant"] == "vf2"
    assert (
        result["confirmation_metrics_oracle_vs_constant"]["geometric_mean_ratio"] < 0.95
    )


def test_discovery_gate_stops_when_no_confirmed_quality_gap_exists():
    design = json.loads(loop.DEFAULT_DESIGN.read_text(encoding="utf-8"))

    result = loop.discovery_gate(_gate_cases(design, gap=False), design)

    assert result["passed"] is False
    assert result["requirements"]["oracle_at_least_discovery_gap_better"] is False
    assert result["requirements"]["gain_beyond_noise_floor"] is False


def test_provider_packet_contains_numeric_candidates_without_case_identity():
    case = {
        "case_id": "private-case-label",
        "family": "private-family-label",
        "source_features": {
            "element_bits": 32,
            "arithmetic_bits": 64,
            "trip_count": 257,
            "loads": 2,
            "stores": 0,
            "multiplies": 1,
            "shifts": 0,
            "branches": 0,
            "reduction": 1,
            "stride": 1,
        },
    }

    packet = loop._provider_packet(case, list(loop.ACTION_BY_NAME))
    serialized = json.dumps(packet)

    assert "private-case-label" not in serialized
    assert "private-family-label" not in serialized
    assert "target_cpu" not in packet["features"]
    assert "vector_isa" not in packet["features"]
    assert set(packet["features"]) == set(packet["feature_descriptions"])
    assert all(
        type(value) in (int, float)
        for vector in packet["features"].values()
        for value in vector
    )
    assert packet["features"]["vector_register_bits"] == [128] * 8


def test_provider_cache_retries_only_failed_provider_and_detects_response_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    packet = {
        "task": "Choose one.",
        "features": {"trip_count": [257, 257]},
        "feature_descriptions": {"trip_count": "loop trip count"},
        "actions": [
            {"id": "0", "description": "stock"},
            {"id": "1", "description": "vf4"},
        ],
    }
    calls = {"jev": 0, "codex": 0}

    def jev_choose(_packet):
        calls["jev"] += 1
        return {
            "provider": "jev",
            "chosen_action": "99" if calls["jev"] == 1 else "0",
            "confidence": 0.7,
        }

    def codex_choose(_packet):
        calls["codex"] += 1
        return {"provider": "codex", "chosen_action": "1", "reason": "candidate"}

    monkeypatch.setattr(loop.jev, "choose", jev_choose)
    monkeypatch.setattr(loop.codex_advisor, "choose", codex_choose)
    cache_path = tmp_path / "providers.json"

    with pytest.raises(loop.LoopExperimentError, match="provider decisions incomplete"):
        loop.query_provider_cache(packet, cache_path)
    result = loop.query_provider_cache(packet, cache_path)
    assert set(result["responses"]) == {"jev", "codex"}
    assert calls == {"jev": 2, "codex": 1}
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "rejected",
        "available",
        "available",
    ]
    assert loop.query_provider_cache(packet, cache_path) == result
    assert calls == {"jev": 2, "codex": 1}

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = cache["packets"][loop._packet_fingerprint(packet)]
    entry["responses"]["jev"]["chosen_action"] = "1"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    with pytest.raises(loop.LoopExperimentError, match="cached jev response changed"):
        loop.query_provider_cache(packet, cache_path)


def test_models_phase_refuses_failed_discovery_before_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    design_path = loop.DEFAULT_DESIGN
    validation = tmp_path / "validation.json"
    validation.write_text("{}", encoding="utf-8")
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(
        json.dumps(
            {
                "provenance": loop._provenance(design_path, validation),
                "status": "stopped_gate_failed",
                "discovery_gate": {"passed": False},
            }
        ),
        encoding="utf-8",
    )

    def unexpected(_packet):
        pytest.fail("provider must not be called after the discovery gate fails")

    monkeypatch.setattr(loop.jev, "choose", unexpected)
    monkeypatch.setattr(loop.codex_advisor, "choose", unexpected)
    with pytest.raises(loop.LoopExperimentError, match="opportunity gate failed"):
        loop.run_models(design_path, validation, tmp_path / "out", discovery_path)
