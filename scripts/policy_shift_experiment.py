"""Audit a frozen harness policy across an LLVM-to-Apple-Clang shift.

The audit compiles and correctness-checks each legal loop order with both
compilers, freezes source-forest choices, then measures two independent
interleaved timing blocks. It deliberately stops before adaptation or test
measurement; those phases are authorized only after the frozen audit gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from . import (
        benchmark,
        compiler_evidence,
        contraction_corpus,
        experiment,
        harness_policy,
        loop_build,
        quality_experiment,
    )
    from . import (
        compiler_feedback_experiment as feedback,
    )
    from . import (
        contraction_experiment as contraction,
    )
except ImportError:  # Running as ``python scripts/policy_shift_experiment.py``.
    import benchmark
    import compiler_evidence
    import compiler_feedback_experiment as feedback
    import contraction_corpus
    import contraction_experiment as contraction
    import experiment
    import harness_policy
    import loop_build
    import quality_experiment


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESIGN = ROOT / "experiments" / "policy-shift-v1.json"
SOURCE_DESIGN = ROOT / "experiments" / "compiler-feedback-v1.json"
SOURCE_POLICY_REPORT = ROOT / "docs" / "results" / "feedback-replication-v1.json"
SCHEMA_VERSION = 1
ARMS = ("source", "target")
ORDERS = tuple(contraction_corpus.ORDERS)
ACTION_NAMES = ("stock", *ORDERS)


class PolicyShiftError(RuntimeError):
    """The compiler-shift audit cannot safely proceed or resume."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return _sha256_bytes(payload.encode("utf-8"))


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PolicyShiftError(f"cannot read {path}: {error}") from error


def _design(path: Path) -> dict[str, Any]:
    try:
        design = _load(path)
    except PolicyShiftError:
        raise
    if not isinstance(design, dict) or design.get("schema_version") != SCHEMA_VERSION:
        raise PolicyShiftError("unsupported policy-shift design")
    if design.get("name") != "policy-shift-v1":
        raise PolicyShiftError("unexpected policy-shift design name")
    if design.get("actions") != list(ACTION_NAMES):
        raise PolicyShiftError("action menu differs from the frozen contraction corpus")
    if design.get("target") != "arm64-apple-darwin" or design.get("target_cpu") != "apple-m3":
        raise PolicyShiftError("policy-shift target changed")
    if design.get("benchmark_rounds") != 11 or design.get("benchmark_blocks") != 2:
        raise PolicyShiftError("audit requires two independent blocks of eleven rounds")
    for key in ("seed", "target_sample_ms"):
        if type(design.get(key)) is not int or design[key] <= 0:
            raise PolicyShiftError(f"design {key} must be a positive integer")
    for key in ("noise_floor_fraction", "minimum_target_regret", "minimum_regret_increase"):
        value = design.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise PolicyShiftError(f"design {key} must be finite and non-negative")
    if design["minimum_target_regret"] > 1:
        raise PolicyShiftError("minimum_target_regret must be a fractional excess, at most one")
    if (
        design.get("source_bundle") != "docs/results/compiler-feedback-v1.json"
        or not isinstance(design.get("source_bundle_sha256"), str)
        or len(design["source_bundle_sha256"]) != 64
        or not isinstance(design.get("sklearn_version"), str)
        or not design["sklearn_version"]
    ):
        raise PolicyShiftError("source training evidence or sklearn version is not pinned")

    cases = design.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        raise PolicyShiftError("frozen design must contain 24 partitioned cases")
    seen_ids: set[str] = set()
    audit_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cases:
        if not isinstance(row, dict) or row.get("partition") not in {"audit", "adaptation", "test"}:
            raise PolicyShiftError("design case has an invalid partition")
        case_id, group, spec = row.get("case_id"), row.get("group"), row.get("spec")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise PolicyShiftError("design case IDs must be unique non-empty strings")
        if not isinstance(group, str) or not group or not isinstance(spec, dict):
            raise PolicyShiftError(f"case {case_id} has incomplete identity data")
        try:
            contraction_corpus._validate(spec, "ijk")
        except (TypeError, ValueError) as error:
            raise PolicyShiftError(f"case {case_id} has an invalid specification: {error}") from error
        seen_ids.add(case_id)
        if row["partition"] == "audit":
            audit_groups[group].append(row)
    if len(audit_groups) != 3 or any(len(rows) != 2 for rows in audit_groups.values()):
        raise PolicyShiftError("audit must contain three structural groups with two cases each")
    for group, rows in audit_groups.items():
        specs = [row["spec"] for row in rows]
        shared = ("m", "n", "k", *contraction_corpus.PADDING_KEYS)
        if any(any(spec.get(key, 0) != specs[0].get(key, 0) for key in shared) for spec in specs):
            raise PolicyShiftError(f"audit group {group} mixes shapes or padding")
        layouts = {
            tuple(spec[name] for name in ("a_column_major", "b_column_major", "c_column_major"))
            for spec in specs
        }
        if len(layouts) != 2:
            raise PolicyShiftError(f"audit group {group} repeats a layout")
    for key in ("source_clang", "target_clang", "linker", "sdk"):
        if not isinstance(design.get(key), str) or not design[key]:
            raise PolicyShiftError(f"design {key} path is missing")
    for path_key, hash_key in (
        ("source_clang", "source_clang_sha256"),
        ("target_clang", "target_clang_sha256"),
        ("linker", "linker_sha256"),
    ):
        digest = design.get(hash_key)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise PolicyShiftError(f"design {hash_key} is invalid")
    for key in ("kernel_flags", "driver_flags"):
        flags = design.get(key)
        if not isinstance(flags, list) or not flags or any(not isinstance(flag, str) for flag in flags):
            raise PolicyShiftError(f"design {key} must be a non-empty string list")
    forest = design.get("forest")
    if not isinstance(forest, dict) or forest != {
        "n_estimators": 64,
        "max_depth": 6,
        "min_samples_leaf": 2,
        "random_state": 20261020,
        "n_jobs": 1,
    }:
        raise PolicyShiftError("frozen source forest parameters changed")
    return design


