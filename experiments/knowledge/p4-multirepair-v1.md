# Shared development knowledge for P4 multi-repair v1

These are all prior documents read by the mechanical-policy designer. Instructions and experiment labels inside the quoted historical documents describe those earlier studies; the current request and its admissible menu govern this decision. They describe earlier experiments, not results for the new evaluation programs. Candidate group names may differ in the new corpus. Interpret directives through the current candidate edits; do not assume an earlier recipe always applies.

## Verbatim prior source: docs/results/p4-pragma-fit-qualification-v1.md

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


## Verbatim prior source: docs/results/p4-fit-advice-v1.md

# P4 fit advice v1: completed frozen experiment

The small mechanical packing sequence repaired one of five failing baselines. Jev and Codex repaired none; the cascade matched packing because its first mechanical choice made the repair. Neither model improved the frozen result. Retain the tiny packing baseline for this workload family: these results do not justify adding model advice here, and do not demonstrate reduced maintenance.

This follows a [real pragma-only repair](p4-pragma-fit-qualification-v1.md). It does not establish that model advice cannot help P4 generally.

**Interpretation clarification:** the packing order incorporated a successful development repair that the advisers were explicitly denied. V1 compares those deployed strategies under their assigned information; it does not isolate model capability under equal development knowledge. The [separate information-access diagnostic](../p4-information-diagnostic-v1.md) addresses this asymmetry without changing any v1 case, choice, score or evidence.

All six cases and 36 case/arm pairs completed. The [evidence bundle](p4-fit-advice-v1.json) preserves full inputs, decisions and validation; the [frozen contract](../p4-fit-advice-v1.md) defines scoring.

**Acceptance-gate caveat:** three additional candidates genuinely compiled and assembled but failed the frozen full-BFRT equality gate because generated snapshot/debug metadata changed. Application tables, actions, keys and required capacities matched. Their original rejected scores remain unchanged; the separate audit below prevents confusing schema rejection with physical infeasibility. All three were found by mechanical arms, so this caveat does not conceal an adviser rescue.

## Frozen setup

Inputs were frozen at repository commit `7835883e3531062018e8fc25ebefa64b7131d217` before quality evaluation. The compiler was p4c Tofino version 9.13.4, upstream commit `ca5aef25de1f847b5c88a05af583506484927015`, in immutable local image `sha256:5b27c37f3835c646c4a7f87c64de986a5fd5d71170b3f8b91e4bfbb908763af9`. Every probe used `--target tofino --arch tna -g -o /output/compiler`, a 120-second deadline, a read-only repository mount and no container network. Tofino is a research testbed here, not a recommendation for a current hardware career target.

Every case has eight side tables and 16,384 required entries per chain table. Chain keys are 32 bits. The header remains 50 bytes, 64 including Ethernet; capacities and behavior are fixed across policies.

| Case | Chain tables | Tenant bits | Side-key bits | Required entries per side table | Unannotated baseline |
| --- | ---: | ---: | ---: | ---: | --- |
| tenant8 | 12 | 8 | 40 | 196,608 | Compiler failure |
| tenant16 | 12 | 16 | 48 | 184,320 | Compiler failure |
| tenant40 | 12 | 40 | 72 | 147,456 | Compiler failure |
| tenant56 | 12 | 56 | 88 | 114,688 | Compiler failure |
| tenant64 | 12 | 64 | 96 | 106,496 | Compiler failure |
| depth10-tenant48 | 10 | 48 | 80 | 114,688 | Accepted fit |

These are six previously uncompiled combinations from one synthetic family, not independent applications. Known tenant-48 and tenant-24 development cases were excluded.

All 25 source policies use the basename `program.p4` and fixed annotation slots. Erasing the allowlisted annotation text leaves identical bytes and code positions within each case. Candidates are **alternatives applied to the original/default source, not cumulative patches**. The menu permits uniform chain/side packing factors 1–9, placement priorities and disabling immediate action data; it does not permit combinations or individual-table tuning.

Arms share the default, stop at an accepted fit and otherwise have three attempts. Jev (`jev-1.14.0`) and Codex (`gpt-6-luna`, `max`) see default source, admissible directives, generic compiler observations and only their own history. Random uses seed 20261018; sequences appear below.

## Frozen outcomes

Accepted fit requires compiler/assembler/manifest/binary acceptance, unchanged full BFRT schema after excluding annotation metadata, unchanged logical capacities, and physical capacity sufficient for every requested table. This is compiler-reported fit, not executed packet correctness.

