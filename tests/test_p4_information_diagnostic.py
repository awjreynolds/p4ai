import itertools
import json
import os

import pytest

from scripts import p4_information_diagnostic as diagnostic


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "ROOT", tmp_path)
    monkeypatch.setattr(diagnostic.fit, "identity", lambda _: {"image": "fixed", "compiler": {}})
    monkeypatch.setattr(diagnostic.fit, "freeze", lambda *args: None)
    monkeypatch.setattr(diagnostic.fit, "hashes", lambda paths, root: {
        os.path.relpath(p, root): diagnostic.p4_compile_probe.sha256(p) for p in sorted(paths)})
    config = {"experiment_id": "info", "cases": [], "providers": ["jev", "codex"],
              "conditions": list(diagnostic.CONDITIONS), "source_run": "old", "source_config": "old-config.json",
              "compile_timeout_seconds": 120, "image": "fixed", "common_context": "Every alternative applies afresh to default.",
              "worked_example": "Development example: tenant48 repaired by side-pack-3.",
              "schedule": [{"case": c, "provider": p, "condition": condition} for c, p, condition in
                           itertools.product(("tenant56", "tenant8"), ("jev", "codex"), diagnostic.CONDITIONS)]}
    for case in ("tenant56", "tenant8"):
        packet = {"observation": {"task": "Choose one.\n" + json.dumps({"source": "// source " + case,
                      "history": [{"policy": "default", "diagnostics": "baseline only"}]}),
                      "features": {"annotation_count": [1] * 24}, "feature_descriptions": {"annotation_count": "count"},
                      "actions": [{"id": str(i), "description": f"policy-{i}"} for i in range(24)]},
                  "action_mapping": {str(i): f"policy-{i}" for i in range(24)}}
        name = f"old/{case}-packet.json"
        diagnostic.save(tmp_path / name, packet)
        config["cases"].append({"id": case, "packet": name})
    diagnostic.save(tmp_path / "docs/results/p4-pragma-fit-qualification-v1.json", {"development": "verified"})
    diagnostic.save(tmp_path / "old/freeze.json", {"source": "frozen"})
    diagnostic.save(tmp_path / "old-config.json", {"image": "fixed", "cases": [{"id": c} for c in ("tenant56", "tenant8")]})
    path = tmp_path / "config.json"
    diagnostic.save(path, config)
    return path, tmp_path / "output", config


def test_prepare_freezes_eight_packets_differing_only_by_example(setup, monkeypatch):
    path, output, config = setup
    monkeypatch.setattr(diagnostic.jev, "choose", lambda _: pytest.fail("prepare called provider"))
    assert diagnostic.run(path, output, "prepare") == {"prepared_choices": 8}
    assert len(list((output / "packets").glob("*.json"))) == 8
    for case, provider in itertools.product(("tenant56", "tenant8"), ("jev", "codex")):
        packets = {}
        for p in (output / "packets").glob(f"*-{case}-{provider}-*.json"):
            packets[p.name.split(f"-{provider}-", 1)[1][:-5]] = diagnostic.read(p)
        base, example = packets["baseline_information"], packets["worked_example"]
        assert example["observation"]["task"] == base["observation"]["task"] + "\n\n" + config["worked_example"]
        example["observation"]["task"] = base["observation"]["task"]
        assert example == base
        assert "eligible_fit" not in json.dumps(base)
        assert "policy-17" not in base["observation"]["task"]


def test_eight_calls_independent_and_resume_does_not_repeat(setup, monkeypatch):
    path, output, _ = setup
    seen = []
    def choose(packet):
        seen.append(packet)
        return {"chosen_action": "3", "usage": {"input_tokens": 2, "output_tokens": 1}}
    monkeypatch.setattr(diagnostic.jev, "choose", choose)
    monkeypatch.setattr(diagnostic.codex_advisor, "choose", choose)
    result = diagnostic.run(path, output)
    assert result["completed_choices"] == len(seen) == 8
    assert result["reported_usage"]["input_tokens"] == 16
    assert not (output / "probes").exists()
    diagnostic.run(path, output)
    assert len(seen) == 8


def test_invalid_choices_and_provider_failure_are_charged(setup, monkeypatch):
    path, output, _ = setup
    def fail(_):
        raise diagnostic.jev.JevUnavailable("transport_error", "safe message")
    monkeypatch.setattr(diagnostic.jev, "choose", fail)
    monkeypatch.setattr(diagnostic.codex_advisor, "choose", lambda _: {"chosen_action": "99"})
    result = diagnostic.run(path, output)
    assert result["completed_choices"] == result["provider_errors"] == 8
    assert all("policy" not in diagnostic.read(p) for p in (output / "decisions").glob("*.json"))


def test_interrupted_call_never_replayed(setup, monkeypatch):
    path, output, config = setup
    diagnostic.run(path, output, "prepare")
    entry = config["schedule"][0]
    packet_path = output / "packets" / (diagnostic.choice_id(0, entry) + ".json")
    diagnostic.save(diagnostic.decision_path(output, 0, entry), {
        "entry": entry, "status": "started", "packet_sha256": diagnostic.p4_compile_probe.sha256(packet_path)})
    calls = []
    def choose(_):
        calls.append(1)
        return {"chosen_action": "0"}
    monkeypatch.setattr(diagnostic.jev, "choose", choose)
    monkeypatch.setattr(diagnostic.codex_advisor, "choose", choose)
    result = diagnostic.run(path, output)
    assert len(calls) == 7
    assert result["provider_errors"] == 1
    assert "unknown" in diagnostic.read(diagnostic.decision_path(output, 0, entry))["error"]


