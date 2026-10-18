#!/usr/bin/env python3
"""Generate unqualified, annotation-only Tofino placement discovery candidates.

This creates input files; it neither invokes a compiler nor labels a case fit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ANNOTATION_LINE_WIDTH = 80


def annotations(policy: dict, group: str, stable_layout: bool = False) -> str:
    values = policy.get(group, {})
    allowed = {"placement_priority", "pack", "immediate", "force_immediate"}
    if set(values) - allowed:
        raise ValueError("unsupported annotation")
    for name, value in values.items():
        if type(value) is not int:
            raise ValueError("annotation values must be integers")
        if name == "pack" and not 1 <= value <= 9:
            raise ValueError("pack must be in 1..9")
        if name in {"immediate", "force_immediate"} and value not in (0, 1):
            raise ValueError("immediate annotations must be 0 or 1")
    if stable_layout:
        lines = []
        for name in sorted(allowed):
            line = f"    @{name}({values[name]})" if name in values else ""
            if len(line) > ANNOTATION_LINE_WIDTH:
                raise ValueError("annotation exceeds the fixed stable-layout line width")
            lines.append(line.ljust(ANNOTATION_LINE_WIDTH) + "\n")
        return "".join(lines)
    return "".join(f"    @{name}({value})\n" for name, value in sorted(values.items()))


def render(spec: dict, policy: dict) -> str:
    depth, sides = spec["depth"], spec["sides"]
    tenant_bits = spec.get("tenant_bits", 32)
    if not 1 <= depth <= 12 or not 0 <= sides <= 8:
        raise ValueError("depth must be 1..12 and sides 0..8")
    if type(tenant_bits) is not int or tenant_bits not in range(8, 65, 8):
        raise ValueError("tenant_bits must be a multiple of 8 in 8..64")
    if min(spec["chain_entries"], spec["side_entries"]) <= 0:
        raise ValueError("table capacities must be positive")
    if set(policy) - {"chain", "side"}:
        raise ValueError("policy can annotate chain and side tables only")
    tables = []
    for group, count, entries in (
        ("chain", depth, spec["chain_entries"]),
        ("side", sides, spec["side_entries"]),
    ):
        for index in range(count):
            name = f"{group}_{index}"
            result = "token" if group == "chain" else f"result{index}"
            key = "hdr.fit.token : exact;" if group == "chain" else (
                "hdr.fit.flow : exact; hdr.fit.tenant : exact;"
            )
            tables.append(
                f"    action set_{name}(bit<32> value) {{ hdr.fit.{result} = value; }}\n"
                + annotations(policy, group, spec.get("stable_layout", False))
                + f"    table {name} {{\n"
                f"        key = {{ {key} }}\n"
                f"        actions = {{ set_{name}; NoAction; }}\n"
                f"        const default_action = NoAction();\n"
                f"        size = {entries};\n"
                f"    }}\n"
            )
    # Source order is natural: independent policy labels, then dependent service chain.
    # The compiler can reorder the independent lookups using real dependencies.
    calls = "\n".join(
        f"            {group}_{i}.apply();"
        for group, count in (("side", sides), ("chain", depth))
        for i in range(count)
    )
    results = "\n".join(f"    bit<32> result{i};" for i in range(8))
    return f'''// SPDX-License-Identifier: MIT
// Generated discovery candidate. Resource fit has not been established.
#include <core.p4>
#if __TARGET_TOFINO__ == 2
#include <t2na.p4>
#else
#include <tna.p4>
#endif

header ethernet_t {{ bit<48> dst; bit<48> src; bit<16> ether_type; }}
header fit_t {{
    bit<32> flow;
    bit<{tenant_bits}> tenant;
    bit<32> token;
{results}
    bit<{80 - tenant_bits}> reserved;
}}
struct headers_t {{ ethernet_t ethernet; fit_t fit; }}
struct metadata_t {{ }}

parser IngressParser(packet_in pkt, out headers_t hdr, out metadata_t md,
                     out ingress_intrinsic_metadata_t ig_intr_md) {{
    state start {{
        pkt.extract(ig_intr_md);
        transition select(ig_intr_md.resubmit_flag) {{ 0: port_metadata; default: reject; }}
    }}
    state port_metadata {{ pkt.advance(PORT_METADATA_SIZE); transition ethernet; }}
    state ethernet {{
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {{ 16w0x88b5: fit; default: accept; }}
    }}
    state fit {{ pkt.extract(hdr.fit); transition accept; }}
}}

control Ingress(inout headers_t hdr, inout metadata_t md,
                in ingress_intrinsic_metadata_t ig_intr_md,
                in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
                inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
                inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {{
{''.join(tables)}
    apply {{
        ig_tm_md.ucast_egress_port = ig_intr_md.ingress_port;
        if (hdr.fit.isValid()) {{
{calls}
        }}
    }}
}}

control IngressDeparser(packet_out pkt, inout headers_t hdr, in metadata_t md,
                        in ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md) {{
    apply {{ pkt.emit(hdr); }}
}}

parser EgressParser(packet_in pkt, out headers_t hdr, out metadata_t md,
                    out egress_intrinsic_metadata_t eg_intr_md) {{
    state start {{ pkt.extract(eg_intr_md); transition ethernet; }}
    state ethernet {{
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {{ 16w0x88b5: fit; default: accept; }}
    }}
    state fit {{ pkt.extract(hdr.fit); transition accept; }}
}}
control Egress(inout headers_t hdr, inout metadata_t md,
               in egress_intrinsic_metadata_t eg_intr_md,
               in egress_intrinsic_metadata_from_parser_t eg_prsr_md,
               inout egress_intrinsic_metadata_for_deparser_t eg_dprsr_md,
               inout egress_intrinsic_metadata_for_output_port_t eg_oport_md) {{
    apply {{ }}
}}
control EgressDeparser(packet_out pkt, inout headers_t hdr, in metadata_t md,
                       in egress_intrinsic_metadata_for_deparser_t eg_dprsr_md) {{
    apply {{ pkt.emit(hdr); }}
}}
Pipeline(IngressParser(), Ingress(), IngressDeparser(),
         EgressParser(), Egress(), EgressDeparser()) pipe;
Switch(pipe) main;
'''


POLICIES = {
    "default": {},
    "chain-priority": {"chain": {"placement_priority": 10}},
    "side-priority": {"side": {"placement_priority": 10}},
    "side-later": {"side": {"placement_priority": -10}},
    "chain-no-immediate": {"chain": {"immediate": 0}},
    "side-no-immediate": {"side": {"immediate": 0}},
    "all-no-immediate": {"chain": {"immediate": 0}, "side": {"immediate": 0}},
    **{f"chain-pack-{n}": {"chain": {"pack": n}} for n in range(1, 10)},
    **{f"side-pack-{n}": {"side": {"pack": n}} for n in range(1, 10)},
}


def without_annotations(source: str) -> str:
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("@"))


def blank_annotations(source: str) -> str:
    return "".join(
        " " * len(line.rstrip("\n")) + "\n" if line.lstrip().startswith("@") else line
        for line in source.splitlines(keepends=True)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--depth", type=int, default=8)
    parser.add_argument("--sides", type=int, default=4)
    parser.add_argument("--chain-entries", type=int, default=16384)
    parser.add_argument("--side-entries", type=int, default=32768)
    parser.add_argument("--tenant-bits", type=int, choices=range(8, 65, 8), default=32)
    parser.add_argument("--policy", choices=list(POLICIES) + ["all"], default="default")
    parser.add_argument("--stable-layout", action="store_true")
    args = parser.parse_args()
    spec = {
        name: getattr(args, name)
        for name in ("depth", "sides", "chain_entries", "side_entries", "tenant_bits", "stable_layout")
    }
    policies = POLICIES if args.policy == "all" else {args.policy: POLICIES[args.policy]}
    baseline = render(spec, {})
    normalize = blank_annotations if args.stable_layout else without_annotations
    try:
        args.output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error(f"output destination already exists: {args.output}; choose a new directory")
    files = {}
    for name, policy in policies.items():
        source = render(spec, policy)
        assert normalize(source) == normalize(baseline)
        path = args.output / name / "program.p4" if args.stable_layout else args.output / f"{name}.p4"
        if args.stable_layout:
            path.parent.mkdir()
        path.write_text(source)
        files[name] = {"file": str(path.relative_to(args.output)), "sha256": hashlib.sha256(source.encode()).hexdigest(), "policy": policy}
    (args.output / "manifest.json").write_text(json.dumps({
        "status": "unqualified_discovery_inputs", "spec": spec, "files": files,
        "annotation_comparison": "blank_fixed_width_lines" if args.stable_layout else "remove_lines",
        "annotation_stripped_sha256": hashlib.sha256(normalize(baseline).encode()).hexdigest(),
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