| Arm | Accepted cases / 6 | Rescues of 5 failing defaults | Repair queries | Provider failures | Input tokens | Output tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| packing | 2 | 1 | 13 | 0 | 0 | 0 |
| priority | 1 | 0 | 15 | 0 | 0 | 0 |
| random | 1 | 0 | 15 | 0 | 0 | 0 |
| jev | 1 | 0 | 15 | 0 | 180,713 | 2,872 |
| codex | 1 | 0 | 15 | 0 | 420,183 | 59,066 |
| cascade | 2 | 1 | 13 | 0 | 173,920 | 16,700 |

Packing is the best single three-attempt mechanical comparator. Jev and Codex each have zero paired rescues and one regression against packing: net −1. Cascade has zero paired rescues and zero regressions: net 0. All fail the preregistered descriptive gate of at least two net additional fits among six cases. These small, related samples do not support statistical superiority claims.

The mechanical union rescues only tenant56, spending 43 virtual queries with a larger nine-attempt allowance. Cascade repairs tenant56 at its first mechanical step; Jev and Codex add nothing over their own preceding prefixes. Third-tier Codex increment: zero.

## Complete decision sequences

`F` = compiler rejection; `G` = genuine compiler fit rejected by the frozen schema gate; `S` = accepted fit. Every listed choice consumes one query. Cascade entries show the provider. The six omitted repair sequences for depth10-tenant48 are explicitly zero-query stops on its accepted baseline.

| Case | Arm | Choices in order |
| --- | --- | --- |
| tenant8 | packing | `side-pack-3` **F** → `side-pack-2` **F** → `side-pack-4` **F** |
| tenant8 | priority | `chain-priority` **F** → `side-priority` **F** → `side-later` **F** |
| tenant8 | random | `side-pack-9` **F** → `side-pack-2` **F** → `side-no-immediate` **G** |
| tenant8 | jev | `chain-pack-4` **F** → `chain-pack-8` **F** → `chain-priority` **F** |
| tenant8 | codex | `chain-priority` **F** → `side-pack-9` **F** → `side-pack-8` **F** |
| tenant8 | cascade | `side-pack-3` (mechanical) **F** → `chain-pack-3` (jev) **F** → `chain-priority` (codex) **F** |
| tenant16 | packing | `side-pack-3` **F** → `side-pack-2` **F** → `side-pack-4` **F** |
| tenant16 | priority | `chain-priority` **F** → `side-priority` **F** → `side-later` **F** |
| tenant16 | random | `chain-pack-1` **F** → `chain-pack-7` **F** → `side-no-immediate` **F** |
| tenant16 | jev | `chain-pack-4` **F** → `chain-pack-8` **F** → `side-pack-8` **F** |
| tenant16 | codex | `chain-priority` **F** → `side-later` **F** → `side-pack-8` **F** |
| tenant16 | cascade | `side-pack-3` (mechanical) **F** → `chain-priority` (jev) **F** → `side-pack-8` (codex) **F** |
| tenant40 | packing | `side-pack-3` **F** → `side-pack-2` **F** → `side-pack-4` **F** |
| tenant40 | priority | `chain-priority` **F** → `side-priority` **F** → `side-later` **F** |
| tenant40 | random | `chain-pack-1` **F** → `chain-pack-8` **F** → `side-pack-6` **F** |
| tenant40 | jev | `chain-pack-4` **F** → `chain-pack-8` **F** → `side-pack-8` **F** |
| tenant40 | codex | `chain-priority` **F** → `side-priority` **F** → `side-pack-9` **F** |
| tenant40 | cascade | `side-pack-3` (mechanical) **F** → `chain-pack-3` (jev) **F** → `chain-priority` (codex) **F** |
| tenant56 | packing | `side-pack-3` **S** |
| tenant56 | priority | `chain-priority` **F** → `side-priority` **F** → `side-later` **F** |
| tenant56 | random | `side-pack-5` **F** → `chain-priority` **F** → `chain-pack-2` **F** |
| tenant56 | jev | `chain-pack-4` **F** → `chain-pack-8` **F** → `chain-pack-2` **F** |
| tenant56 | codex | `side-later` **F** → `side-pack-4` **F** → `chain-priority` **F** |
| tenant56 | cascade | `side-pack-3` (mechanical) **S** |
| tenant64 | packing | `side-pack-3` **F** → `side-pack-2` **G** → `side-pack-4` **G** |
| tenant64 | priority | `chain-priority` **F** → `side-priority` **F** → `side-later` **F** |
| tenant64 | random | `chain-pack-8` **F** → `side-pack-8` **F** → `side-pack-6` **F** |
| tenant64 | jev | `chain-pack-4` **F** → `chain-pack-8` **F** → `chain-pack-2` **F** |
| tenant64 | codex | `chain-priority` **F** → `side-later` **F** → `side-priority` **F** |
| tenant64 | cascade | `side-pack-3` (mechanical) **F** → `chain-pack-3` (jev) **F** → `chain-priority` (codex) **F** |
| depth10-tenant48 | packing | Baseline accepted; 0 repair queries |
| depth10-tenant48 | priority | Baseline accepted; 0 repair queries |
| depth10-tenant48 | random | Baseline accepted; 0 repair queries |
| depth10-tenant48 | jev | Baseline accepted; 0 repair queries |
| depth10-tenant48 | codex | Baseline accepted; 0 repair queries |
| depth10-tenant48 | cascade | Baseline accepted; 0 repair queries |

