#!/usr/bin/env python3
"""Run a bounded protocol and code-shape smoke for LLVM's interactive inliner.

The default route emits unoptimized LLVM IR with the repository's pinned Clang,
then runs the pinned ``opt`` SCC-Oz inliner pass. No tools are built or selected
from PATH automatically. This is a feasibility check, not a benchmark.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLANG = ROOT / ".cache/llvm-build/bin/clang"
DEFAULT_OPT = ROOT / ".cache/llvm-build/bin/opt"
DEFAULT_LLVM_SOURCE = ROOT / ".cache/iree/third_party/llvm-project"
POLICIES = ("0", "1", "default")

SOURCE = """\
static unsigned transform(unsigned x) {
  unsigned a = x * 17u + 13u;
  unsigned b = (a ^ (x >> 2)) * 7u;
  unsigned c = (b + 29u) ^ (a >> 1);
  return c + (x * x);
}

unsigned public_caller(unsigned x) {
  unsigned first = transform(x);
  unsigned second = transform(x + 1u);
  return first ^ (second + 3u);
}
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_caller_body(ir: str) -> dict[str, Any]:
    """Return stable code evidence for public_caller, excluding LLVM metadata."""
    match = re.search(
        r"^define\b[^\n]*@public_caller\([^\n]*?\)\s*[^\n]*\{\n"
        r"(?P<body>.*?)^\}",
        ir,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        return {
            "found": False,
            "body": None,
            "body_sha256": None,
            "body_lines": 0,
            "calls_transform": None,
        }

    lines = []
    for line in match.group("body").splitlines():
        line = re.sub(r",?\s*![A-Za-z0-9_.-]+\s+!\d+", "", line).strip()
        if line and not line.startswith(";"):
            lines.append(line)
    body = "\n".join(lines)
    return {
        "found": True,
        "body": body,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "body_lines": len(lines),
        "calls_transform": bool(re.search(r"\bcall\b[^\n]*@transform\(", body)),
    }


def choose_advice(policy: str, default: list[int]) -> int:
    if len(default) != 1 or default[0] not in (0, 1):
        raise ValueError("inlining_default must contain one boolean value")
    choice = default[0] if policy == "default" else int(policy)
    if choice not in (0, 1):
        raise ValueError(f"invalid inliner choice {choice}")
    return choice


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def _run_to_files(
    command: list[str], timeout_seconds: int, stdout_path: Path, stderr_path: Path
) -> dict[str, Any]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    error = None
    return_code = None
    started = time.monotonic()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            try:
                return_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                error = f"process exceeded {timeout_seconds}s timeout"
                _stop_process(process)
                return_code = process.returncode
        except OSError as exc:
            error = str(exc)
        finally:
            if process is not None and process.poll() is None:
                _stop_process(process)
            if process is not None:
                return_code = process.returncode

    return {
        "command": command,
        "return_code": return_code,
        "timed_out": timed_out,
        "error": error,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "stdout": {"path": str(stdout_path), "bytes": stdout_path.stat().st_size},
        "stderr": {"path": str(stderr_path), "bytes": stderr_path.stat().st_size},
        "stderr_head": stderr_path.read_text(errors="replace")[:2000],
        "stderr_tail": stderr_path.read_text(errors="replace")[-2000:],
    }


def _tool_identity(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        version = result.stdout.strip()
        version_return_code = result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        version = f"unavailable: {exc}"
        version_return_code = None
    return {
        "path": str(path),
        "sha256": sha256(path),
        "version": version,
        "version_return_code": version_return_code,
    }


def _llvm_revision(llvm_source: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(llvm_source), "rev-parse", "HEAD"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _frontend_command(clang: Path, source: Path, input_ir: Path) -> list[str]:
    return [
        str(clang),
        "-O2",
        "-Xclang",
        "-disable-llvm-passes",
        "-S",
        "-emit-llvm",
        str(source),
        "-o",
        str(input_ir),
    ]


def run_policy(
    *,
    advisor: Any,
    log_reader: Any,
    tool: Path,
    mode: str,
    source: Path,
    input_ir: Path | None,
    output_dir: Path,
    policy: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_id = f"{mode}-{policy}"
    ir_path = output_dir / f"{run_id}.ll"
    stdout_path = output_dir / f"{run_id}.stdout"
    stderr_path = output_dir / f"{run_id}.stderr"
    observations: list[dict[str, Any]] = []
    error = None
    timed_out = False
    return_code = None
    command: list[str] = []
    process: subprocess.Popen[bytes] | None = None
    input_fd: int | None = None
    output_fd: int | None = None
    started = time.monotonic()
    deadline = started + timeout_seconds
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    ir_path.unlink(missing_ok=True)

    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            with tempfile.TemporaryDirectory(
                prefix="inliner-channel-", dir=output_dir
            ) as temp_dir:
                channel_base = Path(temp_dir) / "channel"
                input_path = Path(f"{channel_base}.in")
                output_path = Path(f"{channel_base}.out")
                os.mkfifo(input_path, 0o600)
                os.mkfifo(output_path, 0o600)
                input_fd = os.open(input_path, os.O_RDWR | os.O_NONBLOCK)
                output_fd = os.open(output_path, os.O_RDWR | os.O_NONBLOCK)

                if mode == "opt":
                    if input_ir is None:
                        raise ValueError("opt mode needs frontend-produced LLVM IR")
                    command = [
                        str(tool),
                        "-passes=scc-oz-module-inliner",
                        "-S",
                        str(input_ir),
                        "-o",
                        str(ir_path),
                        "-enable-ml-inliner=release",
                        "-inliner-interactive-include-default",
                        f"-inliner-interactive-channel-base={channel_base}",
                    ]
                else:
                    command = [
                        str(tool),
                        "-O2",
                        "-S",
                        "-emit-llvm",
                        str(source),
                        "-o",
                        str(ir_path),
                        "-mllvm",
                        "-enable-ml-inliner=release",
                        "-mllvm",
                        "-inliner-interactive-include-default",
                        "-mllvm",
                        f"-inliner-interactive-channel-base={channel_base}",
                    ]

                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                reader = advisor.FIFOReader(output_fd, process, deadline)
                header = reader.readline()
                if not header:
                    raise RuntimeError("compiler exited before interactive header")
                tensor_specs, score_spec, advice_spec = log_reader.read_header(
                    io.BytesIO(header)
                )
                if score_spec is not None:
                    raise RuntimeError("unexpected score tensor in inliner protocol")
                if (
                    advice_spec is None
                    or advice_spec.name != "inlining_decision"
                    or advice_spec.element_type is not ctypes.c_int64
                    or advice_spec.shape != [1]
                ):
                    raise RuntimeError(
                        "expected signed int64[1] inlining_decision advice"
                    )
                defaults = [spec for spec in tensor_specs if spec.name == "inlining_default"]
                if (
                    len(defaults) != 1
                    or defaults[0].element_type is not ctypes.c_int64
                    or defaults[0].shape != [1]
                ):
                    raise RuntimeError("expected signed int64[1] inlining_default")

                context: str | None = None
                while True:
                    event_line = reader.readline()
                    if not event_line:
                        break
                    context, observation_id, tensors, _ = log_reader.read_one_observation(
                        context,
                        event_line.decode("utf-8"),
                        reader,
                        tensor_specs,
                        None,
                    )
                    features = advisor._feature_values(tensors)
                    choice = choose_advice(policy, features.get("inlining_default", []))
                    payload = advisor._action_bytes(choice)
                    written = os.write(input_fd, payload)
                    if written != len(payload):
                        raise RuntimeError(
                            f"short FIFO write: {written} of {len(payload)} bytes"
                        )
                    observations.append(
                        {
                            "context": context,
                            "observation": observation_id,
                            "features": features,
                            "choice": choice,
                            "policy": policy,
                        }
                    )

                os.close(input_fd)
                input_fd = None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                try:
                    return_code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    error = f"compiler did not exit within {timeout_seconds}s"
                    _stop_process(process)
                    return_code = process.returncode
        except TimeoutError as exc:
            timed_out = True
            error = str(exc)
        except subprocess.TimeoutExpired:
            timed_out = True
            error = f"compiler did not exit within {timeout_seconds}s"
        except (
            OSError,
            ValueError,
            EOFError,
            RuntimeError,
            json.JSONDecodeError,
        ) as exc:
            error = str(exc)
        finally:
            if input_fd is not None:
                os.close(input_fd)
            if output_fd is not None:
                os.close(output_fd)
            if process is not None and process.poll() is None:
                _stop_process(process)
            if process is not None:
                return_code = process.returncode

    ir_exists = ir_path.is_file()
    return {
        "policy": policy,
        "mode": mode,
        "command": command,
        "return_code": return_code,
        "timed_out": timed_out,
        "communication_error": error,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "observations": observations,
        "observation_count": len(observations),
        "optimized_ir": {
            "path": str(ir_path),
            "sha256": sha256(ir_path) if ir_exists else None,
            "caller_body": (
                normalized_caller_body(ir_path.read_text(errors="replace"))
                if ir_exists
                else None
            ),
        },
        "stdout": {"path": str(stdout_path), "bytes": stdout_path.stat().st_size},
        "stderr": {"path": str(stderr_path), "bytes": stderr_path.stat().st_size},
        "stderr_head": stderr_path.read_text(errors="replace")[:2000],
        "stderr_tail": stderr_path.read_text(errors="replace")[-2000:],
    }


def _valid_run(run: dict[str, Any]) -> bool:
    caller_body = _caller_body(run)
    return (
        run["return_code"] == 0
        and not run["timed_out"]
        and run["communication_error"] is None
        and run["observation_count"] > 0
        and caller_body is not None
        and caller_body["found"]
    )


def _caller_body(run: dict[str, Any]) -> dict[str, Any] | None:
    optimized_ir = run.get("optimized_ir")
    if not isinstance(optimized_ir, dict):
        return None
    caller_body = optimized_ir.get("caller_body")
    return caller_body if isinstance(caller_body, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("opt", "clang-driver"), default="opt")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--clang", type=Path)
    parser.add_argument("--opt", type=Path, help="explicit opt override in --mode opt")
    parser.add_argument("--llvm-source", type=Path, default=DEFAULT_LLVM_SOURCE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=45)
    args = parser.parse_args(argv)

    root = args.repo_root.resolve()
    llvm_source = args.llvm_source.resolve()
    clang = (args.clang or root / ".cache/llvm-build/bin/clang").resolve()
    if args.mode == "clang-driver" and args.opt is not None:
        parser.error("--opt is valid only with --mode opt")
    tool = (
        args.opt.resolve()
        if args.opt is not None
        else (root / ".cache/llvm-build/bin/opt").resolve()
        if args.mode == "opt"
        else clang
    )
    output_dir = (args.output_dir or root / "artifacts/inliner-feasibility").resolve()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    for label, path in (("clang", clang), ("inliner tool", tool)):
        if not path.is_file() or not os.access(path, os.X_OK):
            hint = (
                "; this script does not build tools or fall back to Homebrew/PATH"
                if label == "inliner tool" and args.mode == "opt"
                else ""
            )
            parser.error(f"{label} is not an executable file: {path}{hint}")
    log_reader_path = llvm_source / "llvm/lib/Analysis/models/log_reader.py"
    if not log_reader_path.is_file():
        parser.error(f"LLVM log_reader.py not found: {log_reader_path}")
    if not (root / "scripts/advisor.py").is_file():
        parser.error(f"scripts/advisor.py not found under --repo-root {root}")

    sys.path.insert(0, str(root))
    from scripts import advisor

    try:
        log_reader = advisor.load_log_reader(llvm_source)
    except (OSError, ImportError, advisor.AdvisorError) as exc:
        parser.error(f"could not load LLVM's protocol reader: {exc}")

    output_dir.mkdir(parents=True, exist_ok=True)
    source = output_dir / "inliner_smoke.c"
    source.write_text(SOURCE, encoding="utf-8")
    input_ir = output_dir / "frontend-no-opt.ll" if args.mode == "opt" else None
    frontend = None
    if input_ir is not None:
        input_ir.unlink(missing_ok=True)
        frontend_command = _frontend_command(clang, source, input_ir)
        frontend = _run_to_files(
            frontend_command,
            args.timeout_seconds,
            output_dir / "frontend.stdout",
            output_dir / "frontend.stderr",
        )
        frontend["input_ir"] = {
            "path": str(input_ir),
            "sha256": sha256(input_ir) if input_ir.is_file() else None,
        }

    runs = []
    if frontend is None or (
        frontend["return_code"] == 0
        and not frontend["timed_out"]
        and frontend["error"] is None
    ):
        runs = [
            run_policy(
                advisor=advisor,
                log_reader=log_reader,
                tool=tool,
                mode=args.mode,
                source=source,
                input_ir=input_ir,
                output_dir=output_dir,
                policy=policy,
                timeout_seconds=args.timeout_seconds,
            )
            for policy in POLICIES
        ]

    all_runs_valid = len(runs) == len(POLICIES) and all(_valid_run(run) for run in runs)
    bodies = [_caller_body(run) for run in runs[:2]]
    distinct_zero_one = (
        len(bodies) == 2
        and all(body is not None and body["found"] for body in bodies)
        and bodies[0]["body_sha256"] != bodies[1]["body_sha256"]
    )
    report = {
        "schema_version": 1,
        "status": "passed" if all_runs_valid and distinct_zero_one else "failed",
        "claim": "interactive inliner protocol and caller-code-shape feasibility; no performance claim",
        "mode": args.mode,
        "decision": "context is recorded as protocol context only; it is not treated as a caller identifier",
        "policies": list(POLICIES),
        "source": {
            "path": str(source),
            "sha256": sha256(source),
            "text": source.read_text(encoding="utf-8"),
        },
        "script": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "clang": _tool_identity(clang),
        "inliner_tool": _tool_identity(tool),
        "llvm_source": {
            "path": str(llvm_source),
            "revision": _llvm_revision(llvm_source),
            "log_reader": str(log_reader_path),
        },
        "frontend": frontend,
        "timeout_seconds_per_run": args.timeout_seconds,
        "distinct_0_vs_1_normalized_caller_body": bool(distinct_zero_one),
        "runs": runs,
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    compact_runs = [
        {
            "policy": run["policy"],
            "return_code": run["return_code"],
            "timed_out": run["timed_out"],
            "communication_error": run["communication_error"],
            "observation_count": run["observation_count"],
            "caller_body": _caller_body(run),
        }
        for run in runs
    ]
    print(
        json.dumps(
            {
                "status": report["status"],
                "mode": args.mode,
                "distinct_0_vs_1_normalized_caller_body": distinct_zero_one,
                "runs": compact_runs,
                "report_path": str(report_path),
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
