"""Robust host for LLVM's POSIX-FIFO interactive register-allocation advisor."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import io
import json
import os
import select
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

Policy = Callable[[dict[str, Any]], int | dict[str, Any]]
ROOT = Path(__file__).resolve().parents[1]
FEATURE_DESCRIPTIONS = {
    "mask": "1 marks a valid action: physical-register columns may be evicted, and the final column is a no-eviction sentinel.",
    "is_free": "1 if the physical register has no interfering live range.",
    "nr_urgent": "Normalized count of urgent live ranges that are safe to break.",
    "nr_broken_hints": "Broken register hints if this candidate is evicted.",
    "is_hint": "1 if this physical register is preferred for the live range.",
    "is_local": "1 if the live range is local to a basic block.",
    "nr_rematerializable": "Count of rematerializable live ranges.",
    "nr_defs_and_uses": "Frequency-weighted number of definitions and uses.",
    "weighed_reads_by_max": "Normalized frequency-weighted number of reads.",
    "weighed_writes_by_max": "Normalized frequency-weighted number of writes.",
    "weighed_read_writes_by_max": "Normalized frequency-weighted read/write uses.",
    "weighed_indvars_by_max": "Normalized frequency-weighted induction-variable updates.",
    "hint_weights_by_max": "Normalized frequency-weighted register hints.",
    "start_bb_freq_by_max": "Normalized frequency of the first block in the live range.",
    "end_bb_freq_by_max": "Normalized frequency of the last block in the live range.",
    "hottest_bb_freq_by_max": "Normalized frequency of the hottest block in the live range.",
    "liverange_size": "Instruction-index span of the live range.",
    "use_def_density": "Manual heuristic estimate of weighted use/definition density.",
    "max_stage": "Highest allocation stage among intervals in the live range.",
    "min_stage": "Lowest allocation stage among intervals in the live range.",
    "progress": "Ratio of the current allocation queue size to its initial size.",
}
NO_EVICTION_ACTION = "Do not evict a physical register; leave the current virtual register for allocator continuation."
ADVISOR_TASK = (
    "Choose one eligible allocator action. A physical-register candidate evicts that register. "
    "The final candidate position is a sentinel action: do not evict a physical register; "
    "leave the current virtual register for allocator continuation."
)


class AdvisorError(RuntimeError):
    """The host could not complete the interactive compiler protocol."""


class ReplayMismatch(AdvisorError):
    """The current decision state differs from the saved replay state."""


def load_log_reader(llvm_source: Path) -> Any:
    """Load the pinned LLVM reader by path instead of copying its binary parser."""
    path = llvm_source / "llvm/lib/Analysis/models/log_reader.py"
    spec = importlib.util.spec_from_file_location("p4ai_llvm_log_reader", path)
    if spec is None or spec.loader is None:
        raise AdvisorError(f"cannot load LLVM log reader at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("interactive compiler deadline expired")
    return remaining


class FIFOReader:
    """A deadline-aware file-like reader over the compiler-to-host FIFO."""

    def __init__(self, fd: int, process: subprocess.Popen[bytes], deadline: float):
        self.fd = fd
        self.process = process
        self.deadline = deadline
        self.buffer = bytearray()

    def _fill(self) -> bool:
        while True:
            try:
                ready, _, _ = select.select(
                    [self.fd], [], [], min(0.1, _remaining(self.deadline))
                )
            except InterruptedError:
                continue
            if not ready:
                if self.process.poll() is not None:
                    return False
                continue
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                continue
            if not data:
                return False
            self.buffer.extend(data)
            return True

    def readline(self) -> bytes:
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                end = newline + 1
                result = bytes(self.buffer[:end])
                del self.buffer[:end]
                return result
            if not self._fill():
                if not self.buffer:
                    return b""
                raise EOFError("compiler closed the FIFO in the middle of a line")

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("interactive protocol reads must have a bounded size")
        while len(self.buffer) < size:
            if not self._fill():
                raise EOFError(
                    f"compiler closed the FIFO after {len(self.buffer)} of {size} bytes"
                )
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


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


def _canonical_feature_value(value: float) -> int | str:
    if isinstance(value, float):
        return value.hex()
    return int(value)


def feature_fingerprint(features: dict[str, list[int | float]]) -> str:
    encoded = json.dumps(
        {
            key: [_canonical_feature_value(value) for value in values]
            for key, values in sorted(features.items())
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _feature_values(tensor_values: list[Any]) -> dict[str, list[int | float]]:
    features: dict[str, list[int | float]] = {}
    for tensor in tensor_values:
        values: list[int | float] = []
        for value in tensor:
            if isinstance(value, float):
                values.append(float(value))
            else:
                values.append(int(value))
        features[tensor.spec().name] = values
    return features


def _legal_actions(features: dict[str, list[int | float]]) -> list[int]:
    mask = features.get("mask")
    if mask is None:
        raise AdvisorError("interactive observation omitted the candidate mask")
    if any(value not in (0, 1) for value in mask):
        raise AdvisorError(
            "interactive candidate mask contains a value other than 0 or 1"
        )
    actions = [index for index, allowed in enumerate(mask) if allowed == 1]
    if not actions:
        raise AdvisorError(
            "interactive observation has no eligible eviction candidates"
        )
    return actions


def _eligible_feature_vectors(
    features: dict[str, list[int | float]], actions: list[int]
) -> dict[str, list[int | float]]:
    """Align candidate features to actions, broadcasting scalar features."""
    candidate_count = len(features["mask"])
    aligned: dict[str, list[int | float]] = {}
    for name, values in features.items():
        if name == "mask":
            continue
        if len(values) == candidate_count:
            aligned[name] = [values[index] for index in actions]
        elif len(values) == 1:
            aligned[name] = [values[0] for _ in actions]
        else:
            raise AdvisorError(
                f"feature {name!r} has {len(values)} values for {candidate_count} candidate columns"
            )
    return aligned


def _describe_actions(actions: list[int], candidate_count: int) -> list[dict[str, str]]:
    """Name physical-register actions and LLVM's final no-eviction sentinel."""
    result: list[dict[str, str]] = []
    for index in actions:
        description = (
            NO_EVICTION_ACTION
            if index == candidate_count - 1
            else f"Evict the physical register at candidate column {index}."
        )
        result.append({"id": str(index), "description": description})
    return result


