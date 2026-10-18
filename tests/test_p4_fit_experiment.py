import json

import pytest

from scripts import p4_fit_experiment as runner


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = {
        "experiment_id": "fixture", "image": "sha256:" + "a" * 64,
        "compile_timeout_seconds": 120, "attempts_per_arm": 3, "random_seed": 20261018,
        "cases": [{"id": "small", "depth": 1, "sides": 1, "chain_entries": 10,
                   "side_entries": 20, "tenant_bits": 8}], "arms": list(runner.ARMS),
    }
    path = tmp_path / "config.json"
    runner.save(path, config)
    monkeypatch.setattr(runner, "identity", lambda _: {"image": config["image"], "compiler": {"bin/p4c": "fixed"}})
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "provenance", lambda path, config: {"config_sha256": runner.p4_compile_probe.sha256(path), "code": {"probe.py": "probe"}, "toolchain": runner.identity(config)})
    monkeypatch.setattr(runner.p4_compile_probe, "__file__", str(tmp_path / "probe.py"))
    compiled = []
    fits = set()

    def compile_case(source, directory, image, timeout):
        assert timeout == 120
        policy = source.parent.name
        compiled.append(policy)
        directory.mkdir(parents=True)
        (directory / "stderr.log").write_text(f"diagnostic for {policy}")
        runner.save(directory / "context.json", {"tables": [
            {"name": f"Ingress.{group}_0", "size": size, "table_type": "match",
             "match_attributes": {"stage_tables": [{"stage_number": 1, "size": size,
                                                       "pack_format": [{"entries_per_table_word": 3}]}]}}
            for group, size in (("chain", 10), ("side", 20))]})
        runner.save(directory / "bfrt.json", {"tables": [{"name": "fixture", "size": 10, "annotations": [policy]}]})
        report = {"compiler_reported_fit": policy in fits, "timed_out": False,
                  "image_id": image, "probe_sha256": "probe",
                  "input_sha256": {str(source.relative_to(tmp_path)): runner.p4_compile_probe.sha256(source)},
                  "compiler_executable_sha256": {"p4c": "fixed"}, "compiler_support_sha256": {}}
        runner.save(directory / "probe.json", report)
        return report

    monkeypatch.setattr(runner.p4_compile_probe, "compile_case", compile_case)
    return path, tmp_path / "output", compiled, fits


def choose_name(packet, name):
    action = next(a["id"] for a in packet["actions"] if a["description"].startswith(name + ":"))
    return {"chosen_action": action, "usage": {"input_tokens": 10, "output_tokens": 2}}


def test_baseline_fit_skips_all_advice(setup, monkeypatch):
    config, output, compiled, fits = setup
    fits.add("default")
    monkeypatch.setattr(runner.jev, "choose", lambda _: pytest.fail("unexpected Jev call"))
    monkeypatch.setattr(runner.codex_advisor, "choose", lambda _: pytest.fail("unexpected Codex call"))
    summary = runner.run(config, output)
    assert compiled == ["default"]
    assert all(a["attempts"] == 0 for a in summary["arms"].values())
    assert summary["physical_unique_compiles"] == 1


def test_provider_errors_and_invalid_choices_consume_attempts(setup, monkeypatch):
    config, output, compiled, _ = setup
    calls = []

    def fail(packet):
        calls.append(packet)
        if len(calls) == 1:
            raise runner.jev.JevUnavailable("transport_error", "safe error")
        return {"chosen_action": "999"}

    monkeypatch.setattr(runner.jev, "choose", fail)
    summary = runner.run(config, output, arms=["jev"])
    assert len(calls) == summary["arms"]["jev"]["attempts"] == 3
    assert summary["arms"]["jev"]["provider_failures"] == 3
    assert compiled == ["default"]
    runner.run(config, output, arms=["jev"])
    assert len(calls) == 3


def test_cache_reuse_does_not_leak_other_arm_feedback(setup, monkeypatch):
    config, output, compiled, _ = setup
    observations = []

    def choose(packet):
        observations.append(json.loads(packet["task"].split("\n", 1)[1]))
        return choose_name(packet, ["side-pack-3", "side-pack-2", "side-pack-4"][len(observations) - 1])

    monkeypatch.setattr(runner.jev, "choose", choose)
    summary = runner.run(config, output, arms=["packing", "jev"])
    assert compiled == ["default", "side-pack-3", "side-pack-2", "side-pack-4"]
    assert summary["physical_unique_compiles"] == 4
    assert summary["arms"]["packing"]["virtual_queries"] == 3
    assert summary["arms"]["jev"]["virtual_queries"] == 3
    assert [[s["policy"] for s in o["history"]] for o in observations] == [
        ["default"], ["default", "side-pack-3"], ["default", "side-pack-3", "side-pack-2"]]
    assert all(a["id"].isdigit() for a in runner.read(output / "decisions/small/jev/1-packet.json")["observation"]["actions"])


