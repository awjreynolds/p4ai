# P4 fit advice v1: completed frozen experiment

The small mechanical packing sequence repaired one of five failing baselines. Jev and Codex repaired none; the cascade matched packing because its first mechanical choice made the repair. Neither model improved the frozen result. Retain the tiny packing baseline for this workload family: these results do not justify adding model advice here, and do not demonstrate reduced maintenance.

This follows a [real pragma-only repair](p4-pragma-fit-qualification-v1.md). It does not establish that model advice cannot help P4 generally.

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
