from pathlib import Path

import pytest
from test_p4_multirepair_experiment import setup as trial_setup  # noqa: F401

from scripts import report_p4_multirepair as report


@pytest.fixture
def completed(trial_setup, monkeypatch):  # noqa: F811 - imported shared pytest fixture
    config, output, _, fits = trial_setup
    root = config.parent
    monkeypatch.setattr(report, "ROOT", root)
    # Keep the fake run's frozen code inputs inside its isolated repository.
    for name, module in (("runner", report.trial), ("fit", report.trial.fit),
                         ("jev", report.trial.jev), ("codex", report.trial.codex_advisor)):
        source = root / f"{name}.py"
        source.write_text(Path(module.__file__).read_text())
        monkeypatch.setattr(module, "__file__", str(source))
    original = report.trial.p4_compile_probe.compile_case
    def compile_case(*args):
        result = original(*args)
        (args[1] / "stdout.log").write_text("mock compiler output")
        return result
    monkeypatch.setattr(report.trial.p4_compile_probe, "compile_case", compile_case)
    fits.add("a")
    def choose(packet):
        action = next(a["id"] for a in packet["actions"] if a["description"].startswith("a:"))
        return {"chosen_action": action, "model": "test-model", "usage": {"input_tokens": 2, "output_tokens": 1}}
    monkeypatch.setattr(report.trial.jev, "choose", choose)
    monkeypatch.setattr(report.trial.codex_advisor, "choose", choose)
    assert report.trial.run(config, output)["complete"]
    monkeypatch.setattr(report.trial.p4_compile_probe, "compile_case", lambda *a: pytest.fail("export compiled"))
    monkeypatch.setattr(report.trial.jev, "choose", lambda *a: pytest.fail("export called provider"))
    monkeypatch.setattr(report.trial.codex_advisor, "choose", lambda *a: pytest.fail("export called provider"))
    return config, output


def test_complete_export_contains_auditable_sanitized_evidence(completed):
    config, output = completed
    destination = config.parent / "results/trial.json"
    markdown = destination.with_suffix(".md")
    before = report.trial.fit.hashes((output).rglob("*.*"), output)
    report.export_report(config, output, destination, markdown)
    bundle = report.read(destination)
    assert bundle["status"] == "complete"
    assert len(bundle["decisions"]) == len(bundle["packets"]) == 4
    assert len(bundle["compiler_probes"]) == 2
    assert len(bundle["sources"]) == 5
    assert bundle["provider_usage"]["jev"] == {"input_tokens": 2, "output_tokens": 1}
    assert bundle["validation"]["summary_recomputed_from_completed_state"]
    assert all("logical_table_id" in t["match_attributes"]["stage_tables"][0]
               for p in bundle["compiler_probes"].values() for t in p["capacity_context"])
    assert str(config.parent) not in destination.read_text()
    assert "not met" in markdown.read_text()
    assert "normalized" in markdown.read_text()
    assert before == report.trial.fit.hashes(output.rglob("*.*"), output)


@pytest.mark.parametrize("change", ["incomplete", "stale", "probe", "decision", "source"])
def test_export_rejects_incomplete_or_changed_trial(completed, change):
    config, output = completed
    if change in ("incomplete", "stale"):
        summary = report.read(output / "summary.json")
        summary["complete" if change == "incomplete" else "positive_gate"] = change == "stale"
        report.trial.save(output / "summary.json", summary)
    else:
        path = {"probe": output / "probes/case/a/probe.json", "decision": output / "decisions/case/jev/1.json",
                "source": output / "sources/case/a/program.p4"}[change]
        path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        report.build_bundle(config, output)


def test_sealed_paths_are_rejected_without_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="sealed"):
        report.read(tmp_path / "artifacts/p4-multirepair-curation/sealed/not-created.json")


def test_credential_fields_and_host_paths_are_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "ROOT", tmp_path)
    assert report.sanitize({"api_key": "never publish", "message": f"{tmp_path}/x Bearer abc123"}) == {
        "api_key": "[REDACTED]", "message": "$REPO/x Bearer [REDACTED]"}


def test_export_cannot_overwrite_run_or_frozen_inputs(completed):
    config, output = completed
    with pytest.raises(ValueError, match="outside immutable"):
        report.export_report(config, output, output / "report.json", config.parent / "report.md")
    with pytest.raises(ValueError, match="overwrite"):
        report.export_report(config, output, config, config.parent / "report.md")


@pytest.mark.parametrize("location, field", [("baseline", "probe"), ("prefix", "decision"), ("arm", "decision"), ("arm", "probe")])
def test_state_traversal_is_rejected_before_outsider_read_or_hash(completed, monkeypatch, location, field):
    config, output = completed
    outsider = config.parent / "outside"
    outsider.mkdir()
    for name in ("1.json", "1-packet.json", "probe.json"):
        (outsider / name).write_text("{}")
    state = report.read(output / "state.json")
    case = state["cases"]["case"]
    step = case["baseline"] if location == "baseline" else case["prefix"][0] if location == "prefix" else case["arms"]["jev"][0]
    step[field] = "../outside/" + ("1.json" if field == "decision" else "probe.json")
    report.trial.save(output / "state.json", state)
    original_text, original_bytes = Path.read_text, Path.read_bytes
    def check(path):
        assert not path.resolve().is_relative_to(outsider), "outsider read/hash invoked"
    def read_text(path, *args, **kwargs):
        check(path)
        return original_text(path, *args, **kwargs)
    def read_bytes(path, *args, **kwargs):
        check(path)
        return original_bytes(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(ValueError, match="inside trial output"):
        report.build_bundle(config, output)


def test_derived_packet_symlink_cannot_escape_trial(completed, monkeypatch):
    config, output = completed
    outsider = config.parent / "outside.json"
    outsider.write_text("{}")
    packet = output / "decisions/case/jev/1-packet.json"
    packet.unlink()
    packet.symlink_to(outsider)
    original = Path.read_bytes
    def read_bytes(path, *args, **kwargs):
        assert path.resolve() != outsider, "outsider hash invoked"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(ValueError, match="inside trial output"):
        report.build_bundle(config, output)


def test_unreferenced_decision_symlink_is_blocked_before_summary_read(completed, monkeypatch):
    config, output = completed
    outsider = config.parent / "outside-decision.json"
    outsider.write_text('{"provider": "mechanical"}')
    (output / "decisions/unreferenced.json").symlink_to(outsider)
    original_text, original_bytes = Path.read_text, Path.read_bytes
    def read_text(path, *args, **kwargs):
        assert path.resolve() != outsider, "outsider read invoked"
        return original_text(path, *args, **kwargs)
    def read_bytes(path, *args, **kwargs):
        assert path.resolve() != outsider, "outsider hash invoked"
        return original_bytes(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(ValueError, match="inside trial output"):
        report.build_bundle(config, output)
