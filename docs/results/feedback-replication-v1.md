# Compiler-informed Jev replication

The replication **did not pass its frozen success gate**. Across both timing blocks, informed Jev reduced aggregate native runtime by about **23% versus the unchanged stride selector** and **8.5% versus the fixed random forest**. Those aggregate gains exceeded the declared noise thresholds. However, Jev improved only three of six structural groups against both the forest and ridge, below the required four.

This is evidence of useful aggregate decision quality on the tested operation and CPU, with important variation across shapes. It is not equivalence with the cheap controls, a forest win, robust superiority across groups, useful third-tier escalation, or lower human maintenance. Stop adding selectors and exceptions to this screen. A separately designed adaptation comparison with direct heuristic repair is a more useful next question; see the [domain-policy review](../research/open-decision-policy-options.md).

## Frozen experiment

The [original design](../../experiments/feedback-replication-v1.json) was committed before fresh outcomes. After an invalid provider response and before any performance timing, the [bounded-retry amendment](../../experiments/feedback-replication-v1-recovery.json) and implementation were frozen at `9fe41d0`. The amendment preserves the [initial abort evidence](feedback-replication-initial-abort-v1.json).

- 24 new unsigned modular contraction cases: six explicitly chosen structural settings, each with four seeded matrix layouts. Full specifications are disjoint from previous manifests; this is not a random sample of a deployment population.
- Native Apple M3 ARM64, pinned LLVM revision `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4`. Six legal loop orders plus a separately built stock label; stock aliases `ijk` for timing.
- The same 42 numeric features and informed Jev packet as the preceding experiment. The stride expression, candidate mechanisms and correctness checks remain unchanged.
- Constant, nearest neighbor, ridge and forest fit only on the first timing block from all 36 previous compiler-feedback cases: 216 unique action rows. The previous held-out cases become training data here; none of the new cases supplies training or tuning labels.
- Forest: 64 trees, maximum depth 6, minimum leaf size 2, seed `20261020`, one worker, scikit-learn 1.9.1. It is one fixed nonlinear comparator, not an upper bound on learned-policy quality.
- Three separately requested Jev choices per identical packet, all frozen before fresh timing. The primary estimate averages the three selected elapsed times arithmetically within each case and round, then applies paired, group-balanced geometric comparisons. It never selects the best response.
- Two separate interleaved blocks of 11 rounds per case, all candidates measured, with no concurrent compilation, tests or fitting. Each block must independently beat every control beyond `max(log(1.02), 3 × maximum paired MAD of log ratios)` and improve at least four of six groups. These are descriptive dispersion thresholds, not confidence intervals.

## Primary results

Ratios are expected Jev runtime divided by comparator runtime; lower than one favors Jev. Blocks are reported separately, not pooled.

| Comparator | Block 1 ratio | Block 2 ratio | Jev runtime reduction, blocks 1 / 2 | Groups improved, both blocks |
| --- | ---: | ---: | ---: | ---: |
| Fitted constant | 0.395497 | 0.394832 | 60.45% / 60.52% | 6/6 |
| Unchanged stride selector | 0.770492 | 0.769333 | 22.95% / 23.07% | 6/6 |
| Nearest neighbor | 0.850843 | 0.849993 | 14.92% / 15.00% | 4/6 |
| Ridge | 0.905160 | 0.905637 | 9.48% / 9.44% | 3/6 |
| Random forest | 0.915068 | 0.914737 | 8.49% / 8.53% | 3/6 |

Every aggregate comparison passes its noise threshold. Both blocks fail exactly the group-direction requirements against ridge and the forest. Overall status: `completed_no_success_gate`.

| Structural group | Jev / forest, block 1 | Jev / forest, block 2 |
| --- | ---: | ---: |
| Small | 0.747023 | 0.747458 |
| Square | 1.000000 | 1.000000 |
| Tall | 1.008096 | 1.005411 |
| Wide | 1.133707 | 1.132408 |
| Deep reduction | 0.827456 | 0.827749 |
| Shallow reduction | 0.831073 | 0.831663 |

The wide group is about 13.3% slower under expected Jev than under both forest and ridge. The small, deep and shallow groups favor Jev over the forest. Equal values reflect identical selected actions, not an independently established equivalence margin. The full bundle includes every per-case, group and individual-response comparison.

## Provider variability and cost

There were **73 requests, 72 valid responses and one invalid response**. All successful responses identified `jev-1.14.0`. The three choices were unanimous for 23/24 cases, agreeing in 70/72 pairwise comparisons. Only `replication-wide-rrc` varied: `ijk`, `jki`, `jki`. Provider-side caching, determinism and statistical independence are not established.

| Individual response | Jev / forest, block 1 | Jev / forest, block 2 |
| --- | ---: | ---: |
| First | 0.937521 | 0.937060 |
| Second | 0.896128 | 0.895975 |
| Third | 0.896128 | 0.895975 |

