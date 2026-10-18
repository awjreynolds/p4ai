# A pragma-only repair of a real Tofino pipeline-fit failure

Status: qualified compiler-fit pair, 18 October 2026. This is adaptive discovery,
not a frozen model evaluation. [Machine-readable evidence](p4-pragma-fit-qualification-v1.json)
records the inputs, artifact hashes, capacity checks and completed discovery runs.

The normal compiler invocation could not fit our generated P4 program into
Tofino's 12 match-action stages. Adding `@pack(3)` to eight independent tables made
the same program compile to a target binary, preserving the required table
capacities, packet-processing statements and control-plane schema apart from
annotation metadata. No compiler source, hardware limit or target changed.

| Controlled variant | Result | Observed stages | SRAM blocks |
| --- | --- | ---: | ---: |
| Unannotated baseline | Assembler rejects allocation; no binary | 14 | 880 |
| Eight side-table `@pack(3)` directives | Exit 0, successful manifest, nonempty `tofino.bin` | 12 | 936 |

The baseline's assembler diagnostic for our program was:

```text
program.bfa:2898: error: tofino supports up to 12 stages, using 14
```

This is an actual target-resource failure, rather than a syntax error, unsupported
language feature, timeout or compiler crash. The repair uses **more total SRAM**
while placing all required work within the stage limit. This demonstrates why
minimizing total memory alone does not capture the fit decision.

## Workload and intervention

The synthetic service-policy workload has twelve sequential token-translation
tables, each with 16,384 entries, and eight independent flow/tenant lookup tables,
each with 114,688 entries. Chain keys and action parameters are 32 bits. Side
keys are 80 bits: a 32-bit flow identifier plus a 48-bit tenant identifier.
The logical requirement is 1,114,112 entries across twenty tables.

An experimental EtherType carries a 50-byte header; Ethernet plus that header is
64 bytes. The header contains identifiers, the token, eight result words and
32 reserved bits. Table actions update the emitted token or result words; misses
leave those fields unchanged. The egress program parses and emits the same
headers, and the ingress program selects the input port as the output port.
This is a constructed resource-pressure workload, not a production application.

The only repair is one directive before each `side_0` through `side_7` table:

```p4
@pack(3)
```

The compiler documents this as selecting exact-match entries per wide table
word, with values from 1 through 9; larger values need not help placement.
[Pinned upstream pragma definition](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/common/pragma/pragmas.cpp).
The successful context reports packing factor 3 for every side-table fragment.
Its chain tables retain packing factor 4.

## Controls and capacity verification

Both sources are named `program.p4`, contain 322 lines and occupy 15,034 bytes.
Four fixed-width annotation-or-blank lines precede every table. Exactly eight
lines differ. Replacing annotation text with spaces makes the files byte-identical,
including source line and code positions. This matters because generated compiler
names can incorporate a source basename and line number; the earlier flat-file
discovery was therefore repeated with this control.

Both compiles used identical hashes for eleven installed executable files and
45 support files, the same immutable container image and the same target/flags.
The BFRT schemas contain 29 tables and compare identically after removing
annotation metadata, including identifiers, key widths, action parameters,
default-action properties and declared capacities.

For all twenty application tables, `context.json` retains the required logical
size, and the sum of physical stage capacities is at least that size. Each
repaired side table has 116,736 physical slots for its 114,688-entry requirement;
each chain table has 16,384. These rounded physical allocations need not match
the baseline: several baseline side tables had 117,760 slots. We claim unchanged
**required/logical capacity**, not identical physical allocation or live insertion
of every possible entry set.

The observed stage identifiers are `0..13` for the failure and `0..11` for the
repair. `resources.mau.nStages` equals 12 in both reports because it describes
target capacity, not observed allocation. Repaired per-stage SRAM use is
`80, 80, 74` repeated four times, totaling 936. The failing allocation totals 880
across fourteen stages and is not an executable deployment.

## Toolchain and reproduction

The public compiler source is pinned to
`ca5aef25de1f847b5c88a05af583506484927015`, archive SHA-256
`0e6df6b7fafa5d04038964a6c1318c47e3b5e96be89ecf9be923572d7512fc25`.
Its backend reports version `9.13.4`. The host toolchain is ARM64 Ubuntu 24.04
with Clang 18, Release `-O1 -DNDEBUG`; upstream backend assertions remain enabled.
No upstream compiler source was modified. The build/install helper was validated
at two jobs.

