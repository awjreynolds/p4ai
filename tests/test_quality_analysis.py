from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import quality_analysis  # noqa: E402, I001


def test_nearest_neighbor_uses_training_ranges_and_returns_menu_action():
    model = quality_analysis.fit_nearest_neighbor(
        [
            {"features": {"pressure": 0, "cost": 0}, "loss": 2},
            {"features": {"pressure": 10, "cost": 100}, "loss": 0.5},
        ]
    )
    model = json.loads(json.dumps(model))
    actions = [{"id": "17", "description": "first"}, {"id": "3", "description": "second"}]

    result = quality_analysis.score_actions(
        model,
        {"pressure": [0.5, 9], "cost": [10, 95]},
        actions,
    )

    assert model["ranges"] == {
        "cost": {"min": 0.0, "max": 100.0, "scale": 100.0},
        "pressure": {"min": 0.0, "max": 10.0, "scale": 10.0},
    }
    assert result["action"] == actions[1]
    assert result["action_id"] == "3"
    assert result["predicted_loss"] == 0.5
    assert result["predictions"][0]["training_example_index"] == 0
    assert result["predictions"][1]["training_example_index"] == 1


def test_nearest_neighbor_breaks_equal_losses_by_menu_order():
    model = quality_analysis.fit_nearest_neighbor(
        [{"features": {"cost": 1}, "loss": 1.0}]
    )

    result = quality_analysis.score_actions(
        model,
        {"cost": [1, 1]},
        [{"id": "later-in-numbering"}, {"id": "earlier-in-numbering"}],
    )

    assert result["action_id"] == "later-in-numbering"


def test_nearest_neighbor_rejects_inconsistent_training_and_candidate_schema():
    with pytest.raises(ValueError, match="same feature names"):
        quality_analysis.fit_nearest_neighbor(
            [
                {"features": {"cost": 1}, "loss": 1},
                {"features": {"pressure": 1}, "loss": 1},
            ]
        )
    model = quality_analysis.fit_nearest_neighbor(
        [{"features": {"cost": 1}, "loss": 1}]
    )
    with pytest.raises(ValueError, match="match the fitted"):
        quality_analysis.score_actions(model, {"pressure": [1]}, [{"id": "0"}])


def test_calibration_respects_two_of_eight_budget_and_selects_threshold():
    rows = [
        {
            "confidence": (index + 1) / 10,
            "jev_ns": 100,
            "codex_ns": 70 if index < 2 else 110,
        }
        for index in range(8)
    ]

    result = quality_analysis.calibrate_threshold(rows)

    assert result["chosen"]["mode"] == "threshold"
    assert result["chosen"]["threshold"] == 0.25
    assert result["chosen"]["escalations"] == 2
    assert result["chosen"]["within_budget"] is True
    assert next(c for c in result["candidates"] if c["mode"] == "always")["within_budget"] is False
    assert quality_analysis.select_route(result["chosen"], 0.1) is True
    assert quality_analysis.select_route(result["chosen"], 0.3) is False


def test_calibration_uses_median_replicates_and_treats_small_gains_as_ties():
    rows = [
        {
            "confidence": confidence,
            "jev_ns": [100, 110, 90],
            "codex_ns": [99, 120, 80],
        }
        for confidence in (0.1, 0.2, 0.3, 0.4)
    ]

    result = quality_analysis.calibrate_threshold(rows, max_escalation_fraction=1)

    assert result["chosen"]["mode"] == "never"
    threshold = next(
        item for item in result["candidates"]
        if item["mode"] == "threshold" and item["threshold"] == 1.0
    )
    assert threshold["raw_average_log_ratio"] < 0
    assert threshold["average_log_ratio"] == 0
    assert quality_analysis.select_route({"mode": "always"}, 1) is True
    assert quality_analysis.select_route({"mode": "never"}, 0) is False


def _report(family: str, state: str, ratios: list[float]) -> dict:
    return {
        "family": family,
        "state": state,
        "round_records": [
            {
                "round": index,
                "measurements": {
                    "jev": {"elapsed_ns": 100},
                    "codex": {"elapsed_ns": ratio * 100},
                },
            }
            for index, ratio in enumerate(ratios, start=1)
        ],
    }


def test_paired_metrics_aggregates_rounds_states_and_families_equally():
    metrics = quality_analysis.paired_metrics(
        [
            _report("family_a", "state_1", [0.5, 2.0]),
            _report("family_a", "state_2", [1.0, 1.0]),
            _report("family_a", "state_3", [1.0, 1.0]),
            _report("family_a", "state_4", [1.0, 1.0]),
            _report("family_b", "state_1", [1.1, 1.1, 1.1]),
        ]
    )

    assert metrics["families"]["family_a"]["geometric_mean_ratio"] == pytest.approx(1.0)
    assert metrics["families"]["family_b"]["geometric_mean_ratio"] == pytest.approx(1.1)
    assert metrics["geometric_mean_ratio"] == pytest.approx(math.sqrt(1.1))
    assert metrics["wins_over_2pct"] == 0
    assert metrics["losses_over_2pct"] == 1
    assert metrics["within_2pct"] == 1
    assert metrics["median_absolute_deviation_log_ratio"] == 0
    assert metrics["maximum_state_mad_log_ratio"] == pytest.approx(math.log(2))
    assert metrics["families"]["family_a"]["median_absolute_deviation_log_ratio"] == 0
    assert metrics["families"]["family_a"]["maximum_state_mad_log_ratio"] == pytest.approx(math.log(2))
    assert metrics["no_difference_state_count"] == 3
    assert metrics["all_identical_sample_state_count"] == 4
    assert metrics["families"]["family_a"]["no_difference_state_count"] == 3
    assert metrics["families"]["family_b"]["all_identical_sample_state_count"] == 1
    assert metrics["families"]["family_a"]["states"]["state_1"]["paired_rounds"] == 2
    assert metrics["families"]["family_a"]["states"]["state_1"]["median_absolute_deviation_log_ratio"] > 0
    assert metrics["families"]["family_a"]["states"]["state_2"]["no_difference"] is True
    assert metrics["families"]["family_b"]["states"]["state_1"]["all_samples_identical"] is True


def test_paired_metrics_accepts_raw_benchmark_round_records():
    result = quality_analysis.paired_metrics(
        {
            "round_records": [
                {
                    "round": 1,
                    "measurements": {
                        "reference": {"elapsed_ns": 100},
                        "candidate": {"elapsed_ns": 90},
                    },
                }
            ]
        },
        reference="reference",
        candidate="candidate",
    )

    assert result["geometric_mean_ratio"] == pytest.approx(0.9)
    assert result["within_2pct"] == 0
    assert result["wins_over_2pct"] == 1
