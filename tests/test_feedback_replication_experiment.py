"""Focused phase, source-integrity, and replicated-choice checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import compiler_feedback_experiment as feedback
from scripts import contraction_corpus
from scripts import feedback_replication_experiment as replication

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "experiments" / "feedback-replication-v1.json"
SOURCE_DESIGN = ROOT / "experiments" / "compiler-feedback-v1.json"
SOURCE_BUNDLE = ROOT / "docs" / "results" / "compiler-feedback-v1.json"


def test_source_loader_verifies_raw_hashes_and_uses_only_first_block() -> None:
    if not (ROOT / "artifacts/compiler-feedback-v1/builds.json").is_file():
        pytest.skip("raw compiler-feedback artifacts are not present in this checkout")
    design = replication._design(DESIGN, SOURCE_DESIGN)
    cases, rows, evidence = replication._source_training(design, SOURCE_BUNDLE, SOURCE_DESIGN)

    assert len(cases) == 36
    assert len(rows) == 216
    assert evidence["training_block"] == 1
    assert all(
        all(record["block"] == 1 for record in case["benchmark"]["round_records"])
        for case in cases
    )
    assert len(evidence["raw_file_sha256"]) >= 75
    assert evidence["audit_mode"] == "raw_and_public"


def test_source_loader_can_reproduce_from_hash_pinned_public_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    design = replication._design(DESIGN, SOURCE_DESIGN)
    monkeypatch.setattr(replication, "ROOT", tmp_path)

    cases, rows, evidence = replication._source_training(design, SOURCE_BUNDLE, SOURCE_DESIGN)

    assert len(cases) == 36
    assert len(rows) == 216
    assert evidence["audit_mode"] == "public_bundle_only"
    assert evidence["raw_file_sha256"]["builds.json"]


def test_raw_source_file_mismatch_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "timing.json"
    path.write_text('{"value": 1}\n', encoding="utf-8")
    published = {"value": 1}
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()

    loaded, mode = replication._load_raw_or_published(path, published, original_hash, "timing")

    assert loaded == published
    assert mode == "raw_and_public"
    path.write_text('{"value": 2}\n', encoding="utf-8")
    with pytest.raises(replication.FeedbackReplicationError, match="hash differs"):
        replication._load_raw_or_published(path, published, original_hash, "timing")


def test_provider_replicates_use_identical_packet_and_separate_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packet = {
        "task": "choose an order",
        "features": {"x": [1, 2, 3]},
        "actions": [{"id": str(index), "description": str(index)} for index in range(6)],
    }
    answers = iter(("0", "3", "3"))
    seen_packets = []

    def choose(value):
        seen_packets.append(value)
        return {"provider": "jev", "chosen_action": next(answers), "confidence": 0.75}

    monkeypatch.setattr(feedback.jev, "choose", choose)
    slots = replication._query_replicates({"case_id": "fixture-case"}, packet, tmp_path)

    assert len(seen_packets) == 3
    assert seen_packets[0] == seen_packets[1] == seen_packets[2] == packet
    assert len({slot["packet_sha256"] for slot in slots}) == 1
    assert len({slot["cache_path"] for slot in slots}) == 3
    assert [slot["action"] for slot in slots] == [feedback.ORDERS[0], feedback.ORDERS[3], feedback.ORDERS[3]]
    assert all(Path(slot["cache_path"]).is_file() for slot in slots)


def test_expected_runtime_uses_arithmetic_mean_of_three_selected_actions() -> None:
    records = []
    for round_index in range(1, 12):
        records.append({
            "round": round_index,
            "block": 1,
            "block_round": round_index,
            "canonical_order": "fixture",
            "measurements": {
                action: {"elapsed_ns": value}
                for action, value in zip(feedback.ORDERS, (10, 20, 100, 40, 50, 60), strict=True)
            },
        })
    case = {
        "case_id": "replication-small-ccc",
        "benchmark": {"round_records": records},
    }
    choices = {case["case_id"]: {"provider_replicates": [
        {"action": feedback.ORDERS[0]},
        {"action": feedback.ORDERS[1]},
        {"action": feedback.ORDERS[2]},
    ]}}

    expected = replication._expected_runtime_cases([case], choices, 1)

    assert expected[0]["benchmark"]["round_records"][0]["measurements"]["jev_expected"]["elapsed_ns"] == pytest.approx(130 / 3)


def test_prepare_fits_and_freezes_without_starting_native_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    design = replication._design(DESIGN, SOURCE_DESIGN)
    built_cases = []
    for index, row in enumerate(replication._cases(design)):
        generated = contraction_corpus.generate_case(tmp_path / row["case_id"], row["spec"], order="ijk")
        action_rows = {}
        for action_index, name in enumerate(feedback.ACTION_NAMES):
            order = "ijk" if name == "stock" else name
            action_rows[name] = {
                "status": "passed",
                "code_identity": "same-ijk" if order == "ijk" else f"{row['case_id']}-{order}",
                "compiler_evidence": {
                    feature: index + action_index + feature_index
                    for feature_index, feature in enumerate(feedback.COMPILER_FEATURES)
                },
                "compiler_evidence_status": {"status": "complete"},
            }
        build = {
            "status": "passed",
            "action_order": list(feedback.ACTION_NAMES),
            "stock_ijk_code_identity_equal": True,
            "actions": action_rows,
            "case_fingerprint": f"fixture-{row['case_id']}",
        }
        feedback._check_compiler_evidence(build, design, row["case_id"])
        built_cases.append({
            **row,
            "source_features": generated["features"],
            "feature_kernel_sha256": hashlib.sha256(Path(generated["kernel"]).read_bytes()).hexdigest(),
            "feature_driver_sha256": hashlib.sha256(Path(generated["driver"]).read_bytes()).hexdigest(),
            "build_report_path": str(tmp_path / f"{row['case_id']}-report.json"),
            "build_report_sha256": "fixture-report-sha",
            "distinct_code_identities": 6,
            "build": build,
        })

    monkeypatch.setattr(replication, "_build_all", lambda *_args: built_cases)
    monkeypatch.setattr(
        replication.contraction,
        "_measure_cases",
        lambda *_args, **_kwargs: pytest.fail("prepare phase must not start native timings"),
    )
    calls = []

    def choose(_packet):
        calls.append(1)
        return {"provider": "jev", "chosen_action": "0", "confidence": 0.8}

    monkeypatch.setattr(feedback.jev, "choose", choose)
    validation = tmp_path / "validation.json"
    validation.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "run"

    report = replication.run_prepare(DESIGN, validation, output, SOURCE_BUNDLE)

    assert report["status"] == "prepared"
    assert report["fresh_timing_started"] is False
    assert report["provider_slots"] == 72
    assert set(report["cost_inventory_seconds"]) == {"fit_seconds", "predictions_seconds"}
    assert all(value >= 0 for value in report["cost_inventory_seconds"].values())
    assert len(calls) == 72
    frozen = json.loads((output / "choices.json").read_text(encoding="utf-8"))
    assert len(frozen["choices"]) == 24
    assert all(len(row["provider_replicates"]) == 3 for row in frozen["choices"].values())


def test_measure_refuses_to_start_without_prepared_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = []
    monkeypatch.setattr(replication.contraction, "_measure_cases", lambda *_args, **_kwargs: called.append(True))
    validation = tmp_path / "validation.json"
    validation.write_text("{}\n", encoding="utf-8")

    with pytest.raises((replication.FeedbackReplicationError, feedback.CompilerFeedbackError)):
        replication.run_measure(DESIGN, validation, tmp_path / "run", SOURCE_BUNDLE)

    assert called == []
