# Application correctness passes; historical SQLite is not cleared for repair

The pinned LLVM Test Suite SQLite 3.5.7 application passes its original small reference workload under both native compiler distributions. It is **not qualified for a performance or compiler-repair experiment**: Apple's UBSan detects an undefined signed left shift in the unmodified source. No application optimization or model calls were attempted.

## Observed results

| Build | Reference workload | Sanitizer result |
| --- | --- | --- |
| Pinned LLVM 24, native ARM64 | Pass | ASan runtime unavailable; sanitizer build cannot link |
| Apple Clang 21, native ARM64 | Pass | UBSan reports signed-left-shift overflow; application exits 134 |

The failure is in `strHash`, at pinned `sqlite3.c:16439:11`: signed `int h` is shifted left by three when its value is 2,115,950,682. With the frozen `halt_on_error=1` configuration, execution stops and the reference comparator rejects the diagnostic output. Passing the unsanitized reference does not make this operation defined. The source was not patched and the sanitizer was not suppressed.

The pinned LLVM sanitizer configuration separately fails because `libclang_rt.asan_osx_dynamic.dylib` is absent from that locally built toolchain. That is an unavailable check, not a clean sanitizer result or another detected source defect.

## Reproducible contract

The [design](../../experiments/sqlite-feasibility-v1.json) pins LLVM Test Suite `2834619c5f535a36498daffc470cf3f4ae12a4f9`, its SQLite subtree, notices, Tcl generator and reference. The compiler source pin remains `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4`. Apple Clang is 21.0.0 (`clang-2100.1.1.101`); the shared macOS SDK reports 26.5. Both application builds use ARM64, macOS deployment target 14.0, the same SDK/linker and explicit `-O3 -DNDEBUG` without LTO. The original SQLite definitions, including `SQLITE_THREADSAFE=0` and `SQLITE_OMIT_LOAD_EXTENSION=1`, remain in force. This is a compiler-distribution comparison, not a single-commit compiler change.

All 15 SQL input hashes match across runnable baseline and sanitizer variants. The run uses the original `-init sqlite3rc :memory:` arguments and `commands` stdin. The upstream `timeit-target` helper preserves stdout/stderr and appends the application exit status; the upstream `fpcmp-target` checks that output against `sqlite3.reference_output.small` with relative tolerance `1e-9`. A pass requires successful execution, application exit zero and comparator acceptance. Helper timing is diagnostic only; no benchmark or speedup is reported.

The initial source/code contract was committed in `9a93fdc`; the working runner is committed in `d52a0c6`. Native setup exposed and preserved several runner problems: an invalid Python subprocess argument, optional code-size collection requiring an absent tool, missing SDK flags for host helpers, an incorrect expected executable substitution, and incompatible newer `lit` dependencies/APIs. The final runner disables unnecessary code-size collection, gives host helpers the same SDK/target flags, and invokes the original native execution/comparison helpers directly. It does not normalize outputs or modify SQLite. Reused builds are checked against compiler, source, CMake, compile-command, generated-input and artifact hashes before the workload is rerun.

The [public evidence bundle](sqlite-feasibility-v1.json) contains the sanitized report, input and toolchain identities, command outputs, sanitizer diagnostics and prior runner failure provenance. Passing the specified workload is a limited correctness observation; even clean sanitizers would not prove the absence of undefined behavior.

```sh
uv run python -m scripts.sqlite_feasibility --phase validate --sanitizers \
  --output artifacts/sqlite-feasibility-v1
```

The report's top-level `status` describes baseline reference correctness. Read `sanitizer_status` and each variant before deciding whether to use the application for optimization. The historical application is rejected for that purpose here. A current SQLite source release needs its own immutable manifest and native checks; the old result stays intact.

## Separate inliner interface check

The [inliner feasibility script](../../scripts/inliner_feasibility.py) demonstrates bounded 0/1/default advice on a small unsigned C caller/callee example. Apple Clang's driver accepts the interactive mode directly. The pinned Clang driver encounters an LLVM I/O-sandbox violation; the supported pinned `opt` path succeeds without changing the sandbox or compiler source. Build `opt` from the existing pinned checkout, emit frontend IR without optimization passes, then invoke the upstream interactive inliner pass. Advice zero retains the calls; advice one inlines both; the default matches one.

This is a **toy protocol/code-shape check**, not application-level inliner validation, performance evidence or policy transfer. Interactive context lacks stable call-site identities. A later repair experiment must address those limitations separately.

```sh
cmake --build .cache/llvm-build --target opt --parallel 4
uv run python -m scripts.inliner_feasibility --mode opt
```

The [inliner evidence](inliner-feasibility-v1.json) records exact compiler/tool identities, commands, decisions and normalized caller bodies, including the failed direct-driver route. It does not substitute an unrelated installed LLVM tool for the pinned path.
