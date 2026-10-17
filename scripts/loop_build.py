"""Compile and verify pinned-LLVM loop-vectorization counterfactuals."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from . import counterfactual, experiment, quality_experiment, validate
except ImportError:  # Running as `python scripts/loop_build.py`.
    import counterfactual
    import experiment
    import quality_experiment
    import validate


SCHEMA_VERSION = 1
TARGET_CPU = "apple-m3"
TARGET_CPU_CANONICAL = "apple-a16"
DEFAULT_ACTIONS = (
    ("stock", None, None),
    ("scalar", 1, 1),
    ("vf2", 2, 1),
    ("vf4", 4, 1),
    ("vf8", 8, 1),
    ("vf4_ic2", 4, 2),
    ("vf4_ic4", 4, 4),
    ("vf8_ic2", 8, 2),
)

_MARKER = re.compile(r"^([ \t]*)// VECTOR_LOOP[ \t]*$")
_VECTOR_REMARK = re.compile(
    r"vectorized loop \(vectorization width: (\d+), interleaved count: (\d+)\)"
)
_INTERLEAVED_REMARK = re.compile(r"interleaved loop \(interleaved count: (\d+)\)")
_ATTRIBUTE_RE = {
    "target_cpu": re.compile(r'"target-cpu"="([^"]+)"'),
    "tune_cpu": re.compile(r'"tune-cpu"="([^"]+)"'),
}


class LoopBuildError(RuntimeError):
    """A loop build could not be safely compiled or resumed."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _source_path(path: Path, name: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise LoopBuildError(f"{name} source is not a file: {resolved}")
    return resolved


def _normalize_actions(
    actions: Sequence[tuple[str, int | None, int | None]],
) -> list[dict[str, Any]]:
    if isinstance(actions, (str, bytes)) or not actions:
        raise ValueError("actions must include stock followed by at least one action")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in actions:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 3:
            raise ValueError("each action must be a (name, vectorize_width, interleave_count) tuple")
        name, width, interleave = item
        if not isinstance(name, str) or re.fullmatch(r"[a-z0-9_]+", name) is None:
            raise ValueError("action names must contain lowercase letters, digits, or underscores")
        if name in seen:
            raise ValueError(f"duplicate action name: {name}")
        seen.add(name)
        if name == "stock":
            if width is not None or interleave is not None:
                raise ValueError("stock must not request a vectorization width or interleave count")
        elif (
            type(width) is not int
            or width <= 0
            or type(interleave) is not int
            or interleave <= 0
        ):
            raise ValueError("hinted actions need positive integer width and interleave count")
        normalized.append(
            {
                "name": name,
                "vectorize_width": width,
                "interleave_count": interleave,
            }
        )
    if normalized[0]["name"] != "stock" or sum(
        action["name"] == "stock" for action in normalized
    ) != 1:
        raise ValueError("actions must start with exactly one stock action")
    return normalized


def _inject_loop_hint(source: str, action: dict[str, Any]) -> tuple[str, int]:
    lines = source.splitlines(keepends=True)
    markers = [index for index, line in enumerate(lines) if _MARKER.fullmatch(line.rstrip("\r\n"))]
    loop_headers = [index for index, line in enumerate(lines) if re.search(r"\bfor\s*\(", line)]
    if (
        len(markers) != 1
        or len(loop_headers) != 1
        or loop_headers[0] != markers[0] + 1
        or "harness_kernel(" not in "".join(lines[: markers[0]])
    ):
        raise LoopBuildError(
            "kernel must contain one VECTOR_LOOP marker immediately before its harness_kernel loop"
        )

    marker = markers[0]
    indent = _MARKER.fullmatch(lines[marker].rstrip("\r\n")).group(1)
    newline = "\r\n" if lines[marker].endswith("\r\n") else "\n" if lines[marker].endswith("\n") else ""
    width = action["vectorize_width"]
    interleave = action["interleave_count"]
    replacement = (
        f"{indent}/* VECTOR_LOOP */{newline}"
        if width is None
        else f"{indent}#pragma clang loop vectorize_width({width}) interleave_count({interleave}){newline}"
    )
    lines[marker] = replacement
    return "".join(lines), loop_headers[0] + 1


