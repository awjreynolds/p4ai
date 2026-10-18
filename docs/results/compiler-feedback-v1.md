# Compiler evidence helps Jev; the full cascade fails

Compiler-informed Jev reduced held-out runtime by **17.56% versus the stride selector** and **12.06% versus ridge**, beyond the declared dispersion thresholds and in the same direction across all three structural groups. Adding compiler evidence improved Jev by **34.86% versus its source-only control**. This supports a useful informed middle tier for this boundary.

The complete three-tier hypothesis still failed. Calibration selected **never escalate** because the predicted-regret signal was zero on every eligible calibration case. Always-informed Codex was **56.10% slower than informed Jev**. The mechanical agreement gate also made one costly mistake, leaving the cascade **6.17% slower than always using informed Jev**. No maintenance saving is established.

## What changed

The previous contraction experiment found schedule sensitivity but no demonstrated model advantage over its small stride selector. This experiment asks whether compiler-generated evidence helps models identify errors that source dimensions and strides miss, and whether a cheap predicted-regret signal identifies useful stronger advice.

The harness compiler still lowers an unsigned modular contraction through six legal loop orders. It retains the same stride expression, `stride_A + stride_B + 2 * stride_C`, with fixed tie-breaking. LLVM performs downstream optimization and native results are checked against an independent scalar reference. No case-specific selector branches were added.

The fresh corpus contains 36 cases with uneven dimensions, padded leading dimensions and seeded row/column layouts: 16 discovery, eight calibration and 12 held-out cases. Entire dimension/padding settings remain within one partition. These are new settings of one operation on one Apple M3 Max, not independent application families or a compiler-distribution shift. All 252 build labels passed native correctness checks. The seven labels are canonical `stock` plus six orders; `stock` duplicates `ijk` and is not an LLVM scheduling oracle.

All informed policies receive the same 42 numeric fields: 30 source/stride/target fields and 12 generic compiler observations. The latter include static IR instruction and vector counts, successful vectorization remark summaries and object bytes. Object size includes metadata; static vector width is not a dynamic native-operation count. The source-only Jev control receives the original 30 fields. Neither packet contains measured runtimes or partition identifiers.

## Confirmed opportunity

Discovery uses two independent blocks of 11 interleaved rounds. Candidate-best orders and the fitted constant are chosen in block one and confirmed in block two. For the learned controls, each structural group is predicted using the other three groups only. This avoids asking nearest neighbor to predict its own training examples. Final deployment models are subsequently fitted on all discovery block-one data.

| Confirmed discovery comparison | Runtime ratio | Runtime reduction | Dispersion threshold |
| --- | ---: | ---: | ---: |
| Frozen per-case choices / fitted constant | 0.4756 | 52.44% | 4.87% |
| Frozen per-case choices / stride selector | 0.6807 | 31.93% | 7.49% |
| Frozen per-case choices / group-held-out nearest neighbor | 0.6220 | 37.80% | 5.83% |
| Frozen per-case choices / group-held-out ridge | 0.8930 | 10.70% | 4.73% |

All comparisons passed the frozen effect-size, dispersion and group-direction requirements. All 16 discovery cases produced six distinct native code outcomes. These are measurement-selected opportunities, not model gains; the learned comparisons concern group-held-out fits rather than the final all-discovery predictors.

## Held-out results

The table reports equally weighted geometric runtime ratios on 12 held-out cases in three equally sized structural groups. Lower is better. Every informed-Jev comparison against a standalone cheap control exceeds its paired-dispersion threshold and improves all three groups.

| Held-out comparison | Runtime ratio | Observed effect |
| --- | ---: | --- |
| Informed Jev / fitted constant | 0.47865 | 52.13% less runtime |
| Informed Jev / nearest neighbor | 0.68028 | 31.97% less runtime |
| Informed Jev / stride selector | 0.82436 | **17.56% less runtime** |
| Informed Jev / ridge | 0.87936 | **12.06% less runtime** |
| Informed Jev / source-only Jev | 0.65142 | **34.86% less runtime** |
| Informed Codex / informed Jev | 1.56104 | 56.10% more runtime |
| Cascade / stride selector | 0.87521 | 12.48% less runtime |
| Cascade / ridge | 0.93361 | 6.64% less runtime |
| Cascade / informed Jev | 1.06169 | 6.17% more runtime |
| Cascade / no-Codex cascade | 1.00000 | Identical choices |
| Cascade / matched ridge-repair cascade | 1.00000 | Identical choices |

The source/informed Jev ablation passed: the 34.86% reduction exceeds its 5.04% dispersion threshold, with positive direction in all groups. Seven of 12 choices are identical; the other five account for the improvement. The thresholds for informed Jev versus stride and ridge are 2.38% and 2.00%, respectively. These are descriptive paired-dispersion bounds, not confidence intervals. One response per packet leaves provider-response variability unmeasured.

The cascade beats every tested standalone cheap control beyond its thresholds, but fails the declared full success gate because it cannot beat informed Jev or the no-Codex controls. It consults Jev on seven of 12 held-out cases and takes the mechanical agreement shortcut on five. One shortcut, `test-wide-ccr`, loses enough performance to explain the aggregate 6.17% penalty versus always-informed Jev. Agreement between two cheap predictors is not a guarantee that their shared choice is good.

