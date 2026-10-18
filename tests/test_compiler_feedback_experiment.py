"""Focused checks for blind packets, frozen folds, routing, and provider resume."""

from __future__ import annotations

import copy
import json
import statistics
from pathlib import Path

import pytest

from scripts import compiler_feedback_experiment as trial

DESIGN = Path(__file__).resolve().parents[1] / "experiments" / "compiler-feedback-v1.json"


def _source_features(spec: dict) -> dict[str, int]:
    m, n, k = (spec[name] for name in ("m", "n", "k"))
    pa, pb, pc = (spec[f"{name}_padding"] for name in ("a", "b", "c"))
    a_ld = (m if spec["a_column_major"] else k) + pa
    b_ld = (k if spec["b_column_major"] else n) + pb
    c_ld = (m if spec["c_column_major"] else n) + pc
    return {
        "m": m,
        "n": n,
        "k": k,
        "trip_count": m * n * k,
        "element_bits": 32,
        "arithmetic_bits": 32,
        "a_i_stride": 1 if spec["a_column_major"] else a_ld,
        "a_k_stride": a_ld if spec["a_column_major"] else 1,
        "b_k_stride": 1 if spec["b_column_major"] else b_ld,
        "b_j_stride": b_ld if spec["b_column_major"] else 1,
        "c_i_stride": 1 if spec["c_column_major"] else c_ld,
        "c_j_stride": c_ld if spec["c_column_major"] else 1,
    }


def _built_case(row: dict, case_index: int) -> dict:
    source = _source_features(row["spec"])
    actions = {}
    for index, name in enumerate(trial.ACTION_NAMES):
        order = "ijk" if name == "stock" else name
        actions[name] = {
            "code_identity": f"object-{case_index}-{order}",
            "compiler_evidence": {
                key: case_index + index + offset
                for offset, key in enumerate(trial.COMPILER_FEATURES)
            },
        }
    actions["ijk"]["code_identity"] = actions["stock"]["code_identity"]
    return {
        **row,
        "source_features": source,
        "distinct_code_identities": len({actions[name]["code_identity"] for name in trial.ORDERS}),
        "build": {"actions": actions},
    }


def _measure_case(case: dict, block: int, index: int) -> dict:
    records = []
    for round_index in range(1, 12):
        base = 20_000 + index * 100 + block * 20 + round_index
        measurements = {
            name: {"elapsed_ns": base + action_index * 300}
            for action_index, name in enumerate(trial.ACTION_NAMES)
        }
        records.append({
            "round": (block - 1) * 11 + round_index,
            "block": block,
            "block_round": round_index,
            "canonical_order": "fixture",
            "measurements": measurements,
        })
    medians = {
        name: float(statistics.median(row["measurements"][name]["elapsed_ns"] for row in records))
        for name in trial.ACTION_NAMES
    }
    return {
        **{key: case[key] for key in ("partition", "family", "spec", "case_id")},
        "source_features": case["source_features"],
        "iterations": 1,
        "timing_calibration": {},
        "code_aliases": {name: name for name in trial.ACTION_NAMES},
        "distinct_code_identities": case["distinct_code_identities"],
        "timing_caches": [],
        "benchmark": {
            "block_seeds": {str(block): block},
            "rounds_per_block": 11,
            "round_records": records,
            "median_ns": medians,
            "canonical_summaries_by_block": {str(block): {}},
        },
    }


def test_packets_separate_source_from_compiler_evidence_without_outcomes() -> None:
    design = trial._design(DESIGN)
    built = _built_case(trial._cases(design, ("discovery",))[0], 0)
    source = trial._provider_packet(built, design, informed=False)
    informed = trial._provider_packet(built, design, informed=True)

    assert set(source["features"]) == set(design["source_features"])
    assert set(informed["features"]) == set(design["source_features"]) | set(design["compiler_features"])
    encoded_source = json.dumps(source)
    encoded_informed = json.dumps(informed)
    for forbidden in (built["case_id"], built["family"], "elapsed_ns", "benchmark", "object-"):
        assert forbidden not in encoded_source
        assert forbidden not in encoded_informed
    assert "Minimize native runtime" in source["task"]
    assert source["task"] == informed["task"][: len(source["task"])]


def test_discovery_fold_predictions_exclude_the_held_out_group() -> None:
    design = trial._design(DESIGN)
    cases, built_cases = [], []
    rows = trial._cases(design, ("discovery",))
    for index, row in enumerate(rows):
        built = _built_case(row, index)
        built_cases.append(built)
        cases.append(_measure_case(built, block=1, index=index))
    cases = trial._attach_compiler_evidence(cases, built_cases)
    fit = trial._fit_discovery(cases, design)

    assert set(fit["out_of_group_predictions"]) == {case["case_id"] for case in cases}
    for group, fold in fit["folds"].items():
        assert fold["training_case_ids"]
        assert fold["held_out_case_ids"]
        assert not set(fold["training_case_ids"]) & set(fold["held_out_case_ids"])
        assert all(
            next(case for case in cases if case["case_id"] == case_id)["family"] != group
            for case_id in fold["training_case_ids"]
        )