def _ir_targets(ir: str) -> dict[str, str]:
    definition = re.search(r"^define\b[^\n]*@harness_kernel\([^\n]*$", ir, re.MULTILINE)
    if definition is None:
        raise LoopBuildError("optimized IR has no harness_kernel definition")
    attribute_number = re.search(r"#(\d+)[^{]*\{", definition.group(0))
    if attribute_number is None:
        raise LoopBuildError("harness_kernel has no LLVM target attribute group")
    attributes = re.search(
        rf"^attributes #{attribute_number.group(1)} = \{{([^\n]*)\}}$",
        ir,
        re.MULTILINE,
    )
    triple = re.search(r'^target triple = "([^"]+)"$', ir, re.MULTILINE)
    if attributes is None or triple is None:
        raise LoopBuildError("optimized IR is missing target metadata")

    values = {
        "target_triple": triple.group(1),
        "requested_target_cpu": TARGET_CPU,
        "requested_tune_cpu": TARGET_CPU,
    }
    body = attributes.group(1)
    for key, pattern in _ATTRIBUTE_RE.items():
        matches = pattern.findall(body)
        expected = TARGET_CPU_CANONICAL if key == "target_cpu" else TARGET_CPU
        if len(matches) != 1 or matches[0] != expected:
            raise LoopBuildError(
                f"harness_kernel {key} must resolve to {expected}; found {matches}"
            )
        values[key] = matches[0]
    values["target_cpu_alias_of"] = TARGET_CPU
    return values


def _realized_loop(
    stderr: bytes, action: dict[str, Any]
) -> tuple[dict[str, int] | None, str, list[str], str | None]:
    diagnostics = stderr.decode("utf-8", errors="replace").splitlines()
    vectorized: list[tuple[int, int]] = []
    missed: list[str] = []
    unrecognized: list[str] = []
    for line in diagnostics:
        match = _VECTOR_REMARK.search(line)
        if match:
            vectorized.append((int(match.group(1)), int(match.group(2))))
        elif match := _INTERLEAVED_REMARK.search(line):
            vectorized.append((1, int(match.group(1))))
        elif "loop not vectorized" in line.lower():
            missed.append(line)
        elif "vectorized loop" in line.lower():
            unrecognized.append(line)
        elif "interleaved loop" in line.lower():
            unrecognized.append(line)

    relevant = [
        line
        for line in diagnostics
        if any(
            phrase in line.lower()
            for phrase in (
                "vectorized loop",
                "interleaved loop",
                "loop not vectorized",
            )
        )
    ]
    remarks = list(dict.fromkeys(relevant))
    unique_realizations = list(dict.fromkeys(vectorized))
    unique_missed = list(dict.fromkeys(missed))
    if unrecognized or len(unique_realizations) > 1:
        return None, "unknown", remarks, "ambiguous LLVM loop-vectorize remarks"
    if unique_realizations and unique_missed:
        return None, "unknown", remarks, "conflicting LLVM loop-vectorize remarks"

    width = action["vectorize_width"]
    interleave = action["interleave_count"]
    if unique_realizations:
        realized_width, realized_interleave = unique_realizations[0]
    elif unique_missed or width == 1:
        realized_width, realized_interleave = 1, 1
    else:
        return None, "unknown", remarks, "LLVM emitted no usable loop-vectorize remark"

    realized = {
        "vectorize_width": realized_width,
        "interleave_count": realized_interleave,
    }
    if width is None:
        status = "natural"
    elif (realized_width, realized_interleave) == (width, interleave):
        status = "exact"
    elif realized_width == 1 and width > 1:
        status = "ignored"
    elif realized_width <= width and realized_interleave <= interleave:
        status = "clamped"
    else:
        status = "ignored"
    return realized, status, remarks, None


def _artifact(path: Path) -> dict[str, str | None]:
    return {
        "path": str(path),
        "sha256": _sha256_file(path) if path.is_file() else None,
    }


def _native_result(
    name: str,
    object_path: Path,
    executable_path: Path,
    *,
    driver_object: Path,
    linker: Path,
    iterations: int,
    artifacts_dir: Path,
    timeout_seconds: int,
    expected_checksum: str | None,
) -> dict[str, Any]:
    link = experiment._run_command(
        f"{name}_link",
        [str(linker), "-O2", str(driver_object), str(object_path), "-o", str(executable_path)],
        artifacts_dir,
        timeout_seconds,
    )
    linked = link["passed"] and executable_path.is_file()
    link_record = {
        "status": "passed" if linked else "failed",
        **experiment._public_command_result(link),
        "executable_path": str(executable_path),
        "executable_sha256": _sha256_file(executable_path)
        if executable_path.is_file()
        else None,
    }
    runtime: dict[str, Any] = {
        "status": "blocked",
        "reason": "link failed",
        "measurements": [],
    }
    if linked:
        run = experiment._run_command(
            f"{name}_run",
            [str(executable_path), str(iterations)],
            artifacts_dir,
            timeout_seconds,
        )
        parsed = validate._read_driver_result(run["_stdout"], iterations)
        checksum_matches = bool(
            parsed
            and (expected_checksum is None or parsed["checksum"] == expected_checksum)
        )
        runtime = {
            "status": "passed"
            if run["passed"] and parsed and checksum_matches
            else "failed",
            "measurements": [
                {
                    **experiment._public_command_result(run),
                    "correct": parsed["correct"] if parsed else False,
                    "iterations": parsed["iterations"] if parsed else None,
                    "checksum": parsed["checksum"] if parsed else None,
                    "checksum_matches_stock": checksum_matches,
                    "elapsed_ns": parsed["elapsed_ns"] if parsed else None,
                }
            ],
        }
    return {"link": link_record, "runtime": runtime}


