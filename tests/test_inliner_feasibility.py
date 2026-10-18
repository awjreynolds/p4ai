"""Contract tests for the interactive inliner feasibility smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import advisor
from scripts import inliner_feasibility as feasibility


def test_normalized_body_ignores_module_path_and_metadata_ids() -> None:
    first = '''; ModuleID = 'left.c'
define i32 @public_caller(i32 %x) {
entry:
  %y = call i32 @transform(i32 %x), !dbg !3
  ret i32 %y, !dbg !4
}
'''
    second = '''; ModuleID = 'right.c'
define i32 @public_caller(i32 %x) {
entry:
  %y = call i32 @transform(i32 %x), !dbg !88
  ret i32 %y, !dbg !89
}
'''

    left = feasibility.normalized_caller_body(first)
    right = feasibility.normalized_caller_body(second)

    assert left["body_sha256"] == right["body_sha256"]
    assert left["calls_transform"] is True
    assert left["body_lines"] == 3


def test_normalized_body_distinguishes_call_from_inline_code() -> None:
    called = '''define i32 @public_caller(i32 %x) {
entry:
  %y = call i32 @transform(i32 %x)
  ret i32 %y
}
'''
    inlined = '''define i32 @public_caller(i32 %x) {
entry:
  %y = mul i32 %x, 17
  ret i32 %y
}
'''

    before = feasibility.normalized_caller_body(called)
    after = feasibility.normalized_caller_body(inlined)

    assert before["calls_transform"] is True
    assert after["calls_transform"] is False
    assert before["body_sha256"] != after["body_sha256"]


@pytest.mark.parametrize(
    ("policy", "default", "expected"),
    [("0", [1], 0), ("1", [0], 1), ("default", [1], 1)],
)
def test_policy_choice_covers_forced_and_compiler_default(
    policy: str, default: list[int], expected: int
) -> None:
    assert feasibility.choose_advice(policy, default) == expected


def test_default_choice_requires_boolean_scalar() -> None:
    with pytest.raises(ValueError, match="one boolean value"):
        feasibility.choose_advice("default", [0, 1])


def test_frontend_failure_writes_failed_report_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    clang = tool_dir / "clang"
    opt = tool_dir / "opt"
    for tool in (clang, opt):
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)

    llvm_source = tmp_path / "llvm"
    reader_path = llvm_source / "llvm/lib/Analysis/models/log_reader.py"
    reader_path.parent.mkdir(parents=True)
    reader_path.write_text("# test placeholder\n", encoding="utf-8")

    monkeypatch.setattr(advisor, "load_log_reader", lambda _source: object())
    monkeypatch.setattr(feasibility, "_llvm_revision", lambda _source: None)
    monkeypatch.setattr(
        feasibility,
        "_tool_identity",
        lambda path: {"path": str(path), "sha256": "test", "version": "test"},
    )
    monkeypatch.setattr(
        feasibility,
        "_run_to_files",
        lambda command, _timeout, stdout, stderr: {
            "command": command,
            "return_code": 1,
            "timed_out": False,
            "error": "frontend failed",
            "duration_ms": 1,
            "stdout": {"path": str(stdout), "bytes": 0},
            "stderr": {"path": str(stderr), "bytes": 0},
            "stderr_head": "frontend failed",
            "stderr_tail": "frontend failed",
        },
    )

    output_dir = tmp_path / "output"
    result = feasibility.main(
        [
            "--repo-root",
            str(feasibility.ROOT),
            "--clang",
            str(clang),
            "--opt",
            str(opt),
            "--llvm-source",
            str(llvm_source),
            "--output-dir",
            str(output_dir),
        ]
    )

    report = json.loads((output_dir / "report.json").read_text())
    assert result == 1
    assert report["status"] == "failed"
    assert report["frontend"]["error"] == "frontend failed"
    assert report["runs"] == []
    assert report["distinct_0_vs_1_normalized_caller_body"] is False
    assert '"status": "failed"' in capsys.readouterr().out
