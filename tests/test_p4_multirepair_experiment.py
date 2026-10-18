import copy
import json
import os

import pytest

from scripts import p4_multirepair_experiment as runner


def schema():
    return {"tables": [{"name": "pipe.Ingress.one", "table_type": "MatchAction_Direct", "id": 10, "size": 10,
                        "key": [{"id": 1, "name": "key"}], "annotations": []},
                       {"name": "pipe.snapshot.cfg", "table_type": "SnapshotCfg", "id": 20,
                        "data": [{"id": 30, "name": "snapshot"}]}]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.fit, "hashes", lambda paths, root: {
        os.path.relpath(p, root): runner.p4_compile_probe.sha256(p) for p in sorted(paths)})
    monkeypatch.setattr(runner.fit, "identity", lambda _: {"image": "fixed", "compiler": {"bin/p4c": "compiler"}})
    probe_script = tmp_path / "probe.py"
    probe_script.write_text("# frozen\n")
    monkeypatch.setattr(runner.p4_compile_probe, "__file__", str(probe_script))
    baseline = "control Ingress {\n" + " " * 80 + "\n    table one { }\n}\n"
    case = {"id": "case", "structure": "structure", "requirements": {"Ingress.one": 10}, "default": "case/default/program.p4", "candidates": []}
    policies = {"a": "    @pack(1)", "b": "    @pack(2)", "c": "    @pack(3)", "d": "    @immediate(0)"}
    for policy, text in {"default": None, **policies}.items():
        destination = tmp_path / "corpus/case" / policy / "program.p4"
        destination.parent.mkdir(parents=True)
        destination.write_text(baseline if text is None else baseline.replace(" " * 80 + "\n", text.ljust(80) + "\n"))
        if text:
            case["candidates"].append({"id": policy, "source": f"case/{policy}/program.p4", "edits": [{"line": 2, "text": text}]})
    runner.save(tmp_path / "corpus/manifest.json", {"schema_version": 1, "cases": [case]})
    runner.save(tmp_path / "policy.json", {"schema_version": 1, "max_attempts": 3, "mechanical_order": ["a", "b", "c"], "knowledge_sources": ["knowledge.txt"]})
    (tmp_path / "knowledge.txt").write_text("Exactly shared development knowledge.")
    (tmp_path / "design.md").write_text("Frozen test design.")
    config = {"experiment_id": "test", "image": "fixed", "compile_timeout_seconds": 120, "target": "tofino",
              "corpus_manifest": "corpus/manifest.json", "policy": "policy.json", "knowledge": "knowledge.txt", "design": "design.md"}
    config_path = tmp_path / "config.json"
    runner.save(config_path, config)
    compiled, fits = [], set()

    def compile_case(source, directory, image, timeout):
        policy = source.parent.name
        compiled.append(policy)
        directory.mkdir(parents=True)
        (directory / "stderr.log").write_text(f"diagnostic:{policy}")
        runner.save(directory / "context.json", {"tables": [{"name": "Ingress.one", "table_type": "match", "size": 10,
            "match_attributes": {"match_type": "exact", "stage_tables": [{"stage_table_type": "hash_match", "stage_number": 0, "logical_table_id": 0, "size": 10}]}}]})
        bfrt = schema()
        bfrt["tables"][1]["id"] = len(compiled)  # Only allowed generated-ID variation.
        runner.save(directory / "bfrt.json", bfrt)
        report = {"image_id": image, "probe_sha256": runner.p4_compile_probe.sha256(probe_script),
                  "input_sha256": {str(source.relative_to(tmp_path)): runner.p4_compile_probe.sha256(source)},
                  "compiler_executable_sha256": {"p4c": "compiler"}, "compiler_support_sha256": {},
                  "compiler_reported_fit": policy in fits, "timed_out": False}
        runner.save(directory / "probe.json", report)
        return report

    monkeypatch.setattr(runner.p4_compile_probe, "compile_case", compile_case)
    return config_path, tmp_path / "output", compiled, fits


def select(packet, policy):
    action = next(a["id"] for a in packet["actions"] if a["description"].startswith(policy + ":"))
    return {"chosen_action": action, "usage": {"input_tokens": 2, "output_tokens": 1}}


