from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import loop_corpus


def _compiler() -> list[str]:
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler or shutil.which(compiler[0]) is None:
        pytest.skip("a system C compiler is required to exercise generated workloads")
    return compiler


@pytest.mark.parametrize("family", loop_corpus.FAMILIES)
def test_generated_loop_matches_scalar_reference_and_emits_stable_json(
    tmp_path: Path, family: str
) -> None:
    generated = loop_corpus.generate_case(tmp_path, family, 257)
    kernel_path = generated["kernel"]
    driver_path = generated["driver"]
    assert isinstance(kernel_path, Path)
    assert isinstance(driver_path, Path)

    kernel = kernel_path.read_text(encoding="utf-8")
    target_loop = "    // VECTOR_LOOP\n    for (size_t i ="
    assert kernel.count("// VECTOR_LOOP") == 1
    assert target_loop in kernel
    assert kernel.count("    for (size_t i =") == 1
    features = generated["features"]
    assert isinstance(features, dict)
    assert features["trip_count"] == (255 if family == "stencil" else 257)
    assert all(type(value) is int for value in features.values())
    assert not {"family", "elapsed_ns", "timing"}.intersection(features)

    executable = tmp_path / f"{family}-loop"
    subprocess.run(
        [
            *_compiler(),
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(kernel_path),
            str(driver_path),
            "-o",
            str(executable),
        ],
        check=True,
    )

    first = json.loads(subprocess.check_output([str(executable), "3"], text=True))
    second = json.loads(subprocess.check_output([str(executable), "3"], text=True))
    assert first["correct"] is True
    assert first["iterations"] == 3
    assert re.fullmatch(r"[0-9a-f]{16}", first["checksum"])
    assert first["checksum"] == second["checksum"]
    assert first["elapsed_ns"] >= 0
    assert set(first) == {"correct", "iterations", "checksum", "elapsed_ns"}

    for invalid_args in ([], ["0"], ["-1"], ["invalid"], ["1", "2"]):
        result = subprocess.run(
            [str(executable), *invalid_args], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert "usage:" in result.stderr


def test_generator_is_deterministic_and_reports_distinct_loop_features(
    tmp_path: Path,
) -> None:
    generated = {
        family: loop_corpus.generate_case(tmp_path / family, family, 257)
        for family in loop_corpus.FAMILIES
    }
    sources = {
        family: paths["kernel"].read_text(encoding="utf-8")
        for family, paths in generated.items()
    }
    assert len(set(sources.values())) == len(loop_corpus.FAMILIES)
    feature_sets = {
        tuple(sorted(paths["features"].items())) for paths in generated.values()
    }
    assert len(feature_sets) == len(loop_corpus.FAMILIES)
    hash_features = generated["hash64"]["features"]
    assert hash_features["element_bits"] == 32
    assert hash_features["arithmetic_bits"] == 64

    again = loop_corpus.generate_case(tmp_path / "affine", "affine", 257)
    assert again["kernel"].read_text(encoding="utf-8") == sources["affine"]
    assert again["driver"].read_text(encoding="utf-8") == generated["affine"][
        "driver"
    ].read_text(encoding="utf-8")


def test_driver_rejects_a_corrupted_kernel(tmp_path: Path) -> None:
    generated = loop_corpus.generate_case(tmp_path, "affine", 257)
    kernel_path = generated["kernel"]
    driver_path = generated["driver"]
    assert isinstance(kernel_path, Path)
    assert isinstance(driver_path, Path)
    kernel = kernel_path.read_text(encoding="utf-8")
    correct_expression = (
        "out[i] = left * UINT32_C(3) + right + (uint32_t)salt;"
    )
    assert kernel.count(correct_expression) == 1
    kernel_path.write_text(
        kernel.replace(
            correct_expression,
            "out[i] = left * UINT32_C(3) + right + (uint32_t)salt + UINT32_C(1);",
        ),
        encoding="utf-8",
    )

    executable = tmp_path / "corrupted-loop"
    subprocess.run(
        [
            *_compiler(),
            "-std=c11",
            "-O0",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(kernel_path),
            str(driver_path),
            "-o",
            str(executable),
        ],
        check=True,
    )
    result = subprocess.run(
        [str(executable), "1"], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "kernel mismatch" in result.stderr


@pytest.mark.parametrize(
    ("family", "n"),
    [
        ("unknown", 257),
        ("affine", 7),
        ("affine", 32769),
        ("affine", True),
        ("affine", 8.0),
    ],
)
def test_generator_rejects_invalid_parameters(
    tmp_path: Path, family: str, n: int
) -> None:
    with pytest.raises(ValueError):
        loop_corpus.generate_case(tmp_path, family, n)
