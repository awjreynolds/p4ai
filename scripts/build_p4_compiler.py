"""Build the pinned public Tofino compiler in an isolated ARM64 Docker image.

Run with Python 3.12+: python -m scripts.build_p4_compiler --jobs 4
Requires a running Docker daemon and network during bootstrap. No host packages
are installed. Compilation later uses --network none and the cached installation.
The base image and compiler archive are pinned; apt dependencies are recorded,
not a fully hermetic snapshot. Negative bootstrap logs are retained per attempt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

SOURCE_COMMIT = "ca5aef25de1f847b5c88a05af583506484927015"
SOURCE_SHA256 = "0e6df6b7fafa5d04038964a6c1318c47e3b5e96be89ecf9be923572d7512fc25"
SOURCE_URL = f"https://codeload.github.com/p4lang/p4c/tar.gz/{SOURCE_COMMIT}"
IMAGE = "p4ai-tofino-builder:ca5aef25"

DOCKERFILE = """FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git cmake ninja-build clang lld g++ make autoconf automake libtool libgc-dev bison flex libfl-dev libboost-dev libboost-iostreams-dev libboost-graph-dev libgmp-dev pkg-config python3 python3-venv python3-pip libpython3.12 curl && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/p4-python && /opt/p4-python/bin/pip install jsl==0.2.4 jsonschema==2.6.0 six==1.17.0 pyelftools==0.33 PyYAML==6.0.2
ENV PATH=/opt/p4-python/bin:$PATH
RUN python3 -m pip install pyinstaller==6.16.0 setuptools==80.9.0
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends rapidjson-dev && rm -rf /var/lib/apt/lists/*
"""

BUILD_SCRIPT = """#!/bin/bash
set -euo pipefail
export P4C_VERSION="1.2.5.18 (SHA: ca5aef25de1f847b5c88a05af583506484927015 BUILD: Release)"
export BFN_P4C_GIT_SHA=ca5aef25de1f847b5c88a05af583506484927015
export GIT_CEILING_DIRECTORIES=/work/.cache/p4c-tofino
src=/work/.cache/p4c-tofino/p4c-ca5aef25de1f847b5c88a05af583506484927015
build=/work/.cache/p4c-tofino/build
cmake -S "$src" -B "$build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_CXX_FLAGS_RELEASE='-O1 -DNDEBUG' -DCMAKE_C_FLAGS_RELEASE='-O1 -DNDEBUG' -DCMAKE_INSTALL_PREFIX=/work/.cache/p4c-tofino/install -DENABLE_TOFINO=ON -DENABLE_BMV2=OFF -DENABLE_EBPF=OFF -DENABLE_UBPF=OFF -DENABLE_DPDK=OFF -DENABLE_P4TC=OFF -DENABLE_P4FMT=OFF -DENABLE_P4TEST=ON -DENABLE_P4C_GRAPHS=OFF -DENABLE_TEST_TOOLS=OFF -DENABLE_GTESTS=OFF -DENABLE_DOCS=OFF -DENABLE_LTO=OFF -DENABLE_WERROR=OFF -DCMAKE_UNITY_BUILD=OFF
cmake --build "$build" --parallel "$P4AI_BUILD_JOBS"
cmake --install "$build"
/work/.cache/p4c-tofino/install/bin/p4c-barefoot --version
/work/.cache/p4c-tofino/install/bin/p4c --target-help
"""


def verify_archive(archive: Path) -> None:
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Pinned P4C source archive SHA256 mismatch")


def ensure_source(archive: Path, cache: Path) -> None:
    """Never silently build an edited source tree under the pinned identity."""
    verify_archive(archive)
    source = cache / f"p4c-{SOURCE_COMMIT}"
    with tarfile.open(archive) as contents:
        for member in contents.getmembers():
            parts = Path(member.name).parts
            if not parts or parts[0] != source.name or ".." in parts:
                raise ValueError("Unexpected archive source path")
        if not source.exists():
            contents.extractall(cache, filter="data")
        for member in contents.getmembers():
            if member.isfile():
                expected = contents.extractfile(member)
                actual = cache / member.name
                if expected is None or not actual.is_file():
                    raise ValueError(f"Missing pinned source file: {member.name}")
                if expected.read() != actual.read_bytes():
                    raise ValueError(f"Edited pinned source file: {member.name}")


def run_logged(command: list[str], log: Path, *, cwd: Path) -> None:
    print(f"Running: {' '.join(command)}\nLog: {log}", flush=True)
    with log.open("w") as stream:
        subprocess.run(
            command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT, check=True
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=4)
    desktop_socket = Path.home() / ".docker/run/docker.sock"
    default_host = os.environ.get("DOCKER_HOST") or (
        f"unix://{desktop_socket}"
        if desktop_socket.exists()
        else "unix:///var/run/docker.sock"
    )
    parser.add_argument("--docker-host", default=default_host)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    cache = root / ".cache/p4c-tofino"
    evidence = root / "artifacts/p4-tofino-toolchain"
    cache.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    archive = cache / "source.tar.gz"
    if not archive.exists():
        partial = archive.with_suffix(".download")
        with (
            urllib.request.urlopen(SOURCE_URL, timeout=120) as response,
            partial.open("wb") as output,
        ):
            while data := response.read(1024 * 1024):
                output.write(data)
        verify_archive(partial)
        partial.replace(archive)
    ensure_source(archive, cache)
    (cache / "Dockerfile").write_text(DOCKERFILE)
    (cache / "build.sh").write_text(BUILD_SCRIPT)
    config = cache / "docker-config"
    config.mkdir(exist_ok=True)
    # Public pulls do not need the user's desktop credential helper or credentials.
    (config / "config.json").write_text("{}\n")
    docker = ["docker", "--config", str(config), "--host", args.docker_host]
    attempt = f"{time.time_ns()}"
    run_logged(
        docker
        + [
            "build",
            "--platform",
            "linux/arm64",
            "-t",
            IMAGE,
            "-f",
            str(cache / "Dockerfile"),
            str(config),
        ],
        evidence / f"builder-{attempt}.log",
        cwd=root,
    )
    image_id = subprocess.check_output(
        docker + ["image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
    ).strip()
    provenance = {
        "source_commit": SOURCE_COMMIT,
        "archive_sha256": SOURCE_SHA256,
        "source_url": SOURCE_URL,
        "image_id": image_id,
        "jobs": args.jobs,
        "mount": "/work",
        "attempt": attempt,
    }
    (evidence / f"provenance-{attempt}.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    container = docker + [
        "run",
        "--rm",
        "--platform",
        "linux/arm64",
        "-v",
        f"{root}:/work",
        "-w",
        "/work",
    ]
    run_logged(
        container
        + [
            "-e",
            f"P4AI_BUILD_JOBS={args.jobs}",
            image_id,
            "bash",
            ".cache/p4c-tofino/build.sh",
        ],
        evidence / f"build-{attempt}.log",
        cwd=root,
    )
    run_logged(
        container
        + [
            "--network",
            "none",
            image_id,
            "bash",
            "-c",
            "dpkg-query -W && python3 -m pip freeze && clang --version && cmake --version && ninja --version",
        ],
        evidence / f"environment-{attempt}.txt",
        cwd=root,
    )
    print(
        "Compiler installed under .cache/p4c-tofino/install; run it inside the recorded image."
    )


if __name__ == "__main__":
    main()
