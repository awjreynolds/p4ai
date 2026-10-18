# Compiler-shift audit: no demonstrated adaptation opportunity

The frozen compiler-shift gate failed in both timing blocks. The source-trained policy had substantial regret on these new workloads, but the compiler change did not worsen it. Source and target selected identical actions in all six cases. This is a negative result for compiler-induced degradation under this shift, not evidence that the policy is already good.

| Block | Source policy / source oracle | Target policy / target oracle | Target minus source ratio | Gate |
| --- | ---: | ---: | ---: | --- |
| 1 | 1.491937 | 1.481011 | -0.010926 | Fail |
| 2 | 1.481364 | 1.478706 | -0.002658 | Fail |

Target regret exceeded 5% and the aggregate dispersion threshold, but neither the required three-point degradation nor the target/source dispersion gate passed. All six policy choices were unchanged across the compiler arms. The adaptation and final-test portions of this compiler-shift experiment were not run.

## Contract and qualification

The [manifest](../../experiments/policy-shift-v1.json) and [design](../policy-shift-design.md) were frozen before new timings. Six audit cases cover three fresh dimension/padding recipes with two layouts each. Kernel compilation changes from pinned LLVM to Apple Clang on the same M3; driver, linking driver and SDK environment remain common. The unchanged forest was loaded from its hash-verified published export. No provider calls or new policy fitting occurred in the audit.

All 84 build labels (six cases × two compilers × seven labels, including stock) passed native scalar-reference checks and supplied the complete compiler feature schema. The audit has 72 legal-action labels and 24 eleven-round timing blocks. Native measurement ran after tests and compilation completed; other agents were instructed not to build, fit or test during it.

The first preparation failed linking because direct CLT Clang did not infer the SDK library path. Its [failure record](policy-shift-initial-qualification-failure.json) is retained. A scoped SDK-explicit link helper and a timing-adapter repair were committed before a fresh preparation. No performance outcomes preceded those repairs. Original frozen experiment helpers were unchanged.

## Interpretation

The policy was roughly 48–49% slower than each compiler’s measured oracle on these audit cases. That reveals poor generalization to the new recipes, not the compiler-induced adaptation problem specified by this gate. The separate workload-policy repair experiment uses these observed target cases as development data and keeps the previously unmeasured recipes separate; it does not relabel this audit as a success.

The oracle is the fastest measured legal action in each round and is optimistically selected from those measurements. Dispersion thresholds are descriptive, not confidence intervals. The corpus contains one operation family and three audit recipes; it does not represent arbitrary programs. Source and target are separate compiler runs, not simultaneous paired hardware observations.

## Reproduce and inspect

```sh
uv run --offline python -m scripts.policy_shift_experiment --phase prepare-audit --design experiments/policy-shift-v1.json --output artifacts/policy-shift-reproduction
uv run --offline python -m scripts.policy_shift_experiment --phase measure-audit --design experiments/policy-shift-v1.json --output artifacts/policy-shift-reproduction
```

Requires the pinned native toolchain and SDK described by the manifest. Fresh timings are a new replication, not byte-for-byte replay of these observations. The [public bundle](policy-shift-v1.json) includes 64 original evidence records: prepared decisions and policies, build reports, raw timing blocks and calibration. Repository paths are normalized; each record retains its original raw-file hash.

The [native harness demonstration](harness-demo-v1.json) separately passed repeated offline selection, native correctness and byte-identical repeated builds on this host. Those repeated executables differ from the original prepared executable; object bytes and code identity match. Equality between two rebuilds is reported separately from equality to the original preparation.
