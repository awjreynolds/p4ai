# Bounded advice for P4 fit: frozen within-family pilot

This pilot follows the [qualified pragma-only repair](results/p4-pragma-fit-qualification-v1.md). It asks whether advisers infer useful allocation directives from compiler feedback within three attempts. It does not measure generalization across applications, runtime correctness, or maintenance savings. The tenant-48 repair and the inspected tenant-24 failure are development evidence and excluded from scoring.

The [case configuration](../experiments/p4-fit-advice-v1.json) fixes six previously uncompiled parameter combinations before evaluation. All belong to one synthetic workload family. Include every case: default successes, unrepaired failures, provider errors and timeouts. Do not manufacture failure or select cases after seeing outcomes.

The generator writes all 25 policies using fixed basenames and source positions. Erasing annotation text must leave byte-identical source within a case. The compiler, target, flags, required capacities and program semantics are fixed. The menu comprises placement priorities, disabling immediate action data, and packing factors 1 through 9 applied uniformly to either side or chain tables. No semantic assertions or capacity reductions are allowed.

Each arm shares the unannotated default compile. If it fits, stop with zero repair calls. Otherwise each arm has three attempts and at most three compiler queries:

| Arm | Choices |
| --- | --- |
| Packing | Side packing 3, then 2, then 4 |
| Priority | Chain priority, side priority, side later |
| Random | Three distinct policies, seed 20261018 |
| Jev | Up to three adaptive choices, pinned `jev-1.14.0` |
| Codex | Up to three adaptive choices, `gpt-6-luna`, effort `max` |
| Cascade | Side packing 3; on failure Jev; on further failure Codex |

Stop an arm at its first accepted fit. Provider failures, invalid responses, duplicate choices and out-of-menu choices consume attempts. Do not retry them automatically. Each compile has a 120-second limit, identical for every arm. Timeout is a censored search outcome, not proof of resource infeasibility. Requests have the existing adapters' fixed provider deadlines. No token cap; record reported usage.

Advisers receive the default source, exact admissible directives, baseline diagnostics and generic table placement/packing/capacity observations. On later attempts they receive only their own previous outcomes. They never receive the development repair, another arm's outcomes, or future candidate results. Numeric candidate features describe annotation count and packing value; they contain no outcome labels or fitted diagnosis rules. Freeze the runner/prompt, adapters, generator, probe, config, sources and compiler identity before the first quality compile. Resume refuses changed inputs.

Identical candidates may share a physical compile cache. Charge each arm its own virtual query and record physical unique compilations separately; cache order must not affect observations or choices. Do not use concurrent/cached elapsed time as a comparative speed result. Success requires assembler/manifest/binary acceptance, unchanged logical capacities and control-plane schema excluding annotation metadata, and physical capacity sufficient for every requested table. It remains compiler-reported fit, not packet execution.

The descriptive primary gate is at least two net additional fits among six cases versus the best single three-attempt mechanical arm. Report all arms, the chosen best mechanical comparator, paired rescues and regressions, and the mechanical union as a stronger reference that spends up to nine attempts. Six related cases cannot substantiate statistical superiority. A third-tier contribution additionally requires an actual Codex rescue after that cascade's packing and Jev attempts fail, improving its own two-step prefix and the complete cascade's result versus equal-budget mechanical alternatives. Do not force this trajectory.

Count the policy burden: mechanical ordering, action grammar, feature extraction, prompt text, model identity and acceptance machinery. Fewer selector branches alone do not demonstrate lower maintenance. If simple packing performs as well, retain that negative result and do not grow a rules engine to make advisers appear necessary. A broader application corpus or policy-update study requires a separate frozen experiment.

## Running and resuming

After building the pinned toolchain, run from the repository root:

```sh
python3 -m scripts.p4_fit_experiment \
  --config experiments/p4-fit-advice-v1.json \
  --output artifacts/p4-fit-advice-v1
```

The existing Jev credential and authenticated Codex CLI are required only if an arm reaches those providers. The output contains the frozen input identities, per-case sources, compiler probes, recorded provider packets/choices, state and summary. Repeating the same command resumes recorded work; changed frozen inputs or damaged evidence require inspection, not silent recomputation. Optional comma-separated `--arms` or `--cases` selects a slice of the same frozen configuration. Do not run simultaneous writers against one output directory.

A rebuilt toolchain needs its own immutable image ID in a separately identified replication configuration; the published image ID refers to this local measured build. Provider tokens and output are recorded; credentials are not included.
