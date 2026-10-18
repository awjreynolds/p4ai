from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import contraction_build  # noqa: E402, I001


SPEC = {
    "m": 3,
    "n": 5,
    "k": 7,
    "a_column_major": False,
    "b_column_major": True,
    "c_column_major": False,
}
CHECKSUM = "0123456789abcdef"
FAKE_IR = '''define void @harness_kernel(ptr %p) {
entry:
  %x = load i32, ptr %p
  %y = load <4 x i32>, ptr %p
  %z = mul <4 x i32> %y, %y
  store <4 x i32> %z, ptr %p
  ret void
}
'''


def install_fakes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_command: str | None = None,
    object_remarks: bytes = b"",
):
    clang = tmp_path / "clang"
    linker = tmp_path / "linker"
    validation = tmp_path / "validation.json"
    clang.write_bytes(b"pinned clang")
    linker.write_bytes(b"validated linker")
    validation.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        contraction_build.counterfactual,
        "_validated_toolchain",
        lambda _path: {
            "clang": clang,
            "paths": {"linker": linker},
            "report": {
                "llvm": {
                    "clang_sha256": hashlib.sha256(clang.read_bytes()).hexdigest(),
                    "linker_sha256": hashlib.sha256(linker.read_bytes()).hexdigest(),
                    "target": "arm64-apple-darwin",
                    "source_revision": "pinned",
                }
            },
        },
    )
    generated_orders: list[str] = []
    generated_specs: list[dict] = []

    def generate_case(outdir: Path, spec: dict, order: str = "ijk") -> dict:
        generated_orders.append(order)
        generated_specs.append(dict(spec))
        outdir.mkdir(parents=True, exist_ok=True)
        kernel = outdir / "kernel.c"
        driver = outdir / "driver.c"
        kernel.write_text(
            f"// order: {order}\n// shape: {spec['m']}x{spec['n']}x{spec['k']}\n"
            f"// padding: {spec['a_padding']},{spec['b_padding']},{spec['c_padding']}\n",
            encoding="utf-8",
        )
        driver.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        return {
            "kernel": kernel,
            "driver": driver,
            "features": {
                "m": spec["m"],
                "n": spec["n"],
                "k": spec["k"],
                "a_padding": spec["a_padding"],
                "b_padding": spec["b_padding"],
                "c_padding": spec["c_padding"],
            },
        }

    monkeypatch.setattr(
        contraction_build,
        "_corpus_module",
        lambda: SimpleNamespace(__file__=Path(__file__), generate_case=generate_case),
    )
    monkeypatch.setattr(
        contraction_build.loop_build,
        "_ir_targets",
        lambda _ir: {
            "target_triple": "arm64-apple-macosx26.0.0",
            "requested_target_cpu": "apple-m3",
            "requested_tune_cpu": "apple-m3",
            "target_cpu": "apple-a16",
            "target_cpu_alias_of": "apple-m3",
            "tune_cpu": "apple-m3",
        },
    )

    commands: list[list[str]] = []

    def run_command(name: str, command: list[str], artifacts_dir: Path, _timeout: int):
        commands.append(command)
        output = Path(command[command.index("-o") + 1]) if "-o" in command else None
        stdout = b""
        stderr = (
            object_remarks
            if name.endswith("_object") and name != "driver_object"
            else b""
        )
        failed = name == fail_command
        if not failed and name.endswith("_optimized_ir"):
            output.write_text(FAKE_IR, encoding="utf-8")
        elif not failed and name.endswith("_object") and name != "driver_object":
            source = next(Path(arg) for arg in command if arg.endswith("kernel.c"))
            output.write_bytes(source.read_bytes())
        elif not failed and (name == "driver_object" or name.endswith("_link")):
            output.write_bytes(name.encode())
        elif not failed and name.endswith("_run"):
            stdout = (
                json.dumps(
                    {
                        "correct": True,
                        "iterations": int(command[-1]),
                        "checksum": CHECKSUM,
                        "elapsed_ns": 17,
                    }
                )
                + "\n"
            ).encode()
        if failed:
            stderr = b"error: deliberate compile failure"
        log_dir = artifacts_dir / "commands"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"{name}.stdout"
        stderr_path = log_dir / f"{name}.stderr"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        return {
            "command": command,
            "return_code": 1 if failed else 0,
            "duration_ms": 1,
            "stdout_path": str(stdout_path),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_path": str(stderr_path),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "error": None,
            "passed": not failed,
            "_stdout": stdout,
            "_stderr": stderr,
        }

    monkeypatch.setattr(contraction_build.experiment, "_run_command", run_command)
    monkeypatch.setattr(
        contraction_build.quality_experiment,
        "code_identity",
        lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return validation, commands, generated_orders, generated_specs


def test_build_case_builds_all_orders_checks_canonical_identity_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    validation, commands, generated_orders, generated_specs = install_fakes(
        tmp_path, monkeypatch
    )
    spec = {**SPEC, "a_padding": 3, "b_padding": 2, "c_padding": 4}
    report = contraction_build.build_case(
        validation, spec, tmp_path / "case", correctness_iterations=2
    )

    assert report["status"] == "passed"
    assert report["action_order"] == list(contraction_build.ACTION_ORDER)
    assert generated_orders == ["ijk", *contraction_build.ORDERS]
    assert all(item == {**SPEC, "a_padding": 3, "b_padding": 2, "c_padding": 4} for item in generated_specs)
    assert report["stock_ijk_code_identity_equal"]
    assert report["provenance"]["compiler_evidence_extractor_sha256"] == hashlib.sha256(
        Path(contraction_build.compiler_evidence.__file__).read_bytes()
    ).hexdigest()
    assert report["stock_checksum"] == CHECKSUM
    assert all(action["status"] == "passed" for action in report["actions"].values())
    assert report["actions"]["stock"]["requested"] == {"order": "ijk"}
    assert report["inputs"]["spec"] == {**SPEC, "a_padding": 3, "b_padding": 2, "c_padding": 4}
    assert report["actions"]["stock"]["features"]["c_padding"] == 4
    assert report["actions"]["stock"]["compiler_evidence"] == {
        "ir_instruction_count": 5,
        "ir_basic_block_count": 1,
        "ir_load_count": 2,
        "ir_store_count": 1,
        "ir_multiply_count": 1,
        "ir_vector_instruction_count": 3,
        "ir_max_vector_bits": 128,
        "ir_loop_metadata_count": 0,
        "vectorize_remark_count": 0,
        "vectorize_remark_max_vf": 0,
        "vectorize_remark_max_interleave": 0,
        "object_bytes": len(
            "// order: ijk\n// shape: 3x5x7\n// padding: 3,2,4\n".encode()
        ),
    }
    assert report["actions"]["stock"]["compiler_evidence_status"]["vectorization_remarks"] == "no_observation"
    assert report["actions"]["stock"]["vectorization_diagnostics"] == []
    assert report["actions"]["stock"]["compiler_evidence_extraction_ms"] >= 0
    assert report["actions"]["kji"]["correctness"]["runtime"]["measurements"][0][
        "checksum_matches_stock"
    ]
    assert all(
        "-O3" in command and "-mcpu=apple-m3" in command and "-mtune=apple-m3" in command
        for command in commands
        if any("kernel.c" in arg for arg in command)
    )
    assert "-fno-vectorize" in report["driver_compile"]["command"]
    command_count = len(commands)
    assert contraction_build.build_case(validation, spec, tmp_path / "case", correctness_iterations=2) == report
    assert len(commands) == command_count

    object_path = Path(report["actions"]["kji"]["artifacts"]["object"]["path"])
    object_path.write_bytes(b"changed")
    with pytest.raises(contraction_build.loop_build.LoopBuildError, match="artifact changed"):
        contraction_build.build_case(validation, spec, tmp_path / "case", correctness_iterations=2)


def test_build_case_records_compile_failure_and_refuses_failed_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    validation, _commands, _orders, _specs = install_fakes(
        tmp_path, monkeypatch, fail_command="kij_object"
    )
    report = contraction_build.build_case(validation, SPEC, tmp_path / "failed")

    assert report["status"] == "failed"
    assert report["actions"]["kij"]["status"] == "failed"
    assert report["actions"]["kij"]["correctness"]["runtime"]["status"] == "blocked"
    with pytest.raises(contraction_build.loop_build.LoopBuildError, match="failed, incomplete"):
        contraction_build.build_case(validation, SPEC, tmp_path / "failed")


def test_nonbeneficial_vectorization_diagnostic_does_not_fail_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    remark = (
        b"kernel.c:10:5: remark: the cost-model indicates that vectorization is not beneficial "
        b"[-Rpass-analysis=loop-vectorize]"
    )
    validation, _commands, _orders, _specs = install_fakes(
        tmp_path, monkeypatch, object_remarks=remark
    )

    report = contraction_build.build_case(validation, SPEC, tmp_path / "no-success")

    action = report["actions"]["stock"]
    assert report["status"] == "passed"
    assert action["compiler_evidence_status"]["status"] == "complete"
    assert action["compiler_evidence_status"]["vectorization_remarks"] == "no_success"
    assert action["compiler_evidence"]["vectorize_remark_count"] == 0
    assert action["vectorization_diagnostics"] == [remark.decode()]


def test_spec_requires_exact_positive_shapes_and_boolean_layouts():
    with pytest.raises(ValueError, match="positive integer"):
        contraction_build._normalize_spec({**SPEC, "m": 0})
    with pytest.raises(ValueError, match="must be a boolean"):
        contraction_build._normalize_spec({**SPEC, "a_column_major": 1})
    assert contraction_build._normalize_spec(SPEC)["a_padding"] == 0
    assert contraction_build._normalize_spec({**SPEC, "b_padding": 64})["b_padding"] == 64
    with pytest.raises(ValueError, match="0 through 64"):
        contraction_build._normalize_spec({**SPEC, "c_padding": 65})
