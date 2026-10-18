#!/usr/bin/env python3
"""Generate fixed-position P4 candidates with distinct dependency/control structures."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_loader = importlib.util.spec_from_file_location("fit_scaffold", ROOT / "examples/p4-fit/generate.py")
assert _loader and _loader.loader
_scaffold = importlib.util.module_from_spec(_loader)
_loader.loader.exec_module(_scaffold)
POLICIES = {
    "default": {},
    **{f"{g}-pack-{n}": {g: {"pack": n}} for g in ("a", "b") for n in range(1, 10)},
    **{f"{g}-no-immediate": {g: {"immediate": 0}} for g in ("a", "b")},
    "all-no-immediate": {"a": {"immediate": 0}, "b": {"immediate": 0}},
    "a-priority": {"a": {"placement_priority": 10}},
    "b-priority": {"b": {"placement_priority": 10}},
    "a-later": {"a": {"placement_priority": -10}},
    "b-later": {"b": {"placement_priority": -10}},
}


def render(spec: dict, policy: dict) -> tuple[str, dict]:
    kind = spec["structure"]
    if kind not in {"diamonds", "exclusive", "wide-actions"}:
        raise ValueError("unknown structure")
    rounds, entries = spec["rounds"], spec["entries"]
    width, outputs = spec.get("key_bits", 88), spec.get("outputs", 4)
    parameter_bits = spec.get("parameter_bits", 32)
    if parameter_bits not in (8, 16, 32):
        raise ValueError("parameter_bits must be 8, 16 or 32")
    if not 1 <= rounds <= 12 or entries <= 0 or not 40 <= width <= 96 or width % 8:
        raise ValueError("invalid dimensions")
    if not 1 <= outputs <= 8 or spec.get("join_entries", 16384) <= 0:
        raise ValueError("invalid output count or join capacity")
    source = _scaffold.render({"depth": 1, "sides": 0, "chain_entries": 1,
                               "side_entries": 1, "stable_layout": True}, {})
    begin = source.index("header fit_t {")
    end = source.index("struct headers_t", begin)
    fields = "\n".join(f"    bit<32> result{i};" for i in range(8))
    source = source[:begin] + (f"header fit_t {{\n    bit<32> token;\n    bit<{width - 32}> tenant;\n"
                                f"{fields}\n    bit<8> branch;\n    bit<{112-width}> reserved;\n}}\n") + source[end:]
    tables, required = [], {}
    extras = spec.get("extras", 0)
    if not 0 <= extras <= 5 or spec.get("extra_entries", 65536) <= 0:
        raise ValueError("invalid independent policy-table dimensions")
    if extras and kind == "wide-actions" and outputs > 1:
        raise ValueError("independent results overlap wide action results")

    def table(name, group, key, assignments, size):
        if extras and not name.startswith("policy_"):
            group = "b"
        bits = parameter_bits if kind == "wide-actions" else 32
        params = ", ".join(f"bit<{bits}> value{i}" for i in range(len(assignments)))
        body = " ".join(
            f"hdr.fit.{field} = "
            + (f"hdr.fit.{field} + " if kind == "wide-actions" or (kind == "exclusive" and field != "token") else "")
            + ("(bit<32>)" if bits != 32 else "") + f"value{i};" for i, field in enumerate(assignments)
        )
        tables.append(f"    action set_{name}({params}) {{ {body} }}\n"
                      + _scaffold.annotations(policy, group, True)
                      + f"    table {name} {{\n        key = {{ {key} }}\n"
                      + f"        actions = {{ set_{name}; NoAction; }}\n"
                      + f"        const default_action = NoAction();\n        size = {size};\n    }}\n")
        required[name] = {"size": size, "group": group}

    key = "hdr.fit.token : exact; hdr.fit.tenant : exact;"
    calls = []
    for i in range(extras):
        name = f"policy_{i}"
        table(name, "a", "hdr.fit.result7 : exact; hdr.fit.tenant : exact;",
              [f"result{i+2}"], spec.get("extra_entries", 65536))
        calls.append(f"            {name}.apply();")
    if kind == "diamonds":
        for r in range(rounds):
            for lane in range(2):
                name = f"lookup_{r}_{lane}"
                table(name, "a", key, [f"result{lane}"], entries)
                calls.append(f"            {name}.apply();")
            name = f"join_{r}"
            table(name, "b", "hdr.fit.result0 : exact; hdr.fit.result1 : exact;", ["token"], spec.get("join_entries", 16384))
            calls.append(f"            {name}.apply();")
    elif kind == "exclusive":
        for lane in range(2):
            calls.append("            " + ("if (hdr.fit.branch == 0) {" if lane == 0 else "} else {"))
            for r in range(rounds):
                name = f"route_{lane}_{r}"
                table(name, "a" if lane == 0 else "b", key, ["token", f"result{lane}"], entries)
                calls.append(f"                {name}.apply();")
        calls.append("            }")
    else:
        for r in range(rounds):
            name = f"update_{r}"
            table(name, "a" if r % 2 == 0 else "b", key, ["token"] + [f"result{i}" for i in range(outputs)], entries)
            calls.append(f"            {name}.apply();")
    begin = source.index("    action set_chain_0")
    end = source.index("    apply {", begin)
    source = source[:begin] + "".join(tables) + "\n" + source[end:]
    source = source.replace("            chain_0.apply();", "\n".join(calls))
    return source, required


def generate(spec: dict, output: Path) -> None:
    if output.exists():
        raise ValueError("destination exists")
    baseline, required = render(spec, {})
    files = {}
    sources = {}
    for name, policy in POLICIES.items():
        source, actual = render(spec, policy)
        assert actual == required
        assert _scaffold.blank_annotations(source) == _scaffold.blank_annotations(baseline)
        sources[name] = source
        files[name] = {"file": f"{name}/program.p4", "sha256": hashlib.sha256(source.encode()).hexdigest(), "policy": policy}
    output.mkdir(parents=True)
    for name, source in sources.items():
        (output/name).mkdir()
        (output/name/"program.p4").write_text(source)
    (output/"manifest.json").write_text(json.dumps({"spec": spec, "required_tables": required,
        "files": files, "status": "unqualified", "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scaffold_sha256": hashlib.sha256((ROOT/"examples/p4-fit/generate.py").read_bytes()).hexdigest()}, indent=2)+"\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    generate(json.loads(args.spec.read_text()), args.output)
