from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from scripts import policy_shift_experiment as shift

DESIGN_PATH = Path(__file__).resolve().parents[1] / "experiments" / "policy-shift-v1.json"


def _audit_inputs(target_ratio: float = 1.16, block_two_ratio: float | None = None):
    design = shift._design(DESIGN_PATH)
    cases = []
    choices = {"source": {}, "target": {}}
    audit_rows = shift._case_rows(design, "audit")
    for case in audit_rows:
        choices["source"][case["case_id"]] = {"action": "ijk"}
        choices["target"][case["case_id"]] = {"action": "ikj"}
        measurements = {}
        for arm, chosen_action, ratio in (
            ("source", "ijk", 1.01),
            ("target", "ikj", target_ratio),
        ):
            blocks = {}
            for block in (1, 2):
                block_ratio = block_two_ratio if arm == "target" and block == 2 and block_two_ratio else ratio
                rounds = []
                for round_number in range(1, 12):
                    elapsed = {name: 100 for name in shift.ACTION_NAMES}
                    elapsed[chosen_action] = math.ceil(100 * block_ratio)
                    rounds.append({"round": round_number, "elapsed_ns": elapsed})
                blocks[str(block)] = {"rounds": rounds}
            measurements[arm] = {"blocks": blocks}
        cases.append({**case, "measurements": measurements})
    return cases, choices, design


def test_frozen_design_has_disjoint_audit_groups() -> None:
    design = shift._design(DESIGN_PATH)
    cases = design["cases"]
    assert len(cases) == 24
    assert {case["partition"] for case in cases} == {"audit", "adaptation", "test"}
    assert len(shift._case_rows(design, "audit")) == 6
    assert len({case["group"] for case in shift._case_rows(design, "audit")}) == 3


def test_audit_gate_requires_both_independent_blocks_to_pass() -> None:
    cases, choices, design = _audit_inputs(target_ratio=1.16, block_two_ratio=1.01)

    result = shift.audit_gate(cases, choices, design)

    assert result["blocks"]["1"]["passed"] is True
    assert result["blocks"]["2"]["requirements"]["target_regret_exceeds_five_percent"] is False
    assert result["passed"] is False


def test_audit_gate_accepts_large_stable_compiler_shift() -> None:
    cases, choices, design = _audit_inputs(target_ratio=1.16)

    result = shift.audit_gate(cases, choices, design)

    assert result["passed"] is True
    for block in result["blocks"].values():
        assert block["group_balanced_target_regret"] == pytest.approx(1.16)
        assert block["group_balanced_source_regret"] == pytest.approx(1.01)
        assert block["target_minus_source_regret"] == pytest.approx(0.15)
        assert block["target_log_regret_mad"] == 0
        assert block["target_source_log_regret_mad"] == 0


def test_audit_gate_rejects_missing_cases_and_invalid_samples() -> None:
    cases, choices, design = _audit_inputs()
    with pytest.raises(shift.PolicyShiftError, match="six cases"):
        shift.audit_gate(cases[:-1], choices, design)

    malformed = copy.deepcopy(cases)
    malformed[0]["measurements"]["target"]["blocks"]["1"]["rounds"][0]["elapsed_ns"]["ijk"] = 0
    with pytest.raises(shift.PolicyShiftError, match="invalid runtime"):
        shift.audit_gate(malformed, choices, design)


def test_build_artifact_hash_validation_fails_closed(tmp_path: Path) -> None:
    artifact = tmp_path / "kernel.o"
    validation = tmp_path / "toolchain.json"
    artifact.write_bytes(b"artifact")
    validation.write_text("{}\n", encoding="utf-8")
    report = {
        "artifacts": {"object": {"path": str(artifact), "sha256": shift._sha256(artifact)}},
        "validation_report": {"path": str(validation), "sha256": shift._sha256(validation)},
    }

    shift._validate_artifacts(report)
    artifact.write_bytes(b"changed")

    with pytest.raises(shift.PolicyShiftError, match="artifact changed"):
        shift._validate_artifacts(report)