def test_discovery_phase_integrates_frozen_build_features_without_compiling_or_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    design = trial._design(DESIGN)
    all_rows = trial._cases(design)
    built = [_built_case(row, index) for index, row in enumerate(all_rows)]
    calls = []

    def fake_measure(cases, _design, _output, *, blocks):
        block = blocks[0]
        calls.append(block)
        return [_measure_case(case, block, index) for index, case in enumerate(cases)]

    monkeypatch.setattr(trial, "_build_all", lambda *_args: built)
    monkeypatch.setattr(trial.contraction, "_measure_cases", fake_measure)
    validation = tmp_path / "validation.json"
    validation.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "run"
    report = trial.run_discovery(DESIGN, validation, output)

    assert calls == [1, 2]
    assert report["model_calls"] == 0
    assert len(report["discovery_cases"]) == 16
    for case in report["discovery_cases"]:
        assert set(case["compiler_evidence_by_action"]) == set(trial.ORDERS)
        assert case["compiler_evidence_by_action"] == {
            order: built_case["build"]["actions"][order]["compiler_evidence"]
            for built_case in built
            if built_case["case_id"] == case["case_id"]
            for order in trial.ORDERS
        }
    checkpoint = json.loads((output / "discovery-fit.json").read_text())
    assert set(checkpoint["fit"]["out_of_group_predictions"]) == {
        case["case_id"] for case in report["discovery_cases"]
    }


def _router_case(case_id: str, family: str, actions: dict[str, int]) -> dict:
    return {
        "case_id": case_id,
        "family": family,
        "benchmark": {
            "round_records": [{
                "block": 1,
                "measurements": {name: {"elapsed_ns": value} for name, value in actions.items()},
            }]
        },
    }


def test_router_uses_regret_and_disagreement_only_and_obeys_call_budget() -> None:
    design = trial._design(DESIGN)
    cases, choices = [], {}
    for index in range(8):
        case_id = f"cal-{index}"
        jev_action = trial.ORDERS[2]
        codex_action = trial.ORDERS[3]
        actions = {name: 1000 + index * 10 + i for i, name in enumerate(trial.ACTION_NAMES)}
        cases.append(_router_case(case_id, "cal-a" if index < 4 else "cal-b", actions))
        disagreement = index < 4
        regret = (index + 1) / 10 if disagreement else 0.0
        losses = {name: 0.1 for name in trial.ORDERS}
        losses[jev_action] = 0.1 + regret
        choices[case_id] = {
            "stride_selector": trial.ORDERS[0] if disagreement else trial.ORDERS[1],
            "ridge": trial.ORDERS[1],
            "informed_jev": jev_action,
            "informed_codex": codex_action,
            "ridge_predicted_log_losses": losses,
            "jev_confidence": index / 7,
        }
    first = trial._fit_router(cases, choices, design)
    changed_confidence = copy.deepcopy(choices)
    for row in changed_confidence.values():
        row["jev_confidence"] = 1 - row["jev_confidence"]
    second = trial._fit_router(cases, changed_confidence, design)

    assert first == second
    assert first["chosen"]["escalations"] <= 2
    assert first["max_escalations"] == 2
    choice = choices["cal-3"]
    _, regret = trial._router_regret(choice)
    assert trial._apply_route(choice, {"mode": "regret_threshold", "threshold": regret})["route_to_codex"] is False
    aligned = {**choice, "stride_selector": choice["ridge"]}
    assert trial._apply_route(aligned, {"mode": "regret_threshold", "threshold": 0})["route_to_codex"] is False


def test_provider_cache_reuses_valid_choice_and_retains_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = {"task": "fixture", "features": {"x": [1, 2]}, "actions": [{"id": "0"}, {"id": "1"}]}
    path = tmp_path / "providers.json"
    calls = []

    def valid(_packet):
        calls.append("valid")
        return {"provider": "jev", "chosen_action": "1", "confidence": 0.7}

    monkeypatch.setattr(trial.jev, "choose", valid)
    first = trial._query_provider(packet, "jev", path, allow_call=True)
    second = trial._query_provider(packet, "jev", path, allow_call=True)
    cached = trial._query_provider(packet, "jev", path, allow_call=False)
    assert calls == ["valid"]
    assert first == second == cached

    failed_path = tmp_path / "failed.json"
    attempts = []

    def invalid(_packet):
        attempts.append("invalid")
        return {"provider": "jev", "chosen_action": "99", "confidence": 0.7}

    monkeypatch.setattr(trial.jev, "choose", invalid)
    with pytest.raises(trial.CompilerFeedbackError):
        trial._query_provider(packet, "jev", failed_path, allow_call=True)
    saved = json.loads(failed_path.read_text())
    entry = next(iter(saved["packets"].values()))
    assert entry["attempts"][-1]["status"] == "rejected"
    with pytest.raises(trial.CompilerFeedbackError):
        trial._query_provider(packet, "jev", failed_path, allow_call=False)
    assert attempts == ["invalid"]
