from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

ADVISOR_PATH = Path(__file__).resolve().parents[1] / "scripts/advisor.py"
ADVISOR_SPEC = importlib.util.spec_from_file_location("advisor", ADVISOR_PATH)
assert ADVISOR_SPEC is not None and ADVISOR_SPEC.loader is not None
advisor = importlib.util.module_from_spec(ADVISOR_SPEC)
sys.modules[ADVISOR_SPEC.name] = advisor
ADVISOR_SPEC.loader.exec_module(advisor)

VALIDATE_PATH = Path(__file__).resolve().parents[1] / "scripts/validate.py"
VALIDATE_SPEC = importlib.util.spec_from_file_location("p4ai_validate", VALIDATE_PATH)
assert VALIDATE_SPEC is not None and VALIDATE_SPEC.loader is not None
validate = importlib.util.module_from_spec(VALIDATE_SPEC)
sys.modules[VALIDATE_SPEC.name] = validate
VALIDATE_SPEC.loader.exec_module(validate)


def _record(
    action: int, *, observation: int = 2, actions: list[int] | None = None
) -> dict[str, object]:
    return {
        "context": "eviction",
        "observation": observation,
        "features_sha256": "state-digest",
        "eligible_actions": actions if actions is not None else [0, 3],
        "action_index": action,
    }


def test_divergence_requires_same_telemetry_state_and_distinct_eligible_actions() -> (
    None
):
    first = [_record(0), _record(0, observation=3)]
    last = [_record(3), _record(3, observation=4)]

    assert validate.find_divergent_decision(first, last) == {
        "context": "eviction",
        "observation": 2,
        "features_sha256": "state-digest",
        "eligible_actions": [0, 3],
        "first_action": 0,
        "last_action": 3,
    }
    assert (
        validate.find_divergent_decision(
            [_record(0, actions=[0])], [_record(0, actions=[0])]
        )
        is None
    )


def test_driver_output_parser_requires_correct_result_and_expected_iteration_count() -> (
    None
):
    valid = json.dumps(
        {
            "correct": True,
            "iterations": 3,
            "checksum": "3701e6c1cf48799b",
            "elapsed_ns": 17,
        }
    ).encode()

    assert validate._read_driver_result(valid, 3) == json.loads(valid)
    assert validate._read_driver_result(valid, 4) is None
    assert validate._read_driver_result(b"not json\n", 3) is None
    incorrect = json.dumps({**json.loads(valid), "correct": False}).encode()
    assert validate._read_driver_result(incorrect, 3) is None
    boolean_elapsed = json.dumps({**json.loads(valid), "elapsed_ns": True}).encode()
    assert validate._read_driver_result(boolean_elapsed, 3) is None
    assert validate._read_driver_result(b"[]\n", 3) is None


def test_cli_positive_integer_parser_rejects_zero() -> None:
    assert validate.positive_int("12") == 12
    try:
        validate.positive_int("0")
    except argparse.ArgumentTypeError as error:
        assert "positive integer" in str(error)
    else:
        raise AssertionError("zero should be rejected")


def test_guard_diagnostic_patterns_match_live_candidate_details() -> None:
    examples = {
        "negative": "Regalloc: ML eviction advisor returned negative candidate position -1",
        "out_of_range": "Regalloc: ML eviction advisor returned candidate position 1000000 outside the candidate columns [0, 34)",
        "masked": "Regalloc: ML eviction advisor selected masked candidate position 2",
    }
    for case, diagnostic in examples.items():
        assert re.search(validate.EXPECTED_GUARDS[case], diagnostic)


def test_replay_identity_normalizes_only_the_object_output_path() -> None:
    command = ["/build/bin/llc", "-O=2", "-filetype=obj", "input.ll", "-o", "first.o"]
    identity = validate._replay_identity(
        command,
        ir_sha256="ir",
        llc_sha256="llc",
        llc_version_sha256="version",
        source_revision="revision",
        guard_source_sha256="guard",
        patch_sha256="patch",
        build_manifest_sha256="manifest",
    )

    assert identity["compiler_args"] == [
        "/build/bin/llc",
        "-O=2",
        "-filetype=obj",
        "input.ll",
        "-o",
        "<object-output>",
    ]
    changed = validate._replay_identity(
        ["/build/bin/llc", "-O=3", "-filetype=obj", "input.ll", "-o", "second.o"],
        ir_sha256="ir",
        llc_sha256="llc",
        llc_version_sha256="version",
        source_revision="revision",
        guard_source_sha256="guard",
        patch_sha256="patch",
        build_manifest_sha256="manifest",
    )
    assert changed["compiler_args"] != identity["compiler_args"]


def test_version_revision_check_rejects_an_unpinned_compiler() -> None:
    expected = validate.PINNED_LLVM_REVISION
    assert (
        validate.check_reported_revision(f"clang version git ({expected})", expected)[
            "status"
        ]
        == "passed"
    )
    assert (
        validate.check_reported_revision("LLVM version 24.0.0git", expected)["status"]
        == "unreported"
    )
    assert (
        validate.check_reported_revision(f"clang version git ({'0' * 40})", expected)[
            "status"
        ]
        == "failed"
    )


def test_build_manifest_binds_source_patch_and_selected_binaries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "llvm"
    guard = source / "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp"
    patch = tmp_path / "guard.patch"
    llc = tmp_path / "build/bin/llc"
    clang = tmp_path / "build/bin/clang"
    filecheck = tmp_path / "build/bin/FileCheck"
    for path, body in (
        (guard, b"guarded source"),
        (patch, b"guard patch"),
        (llc, b"llc binary"),
        (clang, b"clang binary"),
        (filecheck, b"FileCheck binary"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest_path = tmp_path / "build/p4ai-build.json"
    manifest = {
        "schema_version": 1,
        "source_revision": validate.PINNED_LLVM_REVISION,
        "source_path": str(source.resolve()),
        "guard_source_path": "llvm/lib/CodeGen/MLRegAllocEvictAdvisor.cpp",
        "guard_source_sha256": digest(guard),
        "patch_sha256": digest(patch),
        "binaries": {
            name: {"path": str(path.resolve()), "sha256": digest(path)}
            for name, path in (("llc", llc), ("clang", clang), ("FileCheck", filecheck))
        },
        "build_config": {
            "generator": "Ninja",
            "build_type": "Release",
            "llvm_projects": ["clang"],
            "llvm_targets": ["AArch64", "BPF"],
            "assertions": True,
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    checked = validate.verify_build_manifest(
        manifest_path,
        source=source,
        source_revision=validate.PINNED_LLVM_REVISION,
        guard_source=guard,
        patch_file=patch,
        llc=llc,
        clang=clang,
        filecheck=filecheck,
    )
    assert checked["status"] == "passed"

    clang.write_bytes(b"different compiler binary")
    changed = validate.verify_build_manifest(
        manifest_path,
        source=source,
        source_revision=validate.PINNED_LLVM_REVISION,
        guard_source=guard,
        patch_file=patch,
        llc=llc,
        clang=clang,
        filecheck=filecheck,
    )
    assert changed["status"] == "failed"
    assert (
        "manifest clang binary hash does not match the selected binary"
        in changed["errors"]
    )
