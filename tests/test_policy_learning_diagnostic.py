"""Grouped-fold and aggregation checks for the development diagnostic."""

from __future__ import annotations

import math

from scripts import policy_learning_diagnostic as diagnostic


def _cases() -> list[dict]:
    return [
        {"case_id": f"recipe-{recipe}-layout-{layout}", "family": f"recipe-{recipe}"}
        for recipe in range(9)
        for layout in range(4)
    ]


def test_learning_curve_folds_keep_each_recipe_and_its_layouts_together() -> None:
    cases = _cases()
    first = diagnostic._fold_plan(cases, 20261019)
    assert first == diagnostic._fold_plan(cases, 20261019)
    assert set(first) == {f"recipe-{index}" for index in range(9)}

    for held_out, plan in first.items():
        assert plan["held_out_case_ids"] == [f"{held_out}-layout-{layout}" for layout in range(4)]
        prior: list[str] = []
        for count in diagnostic.RECIPE_COUNTS:
            train_recipes = plan["training_recipes_by_count"][str(count)]
            assert len(train_recipes) == count
            assert train_recipes[: len(prior)] == prior
            assert held_out not in train_recipes
            train_cases = diagnostic._cases_for_recipes(cases, train_recipes)
            assert len(train_cases) == count * 4
            assert {diagnostic._recipe(case) for case in train_cases} == set(train_recipes)
            assert not {case["case_id"] for case in train_cases} & set(plan["held_out_case_ids"])
            # Existing training creates every action row from a selected case,
            # so the complete layout group travels together into or out of fit.
            assert len(train_cases) * 6 == count * 4 * 6
            prior = train_recipes


def test_metric_gives_each_recipe_equal_weight_in_geometric_summary() -> None:
    predictions = [
        {"recipe": "a", "selected_loss_over_stock": 1.0, "oracle_loss_over_stock": 1.0}
        for _ in range(2)
    ] + [
        {"recipe": "b", "selected_loss_over_stock": 4.0, "oracle_loss_over_stock": 2.0}
    ]
    metrics = diagnostic._summarize_predictions(predictions)

    assert metrics["group_balanced_geometric_runtime_over_stock"] == 2.0
    assert math.isclose(metrics["group_balanced_oracle_geometric_runtime_over_stock"], math.sqrt(2.0))
    assert math.isclose(metrics["group_balanced_runtime_to_oracle"], math.sqrt(2.0))
