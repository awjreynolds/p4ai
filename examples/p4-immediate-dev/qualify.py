"""Record the development-only immediate-allocation repair audit without compiling."""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.p4_multirepair_experiment import acceptance, validate_manifest

ROOT = Path(__file__).resolve().parents[2]
NAME = "overlapping-admission-196608"
SOURCES = ROOT / "examples/p4-immediate-dev" / NAME
RUNS = ROOT / "artifacts/p4-immediate-dev" / NAME


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


requirements = {
    **{f"Ingress.chain_{i}": 16384 for i in range(12)},
    **{f"Ingress.side_{i}": 196608 for i in range(8)},
}
default = (SOURCES / "default/program.p4").read_text().splitlines(keepends=True)
candidate = (
    (SOURCES / "side-no-immediate/program.p4").read_text().splitlines(keepends=True)
)
edits = [
    {"line": i + 1, "text": new.rstrip()}
    for i, (old, new) in enumerate(zip(default, candidate))
    if old != new
]
assert len(default) == len(candidate) and len(edits) == 8
assert all(e["text"].strip() == "@immediate(0)" for e in edits)
manifest = {
    "schema_version": 1,
    "cases": [
        {
            "id": NAME,
            "structure": "overlapping-admission-plus-dependent-chain",
            "requirements": requirements,
            "default": "default/program.p4",
            "candidates": [
                {
                    "id": "side-no-immediate",
                    "source": "side-no-immediate/program.p4",
                    "edits": edits,
                }
            ],
        }
    ],
}
validate_manifest(manifest, SOURCES)
(SOURCES / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
rows = {}
for policy in ("default", "side-no-immediate"):
    directory = RUNS / policy
    probe = read(directory / "probe.json")
    resource = read(directory / "compiler/pipe/logs/resources.json")["resources"]["mau"]
    metrics = read(directory / "compiler/pipe/logs/metrics.json")
    audit = acceptance(manifest["cases"][0], directory, RUNS / "default")
    tables = {
        t["name"]: t for p in directory.rglob("context.json") for t in read(p)["tables"]
    }
    capacity = {
        name: {
            "requested": size,
            "logical": tables[name]["size"],
            "physical": sum(
                s["size"] for s in tables[name]["match_attributes"]["stage_tables"]
            ),
        }
        for name, size in requirements.items()
    }
    rows[policy] = {
        "compiler_reported_fit": probe["compiler_reported_fit"],
        "returncode": probe["returncode"],
        "timed_out": probe["timed_out"],
        "actual_stage_ids": [s["stage_number"] for s in resource["mau_stages"]],
        "srams": metrics["mau"]["srams"],
        "acceptance": audit,
        "accepted_fit": probe["compiler_reported_fit"] and audit["eligible_fit"],
        "capacities": capacity,
        "source_sha256": sha(SOURCES / policy / "program.p4"),
        "probe_sha256": sha(directory / "probe.json"),
        "compiler_identity": {
            k: probe[k]
            for k in (
                "image_id",
                "target",
                "architecture",
                "compiler_executable_sha256",
                "compiler_support_sha256",
                "probe_sha256",
            )
        },
        "receipt_path": str(directory.relative_to(ROOT) / "probe.json"),
        "bfrt_sha256": {
            str(p.relative_to(directory)): sha(p) for p in directory.rglob("bfrt.json")
        },
        "binaries": probe["binaries"],
    }
assert (
    rows["default"]["compiler_identity"]
    == rows["side-no-immediate"]["compiler_identity"]
)
assert (
    not rows["default"]["compiler_reported_fit"]
    and rows["side-no-immediate"]["accepted_fit"]
)
assert "supports up to 12 stages, using 15" in (RUNS / "default/stderr.log").read_text()
report = {
    "status": "qualified_development_example",
    "scope": "Compiler-reported fit and declared interface/capacity preservation only; no hardware execution or full semantic proof. Not held-out evidence.",
    "ancestry": "Adapted published tenant8 default source: eight independent side table calls become two overlapping conditional groups selected by tenant bits0 and1; twelve dependent chain tables unchanged. Synthetic modification of the prior chain/side family, not an independent production application.",
    "anchor": "artifacts/p4-fit-advice-v1/sources/tenant8/default/program.p4",
    "anchor_sha256": sha(
        ROOT / "artifacts/p4-fit-advice-v1/sources/tenant8/default/program.p4"
    ),
    "group_mapping": {
        "a": [f"Ingress.side_{i}" for i in range(8)],
        "b": [f"Ingress.chain_{i}" for i in range(12)],
    },
    "manifest_sha256": sha(SOURCES / "manifest.json"),
    "fixed_slot_edits": edits,
    "annotation_only_validation": "passed validate_manifest; all8edits replace blank80columnlines; sourcebasename and linepositions unchanged",
    "compilation_attempts": 2,
    "outcomes": rows,
}
(ROOT / "examples/p4-immediate-dev/qualification.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "qualified": True,
            "baseline_stages": len(rows["default"]["actual_stage_ids"]),
            "candidate_stages": len(rows["side-no-immediate"]["actual_stage_ids"]),
            "source_edits": len(edits),
        }
    )
)
