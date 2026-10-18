# Workload policy repair: the learned update fails

The runnable harness completed its frozen comparison on twelve fresh cases across six workload recipes. Automatic target-only refitting made code **14.6–14.8% slower** than the unchanged source forest. The primary acceptance gate failed in both timing blocks. No policy was changed after these outcomes, and the failed refit is retained as evidence rather than promoted.

One small agent-authored heuristic produced a useful, concentrated improvement: **13.2–13.3% less runtime** than the unchanged forest. It changed two decisions and tied the other ten exactly, so only two recipe groups improved. It therefore **also failed** the frozen requirement for four improving groups. Jev did not pass its comparison with the refit. These are separate results; a working harness does not establish successful learned repair, useful three-tier escalation or lower maintenance.

## Frozen comparisons

Ratios are candidate runtime divided by reference runtime: below one is faster. Each block contains eleven rounds, with equal weight for the six recipes and their two layouts. Every comparison required ratio below 0.95, paired log improvement above `max(log(1.02), 3 × MAD)`, and at least four improving groups, independently in both blocks.

| Candidate / reference | Block 1 | Block 2 | Improving groups, each block | Verdict |
| --- | ---: | ---: | ---: | --- |
| Target refit / unchanged forest — primary | 1.14623 | 1.14820 | 2/6 | Fail |
| Direct heuristic / unchanged forest | 0.86688 | 0.86755 | 2/6 | Fail: four groups tie |
| Target refit / direct heuristic | 1.32225 | 1.32350 | 1/6 | Fail |
| Jev / target refit | 0.97121 | 0.97056 | 2/6 | Fail |

The direct heuristic and Jev comparisons exceeded their dispersion thresholds, but failed other requirements. These are descriptive gates, not confidence intervals or multiplicity-adjusted significance tests. Failure to pass does not mean equivalence or an absence of useful improvements.

For context, the following descriptive ratios compare each policy with the fastest measured legal action **in each round**. This is an optimistic lower envelope selected from the same observations, not a deployable policy or an independently measured fixed oracle.

| Policy / per-round measured oracle | Block 1 | Block 2 |
| --- | ---: | ---: |
| Direct heuristic | 1.00559 | 1.00551 |
| Unchanged source forest | 1.16002 | 1.15902 |
| Informed Jev reference | 1.29137 | 1.29162 |
| Target refit | 1.32964 | 1.33079 |
| Unchanged stride selector | 1.39640 | 1.39538 |

The direct heuristic changes `wide-rrc` from `jki` to `kji` and `deep-ccr` from `ikj` to `kij`; the other ten choices match the unchanged forest. Conversely, the refit chooses `ijk` on `small-ccc` and `wide-rrr`, approximately 7.06× and 4.32× the unchanged policy's runtimes in block one. Benefits elsewhere do not compensate. The [summary](workload-policy-repair-summary-v1.json) includes every case's choices, ratios and complete gate calculations.

## What was actually tested

The [manifest](../../experiments/workload-policy-repair-v1.json) and [design](../workload-policy-repair-design.md) were frozen before new measurements. This is workload generalization after a [negative compiler-shift audit](policy-shift-v1.md), not a relabelled successful compiler shift. The six observed target audit cases became development data. Six previously unmeasured adaptation cases supplied the remaining labels. Twelve final-test cases remained unmeasured until all decisions were frozen.

The refit used **72 action rows from twelve cases and six recipes**, always first-block labels, with the same 42 features and fixed 64-tree/depth-six/minimum-two-leaf parameters. These are not 72 independent programs. It used target-only labels; it did not preserve the original forest's larger training set or tune its hyperparameters. This tests that specific update procedure, not learned compiler policies generally.

The isolated heuristic-writing agent received exactly the same 72-row training packet and feature/action descriptions, with no final-test specifications, outcomes, block-two labels or extra historical labels. It wrote one candidate in 157 seconds without a model fit or threshold sweep. Four named composite predicates contain **seven atomic comparisons and two additional numeric ranking terms**, plus canonical tie-breaking. Its final code was committed before final compilation/decisions. This is an agent-authored comparator, not a measurement of expert human engineering or causal evidence that LLM assistance improved the repair.

