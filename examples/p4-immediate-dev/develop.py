"""Development-only structural transformations of the published tenant8 anchor."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.p4_compile_probe import compile_case
from scripts.p4_multirepair_experiment import acceptance

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "sha256:5b27c37f3835c646c4a7f87c64de986a5fd5d71170b3f8b91e4bfbb908763af9"


def generate(kind, entries=196608):
    source = (
        ROOT / "artifacts/p4-fit-advice-v1/sources/tenant8/default/program.p4"
    ).read_text()
    source = source.replace("size = 196608;", f"size = {entries};")
    if kind == "overlapping-admission":
        for i in range(8):
            source = source.replace(
                f"            side_{i}.apply();",
                f"            if ((hdr.fit.tenant & 8w{1 << (i // 4)}) != 0) {{ side_{i}.apply(); }}",
            )
    else:
        raise ValueError(kind)
    return source


def run(kind, entries=196608):
    name = f"{kind}-{entries}"
    base = ROOT / "examples/p4-immediate-dev" / name
    source = generate(kind, entries)
    for policy in ("default", "side-no-immediate"):
        target = base / policy / "program.p4"
        if not target.exists():
            text = source
            if policy != "default":
                for i in range(8):
                    text = text.replace(
                        " " * 80 + f"\n    table side_{i}",
                        "    @immediate(0)".ljust(80) + f"\n    table side_{i}",
                    )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        output = ROOT / "artifacts/p4-immediate-dev" / name / policy
        if not (output / "probe.json").exists():
            compile_case(target, output, IMAGE, 120)
        report = json.loads((output / "probe.json").read_text())
        audit = acceptance(
            {
                "requirements": {
                    **{f"Ingress.chain_{i}": 16384 for i in range(12)},
                    **{f"Ingress.side_{i}": entries for i in range(8)},
                }
            },
            output,
            ROOT / "artifacts/p4-immediate-dev" / name / "default",
        )
        row = {
            "case": name,
            "policy": policy,
            "fit": report["compiler_reported_fit"],
            "returncode": report["returncode"],
            "elapsed_seconds": report["elapsed_seconds"],
            "audit": audit,
        }
        (output / "audit.json").write_text(json.dumps(row, indent=2) + "\n")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    run(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 196608)
