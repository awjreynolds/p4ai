"""Frozen, resumable comparison of annotation advisers; no hardware correctness claim."""
from __future__ import annotations

import argparse
import fcntl
import json
import random
import re
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import codex_advisor, jev, p4_compile_probe

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "examples/p4-fit/generate.py"
ARMS = ("packing", "priority", "random", "jev", "codex", "cascade")
FIXED = {"packing": ["side-pack-3", "side-pack-2", "side-pack-4"],
         "priority": ["chain-priority", "side-priority", "side-later"]}


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def hashes(paths, root):
    return {str(p.relative_to(root)): p4_compile_probe.sha256(p) for p in sorted(paths)}


def identity(config):
    image = config["image"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("image must be an immutable sha256 ID")
    actual = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()
    if actual != image:
        raise ValueError("image identity mismatch")
    install = ROOT / ".cache/p4c-tofino/install"
    compiler = [p for p in install.rglob("*") if p.is_file() and
                (p.parent == install / "bin" or p.suffix in {".p4", ".py", ".cfg"})]
    if not compiler:
        raise ValueError("compiler installation missing")
    return {"image": actual, "compiler": hashes(compiler, install)}


def provenance(config_path, config):
    paths = [Path(__file__), GENERATOR, Path(p4_compile_probe.__file__),
             Path(jev.__file__), Path(codex_advisor.__file__), ROOT / "docs/p4-fit-advice-v1.md"]
    return {"config_sha256": p4_compile_probe.sha256(config_path),
            "code": hashes(paths, ROOT), "toolchain": identity(config)}


def normalize(source):
    return "".join(" " * len(line.rstrip("\n")) + "\n" if line.lstrip().startswith("@") else line
                   for line in source.splitlines(keepends=True))


def freeze(config_path, output, config):
    current = provenance(config_path, config)
    frozen = output / "freeze.json"
    if frozen.exists():
        value = read(frozen)
        if value["provenance"] != current or value["sources"] != hashes((output / "sources").rglob("*.*"), output):
            raise ValueError("frozen provenance or source drift; use a separately identified experiment")
        return
    if output.exists() and any(output.iterdir()):
        raise ValueError("nonempty output has no freeze; preserve it and use a new directory")
    for case in config["cases"]:
        destination = output / "sources" / case["id"]
        command = [sys.executable, str(GENERATOR), "--output", str(destination), "--policy", "all", "--stable-layout"]
        for name in ("depth", "sides", "chain_entries", "side_entries", "tenant_bits"):
            command += ["--" + name.replace("_", "-"), str(case[name])]
        subprocess.run(command, check=True, capture_output=True, text=True)
        manifest = read(destination / "manifest.json")
        baseline = normalize((destination / manifest["files"]["default"]["file"]).read_text())
        if len(manifest["files"]) != 25 or any(normalize((destination / f["file"]).read_text()) != baseline for f in manifest["files"].values()):
            raise ValueError("generator did not preserve the frozen annotation-only contract")
    save(frozen, {"provenance": current, "sources": hashes((output / "sources").rglob("*.*"), output)})


def table_context(directory):
    tables = []
    for path in sorted(directory.rglob("context.json")):
        for table in read(path).get("tables", []):
            if table.get("table_type") != "match":
                continue
            stages = table.get("match_attributes", {}).get("stage_tables", [])
            tables.append({"name": table["name"], "size": table.get("size"), "stages": [
                {k: stage[k] for k in ("stage_number", "size", "stage_table_type", "pack_format") if k in stage}
                for stage in stages]})
    return tables


def without_annotations(value):
    if isinstance(value, dict):
        return {k: without_annotations(v) for k, v in value.items() if k != "annotations"}
    if isinstance(value, list):
        return [without_annotations(v) for v in value]
    return value


def validation(case, directory, baseline):
    tables = {t["name"]: t for t in table_context(directory)}
    errors = []
    for group, count, size in (("chain", case["depth"], case["chain_entries"]), ("side", case["sides"], case["side_entries"])):
        for index in range(count):
            table = tables.get(f"Ingress.{group}_{index}", {})
            if table.get("size") != size or sum(s.get("size", 0) for s in table.get("stages", [])) < size:
                errors.append(f"capacity:{group}_{index}")
    reference, candidate = list(baseline.rglob("bfrt.json")), list(directory.rglob("bfrt.json"))
    bfrt = "unavailable"
    if len(reference) == len(candidate) == 1:
        bfrt = "equal" if without_annotations(read(reference[0])) == without_annotations(read(candidate[0])) else "different"
        if bfrt == "different":
            errors.append("bfrt_changed")
    if bfrt == "unavailable":
        errors.append("interface_unverified")
    return {"eligible_fit": not errors, "errors": errors, "bfrt_comparison": bfrt}


def compile_policy(output, config, case, policy, manifest):
    directory = output / "probes" / case["id"] / policy
    report_path = directory / "probe.json"
    source = output / "sources" / case["id"] / manifest["files"][policy]["file"]
    receipt = directory / "receipt.json"
    if report_path.exists():
        if not receipt.exists() or read(receipt) != hashes((p for p in directory.rglob("*") if p.is_file() and p != receipt), directory):
            raise ValueError(f"cached compiler evidence drift: {directory}")
        report = read(report_path)
    else:
        if directory.exists():
            raise ValueError(f"incomplete compilation needs inspection: {directory}")
        report = p4_compile_probe.compile_case(source, directory, config["image"], config["compile_timeout_seconds"])
        save(receipt, hashes((p for p in directory.rglob("*") if p.is_file()), directory))
    frozen = read(output / "freeze.json")["provenance"]
    compiler = frozen["toolchain"]["compiler"]
    expected = {"image_id": config["image"],
                "probe_sha256": frozen["code"][str(Path(p4_compile_probe.__file__).relative_to(ROOT))],
                "input_sha256": {str(source.relative_to(ROOT)): p4_compile_probe.sha256(source)},
                "compiler_executable_sha256": {Path(k).name: v for k, v in compiler.items() if Path(k).parent == Path("bin")},
                "compiler_support_sha256": {k: v for k, v in compiler.items() if Path(k).suffix in {".p4", ".py", ".cfg"}}}
    if any(report.get(k) != v for k, v in expected.items()):
        raise ValueError("probe identity does not match frozen inputs")
    checked = {"eligible_fit": False, "errors": [], "bfrt_comparison": "not_applicable"}
    if report["compiler_reported_fit"]:
        checked = validation(case, directory, output / "probes" / case["id"] / "default")
    return {"policy": policy, "probe": str(report_path.relative_to(output)),
            "status": "timeout" if report["timed_out"] else "fit" if report["compiler_reported_fit"] else "compiler_failure",
            **checked}


def feedback(output, result):
    if "probe" not in result:
        return {k: result[k] for k in ("provider", "error") if k in result}
    directory = (output / result["probe"]).parent
    stderr = directory / "stderr.log"
    return {"policy": result["policy"], "status": result["status"],
            "eligible_fit": result["eligible_fit"], "validation_errors": result["errors"],
            "diagnostics": stderr.read_text()[-24000:] if stderr.exists() else "",
            "tables": table_context(directory)}


def packet(output, case, manifest, history):
    attempted = {step.get("policy") for step in history}
    menu = [name for name in manifest["files"] if name != "default" and name not in attempted]
    actions, counts, packs = [], [], []
    for index, name in enumerate(menu):
        policy = manifest["files"][name]["policy"]
        edits = [f"Add @{key}({value}) to every {group}_N table" for group, values in policy.items() for key, value in values.items()]
        actions.append({"id": str(index), "description": name + ": " + "; ".join(edits)})
        counts.append(sum((case["depth"] if group == "chain" else case["sides"]) * len(values) for group, values in policy.items()))
        packs.append(next((values["pack"] for values in policy.values() if "pack" in values), 0))
    context = {"source": (output / "sources" / case["id"] / manifest["files"]["default"]["file"]).read_text(),
               "history": [feedback(output, step) for step in history]}
    observation = {"task": "Choose one annotation-only candidate to make this Tofino program fit while retaining capacities and semantics. Pack values request entries per wide table word (not per SRAM unit); placement_priority controls placement preference; immediate(0) disables immediate action data. Compiler failures are not proofs of impossibility. Use only the supplied evidence.\n" + json.dumps(context),
                   "features": {"annotation_count": counts, "pack_value": packs},
                   "feature_descriptions": {"annotation_count": "Number of inserted annotation lines", "pack_value": "Requested pack value, or 0 when absent"}, "actions": actions}
    return observation, dict(zip((a["id"] for a in actions), menu, strict=True))


def decide(output, config, case, arm, index, manifest, history, path):
    if path.exists():
        decision = read(path)
        if decision.get("status") == "provider_started":
            decision = {"provider": decision["provider"], "error": "interrupted_provider_call_usage_unknown"}
            save(path, decision)
        return decision
    observation, mapping = packet(output, case, manifest, history)
    save(path.with_name(path.stem + "-packet.json"), {"observation": observation, "action_mapping": mapping})
    provider = arm if arm in ("jev", "codex") else ("jev" if index == 1 else "codex") if arm == "cascade" and index else "mechanical"
    if provider == "mechanical":
        if arm == "random":
            choices = random.Random(f'{config["random_seed"]}:{case["id"]}').sample([n for n in manifest["files"] if n != "default"], 3)
        else:
            choices = FIXED.get(arm, ["side-pack-3"])
        decision = {"provider": provider, "policy": choices[index]}
    else:
        save(path, {"status": "provider_started", "provider": provider})
        try:
            response = (jev if provider == "jev" else codex_advisor).choose(observation)
            action = response.get("chosen_action")
            decision = {"provider": provider, "response": response}
            if not isinstance(action, str) or action not in mapping:
                decision["error"] = "invalid_or_ineligible_action"
            else:
                decision["policy"] = mapping[action]
        except (jev.JevUnavailable, codex_advisor.CodexUnavailable) as exc:
            decision = {"provider": provider, "error": exc.reason, "usage": "unknown"}
    save(path, decision)  # Durable before any candidate compile.
    return decision


def summarize(output, config, state):
    results = state["cases"]
    arms = {}
    for arm in config["arms"]:
        records = [case["arms"][arm] for case in results.values() if arm in case["arms"]]
        arms[arm] = {"cases": len(records), "rescues": sum(r["fit"] and not r["baseline_fit"] for r in records),
                     "virtual_queries": sum(len([s for s in r["steps"] if "probe" in s]) for r in records),
                     "attempts": sum(len(r["steps"]) for r in records),
                     "provider_failures": sum("error" in s for r in records for s in r["steps"]),
                     "reported_usage": {key: sum(s.get("response", {}).get("usage", {}).get(key, 0) for r in records for s in r["steps"]) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")},
                     "complete_cases": sum(r["complete"] for r in records)}
    paired = {}
    for a in config["arms"]:
        for b in config["arms"]:
            if a >= b:
                continue
            pairs = [(c["arms"][a]["fit"], c["arms"][b]["fit"]) for c in results.values() if a in c["arms"] and b in c["arms"] and c["arms"][a]["complete"] and c["arms"][b]["complete"]]
            wins, losses = sum(x and not y for x, y in pairs), sum(y and not x for x, y in pairs)
            paired[f"{a}_vs_{b}"] = {"cases": len(pairs), "wins": wins, "losses": losses, "net": wins - losses}
    union_cases = [c for c in results.values() if all(a in c["arms"] and c["arms"][a]["complete"] for a in ("packing", "priority", "random"))]
    mechanical = [a for a in ("packing", "priority", "random") if a in arms]
    best = max(mechanical, key=lambda a: arms[a]["rescues"]) if len(mechanical) == 3 and all(arms[a]["complete_cases"] == len(config["cases"]) for a in mechanical) else None
    return {"experiment_id": config["experiment_id"], "arms": arms, "paired": paired,
            "best_single_mechanical": best,
            "physical_unique_compiles": len(list((output / "probes").rglob("probe.json"))),
            "shared_baseline_queries": len(results),
            "mechanical_union": {"cases": len(union_cases), "rescues": sum(any(c["arms"][a]["fit"] for a in ("packing", "priority", "random")) and not c["baseline"]["eligible_fit"] for c in union_cases)},
            "cascade_codex_increment": sum(any(s.get("provider") == "codex" and s.get("eligible_fit") for s in c["arms"].get("cascade", {}).get("steps", [])) for c in results.values()),
            "interpretation": "Descriptive compiler-reported fit pilot; no wall-clock or hardware correctness claim."}


def run(config_path, output, arms=None, cases=None):
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_name(output.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_locked(config_path, output, arms, cases)


def run_locked(config_path, output, arms=None, cases=None):
    config_path, output = config_path.resolve(), output.resolve()
    config = read(config_path)
    if config["attempts_per_arm"] != 3 or config["compile_timeout_seconds"] != 120:
        raise ValueError("v1 requires three attempts and a 120-second compiler timeout")
    ids = [case["id"] for case in config["cases"]]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids):
        raise ValueError("case IDs must be unique safe path components")
    selected_arms, selected_cases = arms or config["arms"], cases or ids
    if not set(selected_arms) <= set(config["arms"]) <= set(ARMS) or not set(selected_cases) <= set(ids):
        raise ValueError("slice not in frozen config")
    freeze(config_path, output, config)
    state_path = output / "state.json"
    state = read(state_path) if state_path.exists() else {"cases": {}}
    # Validate all recorded evidence, including arms excluded from this resume slice.
    checked = {}
    for case in config["cases"]:
        previous = state["cases"].get(case["id"], {})
        records = [previous["baseline"]] if "baseline" in previous else []
        records += [step for arm in previous.get("arms", {}).values() for step in arm["steps"] if "probe" in step]
        manifest = read(output / "sources" / case["id"] / "manifest.json")
        for result in records:
            key = (case["id"], result["policy"])
            if key not in checked:
                if not (output / result["probe"]).is_file():
                    raise ValueError("recorded compiler evidence is missing")
                checked[key] = compile_policy(output, config, case, result["policy"], manifest)
            if any(result.get(k) != v for k, v in checked[key].items()):
                raise ValueError("recorded compiler outcome drift")
        for arm in previous.get("arms", {}).values():
            baseline_fit = previous["baseline"]["eligible_fit"]
            fit = baseline_fit or any(step.get("eligible_fit", False) for step in arm["steps"])
            if arm["baseline_fit"] != baseline_fit or arm["fit"] != fit:
                raise ValueError("recorded aggregate outcome drift")
    for case in config["cases"]:
        if case["id"] not in selected_cases:
            continue
        manifest = read(output / "sources" / case["id"] / "manifest.json")
        record = state["cases"].setdefault(case["id"], {"arms": {}})
        record["baseline"] = compile_policy(output, config, case, "default", manifest)
        save(state_path, state)
        for arm in selected_arms:
            result = record["arms"].setdefault(arm, {"steps": [], "baseline_fit": record["baseline"]["eligible_fit"], "fit": record["baseline"]["eligible_fit"], "complete": False})
            for index in range(3):
                if result["fit"]:
                    break
                if index < len(result["steps"]):
                    continue
                path = output / "decisions" / case["id"] / arm / f"{index + 1}.json"
                decision = decide(output, config, case, arm, index, manifest, [record["baseline"], *result["steps"]], path)
                step = dict(decision)
                if "policy" in decision:
                    step.update(compile_policy(output, config, case, decision["policy"], manifest))
                result["steps"].append(step)
                result["fit"] = step.get("eligible_fit", False)
                save(state_path, state)
                save(output / "summary.json", summarize(output, config, state))
            result["complete"] = True
            save(state_path, state)
            save(output / "summary.json", summarize(output, config, state))
    return summarize(output, config, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arms", help="Comma-separated frozen arms")
    parser.add_argument("--cases", help="Comma-separated frozen case IDs")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output, args.arms.split(",") if args.arms else None, args.cases.split(",") if args.cases else None), indent=2))


if __name__ == "__main__":
    main()
