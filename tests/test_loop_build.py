from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import loop_build  # noqa: E402, I001


KERNEL = """#include <stdint.h>
uint64_t harness_kernel(const uint32_t *a, uint32_t *out) {
    // VECTOR_LOOP
    for (unsigned i = 0; i < 16; ++i) out[i] = a[i] + 1;
    return out[0];
}
"""

IR = '''target triple = "arm64-apple-macosx26.0.0"
define i64 @harness_kernel(ptr %a) #0 {
  ret i64 0
}
attributes #0 = { "target-cpu"="apple-a16" "tune-cpu"="apple-m3" }
'''
CHECKSUM = "0123456789abcdef"


def install_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fail_object: str | None = None):
    clang = tmp_path / "clang"
    linker = tmp_path / "linker"
    validation = tmp_path / "validation.json"
    clang.write_bytes(b"pinned clang")
    linker.write_bytes(b"validated linker")
    validation.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        loop_build.counterfactual,
        "_validated_toolchain",
        lambda _path: {
            "clang": clang,
            "paths": {"linker": linker},
            "expected_checksum": "fedcba9876543210",
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

    commands = []

    def run_command(name: str, command: list[str], artifacts_dir: Path, _timeout: int):
        commands.append(command)
        stdout = b""
        stderr = b""
        output = Path(command[command.index("-o") + 1]) if "-o" in command else None
        action_name = output.parent.name if output else ""
        if name.endswith("_optimized_ir"):
            output.write_text(IR, encoding="utf-8")
        elif name.endswith("_object"):
            if action_name == fail_object:
                stderr = b"error: deliberate compile failure"
            else:
                output.write_bytes(f"object:{action_name}".encode())
                if action_name == "stock":
                    stderr = b"remark: vectorized loop (vectorization width: 4, interleaved count: 2)"
                elif action_name == "vf2":
                    stderr = b"remark: vectorized loop (vectorization width: 2, interleaved count: 1)"
        elif name.endswith("_link"):
            output.write_bytes(f"executable:{name}".encode())
        elif name.endswith("_run"):
            stdout = (
                json.dumps(
                    {
                        "correct": True,
                        "iterations": int(command[1]),
                        "checksum": CHECKSUM,
                        "elapsed_ns": 12,
                    }
                )
                + "\n"
            ).encode()

        command_dir = artifacts_dir / "commands"
        command_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = command_dir / f"{name}.stdout"
        stderr_path = command_dir / f"{name}.stderr"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        failed = name.endswith("_object") and action_name == fail_object
        return {
            "command": command,
            "return_code": 1 if failed else 0,
            "duration_ms": 2,
            "stdout_path": str(stdout_path),
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_path": str(stderr_path),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
            "error": None,
            "passed": not failed,
            "_stdout": stdout,
            "_stderr": stderr,
        }

    monkeypatch.setattr(loop_build.experiment, "_run_command", run_command)
    monkeypatch.setattr(loop_build.quality_experiment, "code_identity", lambda _path: "native-code-id")
    return validation, commands


def test_build_case_injects_only_loop_directives_and_records_realized_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    validation, commands = install_fakes(tmp_path, monkeypatch)
    kernel = tmp_path / "kernel.c"
    driver = tmp_path / "driver.c"
    kernel.write_text(KERNEL, encoding="utf-8")
    driver.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    actions = (("stock", None, None), ("scalar", 1, 1), ("vf2", 2, 1))

    report = loop_build.build_case(
        validation,
        kernel,
        driver,
        tmp_path / "case",
        actions=actions,
        correctness_iterations=3,
    )

    assert report["status"] == "passed"
    assert report["stock_checksum"] == CHECKSUM
    assert report["actions"]["stock"]["realized"] == {
        "vectorize_width": 4,
        "interleave_count": 2,
    }
    assert report["actions"]["stock"]["realization_status"] == "natural"
    assert report["actions"]["scalar"]["realized"] == {
        "vectorize_width": 1,
        "interleave_count": 1,
    }
    assert report["actions"]["scalar"]["realization_status"] == "exact"
    assert report["actions"]["vf2"]["realization_status"] == "exact"
    assert Path(report["actions"]["vf2"]["object_path"]).is_file()
    assert Path(report["actions"]["vf2"]["executable_path"]).is_file()
    assert report["actions"]["vf2"]["ir_targets"] == {
        "target_triple": "arm64-apple-macosx26.0.0",
        "requested_target_cpu": "apple-m3",
        "requested_tune_cpu": "apple-m3",
        "target_cpu": "apple-a16",
        "target_cpu_alias_of": "apple-m3",
        "tune_cpu": "apple-m3",
    }
    assert all(
        action["correctness"]["runtime"]["measurements"][0]["checksum_matches_stock"]
        for action in report["actions"].values()
    )

    stock_source = Path(report["actions"]["stock"]["artifacts"]["source"]["path"]).read_text()
    vf2_source = Path(report["actions"]["vf2"]["artifacts"]["source"]["path"]).read_text()
    assert "#pragma clang loop" not in stock_source
    assert "#pragma clang loop vectorize_width(2) interleave_count(1)" in vf2_source
    compile_commands = [
        command
        for command in commands
        if "-c" in command or "-emit-llvm" in command
    ]
    assert all("-mcpu=apple-m3" in command and "-mtune=apple-m3" in command for command in compile_commands)
    kernel_commands = [command for command in compile_commands if any("kernel.c" in arg for arg in command)]
    assert all("-Rpass=loop-vectorize" in command for command in kernel_commands)
    driver_command = report["driver_compile"]["command"]
    assert "-fno-vectorize" in driver_command
    assert "-fno-slp-vectorize" in driver_command

    command_count = len(commands)
    assert loop_build.build_case(validation, kernel, driver, tmp_path / "case", actions, 3) == report
    assert len(commands) == command_count

    Path(report["actions"]["vf2"]["artifacts"]["object"]["path"]).write_bytes(b"changed")
    with pytest.raises(loop_build.LoopBuildError, match="artifact changed"):
        loop_build.build_case(validation, kernel, driver, tmp_path / "case", actions, 3)


def test_build_case_reports_compile_failure_and_refuses_failed_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    validation, _commands = install_fakes(tmp_path, monkeypatch, fail_object="vf2")
    kernel = tmp_path / "kernel.c"
    driver = tmp_path / "driver.c"
    kernel.write_text(KERNEL, encoding="utf-8")
    driver.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    actions = (("stock", None, None), ("vf2", 2, 1))

    report = loop_build.build_case(validation, kernel, driver, tmp_path / "case", actions)

    assert report["status"] == "failed"
    assert report["actions"]["vf2"]["status"] == "failed"
    assert report["actions"]["vf2"]["correctness"]["link"]["status"] == "blocked"
    with pytest.raises(loop_build.LoopBuildError, match="failed, incomplete"):
        loop_build.build_case(validation, kernel, driver, tmp_path / "case", actions)


def test_loop_hint_injection_rejects_ambiguous_or_detached_marker():
    action = {"name": "vf4", "vectorize_width": 4, "interleave_count": 2}
    with pytest.raises(loop_build.LoopBuildError, match="one VECTOR_LOOP marker"):
        loop_build._inject_loop_hint(KERNEL.replace("// VECTOR_LOOP\n", ""), action)
    detached = KERNEL.replace("// VECTOR_LOOP\n    for", "// VECTOR_LOOP\n    int x = 0;\n    for")
    with pytest.raises(loop_build.LoopBuildError, match="immediately before"):
        loop_build._inject_loop_hint(detached, action)


def test_realized_loop_accepts_deduplicated_interleaved_remark():
    action = {"name": "stock", "vectorize_width": None, "interleave_count": None}
    remark = (
        b"kernel.c:10:5: remark: interleaved loop (interleaved count: 4) "
        b"[-Rpass=loop-vectorize]"
    )

    realized, status, remarks, error = loop_build._realized_loop(
        remark + b"\n" + remark, action
    )

    assert realized == {"vectorize_width": 1, "interleave_count": 4}
    assert status == "natural"
    assert remarks == [remark.decode()]
    assert error is None


def test_realized_stock_without_vectorizer_remark_stays_unknown():
    action = {"name": "stock", "vectorize_width": None, "interleave_count": None}

    realized, status, remarks, error = loop_build._realized_loop(b"", action)

    assert realized is None
    assert status == "unknown"
    assert remarks == []
    assert error == "LLVM emitted no usable loop-vectorize remark"
