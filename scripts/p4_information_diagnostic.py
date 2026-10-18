"""Freeze an eight-choice information diagnostic; evaluate only after every choice."""
from __future__ import annotations

import argparse
import copy
import fcntl
import itertools
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import codex_advisor, jev, p4_compile_probe
from scripts import p4_fit_experiment as fit

ROOT = Path(__file__).resolve().parents[1]
read, save = fit.read, fit.save
CONDITIONS = ("baseline_information", "worked_example")


def resolve(path):
    return (ROOT / path).resolve()


def choice_id(index, entry):
    return f'{index + 1:02d}-{entry["case"]}-{entry["provider"]}-{entry["condition"]}'


def load_config(path):
    config = read(path)
    expected = set(itertools.product(("tenant56", "tenant8"), ("jev", "codex"), CONDITIONS))
    actual = [(x["case"], x["provider"], x["condition"]) for x in config["schedule"]]
    if len(actual) != 8 or set(actual) != expected:
        raise ValueError("schedule must contain exactly the eight independent choices")
    if {c["id"] for c in config["cases"]} != {"tenant56", "tenant8"} or len(config["cases"]) != 2:
        raise ValueError("diagnostic requires exactly tenant56 and tenant8")
    if set(config["providers"]) != {"jev", "codex"} or set(config["conditions"]) != set(CONDITIONS):
        raise ValueError("unexpected providers or conditions")
    if config["compile_timeout_seconds"] != 120:
        raise ValueError("diagnostic compiler timeout must remain 120 seconds")
    source_config = read(resolve(config["source_config"]))
    if source_config["image"] != config["image"]:
        raise ValueError("diagnostic image must match the source experiment")
    return config, source_config


def prepared_packet(base, config, condition):
    packet = copy.deepcopy(base)
    # Reject accidental later-turn feedback before constructing either condition.
    context = fit.json.loads(packet["observation"]["task"].split("\n", 1)[1])
    if [h.get("policy") for h in context["history"]] != ["default"]:
        raise ValueError("diagnostic requires baseline-only feedback")
    mapping = packet["action_mapping"]
    if len(mapping) != 24 or "default" in mapping.values() or len(set(mapping.values())) != 24:
        raise ValueError("expected the original 24 nondefault alternatives")
    if set(mapping) != {a["id"] for a in packet["observation"]["actions"]}:
        raise ValueError("action mapping differs from the packet menu")
    packet["observation"]["task"] += "\n\n" + config["common_context"]
    if condition == "worked_example":
        packet["observation"]["task"] += "\n\n" + config["worked_example"]
    return packet


def prepare(config_path, output, config, source_config):
    source_run = resolve(config["source_run"])
    # Existing freeze only: never bootstrap or mutate the previous experiment.
    if not (source_run / "freeze.json").is_file():
        raise ValueError("source experiment is not frozen")
    fit.freeze(resolve(config["source_config"]), source_run, source_config)
    inputs = [resolve(c["packet"]) for c in config["cases"]]
    inputs += [source_run / "freeze.json", resolve(config["source_config"]),
               ROOT / "docs/results/p4-pragma-fit-qualification-v1.json"]
    if "design" in config:
        inputs.append(resolve(config["design"]))
    code = [Path(__file__), Path(fit.__file__), Path(jev.__file__), Path(codex_advisor.__file__), Path(p4_compile_probe.__file__)]
    current = {"config_sha256": p4_compile_probe.sha256(config_path),
               "inputs": fit.hashes(inputs, ROOT), "code": fit.hashes(code, ROOT),
               "toolchain": fit.identity(config)}
    frozen = output / "freeze.json"
    if frozen.exists():
        stored = read(frozen)
        if stored["provenance"] != current or stored["packets"] != fit.hashes((output / "packets").glob("*.json"), output):
            raise ValueError("diagnostic provenance or prepared packet drift")
        return
    if output.exists() and any(output.iterdir()):
        raise ValueError("nonempty diagnostic output has no freeze")
    bases = {c["id"]: read(resolve(c["packet"])) for c in config["cases"]}
    for index, entry in enumerate(config["schedule"]):
        save(output / "packets" / (choice_id(index, entry) + ".json"), prepared_packet(bases[entry["case"]], config, entry["condition"]))
    save(frozen, {"provenance": current, "packets": fit.hashes((output / "packets").glob("*.json"), output)})


def decision_path(output, index, entry):
    return output / "decisions" / (choice_id(index, entry) + ".json")


def validate_decision(path, packet_path, entry):
    decision = read(path)
    if decision.get("entry") != entry or decision.get("packet_sha256") != p4_compile_probe.sha256(packet_path):
        raise ValueError("decision identity differs from frozen packet")
    if decision["status"] == "complete" and "policy" in decision:
        action = decision.get("response", {}).get("chosen_action")
        if read(packet_path)["action_mapping"].get(action) != decision["policy"]:
            raise ValueError("saved policy differs from returned action")
    return decision


def choose_all(output, config):
    for index, entry in enumerate(config["schedule"]):
        packet_path = output / "packets" / (choice_id(index, entry) + ".json")
        path = decision_path(output, index, entry)
        if path.exists():
            decision = validate_decision(path, packet_path, entry)
            if decision["status"] == "started":
                decision.update(status="complete", error="interrupted_provider_call_usage_unknown")
                save(path, decision)
            continue
        packet = read(packet_path)
        decision = {"entry": entry, "packet_sha256": p4_compile_probe.sha256(packet_path), "status": "started"}
        save(path, decision)
        try:
            response = (jev if entry["provider"] == "jev" else codex_advisor).choose(packet["observation"])
            decision["response"] = response
            action = response.get("chosen_action")
            if not isinstance(action, str) or action not in packet["action_mapping"]:
                decision["error"] = "invalid_or_ineligible_action"
            else:
                decision["policy"] = packet["action_mapping"][action]
        except (jev.JevUnavailable, codex_advisor.CodexUnavailable) as exc:
            decision.update(error=exc.reason, usage="unknown")
        decision["status"] = "complete"
        save(path, decision)


