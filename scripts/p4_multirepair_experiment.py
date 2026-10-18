"""Frozen annotation-only multi-repair comparison with a shared escalation prefix."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import codex_advisor, jev, p4_compile_probe
from scripts import p4_fit_experiment as fit

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("mechanical", "jev", "codex", "cheap_continuation", "cascade")
DIRECTIVE = re.compile(r"\s*@(pack\(([1-9])\)|immediate\(0\)|placement_priority\((-10|10)\))\s*")
read, save = fit.read, fit.save


def resolve(path):
    return (ROOT / path).resolve()


def canonical_schema(value, generated_table=False, root=True):
    """Ignore annotations/order, and only one declared generated table's own ID."""
    if isinstance(value, list):
        return sorted((canonical_schema(v, root=False) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if not isinstance(value, dict):
        return value
    debug = generated_table and value.get("name") == "pipe.snapshot.cfg" and value.get("table_type") == "SnapshotCfg" and type(value.get("id")) is int
    result = {}
    for key, child in value.items():
        if key == "annotations" or (key == "id" and debug):
            continue
        if root and key == "tables" and isinstance(child, list):
            result[key] = sorted((canonical_schema(t, True, False) for t in child), key=lambda v: json.dumps(v, sort_keys=True))
        else:
            result[key] = canonical_schema(child, root=False)
    return result


def acceptance(case, directory, baseline):
    observations = [t for path in directory.rglob("context.json") for t in read(path).get("tables", [])]
    tables = {table["name"]: table for table in observations}
    errors = []
    if len(tables) != len(observations):
        errors.append("ambiguous_table_context")
    for name, size in case["requirements"].items():
        table = tables.get(name, {})
        attributes = table.get("match_attributes", {})
        stages = attributes.get("stage_tables", [])
        supported = (table.get("table_type") == "match" and attributes.get("match_type") == "exact"
                     and not table.get("action_profile") and not table.get("selection_table_refs")
                     and all(r.get("how_referenced") == "direct" for r in table.get("action_data_table_refs", []))
                     and stages and all(s.get("stage_table_type") == "hash_match" and type(s.get("size")) is int and s["size"] >= 0 for s in stages))
        if not supported:
            errors.append("unsupported_or_missing_context:" + name)
        if table.get("size") != size or (supported and sum(s["size"] for s in stages) < size):
            errors.append("capacity:" + name)
    reference, candidate = list(baseline.rglob("bfrt.json")), list(directory.rglob("bfrt.json"))
    schema = "unavailable"
    if len(reference) == len(candidate) == 1:
        schema = "equal" if canonical_schema(read(reference[0])) == canonical_schema(read(candidate[0])) else "different"
    if schema != "equal":
        errors.append("interface_unverified" if schema == "unavailable" else "bfrt_changed")
    return {"eligible_fit": not errors, "errors": errors, "bfrt_comparison": schema}


def validate_manifest(manifest, directory):
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported corpus manifest schema")
    identifiers, menu = set(), None
    for case in manifest["cases"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", case["id"]) or case["id"] in identifiers:
            raise ValueError("invalid or duplicate case ID")
        identifiers.add(case["id"])
        if not case.get("structure") or not case["requirements"] or any(type(n) is not int or n < 1 for n in case["requirements"].values()):
            raise ValueError("structure and positive named-table capacities are required")
        baseline = (directory / case["default"]).resolve()
        if not baseline.is_relative_to(directory) or baseline.name != "program.p4":
            raise ValueError("default source must be contained and named program.p4")
        original = baseline.read_bytes().decode()
        source_tables = re.findall(r"\btable\s+([A-Za-z_]\w*)\s*\{", original)
        required_tables = [name.rsplit(".", 1)[-1] for name in case["requirements"]]
        if "\r" in original or len(set(source_tables)) != len(source_tables) or sorted(source_tables) != sorted(required_tables):
            raise ValueError("manifest must cover every unambiguous source application table")
        if any(name not in {"core.p4", "tna.p4", "t2na.p4"} for name in re.findall(r'#\s*include\s*[<"]([^>"]+)[>"]', original)):
            raise ValueError("only frozen target headers are supported")
        policies = []
        for candidate in case["candidates"]:
            policy = candidate["id"]
            if policy == "default" or not re.fullmatch(r"[A-Za-z0-9_-]+", policy) or policy in policies:
                raise ValueError("invalid or duplicate policy ID")
            policies.append(policy)
            expected, seen = original.splitlines(keepends=True), set()
            if not candidate["edits"]:
                raise ValueError("candidate must apply an annotation")
            for edit in candidate["edits"]:
                line, text = edit["line"], edit["text"]
                if type(line) is not int or line < 1 or line > len(expected) or line in seen or "\n" in text or not DIRECTIVE.fullmatch(text):
                    raise ValueError("invalid or unapproved annotation edit")
                seen.add(line)
                old = expected[line - 1]
                width = len(old.rstrip("\n"))
                if old.strip() or len(text) > width or not old.endswith("\n"):
                    raise ValueError("annotation must fill a fixed-width blank line")
                expected[line - 1] = text.ljust(width) + "\n"
            source = (directory / candidate["source"]).resolve()
            if not source.is_relative_to(directory) or source.name != "program.p4" or source.read_bytes().decode() != "".join(expected):
                raise ValueError("candidate changed bytes outside declared annotation slots")
        if menu is None:
            menu = policies
        elif set(policies) != set(menu):
            raise ValueError("all cases require the same policy menu")
    if not identifiers:
        raise ValueError("empty corpus")
    return menu


class Experiment:
    def __init__(self, config_path, output):
        self.config_path, self.output = config_path.resolve(), output.resolve()
        self.config = read(self.config_path)
        self.policy = read(resolve(self.config["policy"]))
        if self.policy.get("schema_version") != 1:
            raise ValueError("unsupported policy schema")
        self.manifest_path = resolve(self.config["corpus_manifest"])
        def unique(pairs):
            if len(dict(pairs)) != len(pairs):
                raise ValueError("duplicate manifest key")
            return dict(pairs)
        self.manifest = json.loads(self.manifest_path.read_text(), object_pairs_hook=unique)
        self.menu = validate_manifest(self.manifest, self.manifest_path.parent)
        order = self.policy["mechanical_order"]
        if len(order) != 3 or len(set(order)) != 3 or not set(order) <= set(self.menu) or self.policy["max_attempts"] != 3:
            raise ValueError("mechanical policy must specify three distinct available actions")
        if self.config["compile_timeout_seconds"] != 120 or self.config.get("attempts_per_arm", 3) != 3 or self.config.get("target", "tofino") != "tofino":
            raise ValueError("fixed three-attempt, 120-second Tofino contract required")
        self.knowledge = resolve(self.config["knowledge"]).read_text()
        self.state_path = self.output / "state.json"
        self.state = read(self.state_path) if self.state_path.exists() else {"cases": {}}

    def freeze(self):
        inputs = [self.config_path, self.manifest_path, resolve(self.config["policy"]), resolve(self.config["knowledge"]), resolve(self.config["design"])]
        inputs += [resolve(p) for p in self.policy["knowledge_sources"]]
        for case in self.manifest["cases"]:
            inputs += [self.manifest_path.parent / case["default"]]
            inputs += [self.manifest_path.parent / c["source"] for c in case["candidates"]]
        code = [Path(__file__), Path(fit.__file__), Path(jev.__file__), Path(codex_advisor.__file__), Path(p4_compile_probe.__file__)]
        identity = {"inputs": fit.hashes(inputs, ROOT), "code": fit.hashes(code, ROOT), "toolchain": fit.identity(self.config)}
        frozen = self.output / "freeze.json"
        if frozen.exists():
            value = read(frozen)
            if value["provenance"] != identity or value["sources"] != fit.hashes((self.output / "sources").rglob("*.p4"), self.output):
                raise ValueError("frozen input, code, toolchain or source drift")
            return
        if self.output.exists() and any(self.output.iterdir()):
            raise ValueError("nonempty output has no freeze")
        for case in self.manifest["cases"]:
            for policy, source in [("default", case["default"]), *((c["id"], c["source"]) for c in case["candidates"])]:
                destination = self.source(case, policy)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((self.manifest_path.parent / source).read_bytes())
        save(frozen, {"provenance": identity, "sources": fit.hashes((self.output / "sources").rglob("*.p4"), self.output)})

    def source(self, case, policy):
        return self.output / "sources" / case["id"] / policy / "program.p4"

    def compile(self, case, policy, must_exist=False):
        directory = self.output / "probes" / case["id"] / policy
        report_path, receipt = directory / "probe.json", directory / "receipt.json"
        if directory.exists():
            if not receipt.exists() or read(receipt) != fit.hashes((p for p in directory.rglob("*") if p.is_file() and p != receipt), directory):
                raise ValueError("compiler evidence missing or changed")
            report = read(report_path)
        else:
            if must_exist:
                raise ValueError("recorded compiler evidence missing")
            report = p4_compile_probe.compile_case(self.source(case, policy), directory, self.config["image"], 120)
            save(receipt, fit.hashes((p for p in directory.rglob("*") if p.is_file()), directory))
        frozen = read(self.output / "freeze.json")["provenance"]
        compiler, source = frozen["toolchain"]["compiler"], self.source(case, policy)
        expected = {"image_id": self.config["image"], "probe_sha256": frozen["code"][str(Path(p4_compile_probe.__file__).relative_to(ROOT))],
                    "input_sha256": {str(source.relative_to(ROOT)): p4_compile_probe.sha256(source)},
                    "compiler_executable_sha256": {Path(k).name: v for k, v in compiler.items() if Path(k).parent == Path("bin")},
                    "compiler_support_sha256": {k: v for k, v in compiler.items() if Path(k).suffix in {".p4", ".py", ".cfg"}}}
        if any(report.get(k) != v for k, v in expected.items()):
            raise ValueError("compiler probe identity differs from freeze")
        checked = {"eligible_fit": False, "errors": [], "bfrt_comparison": "not_applicable"}
        if report["compiler_reported_fit"]:
            checked = acceptance(case, directory, self.output / "probes" / case["id"] / "default")
        return {"policy": policy, "probe": str(report_path.relative_to(self.output)), "compiler_reported_fit": report["compiler_reported_fit"],
                "status": "timeout" if report["timed_out"] else "fit" if report["compiler_reported_fit"] else "compiler_failure", **checked}

    def packet(self, case, history):
        attempted = {h.get("policy") for h in history}
        candidates = {c["id"]: c for c in case["candidates"]}
        eligible = [p for p in self.menu if p not in attempted]
        actions, counts, packs = [], [], []
        for index, policy in enumerate(eligible):
            edits = candidates[policy]["edits"]
            actions.append({"id": str(index), "description": policy + ": " + "; ".join(f'line {e["line"]}: {e["text"].strip()}' for e in edits)})
            counts.append(len(edits))
            packs.append(max([int(m.group(2)) for e in edits if (m := DIRECTIVE.fullmatch(e["text"])) and m.group(2)] or [0]))
        context = {"shared_development_knowledge": self.knowledge, "source": self.source(case, "default").read_text(),
                   "history": [fit.feedback(self.output, h) for h in history]}
        observation = {"task": "Choose one annotation-only alternative applied afresh to the original program. Preserve capacities and semantics. Use the shared development knowledge and only this arm's supplied feedback. Compiler failure is not proof of infeasibility.\n" + json.dumps(context),
                       "actions": actions, "features": {"annotation_count": counts, "pack_value": packs},
                       "feature_descriptions": {"annotation_count": "Inserted annotation lines", "pack_value": "Largest requested packing value, or zero when absent"}}
        return {"observation": observation, "action_mapping": dict(zip((a["id"] for a in actions), eligible, strict=True))}

    def step(self, case, arm, index, history, provider, policy=None):
        directory = self.output / "decisions" / case["id"] / arm
        packet_path, decision_path = directory / f"{index + 1}-packet.json", directory / f"{index + 1}.json"
        packet = self.packet(case, history)
        if packet_path.exists() and read(packet_path) != packet:
            raise ValueError("recorded decision context drift")
        save(packet_path, packet)
        if decision_path.exists():
            decision = read(decision_path)
            if decision["provider"] != provider or decision["packet_sha256"] != p4_compile_probe.sha256(packet_path):
                raise ValueError("decision provenance drift")
            if decision["status"] == "started":
                decision.update(status="complete", error="interrupted_provider_call_usage_unknown")
                save(decision_path, decision)
        else:
            decision = {"provider": provider, "packet_sha256": p4_compile_probe.sha256(packet_path), "status": "started"}
            save(decision_path, decision)
            if provider == "mechanical":
                decision["policy"] = policy
            else:
                try:
                    response = (jev if provider == "jev" else codex_advisor).choose(packet["observation"])
                    decision["response"] = response
                    action = response.get("chosen_action")
                    if not isinstance(action, str) or action not in packet["action_mapping"]:
                        decision["error"] = "invalid_or_ineligible_action"
                    else:
                        decision["policy"] = packet["action_mapping"][action]
                except (jev.JevUnavailable, codex_advisor.CodexUnavailable) as exc:
                    decision.update(error=exc.reason, usage="unknown")
            decision["status"] = "complete"
            save(decision_path, decision)
        if "policy" in decision:
            if decision["policy"] not in packet["action_mapping"].values() or (provider == "mechanical" and decision["policy"] != policy):
                raise ValueError("persisted policy is ineligible")
            if provider != "mechanical" and packet["action_mapping"].get(decision.get("response", {}).get("chosen_action")) != decision["policy"]:
                raise ValueError("persisted response/policy mismatch")
        result = {"decision": str(decision_path.relative_to(self.output)), "provider": provider,
                  "decision_sha256": p4_compile_probe.sha256(decision_path), "packet_sha256": p4_compile_probe.sha256(packet_path)}
        result.update(self.compile(case, decision["policy"]) if "policy" in decision else {"error": decision["error"]})
        return result

    def validate_state(self):
        checked = {}
        for case in self.manifest["cases"]:
            record = self.state["cases"].get(case["id"], {})
            records = ([record["baseline"]] if "baseline" in record else []) + record.get("prefix", [])
            records += [s for steps in record.get("arms", {}).values() for s in steps]
            for result in records:
                if "decision" in result:
                    decision_path = self.output / result["decision"]
                    packet_path = decision_path.with_name(decision_path.stem + "-packet.json")
                    if not decision_path.exists() or not packet_path.exists() or p4_compile_probe.sha256(decision_path) != result["decision_sha256"] or p4_compile_probe.sha256(packet_path) != result["packet_sha256"]:
                        raise ValueError("recorded decision evidence drift")
                    decision, packet = read(decision_path), read(packet_path)
                    if decision.get("status") != "complete" or any(result.get(k) != decision.get(k) for k in ("provider", "policy", "error")):
                        raise ValueError("recorded outcome differs from decision")
                    if "policy" in decision and decision["provider"] != "mechanical":
                        action = decision.get("response", {}).get("chosen_action")
                        if not isinstance(action, str) or packet["action_mapping"].get(action) != decision["policy"]:
                            raise ValueError("recorded model action differs from policy")
                if "probe" not in result:
                    continue
                key = (case["id"], result["policy"])
                if key not in checked:
                    checked[key] = self.compile(case, result["policy"], must_exist=True)
                if any(result.get(k) != v for k, v in checked[key].items()):
                    raise ValueError("recorded outcome drift")
            for arm in ("cascade", "cheap_continuation"):
                steps = record.get("arms", {}).get(arm, [])
                if steps and steps[:len(record.get("prefix", []))] != record.get("prefix", []):
                    raise ValueError("shared prefix drift")

    def run(self, arms=ARMS):
        if not set(arms) <= set(ARMS):
            raise ValueError("unknown arm")
        self.validate_state()
        for case in self.manifest["cases"]:
            record = self.state["cases"].setdefault(case["id"], {"arms": {}, "prefix": []})
            record["baseline"] = self.compile(case, "default")
            save(self.state_path, self.state)
            for arm in arms:
                if arm in ("cascade", "cheap_continuation") and not record["baseline"]["eligible_fit"]:
                    prefix = record["prefix"]
                    for i, provider in enumerate(("mechanical", "jev")):
                        if any(s.get("eligible_fit") for s in prefix):
                            break
                        if len(prefix) <= i:
                            prefix.append(self.step(case, "prefix", i, [record["baseline"], *prefix], provider, self.policy["mechanical_order"][0] if i == 0 else None))
                            save(self.state_path, self.state)
                    record["arms"].setdefault(arm, copy.deepcopy(prefix))
                steps = record["arms"].setdefault(arm, [])
                while len(steps) < 3 and not record["baseline"]["eligible_fit"] and not any(s.get("eligible_fit") for s in steps):
                    i = len(steps)
                    provider = "mechanical" if arm == "mechanical" else "jev" if arm in ("jev", "cheap_continuation") else "codex"
                    steps.append(self.step(case, arm, i, [record["baseline"], *steps], provider, self.policy["mechanical_order"][i] if arm == "mechanical" else None))
                    save(self.state_path, self.state)
                save(self.state_path, self.state)
                save(self.output / "summary.json", self.summary())
        return self.summary()

    def summary(self):
        def terminated(record, arm):
            steps = record["arms"][arm]
            return record["baseline"]["eligible_fit"] or any(s.get("eligible_fit") for s in steps) or len(steps) == 3
        arms, pairs = {}, {}
        for arm in ARMS:
            records = [(c, self.state["cases"][c["id"]]) for c in self.manifest["cases"] if arm in self.state["cases"].get(c["id"], {}).get("arms", {})]
            arms[arm] = {"cases": len(records), "complete_cases": sum(terminated(r, arm) for _, r in records), "repairs": sum(not r["baseline"]["eligible_fit"] and any(s.get("eligible_fit") for s in r["arms"][arm]) for _, r in records),
                         "attempts": sum(len(r["arms"][arm]) for _, r in records), "virtual_queries": sum("probe" in s for _, r in records for s in r["arms"][arm]),
                         "provider_errors": sum("error" in s for _, r in records for s in r["arms"][arm])}
        for reference in ("mechanical", "cheap_continuation"):
            wins, losses, structures, count = 0, 0, set(), 0
            for case in self.manifest["cases"]:
                record = self.state["cases"].get(case["id"], {})
                if not {"cascade", reference} <= set(record.get("arms", {})) or not all(terminated(record, arm) for arm in ("cascade", reference)):
                    continue
                count += 1
                cascade = any(s.get("eligible_fit") for s in record["arms"]["cascade"])
                other = any(s.get("eligible_fit") for s in record["arms"][reference])
                wins += cascade and not other
                losses += other and not cascade
                if cascade and not other:
                    structures.add(case["structure"])
            pairs[reference] = {"cases": count, "wins": wins, "losses": losses, "net": wins - losses, "winning_structures": sorted(structures)}
        decisions = [read(p) for p in (self.output / "decisions").rglob("*.json") if not p.name.endswith("-packet.json")]
        complete = all(a["complete_cases"] == len(self.manifest["cases"]) for a in arms.values())
        return {"experiment_id": self.config["experiment_id"], "arms": arms, "cascade_vs": pairs, "complete": complete,
                "positive_gate": complete and all(p["net"] >= 2 and len(p["winning_structures"]) >= 2 for p in pairs.values()),
                "physical_unique_compiles": len(list((self.output / "probes").rglob("probe.json"))),
                "recorded_provider_attempts": sum(d["provider"] != "mechanical" for d in decisions),
                "interrupted_attempts_usage_unknown": sum(d.get("status") == "started" or d.get("error") == "interrupted_provider_call_usage_unknown" for d in decisions),
                "reported_usage": {key: sum(d.get("response", {}).get("usage", {}).get(key, 0) for d in decisions) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")},
                "interpretation": "Compiler-reported fitting only; descriptive screen, not hardware execution or maintenance proof."}


def run(config_path, output, prepare_only=False, arms=ARMS):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_name(output.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        experiment = Experiment(config_path, output)
        experiment.freeze()
        return {"prepared": True} if prepare_only else experiment.run(arms)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--arms", help="Comma-separated resumable arm slice")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, args.prepare_only, args.arms.split(",") if args.arms else ARMS), indent=2))


if __name__ == "__main__":
    main()
