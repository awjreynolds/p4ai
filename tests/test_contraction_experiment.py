from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import contraction_experiment as contraction  # noqa: E402
from scripts import loop_experiment  # noqa: E402


def _source_features() -> dict[str, int]:
    return {
        "m": 16,
        "n": 32,
        "k": 8,
        "trip_count": 4096,
        "element_bits": 32,
        "arithmetic_bits": 32,
        "a_i_stride": 8,
        "a_k_stride": 1,
        "b_k_stride": 32,
        "b_j_stride": 1,
        "c_i_stride": 32,
        "c_j_stride": 1,
    }


def _gate_cases(design: dict, *, gap: bool = True) -> list[dict]:
    winners = {"rrc": "ikj", "ccr": "jik", "crr": "jki"}
    cases = []
    for group in design["families"]["discovery"]:
        for shape in design["shapes"]:
            case_id = f"{group}-m{shape['m']}-n{shape['n']}-k{shape['k']}"
            records = []
            for block in (1, 2):
                for block_round in range(1, design["benchmark_rounds"] + 1):
                    measurements = {}
                    for action in design["actions"]:
                        elapsed = 800 if gap and action == winners[group] else 1000
                        measurements[action] = {"elapsed_ns": elapsed}
                    records.append(
                        {
                            "round": (block - 1) * design["benchmark_rounds"] + block_round,
                            "block": block,
                            "block_round": block_round,
                            "measurements": measurements,
                        }
                    )
            cases.append(
                {
                    "partition": "discovery",
                    "family": group,
                    "case_id": case_id,
                    "source_features": {key: 0 for key in _source_features()},
                    "distinct_code_identities": 6,
                    "benchmark": {"round_records": records},
                }
            )
    return cases


def test_discovery_gate_requires_oracle_gap_over_constant_and_stride_control():
    design = json.loads(contraction.DEFAULT_DESIGN.read_text(encoding="utf-8"))

    result = contraction.discovery_gate(_gate_cases(design), design)

    assert result["passed"] is True
    assert result["best_constant"] == "ikj"
    assert result["stride_actions"]
    assert set(result["stride_actions"].values()) == {"ijk"}
    assert result["requirements"]["oracle_five_percent_better_than_constant"]
    assert result["requirements"]["oracle_five_percent_better_than_stride"]
    assert result["requirements"]["positive_in_two_groups_vs_constant"]
    assert result["requirements"]["positive_in_two_groups_vs_stride"]


def test_discovery_gate_stops_without_a_confirmed_gap():
    design = json.loads(contraction.DEFAULT_DESIGN.read_text(encoding="utf-8"))

    result = contraction.discovery_gate(_gate_cases(design, gap=False), design)

    assert result["passed"] is False
    assert result["requirements"]["oracle_five_percent_better_than_constant"] is False
    assert result["requirements"]["oracle_five_percent_better_than_stride"] is False


def test_stride_selector_uses_byte_strides_and_fixed_tie_order():
    features = _source_features()

    # j costs 4 + 2*4 = 12 bytes; the other axes cost more.
    assert contraction.stride_choice(features) == "ikj"
    assert contraction.stride_choice({}) == "ijk"


def test_packet_encodes_numeric_axes_and_strides_without_layout_or_outcome_names():
    case = {
        "case_id": "private-layout-case",
        "family": "rrc",
        "source_features": _source_features(),
    }

    packet = contraction._provider_packet(case)
    serialized = json.dumps(packet)

    assert "private-layout-case" not in serialized
    assert "rrc" not in serialized
    assert all(name not in serialized for name in contraction.ORDERS)
    assert len(packet["actions"]) == 6
    assert packet["features"]["outer_axis_id"] == [0, 0, 1, 1, 2, 2]
    assert packet["features"]["inner_axis_id"] == [2, 1, 2, 0, 1, 0]
    assert packet["features"]["inner_a_stride_bytes"] == [4, 0, 4, 32, 0, 32]
    assert set(packet["features"]) == set(packet["feature_descriptions"])
    assert all(
        type(value) in (int, float)
        for vector in packet["features"].values()
        for value in vector
    )


def test_model_phase_rejects_failed_discovery_before_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    design_path = contraction.DEFAULT_DESIGN
    validation_path = tmp_path / "validation.json"
    validation_path.write_text("{}", encoding="utf-8")
    output = tmp_path / "experiment"
    output.mkdir()
    (output / "discovery.json").write_text(
        json.dumps({"status": "stopped_no_confirmed_gap", "discovery_gate": {"passed": False}}),
        encoding="utf-8",
    )

    def unexpected_provider(*_args, **_kwargs):
        pytest.fail("providers must not run when discovery did not pass")

    monkeypatch.setattr(contraction, "_provider_choices", unexpected_provider)
    with pytest.raises(contraction.ContractionExperimentError, match="passed discovery"):
        contraction.run_models(design_path, validation_path, output)


def test_provider_cache_keeps_individual_responses_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = {"jev": 0, "codex": 0}

    def fake_jev(_packet):
        calls["jev"] += 1
        return {
            "provider": "jev",
            "chosen_action": "0",
            "confidence": 0.8,
            "latency_ms": 1.0,
        }

    def fake_codex(_packet):
        calls["codex"] += 1
        return {
            "provider": "codex",
            "chosen_action": "1",
            "reason": "numeric stride evidence",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    monkeypatch.setattr(loop_experiment.jev, "choose", fake_jev)
    monkeypatch.setattr(loop_experiment.codex_advisor, "choose", fake_codex)
    case = {"source_features": _source_features()}
    cache_path = tmp_path / "providers.json"

    result = contraction._provider_choices(case, cache_path)
    assert result["jev_action"] == "ijk"
    assert result["codex_action"] == "ikj"
    assert calls == {"jev": 1, "codex": 1}

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = next(iter(cache["packets"].values()))
    entry["responses"]["jev"]["chosen_action"] = "2"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    with pytest.raises(loop_experiment.LoopExperimentError, match="cached jev response changed"):
        contraction._provider_choices(case, cache_path)
    assert calls == {"jev": 1, "codex": 1}