def test_schema_only_ignores_order_annotations_and_exact_debug_id():
    original = schema()
    changed = copy.deepcopy(original)
    changed["tables"][1]["id"] += 1
    changed["tables"][0]["annotations"] = ["pack"]
    changed["tables"].reverse()
    assert runner.canonical_schema(original) == runner.canonical_schema(changed)
    for table_index, field in ((0, "id"), (0, "size")):
        changed = copy.deepcopy(original)
        changed["tables"][table_index][field] += 1
        assert runner.canonical_schema(original) != runner.canonical_schema(changed)
    changed = copy.deepcopy(original)
    changed["tables"][1]["data"][0]["id"] += 1
    assert runner.canonical_schema(original) != runner.canonical_schema(changed)
    for key, value in (("name", "application"), ("table_type", "Other"), ("id", "20")):
        changed = copy.deepcopy(original)
        changed["tables"][1][key] = value
        assert runner.canonical_schema(original) != runner.canonical_schema(changed)
    changed = copy.deepcopy(original)
    changed["tables"].append(copy.deepcopy(changed["tables"][0]))
    assert runner.canonical_schema(original) != runner.canonical_schema(changed)


def test_nested_snapshot_id_is_not_exempted():
    original = {"nested": schema()}
    changed = copy.deepcopy(original)
    changed["nested"]["tables"][1]["id"] += 1
    assert runner.canonical_schema(original) != runner.canonical_schema(changed)


def test_default_fit_has_no_repair_calls(setup, monkeypatch):
    path, output, compiled, fits = setup
    fits.add("default")
    monkeypatch.setattr(runner.jev, "choose", lambda _: pytest.fail("model called"))
    monkeypatch.setattr(runner.codex_advisor, "choose", lambda _: pytest.fail("model called"))
    summary = runner.run(path, output)
    assert compiled == ["default"]
    assert all(a["attempts"] == 0 for a in summary["arms"].values())
    assert summary["complete"] and not summary["positive_gate"]


def test_shared_prefix_forks_once_and_resume_reuses_decisions(setup, monkeypatch):
    path, output, compiled, fits = setup
    fits.add("d")
    jev_calls, codex_calls = [], []
    def jev(packet):
        jev_calls.append(packet)
        return select(packet, "b" if len(jev_calls) == 1 else "c")
    def codex(packet):
        codex_calls.append(packet)
        return select(packet, "d")
    monkeypatch.setattr(runner.jev, "choose", jev)
    monkeypatch.setattr(runner.codex_advisor, "choose", codex)
    summary = runner.run(path, output, arms=["cascade", "cheap_continuation"])
    record = runner.read(output / "state.json")["cases"]["case"]
    assert record["arms"]["cascade"][:2] == record["arms"]["cheap_continuation"][:2] == record["prefix"]
    assert [s["policy"] for s in record["prefix"]] == ["a", "b"]
    assert len(jev_calls) == 2 and len(codex_calls) == 1
    assert json.loads(jev_calls[-1]["task"].split("\n", 1)[1])["history"] == json.loads(codex_calls[-1]["task"].split("\n", 1)[1])["history"]
    assert summary["arms"]["cascade"]["virtual_queries"] == summary["arms"]["cheap_continuation"]["virtual_queries"] == 3
    assert summary["recorded_provider_attempts"] == 3 and len(compiled) == 5
    runner.run(path, output, arms=["cascade", "cheap_continuation"])
    assert len(jev_calls) == 2 and len(codex_calls) == 1


def test_other_arm_cache_does_not_leak_feedback(setup, monkeypatch):
    path, output, compiled, _ = setup
    packets = []
    def choose(packet):
        packets.append(packet)
        return select(packet, ["a", "b", "c"][len(packets) - 1])
    monkeypatch.setattr(runner.jev, "choose", choose)
    summary = runner.run(path, output, arms=["mechanical", "jev"])
    histories = [json.loads(p["task"].split("\n", 1)[1])["history"] for p in packets]
    assert [[h["policy"] for h in hs] for hs in histories] == [["default"], ["default", "a"], ["default", "a", "b"]]
    assert compiled == ["default", "a", "b", "c"]
    assert summary["arms"]["jev"]["virtual_queries"] == 3
    assert all("Exactly shared development knowledge." in p["task"] for p in packets)


