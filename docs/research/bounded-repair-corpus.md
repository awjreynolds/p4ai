# A corpus for bounded compiler repair

Use the SQLite application in LLVM Test Suite as the first **technical feasibility gate** for evidence-seeking compiler repair. It provides a real C application with deterministic inputs and a maintained test-suite integration. This research establishes a reproducible candidate, not native build success or optimization headroom. The active compiler-feedback experiment remains unchanged.

## Immutable source and exact contract

Pin LLVM Test Suite to `2834619c5f535a36498daffc470cf3f4ae12a4f9`, identified by the [`llvmorg-21.1.0-rc3` tag](https://fuchsia.googlesource.com/third_party/llvm-test-suite/+/refs/tags/llvmorg-21.1.0-rc3). This source pin is independent of our compiler pin. No exact revision equivalence with IREE's LLVM or Apple Clang is claimed.

The pinned [SQLite CMake file](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/CMakeLists.txt) builds two C translation units, `sqlite3.c` and `shell.c`, with the bundled header. It does not link system SQLite. Definitions include `SQLITE_OMIT_LOAD_EXTENSION=1` and `SQLITE_THREADSAFE=0`. The bundled [VERSION](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/VERSION) is 3.5.7; this is an established historical benchmark application, not a proposed current SQLite deployment.

The invocation is `sqlite3 -init sqlite3rc :memory: < commands`. The empty initialization file and [commands file](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/commands) drive 15 generated SQL scripts. [speedtest.tcl](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/speedtest.tcl) supplies the normal workload and [smalltest.tcl](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/smalltest.tcl) the small configuration; both seed the generator with 1. They exercise insertion, selection, indexing and updates.

The [normal reference output](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/sqlite3.reference_output) and [small reference](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/sqlite3.reference_output.small) are checked through the suite's generated test script and `fpcmp`, with the configured relative tolerance `1e-9`. The suite's [test-file implementation](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/cmake/modules/TestFile.cmake) and [MultiSource implementation](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/cmake/modules/SingleMultiSource.cmake) define that integration; there is no hand-authored `sqlite3.test` in this directory.

A clean build must verify Tcl availability and generation of all 15 files. CMake declares only `test15.sql` as the generated output, although the script writes all 15, and does not list the Tcl script as a dependency. An incremental directory with missing intermediate inputs may therefore require explicit regeneration. This is a source-inspection finding, not an observed build failure.

The pinned [copyright release](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/copyright-release.html) covers SQLite source, documentation, build files and test scripts. The inspected [shell source](https://github.com/llvm/llvm-test-suite/blob/2834619c5f535a36498daffc470cf3f4ae12a4f9/MultiSource/Applications/sqlite3/shell.c) and header carry public-domain notices. The large amalgamation's header was not returned by the GitHub excerpt endpoint; its exact blob is `b496e77ee605a19c5681049b78f57a8d9d6777e5`. Verify that header during the download gate and preserve the notices. Do not assume the suite's top-level license covers every third-party directory.

## Next feasibility gate

The existing host has pinned LLVM `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4` and Apple Clang 21.0.0 (`clang-2100.1.1.101`). Before designing a measured repair trial:

1. Fetch the immutable SQLite subtree and notices into ignored cache storage; hash every input.
2. Build and pass the pinned reference check with both compilers using equivalent explicit native target settings. Record the SDK/toolchain differences: this is a compiler-distribution shift, not a controlled single-commit change.
3. Verify a bounded policy interface on the actual application, such as LLVM's existing interactive inliner, with distinct legal code outcomes. Do not infer useful opportunity from interface availability.
4. Freeze the repair action menu, diagnostic/measurement budget and equal-budget cheap search baseline before performance screening.

SQLite alone cannot establish application-disjoint generalization. SQL scripts are workloads within one application, not independent application families. Additional applications must pass their own source/license/build gates before a multi-application experiment is claimed.

## Further candidates, not yet validated

MAFFT core/no-extensions is a plausible C bioinformatics application; [upstream licensing](https://mafft.ddbj.nig.ac.jp/alignment/software/) distinguishes its BSD core from components bundled with extensions. The exact LLVM copy and fixtures need inspection. DOE Pathfinder is another multi-file C candidate, but [Mantevo lists LGPL-3.0](https://mantevo.org/download/license/) and the exact copied notices/input contract need validation. Neither has been built or benchmarked here.

The research subagent inspected primary sources only and performed no native builds, model calls or performance measurements. Findings were recorded by the parent because that subagent's role permits read-only work.
