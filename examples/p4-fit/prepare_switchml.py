#!/usr/bin/env python3
"""Prepare pinned, annotation-only SwitchML discovery inputs without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ANNOTATIONS = {
    "container": (
        "headers.p4",
        b'@pa_container_size("ingress", "hdr.ib_bth.psn", 32)',
    ),
    "overlay": (
        "types.p4",
        b'@pa_no_overlay("ingress", "ig_md.switchml_md.simulate_egress_drop")',
    ),
}
VARIANTS = {
    "original": (),
    "without-container": ("container",),
    "without-overlay": ("overlay",),
    "without-both": ("container", "overlay"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_selected_annotations(name: str, data: bytes) -> bytes:
    for file_name, annotation in ANNOTATIONS.values():
        if name == file_name:
            data = data.replace(annotation, b"")
    return data


def prepare(source: Path, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError(f"output destination already exists: {output}; choose a new directory")
    pin = json.loads(Path(__file__).with_name("switchml-source.json").read_text())
    directory = source / pin["p4_directory"]
    actual_names = {path.name for path in directory.glob("*.p4")}
    if actual_names != set(pin["source_sha256"]):
        raise ValueError("source P4 file inventory differs from the pinned manifest")
    originals = {name: (directory / name).read_bytes() for name in sorted(actual_names)}
    for name, data in originals.items():
        if sha256(data) != pin["source_sha256"][name]:
            raise ValueError(f"source hash mismatch: {name}")
    license_data = (source / "LICENSE").read_bytes()
    if sha256(license_data) != pin["license_sha256"]:
        raise ValueError("source LICENSE hash mismatch")
    for name, annotation in ANNOTATIONS.values():
        if originals[name].count(annotation) != 1:
            raise ValueError(f"expected exactly one annotation in {name}")

    # Verify every transformation before creating any output directory or file.
    prepared = {}
    variants = []
    for variant, removed in VARIANTS.items():
        files = originals.copy()
        for key in removed:
            name, annotation = ANNOTATIONS[key]
            files[name] = files[name].replace(annotation, b"")
        for name, data in files.items():
            if strip_selected_annotations(name, data) != strip_selected_annotations(name, originals[name]):
                raise ValueError(f"non-annotation change in {variant}/{name}")
        prepared[variant] = files
        variants.append({
            "variant": variant,
            "compile_status": "not_run",
            "entrypoint": f"{variant}/source/switchml.p4",
            "removed_annotations": list(removed),
            "source_sha256": {name: sha256(data) for name, data in files.items()},
            "license_sha256": sha256(license_data),
        })
    manifest = {
        "purpose": "Qualification inputs only; no asserted fail/pass result.",
        "source_repository": pin["source_repository"],
        "source_revision": pin["source_revision"],
        "generator_sha256": sha256(Path(__file__).read_bytes()),
        "annotation_only_verified": True,
        "variants": variants,
    }
    output.mkdir(parents=True, exist_ok=False)
    for variant, files in prepared.items():
        destination = output / variant / "source"
        destination.mkdir(parents=True)
        for name, data in files.items():
            (destination / name).write_bytes(data)
        (destination / "LICENSE").write_bytes(license_data)
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="SwitchML checkout root")
    parser.add_argument("--output", type=Path, required=True, help="fresh destination directory")
    args = parser.parse_args()
    try:
        manifest = prepare(args.source, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Prepared {len(manifest['variants'])} uncompiled variants in {args.output}")


if __name__ == "__main__":
    main()