def _action_bytes(action: int) -> bytes:
    return bytes(ctypes.c_int64(action))


def first_allowed(observation: dict[str, Any]) -> int:
    return int(observation["actions"][0]["id"])


def last_allowed(observation: dict[str, Any]) -> int:
    return int(observation["actions"][-1]["id"])


def replay_policy(records: list[dict[str, Any]]) -> Policy:
    class ReplayPolicy:
        def __init__(self) -> None:
            self.next_index = 0

        def __call__(self, observation: dict[str, Any]) -> int:
            if self.next_index >= len(records):
                raise ReplayMismatch(
                    "replay has no saved decision for the current observation"
                )
            saved = records[self.next_index]
            actual_fingerprint = observation["features_sha256"]
            actual_actions = [int(action["id"]) for action in observation["actions"]]
            if (
                saved.get("run_identity") != observation.get("run_identity")
                or saved["context"] != observation["context"]
                or saved["observation"] != observation["observation"]
                or saved["features_sha256"] != actual_fingerprint
                or saved["eligible_actions"] != actual_actions
            ):
                raise ReplayMismatch(
                    f"decision {self.next_index} run identity, state fingerprint, or legal actions changed; refusing replay"
                )
            self.next_index += 1
            return int(saved["action_index"])

        def finalize(self) -> None:
            if self.next_index != len(records):
                raise ReplayMismatch(
                    f"replay consumed {self.next_index} of {len(records)} saved decisions; refusing incomplete replay"
                )

    return ReplayPolicy()


