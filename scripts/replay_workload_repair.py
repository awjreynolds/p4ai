"""Replay frozen workload-policy choices from the public evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from . import harness_policy, workload_heuristic
except ImportError:  # Running as ``python scripts/replay_workload_repair.py``.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts import harness_policy, workload_heuristic


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "docs" / "results" / "workload-policy-repair-v1.json"
SOURCE_POLICY = "policies/unchanged-source-forest.json"
REFIT_POLICY = "policies/target-refit.json"
METHODS = ("unchanged_source_forest", "target_refit", "direct_patch")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReplayError(ValueError):
    """The public bundle is incomplete or its frozen choices do not replay."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence(files: dict[str, Any], name: str) -> tuple[dict[str, Any], str]:
    record = files.get(name)
    if not isinstance(record, dict):
        raise ReplayError(f"public bundle is missing {name}")
    raw_sha = record.get("raw_sha256")
    evidence = record.get("evidence")
    if not isinstance(raw_sha, str) or _SHA256.fullmatch(raw_sha) is None:
        raise ReplayError(f"public bundle has an invalid raw SHA-256 for {name}")
    if not isinstance(evidence, dict):
        raise ReplayError(f"public bundle has no normalized evidence for {name}")
    return evidence, raw_sha


def replay_bundle(bundle: Any) -> dict[str, Any]:
    """Validate public frozen inputs and reproduce all 36 local choices."""
    if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
        raise ReplayError("unsupported workload evidence bundle")
    files = bundle.get("files")
    if not isinstance(files, dict):
        raise ReplayError("public bundle has no file evidence map")

    prepared, prepared_sha = _evidence(files, "prepared-test.json")
    prepared_adaptation, _adaptation_sha = _evidence(files, "prepared-adaptation.json")
    fit_report, fit_report_sha = _evidence(files, "fit-report.json")
    test_report, _test_report_sha = _evidence(files, "test-report.json")
    source_wrapper, source_policy_sha = _evidence(files, SOURCE_POLICY)
    refit_wrapper, refit_policy_sha = _evidence(files, REFIT_POLICY)

    if (
        prepared.get("schema_version") != 1
        or prepared.get("status") != "prepared_test"
        or prepared.get("native_measurements_started") is not False
        or prepared.get("test_outcomes_used") is not False
        or fit_report.get("status") != "fit_frozen"
        or fit_report.get("test_outcomes_used") is not False
    ):
        raise ReplayError("prepared/fitted inputs are not the frozen test-only checkpoint")
    if prepared.get("fit_report_sha256") != fit_report_sha:
        raise ReplayError("prepared test does not pin the public fit report")
    if prepared.get("target_policy_sha256") != refit_policy_sha:
        raise ReplayError("prepared test does not pin the refit policy")
    fit_policy_ref = fit_report.get("target_policy")
    source_policy_ref = prepared_adaptation.get("source_policy")
    if not isinstance(fit_policy_ref, dict) or not isinstance(source_policy_ref, dict):
        raise ReplayError("public training checkpoints have malformed policy references")
    if fit_policy_ref.get("sha256") != refit_policy_sha:
        raise ReplayError("fit report does not pin the refit policy")
    if source_policy_ref.get("sha256") != source_policy_sha:
        raise ReplayError("prepared adaptation does not pin the source policy")
    if test_report.get("prepared_test_sha256") != prepared_sha:
        raise ReplayError("test report does not pin the prepared test checkpoint")
    repair_gate = test_report.get("repair_gate")
    if (
        test_report.get("status") != "repair_gate_failed"
        or not isinstance(repair_gate, dict)
        or repair_gate.get("passed") is not False
        or test_report.get("test_labels_used_for_fit_or_choice") is not False
    ):
        raise ReplayError("public test report does not record the original failed gate")

    heuristic_path = ROOT / "scripts" / "workload_heuristic.py"
    actual_heuristic_sha = _sha256(heuristic_path)
    if prepared.get("direct_patch_sha256") != actual_heuristic_sha:
        raise ReplayError("current workload heuristic differs from its frozen direct-patch hash")

    if source_wrapper.get("schema_version") != 1 or refit_wrapper.get("schema_version") != 1:
        raise ReplayError("policy wrapper schema is unsupported")
    source_artifact = source_wrapper.get("artifact")
    refit_artifact = refit_wrapper.get("artifact")
    if not isinstance(source_artifact, dict) or not isinstance(refit_artifact, dict):
        raise ReplayError("public policy artifact is malformed")
    if harness_policy.canonical_sha256(refit_artifact.get("model")) != fit_report.get(
        "target_model_sha256"
    ):
        raise ReplayError("refit model hash differs from its frozen fit report")

    case_ids = prepared.get("case_ids")
    expected_choices = prepared.get("choices_by_case")
    packets = prepared.get("packets_by_case")
    recorded_choices = test_report.get("choices_by_case")
    if (
        not isinstance(case_ids, list)
        or len(case_ids) != 12
        or any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
        or len(set(case_ids)) != len(case_ids)
        or not isinstance(expected_choices, dict)
        or not isinstance(packets, dict)
        or not isinstance(recorded_choices, dict)
        or set(expected_choices) != set(case_ids)
        or set(packets) != set(case_ids)
        or set(recorded_choices) != set(case_ids)
    ):
        raise ReplayError("prepared test case choices or packets are incomplete")

    choices_by_case: dict[str, dict[str, str]] = {}
    matched = 0
    for case_id in case_ids:
        packet = packets[case_id]
        frozen = expected_choices[case_id]
        measured = recorded_choices[case_id]
        if not isinstance(packet, dict) or not isinstance(frozen, dict) or not isinstance(measured, dict):
            raise ReplayError(f"case {case_id} has malformed packet or choice rows")
        try:
            source_choice = harness_policy.choose(
                source_artifact, packet, source_artifact.get("compiler_identity")
            )["action"]
            refit_choice = harness_policy.choose(
                refit_artifact, packet, refit_artifact.get("compiler_identity")
            )["action"]
            patch_choice = workload_heuristic.choose(packet)
        except (KeyError, TypeError, ValueError) as error:
            raise ReplayError(f"{case_id} cannot be replayed: {error}") from error
        actual = {
            "unchanged_source_forest": source_choice,
            "target_refit": refit_choice,
            "direct_patch": patch_choice,
        }
        for method, choice in actual.items():
            if choice != frozen.get(method) or choice != measured.get(method):
                raise ReplayError(f"{case_id}/{method} differs from the frozen recorded choice")
            matched += 1
        choices_by_case[case_id] = actual

    return {
        "schema_version": 1,
        "status": "passed",
        "case_count": len(case_ids),
        "matched_choices": matched,
        "total_choices": len(case_ids) * len(METHODS),
        "choices_by_case": choices_by_case,
        "original_experiment": {
            "test_report_status": test_report["status"],
            "repair_gate_passed": repair_gate["passed"],
            "test_labels_used_for_fit_or_choice": test_report.get(
                "test_labels_used_for_fit_or_choice"
            ),
        },
        "interpretation": (
            "This is an offline replay of frozen choices. It does not rerun the experiment "
            "or constitute a new quality measurement. The published repair gate remains failed."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        bundle = json.loads(DEFAULT_BUNDLE.read_text(encoding="utf-8"))
        result = replay_bundle(bundle)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ReplayError,
        ValueError,
    ) as error:
        print(f"workload policy replay failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
