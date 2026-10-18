"""Portable replay checks over the published workload-policy evidence bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import replay_workload_repair as replay

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_PATH = ROOT / "docs" / "results" / "workload-policy-repair-v1.json"


def _bundle() -> dict:
    return json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))


def test_public_bundle_replays_all_frozen_choices_without_new_quality_claim() -> None:
    report = replay.replay_bundle(_bundle())

    assert report["status"] == "passed"
    assert report["case_count"] == 12
    assert report["matched_choices"] == 36
    assert report["total_choices"] == 36
    assert report["original_experiment"] == {
        "test_report_status": "repair_gate_failed",
        "repair_gate_passed": False,
        "test_labels_used_for_fit_or_choice": False,
    }
    assert "offline replay of frozen choices" in report["interpretation"]
    assert "not rerun the experiment" in report["interpretation"]


def test_rejects_corrupted_prepared_heuristic_hash() -> None:
    bundle = _bundle()
    files = dict(bundle["files"])
    record = dict(files["prepared-test.json"])
    evidence = dict(record["evidence"])
    evidence["direct_patch_sha256"] = "0" * 64
    record["evidence"] = evidence
    files["prepared-test.json"] = record
    bundle["files"] = files

    with pytest.raises(replay.ReplayError, match="current workload heuristic differs"):
        replay.replay_bundle(bundle)