def completed_decisions(output, config):
    decisions = []
    for index, entry in enumerate(config["schedule"]):
        path = decision_path(output, index, entry)
        if not path.exists():
            raise ValueError("all eight choices must complete before evaluation")
        decision = validate_decision(path, output / "packets" / (choice_id(index, entry) + ".json"), entry)
        if decision["status"] != "complete":
            raise ValueError("all eight choices must complete before evaluation")
        decisions.append(decision)
    return decisions


def new_probe(output, config, case, policy, manifest):
    source_run = resolve(config["source_run"])
    source = source_run / "sources" / case["id"] / manifest["files"][policy]["file"]
    directory = output / "probes" / case["id"] / policy
    receipt, report_path = directory / "receipt.json", directory / "probe.json"
    if directory.exists():
        if not receipt.exists() or read(receipt) != fit.hashes((p for p in directory.rglob("*") if p.is_file() and p != receipt), directory):
            raise ValueError("new diagnostic probe is incomplete or changed")
        report = read(report_path)
    else:
        report = p4_compile_probe.compile_case(source, directory, config["image"], config["compile_timeout_seconds"])
        save(receipt, fit.hashes((p for p in directory.rglob("*") if p.is_file()), directory))
    frozen = read(output / "freeze.json")["provenance"]
    compiler = frozen["toolchain"]["compiler"]
    expected = {"image_id": config["image"], "input_sha256": {str(source.relative_to(ROOT)): p4_compile_probe.sha256(source)},
                "probe_sha256": frozen["code"][str(Path(p4_compile_probe.__file__).relative_to(ROOT))],
                "compiler_executable_sha256": {Path(k).name: v for k, v in compiler.items() if Path(k).parent == Path("bin")},
                "compiler_support_sha256": {k: v for k, v in compiler.items() if Path(k).suffix in {".p4", ".py", ".cfg"}}}
    if any(report.get(k) != v for k, v in expected.items()):
        raise ValueError("diagnostic probe differs from frozen identity")
    acceptance = {"eligible_fit": False, "errors": [], "bfrt_comparison": "not_applicable"}
    if report["compiler_reported_fit"]:
        acceptance = fit.validation(case, directory, source_run / "probes" / case["id"] / "default")
    return {"policy": policy, "probe": str(report_path.relative_to(ROOT)), "origin": "diagnostic",
            "compiler_reported_fit": report["compiler_reported_fit"], "timed_out": report["timed_out"], **acceptance}


def evaluate(output, config, source_config):
    decisions = completed_decisions(output, config)  # No compile or feedback until all choices are durable.
    source_run = resolve(config["source_run"])
    cases = {c["id"]: c for c in source_config["cases"]}
    outcomes = []
    for decision in decisions:
        if "policy" not in decision:
            outcomes.append({"entry": decision["entry"], "error": decision["error"], "eligible_fit": False})
            continue
        case, policy = cases[decision["entry"]["case"]], decision["policy"]
        manifest = read(source_run / "sources" / case["id"] / "manifest.json")
        # Validate the unchanged baseline interface before accepting any new/cached candidate.
        baseline = source_run / "probes" / case["id"] / "default" / "probe.json"
        if not baseline.is_file():
            raise ValueError("original baseline evidence is missing")
        fit.compile_policy(source_run, source_config, case, "default", manifest)
        cached = source_run / "probes" / case["id"] / policy / "probe.json"
        if cached.is_file():
            result = fit.compile_policy(source_run, source_config, case, policy, manifest)
            result.update(origin="source_cache", probe=str(cached.relative_to(ROOT)),
                          compiler_reported_fit=read(cached)["compiler_reported_fit"], timed_out=read(cached)["timed_out"])
        else:
            result = new_probe(output, config, case, policy, manifest)
        outcomes.append({"entry": decision["entry"], **result})
        save(output / "evaluation.json", {"outcomes": outcomes, "complete": False})
    result = {"experiment_id": config["experiment_id"], "outcomes": outcomes, "complete": True,
              "choice_attempts": 8, "virtual_queries": sum("policy" in d for d in decisions),
              "new_unique_compiles": len(list((output / "probes").rglob("probe.json"))),
              "interpretation": "Information-access diagnostic on two already measured cases; not a generalization result."}
    save(output / "evaluation.json", result)
    return result


def run(config_path, output, phase="choose"):
    config_path, output = config_path.resolve(), output.resolve()
    config, source_config = load_config(config_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_name(output.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare(config_path, output, config, source_config)
        if phase == "prepare":
            return {"prepared_choices": 8}
        if phase == "evaluate":
            return evaluate(output, config, source_config)
        choose_all(output, config)
        decisions = completed_decisions(output, config)
        summary = {"experiment_id": config["experiment_id"], "completed_choices": len(decisions),
                   "provider_errors": sum("error" in d for d in decisions),
                   "reported_usage": {key: sum(d.get("response", {}).get("usage", {}).get(key, 0) for d in decisions) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")}}
        save(output / "choices-summary.json", summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    phases = parser.add_mutually_exclusive_group()
    phases.add_argument("--prepare-only", action="store_true")
    phases.add_argument("--run-choice", action="store_true")
    phases.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    phase = "prepare" if args.prepare_only else "evaluate" if args.evaluate else "choose"
    print(fit.json.dumps(run(args.config, args.output, phase), indent=2))


if __name__ == "__main__":
    main()
