import json
from pathlib import Path

import pytest

from scripts import compiler_feedback_experiment as feedback
from scripts import feedback_replication_experiment as replication
from scripts import policy_repair_experiment as repair
from scripts import policy_shift_experiment as shift


def _timed_cases(*, block_two_target_ns=90):
    cases = []
    choices = {}
    for group_index in range(6):
        group = f"recipe-{group_index}"
        for case_index in range(2):
            case_id = f"case-{group_index}-{case_index}"
            records = []
            for block in (1, 2):
                for round_number in range(1, 12):
                    target_ns = 90 if block == 1 else block_two_target_ns
                    timings = {
                        "stock": 100,
                        "ijk": 100,
                        "ikj": target_ns,
                        "jik": 91,
                        "jki": 110,
                        "kij": 105,
                        "kji": 120,
                    }
                    records.append(
                        {
                            "round": (block - 1) * 11 + round_number,
                            "block": block,
                            "block_round": round_number,
                            "measurements": {
                                action: {"elapsed_ns": value}
                                for action, value in timings.items()
                            },
                        }
                    )
            cases.append(
                {
                    "case_id": case_id,
                    "group": group,
                    "benchmark": {"round_records": records},
                }
            )
            choices[case_id] = {
                "unchanged_source_forest": "ijk",
                "stride_selector": "kij",
                "target_refit": "ikj",
                "direct_patch": "jik",
            }
    return cases, choices


def test_historical_public_bundle_import_expands_real_action_aliases():
    design = repair._design(repair.DEFAULT_DESIGN)
    shift_path = repair._repo_path(design["shift_design"])
    shift_design = shift._design(shift_path)
    raw_root = repair._repo_path(str(Path(design["audit_report"]).parent))
    bundle = repair._public_bundle(design)
    audit, _audit_source = repair._bundle_evidence(
        bundle, "audit-report.json", design["audit_report_sha256"], raw_root
    )
    prepared, _prepared_source = repair._bundle_evidence(
        bundle, "prepared-audit.json", design["prepared_audit_sha256"], raw_root
    )
    artifact_ref = prepared["policy_artifacts"]["target"]
    policy_path = repair._bundle_relative_path(artifact_ref["path"], raw_root)
    source_policy, _policy_source = repair._bundle_evidence(
        bundle, policy_path, artifact_ref["sha256"], raw_root
    )
    inputs = {
        "shift_design": shift_design,
        "audit": audit,
        "prepared": prepared,
        "bundle": bundle,
        "raw_root": raw_root,
        "target_identity": source_policy["artifact"]["compiler_identity"],
    }

    cases = repair._historical_training_cases(design, inputs)
    rows = feedback._training_rows(cases, block=1, informed=True)

    assert len(cases) == 6
    assert len(rows) == 36
    assert len(rows[0]["features"]) == 42
    assert all(set(row["measurements"]) == set(repair.ACTION_NAMES) for case in cases for row in case["benchmark"]["round_records"])


def test_repo_path_normalization_and_historical_reference_confinement(tmp_path):
    root = repair.ROOT
    raw_root = root / "artifacts" / "historical"
    child = raw_root / "cases" / "build-report.json"

    assert repair._normalize_repo_paths({"path": str(child)}) == {
        "path": "$REPO/artifacts/historical/cases/build-report.json"
    }
    assert repair._bundle_relative_path(str(child), raw_root) == "cases/build-report.json"
    assert repair._bundle_relative_path("$REPO/artifacts/historical/cases/build-report.json", raw_root) == "cases/build-report.json"
    with pytest.raises(repair.PolicyRepairError, match="outside"):
        repair._bundle_relative_path(str(tmp_path / "outside.json"), raw_root)


def test_measured_metadata_restores_group_dropped_by_shared_timing_helper():
    originals = [
        {"case_id": "c1", "partition": "adaptation", "family": "g1", "group": "g1", "spec": {"m": 7}},
        {"case_id": "c2", "partition": "adaptation", "family": "g2", "group": "g2", "spec": {"m": 9}},
    ]
    measured = [
        {"case_id": "c2", "partition": "discovery", "family": "g2", "benchmark": {"round_records": []}},
        {"case_id": "c1", "partition": "discovery", "family": "g1", "benchmark": {"round_records": []}},
    ]

    restored = repair._restore_measured_metadata(measured, originals, "adaptation")

    assert [(case["case_id"], case["group"], case["spec"]) for case in restored] == [
        ("c2", "g2", {"m": 9}),
        ("c1", "g1", {"m": 7}),
    ]
    with pytest.raises(repair.PolicyRepairError, match="case set"):
        repair._restore_measured_metadata(measured[:1], originals, "adaptation")


