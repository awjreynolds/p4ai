"""Export a verified completed trial without provider calls, compilation, or oracle data."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import p4_multirepair_experiment as trial

ROOT = Path(__file__).resolve().parents[1]
sha256 = trial.p4_compile_probe.sha256


def safe(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT) or "sealed" in path.relative_to(ROOT).parts:
        raise ValueError("export cannot access paths outside the repository or sealed data")
    return path


def read(path):
    return trial.read(safe(path))


def sanitize(value):
    """Normalize host paths; do not embed transport credentials."""
    if isinstance(value, dict):
        return {sanitize(k): "[REDACTED]" if k.lower() in {"authorization", "api_key", "access_token", "password", "secret"} else sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        value = value.replace(str(ROOT), "$REPO").replace(str(Path.home()), "$HOME")
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
    return value


def file_record(path, text=False):
    path = safe(path)
    return {"sha256": sha256(path), "text" if text else "json": path.read_text() if text else read(path)}



def preflight_references(state, output, manifest):
    def reference(name):
        path = safe(output / name)
        if not path.is_relative_to(output):
            raise ValueError("state reference must remain inside trial output")
        return path

    policies = {case["id"]: {"default", *(c["id"] for c in case["candidates"])} for case in manifest["cases"]}
    for case_id, record in state["cases"].items():
        if case_id not in policies:
            raise ValueError("state contains an undeclared case")
        records = ([record["baseline"]] if "baseline" in record else []) + record.get("prefix", [])
        records += [step for steps in record.get("arms", {}).values() for step in steps]
        for step in records:
            if "decision" in step:
                decision = reference(step["decision"])
                reference(decision.with_name(decision.stem + "-packet.json"))
            if "probe" in step:
                reference(step["probe"])
            if "policy" in step and step["policy"] not in policies[case_id]:
                raise ValueError("state contains an undeclared policy")


def verify_run(config_path, output):
    for path in output.rglob("*"):
        if not safe(path).is_relative_to(output):
            raise ValueError("every artifact must remain inside trial output")
    stored = read(output / "summary.json")
    if stored.get("complete") is not True:
        raise ValueError("only completed trials may be exported")
    frozen = read(output / "freeze.json")
    if frozen["provenance"]["inputs"].get(str(config_path.relative_to(ROOT))) != sha256(config_path):
        raise ValueError("requested config is not the frozen config")
    for section in ("inputs", "code"):
        for name, expected in frozen["provenance"][section].items():
            if sha256(safe(ROOT / name)) != expected:
                raise ValueError("frozen input/code drift")
    sources = trial.fit.hashes((output / "sources").rglob("*.p4"), output)
    if sources != frozen["sources"]:
        raise ValueError("frozen source drift")
    config = read(config_path)
    for key in ("policy", "knowledge", "design", "corpus_manifest"):
        safe(ROOT / config[key])
    manifest_path = safe(ROOT / config["corpus_manifest"])
    manifest = read(manifest_path)
    for case in manifest["cases"]:
        safe(manifest_path.parent / case["default"])
        for candidate in case["candidates"]:
            safe(manifest_path.parent / candidate["source"])
    preflight_references(read(output / "state.json"), output, manifest)
    experiment = trial.Experiment(config_path, output)
    experiment.validate_state()  # Only must_exist probes; no compile or identity subprocess.
    calculated = experiment.summary()
    if calculated.get("complete") is not True or calculated != stored:
        raise ValueError("stored summary is incomplete or differs from verified state")
    return experiment, frozen


def probe_record(output, relative, blobs):
    path = safe(output / relative)
    directory = path.parent
    receipt = read(directory / "receipt.json")
    current = trial.fit.hashes((p for p in directory.rglob("*") if p.is_file() and p.name != "receipt.json"), directory)
    if current != receipt:
        raise ValueError("compiler receipt drift")
    contexts = []
    for context_path in directory.rglob("context.json"):
        for table in read(context_path).get("tables", []):
            attributes = table.get("match_attributes", {})
            contexts.append({k: table[k] for k in ("name", "table_type", "size", "action_profile", "selection_table_refs", "action_data_table_refs") if k in table} | {
                "match_attributes": {"match_type": attributes.get("match_type"), "stage_tables": [
                    {k: s[k] for k in ("stage_number", "logical_table_id", "stage_table_type", "size") if k in s}
                    for s in attributes.get("stage_tables", [])]}})
    schemas = []
    for schema_path in directory.rglob("bfrt.json"):
        digest = sha256(safe(schema_path))
        blobs.setdefault(digest, read(schema_path))
        schemas.append({"path": str(schema_path.relative_to(directory)), "sha256": digest})
    return {"report": file_record(path), "receipt": file_record(directory / "receipt.json"),
            "stdout": file_record(directory / "stdout.log", text=True), "stderr": file_record(directory / "stderr.log", text=True),
            "manifests": {str(p.relative_to(directory)): file_record(p) for p in directory.rglob("manifest.json")},
            "capacity_context": contexts, "bfrt_blobs": schemas}


def build_bundle(config_path, output):
    experiment, frozen = verify_run(config_path, output)
    decisions, packets, probes, schemas, cases = {}, {}, {}, {}, []
    usage = {provider: {} for provider in ("jev", "codex")}
    for case in experiment.manifest["cases"]:
        state = experiment.state["cases"][case["id"]]
        record = {"id": case["id"], "structure": case["structure"], "requirements": case["requirements"], "baseline": state["baseline"], "prefix": state["prefix"], "arms": {}}
        records = [state["baseline"], *state["prefix"]]
        for arm, steps in state["arms"].items():
            accepted = state["baseline"]["eligible_fit"] or any(s.get("eligible_fit") for s in steps)
            raw = state["baseline"]["compiler_reported_fit"] or any(s.get("compiler_reported_fit") for s in steps)
            record["arms"][arm] = {"accepted_fit": accepted, "raw_fit": raw, "repaired": accepted and not state["baseline"]["eligible_fit"], "steps": steps}
            records += steps
        for step in records:
            if "probe" in step and step["probe"] not in probes:
                probes[step["probe"]] = probe_record(output, step["probe"], schemas)
            if "decision" in step and step["decision"] not in decisions:
                path = safe(output / step["decision"])
                decisions[step["decision"]] = file_record(path)
                packet = path.with_name(path.stem + "-packet.json")
                packets[str(packet.relative_to(output))] = file_record(packet)
                value = decisions[step["decision"]]["json"]
                if value["provider"] in usage:
                    for key, tokens in value.get("response", {}).get("usage", {}).items():
                        usage[value["provider"]][key] = usage[value["provider"]].get(key, 0) + tokens
        cases.append(record)
    decision_files = {str(p.relative_to(output)) for p in (output / "decisions").rglob("*.json") if not p.name.endswith("-packet.json")}
    probe_files = {str(p.relative_to(output)) for p in (output / "probes").rglob("probe.json")}
    if set(decisions) != decision_files or set(probes) != probe_files:
        raise ValueError("completed run contains unreferenced decisions or probes; inspect before export")
    bundle = {"schema_version": 1, "experiment_id": experiment.config["experiment_id"], "status": "complete",
              "encoding": {"paths": "Host paths normalized to $REPO/$HOME; hashes refer to original bytes.",
                           "scope": "Requests, responses, all source variants, compiler receipts, capacity/schema evidence and diagnostics. Binary bytes and full compiler IR excluded.",
                           "tokens": "Reasoning output and cached input are subsets; do not add them to total input plus output.",
                           "oracle": "No sealed qualification or oracle data read or embedded.",
                           "providers": "Normalized adapter responses only; raw HTTP bodies, Codex events and failed-call latencies were not retained."},
              "config": file_record(config_path), "policy": file_record(ROOT / experiment.config["policy"]),
              "knowledge": file_record(ROOT / experiment.config["knowledge"], text=True), "design": file_record(ROOT / experiment.config["design"], text=True),
              "corpus_manifest": file_record(experiment.manifest_path), "freeze": file_record(output / "freeze.json"),
              "state": file_record(output / "state.json"), "summary": file_record(output / "summary.json"),
              "sources": {str(p.relative_to(output)): file_record(p, text=True) for p in sorted((output / "sources").rglob("*.p4"))},
              "cases": cases, "decisions": decisions, "packets": packets, "compiler_probes": probes, "bfrt_blobs": schemas,
              "compiler_identity": frozen["provenance"]["toolchain"], "provider_usage": usage,
              "validation": {"frozen_inputs_sources_code_verified": True, "decisions_and_compiler_receipts_verified": True,
                             "summary_recomputed_from_completed_state": True, "shared_prefix_verified": True,
                             "provider_calls_or_compilation_performed_by_exporter": False}}
    return sanitize(bundle)


def markdown(bundle, json_name):
    summary = bundle["summary"]["json"]
    passed = summary["positive_gate"]
    lines = [f'# {bundle["experiment_id"]}', "", "The frozen descriptive escalation gate was " + ("met." if passed else "not met."), "",
             "This is compiler-reported resource fitting on synthetic programs. It does not establish hardware correctness, statistical superiority, or reduced policy maintenance.", "",
             f'The [self-contained evidence bundle]({json_name}) includes every request, normalized response, source variant, compiler receipt, and capacity/interface audit. No hidden qualification results are included.', "",
             "## Arms", "", "| Arm | Accepted repairs | Attempts | Virtual queries | Provider errors |", "| --- | ---: | ---: | ---: | ---: |"]
    for arm, data in summary["arms"].items():
        lines.append(f'| {arm} | {data["repairs"]} | {data["attempts"]} | {data["virtual_queries"]} | {data["provider_errors"]} |')
    lines += ["", "## Paired escalation comparison", "", "| Comparator | Wins | Losses | Net | Structures with wins |", "| --- | ---: | ---: | ---: | --- |"]
    for reference, data in summary["cascade_vs"].items():
        lines.append(f'| {reference} | {data["wins"]} | {data["losses"]} | {data["net"]} | {", ".join(data["winning_structures"]) or "none"} |')
    lines += ["", "The gate requires at least two net additional repairs and wins across at least two structures against each comparator. The escalation and cheap-continuation arms share the exact recorded mechanical/Jev prefix.", "",
              "## All case outcomes", "", "| Case | Structure | Arm | Choices or errors | Raw fit | Accepted fit | Rejections |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for case in bundle["cases"]:
        for arm, data in case["arms"].items():
            choices = " → ".join(s.get("policy", s.get("error", "unknown")) for s in data["steps"]) or "default fits"
            errors = "; ".join(error for step in data["steps"] for error in step.get("errors", [])) or "none"
            lines.append(f'| {case["id"]} | {case["structure"]} | {arm} | {choices} | {"Yes" if data["raw_fit"] else "No"} | {"Yes" if data["accepted_fit"] else "No"} | {errors} |')
    lines += ["", "## Evidence and usage", "", f'{summary["physical_unique_compiles"]} physical compilations, {summary["recorded_provider_attempts"]} recorded provider attempts, and {summary["interrupted_attempts_usage_unknown"]} interrupted attempts with unknown usage. Shared-prefix requests are counted once in provider totals; each arm retains its virtual query charges.', ""]
    for provider, usage in bundle["provider_usage"].items():
        lines.append(f'- {provider}: {usage.get("input_tokens", 0):,} input + {usage.get("output_tokens", 0):,} output tokens reported. Missing usage is unknown, not measured zero.')
    lines += ["", "Reasoning-output and cached-input counts in the bundle are subsets, not extra tokens. Only normalized provider responses were retained, not raw HTTP bodies, Codex events or failed-call latencies. Recorded elapsed times are not a latency comparison.", "",
              f'Immutable compiler image: `{bundle["compiler_identity"]["image"]}`. Frozen source, code, executable and support-file hashes are embedded. Only annotation metadata, ordering, and the numeric top-level ID of `pipe.snapshot.cfg` of type `SnapshotCfg` are excluded from BFRT comparison; application IDs remain checked.', "",
              "Curation cost and qualification evidence must be reported separately after the trial; this exporter does not inspect or infer an oracle.", ""]
    return "\n".join(lines)


def export_report(config_path, output, json_path, markdown_path):
    config_path, output, json_path, markdown_path = map(safe, (config_path, output, json_path, markdown_path))
    if json_path.is_relative_to(output) or markdown_path.is_relative_to(output) or json_path == markdown_path:
        raise ValueError("reports must have distinct destinations outside immutable run artifacts")
    with output.with_name(output.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        bundle = build_bundle(config_path, output)
        frozen = bundle["freeze"]["json"]["provenance"]
        protected = {safe(ROOT / name) for section in ("inputs", "code") for name in frozen[section]}
        if json_path in protected or markdown_path in protected or Path(__file__).resolve() in (json_path, markdown_path):
            raise ValueError("report destination would overwrite an input or implementation")
        trial.save(json_path, bundle)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown(bundle, os.path.relpath(json_path, markdown_path.parent)))
    return {"json": str(json_path), "markdown": str(markdown_path), "experiment_id": bundle["experiment_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Completed trial directory")
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_report(args.config, args.output, args.json, args.markdown), indent=2))


if __name__ == "__main__":
    main()
