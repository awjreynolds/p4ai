"""Extract bounded, static evidence from one optimized kernel artifact."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

FEATURE_KEYS = (
    "ir_instruction_count",
    "ir_basic_block_count",
    "ir_load_count",
    "ir_store_count",
    "ir_multiply_count",
    "ir_vector_instruction_count",
    "ir_max_vector_bits",
    "ir_loop_metadata_count",
    "vectorize_remark_count",
    "vectorize_remark_max_vf",
    "vectorize_remark_max_interleave",
    "object_bytes",
)

_KERNEL_START = re.compile(r"^define\b[^\n]*@harness_kernel\(", re.MULTILINE)
_BLOCK_LABEL = re.compile(r'^(?:[-\w.$]+|"[^"\n]+"):\s*(?:;.*)?$')
_INSTRUCTION = re.compile(
    r'^\s*(?:%(?:[-\w.$]+|"[^"\n]+")\s*=\s*)?'
    r"(?:(?:tail|musttail|notail)\s+)?(?P<opcode>[A-Za-z][\w.]*)\b"
)
_VECTOR_TYPE = re.compile(
    r"<\s*(?P<scalable>vscale\s+x\s*)?(?P<width>\d+)\s+x\s*(?P<element>[^>]+)>"
)
_VECTOR_REMARK = re.compile(
    r"vectorized loop \(vectorization width: (\d+), interleaved count: (\d+)\)",
    re.IGNORECASE,
)
_INTERLEAVED_REMARK = re.compile(r"interleaved loop \(interleaved count: (\d+)\)", re.IGNORECASE)
_REMARK_TAG = re.compile(
    r"\[-Rpass(?P<kind>-(?:missed|analysis))?=loop-vectorize\]", re.IGNORECASE
)
_MISSED_REMARK = re.compile(
    r"\bloop not vectorized\b|\bvectorization is not (?:beneficial|profitable)\b",
    re.IGNORECASE,
)
_VECTOR_OPCODES = {"shufflevector", "extractelement", "insertelement"}
_ELEMENT_BITS = {
    "half": 16,
    "bfloat": 16,
    "float": 32,
    "double": 64,
    "fp128": 128,
}


def _empty_features() -> dict[str, int]:
    return dict.fromkeys(FEATURE_KEYS, 0)


def _kernel_body(ir: str) -> str | None:
    definition = _KERNEL_START.search(ir)
    if definition is None:
        return None
    header_end = ir.find("\n", definition.start())
    if header_end < 0 or "{" not in ir[definition.start() : header_end]:
        return None
    closing = re.search(r"^\}\s*$", ir[header_end + 1 :], re.MULTILINE)
    if closing is None:
        return None
    return ir[header_end + 1 : header_end + 1 + closing.start()]


def _element_bits(element: str) -> int | None:
    normalized = element.strip()
    integer = re.fullmatch(r"i(\d+)", normalized)
    if integer is not None:
        return int(integer.group(1))
    return _ELEMENT_BITS.get(normalized)


def _ir_features(ir: str) -> tuple[dict[str, int], str, str]:
    features = _empty_features()
    body = _kernel_body(ir)
    if body is None:
        return features, "unavailable", "harness_kernel body is missing or malformed"

    scalable_vector = False
    unknown_vector_type = False
    max_vector_bits = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or _BLOCK_LABEL.fullmatch(stripped):
            continue
        match = _INSTRUCTION.match(line)
        if match is None:
            continue
        opcode = match.group("opcode").lower()
        features["ir_instruction_count"] += 1
        features["ir_load_count"] += opcode == "load"
        features["ir_store_count"] += opcode == "store"
        features["ir_multiply_count"] += opcode in {"mul", "fmul"}
        vector_types = list(_VECTOR_TYPE.finditer(line))
        if vector_types or opcode in _VECTOR_OPCODES or "llvm.masked." in line or "llvm.vector." in line:
            features["ir_vector_instruction_count"] += 1
        for vector_type in vector_types:
            if vector_type.group("scalable") is not None:
                scalable_vector = True
                continue
            bits = _element_bits(vector_type.group("element"))
            if bits is None:
                unknown_vector_type = True
                continue
            max_vector_bits = max(
                max_vector_bits, int(vector_type.group("width")) * bits
            )
    features["ir_basic_block_count"] = sum(
        _BLOCK_LABEL.fullmatch(line.strip()) is not None for line in body.splitlines()
    )
    features["ir_loop_metadata_count"] = len(set(re.findall(r"!llvm\.loop\s+!(\d+)", body)))
    if scalable_vector or unknown_vector_type:
        return features, "ambiguous", "vector width cannot be represented as a fixed bit count"
    features["ir_max_vector_bits"] = max_vector_bits
    return features, "complete", ""


def _remark_features(stderr: bytes) -> tuple[dict[str, int], str, list[str]]:
    features = _empty_features()
    relevant = list(
        dict.fromkeys(
            line.strip()
            for line in stderr.decode("utf-8", errors="replace").splitlines()
            if "remark:" in line.lower()
            and _REMARK_TAG.search(line)
        )
    )
    widths: list[int] = []
    interleaves: list[int] = []
    missed: list[str] = []
    ambiguous = False
    for line in relevant:
        tag = _REMARK_TAG.search(line)
        assert tag is not None
        kind = tag.group("kind")
        if kind == "-missed":
            missed.append(line)
            continue
        if kind == "-analysis":
            if _MISSED_REMARK.search(line):
                missed.append(line)
            else:
                ambiguous = True
            continue
        if match := _VECTOR_REMARK.search(line):
            widths.append(int(match.group(1)))
            interleaves.append(int(match.group(2)))
        elif match := _INTERLEAVED_REMARK.search(line):
            widths.append(1)
            interleaves.append(int(match.group(1)))
        else:
            ambiguous = True
    # This feature counts successful loop-vectorize remarks only. Known misses
    # remain available as diagnostics but are not mixed into the success count.
    features["vectorize_remark_count"] = len(widths)
    if not ambiguous and widths:
        features["vectorize_remark_max_vf"] = max(widths)
        features["vectorize_remark_max_interleave"] = max(interleaves)
        status = "observed"
    elif ambiguous:
        status = "ambiguous"
    elif missed:
        status = "no_success"
    else:
        status = "no_observation"
    return features, status, missed


def extract_compiler_evidence(
    optimized_ir: Path | None, remarks: bytes, object_path: Path
) -> dict[str, Any]:
    """Return numeric features plus explicit extraction and observation statuses.

    Counts are limited to the ``harness_kernel`` function. This is static compiler
    evidence, not a runtime predictor; LLVM ``alloca`` instructions are not spills.
    """
    started = time.perf_counter()
    features = _empty_features()
    status = "complete"
    reason = ""
    if optimized_ir is None:
        ir_status, reason = "unavailable", "optimized IR path was not provided"
    else:
        try:
            ir = optimized_ir.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            ir_status, reason = "unavailable", f"optimized IR could not be read: {error}"
        else:
            ir_features, ir_status, reason = _ir_features(ir)
            features.update(ir_features)
    remark_features, remark_status, remark_diagnostics = _remark_features(remarks)
    for key in FEATURE_KEYS:
        if key.startswith("vectorize_"):
            features[key] = remark_features[key]
    try:
        features["object_bytes"] = object_path.stat().st_size
    except OSError as error:
        status = "partial"
        reason = reason or f"object size is unavailable: {error}"
    if ir_status != "complete" or remark_status == "ambiguous":
        status = "partial" if ir_status != "unavailable" else "unavailable"
    if remark_status == "ambiguous" and not reason:
        reason = "loop-vectorize remark format is unrecognized"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "features": features,
        "status": status,
        "ir_status": ir_status,
        "remark_status": remark_status,
        "remark_diagnostics": remark_diagnostics,
        "reason": reason,
        "extraction_elapsed_ms": round(elapsed_ms, 3),
    }
