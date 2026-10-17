#!/usr/bin/env python3
"""Measure native corpus executables in deterministic interleaved rounds."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import re
import subprocess
import sys
from pathlib import Path
from statistics import median
from typing import Any

try:
    from .validate import _read_driver_result
except ImportError:  # Running as `python scripts/benchmark.py`.
    from validate import _read_driver_result


DEFAULT_ITERATIONS = 100_000
DEFAULT_ROUNDS = 9
DEFAULT_SEED = 20261017
PROCESS_TIMEOUT_SECONDS = 30
NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\Z")


class BenchmarkError(RuntimeError):
    """An executable or its reported result failed closed validation."""


def parse_executable(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path or not NAME_PATTERN.fullmatch(name):
        raise argparse.ArgumentTypeError("expected NAME=PATH with a simple name")
    return name, Path(path).expanduser().resolve()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _executable_hash(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as executable:
            for chunk in iter(lambda: executable.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as error:
        raise BenchmarkError(f"cannot read executable {path}: {error}") from error


def _run_one(name: str, path: Path, iterations: int, phase: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(path), str(iterations)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BenchmarkError(f"{name} {phase} execution failed") from error
    if completed.returncode != 0:
        raise BenchmarkError(
            f"{name} {phase} execution exited with status {completed.returncode}"
        )
    result = _read_driver_result(completed.stdout, iterations)
    if result is None:
        raise BenchmarkError(f"{name} {phase} returned an invalid driver result")
    return result


def run_benchmark(
    executables: list[tuple[str, Path]],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    rounds: int = DEFAULT_ROUNDS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Warm once, validate, then collect seeded interleaved measurements."""
    if iterations <= 0 or rounds <= 0:
        raise BenchmarkError("iterations and rounds must be positive")
    names = [name for name, _path in executables]
    if len(names) != len(set(names)):
        raise BenchmarkError("executable names must be unique")
    if "stock" not in names:
        raise BenchmarkError("an executable named 'stock' is required")
    if not executables:
        raise BenchmarkError("at least one executable is required")

    paths = dict(executables)
    for name, path in executables:
        if not path.is_file():
            raise BenchmarkError(f"{name} executable is not a file: {path}")

    hashes = {name: _executable_hash(path) for name, path in executables}
    warmup_order = ["stock", *(name for name in names if name != "stock")]
    warmups: dict[str, dict[str, Any]] = {}
    stock_result: dict[str, Any] | None = None
    for name in warmup_order:
        result = _run_one(name, paths[name], iterations, "warm-up")
        if name == "stock":
            stock_result = result
        elif stock_result is None or result["checksum"] != stock_result["checksum"]:
            raise BenchmarkError(f"{name} warm-up checksum does not match stock")
        warmups[name] = {
            "elapsed_ns": result["elapsed_ns"],
            "checksum": result["checksum"],
        }

    assert stock_result is not None
    stock_checksum = stock_result["checksum"]
    rng = random.Random(seed)
    round_records: list[dict[str, Any]] = []
    samples: dict[str, list[int]] = {name: [] for name in names}
    for round_number in range(1, rounds + 1):
        order = list(names)
        rng.shuffle(order)
        measurements: dict[str, dict[str, Any]] = {}
        for name in order:
            result = _run_one(name, paths[name], iterations, f"round {round_number}")
            if result["checksum"] != stock_checksum:
                raise BenchmarkError(
                    f"{name} round {round_number} checksum does not match stock warm-up"
                )
            elapsed_ns = result["elapsed_ns"]
            samples[name].append(elapsed_ns)
            measurements[name] = {"elapsed_ns": elapsed_ns}
        round_records.append(
            {"round": round_number, "order": order, "measurements": measurements}
        )

    summaries = {
        name: {
            "samples_ns": values,
            "median_ns": median(values),
            "min_ns": min(values),
            "max_ns": max(values),
        }
        for name, values in samples.items()
    }
    return {
        "schema_version": 1,
        "status": "measured",
        "scope": "one synthetic native corpus; measurements are not generalized",
        "optimization_claim": False,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "seed": seed,
        "iterations": iterations,
        "rounds": rounds,
        "timeout_seconds": PROCESS_TIMEOUT_SECONDS,
        "stock_checksum": stock_checksum,
        "executables": {
            name: {"path": str(path), "sha256": hashes[name]}
            for name, path in executables
        },
        "warmup_order": warmup_order,
        "warmups": warmups,
        "round_records": round_records,
        "summaries": summaries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executable",
        action="append",
        type=parse_executable,
        required=True,
        metavar="NAME=PATH",
        help="executable to measure; include a stock executable",
    )
    parser.add_argument("--iterations", type=_positive_int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--rounds", type=_positive_int, default=DEFAULT_ROUNDS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(
            args.executable,
            iterations=args.iterations,
            rounds=args.rounds,
            seed=args.seed,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (BenchmarkError, OSError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 2
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
