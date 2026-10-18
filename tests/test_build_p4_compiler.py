import hashlib
import io
import tarfile

import pytest

from scripts import build_p4_compiler as build


def test_pinned_source_is_verified_and_edits_are_rejected(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar.gz"
    root = f"p4c-{build.SOURCE_COMMIT}"
    with tarfile.open(archive, "w:gz") as contents:
        member = tarfile.TarInfo(f"{root}/source.cpp")
        member.size = 8
        contents.addfile(member, io.BytesIO(b"original"))
    monkeypatch.setattr(
        build, "SOURCE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    build.ensure_source(archive, tmp_path)
    assert (tmp_path / root / "source.cpp").read_bytes() == b"original"
    build.ensure_source(archive, tmp_path)
    (tmp_path / root / "source.cpp").write_text("modified")
    with pytest.raises(ValueError, match="Edited pinned source"):
        build.ensure_source(archive, tmp_path)


def test_archive_hash_rejected_before_extraction(tmp_path):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"wrong archive")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        build.ensure_source(archive, tmp_path)
    assert list(tmp_path.iterdir()) == [archive]


def test_archive_root_must_match_pin(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as contents:
        contents.addfile(tarfile.TarInfo("unrelated/file"), io.BytesIO())
    monkeypatch.setattr(
        build, "SOURCE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="Unexpected archive source path"):
        build.ensure_source(archive, tmp_path)
    assert not (tmp_path / "unrelated").exists()