def _compiler_path(design: dict[str, Any], arm: str) -> Path:
    key = "source_clang" if arm == "source" else "target_clang"
    value = Path(design[key]).expanduser()
    return (ROOT / value).resolve() if not value.is_absolute() else value.resolve()


def _run_identity_command(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PolicyShiftError(f"toolchain identity command failed: {command[0]}") from error
    if result.returncode != 0:
        raise PolicyShiftError(f"toolchain identity command exited {result.returncode}: {command[0]}")
    return {
        "return_code": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace").strip(),
        "stderr": result.stderr.decode("utf-8", errors="replace").strip(),
    }


def _toolchains(design: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected_keys = {"source": "source_clang_sha256", "target": "target_clang_sha256"}
    toolchains = {}
    for arm in ARMS:
        compiler = _compiler_path(design, arm)
        if not compiler.is_file():
            raise PolicyShiftError(f"{arm} compiler is missing: {compiler}")
        actual_hash = _sha256(compiler)
        if actual_hash != design[expected_keys[arm]]:
            raise PolicyShiftError(f"{arm} compiler hash differs from the frozen design")
        toolchains[arm] = {
            "path": str(compiler),
            "sha256": actual_hash,
            "version": _run_identity_command([str(compiler), "--version"]),
            "default_target": _run_identity_command([str(compiler), "-dumpmachine"]),
            "compiler_identity": {"sha256": actual_hash, "target": design["target"]},
        }

    linker = Path(design["linker"]).expanduser().resolve()
    if not linker.is_file() or _sha256(linker) != design["linker_sha256"]:
        raise PolicyShiftError("shared driver/linker path or hash differs from the design")
    sdk = Path(design["sdk"]).expanduser().resolve()
    if not sdk.is_dir():
        raise PolicyShiftError(f"pinned SDK is missing: {sdk}")
    xcrun = Path("/usr/bin/xcrun")
    if xcrun.is_file():
        sdk_path = _run_identity_command([str(xcrun), "--sdk", "macosx", "--show-sdk-path"])["stdout"]
        if Path(sdk_path).resolve() != sdk:
            raise PolicyShiftError("active macOS SDK path differs from the pinned SDK")
        sdk_version = _run_identity_command([str(xcrun), "--sdk", "macosx", "--show-sdk-version"])["stdout"]
    else:
        raise PolicyShiftError("xcrun is required to record the active macOS SDK")
    for toolchain in toolchains.values():
        toolchain["sdk"] = {"path": str(sdk), "version": sdk_version}
        toolchain["linker"] = {"path": str(linker), "sha256": design["linker_sha256"]}
        toolchain["target"] = design["target"]
        toolchain["target_cpu"] = design["target_cpu"]
    return toolchains


def _source_policy(design: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the published source-trained RF and verify its model digest."""
    report = _load(SOURCE_POLICY_REPORT)
    try:
        model = report["fitted_controls"]["random_forest"]
        expected_hash = report["provenance"]["forest_json_sha256"]
        source_bundle_ref = report["provenance"]["source_training_public_bundle"]
        training = {
            "source_report": str(SOURCE_POLICY_REPORT.relative_to(ROOT)),
            "source_report_sha256": _sha256(SOURCE_POLICY_REPORT),
            "source_model_sha256": expected_hash,
            "training_rows_sha256": report["provenance"]["training_rows_sha256"],
            "training_model_file_sha256": report["fitted_controls"]["training_model_file_sha256"],
            "source_design_reference": report["provenance"]["design"],
            "source_bundle_reference": source_bundle_ref,
            "source_training_evidence_sha256": report["provenance"]["training_source_evidence"].get("raw_file_sha256"),
            "labeled_records_sha256": report["source_training"]["labeled_records_sha256"],
        }
    except (KeyError, TypeError) as error:
        raise PolicyShiftError("published frozen source policy is incomplete") from error
    actual_hash = _fingerprint(model)
    if not isinstance(expected_hash, str) or actual_hash != expected_hash:
        raise PolicyShiftError("frozen source RF export hash differs from its published provenance")
    source_design = feedback._design(SOURCE_DESIGN)
    expected_features = sorted(source_design["source_features"] + list(source_design["compiler_features"]))
    if model.get("features") != expected_features:
        raise PolicyShiftError("source RF feature schema differs from the frozen 42-feature design")
    if model.get("parameters") != {
        "n_estimators": 64,
        "max_depth": 6,
        "min_samples_leaf": 2,
        "random_state": 20261020,
        "n_jobs": 1,
    }:
        raise PolicyShiftError("source RF parameters differ from the frozen configuration")
    design = design or _design(DEFAULT_DESIGN)
    source_bundle = ROOT / design["source_bundle"]
    if not source_bundle.is_file() or _sha256(source_bundle) != design["source_bundle_sha256"]:
        raise PolicyShiftError("pinned source compiler-feedback bundle hash changed")
    if model.get("library_version") != design["sklearn_version"]:
        raise PolicyShiftError("source RF scikit-learn version differs from the frozen model")
    try:
        harness_policy._validate_model(model)
    except (KeyError, TypeError, ValueError) as error:
        raise PolicyShiftError(f"source RF cannot be replayed by the offline evaluator: {error}") from error
    training["source_bundle_sha256"] = design["source_bundle_sha256"]
    training["model_digest_verified"] = True
    return model, training


def _case_rows(design: dict[str, Any], partition: str) -> list[dict[str, Any]]:
    return [
        {"partition": row["partition"], "group": row["group"], "family": row["group"],
         "case_id": row["case_id"], "spec": dict(row["spec"])}
        for row in design["cases"]
        if row["partition"] == partition
    ]


def _generate_sources(case: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    source_dir = case_dir / "sources"
    manifest_path = source_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = _load(manifest_path)
        if manifest.get("case_id") != case["case_id"] or manifest.get("spec") != case["spec"]:
            raise PolicyShiftError(f"generated source identity changed for {case['case_id']}")
        for action, reference in manifest.get("actions", {}).items():
            for kind in ("kernel", "driver"):
                path = Path(reference[kind]["path"])
                if not path.is_file() or _sha256(path) != reference[kind]["sha256"]:
                    raise PolicyShiftError(f"generated {kind} changed for {case['case_id']}/{action}")
        if set(manifest.get("actions", {})) != set(ACTION_NAMES):
            raise PolicyShiftError(f"generated action source set changed for {case['case_id']}")
        return manifest
    if source_dir.exists() and any(source_dir.iterdir()):
        raise PolicyShiftError(
            f"partial generated sources already exist for {case['case_id']}; preserve them and use a fresh output"
        )
    generated: dict[str, dict[str, Any]] = {}
    for action in ACTION_NAMES:
        order = "ijk" if action == "stock" else action
        action_dir = source_dir / action
        action_dir.mkdir(parents=True, exist_ok=True)
        result = contraction_corpus.generate_case(action_dir, case["spec"], order=order)
        paths: dict[str, Path] = {}
        for kind in ("kernel", "driver"):
            path = Path(result[kind]).resolve()
            if not path.is_file() or not path.is_relative_to(source_dir.resolve()):
                raise PolicyShiftError(f"corpus generator returned an unsafe {kind} path")
            paths[kind] = path
        features = result.get("features")
        if not isinstance(features, dict) or any(type(value) is not int for value in features.values()):
            raise PolicyShiftError(f"corpus generator returned invalid source features for {case['case_id']}")
        generated[action] = {
            "paths": paths,
            "features": features,
        }
    driver_hashes = {_sha256(item["paths"]["driver"]) for item in generated.values()}
    if len(driver_hashes) != 1:
        raise PolicyShiftError(f"driver source differs across actions for {case['case_id']}")
    features = [item["features"] for item in generated.values()]
    if any(row != features[0] for row in features[1:]):
        raise PolicyShiftError(f"source features vary by action for {case['case_id']}")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["case_id"],
        "spec": case["spec"],
        "source_features": features[0],
        "actions": {
            action: {
                kind: {"path": str(path), "sha256": _sha256(path)}
                for kind, path in value["paths"].items()
            }
            for action, value in generated.items()
        },
    }
    _save(manifest_path, manifest)
    return manifest


def _public_artifact(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise PolicyShiftError(f"expected build artifact is missing: {path}")
    return {"path": str(path), "sha256": _sha256(path)}


def _validate_artifacts(report: dict[str, Any]) -> None:
    refs = report.get("artifacts")
    if not isinstance(refs, dict) or not refs:
        raise PolicyShiftError("build report has no artifact hashes")
    for name, ref in refs.items():
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise PolicyShiftError(f"invalid artifact reference: {name}")
        path = Path(ref["path"])
        if not path.is_file() or _sha256(path) != ref.get("sha256"):
            raise PolicyShiftError(f"build artifact changed: {name}")
    validation = report.get("validation_report")
    if (
        not isinstance(validation, dict)
        or not isinstance(validation.get("path"), str)
        or not Path(validation["path"]).is_file()
        or _sha256(Path(validation["path"])) != validation.get("sha256")
    ):
        raise PolicyShiftError("toolchain validation report is missing or changed")


def build_case(
    case: dict[str, Any], arm: str, output: Path, design: dict[str, Any] | None = None,
    toolchain: dict[str, Any] | None = None, design_path: Path | None = None,
) -> dict[str, Any]:
    """Compile/correctness-check one frozen case with one compiler arm.

    This public helper also supports the separate frozen-policy execution demo.
    It never mutates compiler selection or accepts caller-supplied flags.
    """
    if arm not in ARMS:
        raise ValueError("arm must be 'source' or 'target'")
    design = design or _design(DEFAULT_DESIGN)
    design_path = (design_path or DEFAULT_DESIGN).expanduser().resolve()
    toolchain = toolchain or _toolchains(design)[arm]
    compiler = Path(toolchain["path"])
    linker = Path(design["linker"]).resolve()
    sdk = Path(design["sdk"]).resolve()
    case_dir = output.expanduser().resolve()
    case_dir.mkdir(parents=True, exist_ok=True)
    source_manifest = _generate_sources(case, case_dir)
    compiler_arm_dir = case_dir / "compilers" / arm
    report_path = compiler_arm_dir / "build-report.json"
    flags = [*design["kernel_flags"], "-isysroot", str(sdk)]
    driver_flags = [*design["driver_flags"], "-isysroot", str(sdk)]
    validation_path = case_dir / f"toolchain-validation-{arm}.json"
    validation_value = {
        "schema_version": SCHEMA_VERSION,
        "compiler_arm": arm,
        "toolchain": toolchain,
        "kernel_flags": flags,
        "driver_flags": driver_flags,
    }
    if validation_path.is_file() and _load(validation_path) != validation_value:
        raise PolicyShiftError(f"toolchain validation changed for {case['case_id']}/{arm}")
    if not validation_path.is_file():
        _save(validation_path, validation_value)
    validation_ref = {"path": str(validation_path), "sha256": _sha256(validation_path)}
    identity_payload = {
        "schema_version": SCHEMA_VERSION,
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "compiler_evidence_sha256": _sha256(Path(compiler_evidence.__file__).resolve()),
        "corpus_sha256": _sha256(Path(contraction_corpus.__file__).resolve()),
        "loop_build_sha256": _sha256(Path(loop_build.__file__).resolve()),
        "quality_experiment_sha256": _sha256(Path(quality_experiment.__file__).resolve()),
        "experiment_sha256": _sha256(Path(experiment.__file__).resolve()),
        "design_sha256": _sha256(design_path),
        "case_id": case["case_id"],
        "spec": case["spec"],
        "compiler": toolchain,
        "linker_sha256": _sha256(linker),
        "sdk": toolchain["sdk"],
        "validation_report": validation_ref,
        "kernel_flags": flags,
        "driver_flags": driver_flags,
        "actions": list(ACTION_NAMES),
        "correctness_iterations": 3,
        "source_manifest_sha256": _fingerprint(source_manifest),
    }
    fingerprint = _fingerprint(identity_payload)
    if report_path.exists():
        report = _load(report_path)
        if report.get("case_fingerprint") != fingerprint or report.get("identity") != identity_payload:
            raise PolicyShiftError(f"prepared build identity changed for {case['case_id']}/{arm}")
        if report.get("status") != "passed":
            raise PolicyShiftError(f"cached build did not pass for {case['case_id']}/{arm}")
        _validate_artifacts(report)
        return report
    if compiler_arm_dir.exists() and any(compiler_arm_dir.iterdir()):
        raise PolicyShiftError(
            f"partial build artifacts already exist for {case['case_id']}/{arm}; preserve them and use a fresh output"
        )
    (compiler_arm_dir / "actions").mkdir(parents=True, exist_ok=True)
    driver_source = Path(source_manifest["actions"]["stock"]["driver"]["path"])
    driver_object = compiler_arm_dir / "driver.o"
    driver_result = experiment._run_command(
        "driver_object",
        [str(linker), *driver_flags, "-c", str(driver_source), "-o", str(driver_object)],
        compiler_arm_dir,
        300,
    )
    actions: dict[str, Any] = {}
    source_refs = source_manifest["actions"]
    artifacts: dict[str, dict[str, str]] = {
        "driver_source": source_refs["stock"]["driver"],
        "driver_object": _public_artifact(driver_object) if driver_object.is_file() else {},
    }
    errors: list[str] = []
    if not driver_result["passed"] or not driver_object.is_file():
        errors.append("shared Apple-Clang driver compilation failed")
    stock_checksum: str | None = None
    for action in ACTION_NAMES:
        action_dir = compiler_arm_dir / "actions" / action
        action_dir.mkdir(parents=True, exist_ok=True)
        source = Path(source_manifest["actions"][action]["kernel"]["path"])
        ir_path = action_dir / "optimized.ll"
        object_path = action_dir / "kernel.o"
        executable_path = action_dir / "kernel"
        ir_result = experiment._run_command(
            f"{action}_optimized_ir",
            [str(compiler), *flags, "-S", "-emit-llvm", str(source), "-o", str(ir_path)],
            compiler_arm_dir,
            300,
        )
        object_result = experiment._run_command(
            f"{action}_object",
            [str(compiler), *flags, "-c", str(source), "-o", str(object_path)],
            compiler_arm_dir,
            300,
        )
        evidence = compiler_evidence.extract_compiler_evidence(
            ir_path if ir_result["passed"] and ir_path.is_file() else None,
            object_result["_stderr"],
            object_path,
        )
        row_errors: list[str] = []
        if not ir_result["passed"] or not ir_path.is_file():
            row_errors.append("optimized IR compilation failed")
        if not object_result["passed"] or not object_path.is_file():
            row_errors.append("kernel object compilation failed")
        if evidence.get("status") != "complete":
            row_errors.append(f"compiler evidence is unavailable or ambiguous: {evidence.get('reason')}")
        code_identity = None
        if object_path.is_file():
            try:
                code_identity = quality_experiment.code_identity(object_path)
            except (OSError, ValueError) as error:
                row_errors.append(f"cannot identify kernel code: {error}")
        correctness = None
        if not row_errors and driver_result["passed"] and driver_object.is_file():
            correctness = loop_build._native_result(
                action,
                object_path,
                executable_path,
                driver_object=driver_object,
                linker=linker,
                iterations=3,
                artifacts_dir=compiler_arm_dir,
                timeout_seconds=300,
                expected_checksum=None if action == "stock" else stock_checksum,
            )
        elif correctness is None:
            correctness = {
                "link": {"status": "blocked", "reason": "compile or evidence failed"},
                "runtime": {"status": "blocked", "reason": "compile or evidence failed"},
            }
        if correctness["link"].get("status") != "passed":
            row_errors.append("native linking failed")
        if correctness["runtime"].get("status") != "passed":
            row_errors.append("kernel correctness check failed")
        if action == "stock" and correctness["runtime"].get("status") == "passed":
            measurements = correctness["runtime"].get("measurements", [])
            if measurements:
                stock_checksum = measurements[0].get("checksum")
        artifacts[f"{action}_source"] = source_manifest["actions"][action]["kernel"]
        for name, path in (("ir", ir_path), ("object", object_path), ("executable", executable_path)):
            if path.is_file():
                artifacts[f"{action}_{name}"] = _public_artifact(path)
        actions[action] = {
            "status": "passed" if not row_errors else "failed",
            "code_identity": code_identity,
            "compiler_evidence": evidence.get("features"),
            "compiler_evidence_status": {
                "status": evidence.get("status"),
                "ir_status": evidence.get("ir_status"),
                "remark_status": evidence.get("remark_status"),
                "reason": evidence.get("reason"),
                "extraction_elapsed_ms": evidence.get("extraction_elapsed_ms"),
            },
            "commands": {
                "optimized_ir": experiment._public_command_result(ir_result),
                "object": experiment._public_command_result(object_result),
            },
            "correctness": correctness,
            "errors": row_errors,
        }
        if row_errors:
            errors.extend(f"{action}: {item}" for item in row_errors)
        for label, result in ((f"{action}_optimized_ir", ir_result), (f"{action}_object", object_result)):
            for output_key in ("stdout_path", "stderr_path"):
                output_path = Path(result[output_key])
                if output_path.is_file():
                    artifacts[f"command_{label}_{output_key}"] = _public_artifact(output_path)
    for label, output_key in (("driver_object", "stdout_path"), ("driver_object", "stderr_path")):
        output_path = Path(driver_result[output_key])
        if output_path.is_file():
            artifacts[f"command_{label}_{output_key}"] = _public_artifact(output_path)
    commands_path = compiler_arm_dir / "commands"
    if commands_path.is_dir():
        for command_path in sorted(commands_path.iterdir()):
            if command_path.is_file():
                artifacts[f"command_log_{command_path.name}"] = _public_artifact(command_path)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if not errors else "failed",
        "case_id": case["case_id"],
        "partition": case["partition"],
        "group": case["group"],
        "spec": case["spec"],
        "compiler_arm": arm,
        "case_fingerprint": fingerprint,
        "identity": identity_payload,
        "action_order": list(ACTION_NAMES),
        "stock_checksum": stock_checksum,
        "source_features": source_manifest["source_features"],
        "distinct_code_identities": len({actions[name]["code_identity"] for name in ORDERS}),
        "toolchain": toolchain,
        "validation_report": validation_ref,
        "kernel_flags": flags,
        "driver_flags": driver_flags,
        "artifacts": artifacts,
        "build_artifacts": {
            "kernel_sources": {name: source_refs[name]["kernel"] for name in ACTION_NAMES},
            "driver_sources": {name: source_refs[name]["driver"] for name in ACTION_NAMES},
            "driver_object": artifacts.get("driver_object"),
            "kernel_objects": {
                name: artifacts.get(f"{name}_object") for name in ACTION_NAMES
            },
            "optimized_ir": {name: artifacts.get(f"{name}_ir") for name in ACTION_NAMES},
            "executables": {
                name: artifacts.get(f"{name}_executable") for name in ACTION_NAMES
            },
        },
        "driver_compile": experiment._public_command_result(driver_result),
        "actions": actions,
        "errors": errors,
    }
    _save(report_path, report)
    if errors:
        raise PolicyShiftError(f"build/correctness failed for {case['case_id']}/{arm}: {'; '.join(errors)}")
    _validate_artifacts(report)
    return report


def policy_packet(case: dict[str, Any], feedback_design: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the same 42-feature source-RF packet used by the original control."""
    design = feedback_design or feedback._design(SOURCE_DESIGN)
    return feedback._provider_packet(case, design, informed=True)


def _prediction(case: dict[str, Any], build: dict[str, Any], artifact: dict[str, Any], identity: dict[str, str]) -> dict[str, Any]:
    compiled_case = {
        **case,
        "source_features": build["source_features"],
        "build": build,
    }
    packet = policy_packet(compiled_case)
    choice = harness_policy.choose(artifact, packet, identity)
    return {
        "case_id": case["case_id"],
        "group": case["group"],
        "action": choice["action"],
        "action_id": choice["action_id"],
        "scores": choice["scores"],
        "packet_sha256": _fingerprint(packet),
        "packet": packet,
    }


def _prepare_provenance(design_path: Path, design: dict[str, Any], toolchains: dict[str, Any]) -> dict[str, Any]:
    return {
        "design_sha256": _sha256(design_path),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "implementation_sha256": {
            str(Path(module.__file__).resolve().relative_to(ROOT)): _sha256(Path(module.__file__).resolve())
            for module in (benchmark, compiler_evidence, contraction, contraction_corpus, experiment, loop_build, quality_experiment, harness_policy, feedback)
        },
        "source_design_sha256": _sha256(SOURCE_DESIGN),
        "source_policy_report_sha256": _sha256(SOURCE_POLICY_REPORT),
        "toolchains": toolchains,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
    }


def prepare_audit(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    design = _design(design_path)
    toolchains = _toolchains(design)
    provenance = _prepare_provenance(design_path, design, toolchains)
    model, training = _source_policy(design)
    audit_cases = _case_rows(design, "audit")
    built: dict[str, dict[str, Any]] = {arm: {} for arm in ARMS}
    artifacts: dict[str, dict[str, Any]] = {}
    choices: dict[str, dict[str, Any]] = {arm: {} for arm in ARMS}
    packets: dict[str, dict[str, Any]] = {arm: {} for arm in ARMS}
    for arm in ARMS:
        identity = toolchains[arm]["compiler_identity"]
        artifact = harness_policy.make_artifact(model, identity, training)
        policy_path = output / "policies" / f"source-rf-{arm}.json"
        policy_value = {"schema_version": SCHEMA_VERSION, "artifact": artifact}
        if policy_path.is_file() and _load(policy_path) != policy_value:
            raise PolicyShiftError(f"frozen {arm} policy artifact changed")
        if not policy_path.is_file():
            _save(policy_path, policy_value)
        artifacts[arm] = {"path": str(policy_path), "sha256": _sha256(policy_path)}
        for case in audit_cases:
            build = build_case(
                case, arm, output / "cases" / case["case_id"], design,
                toolchains[arm], design_path,
            )
            built[arm][case["case_id"]] = {
                "build_report_path": str(output / "cases" / case["case_id"] / "compilers" / arm / "build-report.json"),
                "build_report_sha256": _sha256(
                    output / "cases" / case["case_id"] / "compilers" / arm / "build-report.json"
                ),
                "case_fingerprint": build["case_fingerprint"],
            }
            prediction = _prediction(case, build, artifact, identity)
            packets[arm][case["case_id"]] = prediction.pop("packet")
            choices[arm][case["case_id"]] = prediction
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared",
        "provenance": provenance,
        "design": str(design_path),
        "output": str(output),
        "audit_case_ids": [case["case_id"] for case in audit_cases],
        "policy_artifacts": artifacts,
        "source_policy_training_provenance": training,
        "builds_by_compiler": built,
        "frozen_source_policy_choices": choices,
        "packets_by_compiler": packets,
        "native_measurements_started": False,
    }
    path = output / "prepared-audit.json"
    if path.is_file() and _load(path) != report:
        raise PolicyShiftError("prepared audit differs from existing frozen manifest")
    if not path.is_file():
        _save(path, report)
    return report


def _validate_prepared_audit(
    prepared_path: Path,
    prepared: dict[str, Any],
    design_path: Path,
    design: dict[str, Any],
    toolchains: dict[str, Any],
    provenance: dict[str, Any],
    output: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if prepared.get("status") != "prepared" or prepared.get("provenance") != provenance:
        raise PolicyShiftError("prepared audit does not match the current frozen inputs")
    if prepared.get("native_measurements_started") is not False:
        raise PolicyShiftError("prepared audit state is not a premeasurement checkpoint")
    cases = _case_rows(design, "audit")
    case_ids = [case["case_id"] for case in cases]
    if prepared.get("audit_case_ids") != case_ids:
        raise PolicyShiftError("prepared audit case order or membership changed")
    model, training = _source_policy(design)
    if prepared.get("source_policy_training_provenance") != training:
        raise PolicyShiftError("source policy training provenance changed")
    rebuilt_choices: dict[str, dict[str, Any]] = {arm: {} for arm in ARMS}
    expected_packets: dict[str, dict[str, Any]] = {arm: {} for arm in ARMS}
    builds_by_compiler = prepared.get("builds_by_compiler")
    policy_refs = prepared.get("policy_artifacts")
    for arm in ARMS:
        if not isinstance(builds_by_compiler, dict) or not isinstance(policy_refs, dict):
            raise PolicyShiftError("prepared audit policy/build indexes are malformed")
        identity = toolchains[arm]["compiler_identity"]
        expected_artifact = harness_policy.make_artifact(model, identity, training)
        policy_ref = policy_refs.get(arm, {})
        policy_path = Path(policy_ref.get("path", ""))
        if not policy_path.is_file() or _sha256(policy_path) != policy_ref.get("sha256"):
            raise PolicyShiftError(f"frozen source policy artifact changed for {arm}")
        if _load(policy_path) != {"schema_version": SCHEMA_VERSION, "artifact": expected_artifact}:
            raise PolicyShiftError(f"frozen source policy artifact content changed for {arm}")
        arm_builds = builds_by_compiler.get(arm)
        if not isinstance(arm_builds, dict) or set(arm_builds) != set(case_ids):
            raise PolicyShiftError(f"prepared build index is incomplete for {arm}")
        for case in cases:
            ref = arm_builds[case["case_id"]]
            path = Path(ref.get("build_report_path", ""))
            if not path.is_file() or _sha256(path) != ref.get("build_report_sha256"):
                raise PolicyShiftError(f"frozen build report changed for {case['case_id']}/{arm}")
            build = _load(path)
            if (
                build.get("status") != "passed"
                or build.get("case_id") != case["case_id"]
                or build.get("compiler_arm") != arm
                or build.get("case_fingerprint") != ref.get("case_fingerprint")
                or build.get("toolchain") != toolchains[arm]
                or build.get("action_order") != list(ACTION_NAMES)
            ):
                raise PolicyShiftError(f"frozen build report identity changed for {case['case_id']}/{arm}")
            _validate_artifacts(build)
            prediction = _prediction(case, build, expected_artifact, identity)
            packet = prediction.pop("packet")
            if prepared.get("frozen_source_policy_choices", {}).get(arm, {}).get(case["case_id"]) != prediction:
                raise PolicyShiftError(f"frozen source policy choice changed for {case['case_id']}/{arm}")
            expected_packets[arm][case["case_id"]] = packet
            rebuilt_choices[arm][case["case_id"]] = prediction
    if prepared.get("packets_by_compiler") != expected_packets:
        raise PolicyShiftError("frozen policy packets changed")
    return cases, rebuilt_choices


def _measurement_case(case: dict[str, Any], build: dict[str, Any]) -> dict[str, Any]:
    """Adapt this experiment's compiler-specific executables to the frozen timing helper."""
    return {
        **case,
        # The existing helper only permits two blocks for discovery. This label
        # is internal to its cache path; the public audit partition is retained.
        "partition": "discovery",
        "build": {
            "case_fingerprint": build["case_fingerprint"],
            "source_features": build["source_features"],
            "actions": {
                action: {
                    "code_identity": build["actions"][action]["code_identity"],
                    "artifacts": {
                        "executable": {
                            "path": build["actions"][action]["correctness"]["link"]["executable_path"]
                        }
                    },
                }
                for action in ACTION_NAMES
            },
        },
    }


def _mad(values: list[float]) -> float:
    center = statistics.median(values)
    return float(statistics.median(abs(value - center) for value in values))


def audit_gate(
    cases: list[dict[str, Any]], choices: dict[str, dict[str, Any]], design: dict[str, Any]
) -> dict[str, Any]:
    """Evaluate the preregistered compiler-induced regret gate from raw rounds."""
    expected = {case["case_id"] for case in cases}
    if len(cases) != 6 or set(choices) != set(ARMS) or any(set(choices[arm]) != expected for arm in ARMS):
        raise PolicyShiftError("audit gate inputs do not cover all six cases and both compiler arms")
    groups = sorted({case["group"] for case in cases})
    if len(groups) != 3 or any(sum(case["group"] == group for case in cases) != 2 for group in groups):
        raise PolicyShiftError("audit gate requires three balanced groups with two cases each")
    reports = {}
    for block in (1, 2):
        round_logs: dict[str, dict[int, dict[str, float]]] = {
            arm: {round_number: {} for round_number in range(1, design["benchmark_rounds"] + 1)}
            for arm in ARMS
        }
        shift_group_logs: dict[int, dict[str, list[float]]] = {
            round_number: {group: [] for group in groups}
            for round_number in range(1, design["benchmark_rounds"] + 1)
        }
        for case in cases:
            case_id, group = case["case_id"], case["group"]
            for arm in ARMS:
                timing = case.get("measurements", {}).get(arm, {}).get("blocks", {}).get(str(block), {})
                rounds = timing.get("rounds")
                if not isinstance(rounds, list) or len(rounds) != design["benchmark_rounds"]:
                    raise PolicyShiftError(f"missing {arm} block {block} rounds for {case_id}")
                action = choices[arm][case_id].get("action")
                if action not in ORDERS:
                    raise PolicyShiftError(f"invalid frozen policy action for {case_id}/{arm}")
                for round_row in rounds:
                    round_number = round_row.get("round")
                    values = round_row.get("elapsed_ns")
                    if type(round_number) is not int or not isinstance(values, dict) or set(values) != set(ACTION_NAMES):
                        raise PolicyShiftError(f"malformed benchmark round for {case_id}/{arm}")
                    if any(type(value) is not int or value <= 0 for value in values.values()):
                        raise PolicyShiftError(f"invalid runtime value for {case_id}/{arm}")
                    oracle_ns = min(values[name] for name in ORDERS)
                    regret = values[action] / oracle_ns
                    round_logs[arm][round_number][case_id] = math.log(regret)
            for round_number in range(1, design["benchmark_rounds"] + 1):
                source_log = round_logs["source"][round_number].get(case_id)
                target_log = round_logs["target"][round_number].get(case_id)
                if source_log is None or target_log is None:
                    raise PolicyShiftError(f"paired source/target audit round missing for {case_id}")
                shift_group_logs[round_number][group].append(target_log - source_log)

        grouped_rounds: dict[str, list[float]] = {arm: [] for arm in ARMS}
        target_source_rounds: list[float] = []
        group_direction: dict[str, dict[str, bool]] = {arm: {} for arm in ARMS}
        for arm in ARMS:
            for round_number in range(1, design["benchmark_rounds"] + 1):
                per_group = []
                for group in groups:
                    members = [case for case in cases if case["group"] == group]
                    group_logs = [round_logs[arm][round_number][case["case_id"]] for case in members]
                    per_group.append(sum(group_logs) / len(group_logs))
                grouped_rounds[arm].append(sum(per_group) / len(per_group))
            for group in groups:
                member_logs = [
                    round_logs[arm][round_number][case["case_id"]]
                    for round_number in range(1, design["benchmark_rounds"] + 1)
                    for case in cases
                    if case["group"] == group
                ]
                group_direction[arm][group] = sum(member_logs) / len(member_logs) > 0
        for round_number in range(1, design["benchmark_rounds"] + 1):
            per_group = [
                sum(shift_group_logs[round_number][group]) / len(shift_group_logs[round_number][group])
                for group in groups
            ]
            target_source_rounds.append(sum(per_group) / len(per_group))

        source_log_regret = sum(grouped_rounds["source"]) / len(grouped_rounds["source"])
        target_log_regret = sum(grouped_rounds["target"]) / len(grouped_rounds["target"])
        shift_log_regret = sum(target_source_rounds) / len(target_source_rounds)
        target_mad = _mad(grouped_rounds["target"])
        shift_mad = _mad(target_source_rounds)
        target_threshold = max(math.log1p(design["noise_floor_fraction"]), 3 * target_mad)
        shift_threshold = max(math.log1p(design["noise_floor_fraction"]), 3 * shift_mad)
        target_regret = math.exp(target_log_regret)
        source_regret = math.exp(source_log_regret)
        shift_ratio = math.exp(shift_log_regret)
        requirements = {
            "target_regret_exceeds_five_percent": target_regret > 1 + design["minimum_target_regret"],
            "target_minus_source_regret_exceeds_three_points": target_regret - source_regret >= design["minimum_regret_increase"],
            "target_log_regret_exceeds_dispersion": target_log_regret > target_threshold,
            "target_source_log_regret_exceeds_dispersion": shift_log_regret > shift_threshold,
        }
        reports[str(block)] = {
            "passed": all(requirements.values()),
            "group_balanced_source_regret": source_regret,
            "group_balanced_target_regret": target_regret,
            "target_minus_source_regret": target_regret - source_regret,
            "target_to_source_regret_ratio": shift_ratio,
            "round_level_group_balanced_log_regret": {
                arm: grouped_rounds[arm] for arm in ARMS
            },
            "round_level_group_balanced_log_target_to_source_regret": target_source_rounds,
            "target_log_regret_mad": target_mad,
            "target_source_log_regret_mad": shift_mad,
            "target_dispersion_threshold": target_threshold,
            "target_source_dispersion_threshold": shift_threshold,
            "optimistic_per_round_oracle": True,
            "requirements": requirements,
        }
    return {
        "passed": all(report["passed"] for report in reports.values()),
        "blocks": reports,
        "dispersion_definition": "MAD of the 11 round-level group-balanced natural-log ratios; target and target/source evaluated independently",
    }


def measure_audit(design_path: Path, output: Path) -> dict[str, Any]:
    design_path, output = design_path.expanduser().resolve(), output.expanduser().resolve()
    prepared_path = output / "prepared-audit.json"
    if not prepared_path.is_file():
        raise PolicyShiftError("measure-audit requires a completed prepare-audit phase")
    prepared = _load(prepared_path)
    design = _design(design_path)
    toolchains = _toolchains(design)
    provenance = _prepare_provenance(design_path, design, toolchains)
    cases, choices = _validate_prepared_audit(
        prepared_path, prepared, design_path, design, toolchains, provenance, output
    )
    builds: dict[str, dict[str, dict[str, Any]]] = {arm: {} for arm in ARMS}
    for arm in ARMS:
        for case in cases:
            build_path = Path(prepared["builds_by_compiler"][arm][case["case_id"]]["build_report_path"])
            if not build_path.is_file() or _sha256(build_path) != prepared["builds_by_compiler"][arm][case["case_id"]]["build_report_sha256"]:
                raise PolicyShiftError(f"frozen audit build report changed for {case['case_id']}/{arm}")
            build = _load(build_path)
            if build.get("status") != "passed":
                raise PolicyShiftError(f"audit build did not pass for {case['case_id']}/{arm}")
            _validate_artifacts(build)
            builds[arm][case["case_id"]] = build
    for case in cases:
        source = builds["source"][case["case_id"]]
        target = builds["target"][case["case_id"]]
        if source["stock_checksum"] != target["stock_checksum"]:
            raise PolicyShiftError(f"source and target compiler correctness checksums differ for {case['case_id']}")
        source_driver = source["build_artifacts"]["driver_object"]["sha256"]
        target_driver = target["build_artifacts"]["driver_object"]["sha256"]
        if source_driver != target_driver:
            raise PolicyShiftError(f"shared Apple-Clang driver object differs for {case['case_id']}")

    per_case_by_id = {
        case["case_id"]: {
            **case,
            "measurements": {
                arm: {
                    "compiler_arm": arm,
                    "build_report_sha256": prepared["builds_by_compiler"][arm][case["case_id"]]["build_report_sha256"],
                    "iterations": None,
                    "timing_calibration": None,
                    "code_aliases": None,
                    "distinct_code_identities": builds[arm][case["case_id"]]["distinct_code_identities"],
                    "timing_caches": [],
                    "blocks": {},
                }
                for arm in ARMS
            },
        }
        for case in cases
    }
    # The frozen contraction timing helper restricts block two to discovery
    # cases. Its internal cache label is isolated from the public audit split.
    for block in (1, 2):
        for arm in ARMS:
            adapted_cases = [
                _measurement_case(case, builds[arm][case["case_id"]])
                for case in cases
            ]
            timed = contraction._measure_cases(
                adapted_cases,
                design,
                output / "timing" / arm,
                blocks=(block,),
            )
            if {case["case_id"] for case in timed} != {case["case_id"] for case in cases}:
                raise PolicyShiftError(f"timing helper omitted cases in {arm} block {block}")
            for timing in timed:
                case_id = timing["case_id"]
                destination = per_case_by_id[case_id]["measurements"][arm]
                if destination["iterations"] is not None and destination["iterations"] != timing["iterations"]:
                    raise PolicyShiftError(f"iteration calibration changed between blocks for {case_id}/{arm}")
                if destination["code_aliases"] is not None and destination["code_aliases"] != timing["code_aliases"]:
                    raise PolicyShiftError(f"action aliases changed between blocks for {case_id}/{arm}")
                destination["iterations"] = timing["iterations"]
                destination["timing_calibration"] = timing["timing_calibration"]
                destination["code_aliases"] = timing["code_aliases"]
                cache_refs = [ref for ref in timing["timing_caches"] if ref["identity_sha256"]]
                if len(cache_refs) != 1 or not Path(cache_refs[0]["path"]).is_file() or _sha256(Path(cache_refs[0]["path"])) != cache_refs[0]["sha256"]:
                    raise PolicyShiftError(f"timing cache reference is invalid for {case_id}/{arm}/block-{block}")
                raw_cache = _load(Path(cache_refs[0]["path"]))
                raw_benchmark = raw_cache.get("benchmark")
                if not isinstance(raw_benchmark, dict) or raw_benchmark.get("status") != "measured":
                    raise PolicyShiftError(f"raw benchmark cache is invalid for {case_id}/{arm}/block-{block}")
                round_rows = [
                    {
                        "round": row["block_round"],
                        "order": row["canonical_order"],
                        "elapsed_ns": {
                            action: row["measurements"][action]["elapsed_ns"]
                            for action in ACTION_NAMES
                        },
                    }
                    for row in timing["benchmark"]["round_records"]
                    if row["block"] == block
                ]
                if len(round_rows) != design["benchmark_rounds"]:
                    raise PolicyShiftError(f"timing block has an incomplete round set for {case_id}/{arm}")
                old_block = destination["blocks"].get(str(block))
                block_report = {
                    "timing_cache": cache_refs[0],
                    "stock_checksum": raw_benchmark["stock_checksum"],
                    "benchmark": raw_benchmark,
                    "rounds": round_rows,
                }
                if old_block is not None and old_block != block_report:
                    raise PolicyShiftError(f"timing block changed while resuming {case_id}/{arm}/{block}")
                destination["blocks"][str(block)] = block_report
                destination["timing_caches"].append(cache_refs[0])
    per_case = [per_case_by_id[case["case_id"]] for case in cases]
    for case in per_case:
        for arm in ARMS:
            blocks = case["measurements"][arm]["blocks"]
            if set(blocks) != {"1", "2"}:
                raise PolicyShiftError(f"both audit timing blocks are required for {case['case_id']}/{arm}")
            if blocks["1"]["stock_checksum"] != blocks["2"]["stock_checksum"]:
                raise PolicyShiftError(f"independent timing blocks changed checksum for {case['case_id']}/{arm}")
    gate = audit_gate(per_case, choices, design)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "audit_passed" if gate["passed"] else "no_demonstrated_adaptation_opportunity",
        "provenance": provenance,
        "prepared_audit_sha256": _sha256(prepared_path),
        "source_model_policy_hashes": prepared["policy_artifacts"],
        "timing_adapter": "contraction._measure_cases internal discovery partition to retain its frozen two-block timing/cache behavior; public case partition remains audit",
        "audit_gate": gate,
        "cases": per_case,
        "actions": list(ACTION_NAMES),
        "model_calls": 0,
        "performance_claim": False,
        "interpretation": (
            "Compiler-induced source-policy regret was demonstrated; proceed to the frozen repair phase."
            if gate["passed"]
            else "No demonstrated adaptation opportunity under this compiler shift; adaptation and test timing are not authorized by this result."
        ),
    }
    report_path = output / "audit-report.json"
    if report_path.is_file() and _load(report_path) != report:
        raise PolicyShiftError("completed audit report differs from existing frozen evidence")
    if not report_path.is_file():
        _save(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare-audit", "measure-audit"), required=True)
    parser.add_argument("--design", type=Path, default=DEFAULT_DESIGN)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.phase == "prepare-audit":
            report = prepare_audit(args.design, args.output)
        else:
            report = measure_audit(args.design, args.output)
    except (
        PolicyShiftError,
        feedback.CompilerFeedbackError,
        contraction.ContractionExperimentError,
        loop_build.LoopBuildError,
        OSError,
        TypeError,
        ValueError,
    ) as error:
        print(f"policy-shift experiment failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
