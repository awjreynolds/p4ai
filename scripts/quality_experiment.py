"""Run the frozen held-out decision-quality and adaptation screening."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
from pathlib import Path
from typing import Any

from scripts import (
    benchmark,
    codex_advisor,
    experiment,
    jev,
    quality_analysis,
    quality_corpus,
)

ROOT = Path(__file__).resolve().parents[1]
OBJECTIVE = (
    " Minimize runtime of the resulting native ARM64 code, considering spill, reload "
    "and rematerialization costs. Use only this compiler feature snapshot. "
    "There are no measured candidate outcomes in this packet."
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def candidate_features(packet: dict, action: str) -> dict[str, float]:
    index = [a["id"] for a in packet["actions"]].index(str(action))
    return {key: float(values[index]) for key, values in packet["features"].items()}


def simple_choices(packet: dict, seed: str) -> dict[str, int]:
    ids = [int(a["id"]) for a in packet["actions"]]
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    density = packet["features"]["use_def_density"]
    return {
        "first": ids[0],
        "last": ids[-1],
        "uniform": rng.choice(ids),
        "density": ids[min(range(len(ids)), key=lambda i: density[i])],
    }


def code_identity(path: Path) -> str:
    """Ignore object layout metadata, retain sections, relocations and symbols."""
    data = path.read_bytes()
    if len(data) < 32 or struct.unpack_from("<I", data)[0] != 0xFEEDFACF:
        raise ValueError("expected native little-endian Mach-O 64 object")
    count = struct.unpack_from("<I", data, 16)[0]
    offset = 32
    parts = []
    for _ in range(count):
        command, size = struct.unpack_from("<II", data, offset)
        if size < 8 or offset + size > len(data):
            raise ValueError("invalid Mach-O load command")
        if command == 0x19:  # LC_SEGMENT_64
            nsects = struct.unpack_from("<I", data, offset + 64)[0]
            for i in range(nsects):
                section = offset + 72 + i * 80
                name = data[section : section + 32]
                length, fileoff = struct.unpack_from("<QI", data, section + 40)
                reloff, nreloc, flags = struct.unpack_from("<III", data, section + 56)
                if flags & 0xFF in (1, 12, 18):  # zero-fill sections have no file bytes
                    content = b"zero-fill:" + str(length).encode()
                else:
                    if fileoff + length > len(data):
                        raise ValueError("invalid Mach-O section")
                    content = data[fileoff : fileoff + length]
                if reloff + 8 * nreloc > len(data):
                    raise ValueError("invalid Mach-O relocation table")
                parts.append(name + content + data[reloff : reloff + 8 * nreloc])
        elif command == 2:  # LC_SYMTAB binds relocations to their symbol identities
            symoff, nsyms, stroff, strsize = struct.unpack_from(
                "<IIII", data, offset + 8
            )
            parts.append(
                data[symoff : symoff + nsyms * 16] + data[stroff : stroff + strsize]
            )
        offset += size
    if not parts:
        raise ValueError("object has no sections")
    return hashlib.sha256(b"".join(parts)).hexdigest()


def query_models(packet: dict, path: Path) -> dict:
    packet = json.loads(json.dumps(packet))
    packet["task"] += OBJECTIVE
    fingerprint = hashlib.sha256(
        json.dumps(packet, sort_keys=True).encode()
    ).hexdigest()
    if path.exists():
        result = load(path)
        if result["packet_sha256"] != fingerprint:
            raise ValueError("cached provider packet changed")
    else:
        result = {
            "packet_sha256": fingerprint,
            "packet": packet,
            "responses": {},
            "attempts": [],
        }
    policy = experiment.OneInterventionPolicy("jev")
    for name, choose in [("jev", jev.choose), ("codex", codex_advisor.choose)]:
        if name in result["responses"]:
            continue
        policy.provider_attempts = []
        response = policy._ask(name, choose, packet, packet["actions"])
        result["attempts"].extend(policy.provider_attempts)
        if response is not None:
            result["responses"][name] = response
        save(path, result)
        if response is None:
            raise RuntimeError(
                f"{name} did not return valid advice; experiment incomplete"
            )
    return result


def label_rows(state: dict) -> list[dict]:
    packet = state["packet"]
    medians = state["benchmark"]["summaries"]
    baseline = medians[state["timing_labels"]["first"]]["median_ns"]
    return [
        {
            "features": candidate_features(packet, action),
            "loss": medians[label]["median_ns"] / baseline,
        }
        for action, label in state["action_timing_labels"].items()
    ]


def calibration_rows(states: list[dict]) -> list[dict]:
    return [
        {
            "confidence": state["responses"]["jev"]["confidence"],
            "jev_ns": state["benchmark"]["summaries"][state["timing_labels"]["jev"]][
                "median_ns"
            ],
            "codex_ns": state["benchmark"]["summaries"][
                state["timing_labels"]["codex"]
            ]["median_ns"],
        }
        for state in states
    ]


def policy_rounds(
    state: dict, routing: dict, adapted_routing: dict | None = None
) -> dict:
    confidence = state["responses"]["jev"]["confidence"]
    labels = dict(state["timing_labels"])
    labels["cascade"] = labels[
        "codex" if quality_analysis.select_route(routing, confidence) else "jev"
    ]
    if adapted_routing is not None:
        labels["adapted_cascade"] = labels[
            "codex"
            if quality_analysis.select_route(adapted_routing, confidence)
            else "jev"
        ]
    records = []
    for row in state["benchmark"]["round_records"]:
        records.append(
            {
                "round": row["round"],
                "measurements": {
                    policy: row["measurements"][label]
                    for policy, label in labels.items()
                },
            }
        )
    return {
        "family": state["family"],
        "state": state["id"],
        "round_records": records,
        "escalated": labels["cascade"] == labels["codex"]
        if labels["codex"] != labels["jev"]
        else quality_analysis.select_route(routing, confidence),
        "confidence": confidence,
        "adapted_escalated": quality_analysis.select_route(adapted_routing, confidence)
        if adapted_routing is not None
        else None,
    }


def evaluate_states(
    states: list[dict], routing: dict, adapted_routing: dict | None = None
) -> dict:
    records = [policy_rounds(s, routing, adapted_routing) for s in states]
    pairs = [
        ("jev", "cascade"),
        ("nearest_neighbor", "cascade"),
        ("stock", "cascade"),
        ("first", "cascade"),
        ("jev", "codex"),
        ("density", "cascade"),
    ]
    if adapted_routing is not None:
        pairs.extend(
            [
                ("cascade", "adapted_cascade"),
                ("nearest_neighbor", "adapted_nearest_neighbor"),
                ("adapted_nearest_neighbor", "adapted_cascade"),
            ]
        )
    comparisons = {
        f"{candidate}_vs_{reference}": quality_analysis.paired_metrics(
            records, reference=reference, candidate=candidate
        )
        for reference, candidate in pairs
    }
    return {
        "states": len(states),
        "families": sorted({s["family"] for s in states}),
        "escalation_rate": sum(r["escalated"] for r in records) / len(records),
        "adapted_escalation_rate": sum(r["adapted_escalated"] for r in records)
        / len(records)
        if adapted_routing is not None
        else None,
        "comparisons": comparisons,
    }


def screening_verdict(summary: dict) -> dict:
    comparisons = summary["comparisons"]
    gain = comparisons["cascade_vs_jev"]
    cheap = comparisons["cascade_vs_nearest_neighbor"]
    noise = max(
        0.02,
        math.expm1(gain["maximum_state_mad_log_ratio"]),
        math.expm1(cheap["maximum_state_mad_log_ratio"]),
    )
    requirements = {
        "gain_over_jev_beyond_noise": gain["geometric_mean_ratio"] < 1 - noise,
        "both_family_directions_improve": all(
            f["geometric_mean_ratio"] < 1 for f in gain["families"].values()
        ),
        "competitive_with_cheap_selector": cheap["geometric_mean_ratio"] <= 1 + noise,
        "within_escalation_budget": summary["escalation_rate"] <= 0.25,
    }
    return {
        "screening_success": all(requirements.values()),
        "requirements": requirements,
        "noise_floor_fraction": noise,
        "interpretation": "Conditional single-decision synthetic screening; not general compiler performance or maintenance proof.",
    }


def usage_summary(states: list[dict], routing: dict) -> dict:
    inputs = outputs = 0
    by_provider = {}
    for state in states:
        for name, response in state["responses"].items():
            usage = response.get("usage", {})
            entry = by_provider.setdefault(
                name,
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0.0},
            )
            entry["calls"] += 1
            entry["latency_ms"] += response.get("latency_ms", 0)
            for field in ("input_tokens", "output_tokens"):
                entry[field] += usage.get(field, 0)
            inputs += usage.get("input_tokens", 0)
            outputs += usage.get("output_tokens", 0)
    return {
        "input_tokens": inputs,
        "output_tokens": outputs,
        "by_provider": by_provider,
        "scope": "Successful screening responses including counterfactual labels; not estimated production cascade usage. Failed attempts remain in per-state logs.",
    }


def run(args: argparse.Namespace) -> dict:
    from scripts import counterfactual

    design = load(args.design)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sources = [args.design, *sorted((ROOT / "scripts").glob("*.py"))]
    manifest = {
        "files": {str(p.relative_to(ROOT)): digest(p) for p in sources},
        "validation_report_sha256": digest(args.validation_report),
        "objective": OBJECTIVE,
    }
    manifest_path = out / "manifest.json"
    if manifest_path.exists() and load(manifest_path) != manifest:
        raise ValueError(
            "experiment implementation changed; use a new output directory"
        )
    save(manifest_path, manifest)
    all_states = []
    fitting = []
    calibration = []
    adaptation = []
    nearest = None
    routing = None
    adapted_nearest = None
    adapted_routing = None
    phases = [
        ("fit", design["families"]["fit"], design["base_variants"]),
        ("calibrate", design["families"]["calibrate"], design["base_variants"]),
        ("test", design["families"]["test"], design["base_variants"]),
        ("adapt", design["families"]["test"], [design["shift_variants"][0]]),
        ("shift_test", design["families"]["test"], [design["shift_variants"][1]]),
    ]
    for phase, families, variants in phases:
        for family in families:
            for variant in variants:
                case_id = f"{phase}-{family}-{variant['lanes']}"
                case_dir = out / case_id
                files = quality_corpus.generate_case(
                    case_dir / "source", family, variant["lanes"], variant["rounds"]
                )
                report_path = case_dir / "case" / "report.json"
                print(f"case {case_id}", flush=True)
                if report_path.exists():
                    case = load(report_path)
                else:
                    case = counterfactual.collect_case(
                        args.validation_report,
                        files["kernel"],
                        files["driver"],
                        case_dir / "case",
                        site_quantiles=tuple(design["site_quantiles"]),
                        iterations=3,
                    )
                # The case API exposes correctness-checked stock/first executable and object paths.
                if case["status"] != "passed":
                    raise RuntimeError(f"baseline failed: {case_id}")
                stock_exe = Path(case["paths"]["stock_executable"])
                pilot = benchmark._run_one(
                    "stock", stock_exe, 10000, "iteration calibration"
                )
                iterations = max(
                    1000,
                    min(
                        10000000,
                        math.ceil(
                            10000
                            * design["target_sample_ms"]
                            * 1e6
                            / pilot["elapsed_ns"]
                        ),
                    ),
                )
                for site in case["baseline"]["first"]["sites"]:
                    site_dir = case_dir / f"site-{site['site_index']}"
                    state_path = site_dir / "state.json"
                    if state_path.exists():
                        state = load(state_path)
                    else:
                        packet = site["provider_packet"]
                        providers = query_models(packet, site_dir / "providers.json")
                        choices = simple_choices(
                            packet, f"{design['seed']}:{case_id}:{site['site_index']}"
                        )
                        responses = providers["responses"]
                        choices.update(
                            {
                                name: int(response["chosen_action"])
                                for name, response in responses.items()
                            }
                        )
                        if nearest is not None:
                            choices["nearest_neighbor"] = int(
                                quality_analysis.score_actions(
                                    nearest, packet["features"], packet["actions"]
                                )["action_id"]
                            )
                        if adapted_nearest is not None:
                            choices["adapted_nearest_neighbor"] = int(
                                quality_analysis.score_actions(
                                    adapted_nearest,
                                    packet["features"],
                                    packet["actions"],
                                )["action_id"]
                            )
                        unique = {}
                        for action in sorted(set(choices.values())):
                            action_dir = site_dir / f"action-{action}"
                            result_path = action_dir / "report.json"
                            if action == choices["first"]:
                                result = {
                                    "status": "passed",
                                    "paths": {
                                        "object": case["paths"]["first_object"],
                                        "executable": case["paths"]["first_executable"],
                                    },
                                }
                            elif result_path.exists():
                                result = load(result_path)
                            else:
                                result = counterfactual.compile_choice(
                                    case, site["site_index"], action, action_dir
                                )
                            if result["status"] != "passed":
                                raise RuntimeError(
                                    f"counterfactual failed: {case_id}, {action}"
                                )
                            unique[action] = result
                        binaries = [("stock", stock_exe)]
                        identities = {
                            code_identity(Path(case["paths"]["stock_object"])): "stock"
                        }
                        action_labels = {}
                        for action, result in unique.items():
                            identity = code_identity(Path(result["paths"]["object"]))
                            if identity not in identities:
                                label = f"action_{action}"
                                binaries.append(
                                    (label, Path(result["paths"]["executable"]))
                                )
                                identities[identity] = label
                            action_labels[str(action)] = identities[identity]
                        measured = benchmark.run_benchmark(
                            binaries,
                            iterations=iterations,
                            rounds=design["benchmark_rounds"],
                            seed=design["seed"] + site["site_index"],
                        )
                        state = {
                            "id": f"{case_id}:{site['site_index']}",
                            "family": family,
                            "phase": phase,
                            "variant": variant,
                            "site": site,
                            "packet": packet,
                            "responses": responses,
                            "provider_attempts": providers["attempts"],
                            "choices": choices,
                            "benchmark": measured,
                            "timing_labels": {
                                "stock": "stock",
                                **{
                                    k: action_labels[str(v)] for k, v in choices.items()
                                },
                            },
                            "action_timing_labels": action_labels,
                            "unique_code_outcomes": len(binaries),
                        }
                        save(state_path, state)
                    all_states.append(state)
                    if phase == "fit":
                        fitting.extend(label_rows(state))
                    elif phase == "calibrate":
                        calibration.append(state)
                    elif phase == "adapt":
                        adaptation.append(state)
                    print(
                        f"  completed {state['id']} actions={len(state['action_timing_labels'])} code={state['unique_code_outcomes']}",
                        flush=True,
                    )
        if phase == "fit":
            nearest = quality_analysis.fit_nearest_neighbor(fitting)
            save(out / "nearest-neighbor.json", nearest)
        elif phase == "calibrate":
            calibrated = quality_analysis.calibrate_threshold(
                calibration_rows(calibration),
                max_escalation_fraction=design["calibration_escalation_budget"],
            )
            routing = calibrated["chosen"]
            save(out / "routing.json", calibrated)
            print(f"Frozen routing: {routing}", flush=True)
        elif phase == "adapt":
            extra = [row for state in adaptation for row in label_rows(state)]
            adapted_nearest = quality_analysis.fit_nearest_neighbor(fitting + extra)
            adapted = quality_analysis.calibrate_threshold(
                calibration_rows(adaptation),
                max_escalation_fraction=design["calibration_escalation_budget"],
            )
            adapted_routing = adapted["chosen"]
            save(out / "adapted-nearest-neighbor.json", adapted_nearest)
            save(out / "adapted-routing.json", adapted)
    results = {
        "status": "completed",
        "manifest": manifest,
        "design": design,
        "routing": routing,
        "adapted_routing": adapted_routing,
        "fitting_labels": len(fitting),
        "adaptation_labels": sum(len(s["action_timing_labels"]) for s in adaptation),
        "test": evaluate_states(
            [s for s in all_states if s["phase"] == "test"], routing
        ),
        "shift": evaluate_states(
            [s for s in all_states if s["phase"] == "shift_test"],
            routing,
            adapted_routing,
        ),
        "states": all_states,
    }
    results["test"]["verdict"] = screening_verdict(results["test"])
    results["shift"]["verdict"] = screening_verdict(results["shift"])
    results["usage"] = usage_summary(all_states, routing)
    results["maintenance"] = {
        "frozen_policy_source_edits": 0,
        "frozen_prompt_edits": 0,
        "adaptation_policy_source_edits": 0,
        "adaptation_prompt_edits": 0,
        "initial_nearest_neighbor_examples": len(fitting),
        "added_nearest_neighbor_examples": results["adaptation_labels"],
        "routing_rule_changed": (routing["mode"], routing["threshold"])
        != (adapted_routing["mode"], adapted_routing["threshold"]),
        "shared_adaptation_states": len(adaptation),
        "shared_adaptation_action_labels": results["adaptation_labels"],
        "reduced_maintenance_established": False,
        "limitation": "Automated within-family pressure extrapolation; no human maintenance time measured. Counts of stored examples and one threshold are not comparable measures of engineering effort.",
    }
    save(out / "report.json", results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design", type=Path, default=ROOT / "experiments/escalation-quality-v1.json"
    )
    parser.add_argument("--validation-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