## Compiler-fit and schema audit

The 64 unique physical probes comprise six shared defaults and 58 distinct repair candidates. They produced five raw compiler fits, 58 stage-capacity failures, and one compiler-estimated power-budget rejection (`tenant64/side-pack-8`: the 40 W budget exceeded by an estimated 6.24 W). This is an estimate from the compiler, not measured hardware power. There were no timeouts or provider errors. The 86 virtual repair queries charge each arm for its choices even when a physical compile is shared; they are not another 86 physical compilations.

| Raw compiler fit | Occupied stages | SRAM blocks | Frozen acceptance |
| --- | ---: | ---: | --- |
| `depth10-tenant48/default` | 12 | 864 | Accepted |
| `tenant56/side-pack-3` | 12 | 936 | Accepted |
| `tenant64/side-pack-2` | 12 | 928 | Rejected: BFRT equality |
| `tenant64/side-pack-4` | 12 | 928 | Rejected: BFRT equality |
| `tenant8/side-no-immediate` | 12 | 944 | Rejected: BFRT equality |

Tenant56 fails at 14 occupied stages/937 SRAM blocks. Eight side-table `@pack(3)` annotations yield 12 stages/936 SRAM blocks and pass. Logical capacities stay fixed; changed physical allocations still satisfy them. Stage usage comes from actual stage IDs.

All three schema-rejected fits preserve required tables, logical capacities and sufficient physical allocations. BFRT normalization leaves only the generated `pipe.snapshot.cfg` (`SnapshotCfg`) ID different: `depends_on` has the same IDs reordered, and outer table order also differs. Application tables/actions/keys match; complete BFRT objects and differences are in the bundle.

The gate is stricter than preserving the application interface. Scores remain unchanged; a revised gate needs a new frozen experiment. Interface matching is not packet execution or full semantic proof.

## Usage, identities and reproducibility

There were 38 valid provider responses: 19 Jev and 19 Codex, including four of each in the cascade. Jev reported 229,582 input and 3,636 output tokens. Codex reported 545,234 input and 75,002 output tokens, including 33,024 cached-input and 73,689 reasoning-output tokens. Total input plus output is **853,454 tokens**. Cached-input and reasoning-output counts are subsets and must not be added again. Jev did not separately report those subsets. Compiler/provider elapsed values are retained as observations, not a latency comparison: caching and other local activity prevent that interpretation.

The JSON retains complete frozen source/toolchain identities, 64 probe receipts, manifests/binary hashes, capacities/placements, diagnostics, all 86 complete decision packets and 38 provider responses. Packets are deduplicated without truncation. Probe paths use `$REPO`; hashes identify original bytes. No credentials are included.