def test_evaluation_requires_all_choices(setup, monkeypatch):
    path, output, _ = setup
    monkeypatch.setattr(diagnostic.fit, "compile_policy", lambda *a: pytest.fail("early compile"))
    monkeypatch.setattr(diagnostic.p4_compile_probe, "compile_case", lambda *a: pytest.fail("early compile"))
    with pytest.raises(ValueError, match="all eight"):
        diagnostic.run(path, output, "evaluate")


@pytest.mark.parametrize("change", ["packet", "config", "source"])
def test_frozen_inputs_refuse_drift(setup, change):
    path, output, config = setup
    diagnostic.run(path, output, "prepare")
    changed = next((output / "packets").glob("*.json")) if change == "packet" else path if change == "config" else path.parent / config["cases"][0]["packet"]
    changed.write_text(changed.read_text() + " ")
    with pytest.raises(ValueError, match="drift"):
        diagnostic.run(path, output, "prepare")


def test_reject_later_feedback(setup):
    _, _, config = setup
    packet = {"observation": {"task": "Choose.\n" + json.dumps({"history": [{"policy": "default"}, {"policy": "side-pack-3"}]})}}
    with pytest.raises(ValueError, match="baseline-only"):
        diagnostic.prepared_packet(packet, config, "worked_example")


def test_evaluation_uses_old_cache_without_writing_it(setup, monkeypatch):
    path, output, config = setup
    monkeypatch.setattr(diagnostic.jev, "choose", lambda _: {"chosen_action": "0"})
    monkeypatch.setattr(diagnostic.codex_advisor, "choose", lambda _: {"chosen_action": "0"})
    diagnostic.run(path, output)
    for case in config["cases"]:
        diagnostic.save(path.parent / "old/sources" / case["id"] / "manifest.json", {"files": {}})
        for policy in ("default", "policy-0"):
            diagnostic.save(path.parent / "old/probes" / case["id"] / policy / "probe.json", {"compiler_reported_fit": True, "timed_out": False})
    calls = []
    def cached(run, source_config, case, policy, manifest):
        calls.append((run, case["id"], policy))
        return {"eligible_fit": True, "errors": [], "policy": policy}
    monkeypatch.setattr(diagnostic.fit, "compile_policy", cached)
    monkeypatch.setattr(diagnostic, "new_probe", lambda *a: pytest.fail("cached policy recompiled"))
    before = diagnostic.fit.hashes((path.parent / "old").rglob("*.json"), path.parent)
    result = diagnostic.run(path, output, "evaluate")
    assert result["complete"] and len(result["outcomes"]) == 8
    assert all(x["origin"] == "source_cache" for x in result["outcomes"])
    assert len(calls) == 16
    assert before == diagnostic.fit.hashes((path.parent / "old").rglob("*.json"), path.parent)


def test_unseen_choices_compile_once_after_all_choices_and_keep_strict_result(setup, monkeypatch):
    path, output, config = setup
    probe_script = path.parent / "probe.py"
    probe_script.write_text("# frozen probe\n")
    monkeypatch.setattr(diagnostic.p4_compile_probe, "__file__", str(probe_script))
    monkeypatch.setattr(diagnostic.jev, "choose", lambda _: {"chosen_action": "0"})
    monkeypatch.setattr(diagnostic.codex_advisor, "choose", lambda _: {"chosen_action": "0"})
    for case in config["cases"]:
        source = path.parent / "old/sources" / case["id"]
        diagnostic.save(source / "manifest.json", {"files": {"policy-0": {"file": "policy-0/program.p4"}}})
        (source / "policy-0").mkdir()
        (source / "policy-0/program.p4").write_text("// unchanged original source")
        diagnostic.save(path.parent / "old/probes" / case["id"] / "default/probe.json", {})
    diagnostic.run(path, output)
    calls = []
    monkeypatch.setattr(diagnostic.fit, "compile_policy", lambda *a: {"eligible_fit": False})
    monkeypatch.setattr(diagnostic.fit, "validation", lambda *a: {
        "eligible_fit": False, "errors": ["bfrt_changed"], "bfrt_comparison": "different"})

    def compile_case(source, destination, image, timeout):
        assert len(diagnostic.completed_decisions(output, config)) == 8
        assert destination.is_relative_to(output / "probes")
        assert source.is_relative_to(path.parent / "old/sources")
        assert timeout == 120
        calls.append(str(destination))
        report = {"image_id": image, "input_sha256": {str(source.relative_to(path.parent)): diagnostic.p4_compile_probe.sha256(source)},
                  "probe_sha256": diagnostic.p4_compile_probe.sha256(probe_script),
                  "compiler_executable_sha256": {}, "compiler_support_sha256": {},
                  "compiler_reported_fit": True, "timed_out": False}
        diagnostic.save(destination / "probe.json", report)
        return report

    monkeypatch.setattr(diagnostic.p4_compile_probe, "compile_case", compile_case)
    result = diagnostic.run(path, output, "evaluate")
    assert len(calls) == result["new_unique_compiles"] == 2
    assert result["virtual_queries"] == 8
    assert all(o["compiler_reported_fit"] and not o["eligible_fit"] and o["errors"] == ["bfrt_changed"] for o in result["outcomes"])
    diagnostic.run(path, output, "evaluate")
    assert len(calls) == 2
    changed = next((output / "probes").rglob("probe.json"))
    changed.write_text(changed.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        diagnostic.run(path, output, "evaluate")
