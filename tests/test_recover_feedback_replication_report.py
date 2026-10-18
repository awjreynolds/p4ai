"""Cache-only report recovery guard and identity-mapping checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import feedback_replication_experiment as replication
from scripts import recover_feedback_replication_report as recovery

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "experiments" / "feedback-replication-v1-recovery.json"
SOURCE_DESIGN = ROOT / "experiments" / "compiler-feedback-v1.json"
SOURCE_BUNDLE = ROOT / "docs" / "results" / "compiler-feedback-v1.json"


def _prepared_output(tmp_path: Path) -> tuple[dict, Path]:
    design = replication._design(DESIGN, SOURCE_DESIGN)
    output = tmp_path / "run"
    rows = []
    for case in replication._cases(design):
        report_path = output / "cases" / case["partition"] / case["case_id"] / "build" / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{}\n", encoding="utf-8")
        rows.append({
            **case,
            "build_report_path": str(report_path),
            "build_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        })
    output.mkdir(parents=True, exist_ok=True)
    (output / "builds.json").write_text(json.dumps({"cases": rows}), encoding="utf-8")
    return design, output


def test_recovery_fails_on_missing_timing_cache_before_measurement_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _design, output = _prepared_output(tmp_path)
    calls = []
    monkeypatch.setattr(replication, "run_measure", lambda *_args, **_kwargs: calls.append("measure"))
    monkeypatch.setattr(
        replication.contraction.benchmark,
        "_run_one",
        lambda *_args, **_kwargs: calls.append("benchmark"),
    )
    validation = tmp_path / "validation.json"
    validation.write_text(json.dumps({"llvm": {"source": str(ROOT)}}), encoding="utf-8")

    with pytest.raises(recovery.ReportRecoveryError, match="refuses missing timing evidence"):
        recovery.run_recovery(DESIGN, validation, output, SOURCE_BUNDLE)

    assert calls == []


def test_group_restoration_requires_family_to_match_frozen_build_identity() -> None:
    built = [{"case_id": "case-a", "group": "group-a", "family": "group-a"}]
    attached = [{"case_id": "case-a", "family": "group-a"}]

    assert recovery._restore_group_identity(attached, built) == [
        {"case_id": "case-a", "family": "group-a", "group": "group-a"}
    ]

    mismatched = [{"case_id": "case-a", "family": "group-b"}]
    with pytest.raises(recovery.ReportRecoveryError, match="family/group identity changed"):
        recovery._restore_group_identity(mismatched, built)


def test_cache_only_guards_block_execution_provider_and_benchmark_calls() -> None:
    with recovery._cache_only_guards():
        with pytest.raises(recovery.ReportRecoveryError, match="subprocess execution"):
            import subprocess

            subprocess.run(["true"])
        with pytest.raises(recovery.ReportRecoveryError, match="provider request"):
            replication.feedback._query_provider({}, "jev", Path("unused"), allow_call=True)
        with pytest.raises(recovery.ReportRecoveryError, match="benchmark run"):
            replication.contraction.benchmark._run_one("fixture", Path("unused"), 1, "test")


def test_cache_only_guards_allow_and_record_only_exact_read_only_git_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "llvm source"
    command = ["git", "-C", str(source.resolve()), "rev-parse", "HEAD"]
    completed = subprocess.CompletedProcess(command, 0, b"a" * 40 + b"\n", b"")
    fake_run = Mock(return_value=completed)
    monkeypatch.setattr(subprocess, "run", fake_run)
    executed = []

    with recovery._cache_only_guards(source, executed):
        result = subprocess.run(command, capture_output=True, check=False, timeout=10)

    assert result is completed
    fake_run.assert_called_once_with(command, capture_output=True, check=False, timeout=10)
    assert executed == [
        {
            "command": command,
            "shell": False,
            "capture_output": True,
            "check": False,
            "timeout_seconds": 10,
            "return_code": 0,
            "stdout": "a" * 40 + "\n",
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }
    ]


def test_cache_only_guards_run_the_exact_revision_check_against_git(
) -> None:
    command = ["git", "-C", str(ROOT.resolve()), "rev-parse", "HEAD"]
    executed = []

    with recovery._cache_only_guards(ROOT, executed):
        result = subprocess.run(command, capture_output=True, check=False, timeout=10)

    assert result.returncode == 0
    assert result.stdout.decode("utf-8").strip()
    assert executed[0]["command"] == command
    assert executed[0]["stdout"] == result.stdout.decode("utf-8")


def test_cache_only_guards_reject_near_miss_and_shell_command_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "llvm source"
    command = ["git", "-C", str(source.resolve()), "rev-parse", "HEAD"]
    fake_run = Mock()
    monkeypatch.setattr(subprocess, "run", fake_run)

    with recovery._cache_only_guards(source, []):
        with pytest.raises(recovery.ReportRecoveryError, match="unapproved subprocess"):
            subprocess.run(
                [*command[:-1], "HEAD; touch /tmp/not-allowed"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        with pytest.raises(recovery.ReportRecoveryError, match="unapproved subprocess"):
            subprocess.run(command, capture_output=True, check=False, timeout=10, shell=True)

    fake_run.assert_not_called()
