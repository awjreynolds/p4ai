"""Development-only grouped learning-curve diagnostic on frozen feedback data.

This script reads the published compiler-feedback-v1 evidence and compares
source-only and source-plus-compiler features with the already-pinned ridge and
random-forest policies. It performs no builds, native timings, or provider calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from . import compiler_feedback_experiment as feedback
    from . import contraction_experiment as contraction
    from . import feedback_policy
    from . import feedback_replication_experiment as replication
except ImportError:  # Running as ``python scripts/policy_learning_diagnostic.py``.
    import compiler_feedback_experiment as feedback
    import contraction_experiment as contraction
    import feedback_policy
    import feedback_replication_experiment as replication


SOURCE_DESIGN = ROOT / "experiments" / "compiler-feedback-v1.json"
REPLICATION_DESIGN = ROOT / "experiments" / "feedback-replication-v1.json"
SOURCE_BUNDLE = ROOT / "docs" / "results" / "compiler-feedback-v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "policy-learning-diagnostic-v1" / "report.json"
RECIPE_COUNTS = (2, 4, 8)
FEATURE_SETS = ("source30", "compiler42")
MODEL_FAMILIES = ("ridge", "random_forest")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _recipe(case: dict[str, Any]) -> str:
    value = case.get("family", case.get("group"))
    if not isinstance(value, str) or not value:
        raise ValueError(f"case {case.get('case_id')} has no recipe group")
    return value


def _fold_plan(cases: list[dict[str, Any]], seed: int) -> dict[str, dict[str, Any]]:
    """Make nested deterministic recipe subsets for leave-one-recipe-out folds."""
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    grouped: dict[str, list[str]] = {}
    for case in cases:
        grouped.setdefault(_recipe(case), []).append(case["case_id"])
    if len(grouped) != 9 or any(len(ids) != 4 for ids in grouped.values()):
        raise ValueError("diagnostic requires the original nine recipes with four layouts each")

    plans = {}
    for held_out in sorted(grouped):
        candidates = [name for name in sorted(grouped) if name != held_out]
        ordered = sorted(
            candidates,
            key=lambda name: (
                hashlib.sha256(f"policy-learning-diagnostic-v1:{seed}:{held_out}:{name}".encode()).hexdigest(),
                name,
            ),
        )
        plans[held_out] = {
            "held_out_case_ids": sorted(grouped[held_out]),
            "training_recipe_order": ordered,
            "training_recipes_by_count": {
                str(count): ordered[:count] for count in RECIPE_COUNTS
            },
        }
    return plans


def _cases_for_recipes(
    cases: list[dict[str, Any]], recipe_names: list[str] | tuple[str, ...]
) -> list[dict[str, Any]]:
    selected = set(recipe_names)
    return [case for case in cases if _recipe(case) in selected]


def _action_loss(case: dict[str, Any], action: str) -> float:
    baseline = contraction._block_median(case, "stock", 1)
    return contraction._block_median(case, action, 1) / baseline


def _summarize_predictions(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    if not predictions:
        raise ValueError("cannot summarize an empty prediction set")
    by_recipe: dict[str, list[dict[str, Any]]] = {}
    for row in predictions:
        by_recipe.setdefault(row["recipe"], []).append(row)
    recipe_metrics = {}
    for recipe, rows in sorted(by_recipe.items()):
        policy_gm = math.exp(sum(math.log(row["selected_loss_over_stock"]) for row in rows) / len(rows))
        oracle_gm = math.exp(sum(math.log(row["oracle_loss_over_stock"]) for row in rows) / len(rows))
        recipe_metrics[recipe] = {
            "cases": len(rows),
            "policy_geometric_runtime_over_stock": policy_gm,
            "oracle_geometric_runtime_over_stock": oracle_gm,
            "policy_to_oracle_geometric_runtime": policy_gm / oracle_gm,
        }
    group_count = len(recipe_metrics)
    policy = math.exp(
        sum(math.log(row["policy_geometric_runtime_over_stock"]) for row in recipe_metrics.values())
        / group_count
    )
    oracle = math.exp(
        sum(math.log(row["oracle_geometric_runtime_over_stock"]) for row in recipe_metrics.values())
        / group_count
    )
    return {
        "recipes": recipe_metrics,
        "group_balanced_geometric_runtime_over_stock": policy,
        "group_balanced_oracle_geometric_runtime_over_stock": oracle,
        "group_balanced_runtime_to_oracle": policy / oracle,
    }


def _training_rows(
    cases: list[dict[str, Any]], feature_set: str
) -> list[dict[str, Any]]:
    return feedback._training_rows(cases, block=1, informed=feature_set == "compiler42")


def _predict_model(
    model: Any,
    feature_names: list[str],
    case: dict[str, Any],
    design: dict[str, Any],
    feature_set: str,
    model_family: str,
) -> str:
    informed = feature_set == "compiler42"
    packet = feedback._provider_packet(case, design, informed=informed)
    if model_family == "ridge":
        score = feedback_policy.score_packet(model, packet)
        scores = [score["predicted_log_losses"][str(index)] for index in range(len(feedback.ORDERS))]
    else:
        columns = packet["features"]
        matrix = [
            [float(columns[name][index]) for name in feature_names]
            for index in range(len(packet["actions"]))
        ]
        scores = model.predict(matrix).tolist()
    choice_index = min(range(len(scores)), key=lambda index: (scores[index], index))
    return feedback.ORDERS[choice_index]


def _fit_model(
    rows: list[dict[str, Any]], model_family: str, replication_design: dict[str, Any]
) -> tuple[Any, list[str], dict[str, Any]]:
    if model_family == "ridge":
        model = feedback_policy.fit_ridge(rows, alpha=1.0)
        return model, model["features"], {"alpha": 1.0}
    model, exported = replication._fit_forest(rows, replication_design)
    return model, exported["features"], exported["parameters"]


def _prediction_record(case: dict[str, Any], choice: str) -> dict[str, Any]:
    oracle = min(feedback.ORDERS, key=lambda action: (_action_loss(case, action), feedback.ORDERS.index(action)))
    selected_loss = _action_loss(case, choice)
    oracle_loss = _action_loss(case, oracle)
    return {
        "case_id": case["case_id"],
        "recipe": _recipe(case),
        "choice": choice,
        "selected_loss_over_stock": selected_loss,
        "oracle_choice": oracle,
        "oracle_loss_over_stock": oracle_loss,
        "choice_to_oracle_runtime": selected_loss / oracle_loss,
    }


def _evaluate_models(
    cases: list[dict[str, Any]],
    fold_plan: dict[str, dict[str, Any]],
    replication_design: dict[str, Any],
    source_design: dict[str, Any],
) -> list[dict[str, Any]]:
    results = []
    for feature_set in FEATURE_SETS:
        feature_count = len(source_design["source_features"])
        if feature_set == "compiler42":
            feature_count += len(source_design["compiler_features"])
        for recipe_count in RECIPE_COUNTS:
            for model_family in MODEL_FAMILIES:
                fold_results = []
                predictions = []
                fit_seconds_total = 0.0
                inference_seconds_total = 0.0
                action_rows = 0
                for held_out, plan in sorted(fold_plan.items()):
                    train_recipes = plan["training_recipes_by_count"][str(recipe_count)]
                    train_cases = _cases_for_recipes(cases, train_recipes)
                    held_cases = [case for case in cases if _recipe(case) == held_out]
                    if len(held_cases) != 4 or any(_recipe(case) in train_recipes for case in held_cases):
                        raise ValueError(f"recipe leakage in outer fold {held_out}")
                    rows = _training_rows(train_cases, feature_set)
                    if len(rows) != len(train_cases) * len(feedback.ORDERS):
                        raise ValueError("training action rows do not match selected layouts")
                    action_rows = len(rows)
                    start = time.perf_counter()
                    model, feature_names, _parameters = _fit_model(rows, model_family, replication_design)
                    fit_seconds = time.perf_counter() - start
                    fit_seconds_total += fit_seconds
                    fold_predictions = []
                    start = time.perf_counter()
                    held_choices = [
                        _predict_model(model, feature_names, case, source_design, feature_set, model_family)
                        for case in held_cases
                    ]
                    inference_seconds = time.perf_counter() - start
                    for case, choice in zip(held_cases, held_choices, strict=True):
                        record = _prediction_record(case, choice)
                        fold_predictions.append(record)
                        predictions.append(record)
                    inference_seconds_total += inference_seconds
                    fold_results.append({
                        "held_out_recipe": held_out,
                        "training_recipe_groups": train_recipes,
                        "fit_seconds": fit_seconds,
                        "held_out_inference_seconds": inference_seconds,
                        "predictions": fold_predictions,
                    })
                results.append({
                    "feature_set": feature_set,
                    "feature_count": feature_count,
                    "training_recipe_count": recipe_count,
                    "training_layout_count_per_fold": recipe_count * 4,
                    "training_action_rows_per_fold": action_rows,
                    "model_family": model_family,
                    "parameters": ({"alpha": 1.0} if model_family == "ridge" else replication_design["forest"]),
                    "fit_seconds_total": fit_seconds_total,
                    "fit_seconds_mean_per_fold": fit_seconds_total / len(fold_results),
                    "held_out_inference_seconds_total": inference_seconds_total,
                    "held_out_inference_seconds_mean_per_case": inference_seconds_total / len(predictions),
                    "metrics": _summarize_predictions(predictions),
                    "folds": fold_results,
                })
    return results


def _evaluate_baselines(
    cases: list[dict[str, Any]], fold_plan: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    start = time.perf_counter()
    stride_choices = [contraction.stride_choice(case["source_features"]) for case in cases]
    stride_inference_seconds = time.perf_counter() - start
    stride_predictions = [
        _prediction_record(case, choice) for case, choice in zip(cases, stride_choices, strict=True)
    ]
    best_constant = []
    for recipe_count in RECIPE_COUNTS:
        predictions = []
        folds = []
        for held_out, plan in sorted(fold_plan.items()):
            train_recipes = plan["training_recipes_by_count"][str(recipe_count)]
            train_cases = _cases_for_recipes(cases, train_recipes)
            held_cases = [case for case in cases if _recipe(case) == held_out]
            start = time.perf_counter()
            choice = contraction._fit_discovery(train_cases)["best_constant"]
            fit_seconds = time.perf_counter() - start
            start = time.perf_counter()
            held_choices = [choice for _case in held_cases]
            inference_seconds = time.perf_counter() - start
            fold_predictions = [
                _prediction_record(case, action)
                for case, action in zip(held_cases, held_choices, strict=True)
            ]
            predictions.extend(fold_predictions)
            folds.append({
                "held_out_recipe": held_out,
                "training_recipe_groups": train_recipes,
                "choice": choice,
                "fit_seconds": fit_seconds,
                "held_out_inference_seconds": inference_seconds,
                "predictions": fold_predictions,
            })
        best_constant.append({
            "training_recipe_count": recipe_count,
            "metrics": _summarize_predictions(predictions),
            "fit_seconds_total": sum(row["fit_seconds"] for row in folds),
            "held_out_inference_seconds_total": sum(row["held_out_inference_seconds"] for row in folds),
            "folds": folds,
        })
    return {
        "stride_selector": {
            "training_recipe_count": 0,
            "fit_seconds": 0.0,
            "held_out_inference_seconds": stride_inference_seconds,
            "held_out_inference_seconds_mean_per_case": stride_inference_seconds / len(cases),
            "metrics": _summarize_predictions(stride_predictions),
            "predictions": stride_predictions,
        },
        "best_constant": best_constant,
    }


def run(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    source_design = feedback._design(SOURCE_DESIGN)
    replication_design = replication._design(REPLICATION_DESIGN, SOURCE_DESIGN)
    cases, _full_informed_rows, source_evidence = replication._source_training(
        replication_design, SOURCE_BUNDLE, SOURCE_DESIGN
    )
    if len(cases) != 36 or len({case["case_id"] for case in cases}) != 36:
        raise ValueError("source evidence must contain exactly the original 36 cases")
    fold_seed = source_design["seed"]
    fold_plan = _fold_plan(cases, fold_seed)
    source_rows = _training_rows(cases, "source30")
    compiler_rows = _training_rows(cases, "compiler42")
    if len(source_rows) != 216 or len(compiler_rows) != 216:
        raise ValueError("expected six action rows for each of the 36 source cases")
    report = {
        "schema_version": 1,
        "status": "development_diagnostic_only",
        "scope": {
            "purpose": "separate representation, training-recipe count, and fixed model-family effects",
            "cases": len(cases),
            "recipes": len(fold_plan),
            "layouts_per_recipe": 4,
            "actions_per_case": len(feedback.ORDERS),
            "timing_block": 1,
            "partitions_included": sorted({case["partition"] for case in cases}),
            "not_an_untouched_performance_claim": True,
            "uses_only_existing_measurements": True,
            "native_builds_or_timings_started": False,
            "provider_calls": 0,
            "tuning_performed": False,
        },
        "provenance": {
            "source_design": str(SOURCE_DESIGN.relative_to(ROOT)),
            "source_design_sha256": _sha256(SOURCE_DESIGN),
            "source_bundle": str(SOURCE_BUNDLE.relative_to(ROOT)),
            "source_bundle_sha256": _sha256(SOURCE_BUNDLE),
            "replication_design": str(REPLICATION_DESIGN.relative_to(ROOT)),
            "replication_design_sha256": _sha256(REPLICATION_DESIGN),
            "source_audit_mode": source_evidence["audit_mode"],
            "source_raw_evidence_sha256": source_evidence["raw_file_sha256"],
            "source30_training_rows_sha256": _fingerprint(source_rows),
            "compiler42_training_rows_sha256": _fingerprint(compiler_rows),
            "implementation_sha256": {
                path.relative_to(ROOT).as_posix(): _sha256(path)
                for path in (
                    Path(__file__),
                    Path(feedback.__file__),
                    Path(replication.__file__),
                    Path(feedback_policy.__file__),
                    Path(contraction.__file__),
                )
            },
        },
        "fold_plan": {
            "algorithm": "for each held-out recipe, sort the other eight by SHA256('policy-learning-diagnostic-v1:<seed>:<held>:<candidate>'); use nested prefixes of length 2, 4, and 8",
            "seed": fold_seed,
            "recipe_to_case_ids": {
                recipe: sorted(case["case_id"] for case in cases if _recipe(case) == recipe)
                for recipe in sorted(fold_plan)
            },
            "folds": fold_plan,
        },
        "model_contract": {
            "features": {"source30": len(source_design["source_features"]), "compiler42": len(source_design["source_features"]) + len(source_design["compiler_features"])},
            "target": "log(block-one median runtime / same-case stock median)",
            "ridge": {"alpha": 1.0},
            "random_forest": {
                **replication_design["forest"],
                "scikit_learn_version": replication_design["sklearn_version"],
            },
            "forest_random_state": replication_design["forest"]["random_state"],
            "tie_break": "feedback action order",
        },
        "model_results": _evaluate_models(cases, fold_plan, replication_design, source_design),
        "baselines": _evaluate_baselines(cases, fold_plan),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = run(args.output)
    print(json.dumps({"status": report["status"], "output": str(args.output), "model_variants": len(report["model_results"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