Follow the [run/resume instructions](../p4-fit-advice-v1.md#running-and-resuming) and [toolchain build instructions](p4-pragma-fit-qualification-v1.md). A rebuilt image needs a separate replication configuration; model choices may vary.

All frozen inputs and 64 probe receipts were checked against their file hashes. An independent read-only audit verified all 36 arms, real manifest/binary outputs, capacities and the three schema-only rejections. Cached full resume completed without additional provider calls or compiles. Repository verification reported 391 tests passed and Ruff clean.

A broader application corpus, richer action space or policy-maintenance study would be separate experiments. No hardware runtime, maintenance superiority or current ASIC career recommendation follows from this pilot.


## Verbatim prior source: docs/results/p4-information-diagnostic-v1.md

# P4 information-access diagnostic v1

Both advisers selected the demonstrated `side-pack-3` strategy when given the worked example. It repaired tenant56 and failed on tenant8, exactly matching the fixed one-choice mechanical reference. **There was no improvement over that mechanical choice.** Without the example, each adviser chose other policies that failed on both cases.

This is consistent with information access affecting choices, but one observation per cell cannot separate that effect from response variability. The result demonstrates neither independent discovery nor an ability to discriminate when the example applies. It also does not show that the models are incapable of making useful compiler decisions.

The [frozen diagnostic contract](../p4-information-diagnostic-v1.md) and [complete evidence bundle](p4-information-diagnostic-v1.json) accompany this report. The [original v1 results](p4-fit-advice-v1.md) remain unchanged.

## Setup

The original mechanical recipe incorporated a development repair that advisers were denied. Its comparison evaluates those deployed strategies; it does not isolate model capability under equal development knowledge.

This retrospective diagnostic deliberately selects two already-measured cases: tenant56, where a repair exists, and tenant8, where that same choice fails. Eight isolated requests cover two providers × two cases × two information conditions, with one choice per cell. Jev is pinned to `jev-1.14.0`; Codex uses `gpt-6-luna`, effort `max`. A counterbalanced schedule varies condition order; requests share no conversational history. There is no seed control or variability estimate.

Both conditions receive the original baseline packet, admissible menu and neutral pinned compiler documentation. Only the worked-example condition receives the verified development tenant48 repair: eight side-table `@pack(3)` annotations turn a 14-stage rejection into a 12-stage fit. Neither condition contains the tested cases’ candidate outcomes. All choices were saved before evaluation. Candidates replace the original proposal; annotations do not accumulate.

## All eight decisions

“Fit” means both raw compiler acceptance and the original strict capacity/full-BFRT gate pass. None of these selected candidates had a raw-fit/schema-rejection discrepancy.

| Case | Provider | Information | Chosen policy | Raw fit | Strict fit | Input tokens | Output tokens |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| tenant56 | Jev | Baseline | side-priority | No | No | 8,894 | 199 |
| tenant56 | Codex | Baseline | chain-priority | No | No | 25,068 | 1,942 |
| tenant56 | Jev | Worked example | side-pack-3 | Yes | Yes | 9,117 | 200 |
| tenant56 | Codex | Worked example | side-pack-3 | Yes | Yes | 25,268 | 856 |
| tenant8 | Jev | Baseline | side-later | No | No | 8,903 | 199 |
| tenant8 | Codex | Baseline | chain-priority | No | No | 25,068 | 1,999 |
| tenant8 | Jev | Worked example | side-pack-3 | No | No | 9,126 | 200 |
| tenant8 | Codex | Worked example | side-pack-3 | No | No | 25,270 | 890 |

Each provider changes from zero to one strict fit across these two cases when shown the example. The equal-budget mechanical reference chooses `side-pack-3` once per case and also gets one fit. This diagnostic is not a comparison against three mechanical attempts or an escalation trajectory.

## Evidence and usage

All eight evaluations used verified cached v1 compiler evidence: **zero new compilations**, eight virtual queries, eight valid responses, zero provider errors and no selected-probe timeouts. The accepted tenant56 repair occupies 12 stages and 936 SRAM blocks, preserves logical capacities and full BFRT equality excluding annotation metadata, and has sufficient physical capacity. Other selected policies fail stage capacity. No packets were executed on hardware.

Jev reported 36,040 input and 798 output tokens; Codex reported 100,674 input and 5,687 output tokens. Total: **136,714 input + 6,485 output = 143,199 tokens**. Codex reported zero cached-input tokens; Jev does not report that field. The 5,404 reasoning-output tokens are already included in output and must not be added again. Elapsed values are observations, not comparative latency measurements.

The bundle embeds all eight packets, eight decisions, freeze, choices summary, evaluation, config, exact source text, original compiler receipts, manifests/binary hashes, diagnostics and capacity/schema audits for six selected candidate probes plus two baselines. It includes the development-example evidence and full frozen compiler identities. Host paths are normalized to `$REPO`; hashes identify original bytes. Binary bytes and full compiler IR are not embedded.

The compiler is p4c Tofino 9.13.4 at `ca5aef25de1f847b5c88a05af583506484927015`, using local immutable image `sha256:5b27c37f3835c646c4a7f87c64de986a5fd5d71170b3f8b91e4bfbb908763af9`, target `tofino`, architecture `tna`, flags `-g -o /output/compiler`. Reproduction requires the pinned toolchain; a rebuilt image needs a separate replication configuration. Recorded model decisions are auditable without calling providers again, but new responses may differ.

Frozen inputs, packet derivations, response mappings and referenced compiler receipts passed independent audit. All original v1 evidence remained unchanged. Focused validation passed 27 tests and Ruff; cached choose/evaluate resume passed with provider and compiler calls disabled.

These observations do not establish causality, generalization, escalation benefit, or reduced policy maintenance. A stronger next quality experiment would need independent application families, equal development information, a frozen application-interface contract and fresh cases hidden from policy designers. This diagnostic alone does not justify a custom decision model, MCP service or additional model infrastructure.


## Verbatim prior source: docs/p4-information-diagnostic-v1.md

# P4 information-access diagnostic v1

This is a retrospective diagnostic, not a new held-out trial. The [original result](results/p4-fit-advice-v1.md) remains unchanged. Its fixed packing order incorporated a development repair that the advisers were explicitly denied. That comparison evaluates the deployed strategies; it does not isolate model capability under equal development knowledge.

## Question and fixed scope

Does providing that same development repair change the advisers' first choices? Use two already-measured cases, `tenant56` (a known repair exists) and `tenant8` (the example's side-pack-3 choice does not repair it). These cases were deliberately selected after seeing v1. Do not report generalization, statistical superiority, or an escalation win.