def _reported_files(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
            yield value["path"], value["sha256"]
        for stem in ("stdout", "stderr", "executable"):
            path = value.get(f"{stem}_path")
            digest = value.get(f"{stem}_sha256")
            if isinstance(path, str) and isinstance(digest, str):
                yield path, digest
        for item in value.values():
            yield from _reported_files(item)
    elif isinstance(value, list):
        for item in value:
            yield from _reported_files(item)


def _resume_report(
    report_path: Path, case_dir: Path, fingerprint: str, action_names: list[str]
) -> dict[str, Any]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LoopBuildError(f"existing loop-build report cannot be read: {error}") from error
    if (
        not isinstance(report, dict)
        or report.get("status") != "passed"
        or report.get("case_fingerprint") != fingerprint
        or report.get("action_order") != action_names
        or not isinstance(report.get("actions"), dict)
        or set(report["actions"]) != set(action_names)
        or any(item.get("status") != "passed" for item in report["actions"].values())
    ):
        raise LoopBuildError(
            "existing loop-build report is failed, incomplete, or belongs to different inputs; use a fresh output directory"
        )
    for path_text, expected_hash in _reported_files(
        {"actions": report.get("actions"), "build_artifacts": report.get("build_artifacts"), "driver_compile": report.get("driver_compile")}
    ):
        path = Path(path_text).resolve()
        if not path.is_relative_to(case_dir) or not path.is_file() or _sha256_file(path) != expected_hash:
            raise LoopBuildError(f"cached loop-build artifact changed or is missing: {path}")
    return report


def build_case(
    validation_report: Path,
    kernel: Path,
    driver: Path,
    outdir: Path,
    actions: Sequence[tuple[str, int | None, int | None]] = DEFAULT_ACTIONS,
    correctness_iterations: int = 3,
    *,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Build loop variants with the validated pinned clang and check correctness."""
    if type(correctness_iterations) is not int or correctness_iterations <= 0:
        raise ValueError("correctness_iterations must be a positive integer")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")

    validation_path = validation_report.expanduser().resolve()
    inputs = counterfactual._validated_toolchain(validation_path)
    kernel_path = _source_path(kernel, "kernel")
    driver_path = _source_path(driver, "driver")
    action_specs = _normalize_actions(actions)
    source_text = kernel_path.read_text(encoding="utf-8")
    sources = {
        action["name"]: _inject_loop_hint(source_text, action)[0]
        for action in action_specs
    }
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
        "validation_report_sha256": _sha256_file(validation_path),
        "kernel_sha256": _sha256_file(kernel_path),
        "driver_sha256": _sha256_file(driver_path),
        "actions": action_specs,
        "correctness_iterations": correctness_iterations,
        "compiler_flags": compiler_flags,
        "driver_flags": driver_flags,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    action_names = [action["name"] for action in action_specs]
    if report_path.exists():
        return _resume_report(report_path, case_dir, fingerprint, action_names)

    commands_dir = case_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
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
            "kernel": {"path": str(kernel_path), "sha256": fingerprint_payload["kernel_sha256"]},
            "driver": {"path": str(driver_path), "sha256": fingerprint_payload["driver_sha256"]},
        },
        "toolchain": {
            "clang": {"path": str(inputs["clang"]), "sha256": llvm["clang_sha256"]},
            "linker": {"path": str(linker), "sha256": llvm["linker_sha256"]},
            "target": llvm["target"],
            "target_cpu": TARGET_CPU,
            "tune_cpu": TARGET_CPU,
        },
        "compiler_flags": compiler_flags,
        "correctness_iterations": correctness_iterations,
        "stock_checksum": None,
        "action_order": action_names,
        "build_artifacts": {},
        "driver_compile": {},
        "actions": {},
    }

    driver_object = case_dir / "driver.o"
    driver_object.unlink(missing_ok=True)
    driver_command = [
        str(linker),
        *driver_flags,
        "-c",
        str(driver_path),
        "-o",
        str(driver_object),
    ]
    driver_result = experiment._run_command(
        "driver_object", driver_command, case_dir, timeout_seconds
    )
    driver_ok = driver_result["passed"] and driver_object.is_file()
    report["driver_compile"] = experiment._public_command_result(driver_result)
    report["build_artifacts"]["driver_object"] = _artifact(driver_object)
    report["driver_flags"] = driver_flags
    if not driver_ok:
        report["errors"] = ["driver compilation failed or produced no object"]

    stock_checksum: str | None = None
    for action in action_specs:
        name = action["name"]
        action_dir = case_dir / "actions" / name
        action_dir.mkdir(parents=True, exist_ok=True)
        source_path = action_dir / "kernel.c"
        ir_path = action_dir / "optimized.ll"
        object_path = action_dir / "kernel.o"
        executable_path = action_dir / "kernel"
        for stale in (ir_path, object_path, executable_path):
            stale.unlink(missing_ok=True)
        source_path.write_text(sources[name], encoding="utf-8")

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
            f"{name}_optimized_ir", ir_command, case_dir, timeout_seconds
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
            f"{name}_object", object_command, case_dir, timeout_seconds
        )

        errors: list[str] = []
        ir_ok = ir_result["passed"] and ir_path.is_file()
        object_ok = object_result["passed"] and object_path.is_file()
        if not ir_ok:
            errors.append("optimized IR compilation failed or produced no IR")
        if not object_ok:
            errors.append("object compilation failed or produced no object")

        targets: dict[str, str] | None = None
        if ir_ok:
            try:
                targets = _ir_targets(ir_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, LoopBuildError) as error:
                errors.append(str(error))

        realized: dict[str, int] | None = None
        realization_status = "unknown"
        remarks: list[str] = []
        if object_ok:
            realized, realization_status, remarks, remark_error = _realized_loop(
                object_result["_stderr"], action
            )
            if remark_error:
                errors.append(remark_error)

        code_identity: str | None = None
        if object_ok:
            try:
                code_identity = quality_experiment.code_identity(object_path)
            except (OSError, ValueError) as error:
                errors.append(f"could not identify native object code: {error}")

        if object_ok and driver_ok:
            executable_path.unlink(missing_ok=True)
            correctness = _native_result(
                name,
                object_path,
                executable_path,
                driver_object=driver_object,
                linker=linker,
                iterations=correctness_iterations,
                artifacts_dir=case_dir,
                timeout_seconds=timeout_seconds,
                expected_checksum=None if name == "stock" else stock_checksum,
            )
        else:
            blocked_reason = "object compilation failed" if not object_ok else "driver compilation failed"
            correctness = {
                "link": {"status": "blocked", "reason": blocked_reason},
                "runtime": {"status": "blocked", "reason": blocked_reason},
            }
        if correctness["link"].get("status") != "passed":
            errors.append("native link failed")
        if correctness["runtime"].get("status") != "passed":
            errors.append("native correctness failed or checksum differed from stock")
        if name == "stock":
            measurements = correctness["runtime"].get("measurements", [])
            if (
                correctness["runtime"].get("status") == "passed"
                and measurements
                and isinstance(measurements[0].get("checksum"), str)
            ):
                stock_checksum = measurements[0]["checksum"]
                report["stock_checksum"] = stock_checksum
            else:
                errors.append("stock runtime did not produce a checksum baseline")
        elif stock_checksum is None:
            errors.append("stock checksum baseline is unavailable")
            correctness["runtime"]["status"] = "failed"
            if correctness["runtime"].get("measurements"):
                correctness["runtime"]["measurements"][0][
                    "checksum_matches_stock"
                ] = False

        artifact_paths = {
            "source": _artifact(source_path),
            "optimized_ir": _artifact(ir_path),
            "object": _artifact(object_path),
            "executable": _artifact(executable_path),
        }
        report["actions"][name] = {
            "status": "passed" if not errors else "failed",
            "object_path": str(object_path),
            "executable_path": str(executable_path),
            "requested": {
                "vectorize_width": action["vectorize_width"],
                "interleave_count": action["interleave_count"],
            },
            "realized": realized,
            "realization_status": realization_status,
            "llvm_remarks": remarks,
            "ir_targets": targets,
            "code_identity": code_identity,
            "artifacts": artifact_paths,
            "commands": {
                "optimized_ir": experiment._public_command_result(ir_result),
                "object": experiment._public_command_result(object_result),
            },
            "correctness": correctness,
            "errors": errors,
        }

    report["status"] = (
        "passed"
        if all(action["status"] == "passed" for action in report["actions"].values())
        else "failed"
    )
    _write_report(report_path, report)
    return report
