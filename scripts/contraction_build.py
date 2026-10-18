"""Build native loop-order variants for unsigned matrix contraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

try:
    from . import counterfactual, experiment, loop_build, quality_experiment, validate
except ImportError:  # Running as `python scripts/contraction_build.py`.
    import counterfactual
    import experiment
    import loop_build
    import quality_experiment
    import validate


SCHEMA_VERSION = 1
TARGET_CPU = "apple-m3"
ORDERS = ("ijk", "ikj", "jik", "jki", "kij", "kji")
ACTION_ORDER = ("stock", *ORDERS)


class ContractionBuildError(RuntimeError):
    """A contraction build could not be safely compiled or resumed."""


def _corpus_module():
    try:
        from . import contraction_corpus
    except ImportError:  # Running as `python scripts/contraction_build.py`.
        import contraction_corpus
    return contraction_corpus


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_spec(spec: dict[str, Any]) -> dict[str, int | bool]:
    expected = {"m", "n", "k", "a_column_major", "b_column_major", "c_column_major"}
    if not isinstance(spec, dict) or set(spec) != expected:
        raise ValueError(f"spec must contain exactly: {', '.join(sorted(expected))}")
    normalized: dict[str, int | bool] = {}
    for name in ("m", "n", "k"):
        if type(spec[name]) is not int or spec[name] <= 0:
            raise ValueError(f"spec {name} must be a positive integer")
        normalized[name] = spec[name]
    for name in ("a_column_major", "b_column_major", "c_column_major"):
        if type(spec[name]) is not bool:
            raise ValueError(f"spec {name} must be a boolean")
        normalized[name] = spec[name]
    return normalized


def _validate_generated_case(value: Any, source_dir: Path, order: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not {"kernel", "driver", "features"} <= value.keys():
        raise ContractionBuildError(f"corpus generator returned an invalid {order} case")
    paths = {}
    for name in ("kernel", "driver"):
        path = Path(value[name]).expanduser().resolve()
        if not path.is_file() or not path.is_relative_to(source_dir.resolve()):
            raise ContractionBuildError(f"corpus generator returned an invalid {order} {name} path")
        paths[name] = path
    features = value["features"]
    if not isinstance(features, dict) or any(
        not isinstance(key, str)
        or type(item) is not int
        for key, item in features.items()
    ):
        raise ContractionBuildError(f"corpus generator returned invalid {order} features")
    return {**paths, "features": features}


def _remarks(stderr: bytes) -> list[str]:
    return list(
        dict.fromkeys(
            line
            for line in stderr.decode("utf-8", errors="replace").splitlines()
            if "remark:" in line.lower()
        )
    )


def build_case(
    validation_report: Path,
    spec: dict[str, Any],
    outdir: Path,
    *,
    correctness_iterations: int = 3,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Compile six legal orders plus a separately built canonical stock arm."""
    if type(correctness_iterations) is not int or correctness_iterations <= 0:
        raise ValueError("correctness_iterations must be a positive integer")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    normalized_spec = _normalize_spec(spec)
    validation_path = validation_report.expanduser().resolve()
    inputs = counterfactual._validated_toolchain(validation_path)
    corpus = _corpus_module()
    corpus_path = Path(corpus.__file__).resolve()
    case_dir = outdir.expanduser().resolve()
    case_dir.mkdir(parents=True, exist_ok=True)
    report_path = case_dir / "report.json"
    compiler_flags = [
        "-std=c11",
        "-O3",
        f"--target={validate.TRIPLE}",
        f"-mcpu={TARGET_CPU}",
        f"-mtune={TARGET_CPU}",
        "-mllvm",
        "-verify-machineinstrs",
        "-Rpass=loop-vectorize",
        "-Rpass-missed=loop-vectorize",
    ]
    driver_flags = [
        "-std=c11",
        "-O2",
        f"--target={validate.TRIPLE}",
        f"-mcpu={TARGET_CPU}",
        f"-mtune={TARGET_CPU}",
        "-fno-vectorize",
        "-fno-slp-vectorize",
    ]
    fingerprint_payload = {
        "schema_version": SCHEMA_VERSION,
        "builder_sha256": _sha256_file(Path(__file__).resolve()),
        "loop_builder_sha256": _sha256_file(Path(loop_build.__file__).resolve()),
        "corpus_sha256": _sha256_file(corpus_path),
        "validation_report_sha256": _sha256_file(validation_path),
        "spec": normalized_spec,
        "action_order": ACTION_ORDER,
        "correctness_iterations": correctness_iterations,
        "compiler_flags": compiler_flags,
        "driver_flags": driver_flags,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if report_path.exists():
        return loop_build._resume_report(report_path, case_dir, fingerprint, list(ACTION_ORDER))

    generated: dict[str, dict[str, Any]] = {}
    for action in ACTION_ORDER:
        order = "ijk" if action == "stock" else action
        source_dir = case_dir / "sources" / action
        source_dir.mkdir(parents=True, exist_ok=True)
        generated[action] = _validate_generated_case(
            corpus.generate_case(source_dir, normalized_spec, order=order), source_dir, order
        )

    driver_hashes = {action: _sha256_file(item["driver"]) for action, item in generated.items()}
    if len(set(driver_hashes.values())) != 1:
        raise ContractionBuildError("loop-order variants did not share one independent driver")

    llvm = inputs["report"]["llvm"]
    linker = inputs["paths"]["linker"]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "report_path": str(report_path),
        "case_fingerprint": fingerprint,
        "inputs": {
            "validation_report": {
                "path": str(validation_path),
                "sha256": fingerprint_payload["validation_report_sha256"],
            },
            "spec": normalized_spec,
            "corpus_generator": {"path": str(corpus_path), "sha256": fingerprint_payload["corpus_sha256"]},
        },
        "toolchain": {
            "clang": {"path": str(inputs["clang"]), "sha256": llvm["clang_sha256"]},
            "linker": {"path": str(linker), "sha256": llvm["linker_sha256"]},
            "target": llvm["target"],
            "target_cpu": TARGET_CPU,
            "tune_cpu": TARGET_CPU,
        },
        "compiler_flags": compiler_flags,
        "driver_flags": driver_flags,
        "correctness_iterations": correctness_iterations,
        "action_order": list(ACTION_ORDER),
        "stock_checksum": None,
        "build_artifacts": {
            "kernel_sources": {action: loop_build._artifact(item["kernel"]) for action, item in generated.items()},
            "driver_sources": {action: loop_build._artifact(item["driver"]) for action, item in generated.items()},
        },
        "driver_compile": {},
        "actions": {},
        "errors": [],
    }

    driver_object = case_dir / "driver.o"
    driver_object.unlink(missing_ok=True)
    driver_command = [
        str(linker),
        *driver_flags,
        "-c",
        str(generated["stock"]["driver"]),
        "-o",
        str(driver_object),
    ]
    driver_result = experiment._run_command(
        "driver_object", driver_command, case_dir, timeout_seconds
    )
    driver_ok = driver_result["passed"] and driver_object.is_file()
    report["driver_compile"] = experiment._public_command_result(driver_result)
    report["build_artifacts"]["driver_object"] = loop_build._artifact(driver_object)
    if not driver_ok:
        report["errors"].append("driver compilation failed or produced no object")

    stock_checksum: str | None = None
    for action in ACTION_ORDER:
        generated_case = generated[action]
        action_dir = case_dir / "actions" / action
        action_dir.mkdir(parents=True, exist_ok=True)
        ir_path = action_dir / "optimized.ll"
        object_path = action_dir / "kernel.o"
        executable_path = action_dir / "kernel"
        for stale in (ir_path, object_path, executable_path):
            stale.unlink(missing_ok=True)
        source_path = generated_case["kernel"]
        source_order = "ijk" if action == "stock" else action
        ir_command = [
            str(inputs["clang"]),
            *compiler_flags,
            "-S",
            "-emit-llvm",
            str(source_path),
            "-o",
            str(ir_path),
        ]
        ir_result = experiment._run_command(
            f"{action}_optimized_ir", ir_command, case_dir, timeout_seconds
        )
        object_command = [
            str(inputs["clang"]),
            *compiler_flags,
            "-c",
            str(source_path),
            "-o",
            str(object_path),
        ]
        object_result = experiment._run_command(
            f"{action}_object", object_command, case_dir, timeout_seconds
        )
        errors: list[str] = []
        ir_ok = ir_result["passed"] and ir_path.is_file()
        object_ok = object_result["passed"] and object_path.is_file()
        if not ir_ok:
            errors.append("optimized IR compilation failed or produced no IR")
        if not object_ok:
            errors.append("object compilation failed or produced no object")

        targets = None
        if ir_ok:
            try:
                targets = loop_build._ir_targets(ir_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, loop_build.LoopBuildError) as error:
                errors.append(str(error))

        code_identity = None
        if object_ok:
            try:
                code_identity = quality_experiment.code_identity(object_path)
            except (OSError, ValueError) as error:
                errors.append(f"could not identify native object code: {error}")

        if object_ok and driver_ok:
            correctness = loop_build._native_result(
                action,
                object_path,
                executable_path,
                driver_object=driver_object,
                linker=linker,
                iterations=correctness_iterations,
                artifacts_dir=case_dir,
                timeout_seconds=timeout_seconds,
                expected_checksum=None if action == "stock" else stock_checksum,
            )
        else:
            reason = "object compilation failed" if not object_ok else "driver compilation failed"
            correctness = {
                "link": {"status": "blocked", "reason": reason},
                "runtime": {"status": "blocked", "reason": reason},
            }
        if correctness["link"].get("status") != "passed":
            errors.append("native link failed")
        if correctness["runtime"].get("status") != "passed":
            errors.append("native correctness failed or checksum differed from stock")

        if action == "stock":
            measurements = correctness["runtime"].get("measurements", [])
            if correctness["runtime"].get("status") == "passed" and measurements:
                stock_checksum = measurements[0].get("checksum")
                if isinstance(stock_checksum, str):
                    report["stock_checksum"] = stock_checksum
                else:
                    errors.append("stock runtime did not produce a checksum baseline")
        elif stock_checksum is None:
            errors.append("stock checksum baseline is unavailable")
            correctness["runtime"]["status"] = "failed"

        report["actions"][action] = {
            "status": "passed" if not errors else "failed",
            "order": source_order,
            "requested": {"order": source_order},
            "features": generated_case["features"],
            "object_path": str(object_path),
            "executable_path": str(executable_path),
            "code_identity": code_identity,
            "ir_targets": targets,
            "llvm_remarks": _remarks(object_result["_stderr"]),
            "artifacts": {
                "source": loop_build._artifact(source_path),
                "optimized_ir": loop_build._artifact(ir_path),
                "object": loop_build._artifact(object_path),
                "executable": loop_build._artifact(executable_path),
            },
            "commands": {
                "optimized_ir": experiment._public_command_result(ir_result),
                "object": experiment._public_command_result(object_result),
            },
            "correctness": correctness,
            "errors": errors,
        }

    stock = report["actions"]["stock"]
    ijk = report["actions"]["ijk"]
    equal_code = stock["code_identity"] is not None and stock["code_identity"] == ijk["code_identity"]
    report["stock_ijk_code_identity_equal"] = equal_code
    if not equal_code:
        for action in (stock, ijk):
            action["errors"].append("canonical stock and ijk code identities differ or are unavailable")
            action["status"] = "failed"
    report["status"] = (
        "passed"
        if not report.get("errors")
        and all(action["status"] == "passed" for action in report["actions"].values())
        else "failed"
    )
    loop_build._write_report(report_path, report)
    return report