There are exactly eight isolated requests: two cases × two pinned providers × two information conditions, one choice each. Providers remain Jev `jev-1.14.0` and Codex `gpt-6-luna` with `max` effort. The [configuration](../experiments/p4-information-diagnostic-v1.json) fixes a counterbalanced schedule so each provider sees the example condition first on one case and second on the other. Requests do not share conversational history. There is one observation per cell, no seed control and no estimate of response variability.

Both conditions reuse the original baseline-only packet, candidate menu and mapping. They receive the same neutral documentation of packing, placement priority and immediate action data, plus explicit wording that candidates replace the current proposal rather than accumulate edits. Only the worked-example condition additionally receives the verified tenant-48 development repair: eight side-table packing-3 annotations change a 14-stage rejection into a 12-stage fit, with unchanged required capacities and interface. This is the example used to inform the mechanical recipe; no tenant56 or tenant8 candidate result is included.

The neutral documentation is paraphrased from pinned upstream definitions of `PragmaPack`, `PragmaPlacementPriority` and `PragmaImmediate` in [pragmas.cpp](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/common/pragma/pragmas.cpp). Its effect is shared by both conditions; comparison with historical v1 calls cannot isolate the example's effect. The development facts come from the [qualification evidence](results/p4-pragma-fit-qualification-v1.json).

## Execution and acceptance

Freeze config, documentation, code/adapters, source packets, prepared requests, source/compiler identities and schedule before the first call. Complete and save all eight decisions before evaluating any chosen candidate. Never adapt a later request to an earlier response or outcome. Invalid responses and provider failures consume their one attempt; interrupted calls are retained with unknown usage rather than repeated. Existing provider deadlines apply; record token usage without imposing a token cap.

After the choice phase, read only verified existing compiler evidence where available. Compile an unseen chosen policy at most once in a new diagnostic output directory, with the same pinned compiler/target/flags and 120-second timeout. Never mutate the v1 run. Evaluation retains the v1 strict capacity/full-schema acceptance rule and separately reports raw compiler fit and rejection reasons; it does not change that contract retrospectively. Elapsed time and cached compilations are not a latency comparison.

Report each choice, change between conditions, strict acceptance, raw fit, provider failure and reported usage. The mechanical reference is its fixed first choice, side-pack-3, on each case; do not compare one adviser call with a three-attempt search as if budgets were equal. Agreement with that recipe is not independent discovery. Both useful transfer and blind copying of a failing choice are possible outcomes.

## Decision after the diagnostic

