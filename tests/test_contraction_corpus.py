from __future__ import annotations

import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import contraction_corpus


def _compiler() -> list[str]:
    compiler = shlex.split(os.environ.get("CC", "cc"))
    if not compiler or shutil.which(compiler[0]) is None:
        pytest.skip("a system C compiler is required to exercise generated workloads")
    return compiler


def _spec(
    a_column_major: bool, b_column_major: bool, c_column_major: bool
) -> dict[str, object]:
    return {
        "m": 5,
        "n": 7,
        "k": 9,
        "a_column_major": a_column_major,
        "b_column_major": b_column_major,
        "c_column_major": c_column_major,
    }


def _padded_spec(
    a_column_major: bool, b_column_major: bool, c_column_major: bool
) -> dict[str, object]:
    return _spec(a_column_major, b_column_major, c_column_major) | {
        "a_padding": 1,
        "b_padding": 2,
        "c_padding": 3,
    }


@pytest.mark.parametrize("order", contraction_corpus.ORDERS)
@pytest.mark.parametrize("layouts", list(itertools.product((False, True), repeat=3)))
def test_all_orders_and_layouts_match_independent_reference(
    tmp_path: Path, order: str, layouts: tuple[bool, bool, bool]
) -> None:
    generated = contraction_corpus.generate_case(tmp_path, _spec(*layouts), order=order)
    kernel_path = generated["kernel"]
    driver_path = generated["driver"]
    assert isinstance(kernel_path, Path)
    assert isinstance(driver_path, Path)
    kernel = kernel_path.read_text(encoding="utf-8")
    assert kernel.count("// CONTRACTION_LOOP_ORDER:") == 1
    assert f"// CONTRACTION_LOOP_ORDER: {order}" in kernel
    first_loop = kernel.index("// CONTRACTION_LOOP_ORDER:")
    for variable in order:
        assert kernel.index(f"for (size_t {variable} =", first_loop) > first_loop
        first_loop = kernel.index(f"for (size_t {variable} =", first_loop)
    assert kernel.index("out[cell] = 0;") < kernel.index("// CONTRACTION_LOOP_ORDER:")

    features = generated["features"]
    assert isinstance(features, dict)
    assert all(type(value) is int for value in features.values())
    assert features["trip_count"] == 5 * 7 * 9
    assert features["a_i_stride"] == (1 if layouts[0] else 9)
    assert features["a_k_stride"] == (5 if layouts[0] else 1)
    assert features["b_k_stride"] == (1 if layouts[1] else 7)
    assert features["b_j_stride"] == (9 if layouts[1] else 1)
    assert features["c_i_stride"] == (1 if layouts[2] else 7)
    assert features["c_j_stride"] == (5 if layouts[2] else 1)
    assert not {"order", "family", "elapsed_ns", "timing"}.intersection(features)

    executable = tmp_path / f"contraction-{order}-{''.join(map(str, layouts))}"
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