def test_provider_failure_consumes_attempt_and_prefix_not_retried(setup, monkeypatch):
    path, output, _, _ = setup
    calls = []
    def fail(_):
        calls.append(1)
        raise runner.jev.JevUnavailable("transport_error", "safe")
    monkeypatch.setattr(runner.jev, "choose", fail)
    monkeypatch.setattr(runner.codex_advisor, "choose", lambda _: {"chosen_action": "999"})
    summary = runner.run(path, output, arms=["cheap_continuation", "cascade"])
    assert len(calls) == 2
    assert summary["arms"]["cascade"]["provider_errors"] == 2
    assert summary["arms"]["cheap_continuation"]["attempts"] == 3
    assert summary["arms"]["cascade"]["virtual_queries"] == 1


@pytest.mark.parametrize("mutation", ["nonannotation", "bad_pragma", "duplicate_line", "missing_table", "duplicate_source_table"])
def test_manifest_rejects_unapproved_source_or_inventory_changes(setup, mutation):
    path, _, _, _ = setup
    manifest_path = path.parent / "corpus/manifest.json"
    manifest = runner.read(manifest_path)
    candidate = manifest["cases"][0]["candidates"][0]
    source = manifest_path.parent / candidate["source"]
    if mutation == "nonannotation":
        source.write_text(source.read_text().replace("table one", "table changed"))
    elif mutation == "bad_pragma":
        candidate["edits"][0]["text"] = "    @force_immediate(0)"
    elif mutation == "duplicate_line":
        candidate["edits"].append(candidate["edits"][0])
    elif mutation == "missing_table":
        manifest["cases"][0]["requirements"] = {"Ingress.other": 10}
    else:
        baseline = manifest_path.parent / manifest["cases"][0]["default"]
        baseline.write_text(baseline.read_text() + "table one {}\n")
    with pytest.raises(ValueError):
        runner.validate_manifest(manifest, manifest_path.parent)


def test_duplicate_manifest_requirement_key_rejected(setup):
    path, output, _, _ = setup
    manifest_path = path.parent / "corpus/manifest.json"
    manifest_path.write_text(manifest_path.read_text().replace('"Ingress.one": 10', '"Ingress.one": 10, "Ingress.one": 10'))
    with pytest.raises(ValueError, match="duplicate manifest"):
        runner.Experiment(path, output)


@pytest.mark.parametrize("fault", ["duplicate", "missing_stage", "missing_logical", "boolean", "negative"])
def test_fail_closed_on_invalid_allocation_identity(setup, fault):
    path, output, _, fits = setup
    fits.add("default")
    experiment = runner.Experiment(path, output)
    experiment.freeze()
    case = experiment.manifest["cases"][0]
    experiment.compile(case, "default")
    directory = output / "probes/case/default"
    context = runner.read(directory / "context.json")
    stages = context["tables"][0]["match_attributes"]["stage_tables"]
    if fault == "duplicate":
        stages[0]["size"] = 5
        stages.append(copy.deepcopy(stages[0]))
    elif fault.startswith("missing"):
        del stages[0]["stage_number" if fault == "missing_stage" else "logical_table_id"]
    else:
        stages[0]["logical_table_id"] = True if fault == "boolean" else -1
    runner.save(directory / "context.json", context)
    checked = runner.acceptance(case, directory, directory)
    assert not checked["eligible_fit"]
    assert "unsupported_or_missing_context:Ingress.one" in checked["errors"]


def test_distinct_logical_allocations_in_same_stage_are_supported(setup):
    path, output, _, fits = setup
    fits.add("default")
    experiment = runner.Experiment(path, output)
    experiment.freeze()
    case = experiment.manifest["cases"][0]
    experiment.compile(case, "default")
    directory = output / "probes/case/default"
    context = runner.read(directory / "context.json")
    stages = context["tables"][0]["match_attributes"]["stage_tables"]
    stages[0]["size"] = 5
    stages.append({**stages[0], "logical_table_id": 1})
    runner.save(directory / "context.json", context)
    assert runner.acceptance(case, directory, directory)["eligible_fit"]