Changes between conditions would be consistent with information access affecting decisions, but one observation per cell cannot separate that effect from response variability. Repeating a demonstrated strategy is not evidence of generalization or escalation benefit. If neither adviser uses the example usefully, inspect representation or adviser suitability before adding model infrastructure. No custom model, MCP service, broader benchmark or extra tuning round follows automatically. A useful next quality test would require independently verified repairs from different application families, equal development information, a frozen application-interface contract and fresh cases hidden from policy designers.

Frozen v1 results and negative findings remain part of the public record. Human maintenance benefit requires a separate measured update study.


## Verbatim new development source: examples/p4-multirepair/development.md

# Shared development knowledge for p4-multirepair-v1

This packet contains DEVELOPMENT knowledge available equally to the fixed-policy designer and all advisers. It contains no held-out case identities, dimensions, outcomes or repair labels. It supplements the four previously published background documents included verbatim by the experiment coordinator: the original pragma qualification, fit-advice results, information-access diagnostic results, and its design. These are synthetic research examples, not independent production applications.

## Uniform candidate grammar and groups

Every final case has the same 25 nondefault candidate IDs: `a-pack-1` through `a-pack-9`, `b-pack-1` through `b-pack-9`, `a-no-immediate`, `b-no-immediate`, `all-no-immediate`, `a-priority`, `b-priority`, `a-later`, `b-later`. The unannotated `default` is additional. Each candidate replaces the baseline proposal; annotations never accumulate. No candidate reduces capacity or changes packet code.

`a-pack-N` applies `@pack(N)` uniformly to group a; b is analogous. `*-no-immediate` applies `@immediate(0)`, and `all-no-immediate` covers both groups. `*-priority` applies `@placement_priority(10)` and `*-later` applies `@placement_priority(-10)`. Concrete candidate edits and source show which tables each group contains; IDs carry no fit labels.

In the two qualified development anchors below, **a = side tables; b = chain tables**. The newly explored templates use these explicit mappings: without independent policy lookups, diamonds have a=fork lookups/b=join tables; exclusive chains have a=first branch/b=second branch; accumulating action chains have a=even steps/b=odd steps. With independent policy lookups enabled, a=those independent tables and b=the whole primary dependency graph. A group label is a source-defined set, not a universal semantic role.

## Verified packing development anchor (previously published)

The old tenant48 program has twelve dependent chain tables, each requiring 16,384 entries, and eight independent side tables, each requiring 114,688 entries. Side keys are 80 bits (flow32+tenant48), with 32-bit action values. The unannotated source fails at14 occupied stages/880 SRAM blocks. Applying `@pack(3)` to all eight side tables, now named **a-pack-3**, yields a12-stage fit/936 SRAM blocks. Required logical capacities and the full control-plane interface excluding annotations match. Physical slots can change but satisfy requested capacities. This was a development discovery, not an unseen evaluation result.

The published v1 report also describes old tenant64 raw fits for side packing2 and4 (here a-pack-2/a-pack-4), rejected by the original stricter gate only because generated SnapshotCfg metadata differed. These observations are old development knowledge. They are not proof that those policies generalize.

## Verified immediate-data development anchor (new control graph)

The new overlapping-admission example descends from the published tenant8 chain/side program. Two overlapping conditions alter its control graph: tenant bit0 enables side tables0–3, and bit1 enables side tables4–7. Either group, both groups, or neither may execute. All twelve dependent chain tables still execute. This is a meaningful control change within the same ancestry, not an independent application.

Required sizes are twelve chain tables×16,384 and eight side tables×196,608. Side exact-match keys are40 bits (flow32+tenant8), action values32 bits. The two sources differ only in eight fixed-width annotation slots, retaining the basename and all source positions.

| Proposal | Raw compiler/assembler | Occupied stages | SRAM blocks | Logical/physical capacities and new full-BFRT gate |
| --- | --- | ---: | ---: | --- |
| Unannotated | Failure: stage overflow |15|942|Preserved, but compiler rejects |
| **a-no-immediate** | Successful manifest and nonempty binary |12|944|Pass |

The new gate ignores annotation metadata and list order plus only the generated `pipe.snapshot.cfg` SnapshotCfg object's own ID. It preserves application IDs and all other schema fields. Exactly two compilations qualified this development pair. No model or hidden-test result informed it. Source and evidence identities are recorded in `examples/p4-immediate-dev/qualification.json`; the designer need not inspect that raw evidence because the relevant facts are reproduced here.

## Other development observations already disclosed

