from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import quality_corpus  # noqa: E402

CASES = [
    ("polynomial", 3, 1),
    ("rotate_xor", 17, 3),
    ("cross_lane", 40, 1),
    ("dot_product", 32, 4),
    ("masked_recurrence", 48, 2),
    ("alternating_inputs", 64, 4),
]


def _compiler() -> list[str]:
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler or shutil.which(compiler[0]) is None:
        pytest.skip("a system C compiler is required to exercise generated workloads")
    return compiler


@pytest.mark.parametrize(("family", "lanes", "rounds"), CASES)
def test_generated_workload_matches_reference_and_emits_stable_json(
    tmp_path: Path, family: str, lanes: int, rounds: int
) -> None:
    paths = quality_corpus.generate_case(tmp_path, family, lanes, rounds)
    executable = tmp_path / f"{family}-workload"
    subprocess.run(
        [
            *_compiler(),
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(paths["kernel"]),
            str(paths["driver"]),
            "-o",
            str(executable),
        ],
        check=True,
        cwd=ROOT,
    )

    first = json.loads(subprocess.check_output([str(executable), "3"], text=True))
    second = json.loads(subprocess.check_output([str(executable), "3"], text=True))
    assert first["correct"] is True
    assert first["iterations"] == 3
    assert re.fullmatch(r"[0-9a-f]{16}", first["checksum"])
    assert first["checksum"] == second["checksum"]
    assert first["elapsed_ns"] >= 0
    assert set(first) == {"correct", "iterations", "checksum", "elapsed_ns"}

    for invalid_args in (["0"], ["-1"], ["invalid"], ["1", "2"]):
        result = subprocess.run(
            [str(executable), *invalid_args], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert "usage:" in result.stderr


def test_generator_is_deterministic_and_families_have_distinct_kernels(
    tmp_path: Path,
) -> None:
    generated = {
        family: quality_corpus.generate_case(tmp_path, family, 24, 4)
        for family in quality_corpus.FAMILIES
    }
    sources = {
        family: paths["kernel"].read_text(encoding="utf-8")
        for family, paths in generated.items()
    }
    assert len(set(sources.values())) == len(quality_corpus.FAMILIES)
    repeated = quality_corpus.generate_case(tmp_path, "polynomial", 24, 4)
    assert repeated["kernel"].read_text(encoding="utf-8") == sources["polynomial"]
    assert repeated["driver"].read_text(encoding="utf-8") == generated["polynomial"][
        "driver"
    ].read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("family", "lanes", "rounds"),
    [
        ("unknown", 24, 4),
        ("polynomial", 0, 4),
        ("polynomial", True, 4),
        ("polynomial", 65, 4),
        ("polynomial", 24, 0),
        ("polynomial", 24, 65),
    ],
)
def test_generator_rejects_invalid_workload_parameters(
    tmp_path: Path, family: str, lanes: int, rounds: int
) -> None:
    with pytest.raises(ValueError):
        quality_corpus.generate_case(tmp_path, family, lanes, rounds)