def test_resume_keeps_decision_after_interrupted_compile(setup, monkeypatch):
    config, output, compiled, fits = setup
    fits.add("side-pack-3")
    calls = []
    monkeypatch.setattr(runner.jev, "choose", lambda p: calls.append(p) or choose_name(p, "side-pack-3"))
    original = runner.p4_compile_probe.compile_case

    def interrupt(source, *args):
        if source.parent.name != "default":
            raise RuntimeError("interrupted before starting compiler")
        return original(source, *args)

    monkeypatch.setattr(runner.p4_compile_probe, "compile_case", interrupt)
    with pytest.raises(RuntimeError):
        runner.run(config, output, arms=["jev"])
    assert runner.read(output / "decisions/small/jev/1.json")["policy"] == "side-pack-3"
    monkeypatch.setattr(runner.p4_compile_probe, "compile_case", original)
    result = runner.run(config, output, arms=["jev"])
    assert len(calls) == 1
    assert compiled == ["default", "side-pack-3"]
    assert result["arms"]["jev"]["rescues"] == 1


@pytest.mark.parametrize("change", ["source", "config", "toolchain", "code"])
def test_refuse_drift(setup, monkeypatch, change):
    config, output, _, fits = setup
    fits.add("default")
    runner.run(config, output, arms=["packing"])
    if change == "source":
        with (output / "sources/small/default/program.p4").open("a") as stream:
            stream.write("// changed\n")
    elif change == "config":
        config.write_text(config.read_text() + "\n")
    elif change == "toolchain":
        monkeypatch.setattr(runner, "identity", lambda _: {"compiler": "changed"})
    else:
        original = runner.provenance
        monkeypatch.setattr(runner, "provenance", lambda *a: {**original(*a), "code": "changed"})
    with pytest.raises(ValueError, match="drift"):
        runner.run(config, output, arms=["packing"])


def test_capacity_and_bfrt_checks(setup):
    config, output, _, fits = setup
    fits.add("default")
    runner.run(config, output, arms=["packing"])
    directory = output / "probes/small/default"
    case = runner.read(config)["cases"][0]
    assert runner.validation(case, directory, directory)["eligible_fit"]
    (directory / "bfrt.json").unlink()
    assert "interface_unverified" in runner.validation(case, directory, directory)["errors"]
    context = runner.read(directory / "context.json")
    context["tables"][0]["match_attributes"]["stage_tables"][0]["size"] = 9
    runner.save(directory / "context.json", context)
    assert "capacity:chain_0" in runner.validation(case, directory, directory)["errors"]


def test_cascade_credits_only_own_codex_rescue(setup, monkeypatch):
    config, output, compiled, fits = setup
    fits.add("side-pack-4")
    monkeypatch.setattr(runner.jev, "choose", lambda p: choose_name(p, "side-pack-2"))
    monkeypatch.setattr(runner.codex_advisor, "choose", lambda p: choose_name(p, "side-pack-4"))
    summary = runner.run(config, output, arms=["cascade"])
    assert compiled == ["default", "side-pack-3", "side-pack-2", "side-pack-4"]
    assert summary["cascade_codex_increment"] == 1
    assert summary["arms"]["cascade"]["attempts"] == 3


@pytest.mark.parametrize("artifact", ["probe.json", "context.json", "bfrt.json", "stderr.log"])
def test_completed_evidence_drift_refused_even_in_other_slice(setup, artifact):
    config, output, _, fits = setup
    fits.add("side-pack-3")
    runner.run(config, output, arms=["packing"])
    path = output / "probes/small/side-pack-3" / artifact
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="evidence drift"):
        runner.run(config, output, arms=["priority"])


def test_started_provider_call_is_never_replayed(setup, monkeypatch):
    config, output, _, _ = setup
    runner.freeze(config, output, runner.read(config))
    runner.save(output / "decisions/small/jev/1.json", {"provider": "jev", "status": "provider_started"})
    calls = []
    monkeypatch.setattr(runner.jev, "choose", lambda p: calls.append(p) or {"chosen_action": "999"})
    summary = runner.run(config, output, arms=["jev"])
    assert len(calls) == 2
    assert summary["arms"]["jev"]["provider_failures"] == 3


def test_resume_refuses_stale_fit_aggregate(setup):
    config, output, _, _ = setup
    runner.run(config, output, arms=["packing"])
    state = runner.read(output / "state.json")
    state["cases"]["small"]["arms"]["packing"]["fit"] = True
    runner.save(output / "state.json", state)
    with pytest.raises(ValueError, match="aggregate outcome drift"):
        runner.run(config, output, arms=["packing"])