Three additional structures were explored: repeated parallel lookup/join diamonds; mutually exclusive service chains; and chains accumulating multiple observable outputs. Their low-pressure forms genuinely compile, confirming feasible dependency paths. High-pressure forms encounter real stage-capacity failures. Rigid layers leave little scheduling freedom and often already use efficient compiler-selected packing. Forcing packing3 worsened one baseline fit to18 stages. Large action payloads already reside in action-data memory, making `immediate(0)` inert; smaller total parameters were then explored. Static checks verify annotation-only fixed positions, full capacity inventory and emitted dataflow; no hardware packets were run.

Adding independent policy lookups alongside a diamond graph produced a near-boundary default14-stage failure; forcing group-a packing3 worsened it to15, and a-no-immediate still used14. Prioritizing the primary graph (b-priority) or delaying independent tables (a-later) also left that development case at14. Thus placement priority was a hypothesis, not a demonstrated repair. No new packing or priority rescue has been disclosed to the designer. All discovery attempts remain recorded separately and do not change these qualified anchors.

## Neutral pinned compiler documentation

These descriptions paraphrase `PragmaPack`, `PragmaPlacementPriority` and `PragmaImmediate` in p4c commit `ca5aef25de1f847b5c88a05af583506484927015`, `backends/tofino/bf-p4c/common/pragma/pragmas.cpp`.

`@pack(N)` requests1–9 exact-match entries per wide table word, not entries per SRAM unit. Wider packing may consume more memory units per word and can prevent placement. Larger N is not necessarily better. `@placement_priority(N)` changes consideration order: default0, positive earlier and negative later. `@immediate(0)` disables the default attempt to put action parameters in match overhead instead of a separate action-data table. It can change allocation geometry; it need not reduce total SRAM. None guarantees fit.

All claims are compiler-reported fit and declared interface/capacity preservation. They do not establish executed packet equivalence, hardware performance, model generalization, or reduced policy maintenance. No hidden test choice should be inferred from the development anchors.

## Verbatim additional document read by the policy designer

Source: `examples/p4-immediate-dev/README.md`

# Immediate-allocation development example

This is a **development example**, available equally to the mechanical-policy designer and model advisers. It is not an unseen evaluation case.

The baseline fails because its placement uses 15 stages on a target with 12. Adding `@immediate(0)` to all eight side tables produces a compiler-accepted binary using 12 stages. SRAM usage changes from 942 to 944 blocks. All 20 application tables retain their declared logical capacities and sufficient physical allocation; the new experiment's full BFRT comparison passes with its narrowly specified generated SnapshotCfg ID exclusion.

| Variant | Compiler result | Actual stages | SRAM blocks | Interface/capacity gate |
|---|---|---:|---:|---|
| `default/program.p4` | Stage overflow, exit 2 | 15 | 942 | Pass |
| `side-no-immediate/program.p4` | Successful manifest and nonempty binary, exit 0 | 12 | 944 | Pass |

The two sources differ only in eight fixed-width annotation slots. Source basename and all line positions remain unchanged. The generated manifest covers every application table. The table group mapping used by the new experiment is **a = eight side tables**, **b = twelve chain tables**; the demonstrated action is therefore **a-no-immediate**.

The baseline descends from the published tenant8 chain/side source. The structural change is two overlapping admission predicates: `tenant & 1` enables side0–3, and `tenant & 2` enables side4–7. A packet can execute either group, both, or neither; the twelve dependent token-chain tables still execute. This changes the control graph, but remains a synthetic derivative of the previous family, not an independent production application. Table dimensions remain eight side tables of 196608 entries with 40-bit keys and 32-bit action values, plus twelve chain tables of 16384 entries.

Exactly two development compilations were made. Both use the same immutable image, compiler executable/support hashes, target and flags. Baseline elapsed time was about 63 seconds; the repair about 8.4 seconds. These are provenance timings under concurrent development load, not performance measurements. No model calls or hidden test outcomes were used.

`qualification.json` records the source and receipt hashes, complete compiler identity, source edits, application capacities and acceptance audits. Raw receipts and compiler outputs are in `artifacts/p4-immediate-dev/overlapping-admission-196608/`. Run `.venv/bin/python examples/p4-immediate-dev/qualify.py` to repeat the evidence checks without compiling. `develop.py overlapping-admission` regenerates the development sources and compiles only if corresponding receipts are absent.

This establishes compiler-reported fit under the stated acceptance contract. It does not establish packet execution, full semantic equivalence, hardware performance or adviser generalization.