def test_measurement_adapter_preserves_features_and_action_executables(tmp_path: Path) -> None:
    case = shift._case_rows(shift._design(DESIGN_PATH), "audit")[0]
    features = {"m": 47, "n": 67, "k": 41}
    build = {
        "case_fingerprint": "frozen-case-build",
        "source_features": features,
        "actions": {
            action: {
                "code_identity": f"identity-{action}",
                "correctness": {
                    "link": {"executable_path": str(tmp_path / f"{action}.bin")}
                },
            }
            for action in shift.ACTION_NAMES
        },
    }

    adapted = shift._measurement_case(case, build)

    assert adapted["partition"] == "discovery"
    assert adapted["family"] == case["group"]
    assert adapted["source_features"] == features
    assert adapted["build"]["case_fingerprint"] == "frozen-case-build"
    for action in shift.ACTION_NAMES:
        assert adapted["build"]["actions"][action]["artifacts"]["executable"]["path"] == str(
            tmp_path / f"{action}.bin"
        )


def test_shared_measurement_helper_accepts_adapter_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = shift._case_rows(shift._design(DESIGN_PATH), "audit")[0]
    features = {"m": 47, "n": 67, "k": 41}
    executables = []
    actions = {}
    for action in shift.ACTION_NAMES:
        executable = tmp_path / f"{action}.bin"
        executable.write_bytes(action.encode())
        if action != "ijk":
            executables.append((action, executable))
        actions[action] = {
            "code_identity": f"identity-{action}",
            "correctness": {"link": {"executable_path": str(executable)}},
        }
    build = {
        "case_fingerprint": "frozen-case-build",
        "source_features": features,
        "actions": actions,
    }
    adapted = shift._measurement_case(case, build)
    aliases = {action: action for action in shift.ACTION_NAMES}
    aliases["ijk"] = "stock"
    monkeypatch.setattr(
        shift.contraction,
        "_identity_actions",
        lambda _case: (executables, aliases),
    )
    monkeypatch.setattr(
        shift.contraction,
        "_pilot_iterations",
        lambda *_args: (2, {"pilot_cache_sha256": "a" * 64}),
    )

    def fake_benchmark(
        named_executables: list[tuple[str, Path]], *, iterations: int, rounds: int, seed: int
    ) -> dict[str, object]:
        names = [name for name, _path in named_executables]
        return {
            "status": "measured",
            "iterations": iterations,
            "rounds": rounds,
            "stock_checksum": "0123456789abcdef",
            "summaries": {name: {} for name in names},
            "round_records": [
                {
                    "round": round_number,
                    "order": str(seed),
                    "measurements": {
                        name: {"elapsed_ns": round_number * 100}
                        for name in names
                    },
                }
                for round_number in range(1, rounds + 1)
            ],
        }

    monkeypatch.setattr(shift.contraction.benchmark, "run_benchmark", fake_benchmark)
    measured = shift.contraction._measure_cases(
        [adapted],
        {"benchmark_rounds": 11, "seed": 20261021, "target_sample_ms": 20},
        tmp_path / "timing",
        blocks=(1,),
    )

    assert measured[0]["source_features"] == features
    assert len(measured[0]["benchmark"]["round_records"]) == 11


def test_native_result_links_with_explicit_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = tmp_path / "SDK"
    sdk.mkdir()
    linker = tmp_path / "clang"
    driver = tmp_path / "driver.o"
    object_path = tmp_path / "kernel.o"
    executable = tmp_path / "kernel"
    artifacts = tmp_path / "artifacts"
    checksum = "0123456789abcdef"
    calls: list[list[str]] = []

    def fake_command(name: str, command: list[str], _directory: Path, _timeout: int):
        calls.append(command)
        if name.endswith("_link"):
            Path(command[-1]).write_bytes(b"linked executable")
            stdout = b""
        else:
            stdout = (
                '{"correct":true,"iterations":2,"checksum":"'
                + checksum
                + '","elapsed_ns":100}\n'
            ).encode()
        return {
            "passed": True,
            "return_code": 0,
            "command": command,
            "_stdout": stdout,
            "_stderr": b"",
        }

    monkeypatch.setattr(shift.experiment, "_run_command", fake_command)

    result = shift.native_result_with_sdk(
        "pilot",
        object_path,
        executable,
        driver_object=driver,
        linker=linker,
        sdk=sdk,
        iterations=2,
        artifacts_dir=artifacts,
        timeout_seconds=5,
        expected_checksum=checksum,
    )

    assert calls[0][1:4] == ["-O2", "-isysroot", str(sdk)]
    assert result["link"]["status"] == "passed"
    assert result["runtime"]["status"] == "passed"
    assert result["runtime"]["measurements"][0]["checksum_matches_stock"] is True