def test_driver_rejects_a_corrupted_contraction(tmp_path: Path) -> None:
    generated = contraction_corpus.generate_case(tmp_path, _spec(False, True, False))
    kernel_path = generated["kernel"]
    driver_path = generated["driver"]
    assert isinstance(kernel_path, Path)
    assert isinstance(driver_path, Path)
    kernel = kernel_path.read_text(encoding="utf-8")
    original = "out[(size_t)((i) * 7u + (j) * 1u)] += left * right;"
    assert kernel.count(original) == 1
    kernel_path.write_text(
        kernel.replace(
            original, "out[(size_t)((i) * 7u + (j) * 1u)] += left * right + 1u;"
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "corrupted-contraction"
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
    result = subprocess.run([str(executable), "1"], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "kernel mismatch" in result.stderr


@pytest.mark.parametrize("order", contraction_corpus.ORDERS)
@pytest.mark.parametrize("layouts", list(itertools.product((False, True), repeat=3)))
def test_padded_orders_and_layouts_match_reference_and_report_physical_extents(
    tmp_path: Path, order: str, layouts: tuple[bool, bool, bool]
) -> None:
    generated = contraction_corpus.generate_case(
        tmp_path, _padded_spec(*layouts), order=order
    )
    kernel_path = generated["kernel"]
    driver_path = generated["driver"]
    assert isinstance(kernel_path, Path)
    assert isinstance(driver_path, Path)
    assert "_pad_1_2_3_" in kernel_path.name
    assert f"// CONTRACTION_LOOP_ORDER: {order}" in kernel_path.read_text(
        encoding="utf-8"
    )

    features = generated["features"]
    assert isinstance(features, dict)
    assert features["a_padding"] == 1
    assert features["b_padding"] == 2
    assert features["c_padding"] == 3
    assert features["a_leading_dimension"] == (6 if layouts[0] else 10)
    assert features["b_leading_dimension"] == (11 if layouts[1] else 9)
    assert features["c_leading_dimension"] == (8 if layouts[2] else 10)
    assert features["a_i_stride"] == (1 if layouts[0] else 10)
    assert features["a_k_stride"] == (6 if layouts[0] else 1)
    assert features["b_k_stride"] == (1 if layouts[1] else 9)
    assert features["b_j_stride"] == (11 if layouts[1] else 1)
    assert features["c_i_stride"] == (1 if layouts[2] else 10)
    assert features["c_j_stride"] == (8 if layouts[2] else 1)
    assert features["a_allocated_elements"] == (54 if layouts[0] else 50)
    assert features["b_allocated_elements"] == (77 if layouts[1] else 81)
    assert features["c_allocated_elements"] == (56 if layouts[2] else 50)

    executable = tmp_path / f"padded-{order}-{''.join(map(str, layouts))}"
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
    result = json.loads(subprocess.check_output([str(executable), "2"], text=True))
    assert result["correct"] is True
    assert result["iterations"] == 2
    assert re.fullmatch(r"[0-9a-f]{16}", result["checksum"])


def test_explicit_zero_padding_preserves_generated_sources(tmp_path: Path) -> None:
    spec = _spec(False, True, False)
    legacy = contraction_corpus.generate_case(tmp_path / "legacy", spec, "ikj")
    explicit_zero = contraction_corpus.generate_case(
        tmp_path / "explicit-zero",
        spec | {"a_padding": 0, "b_padding": 0, "c_padding": 0},
        "ikj",
    )
    assert legacy["kernel"].read_text(encoding="utf-8") == explicit_zero[
        "kernel"
    ].read_text(encoding="utf-8")
    assert legacy["driver"].read_text(encoding="utf-8") == explicit_zero[
        "driver"
    ].read_text(encoding="utf-8")


def test_reference_rejects_nonzero_padding_output(tmp_path: Path) -> None:
    spec = _spec(False, True, False) | {"c_padding": 2}
    generated = contraction_corpus.generate_case(tmp_path, spec)
    kernel_path = generated["kernel"]
    driver_path = generated["driver"]
    assert isinstance(kernel_path, Path)
    assert isinstance(driver_path, Path)
    kernel = kernel_path.read_text(encoding="utf-8")
    zeroing = "        out[cell] = 0;"
    assert kernel.count(zeroing) == 1
    kernel_path.write_text(
        kernel.replace(zeroing, zeroing + "\n        out[C_COUNT - 1u] = 1u;"),
        encoding="utf-8",
    )
    executable = tmp_path / "corrupted-padding"
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
    result = subprocess.run([str(executable), "1"], capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "kernel mismatch" in result.stderr


@pytest.mark.parametrize(
    ("spec", "order"),
    [
        (_spec(False, False, False) | {"extra": 1}, "ijk"),
        (_spec(False, False, False) | {"m": True}, "ijk"),
        (_spec(False, False, False) | {"n": 0}, "ijk"),
        (_spec(False, False, False) | {"k": 4097}, "ijk"),
        (_spec(False, False, False) | {"m": 1000, "n": 1000, "k": 1000}, "ijk"),
        (_spec(False, False, False) | {"a_column_major": 1}, "ijk"),
        ({"m": 5}, "ijk"),
        (_spec(False, False, False), "ik"),
        (_spec(False, False, False) | {"a_padding": -1}, "ijk"),
        (_spec(False, False, False) | {"b_padding": 65}, "ijk"),
        (_spec(False, False, False) | {"c_padding": True}, "ijk"),
        (_spec(False, False, False) | {"a_padding": 2.0}, "ijk"),
    ],
)
def test_generator_rejects_invalid_spec_or_order(
    tmp_path: Path, spec: dict[str, object], order: str
) -> None:
    with pytest.raises(ValueError):
        contraction_corpus.generate_case(tmp_path, spec, order=order)
