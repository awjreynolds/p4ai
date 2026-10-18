from __future__ import annotations

import json
import math

import pytest

from scripts.feedback_policy import fit_ridge, predict_ridge, score_packet


def test_fit_predict_and_json_roundtrip() -> None:
    rows = [
        {"features": {"width": width, "loads": width * 2}, "loss": math.exp(width)}
        for width in (0.0, 1.0, 2.0)
    ]
    model = fit_ridge(rows)
    restored = json.loads(json.dumps(model))

    assert restored["features"] == ["loads", "width"]
    assert predict_ridge(model, {"width": 1.0, "loads": 2.0}) == pytest.approx(
        predict_ridge(restored, {"width": 1.0, "loads": 2.0})
    )
    assert math.isfinite(predict_ridge(restored, {"width": 1.5, "loads": 3.0}))


def test_predict_extrapolates_using_training_only_ranges() -> None:
    model = fit_ridge(
        [
            {"features": {"x": 0.0}, "loss": 1.0},
            {"features": {"x": 1.0}, "loss": math.exp(2.0)},
        ]
    )

    assert model["ranges"]["x"] == {"min": 0.0, "max": 1.0}
    assert predict_ridge(model, {"x": 2.0}) > predict_ridge(
        model, {"x": 1.0}
    )


def test_training_predictions_preserve_mean_log_loss_with_intercept() -> None:
    rows = [
        {"features": {"x": x}, "loss": math.exp(log_loss)}
        for x, log_loss in ((10.0, 0.0), (20.0, 1.0), (40.0, 4.0))
    ]
    model = fit_ridge(rows)
    predictions = [predict_ridge(model, row["features"]) for row in rows]

    assert model["means"]["x"] != 0.0
    assert model["coefficients"]["x"] != 0.0
    assert sum(predictions) / len(predictions) == pytest.approx(
        sum(math.log(row["loss"]) for row in rows) / len(rows)
    )


def test_constant_and_duplicate_features_are_stable() -> None:
    constant = fit_ridge(
        [
            {"features": {"x": 7.0}, "loss": 1.0},
            {"features": {"x": 7.0}, "loss": 4.0},
            {"features": {"x": 7.0}, "loss": 16.0},
        ]
    )
    assert constant["coefficients"]["x"] == 0.0
    assert predict_ridge(constant, {"x": -1e9}) == pytest.approx(
        (math.log(1) + math.log(4) + math.log(16)) / 3
    )

    duplicate = fit_ridge(
        [
            {"features": {"first": x, "second": x}, "loss": math.exp(x)}
            for x in (0.0, 1.0, 2.0)
        ]
    )
    first = duplicate["coefficients"]["first"]
    second = duplicate["coefficients"]["second"]
    assert math.isfinite(first) and math.isfinite(second)
    assert first == pytest.approx(second)


def test_packet_chooses_lowest_predicted_loss_with_numeric_tie_break() -> None:
    model = fit_ridge(
        [
            {"features": {"cost": 0.0}, "loss": 1.0},
            {"features": {"cost": 1.0}, "loss": math.exp(2.0)},
        ]
    )
    packet = {
        "features": {"cost": [0.8, 0.1, 0.1]},
        "actions": [{"id": "3"}, {"id": "0"}, {"id": "2"}],
    }

    result = score_packet(model, packet)

    assert result["action_id"] == "0"
    assert list(result["predicted_log_losses"]) == ["0", "2", "3"]
    assert result["predicted_log_losses"]["0"] == pytest.approx(
        result["predicted_log_losses"]["2"]
    )


def test_packet_breaks_equal_scores_by_numeric_id_not_input_order() -> None:
    model = fit_ridge(
        [
            {"features": {"constant": 4.0}, "loss": 2.0},
            {"features": {"constant": 4.0}, "loss": 8.0},
        ]
    )
    result = score_packet(
        model,
        {
            "features": {"constant": [100.0, -100.0]},
            "actions": [{"id": "10"}, {"id": "2"}],
        },
    )

    assert result["action_id"] == "2"
    assert list(result["predicted_log_losses"]) == ["2", "10"]


@pytest.mark.parametrize(
    ("rows", "alpha"),
    [
        ([], 1.0),
        ([{"features": {}, "loss": 1.0}], 1.0),
        ([{"features": {"x": 0.0}, "loss": 0.0}], 1.0),
        ([{"features": {"x": 0.0}, "loss": math.inf}], 1.0),
        (
            [
                {"features": {"x": 0.0}, "loss": 1.0},
                {"features": {"y": 1.0}, "loss": 2.0},
            ],
            1.0,
        ),
        ([{"features": {"x": True}, "loss": 1.0}], 1.0),
        ([{"features": {"x": 0.0}, "loss": 1.0}], 0.0),
        ([{"features": {"x": 0.0}, "loss": 1.0}], math.nan),
    ],
)
def test_fit_rejects_invalid_training_data(
    rows: list[dict[str, object]], alpha: float
) -> None:
    with pytest.raises(ValueError):
        fit_ridge(rows, alpha=alpha)


def test_predict_rejects_malformed_model_and_feature_mapping() -> None:
    model = fit_ridge([{"features": {"x": 0.0}, "loss": 1.0}])
    with pytest.raises(ValueError):
        predict_ridge({"schema_version": 2}, {"x": 0.0})
    with pytest.raises(ValueError):
        predict_ridge(model, {"other": 0.0})
    with pytest.raises(ValueError):
        predict_ridge(model, {"x": math.nan})


def test_packet_rejects_misaligned_or_duplicate_actions() -> None:
    model = fit_ridge([{"features": {"x": 0.0}, "loss": 1.0}])
    with pytest.raises(ValueError):
        score_packet(model, {"features": {"x": [1.0]}, "actions": []})
    with pytest.raises(ValueError):
        score_packet(
            model,
            {"features": {"x": [1.0]}, "actions": [{"id": "1"}, {"id": "1"}]},
        )
    with pytest.raises(ValueError):
        score_packet(
            model,
            {"features": {"x": [1.0]}, "actions": [{"id": "action-1"}]},
        )
