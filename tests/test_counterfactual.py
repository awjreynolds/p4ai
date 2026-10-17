from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import advisor  # noqa: E402
import counterfactual  # noqa: E402

RUN_IDENTITY = {"ir_sha256": "ir-digest"}


def _observation(index: int, actions: tuple[int, ...], *, digest: str | None = None):
    return {
        "run_identity": RUN_IDENTITY,
        "context": "pressure_dot",
        "observation": index,
        "features_sha256": digest or f"features-{index}",
        "actions": [{"id": str(action)} for action in actions],
    }


def _trace_state(index: int, actions: tuple[int, ...], first_action: int):
    return {
        **counterfactual._state(_observation(index, actions)),
        "first_action": first_action,
    }


def test_quantiles_select_nearest_rank_sites_and_direct_indices_are_supported():
    indices, metadata = counterfactual._select_sites(
        4, site_quantiles=(0.25, 0.75), site_indices=None
    )
    assert indices == [0, 2]
    assert metadata["kind"] == "nearest_rank_quantiles"

    indices, metadata = counterfactual._select_sites(
        5, site_quantiles=None, site_indices=[4]
    )
    assert indices == [4]
    assert metadata["kind"] == "ordinal_indices"


def test_quantile_selection_rejects_duplicate_sites_when_trace_is_too_short():
    with pytest.raises(counterfactual.CounterfactualError, match="duplicate states"):
        counterfactual._select_sites(1, site_quantiles=(0.25, 0.75), site_indices=None)


def test_one_choice_policy_replays_prefix_changes_target_and_uses_first_suffix():
    observations = [
        _observation(0, (1, 3)),
        _observation(1, (2, 4, 6)),
        _observation(2, (5, 8)),
    ]
    trace = [
        _trace_state(0, (1, 3), 1),
        _trace_state(1, (2, 4, 6), 2),
        _trace_state(2, (5, 8), 5),
    ]
    target_state = {
        key: trace[1][key] for key in counterfactual._state(observations[1])
    }
    policy = counterfactual._OneChoicePolicy(trace, 1, target_state, 6)

    assert policy(observations[0]) == 1
    assert policy(observations[1]) == 6
    assert policy(observations[2]) == 5
    assert policy.target_positions == [1]


def test_one_choice_policy_refuses_prefix_or_target_state_drift():
    observations = [_observation(0, (1, 3)), _observation(1, (2, 4))]
    trace = [
        _trace_state(0, (1, 3), 1),
        _trace_state(1, (2, 4), 2),
    ]
    target_state = {
        key: trace[1][key] for key in counterfactual._state(observations[1])
    }

    prefix_policy = counterfactual._OneChoicePolicy(trace, 1, target_state, 4)
    with pytest.raises(advisor.ReplayMismatch, match="prefix diverged"):
        prefix_policy(_observation(0, (1, 3), digest="changed-prefix"))

    target_policy = counterfactual._OneChoicePolicy(trace, 1, target_state, 4)
    assert target_policy(observations[0]) == 1
    with pytest.raises(advisor.ReplayMismatch, match="target state"):
        target_policy(_observation(1, (2, 4), digest="changed-target"))


def test_one_choice_policy_tracks_repeated_target_occurrences_for_final_gate():
    observations = [
        _observation(0, (1, 3)),
        _observation(1, (2, 4)),
    ]
    trace = [_trace_state(0, (1, 3), 1), _trace_state(1, (2, 4), 2)]
    target_state = {
        key: trace[1][key] for key in counterfactual._state(observations[1])
    }
    policy = counterfactual._OneChoicePolicy(trace, 1, target_state, 4)

    assert policy(observations[0]) == 1
    assert policy(observations[1]) == 4
    assert policy(observations[1]) == 2
    assert policy.target_positions == [1, 2]


