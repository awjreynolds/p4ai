"""Record one real Tofino compilation without interpreting failure as infeasibility."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPILER = "/work/.cache/p4c-tofino/install/bin/p4c"
ARCHITECTURES = {"tofino": "tna", "tofino2": "t2na"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fit_evidence(output: Path, returncode: int | None, target: str = "tofino") -> dict:
    if target not in ARCHITECTURES:
        raise ValueError("unsupported target")
    manifests = sorted(output.rglob("manifest.json"))
    records = []
    for path in manifests:
        try:
            value = json.loads(path.read_text())
            succeeded = value.get("compilation_succeeded") if isinstance(value, dict) else None
        except (ValueError, OSError):
            succeeded = None
        records.append({"path": str(path.relative_to(output)), "compilation_succeeded": succeeded})
    binaries = sorted(path for path in output.rglob(f"{target}.bin") if path.stat().st_size)
    return {
        "compiler_reported_fit": returncode == 0
        and len(records) == 1
        and records[0]["compilation_succeeded"] is True
        and bool(binaries),
        "manifests": records,
        "binaries": {str(path.relative_to(output)): sha256(path) for path in binaries},
        "interpretation": "Compiler-reported fit only. Failure reason and functional correctness require separate checks.",
    }


def compile_case(source: Path, output: Path, image: str, timeout: int, target: str = "tofino") -> dict:
    architecture = ARCHITECTURES[target]
    source, output = source.resolve(), output.resolve()
    relative = source.relative_to(ROOT)
    output.relative_to(ROOT)
    if not source.is_file() or output.exists():
        raise ValueError("source must exist and output directory must be new")
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True
    ).strip()
    name = f"p4ai-fit-{uuid.uuid4().hex[:12]}"
    command = [
        "docker", "run", "--rm", "--name", name, "--network", "none",
        "--mount", f"type=bind,source={ROOT},target=/work,readonly",
        "--mount", f"type=bind,source={output},target=/output",
        "-w", "/work", image_id, COMPILER,
        "--target", target, "--arch", architecture, "-g", "-o", "/output/compiler",
        str(Path("/work") / relative),
    ]
    inputs = {str(path.relative_to(ROOT)): sha256(path) for path in sorted(source.parent.rglob("*.p4"))}
    installation = ROOT / ".cache/p4c-tofino/install"
    identity = {
        path.name: sha256(path)
        for path in sorted((installation / "bin").iterdir())
        if path.is_file()
    }
    support_inputs = {
        str(path.relative_to(installation)): sha256(path)
        for path in sorted(installation.rglob("*"))
        if path.is_file() and path.suffix in {".p4", ".py", ".cfg"}
    }
    output.mkdir(parents=True)
    started = time.monotonic()
    timed_out = False
    with (output / "stdout.log").open("w") as stdout, (output / "stderr.log").open("w") as stderr:
        try:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout)
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = None
            subprocess.run(["docker", "kill", name], capture_output=True, check=False)
    report = {
        "schema_version": 1,
        "probe_sha256": sha256(Path(__file__)),
        "command": command,
        "image_id": image_id,
        "target": target,
        "architecture": architecture,
        "compiler_executable_sha256": identity,
        "compiler_support_sha256": support_inputs,
        "input_sha256": inputs,
        "elapsed_seconds": time.monotonic() - started,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_sha256": sha256(output / "stdout.log"),
        "stderr_sha256": sha256(output / "stderr.log"),
        **fit_evidence(output / "compiler", returncode, target),
    }
    (output / "probe.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="p4ai-tofino-builder:ca5aef25")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--target", choices=ARCHITECTURES, default="tofino")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    report = compile_case(args.source, args.output, args.image, args.timeout, args.target)
    print(json.dumps({key: report[key] for key in ("returncode", "timed_out", "compiler_reported_fit", "elapsed_seconds")}))
    return 0  # A recorded compiler failure is a valid probe result.


if __name__ == "__main__":
    raise SystemExit(main())