def test_round_lookup_uses_block_local_round_for_second_measurement_block():
    cases, _choices = _timed_cases()

    assert repair._round_runtime(cases[0], "ikj", 2, 1) == 90
    assert repair._round_runtime(cases[0], "ikj", 2, 11) == 90


def test_repair_gate_requires_each_block_and_uses_frozen_jevs_mean_choice():
    design = repair._design(repair.DEFAULT_DESIGN)
    cases, choices = _timed_cases()
    result = repair.repair_gate(cases, choices, design, jev_available=False)

    assert result["passed"]
    assert result["primary"]["blocks"]["1"]["geometric_runtime_ratio"] == pytest.approx(0.9)
    assert result["primary"]["blocks"]["2"]["geometric_runtime_ratio"] == pytest.approx(0.9)
    assert result["stride_control_vs_unchanged_source_forest"]["evaluation_role"] == "descriptive_control_not_repair_acceptance"

    worse_block_two, choices = _timed_cases(block_two_target_ns=101)
    failed = repair.repair_gate(worse_block_two, choices, design, jev_available=False)
    assert not failed["passed"]
    assert not failed["primary"]["blocks"]["2"]["passed"]

    for case in cases:
        for round_record in case["benchmark"]["round_records"]:
            round_record["measurements"]["ikj"]["elapsed_ns"] = 80
            round_record["measurements"]["jik"]["elapsed_ns"] = 120
            round_record["measurements"]["kij"]["elapsed_ns"] = 120
        choices[case["case_id"]]["informed_jev_reference"] = ["ikj", "jik", "kij"]
    jev_comparison = repair._compare_policies(
        cases, choices, "informed_jev_reference", "unchanged_source_forest", design
    )
    assert jev_comparison["blocks"]["1"]["geometric_runtime_ratio"] == pytest.approx(320 / 300)


def test_jev_accounting_keeps_partial_success_and_does_not_assume_failed_usage_zero():
    summary = repair._jev_summary(
        {
            "failed-case": {
                "status": "unavailable",
                "slots": [
                    {
                        "response": {
                            "model": "jev-test",
                            "usage": {"input_tokens": 13},
                            "latency_ms": 8.5,
                        },
                        "attempts": [{"status": "available"}],
                    }
                ],
                "incomplete_slots": [{"attempt_count": 3}],
            },
            "not-attempted": {"status": "not_attempted_prior_failure", "slots": []},
        }
    )

    assert summary["response_count"] == 1
    assert summary["input_tokens_observed"] == 13
    assert summary["output_tokens_observed"] == 0
    assert summary["responses_with_output_token_count"] == 0
    assert summary["recorded_attempts"] == 4
    assert summary["failed_or_unattempted_slots"] == 4
    assert "may be unavailable" in summary["usage_scope"]


def test_expand_action_aliases_reconstructs_missing_ijk_from_stock():
    aliases = {name: name for name in repair.ACTION_NAMES}
    aliases["ijk"] = "stock"
    raw = {
        name: {"elapsed_ns": index + 1}
        for index, name in enumerate(repair.ACTION_NAMES)
        if name != "ijk"
    }

    expanded = repair._expand_action_measurements(raw, aliases, "alias-case")

    assert set(expanded) == set(repair.ACTION_NAMES)
    assert expanded["ijk"] == expanded["stock"]
    with pytest.raises(repair.PolicyRepairError, match="timing sample"):
        repair._expand_action_measurements({"stock": {"elapsed_ns": 0}}, aliases, "bad-case")