def test_collect_case_saves_selected_packets_and_baseline_artifact_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "kernel.c"
    driver = tmp_path / "driver.c"
    clang = tmp_path / "clang"
    llc = tmp_path / "llc"
    linker = tmp_path / "linker"
    llvm_source = tmp_path / "llvm-source"
    llvm_source.mkdir()
    for path in (source, driver, clang, llc, linker):
        path.write_text(path.name, encoding="utf-8")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    inputs = {
        "report_path": tmp_path / "validation.json",
        "report": {
            "llvm": {"source_revision": "pinned", "llc_sha256": digest(llc)},
            "checks": {
                "compiler_provenance": {
                    "build_manifest": {"manifest_sha256": "manifest"}
                }
            },
        },
        "paths": {"llvm_source": llvm_source, "llc": llc, "linker": linker},
        "replay_identity": {
            "llc_version_stdout_sha256": "version",
            "llvm_source_revision": "pinned",
            "mlgo_guard_source_sha256": "guard",
            "guard_patch_sha256": "patch",
            "build_manifest_sha256": "manifest",
        },
        "iterations": 3,
        "clang": clang,
    }
    monkeypatch.setattr(counterfactual, "_validated_toolchain", lambda _path: inputs)

    def run_command(name: str, command: list[str], artifacts_dir: Path, _timeout: int):
        output = command[command.index("-o") + 1]
        Path(output).write_bytes(f"{name}-output".encode())
        stdout_path = artifacts_dir / "commands" / f"{name}.stdout"
        stderr_path = artifacts_dir / "commands" / f"{name}.stderr"
        stdout_path.write_bytes(b"")
        stderr_path.write_bytes(b"")
        return {
            "command": command,
            "return_code": 0,
            "duration_ms": 2,
            "stdout_path": str(stdout_path),
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_path": str(stderr_path),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "error": None,
            "passed": True,
            "_stdout": b"",
            "_stderr": b"",
        }

    monkeypatch.setattr(counterfactual.experiment, "_run_command", run_command)

    def native_result(
        name: str,
        _object_path: Path,
        executable_path: Path,
        **kwargs: Any,
    ):
        executable_path.write_bytes(f"{name}-executable".encode())
        checksum = "0123456789abcdef"
        return {
            "link": {
                "status": "passed",
                "executable_path": str(executable_path),
                "executable_sha256": digest(executable_path),
            },
            "runtime": {
                "status": "passed",
                "measurements": [
                    {
                        "correct": True,
                        "iterations": kwargs["iterations"],
                        "checksum": checksum,
                        "checksum_matches_stock": True,
                        "elapsed_ns": 20,
                    }
                ],
            },
        }

    monkeypatch.setattr(counterfactual, "_native_result", native_result)

    def run_interactive(
        command: list[str],
        *,
        policy: Any,
        policy_name: str,
        stdout_path: Path,
        stderr_path: Path,
        run_identity: dict[str, Any],
        **_kwargs: Any,
    ):
        action_menus = ((0,), (2, 5), (3, 6), tuple(range(8)))
        decisions = []
        for index, action_ids in enumerate(action_menus):
            actions = [
                {"id": str(action_id), "description": f"candidate {action_id}"}
                for action_id in action_ids
            ]
            observation = {
                "run_identity": run_identity,
                "context": "pressure",
                "observation": index,
                "features_sha256": f"digest-{index}",
                "actions": actions,
                "provider_packet": {
                    "task": "choose",
                    "features": {"cost": list(range(len(actions)))},
                    "feature_descriptions": {"cost": "cost"},
                    "actions": actions,
                },
            }
            action = policy(observation)
            decisions.append(
                {
                    "run_identity": run_identity,
                    "context": "pressure",
                    "observation": index,
                    "features_sha256": f"digest-{index}",
                    "eligible_actions": list(action_ids),
                    "action_index": action,
                }
            )
        object_path = Path(command[command.index("-o") + 1])
        object_path.write_bytes(b"first-object")
        stdout_path.write_bytes(b"")
        stderr_path.write_bytes(b"")
        empty_hash = hashlib.sha256(b"").hexdigest()
        return {
            "compiler_command": command,
            "return_code": 0,
            "timed_out": False,
            "communication_error": None,
            "duration_ms": 7,
            "decision_count": len(decisions),
            "decisions": decisions,
            "stdout_path": str(stdout_path),
            "stdout_sha256": empty_hash,
            "stderr_path": str(stderr_path),
            "stderr_sha256": empty_hash,
        }

    monkeypatch.setattr(counterfactual.advisor, "run_interactive", run_interactive)

    report = counterfactual.collect_case(
        tmp_path / "validation.json", source, driver, tmp_path / "case", iterations=3
    )

    assert report["status"] == "passed"
    first = report["baseline"]["first"]
    assert first["ambiguous_state_count"] == 3
    assert first["selectable_state_count"] == 3
    assert [site["site_index"] for site in first["sites"]] == [0, 2]
    assert [site["trace_index"] for site in first["sites"]] == [1, 3]
    assert [site["candidate_count"] for site in first["sites"]] == [2, 8]
    assert (
        report["paths"]["stock_executable"]
        == report["baseline"]["stock"]["link"]["executable_path"]
    )
    saved = json.loads((tmp_path / "case" / "report.json").read_text())
    assert saved["baseline"]["first"]["sites"] == first["sites"]


