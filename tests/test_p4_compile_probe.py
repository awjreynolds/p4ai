import json

from scripts.p4_compile_probe import fit_evidence


def test_fit_requires_success_manifest_and_nonempty_binary(tmp_path):
    assert not fit_evidence(tmp_path, 0)["compiler_reported_fit"]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"compilation_succeeded": True}))
    binary = tmp_path / "tofino.bin"
    binary.write_bytes(b"")
    assert not fit_evidence(tmp_path, 0)["compiler_reported_fit"]
    binary.write_bytes(b"fixture binary")
    assert fit_evidence(tmp_path, 0)["compiler_reported_fit"]
    assert not fit_evidence(tmp_path, 1)["compiler_reported_fit"]
    assert not fit_evidence(tmp_path, None)["compiler_reported_fit"]
    manifest.write_text(json.dumps({"compilation_succeeded": False}))
    assert not fit_evidence(tmp_path, 0)["compiler_reported_fit"]


def test_ambiguous_or_invalid_manifest_is_not_fit(tmp_path):
    (tmp_path / "tofino.bin").write_bytes(b"fixture binary")
    (tmp_path / "manifest.json").write_text("not JSON")
    assert not fit_evidence(tmp_path, 0)["compiler_reported_fit"]
    (tmp_path / "manifest.json").write_text(json.dumps({"compilation_succeeded": True}))
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "manifest.json").write_text(json.dumps({"compilation_succeeded": True}))
    assert not fit_evidence(tmp_path, 0)["compiler_reported_fit"]
