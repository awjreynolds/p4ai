"""Offline contract and mocked native-command tests for the harness demo."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import harness_demo as demo
from scripts import harness_policy


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def _reference(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": _sha256(path)}


def _model() -> dict:
    return {
        "schema_version": 1,
        "library": "scikit-learn",
        "library_version": "test",
        "parameters": {"n_estimators": 1, "n_jobs": 1},
        "features": ["x"],
        "training_rows": 1,
        "training_targets": "test objective",
        "estimators": [{
            "children_left": [-1],
            "children_right": [-1],
            "feature": [-2],
            "threshold": [-2.0],
            "value": [0.0],
        }],
    }


def _fixture(tmp_path: Path) -> dict[str, Path | dict]:
    root = tmp_path / "audit"
    root.mkdir()
    design_path = tmp_path / "policy-shift-v1.json"
    compiler = tmp_path / "clang"
    linker = tmp_path / "link-driver"
    sdk = tmp_path / "MacOSX.sdk"
    sdk.mkdir()
    compiler.write_bytes(b"compiler-v1")
    linker.write_bytes(b"linker-v1")
    toolchain = {
        "path": str(compiler),
        "sha256": _sha256(compiler),
        "version": {"return_code": 0, "stdout": "test clang", "stderr": ""},
        "default_target": {"return_code": 0, "stdout": "arm64-apple-darwin", "stderr": ""},
        "compiler_identity": {
            "sha256": _sha256(compiler),
            "target": "arm64-apple-darwin",
        },
        "sdk": {"path": str(sdk), "version": "99.0"},
        "linker": {"path": str(linker), "sha256": _sha256(linker)},
        "target": "arm64-apple-darwin",
        "target_cpu": "apple-m3",
    }
    design = {
        "schema_version": 1,
        "name": "policy-shift-v1",
        "target": "arm64-apple-darwin",
        "target_cpu": "apple-m3",
        "linker": str(linker),
        "linker_sha256": _sha256(linker),
        "sdk": str(sdk),
        "kernel_flags": ["-target", "arm64-apple-darwin"],
        "driver_flags": ["-target", "arm64-apple-darwin"],
        "cases": [{
            "case_id": "audit-1",
            "partition": "audit",
            "group": "shape-a",
            "spec": {"m": 3, "n": 5, "k": 7},
        }],
    }
    _save(design_path, design)

    validation_path = root / "validation.json"
    kernel_source = root / "kernels" / "ijk.c"
    driver_object = root / "driver.o"
    baseline_object = root / "baseline.o"
    baseline_executable = root / "baseline"
    for path, data in (
        (kernel_source, b"int selected_kernel(void) { return 0; }\n"),
        (driver_object, b"driver object"),
        (baseline_object, b"kernel object"),
        (baseline_executable, b"native executable"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    kernel_flags = [*design["kernel_flags"], "-isysroot", str(sdk)]
    driver_flags = [*design["driver_flags"], "-isysroot", str(sdk)]
    _save(validation_path, {
        "schema_version": 1,
        "compiler_arm": "source",
        "toolchain": toolchain,
        "kernel_flags": kernel_flags,
        "driver_flags": driver_flags,
    })

    training_provenance = {"training_bundle_sha256": "b" * 64}
    artifact = harness_policy.make_artifact(
        _model(), toolchain["compiler_identity"], training_provenance
    )
    policy_path = root / "policy-source.json"
    _save(policy_path, {"schema_version": 1, "artifact": artifact})
    packet = {
        "features": {"x": [0, 0, 0, 0, 0, 0]},
        "actions": [
            {"id": str(index), "description": action}
            for index, action in enumerate(harness_policy.ACTION_ORDERS)
        ],
    }
    packet_sha = demo._fingerprint(packet)

    report_path = root / "cases" / "audit-1" / "build-report.json"
    build_report = {
        "schema_version": 1,
        "status": "passed",
        "case_id": "audit-1",
        "partition": "audit",
        "group": "shape-a",
        "spec": design["cases"][0]["spec"],
        "compiler_arm": "source",
        "case_fingerprint": "c" * 64,
        "stock_checksum": "0123456789abcdef",
        "toolchain": toolchain,
        "kernel_flags": kernel_flags,
        "driver_flags": driver_flags,
        "validation_report": _reference(validation_path),
        "build_artifacts": {
            "kernel_sources": {"ijk": _reference(kernel_source)},
            "driver_object": _reference(driver_object),
            "kernel_objects": {"ijk": _reference(baseline_object)},
            "executables": {"ijk": _reference(baseline_executable)},
        },
        "actions": {
            "ijk": {
                "status": "passed",
                "code_identity": hashlib.sha256(b"kernel object").hexdigest(),
                "correctness": {
                    "link": {"status": "passed"},
                    "runtime": {
                        "status": "passed",
                        "measurements": [{
                            "correct": True,
                            "checksum_matches_stock": True,
                            "checksum": "0123456789abcdef",
                        }],
                    },
                },
            }
        },
    }
    _save(report_path, build_report)

    manifest_path = root / "prepared-audit.json"
    prepared = {
        "schema_version": 1,
        "status": "prepared",
        "native_measurements_started": False,
        "output": str(root),
        "design": str(design_path),
        "provenance": {
            "design_sha256": _sha256(design_path),
            "platform": {"system": "Darwin", "machine": "arm64"},
        },
        "audit_case_ids": ["audit-1"],
        "source_policy_training_provenance": training_provenance,
        "builds_by_compiler": {
            "source": {"audit-1": {
                "build_report_path": str(report_path),
                "build_report_sha256": _sha256(report_path),
                "case_fingerprint": "c" * 64,
            }},
        },
        "policy_artifacts": {"source": _reference(policy_path)},
        "frozen_source_policy_choices": {"source": {"audit-1": {
            "action": "ijk",
            "action_id": "0",
            "scores": {action: 0.0 for action in harness_policy.ACTION_ORDERS},
            "packet_sha256": packet_sha,
        }}},
        "packets_by_compiler": {"source": {"audit-1": packet}},
    }
    _save(manifest_path, prepared)
    return {
        "root": root,
        "design_path": design_path,
        "manifest_path": manifest_path,
        "compiler": compiler,
        "driver_object": driver_object,
        "baseline_object": baseline_object,
        "baseline_executable": baseline_executable,
        "source": kernel_source,
        "build_report": report_path,
    }


def _mock_native_commands(
    monkeypatch: pytest.MonkeyPatch, *, diverge: bool = False
) -> dict[str, list]:
    calls: dict[str, list] = {"compile": [], "native": []}

    def fake_run_command(name, command, artifacts_dir, timeout_seconds):
        del name, artifacts_dir, timeout_seconds
        calls["compile"].append(command)
        output = Path(command[command.index("-o") + 1])
        object_data = b"kernel object"
        if diverge and len(calls["compile"]) == 2:
            object_data = b"changed kernel object"
        output.write_bytes(object_data)
        return {"passed": True, "return_code": 0, "stderr_sha256": "d" * 64}

    def fake_native_result(
        name, object_path, executable_path, *, driver_object, linker, sdk, iterations,
        artifacts_dir, timeout_seconds, expected_checksum,
    ):
        del name, object_path, linker, iterations, artifacts_dir, timeout_seconds
        repeat = len(calls["native"]) + 1
        calls["native"].append({"driver_object": driver_object, "sdk": sdk})
        executable_data = b"native executable"
        if diverge and repeat == 2:
            executable_data = b"different executable"
        executable_path.write_bytes(executable_data)
        return {
            "link": {"status": "passed", "return_code": 0},
            "runtime": {
                "status": "passed",
                "measurements": [{
                    "correct": True,
                    "iterations": 1,
                    "checksum": expected_checksum,
                    "checksum_matches_stock": True,
                    "elapsed_ns": 123456,
                    "duration_ms": 123,
                }],
            },
        }

    monkeypatch.setattr(demo.experiment, "_run_command", fake_run_command)
    monkeypatch.setattr(
        demo.policy_shift_experiment, "native_result_with_sdk", fake_native_result
    )
    monkeypatch.setattr(demo, "_host_identity", lambda _prepared: {"system": "Darwin", "machine": "arm64"})
    monkeypatch.setattr(
        demo.quality_experiment,
        "code_identity",
        lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return calls


def test_replays_frozen_choice_and_correctness_checks_two_independent_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    calls = _mock_native_commands(monkeypatch)

    report = demo.run_demo(
        case_id="audit-1",
        compiler_arm="source",
        output=tmp_path / "demo-output",
        prepared_path=fixture["manifest_path"],
    )

    assert report["status"] == "passed"
    assert report["decision"]["action"] == "ijk"
    assert report["comparisons"]["decision_repeated"] is True
    assert report["comparisons"]["rebuild_object_sha256_equal"] is True
    assert report["comparisons"]["rebuild_code_identity_equal"] is True
    assert report["comparisons"]["rebuild_executable_sha256_equal"] is True
    assert report["comparisons"]["rebuild_executable_content_equal"] is True
    assert report["comparisons"]["rebuild_executable_size_equal"] is True
    assert report["comparisons"]["prepared_executable_content_equal"] is True
    assert [Path(command[command.index("-o") + 1]).parent.name for command in calls["compile"]] == [
        "repeat-1", "repeat-2"
    ]
    assert calls["native"] == [
        {"driver_object": fixture["driver_object"], "sdk": tmp_path / "MacOSX.sdk"},
        {"driver_object": fixture["driver_object"], "sdk": tmp_path / "MacOSX.sdk"},
    ]
    assert all(row["correctness"]["status"] == "passed" for row in report["repeats"])
    serialized = json.dumps(report)
    assert "elapsed_ns" not in serialized
    assert "duration_ms" not in serialized
    assert "no performance benchmark was collected" in report["interpretation"]


def test_reports_correctness_but_fails_repeatability_when_rebuilds_diverge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    _mock_native_commands(monkeypatch, diverge=True)

    report = demo.run_demo(
        case_id="audit-1",
        compiler_arm="source",
        output=tmp_path / "demo-output",
        prepared_path=fixture["manifest_path"],
    )

    assert report["correctness_status"] == "passed"
    assert report["status"] == "not_repeatable"
    assert report["repeatability_status"] == "failed"
    assert report["comparisons"]["rebuild_object_sha256_equal"] is False
    assert report["comparisons"]["rebuild_code_identity_equal"] is False
    assert report["comparisons"]["rebuild_executable_content_equal"] is False


def test_cli_returns_failure_for_nonrepeatable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo, "run_demo", lambda **_kwargs: {"status": "not_repeatable"})

    result = demo.main([
        "--prepared", str(tmp_path / "prepared-audit.json"),
        "--case-id", "audit-1",
        "--compiler", "source",
        "--output", str(tmp_path / "demo-output"),
    ])

    assert result == 1
    assert json.loads(capsys.readouterr().out)["status"] == "not_repeatable"


def test_rejects_changed_kernel_source_before_compiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    fixture["source"].write_bytes(b"tampered kernel")
    calls = _mock_native_commands(monkeypatch)

    with pytest.raises(demo.HarnessDemoError, match="selected kernel source.*SHA-256 changed"):
        demo.run_demo(
            case_id="audit-1",
            compiler_arm="source",
            output=tmp_path / "demo-output",
            prepared_path=fixture["manifest_path"],
        )

    assert calls["compile"] == []
    assert calls["native"] == []


def test_rejects_policy_bound_to_different_compiler_before_compiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    manifest_path = fixture["manifest_path"]
    prepared = json.loads(manifest_path.read_text())
    ref = prepared["policy_artifacts"]["source"]
    policy_path = Path(ref["path"])
    wrapper = json.loads(policy_path.read_text())
    wrong_identity = {"sha256": "e" * 64, "target": "arm64-apple-darwin"}
    wrapper["artifact"] = harness_policy.make_artifact(
        _model(), wrong_identity, {"training_bundle_sha256": "b" * 64}
    )
    _save(policy_path, wrapper)
    ref["sha256"] = _sha256(policy_path)
    _save(manifest_path, prepared)
    calls = _mock_native_commands(monkeypatch)

    with pytest.raises(demo.HarnessDemoError, match="policy compiler identity differs"):
        demo.run_demo(
            case_id="audit-1",
            compiler_arm="source",
            output=tmp_path / "demo-output",
            prepared_path=manifest_path,
        )

    assert calls["compile"] == []
    assert calls["native"] == []


def test_rejects_manifest_packet_that_does_not_match_frozen_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    manifest_path = fixture["manifest_path"]
    prepared = json.loads(manifest_path.read_text())
    prepared["packets_by_compiler"]["source"]["audit-1"]["features"]["x"][0] = 99
    _save(manifest_path, prepared)
    calls = _mock_native_commands(monkeypatch)

    with pytest.raises(demo.HarnessDemoError, match="prepared packet differs"):
        demo.run_demo(
            case_id="audit-1",
            compiler_arm="source",
            output=tmp_path / "demo-output",
            prepared_path=manifest_path,
        )

    assert calls["compile"] == []
    assert calls["native"] == []
