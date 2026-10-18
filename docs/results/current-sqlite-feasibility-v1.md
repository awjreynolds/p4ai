# Current SQLite qualifies for a bounded repair experiment

SQLite 3.54.1 passes the unchanged LLVM Test Suite small SQLite workload on both pinned LLVM and Apple Clang. The Apple AddressSanitizer/UndefinedBehaviorSanitizer build also exits successfully and matches the same reference. This clears the application correctness gate for a later compiler-policy experiment. It establishes no optimization headroom, model benefit or maintenance saving.

| Variant | Build | Application exit | Reference comparison | Result |
| --- | --- | ---: | ---: | --- |
| Pinned LLVM 24, revision `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4` | Pass | 0 | 0 | Pass |
| Apple Clang 21.0.0, `clang-2100.1.1.101` | Pass | 0 | 0 | Pass |
| Apple Clang with ASan and UBSan | Pass | 0 | 0 | Pass |
| Pinned LLVM with sanitizers | Not retried | — | — | Runtime unavailable |

The pinned compiler's missing ASan runtime is verified from the retained historical failure evidence; it is not a sanitizer pass. The available Apple sanitizer result covers this workload and configuration, not every execution or absence of all undefined behavior. There are **zero performance measurements**.

## Source and workload

The [official SQLite 3.54.1 release](https://www.sqlite.org/releaselog/3_54_1.html) supplies a separately staged, matching `sqlite3.c`, `sqlite3.h` and `shell.c`. Its source ID is:

```text
2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76
```

The [official archive](https://www.sqlite.org/2026/sqlite-autoconf-3540100.tar.gz) has SHA3-256 `b6ced585c750916b1052667239a12c25a7ff34e7f06e1e6e01ecb0427657d9c4`; the amalgamation has SHA3-256 `e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81`. Both were checked before execution. The extraction allowlist retains embedded notices and package license material; it never runs the archive's configure script. SQLite and bundled build material retain their own licensing.

The historical LLVM Test Suite checkout remains unchanged at `2834619c5f535a36498daffc470cf3f4ae12a4f9`. The adapter reuses its verified generated SQL, empty initialization file, command sequence, exact reference and native `timeit-target`/`fpcmp-target` helpers. All 15 SQL files form **one dependent episode in one in-memory database**, not 15 independent application families. The original relative comparison tolerance is `1e-9`; no output normalization or reference edits were made.

Both compilers use the same explicit `-O3`, `-DNDEBUG`, SQLite definitions, ARM64 target, macOS 26.5 SDK and minimum OS 14.0 contract, without LTO. The sanitizer build adds `-fsanitize=address,undefined` and `-fno-omit-frame-pointer`, with both sanitizers configured to halt on error. Wrapper, application and comparator must all succeed. `qualified_for_repair` additionally requires the Apple sanitizer variant to pass; a baseline-only pass is insufficient.

This replaces the source substrate for future work. The [historical SQLite sanitizer failure](sqlite-feasibility-v1.md) remains a valid negative result and was neither patched nor suppressed.

## Reproduction and evidence

The [configuration](../../experiments/current-sqlite-feasibility-v1.json), [runner](../../scripts/current_sqlite_feasibility.py) and focused tests were frozen in `4bee82e` before native validation. The [public evidence bundle](current-sqlite-feasibility-v1.json) contains sanitized provenance, source/license manifests, input hashes, captured command output and build-artifact hashes. It does not embed the SQLite source archive. The original native report SHA-256 is `5baec78937a30841f4d80e94b71b432b65ddbc4d97070a90cd94439bfd2f446f`.

The recorded run on the existing host was:

```sh
uv run python -m scripts.current_sqlite_feasibility --phase validate \
  --output artifacts/current-sqlite-feasibility-v1-final
```

A fresh checkout must first obtain the pinned LLVM/test-suite prerequisites described in the [historical qualification report](sqlite-feasibility-v1.md), run that historical gate with sanitizers, and download the official current SQLite archive to `.cache/current-sqlite/sqlite-autoconf-3540100.tar.gz`. Then select the newly generated historical report explicitly:

```sh
uv run python -m scripts.sqlite_feasibility --phase validate --sanitizers \
  --output artifacts/sqlite-feasibility-reproduction
uv run python -m scripts.current_sqlite_feasibility --phase validate \
  --historical-report /absolute/path/to/the/new/feasibility-report.json \
  --output artifacts/current-sqlite-reproduction
```

The explicit report path requires a fresh output directory and records a new effective-design hash. Source, generated inputs, toolchain, flags and helper artifact hashes are still validated; the default path instead binds to this run's exact historical snapshot. The historical sanitizer is expected to expose the retained old-source finding. This procedure targets a compatible ARM64 macOS environment; a clean-host end-to-end reproduction has not been performed.

Thirteen focused adapter tests and the 301-test repository suite passed; CI passed on the frozen commit. Independent review checked the protocol and staging/override behavior, and the public exporter revalidated all recorded source, input, command and build-artifact hashes.