@pytest.mark.parametrize("prefix_drift", [False, True])
def test_compile_choice_links_only_after_exact_state_and_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix_drift: bool
):
    kernel = tmp_path / "kernel.c"
    driver = tmp_path / "driver.c"
    ir = tmp_path / "kernel.ll"
    clang = tmp_path / "clang"
    llc = tmp_path / "llc"
    linker = tmp_path / "linker"
    llvm_source = tmp_path / "llvm-source"
    llvm_source.mkdir()
    for path in (kernel, driver, ir, clang, llc, linker):
        path.write_text(path.name, encoding="utf-8")

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    replay_identity = {
        "llc_version_stdout_sha256": "version",
        "llvm_source_revision": "pinned",
        "mlgo_guard_source_sha256": "guard",
        "guard_patch_sha256": "patch",
        "build_manifest_sha256": "manifest",
    }
    inputs = {
        "report_path": tmp_path / "validation.json",
        "report": {
            "llvm": {"source_revision": "pinned", "llc_sha256": digest(llc)},
            "checks": {
                "compiler_provenance": {
                    "build_manifest": {"manifest_sha256": "manifest"}
                }
            },
        },
        "paths": {"llvm_source": llvm_source, "llc": llc, "linker": linker},
        "replay_identity": replay_identity,
        "iterations": 3,
        "clang": clang,
    }
    monkeypatch.setattr(counterfactual, "_validated_toolchain", lambda _path: inputs)

    identity = counterfactual.validate._replay_identity(
        counterfactual.validate._interactive_llc_command(llc, ir, tmp_path / "any.o"),
        ir_sha256=digest(ir),
        llc_sha256=digest(llc),
        llc_version_sha256="version",
        source_revision="pinned",
        guard_source_sha256="guard",
        patch_sha256="patch",
        build_manifest_sha256="manifest",
    )
    observations = [
        _observation(0, (0, 3)),
        _observation(1, (2, 4)),
        _observation(2, (5, 7)),
    ]
    states = [
        counterfactual._state({**observation, "run_identity": identity})
        for observation in observations
    ]
    trace = [
        {**states[0], "first_action": 0},
        {**states[1], "first_action": 2},
        {**states[2], "first_action": 5},
    ]
    stock_executable = tmp_path / "stock-executable"
    stock_executable.write_bytes(b"stock")
    validation = {
        "report_path": str(inputs["report_path"]),
        "source_revision": "pinned",
        "build_manifest_sha256": "manifest",
        "replay_identity": replay_identity,
    }
    case = {
        "schema_version": counterfactual.SCHEMA_VERSION,
        "status": "passed",
        "validation": validation,
        "kernel": {"path": str(kernel), "sha256": digest(kernel)},
        "driver": {"path": str(driver), "sha256": digest(driver)},
        "ir": {"path": str(ir), "sha256": digest(ir)},
        "iterations": 3,
        "baseline": {
            "stock": {
                "link": {
                    "executable_path": str(stock_executable),
                    "executable_sha256": digest(stock_executable),
                },
                "runtime": {"measurements": [{"checksum": "0123456789abcdef"}]},
            },
            "first": {
                "trace": trace,
                "sites": [
                    {
                        "site_index": 0,
                        "trace_index": 1,
                        "state": states[1],
                        "provider_packet": {
                            "actions": [
                                {"id": "2", "description": "candidate 2"},
                                {"id": "4", "description": "candidate 4"},
                            ]
                        },
                    }
                ],
            },
        },
    }

    def run_interactive(
        command: list[str],
        *,
        policy: Any,
        stdout_path: Path,
        stderr_path: Path,
        run_identity: dict[str, Any],
        **_kwargs: Any,
    ):
        current = [dict(item) for item in observations]
        if prefix_drift:
            current[0]["features_sha256"] = "changed-prefix"
        decisions = []
        communication_error = None
        for observation in current:
            observation["run_identity"] = run_identity
            try:
                action = policy(observation)
            except advisor.ReplayMismatch as error:
                communication_error = str(error)
                break
            state = counterfactual._state(observation)
            decisions.append(
                {
                    **state,
                    "eligible_actions": state["eligible_actions"],
                    "action_index": action,
                }
            )
        object_path = Path(command[command.index("-o") + 1])
        if communication_error is None:
            object_path.write_bytes(b"counterfactual-object")
        stdout_path.write_bytes(b"")
        stderr_path.write_bytes(b"")
        return {
            "compiler_command": command,
            "return_code": 1 if communication_error else 0,
            "timed_out": False,
            "communication_error": communication_error,
            "duration_ms": 8,
            "decision_count": len(decisions),
            "decisions": decisions,
            "stdout_path": str(stdout_path),
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_path": str(stderr_path),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }

    monkeypatch.setattr(counterfactual.advisor, "run_interactive", run_interactive)
    native_calls = []

    def native_result(
        _name: str,
        _object_path: Path,
        executable_path: Path,
        **kwargs: Any,
    ):
        native_calls.append(kwargs["expected_checksum"])
        executable_path.write_bytes(b"counterfactual-executable")
        return {
            "link": {"status": "passed", "executable_path": str(executable_path)},
            "runtime": {"status": "passed"},
        }

    monkeypatch.setattr(counterfactual, "_native_result", native_result)

    result = counterfactual.compile_choice(case, 0, 4, tmp_path / "choice")

    if prefix_drift:
        assert result["status"] == "failed"
        assert result["compile"]["prefix_matches_baseline"] is False
        assert result["link"]["status"] == "blocked"
        assert native_calls == []
    else:
        assert result["status"] == "passed"
        assert result["compile"]["prefix_matches_baseline"] is True
        assert result["compile"]["target_reached_once_at_expected_index"] is True
        assert result["compile"]["object_sha256"] == digest(
            Path(result["paths"]["object"])
        )
        assert native_calls == ["0123456789abcdef"]
