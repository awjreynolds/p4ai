import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "fixtures" / "native"


def test_native_corpus_matches_scalar_references(tmp_path: Path) -> None:
    compiler = shlex.split(os.environ.get("CC", "clang"))
    if not compiler or shutil.which(compiler[0]) is None:
        raise AssertionError("a C compiler is required to exercise the native corpus")

    executable = tmp_path / "native-corpus"
    subprocess.run(
        [
            *compiler,
            "-std=c11",
            "-O2",
            str(NATIVE / "kernels.c"),
            str(NATIVE / "driver.c"),
            "-o",
            str(executable),
        ],
        check=True,
        cwd=ROOT,
    )
    first = json.loads(subprocess.check_output([str(executable), "3"], text=True))
    second = json.loads(subprocess.check_output([str(executable), "3"], text=True))

    assert first["correct"] is True
    assert first["iterations"] == 3
    assert re.fullmatch(r"[0-9a-f]{16}", first["checksum"])
    assert first["checksum"] == second["checksum"]
    assert first["elapsed_ns"] >= 0
