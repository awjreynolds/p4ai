"""Pure contract tests for the SQLite substrate gate; no compiler or app runs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import sqlite_feasibility as feasibility

DESIGN = Path(__file__).resolve().parents[1] / "experiments" / "sqlite-feasibility-v1.json"


def test_design_pins_upstream_small_correctness_contract() -> None:
    design = feasibility._design(DESIGN)

    assert design["source"]["revision"] == feasibility.PINNED_REVISION
    assert design["build"]["small_problem_size"] is True
    assert design["build"]["release_cache"] == "cmake/caches/O3.cmake"
    assert design["build"]["optimization_profile"] == "upstream O3 without LTO"
    deviation = design["build"]["profile_deviation"]
    assert "-flto" in deviation
    assert "-fomit-frame-pointer" in deviation
    assert design["verification"]["reference_output"] == "sqlite3.reference_output.small"
    assert design["verification"]["comparator"] == "upstream fpcmp-target"
    assert design["sanitizers"]["run_same_small_workload"] is True
    assert design["scope"].endswith("no repair interventions or performance timing")


def test_git_blob_verification_retains_source_and_notice_bytes(tmp_path: Path) -> None:
    path = tmp_path / "copyright-release.html"
    content = b"SQLite copyright release notice\n"
    path.write_bytes(content)
    entry = {
        "path": path.name,
        "mode": "100644",
        "git_blob": feasibility._git_blob_oid(content),
    }

    assert feasibility._read_tracked_file(tmp_path, entry) == content
    with pytest.raises(feasibility.FeasibilityError, match="differs from the pinned Git blob"):
        path.write_bytes(b"replaced license text\n")
        feasibility._read_tracked_file(tmp_path, entry)


def test_generated_small_input_contract_requires_exactly_15_files(tmp_path: Path) -> None:
    app_build = tmp_path / feasibility.SUBTREE
    app_build.mkdir(parents=True)
    for name in feasibility.GENERATED_INPUTS:
        (app_build / name).write_text(f"-- {name}\n", encoding="utf-8")

    assert feasibility._generated_input_records(tmp_path)["status"] == "passed"
    (app_build / "test7.sql").unlink()
    assert feasibility._generated_input_records(tmp_path)["missing"] == ["test7.sql"]
    (app_build / "test7.sql").write_text("-- restored\n", encoding="utf-8")
    (app_build / "test16.sql").write_text("-- unexpected\n", encoding="utf-8")
    assert feasibility._generated_input_records(tmp_path)["unexpected"] == ["test16.sql"]


def test_generated_sql_inputs_must_match_across_compiler_variants() -> None:
    files = [
        {"path": name, "sha256": f"hash-{name}"}
        for name in feasibility.GENERATED_INPUTS
    ]
    variants = [
        {"name": "pinned", "generated_inputs": {"status": "passed", "files": files}},
        {"name": "apple", "generated_inputs": {"status": "passed", "files": list(files)}},
    ]

    assert feasibility._compare_generated_inputs(variants)["status"] == "passed"
    variants[1]["generated_inputs"]["files"][4] = {
        "path": "test5.sql",
        "sha256": "different-workload",
    }
    comparison = feasibility._compare_generated_inputs(variants)
    assert comparison["status"] == "failed"
    assert comparison["errors"][0]["path"] == "test5.sql"


def test_source_generator_is_hashed_but_not_expected_as_copied_build_input(tmp_path: Path) -> None:
    source_app = tmp_path / "source" / feasibility.SUBTREE
    build_app = tmp_path / "build" / feasibility.SUBTREE
    source_app.mkdir(parents=True)
    build_app.mkdir(parents=True)
    for name in feasibility.STATIC_INPUTS:
        payload = f"{name}\n"
        (source_app / name).write_text(payload, encoding="utf-8")
        (build_app / name).write_text(payload, encoding="utf-8")
    (source_app / "smalltest.tcl").write_text("source generator", encoding="utf-8")

    assert feasibility._copied_input_records(tmp_path / "source", tmp_path / "build")["status"] == "passed"


def test_compile_commands_require_shared_o3_target_and_forbid_lto(tmp_path: Path) -> None:
    design = feasibility._design(DESIGN)
    tools = {"sdk": "/SDK/MacOSX.sdk"}
    definitions = " ".join(f"-D{item}" for item in design["build"]["definitions_from_upstream_cmake"])
    compile_command = (
        "/tools/clang -O3 -DNDEBUG -w -Werror=date-time -I. "
        "--target=arm64-apple-darwin -arch arm64 -mmacosx-version-min=14.0 "
        "-isysroot /SDK/MacOSX.sdk "
        f"{definitions} -c sqlite3.c"
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps([
            {"file": "/src/sqlite3.c", "directory": "/build", "command": compile_command},
            {"file": "/src/shell.c", "directory": "/build", "command": compile_command.replace("sqlite3.c", "shell.c")},
        ]),
        encoding="utf-8",
    )

    assert feasibility._compile_command_records(tmp_path, design, tools)["status"] == "passed"
    compile_db.write_text(compile_db.read_text(encoding="utf-8").replace("-O3", "-O3 -flto"), encoding="utf-8")
    assert feasibility._compile_command_records(tmp_path, design, tools)["status"] == "failed"


def test_upstream_test_file_must_run_original_small_reference_and_tolerance(tmp_path: Path) -> None:
    design = feasibility._design(DESIGN)
    test_file = tmp_path / "sqlite3.test"
    test_file.write_text(
        "RUN: cd %S ; %b/sqlite3 -init sqlite3rc :memory: < commands\n"
        "VERIFY: %b/fpcmp-target -r 1.0e-9 %o %S/sqlite3.reference_output.small\n",
        encoding="utf-8",
    )

    assert feasibility._verify_upstream_test(test_file, design)["status"] == "passed"
    test_file.write_text(
        "RUN: cd %S ; %b/sqlite3 -init sqlite3rc :memory: < commands\n"
        "VERIFY: %b/fpcmp-target -r 1.0e-8 %o %S/sqlite3.reference_output.small\n",
        encoding="utf-8",
    )
    result = feasibility._verify_upstream_test(test_file, design)
    assert result["status"] == "failed"
    assert result["missing_lines"] == [result["expected_lines"][1]]


def test_both_compilers_receive_identical_native_cmake_contract(tmp_path: Path) -> None:
    design = feasibility._design(DESIGN)
    tools = {
        "cmake": "/tools/cmake",
        "ninja": "/tools/ninja",
        "lit": "/tools/llvm-lit",
        "sdk": "/SDK/MacOSX.sdk",
        "linker": "/tools/ld",
        "host_cc": "/tools/apple-clang",
        "host_cxx": "/tools/apple-clang++",
    }
    source = tmp_path / "source"
    common = {
        "design": design,
        "source": source,
        "tools": tools,
        "sanitizer": False,
    }
    pinned = feasibility._cmake_command(
        **common,
        build_dir=tmp_path / "pinned",
        compiler=Path("/tools/pinned-clang"),
    )
    system = feasibility._cmake_command(
        **common,
        build_dir=tmp_path / "system",
        compiler=Path("/tools/apple-clang"),
    )
    assert f"-DCMAKE_C_COMPILER_TARGET={design['target']['triple']}" in pinned
    assert f"-DCMAKE_OSX_ARCHITECTURES={design['target']['architecture']}" in pinned
    assert f"-DCMAKE_OSX_SYSROOT={tools['sdk']}" in pinned
    assert f"-DCMAKE_LINKER={tools['linker']}" in pinned
    assert "-C" in pinned
    assert str(source / design["build"]["release_cache"]) in pinned
    assert "-DSMALL_PROBLEM_SIZE=ON" in pinned
    pinned_common = [arg for arg in pinned if not arg.startswith("-DCMAKE_C_COMPILER=") and arg != str(tmp_path / "pinned")]
    system_common = [arg for arg in system if not arg.startswith("-DCMAKE_C_COMPILER=") and arg != str(tmp_path / "system")]
    assert pinned_common == system_common


def test_optional_sanitizer_variant_adds_flags_without_changing_source_contract(tmp_path: Path) -> None:
    design = feasibility._design(DESIGN)
    tools = {
        "cmake": "/tools/cmake",
        "ninja": "/tools/ninja",
        "lit": "/tools/llvm-lit",
        "sdk": "/SDK/MacOSX.sdk",
        "linker": "/tools/ld",
        "host_cc": "/tools/apple-clang",
        "host_cxx": "/tools/apple-clang++",
    }
    command = feasibility._cmake_command(
        design,
        source=tmp_path / "source",
        build_dir=tmp_path / "sanitized",
        compiler=Path("/tools/pinned-clang"),
        tools=tools,
        sanitizer=True,
    )

    assert "-DCMAKE_C_FLAGS=-fsanitize=address,undefined -fno-omit-frame-pointer" in command
    assert "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined" in command
    assert "-DSMALL_PROBLEM_SIZE=ON" in command

