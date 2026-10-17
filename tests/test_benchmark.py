from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import benchmark  # noqa: E402, I001


CHECKSUM = "0123456789abcdef"


def _completed(argv, *, checksum=CHECKSUM, elapsed_ns=10, correct=True, iterations=4):
    payload = {
        "correct": correct,
        "iterations": iterations,
        "checksum": checksum,
        "elapsed_ns": elapsed_ns,
    }
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload).encode(), b"")


def _executables(tmp_path: Path):
    result = []
    for name in ("first", "stock", "last"):
        path = tmp_path / name
        path.write_text(f"fixture for {name}\n", encoding="utf-8")
        result.append((name, path))
    return result


def test_interleaves_seeded_rounds_after_one_warmup_per_executable(
    tmp_path, monkeypatch
):
    executables = _executables(tmp_path)
    calls = []

    def fake_run(argv, **_kwargs):
        name = Path(argv[0]).name
        calls.append(name)
        return _completed(argv, elapsed_ns=len(calls))

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    report = benchmark.run_benchmark(executables, iterations=4, rounds=3, seed=37)

    expected_orders = []
    rng = random.Random(37)
    for _ in range(3):
        order = [name for name, _path in executables]
        rng.shuffle(order)
        expected_orders.append(order)
    assert report["warmup_order"] == ["stock", "first", "last"]
    assert calls[:3] == report["warmup_order"]
    assert [record["order"] for record in report["round_records"]] == expected_orders
    assert calls[3:] == [name for order in expected_orders for name in order]
    assert report["summaries"]["stock"]["samples_ns"] == [
        report["round_records"][index]["measurements"]["stock"]["elapsed_ns"]
        for index in range(3)
    ]
    assert report["optimization_claim"] is False
    assert report["seed"] == 37


def test_checksum_mismatch_fails_closed_during_warmup(tmp_path, monkeypatch):
    executables = _executables(tmp_path)

    def fake_run(argv, **_kwargs):
        checksum = "fedcba9876543210" if Path(argv[0]).name == "first" else CHECKSUM
        return _completed(argv, checksum=checksum)

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    with pytest.raises(benchmark.BenchmarkError, match="warm-up checksum"):
        benchmark.run_benchmark(executables, iterations=4, rounds=1)


def test_checksum_mismatch_fails_closed_during_measurement(tmp_path, monkeypatch):
    executables = _executables(tmp_path)
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(Path(argv[0]).name)
        checksum = "fedcba9876543210" if len(calls) == 4 else CHECKSUM
        return _completed(argv, checksum=checksum)

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    with pytest.raises(benchmark.BenchmarkError, match="checksum does not match stock"):
        benchmark.run_benchmark(executables, iterations=4, rounds=1, seed=1)


@pytest.mark.parametrize(
    "stdout",
    [
        b"{}\n",
        b'{"correct": true}\nextra\n',
        b'{"correct":true,"iterations":5,"checksum":"0123456789abcdef","elapsed_ns":1}\n',
    ],
)
def test_invalid_driver_json_fails_closed(tmp_path, monkeypatch, stdout):
    executables = _executables(tmp_path)

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout, b"")

    monkeypatch.setattr(benchmark.subprocess, "run", fake_run)
    with pytest.raises(benchmark.BenchmarkError, match="invalid driver result"):
        benchmark.run_benchmark(executables, iterations=4, rounds=1)


def test_parse_executable_requires_name_and_path():
    assert benchmark.parse_executable("stock=/tmp/stock") == (
        "stock",
        Path("/tmp/stock").resolve(),
    )
    with pytest.raises(argparse.ArgumentTypeError):
        benchmark.parse_executable("=path")
    with pytest.raises(argparse.ArgumentTypeError):
        benchmark.parse_executable("name=")
