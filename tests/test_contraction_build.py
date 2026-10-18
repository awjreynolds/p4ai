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


def install_fakes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_command: str | None = None,
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

    def generate_case(outdir: Path, spec: dict, order: str = "ijk") -> dict:
        generated_orders.append(order)
        outdir.mkdir(parents=True, exist_ok=True)
        kernel = outdir / "kernel.c"
        driver = outdir / "driver.c"
        kernel.write_text(
            f"// order: {order}\n// shape: {spec['m']}x{spec['n']}x{spec['k']}\n",
            encoding="utf-8",
        )
        driver.write_text("int main(void) { return 0; }\n", encoding="utf-8")
        return {
            "kernel": kernel,
            "driver": driver,
            "features": {"m": spec["m"], "n": spec["n"], "k": spec["k"]},
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
        stderr = b""
        failed = name == fail_command
        if not failed and name.endswith("_optimized_ir"):
            output.write_text("; fake optimized IR\n", encoding="utf-8")
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
    return validation, commands, generated_orders


def test_build_case_builds_all_orders_checks_canonical_identity_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    validation, commands, generated_orders = install_fakes(tmp_path, monkeypatch)
    report = contraction_build.build_case(
        validation, SPEC, tmp_path / "case", correctness_iterations=2
    )

    assert report["status"] == "passed"
    assert report["action_order"] == list(contraction_build.ACTION_ORDER)
    assert generated_orders == ["ijk", *contraction_build.ORDERS]
    assert report["stock_ijk_code_identity_equal"]
    assert report["stock_checksum"] == CHECKSUM
    assert all(action["status"] == "passed" for action in report["actions"].values())
    assert report["actions"]["stock"]["requested"] == {"order": "ijk"}
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
    assert contraction_build.build_case(validation, SPEC, tmp_path / "case", correctness_iterations=2) == report
    assert len(commands) == command_count

    object_path = Path(report["actions"]["kji"]["artifacts"]["object"]["path"])
    object_path.write_bytes(b"changed")
    with pytest.raises(contraction_build.loop_build.LoopBuildError, match="artifact changed"):
        contraction_build.build_case(validation, SPEC, tmp_path / "case", correctness_iterations=2)


def test_build_case_records_compile_failure_and_refuses_failed_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    validation, _commands, _orders = install_fakes(
        tmp_path, monkeypatch, fail_command="kij_object"
    )
    report = contraction_build.build_case(validation, SPEC, tmp_path / "failed")

    assert report["status"] == "failed"
    assert report["actions"]["kij"]["status"] == "failed"
    assert report["actions"]["kij"]["correctness"]["runtime"]["status"] == "blocked"
    with pytest.raises(contraction_build.loop_build.LoopBuildError, match="failed, incomplete"):
        contraction_build.build_case(validation, SPEC, tmp_path / "failed")


def test_spec_requires_exact_positive_shapes_and_boolean_layouts():
    with pytest.raises(ValueError, match="positive integer"):
        contraction_build._normalize_spec({**SPEC, "m": 0})
    with pytest.raises(ValueError, match="must be a boolean"):
        contraction_build._normalize_spec({**SPEC, "a_column_major": 1})
