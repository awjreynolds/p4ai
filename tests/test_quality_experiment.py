import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import quality_experiment as quality  # noqa: E402


def test_simple_policies_use_menu_alignment_not_raw_column_ids():
    packet = {
        "actions": [{"id": "7"}, {"id": "32"}],
        "features": {"use_def_density": [4, 1]},
    }
    choices = quality.simple_choices(packet, "seed")
    assert choices["first"] == 7
    assert choices["last"] == choices["density"] == 32
    assert choices["uniform"] in (7, 32)
    assert quality.candidate_features(packet, "32") == {"use_def_density": 1.0}


def macho_object(code, ignored_header=0):
    data = bytearray(32 + 72 + 80 + len(code))
    struct.pack_into("<I", data, 0, 0xFEEDFACF)
    struct.pack_into("<I", data, 12, ignored_header)
    struct.pack_into("<I", data, 16, 1)
    struct.pack_into("<II", data, 32, 0x19, 152)
    struct.pack_into("<I", data, 96, 1)
    data[104:110] = b"__text"
    struct.pack_into("<QI", data, 144, len(code), 184)
    data[184:] = code
    return data


def test_code_identity_shares_equal_code_but_not_changed_code(tmp_path):
    a, b, c = [tmp_path / name for name in ("a.o", "b.o", "c.o")]
    a.write_bytes(macho_object(b"code"))
    b.write_bytes(macho_object(b"code", 123))
    c.write_bytes(macho_object(b"Code"))
    assert quality.code_identity(a) == quality.code_identity(b)
    assert quality.code_identity(a) != quality.code_identity(c)
    c.write_bytes(b"not an object")
    with pytest.raises(ValueError, match="Mach-O"):
        quality.code_identity(c)


def test_provider_answers_reused_for_identical_packet(tmp_path, monkeypatch):
    calls = []

    def ask(self, name, choose, packet, actions):
        calls.append(name)
        return {"chosen_action": "1", "provider": name, "confidence": 0.2}

    monkeypatch.setattr(quality.experiment.OneInterventionPolicy, "_ask", ask)
    packet = {"task": "choose", "actions": [{"id": "1"}], "features": {"x": [3]}}
    path = tmp_path / "providers.json"
    first = quality.query_models(packet, path)
    assert quality.query_models(packet, path) == first
    assert calls == ["jev", "codex"]
    packet["features"]["x"] = [4]
    with pytest.raises(ValueError, match="changed"):
        quality.query_models(packet, path)


def test_reconstructed_cascade_uses_paired_samples_and_counts_equal_actions():
    state = {
        "family": "heldout",
        "id": "test",
        "responses": {"jev": {"confidence": 0.1}},
        "timing_labels": {"jev": "a", "codex": "a"},
        "benchmark": {
            "round_records": [{"round": 1, "measurements": {"a": {"elapsed_ns": 99}}}]
        },
    }
    result = quality.policy_rounds(state, {"mode": "always"})
    assert result["escalated"] is True
    measurements = result["round_records"][0]["measurements"]
    assert measurements["jev"] == measurements["codex"] == measurements["cascade"]


def test_partial_failure_preserves_successful_provider_response(tmp_path, monkeypatch):
    calls = []

    def ask(self, name, choose, packet, actions):
        calls.append(name)
        self.provider_attempts.append({"provider": name})
        if calls == ["jev", "codex"]:
            return None
        return {"chosen_action": "1", "provider": name, "confidence": 0.2}

    monkeypatch.setattr(quality.experiment.OneInterventionPolicy, "_ask", ask)
    packet = {"task": "choose", "actions": [{"id": "1"}], "features": {"x": [3]}}
    path = tmp_path / "providers.json"
    with pytest.raises(RuntimeError):
        quality.query_models(packet, path)
    response = quality.query_models(packet, path)
    assert calls == ["jev", "codex", "codex"]
    assert len(response["attempts"]) == 3
    assert set(response["responses"]) == {"jev", "codex"}


def test_iteration_calibration_is_preserved_on_resume(tmp_path, monkeypatch):
    executable = tmp_path / "stock"
    executable.write_bytes(b"fixed binary")
    calls = []

    def sample(*args):
        calls.append(args)
        return {"elapsed_ns": 10000000}

    monkeypatch.setattr(quality.benchmark, "_run_one", sample)
    assert quality.case_iterations(tmp_path, executable, 100) == 100000
    assert quality.case_iterations(tmp_path, executable, 100) == 100000
    assert len(calls) == 1
    executable.write_bytes(b"changed binary")
    with pytest.raises(ValueError, match="identity changed"):
        quality.case_iterations(tmp_path, executable, 100)