def _resolve_policy_result(
    result: int | dict[str, Any], actions: list[int]
) -> tuple[int, dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if isinstance(result, dict):
        metadata = result
        chosen = result.get("chosen_action", result.get("action_index"))
        if chosen is None:
            raise AdvisorError("provider result is missing chosen_action")
        try:
            action = int(chosen)
        except (TypeError, ValueError) as exc:
            raise AdvisorError(
                "chosen_action must identify an integer candidate column"
            ) from exc
    else:
        action = int(result)
    if action not in actions:
        raise AdvisorError(
            f"policy selected ineligible action {action}; eligible actions are {actions}"
        )
    return action, metadata


def run_interactive(
    command: list[str],
    *,
    llvm_source: Path,
    policy: Policy,
    policy_name: str,
    timeout_seconds: int,
    stdout_path: Path,
    stderr_path: Path,
    run_identity: dict[str, Any] | None = None,
    allow_raw_action: bool = False,
    raw_action: int | str | None = None,
) -> dict[str, Any]:
    """Run one compiler process and answer each MLGO eviction query."""
    if not command:
        raise ValueError("compiler command is empty")
    if allow_raw_action and raw_action is None:
        raise ValueError("raw action mode requires an integer action")
    reader_module = load_log_reader(llvm_source)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    decisions: list[dict[str, Any]] = []
    communication_error: str | None = None
    timed_out = False
    return_code: int | None = None
    start = time.monotonic()
    deadline = start + timeout_seconds
    stdout_file = stdout_path.open("wb")
    stderr_file = stderr_path.open("wb")
    process: subprocess.Popen[bytes] | None = None
    input_fd: int | None = None
    output_fd: int | None = None

    try:
        with tempfile.TemporaryDirectory(prefix="p4ai-regalloc-") as temp_dir:
            channel_base = Path(temp_dir) / "channel"
            input_path = Path(f"{channel_base}.in")
            output_path = Path(f"{channel_base}.out")
            os.mkfifo(input_path, 0o600)
            os.mkfifo(output_path, 0o600)
            input_fd = os.open(input_path, os.O_RDWR | os.O_NONBLOCK)
            output_fd = os.open(output_path, os.O_RDWR | os.O_NONBLOCK)
            compiler_command = [
                *command,
                f"-regalloc-evict-interactive-channel-base={channel_base}",
            ]
            process = subprocess.Popen(
                compiler_command,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            reader = FIFOReader(output_fd, process, deadline)
            header_line = reader.readline()
            if not header_line:
                raise AdvisorError(
                    "compiler closed the output FIFO before writing its header"
                )
            tensor_specs, score_spec, advice_spec = reader_module.read_header(
                io.BytesIO(header_line)
            )
            if score_spec is not None:
                raise AdvisorError("unexpected scored interactive regalloc protocol")
            if advice_spec is None or advice_spec.element_type is not ctypes.c_int64:
                raise AdvisorError(
                    "regalloc advice header is not a signed int64 tensor"
                )
            if advice_spec.shape != [1]:
                raise AdvisorError(
                    f"expected one int64 advice value, got shape {advice_spec.shape}"
                )

            context_name: str | None = None
            while True:
                event = reader.readline()
                if not event:
                    break
                context_name, observation, tensors, _ = (
                    reader_module.read_one_observation(
                        context_name, event.decode("utf-8"), reader, tensor_specs, None
                    )
                )
                features = _feature_values(tensors)
                actions = _legal_actions(features)
                actions_for_provider = _describe_actions(actions, len(features["mask"]))
                context = {
                    "context": context_name,
                    "observation": observation,
                    "actions": actions_for_provider,
                    "task": ADVISOR_TASK,
                    "features": features,
                    "mask": features["mask"],
                    "feature_descriptions": FEATURE_DESCRIPTIONS,
                    "run_identity": run_identity,
                }
                features_hash = feature_fingerprint(features)
                context["features_sha256"] = features_hash
                context["provider_packet"] = {
                    "task": context["task"],
                    "features": _eligible_feature_vectors(features, actions),
                    "feature_descriptions": FEATURE_DESCRIPTIONS,
                    "actions": actions_for_provider,
                }
                provider_metadata: dict[str, Any] = {}
                if allow_raw_action:
                    if raw_action == "masked":
                        # A padded column fails the order bound first. Advance
                        # legally until a zero-mask slot lies inside that order.
                        physical = [i for i in actions if i < len(features["mask"]) - 1]
                        action = next(
                            (
                                i
                                for i in range(max(physical, default=0))
                                if i not in actions
                            ),
                            actions[0],
                        )
                    else:
                        action = int(raw_action)
                else:
                    action, provider_metadata = _resolve_policy_result(
                        policy(context), actions
                    )
                payload = _action_bytes(action)
                written = os.write(input_fd, payload)
                if written != len(payload):
                    raise AdvisorError(
                        f"short write to compiler FIFO: sent {written} of {len(payload)} bytes"
                    )
                decisions.append(
                    {
                        "run_identity": run_identity,
                        "context": context_name,
                        "observation": observation,
                        "eligible_actions": actions,
                        "features_sha256": features_hash,
                        "action_index": action,
                        "action_sha256": hashlib.sha256(payload).hexdigest(),
                        "policy": policy_name,
                        "provider_metadata": provider_metadata,
                    }
                )

            finalize = getattr(policy, "finalize", None)
            if finalize is not None and not allow_raw_action:
                finalize()

            if input_fd is not None:
                os.close(input_fd)
                input_fd = None
            wait_for = _remaining(deadline)
            try:
                return_code = process.wait(timeout=wait_for)
            except subprocess.TimeoutExpired:
                timed_out = True
                communication_error = (
                    f"compiler did not exit within {timeout_seconds} seconds"
                )
                _stop_process(process)
                return_code = process.returncode
    except TimeoutError as exc:
        timed_out = True
        communication_error = str(exc)
    except (AdvisorError, EOFError, OSError, ValueError, json.JSONDecodeError) as exc:
        communication_error = str(exc)
    finally:
        if input_fd is not None:
            os.close(input_fd)
        if output_fd is not None:
            os.close(output_fd)
        if process is not None and process.poll() is None:
            _stop_process(process)
        if process is not None:
            return_code = process.returncode
        stdout_file.close()
        stderr_file.close()

    return {
        "command": command,
        "compiler_command": compiler_command
        if "compiler_command" in locals()
        else command,
        "policy": policy_name,
        "return_code": return_code,
        "timed_out": timed_out,
        "communication_error": communication_error,
        "duration_ms": round((time.monotonic() - start) * 1000),
        "decision_count": len(decisions),
        "decisions": decisions,
        "stdout_path": str(stdout_path),
        "stdout_sha256": hashlib.sha256(stdout_path.read_bytes()).hexdigest(),
        "stderr_path": str(stderr_path),
        "stderr_sha256": hashlib.sha256(stderr_path.read_bytes()).hexdigest(),
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llvm-source",
        type=Path,
        default=ROOT / ".cache/iree/third_party/llvm-project",
    )
    parser.add_argument("--policy", choices=("first", "last"), default="first")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--stdout", type=Path, default=Path("advisor.stdout"))
    parser.add_argument("--stderr", type=Path, default=Path("advisor.stderr"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    policy = first_allowed if args.policy == "first" else last_allowed
    result = run_interactive(
        command,
        llvm_source=args.llvm_source.resolve(),
        policy=policy,
        policy_name=args.policy,
        timeout_seconds=args.timeout_seconds,
        stdout_path=args.stdout.resolve(),
        stderr_path=args.stderr.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0 if result["return_code"] == 0 and result["communication_error"] is None else 1
    )


if __name__ == "__main__":
    raise SystemExit(_main())