The measured image ID is
`sha256:5b27c37f3835c646c4a7f87c64de986a5fd5d71170b3f8b91e4bfbb908763af9`.
Both invocations used `/work/.cache/p4c-tofino/install/bin/p4c` with
`--target tofino --arch tna -g -o /output/compiler`, a read-only workspace mount,
network disabled and a 600-second timeout. The JSON companion includes exact
compiler, support-file, source, probe, generator and target-binary hashes. The
recorded generator snapshot was checked to reproduce both sources exactly.

On an ARM64 machine with Docker available, build the pinned toolchain and generate
a fresh controlled case:

```sh
python3 -m scripts.build_p4_compiler --jobs 2
python3 examples/p4-fit/generate.py \
  --output artifacts/p4-fit-reproduction/input \
  --depth 12 --sides 8 --chain-entries 16384 --side-entries 114688 \
  --tenant-bits 48 --policy all --stable-layout
python3 -m scripts.p4_compile_probe \
  artifacts/p4-fit-reproduction/input/default/program.p4 \
  --output artifacts/p4-fit-reproduction/default --target tofino --timeout 600
python3 -m scripts.p4_compile_probe \
  artifacts/p4-fit-reproduction/input/side-pack-3/program.p4 \
  --output artifacts/p4-fit-reproduction/side-pack-3 --target tofino --timeout 600
```

Use new paths if destinations exist. The probe resolves the locally rebuilt image
tag to its immutable ID and records it; a rebuild need not reproduce this machine's
image or binary hashes. Base image/source pins and dependency records provide
provenance, but apt repositories are not snapshot-pinned, so this is not a fully
hermetic-build claim. Initial build attempts failed for missing RapidJSON,
PyInstaller/libpython and PyYAML dependencies; those failures were retained and
the required dependencies added to the container before the successful build.

## Complete discovery snapshot

These are all thirteen completed probes present when the evidence was captured.
Dimensions use binary K (1,024 entries). They are adaptive discovery trials, not
independent held-out samples or an estimate of model success rate.

| Probe | Outcome | Stages | SRAM |
| --- | --- | ---: | ---: |
| Small qualification baseline | Fit | 1 | 8 |
| Chain 8 / side 4, 16K / 32K, tenant 32 | Fit | 8 | 160 |
| Chain 12 / side 8, 16K / 128K, tenant 32 | Fit | 12 | 864 |
| Chain 12 / side 8, 16K / 144K, tenant 32 | Fit | 12 | 960 |
| Chain 12 / side 8, 16K / 144K, tenant 24 | Stage-limit failure | 15 | 900 |
| Tenant 48, 16K / 112K, flat baseline | Stage-limit failure | 14 | 880 |
| Same flat case, side pack 3 | Fit; filename/line positions were not controlled | 12 | 936 |
| Same case, stable-layout baseline | Stage-limit failure | 14 | 880 |
| Same case, stable-layout side pack 3 | Fit | 12 | 936 |
| Upstream issue 5192, Tofino2 | Stateful-ALU input allocation rejection | 9 allocated | 13 allocated |
| SwitchML original | PHV allocation rejection with container-size constraint | — | — |
| SwitchML without container-size directive | 600-second timeout | — | — |
| SwitchML without both allocation directives | 600-second timeout | — | — |

The issue-5192 specimen uses three distinct 32-bit inputs in a stateful operation;
the backend's search-bus code has two source slots. No safe pragma-only repair
was established. SwitchML timeouts are censored outcomes, not proof of infeasibility.
The upstream specimens remain separately attributed and are not vendored in this
report. [Issue 5192](https://github.com/p4lang/p4c/issues/5192),
[SwitchML source pin](https://github.com/p4lang/p4app-switchML/tree/b5c74a04fa66faa82acab154a4e21c8f76d913f2).

## What this establishes

We now have the required compiler decision boundary: a normal failed allocation,
a small developer-style annotation intervention and a successful target artifact
with unchanged requirements. It is a useful feasibility reference for a harness
that proposes and validates directives.

No packet or hardware execution, complete semantic-equivalence proof, inference-
policy model call or held-out model evaluation has occurred in this qualification.
Codex agents performed discovery and implementation; this is not evidence that
Jev or a stronger adviser inferred the repair independently. The answer is now
known and must be withheld from any future evaluation cases. Compilation wall
times are recorded only as provenance because other compilation work ran
concurrently; they are not comparative latency measurements. Maintenance savings,
model advantage and transfer to a current ASIC remain unproved. Tofino is the
user-authorized research testbed, not a new career-target recommendation.
