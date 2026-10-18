"""Bounded current-SQLite adapter checks; no compiler or database is run."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from scripts import current_sqlite_feasibility as current
from scripts import sqlite_feasibility as historical

DESIGN = Path(__file__).resolve().parents[1] / "experiments" / "current-sqlite-feasibility-v1.json"


def _mini_archive(tmp_path: Path, *, symlink_shell: bool = False) -> tuple[Path, dict[str, object], dict[str, bytes]]:
    source_id = current.EXPECTED_SOURCE_ID.encode()
    files = {
        "VERSION": b"3.54.1\n",
        "sqlite3.c": b"/* The author disclaims copyright */\n" + source_id,
        "sqlite3.h": b"/* The author disclaims copyright */\n" + source_id,
        "shell.c": b"/* The author disclaims copyright */\n",
        "README.txt": b"package note",
        "notices/autosetup-LICENSE": b"retained notice",
    }
    archive_path = tmp_path / "sqlite-mini.tar.gz"
    members = {}
    with tarfile.open(archive_path, "w:gz") as archive:
        for relative, data in files.items():
            member_name = f"sqlite-autoconf-3540100/{relative.removeprefix('notices/')}"
            if relative == "notices/autosetup-LICENSE":
                member_name = "sqlite-autoconf-3540100/autosetup/LICENSE"
            members[relative] = {"member": member_name, "sha3_256": hashlib.sha3_256(data).hexdigest()}
            info = tarfile.TarInfo(member_name)
            info.size = len(data)
            if relative == "shell.c" and symlink_shell:
                info.type = tarfile.SYMTYPE
                info.linkname = "sqlite3.c"
                info.size = 0
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(data))
        extra = tarfile.TarInfo("sqlite-autoconf-3540100/configure")
        extra_data = b"must never be extracted or executed"
        extra.size = len(extra_data)
        archive.addfile(extra, io.BytesIO(extra_data))
    design = {
        "source": {
            "archive_sha3_256": hashlib.sha3_256(archive_path.read_bytes()).hexdigest(),
            "members": members,
            "version": "3.54.1",
            "source_id": current.EXPECTED_SOURCE_ID,
            "source_id_is_required_in": ["sqlite3.c", "sqlite3.h"],
        }
    }
    return archive_path, design, files


def test_design_pins_current_source_history_and_exact_workload() -> None:
    design = current._design(DESIGN)

    assert design["source"]["archive_sha3_256"] == current.EXPECTED_ARCHIVE_SHA3
    assert design["source"]["source_id"] == current.EXPECTED_SOURCE_ID
    assert design["historical_evidence"]["require_baseline_variants"] == ["pinned_llvm", "system_apple"]
    assert design["build"]["execute_configure"] is False
    assert design["verification"]["preserve_exact_output"] is True
    assert len(design["workload"]["input_files"]) == 17


def test_archive_extraction_is_digest_checked_and_allowlisted(tmp_path: Path) -> None:
    archive, design, expected = _mini_archive(tmp_path)
    stage = tmp_path / "stage" / "source"

    manifest = current._stage_verified_archive(archive, stage, design)

    assert manifest["version"] == "3.54.1"
    assert manifest["source_id"] == current.EXPECTED_SOURCE_ID
    assert set(manifest["files"]) == set(expected)
    assert {path.name for path in stage.iterdir()} == {"VERSION", "sqlite3.c", "sqlite3.h", "shell.c", "README.txt", "notices", "source-manifest.json"}
    assert not (stage / "configure").exists()
    assert (stage / "notices" / "autosetup-LICENSE").read_bytes() == expected["notices/autosetup-LICENSE"]


def test_archive_digest_mismatch_fails_before_creating_stage(tmp_path: Path) -> None:
    archive, design, _ = _mini_archive(tmp_path)
    design["source"]["archive_sha3_256"] = "0" * 64
    stage = tmp_path / "stage"

    with pytest.raises(current.CurrentSQLiteError, match="archive SHA3-256 mismatch"):
        current._stage_verified_archive(archive, stage, design)

    assert not stage.exists()


def test_nonregular_source_members_are_rejected(tmp_path: Path) -> None:
    archive, design, _ = _mini_archive(tmp_path, symlink_shell=True)

    with pytest.raises(current.CurrentSQLiteError, match="not a regular file"):
        current._stage_verified_archive(archive, tmp_path / "stage", design)


def test_explicit_historical_report_is_hashed_and_needs_new_output(tmp_path: Path) -> None:
    design = current._design(DESIGN)
    report = tmp_path / "historical-report.json"
    report.write_bytes(b'{"status":"passed"}\n')
    output = tmp_path / "new-output"

    selected, digest, effective = current._select_historical_report(design, report, output)

    assert selected == report.resolve()
    assert digest == hashlib.sha256(report.read_bytes()).hexdigest()
    assert effective["historical_evidence"]["report"] == str(report.resolve())
    assert effective["historical_evidence"]["report_sha256"] == digest
    assert effective["historical_evidence"]["report_selection"]["mode"] == "explicit_override"
    with pytest.raises(current.CurrentSQLiteError, match="new --output"):
        current._select_historical_report(design, report, current.DEFAULT_OUTPUT)
    output.mkdir()
    (output / "unrelated.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(current.CurrentSQLiteError, match="new, empty"):
        current._select_historical_report(design, report, output)


def test_prepare_records_explicit_historical_report_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = tmp_path / "replacement-report.json"
    report.write_bytes(b"replacement report fixture\n")
    forwarded: dict[str, object] = {}

    def fake_stage(archive_path: Path, source_dir: Path, design: dict[str, object]) -> dict[str, object]:
        return {"archive_path": str(archive_path), "source_dir": str(source_dir)}

    def fake_evidence(
        design: dict[str, object], output: Path, report_path: Path, expected_hash: str, *, report_override: bool
    ) -> dict[str, object]:
        forwarded.update(path=report_path, digest=expected_hash, override=report_override)
        return {"report_path": str(report_path), "report_sha256": expected_hash}

    monkeypatch.setattr(current, "_stage_verified_archive", fake_stage)
    monkeypatch.setattr(current, "_historical_evidence", fake_evidence)
    prepared = current.prepare(
        archive_path=tmp_path / "unused-archive",
        source_dir=tmp_path / "source",
        historical_report=report,
        output=tmp_path / "fresh-output",
    )

    selection = prepared["effective_design"]["historical_evidence"]["report_selection"]
    assert selection == {
        "mode": "explicit_override",
        "path": str(report.resolve()),
        "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
    }
    assert prepared["effective_design_sha256"] == current._fingerprint(prepared["effective_design"])
    assert prepared["historical_evidence"]["report_sha256"] == selection["sha256"]
    assert forwarded == {"path": report.resolve(), "digest": selection["sha256"], "override": True}


def test_native_helper_hash_validation_respects_override_mode(tmp_path: Path) -> None:
    artifacts = {}
    for name in ("timeit_target", "fpcmp_target"):
        path = tmp_path / name
        path.write_text(f"{name}\n", encoding="utf-8")
        artifacts[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    variant = {"artifacts": artifacts}

    result = current._verified_native_helpers(variant, explicit_override=True)
    assert set(result) == {"timeit_target", "fpcmp_target"}
    with pytest.raises(current.CurrentSQLiteError, match="pinned hash"):
        current._verified_native_helpers(variant, explicit_override=False)


def test_override_helper_hashes_are_taken_from_selected_report() -> None:
    replacement_hashes = {
        "timeit_target": {"sha256": "a" * 64},
        "fpcmp_target": {"sha256": "b" * 64},
    }

    current._check_helper_pins(replacement_hashes, explicit_override=True)
    with pytest.raises(current.CurrentSQLiteError, match="pinned hash"):
        current._check_helper_pins(replacement_hashes, explicit_override=False)


def test_frozen_input_symlink_is_rejected_before_following_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    link = tmp_path / "test1.sql"
    link.symlink_to(outside)

    with pytest.raises(current.CurrentSQLiteError, match="must not be a symlink"):
        current._freeze_input(link, b"would escape", "test1.sql")

    assert not outside.exists()


def test_source_staging_root_symlink_is_rejected_before_resolution(tmp_path: Path) -> None:
    source = tmp_path / "real-source"
    source.mkdir()
    link = tmp_path / "source-link"
    link.symlink_to(source, target_is_directory=True)

    with pytest.raises(current.CurrentSQLiteError, match="source staging path must not be a symlink"):
        current.prepare(source_dir=link, output=tmp_path / "out")


def test_compile_adapter_changes_only_compiler_source_and_object_paths() -> None:
    old = [
        "/old/clang", "--target=arm64-apple-darwin", "-DNDEBUG", "-O3", "-arch", "arm64",
        "-isysroot", "/SDK", "-mmacosx-version-min=14.0", "-DSQLITE_THREADSAFE=0",
        "-o", "old.o", "-c", "/old/sqlite3.c",
    ]

    result = current._compile_argv(
        old,
        compiler=Path("/new/clang"),
        source=Path("/new/sqlite3.c"),
        output=Path("/build/sqlite3.o"),
        sanitizer_flags=["-fsanitize=address,undefined", "-fno-omit-frame-pointer"],
    )

    assert result[:9] == [
        "/new/clang", "--target=arm64-apple-darwin", "-DNDEBUG", "-O3", "-arch", "arm64",
        "-isysroot", "/SDK", "-mmacosx-version-min=14.0",
    ]
    assert result[-4:] == ["-c", "/new/sqlite3.c", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    assert result[result.index("-o") + 1] == "/build/sqlite3.o"


def test_compile_signature_normalizes_input_paths_but_detects_flag_changes() -> None:
    core = ["clang", "-O3", "-o", "core.o", "-c", "sqlite3.c"]
    shell = ["clang", "-O3", "-o", "shell.o", "-c", "shell.c"]
    changed = ["clang", "-O2", "-o", "shell.o", "-c", "shell.c"]

    assert current._normalized_compile_signature(core) == current._normalized_compile_signature(shell)
    assert current._normalized_compile_signature(core) != current._normalized_compile_signature(changed)


def test_correctness_requires_runner_exit_application_exit_and_reference_match() -> None:
    run = {"return_code": 0, "target_exit_code": 0}
    assert historical._reference_test_passed(run, {"return_code": 0})
    assert not historical._reference_test_passed(run, {"return_code": 1})
    assert not historical._reference_test_passed({"return_code": 0, "target_exit_code": 134}, {"return_code": 0})
    assert current._exit_code("validate", {"qualified_for_repair": True}) == 0
    assert current._exit_code("validate", {"status": "passed", "qualified_for_repair": False}) == 1
    assert current._exit_code("prepare", {"status": "prepared"}) == 0