def test_fail_closed_on_capacity_or_unsupported_context(setup):
    path, output, _, fits = setup
    fits.add("default")
    runner.run(path, output, arms=["mechanical"])
    case = runner.Experiment(path, output).manifest["cases"][0]
    directory = output / "probes/case/default"
    context = runner.read(directory / "context.json")
    context["tables"][0]["match_attributes"]["stage_tables"][0]["stage_table_type"] = "unknown"
    runner.save(directory / "context.json", context)
    assert not runner.acceptance(case, directory, directory)["eligible_fit"]
    context["tables"][0]["match_attributes"]["stage_tables"][0].update(stage_table_type="hash_match", size=9)
    runner.save(directory / "context.json", context)
    assert "capacity:Ingress.one" in runner.acceptance(case, directory, directory)["errors"]
    context["tables"].append(context["tables"][0])
    runner.save(directory / "context.json", context)
    assert "ambiguous_table_context" in runner.acceptance(case, directory, directory)["errors"]


@pytest.mark.parametrize("kind", ["source", "probe", "decision", "packet", "knowledge"])
def test_resume_refuses_evidence_or_input_drift(setup, kind):
    path, output, _, _ = setup
    runner.run(path, output, arms=["mechanical"])
    changed = {"source": output / "sources/case/a/program.p4", "probe": output / "probes/case/a/probe.json",
               "decision": output / "decisions/case/mechanical/1.json", "packet": output / "decisions/case/mechanical/1-packet.json",
               "knowledge": path.parent / "knowledge.txt"}[kind]
    changed.write_text(changed.read_text() + " ")
    with pytest.raises(ValueError):
        runner.run(path, output, arms=["mechanical"])


def test_sliced_resume_does_not_score_unfinished_shared_prefix_arm(setup, monkeypatch):
    path, output, _, _ = setup
    monkeypatch.setattr(runner.jev, "choose", lambda _: {"chosen_action": "999"})
    monkeypatch.setattr(runner.codex_advisor, "choose", lambda _: {"chosen_action": "999"})
    assert runner.run(path, output)["complete"]
    state = runner.read(output / "state.json")
    record = state["cases"]["case"]
    record["arms"]["cheap_continuation"] = copy.deepcopy(record["prefix"])
    runner.save(output / "state.json", state)
    summary = runner.run(path, output, arms=["cascade"])
    assert not summary["complete"] and not summary["positive_gate"]
    assert summary["arms"]["cheap_continuation"]["complete_cases"] == 0
    assert summary["cascade_vs"]["cheap_continuation"]["cases"] == 0


def test_resume_rejects_real_fit_probe_attached_to_different_decision(setup, monkeypatch):
    path, output, _, fits = setup
    fits.add("d")
    monkeypatch.setattr(runner.codex_advisor, "choose", lambda p: select(p, "d"))
    runner.run(path, output, arms=["mechanical", "codex"])
    state = runner.read(output / "state.json")
    record = state["cases"]["case"]
    target, fit_step = record["arms"]["mechanical"][0], record["arms"]["codex"][0]
    for key in ("policy", "probe", "compiler_reported_fit", "status", "eligible_fit", "errors", "bfrt_comparison"):
        target[key] = fit_step[key]
    runner.save(output / "state.json", state)
    with pytest.raises(ValueError, match="differs from decision"):
        runner.run(path, output, arms=["mechanical"])


def test_resume_reuses_choice_persisted_before_compiler_interruption(setup, monkeypatch):
    path, output, _, fits = setup
    fits.add("a")
    calls = []
    monkeypatch.setattr(runner.jev, "choose", lambda p: calls.append(p) or select(p, "a"))
    original = runner.p4_compile_probe.compile_case
    def interrupt(source, *args):
        if source.parent.name != "default":
            raise RuntimeError("interrupted before compile")
        return original(source, *args)
    monkeypatch.setattr(runner.p4_compile_probe, "compile_case", interrupt)
    with pytest.raises(RuntimeError):
        runner.run(path, output, arms=["jev"])
    monkeypatch.setattr(runner.p4_compile_probe, "compile_case", original)
    summary = runner.run(path, output, arms=["jev"])
    assert len(calls) == 1 and summary["arms"]["jev"]["repairs"] == 1


def test_interrupted_provider_marker_consumes_attempt_without_retry(setup, monkeypatch):
    path, output, _, fits = setup
    fits.add("a")
    def interrupt(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(runner.jev, "choose", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.run(path, output, arms=["jev"])
    calls = []
    monkeypatch.setattr(runner.jev, "choose", lambda p: calls.append(p) or select(p, "a"))
    summary = runner.run(path, output, arms=["jev"])
    assert len(calls) == 1
    assert summary["arms"]["jev"]["attempts"] == 2
    assert summary["interrupted_attempts_usage_unknown"] == 1