Jev received the static compiler-informed packet without adaptation labels. Its data access therefore differs from the local training arms. All three responses per case happened to agree; the implementation nevertheless evaluates their arithmetic mean runtime, never the best response. All 36 attempts succeeded using `jev-1.14.0`: **148,494 input tokens, 2,124 output tokens**, and **20.17 seconds** of summed response latency. No stronger-model runtime tier or adaptive route was tested here; earlier cascade failures remain unchanged.

All 126 new build labels (18 cases × stock plus six orders) passed native scalar-reference checks. Timing ran in two eleven-round blocks with compilation, fitting, tests, model calls and other deliberate project load stopped. The hardware and semantics remain narrow: Apple M3, Apple Clang, fixed-shape disjoint unsigned modular matrix contractions. All candidates are compiled before selection to obtain compiler features. This does not establish end-to-end compilation savings or general application performance.

## Maintenance and deployment implications

The forest fit took 0.60 seconds and its wrapped JSON artifact occupies 218,662 bytes. That low fitting cost excludes acquisition of native labels, compiler-feature extraction and validation. Across the new builds, 522 distinct recorded commands sum to 51.61 seconds; this is partial command-duration accounting, not elapsed experiment time or full-system cost. The authoring agent's token usage was not supplied by the subagent interface and is not inferred from its elapsed time.

The repair runner alone is 1,554 source lines plus 334 test lines. This is experiment and evidence infrastructure, not all active selection policy, but it still counts as maintained system complexity. The [maintenance ledger](../policy-maintenance-ledger.md) records feature contracts, artifact compatibility, prompts, native linking and wrapper repairs. Comparing 0.60 seconds of fitting with 157 seconds of agent authoring would omit those common and different costs; it would not measure human maintenance savings.

Keep compiler legality and checks authoritative, and keep policy changes explicit and versioned. The demonstrated workflow can reject a harmful learned update while preserving reproducible decisions. The evidence does not establish that a model service, general System One model, domain-trained model or MCP interface belongs in every compilation. MCP could expose these operations to clients; it would not change this quality result.

The small direct patch remains a candidate engineering improvement, with its failed broad acceptance gate visible. Promoting it under a different criterion would be a new decision, not a retrospective pass. No additional selector, exception or training sweep was added to rescue this experiment.

## Run and inspect

Portable replay requires only Python 3.12 or later, without credentials, a compiler, scikit-learn or network:

```sh
python3 -S -m scripts.replay_workload_repair
```

It reproduces all twelve frozen local decisions and checks them against the published record. It does **not** rerun native timings or turn the failed update into a successful one. The earlier [native demo](../harness-compiler-poc.md#native-rebuild-demonstration) separately checks selection, scalar correctness and two byte-identical rebuilds on the pinned host.

Native reruns require the pinned toolchain and SDK. Use a fresh directory and the frozen heuristic:

```sh
uv run --offline python -m scripts.policy_repair_experiment --phase prepare-adaptation --output artifacts/workload-repair-reproduction
uv run --offline python -m scripts.policy_repair_experiment --phase measure-adaptation --output artifacts/workload-repair-reproduction
uv run --offline python -m scripts.policy_repair_experiment --phase fit --output artifacts/workload-repair-reproduction
uv run --offline python -m scripts.policy_repair_experiment --phase prepare-test --output artifacts/workload-repair-reproduction
uv run --offline python -m scripts.policy_repair_experiment --phase measure-test --output artifacts/workload-repair-reproduction
```

`prepare-test` uses the existing Jev credential and calls the external service; `uv --offline` applies to dependency resolution, not that service. Reproduction uses the recorded heuristic, not a new agent-authored candidate. A fresh refit, provider response or timing run is a replication, not replay of the original observations. The historical audit labels can be read from the hash-pinned public bundle on a clean checkout.

The [full evidence bundle](workload-policy-repair-v1.json) contains 171 records, including training labels, authoring worklog, policies, prepared decisions, provider caches, build reports and timing blocks. `$REPO` and `$HOME` replace local paths; `raw_sha256` identifies original file bytes, not the normalized record. The summary records the complete public bundle's hash. Implementation was frozen at `17a3fda`, and the heuristic at `9012f76`, before this final evaluation.