def test_adaptation_timing_verifier_matches_raw_cache_and_frozen_global_rounds(tmp_path):
    rounds = 11
    case_id = "case-one"
    aliases = {name: name for name in repair.ACTION_NAMES}
    records = []
    references = []
    for block in (1, 2):
        raw_records = [
            {
                "round": round_number,
                "order": list(repair.ORDERS),
                "measurements": {
                    action: {"elapsed_ns": 100 + action_index}
                    for action_index, action in enumerate(repair.ACTION_NAMES)
                },
            }
            for round_number in range(1, rounds + 1)
        ]
        raw = {"status": "measured", "rounds": rounds, "round_records": raw_records}
        path = (
            tmp_path / "timing" / "adaptation" / "cases" / "discovery" / case_id
            / "timings" / f"block-{block}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        identity = {
            "case_id": case_id,
            "partition": "discovery",
            "block": block,
            "rounds": rounds,
            "action_aliases": aliases,
        }
        identity_sha = repair._fingerprint(identity)
        path.write_text(json.dumps({
            "schema_version": 1,
            "identity": identity,
            "identity_sha256": identity_sha,
            "benchmark": raw,
        }))
        references.append({"path": str(path.resolve()), "sha256": repair._sha256(path), "identity_sha256": identity_sha})
        for raw_row in raw_records:
            records.append({
                "round": (block - 1) * rounds + raw_row["round"],
                "block": block,
                "block_round": raw_row["round"],
                "canonical_order": raw_row["order"],
                "measurements": raw_row["measurements"],
            })
    case = {"case_id": case_id, "code_aliases": aliases, "timing_caches": references, "benchmark": {"round_records": records}}

    repair._verify_adaptation_timing_case(case, tmp_path, rounds)

    case["benchmark"]["round_records"][-1]["measurements"]["stock"]["elapsed_ns"] += 1
    with pytest.raises(repair.PolicyRepairError, match="differ from block cache"):
        repair._verify_adaptation_timing_case(case, tmp_path, rounds)


def test_fit_phase_reuses_frozen_fit_without_refitting(tmp_path, monkeypatch):
    design_path = tmp_path / "design.json"
    design_path.write_text("{}")
    output = tmp_path / "run"
    output.mkdir()
    (output / "prepared-adaptation.json").write_text(json.dumps({"provenance": {"p": 1}}))
    (output / "adaptation-report.json").write_text("{}")
    (output / "fit-report.json").write_text("{}")
    forest = {
        "n_estimators": 64,
        "max_depth": 6,
        "min_samples_leaf": 2,
        "random_state": 20261020,
        "n_jobs": 1,
    }
    design = {
        "forest": forest,
        "training": {"action_rows": 72},
        "audit_report_sha256": "a" * 64,
        "public_audit_bundle": {"sha256": "b" * 64},
    }
    identity = {"sha256": "c" * 64, "target": "test-target"}
    inputs = {"target_identity": identity}
    monkeypatch.setattr(repair, "_design", lambda _path: design)
    monkeypatch.setattr(repair, "_pinned_inputs", lambda _design: inputs)
    monkeypatch.setattr(repair, "_provenance", lambda *_args: {"p": 1})
    historical = [
        {"case_id": f"hist-{index}", "group": f"group-{index // 2}"}
        for index in range(6)
    ]
    adaptation = [
        {"case_id": f"new-{index}", "group": f"group-{index // 2 + 3}"}
        for index in range(6)
    ]
    monkeypatch.setattr(repair, "_historical_training_cases", lambda *_args: historical)
    monkeypatch.setattr(repair, "_adaptation_training_cases", lambda *_args: adaptation)
    rows = [{"features": {"x": float(index)}, "loss": 1.0} for index in range(72)]
    monkeypatch.setattr(feedback, "_training_rows", lambda *_args, **_kwargs: rows)
    model_provenance = {
        "design_sha256": repair._sha256(design_path),
        "historical_audit_sha256": design["audit_report_sha256"],
        "public_audit_bundle_sha256": design["public_audit_bundle"]["sha256"],
        "adaptation_report_sha256": repair._sha256(output / "adaptation-report.json"),
        "training_case_ids": [case["case_id"] for case in [*historical, *adaptation]],
        "training_block": 1,
        "compiler_identity": identity,
    }
    report = {
        "training_provenance": model_provenance,
        "training_case_ids": model_provenance["training_case_ids"],
        "training_groups": [f"group-{index}" for index in range(6)],
        "training_rows": rows,
        "training_rows_sha256": repair._fingerprint(rows),
        "feature_names": ["x"],
        "parameters": forest,
    }
    monkeypatch.setattr(
        repair,
        "_load_frozen_fit",
        lambda *_args: (report, {"feature_schema": ["x"]}),
    )

    def unexpected_fit(*_args, **_kwargs):
        raise AssertionError("a frozen fit should be resumed, not recalculated")

    monkeypatch.setattr(replication, "_fit_forest", unexpected_fit)

    result = repair.fit_repair(design_path, output)

    assert result is report