All individual replicates retain aggregate advantages over every comparator. Three requests do not characterize the full response distribution.

Valid responses report **296,211 input tokens, 4,248 output tokens and 19.292 seconds summed latency**. The invalid response's usage and latency were not retained, so those totals are lower bounds. No Codex requests were made in this replication.

The original aborted preparation spent 66.444 seconds in recorded staged build/correctness work and 0.430 seconds fitting/predicting. Recovery preparation separately records 13.128 seconds in compiler commands, 5.948 seconds linking, 46.727 seconds in correctness commands and 0.061 seconds extracting compiler evidence, plus 0.210 seconds fitting and 0.214 seconds predicting. These are component durations, not total elapsed experiment cost. The bundle preserves their definitions and separate inventories.

Compiler-informed selection compiles all candidate orders to obtain features. Runtime improvements do not establish cheaper compilation or lower end-to-end cost. The fitted model, label acquisition, feature schema, prompts, compatibility and acceptance tests all remain maintained surfaces.

## Failures and report recovery

The initial attempt stopped after 19 valid responses and one invalid response at `replication-square-rrr`, repetition 2. No fresh performance timings existed. The adapter retained only `invalid_response`; the exact validation failure and rejected response usage are unavailable.

Before timing, the recovery design permitted at most three attempts per invalid/unavailable slot, counting the original failure. Valid choices could never be retried. All 20 original cache files were imported byte-for-byte, including the 19 valid responses; the failed slot subsequently succeeded. Exhaustion would have aborted without dropping a case. The measured policy is therefore a bounded-retry provider policy, not flawless first-request reliability.

After all 48 timing blocks completed, report serialization raised `KeyError('group')`: the shared timing helper preserved `family` but omitted the redundant `group` field. The new [cache-only recovery command](../../scripts/recover_feedback_replication_report.py) restores `group` from frozen build metadata only after checking `group == family`. It uses the original metric and gate implementation, blocks compilation, native execution, network and provider requests, and permits only the exact read-only Git revision query required by the existing validator.

Recovery verified **2,501 pre-existing evidence files unchanged**, along with frozen inputs, all 48 timing blocks, 24 pilots and 24 calibration files. Its separate manifest records the error, mapping, wrapper hash, original provenance and read-only checks. Repeating recovery reproduced the exact saved report. Original experiment files, choices, thresholds and measurements were not edited.

## Evidence and reproduction

The [public JSON bundle](feedback-replication-v1.json) contains the exact sanitized report, frozen choices and provider responses, raw timing rounds, build reports and logs, fitted forest, 216 training rows, abort/recovery accounting and report-recovery manifest. The earlier training bundle is referenced by its existing hash rather than embedded again. The public bundle uses compact JSON without removing data.

| Artifact | SHA-256 |
| --- | --- |
| Public bundle | `1ef3a15d0744693e0b69befdf818dc056d8c023ee60b9e75469ff106decd46d0` |
| Recovery design | `e4f88a320bc67f5ea6b97c76115cbd55a5b0506a10d9710f0c3a76e2c8329415` |
| Raw report | `7757f722a6239b259fd40957fca3fa8a190e8b35099ad0718f420114d46e4daa` |
| Report recovery manifest | `cc4246d307d00e255aa207fb989589d32b53dccccb94d8a8d9e8d007a1952d5d` |
| Recovery wrapper | `9a52a3bb3a6ee8bee2fe24ed4c75850495b4d1b031ed8149f551de1bc6fd7632` |

Preparation builds the candidates and makes live provider requests. A fresh preparation is a new sample from the same protocol, not a replay of the retained provider responses. On the pinned native toolchain:

```sh
uv run --offline python -m scripts.feedback_replication_experiment \
  --phase prepare --design experiments/feedback-replication-v1-recovery.json \
  --validation-report artifacts/native-validation-measured/report.json \
  --output artifacts/feedback-replication-v1-recovery

uv run --offline python -m scripts.feedback_replication_experiment \
  --phase measure --design experiments/feedback-replication-v1-recovery.json \
  --validation-report artifacts/native-validation-measured/report.json \
  --output artifacts/feedback-replication-v1-recovery
```

The frozen measurement command has the documented serialization defect after completing its caches. Once all caches exist, assemble or verify the report without collecting any new outcomes:

```sh
uv run --offline python -m scripts.recover_feedback_replication_report \
  --validation-report artifacts/native-validation-measured/report.json \
  --output artifacts/feedback-replication-v1-recovery
```

The recovery command fails if evidence is incomplete or has changed. Generated executables and raw local paths remain ignored; independent numeric recomputation can use the public bundle without an Apple machine. A separate reviewer reproduced all aggregate and individual ratios, dispersion thresholds and gates from the raw timings and frozen choices. Validation: **322 tests passed**, Ruff passed, and `git diff --check` passed.