## What the router tests

The deployment cascade returns the cheap choice when stride and ridge agree. On disagreement it consults informed Jev. It calls Codex only when ridge predicts that Jev's choice exceeds a calibration-selected regret threshold. Calibration retains the option never to escalate and allows at most two stronger calls among eight cases. The held-out budget is at most three among 12 cases; it is not a guarantee about future traffic.

All 60 offline provider decisions—source Jev, informed Jev and informed Codex on every calibration/test case—were saved before calibration timing. The router was saved before held-out timing. The success gate compares the cascade with every cheap control, informed Jev, the same cascade with Codex disabled, and a matched repair cascade that substitutes ridge at exactly the same routed cases. That last control tests whether simply following the cheap predictor repairs the suspected error. The source/informed Jev ablation is a separate gate.

Every eligible calibration predicted regret was zero. The only candidate rules were never and a strict positive-regret threshold of zero; both routed no cases, so the prescribed tie-break selected never. Stronger-call rates were 0/8 calibration and 0/12 held-out. This router supplied no calibration opportunity to assess selective stronger advice; its failure does not show that no other valid routing signal could help. Always-Codex's regression is evidence about this fixed packet and corpus, not general model capability. The matched ridge-repair comparison is also inactive because nothing routes.

## Inference, compilation and maintained policy

All 60 offline decisions returned valid responses with no failed attempts. Jev reported `jev-1.14.0`; Codex requested `gpt-6-luna` at `max` reasoning. The Codex name is the requested configuration, not an independently verified server model identity.

| Provider | Decisions | Input tokens | Output tokens | Summed response latency |
| --- | ---: | ---: | ---: | ---: |
| Jev | 40 | 144,143 | 2,360 | 10.19 s |
| Codex | 20 | 401,139 | 55,404 | 517.26 s |

Codex reported 115,584 cached input tokens within its input total and 53,818 reasoning tokens within its output total. These are usage counts, not a cash-cost calculation. Offline counterfactual calls are not deployed inference cost: the fitted cascade would use Jev on three of eight calibration cases and seven of 12 test cases, with no Codex calls.

The complete corpus incurred 540 recorded compiler invocations: 36 shared driver compilations, 252 IR emissions and 252 object compilations, totaling 20.51 seconds. There were also 252 links (8.34 seconds), 252 native correctness commands (59.35 seconds), and compiler-evidence extraction totaling 0.098 seconds. These are recorded stage sums, not elapsed end-to-end time, and exclude the prerequisite LLVM build and runtime benchmark. The build path redundantly compiles the stock/ijk labels. A production informed selector still needs six candidate compilations/extractions for its current feature contract; inference latency alone therefore understates its cost.

Amortization depends on use: extra one-time compilation/selection cost divided by per-invocation runtime saved gives a break-even invocation count only when that saving is positive. This trial does not measure deployment traffic or claim an end-to-end cost saving.

The policy inventory includes six actions, the unchanged stride expression, 42 feature definitions, 96 timing-derived nearest-neighbor labels, ridge's 42 slopes plus intercept and scaling state, an agreement gate, a threshold and provider prompt contracts. Learned labels are data rather than handwritten case rules. The informed path must nevertheless compile all candidate orders to obtain its evidence before choosing; stride alone can choose from source features. Compiler extraction, legality checking and experimental machinery remain engineering work.

This experiment does not measure human maintenance effort or establish that maintenance fell. It retains the stride rule and adds feature schemas, extraction, fitted models and routing. New structural settings test limited generalization, not adaptation across compiler releases or applications.

## Reproduction and audit trail

- [Design](../compiler-feedback-design.md) and [frozen configuration](../../experiments/compiler-feedback-v1.json). Initial design `fe31906`; matched cheap-repair requirement added in `4677b17`, before measurements.
- Measured implementation `6cf8ec4`; corpus/extraction/scorer implementation `4b10cd9`. Reports preserve source, toolchain and input hashes.
- Pinned LLVM `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4`, explicit Apple M3 target/tuning, `-O3` and machine-instruction verification. This LLVM canonicalizes target CPU to `apple-a16` while retaining `apple-m3` tuning. System Clang builds the separate unvectorized reference drivers.
- [Public evidence bundle](compiler-feedback-v1.json): 36 build reports, 124 raw timing files, provider packets/responses, choices, fit, routing and terminal comparisons. Absolute workspace paths are sanitized. The large prerequisite validation report is included as a lossless gzip/base64 snapshot with a readable summary and hash binding.
- Independent review recomputed calibration and test comparisons, routing, and evidence hashes. All cases were retained; none were retimed to improve the outcome. The implementation suite passed before measurement.

```sh
uv run python -m scripts.compiler_feedback_experiment --phase discovery \
  --validation-report artifacts/native-validation-measured/report.json \
  --output artifacts/compiler-feedback-v1
uv run python -m scripts.compiler_feedback_experiment --phase models \
  --validation-report artifacts/native-validation-measured/report.json \
  --output artifacts/compiler-feedback-v1
```

The models command retains its report and returns a nonzero exit status if the frozen success gate fails. Resume checks reject changed implementation or evidence. Native timings exclude compilation and inference.
