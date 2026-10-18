from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import compiler_evidence  # noqa: E402, I001


FIXED_IR = '''target triple = "arm64-apple-macosx26.0.0"
define void @harness_kernel(ptr %a, ptr %b) {
entry:
  %x = load i32, ptr %a
  %y = load <4 x i32>, ptr %b
  %z = mul <4 x i32> %y, %y
  store <4 x i32> %z, ptr %a
  br label %loop, !llvm.loop !0
loop:
  ret void
}
!0 = distinct !{!0, !"llvm.loop.mustprogress"}
'''


@pytest.fixture
def artifacts(tmp_path: Path) -> tuple[Path, Path]:
    ir = tmp_path / "optimized.ll"
    obj = tmp_path / "kernel.o"
    ir.write_text(FIXED_IR, encoding="utf-8")
    obj.write_bytes(b"object")
    return ir, obj


def test_extracts_bounded_numeric_evidence_from_kernel_and_object(
    artifacts: tuple[Path, Path]
):
    ir, obj = artifacts
    remark = (
        b"kernel.c:10:5: remark: vectorized loop "
        b"(vectorization width: 4, interleaved count: 2) [-Rpass=loop-vectorize]"
    )
    report = compiler_evidence.extract_compiler_evidence(
        ir, remark + b"\n" + remark + b"\n" +
        b"kernel.c:10:5: remark: interleaved loop (interleaved count: 4) [-Rpass=loop-vectorize]",
        obj,
    )

    assert report["status"] == "complete"
    assert report["ir_status"] == "complete"
    assert report["remark_status"] == "observed"
    assert report["features"] == {
        "ir_instruction_count": 6,
        "ir_basic_block_count": 2,
        "ir_load_count": 2,
        "ir_store_count": 1,
        "ir_multiply_count": 1,
        "ir_vector_instruction_count": 3,
        "ir_max_vector_bits": 128,
        "ir_loop_metadata_count": 1,
        "vectorize_remark_count": 2,
        "vectorize_remark_max_vf": 4,
        "vectorize_remark_max_interleave": 4,
        "object_bytes": 6,
    }
    assert math.isfinite(report["extraction_elapsed_ms"])
    assert not any("spill" in key for key in report["features"])


def test_missing_vectorizer_remarks_are_explicit_zeroes(artifacts: tuple[Path, Path]):
    ir, obj = artifacts
    report = compiler_evidence.extract_compiler_evidence(ir, b"", obj)

    assert report["status"] == "complete"
    assert report["remark_status"] == "no_observation"
    assert report["features"]["vectorize_remark_count"] == 0
    assert report["features"]["vectorize_remark_max_vf"] == 0
    assert report["features"]["vectorize_remark_max_interleave"] == 0


def test_known_missed_remarks_are_no_success_not_ambiguous(
    artifacts: tuple[Path, Path],
):
    ir, obj = artifacts
    remarks = (
        b"kernel.c:10:5: remark: the cost-model indicates that vectorization is not beneficial "
        b"[-Rpass-analysis=loop-vectorize]\n"
        b"kernel.c:10:5: remark: loop not vectorized: could not prove legality "
        b"[-Rpass-missed=loop-vectorize]"
    )

    report = compiler_evidence.extract_compiler_evidence(ir, remarks, obj)

    assert report["status"] == "complete"
    assert report["remark_status"] == "no_success"
    assert report["features"]["vectorize_remark_count"] == 0
    assert report["features"]["vectorize_remark_max_vf"] == 0
    assert report["features"]["vectorize_remark_max_interleave"] == 0
    assert report["remark_diagnostics"] == [
        "kernel.c:10:5: remark: the cost-model indicates that vectorization is not beneficial "
        "[-Rpass-analysis=loop-vectorize]",
        "kernel.c:10:5: remark: loop not vectorized: could not prove legality "
        "[-Rpass-missed=loop-vectorize]",
    ]


def test_missed_tag_is_no_success_even_for_interleaving_diagnostic(
    artifacts: tuple[Path, Path],
):
    ir, obj = artifacts
    remark = (
        b"kernel.c:10:5: remark: the cost-model indicates that interleaving is not beneficial "
        b"[-Rpass-missed=loop-vectorize]"
    )

    report = compiler_evidence.extract_compiler_evidence(ir, remark, obj)

    assert report["status"] == "complete"
    assert report["remark_status"] == "no_success"
    assert report["features"]["vectorize_remark_count"] == 0
    assert report["features"]["vectorize_remark_max_vf"] == 0
    assert report["features"]["vectorize_remark_max_interleave"] == 0
    assert report["remark_diagnostics"] == [remark.decode()]


def test_scalable_vector_or_unrecognized_remark_is_not_silently_numeric(
    tmp_path: Path,
):
    ir = tmp_path / "scaled.ll"
    obj = tmp_path / "kernel.o"
    ir.write_text(
        FIXED_IR.replace("<4 x i32>", "<vscale x 4 x i32>"), encoding="utf-8"
    )
    obj.write_bytes(b"object")
    report = compiler_evidence.extract_compiler_evidence(
        ir,
        b"remark: loop vectorized using an unknown format [-Rpass=loop-vectorize]",
        obj,
    )

    assert report["status"] == "partial"
    assert report["ir_status"] == "ambiguous"
    assert report["remark_status"] == "ambiguous"
    assert report["features"]["ir_max_vector_bits"] == 0
    assert report["reason"]
