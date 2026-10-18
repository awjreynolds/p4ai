# Loop-order selection: real headroom, no demonstrated escalation benefit

The harness compiler now has a decision boundary with substantial measured headroom: choosing one of six loop orders when lowering a structured unsigned matrix contraction to native ARM64 code. The frozen discovery experiment passed. The subsequent model experiment **did not demonstrate useful escalation or an advantage over the strongest simple selector**.

Jev produced much faster held-out code than the discovery-fitted constant and nearest-neighbor predictor. However, its 1.66% runtime advantage over the tiny stride selector was below the declared noise threshold. Always using Codex was 6.54% slower than Jev. Both the primary router and a separately frozen exploratory refinement selected **never escalate**. This is a completed negative escalation experiment, not a successful three-tier optimisation claim.

## What was tested

The Python harness lowers `C_ij = sum_k ((A_ik XOR uint32(salt)) * B_kj)` modulo 2^32 from dimensions and array strides. With initialized output and disjoint buffers, the six permutations of `i,j,k` preserve semantics. LLVM retains all downstream optimizations. This is an actual scheduling decision in the harness compiler's lowering pass; it does not replace an existing LLVM scheduling callback or LLVM's complete cost model.

All **168 variants** (24 cases × seven labels) passed native scalar-reference and cross-action checks. The `stock` label means canonical `ijk` source order, whose compiled identity must match the separately built `ijk` action; it is not an LLVM scheduling oracle. Identical code shares timing observations.

The corpus contains eight row/column layout groups and three shapes. All shapes of a layout stay in its assigned partition: nine discovery, six calibration and nine held-out cases. These are layout variants of **one operation**, not independent application families. The host was an Apple M3 Max. Kernels used pinned LLVM revision `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4`, explicit M3 target/tuning, `-O3`, and machine-instruction verification. This compiler canonicalizes the M3 CPU attribute to `apple-a16` while retaining `apple-m3` tuning. System Clang built the separate unvectorized reference drivers.

## Opportunity gate

Per-case actions and the best fixed order (`kij`) were selected in discovery block one, then frozen before the independent confirmation block. Each block contained 11 interleaved rounds.

| Confirmed discovery comparison | Runtime ratio | Runtime reduction |
| --- | ---: | ---: |
| Frozen per-case choices / fitted constant | 0.5852 | **41.48%** |
| Frozen per-case choices / stride selector | 0.7327 | **26.73%** |

Both comparisons passed the 5% requirement and paired-dispersion bound, with positive direction in all three discovery layout groups. All nine cases produced six distinct native code outcomes. These numbers establish a measurement-selected opportunity; they are not model gains.

This followed a separately frozen [vectorization experiment](loop-policy-v1.md), which stopped with zero model calls because its confirmed improvement was only 1.50%. Its failed gate was preserved.

## Held-out model results

All 15 calibration/test decisions from each provider, plus nearest-neighbor choices, were frozen before their timing pilots and measurements. The table uses equally weighted geometric runtime ratios across the three held-out layouts. Lower is better.

| Held-out comparison | Runtime ratio | Observed effect |
| --- | ---: | --- |
| Jev / fitted constant | 0.3377 | 66.23% less runtime |
| Jev / nearest neighbor | 0.3995 | 60.05% less runtime |
| Jev / stride selector | 0.9834 | 1.66% less runtime; below noise gate |
| Jev / canonical `ijk` | 0.6967 | 30.33% less runtime |
| Codex / Jev | 1.0654 | 6.54% more runtime |
| Primary cascade / Jev | 1.0000 | Identical choices |
| Exploratory cascade / Jev | 1.0000 | Identical choices |

Jev's advantage over the constant and nearest-neighbor control exceeded the declared paired-dispersion bounds and had positive direction in every held-out layout. Its difference from the stride selector did not: the bound was 0.03521 in log-ratio units (`expm1` = 3.58%). This is a descriptive dispersion bound, not a confidence interval. Always-Codex's regression was concentrated in one layout group; it does not establish general stronger-model inferiority.

The mechanical stride selector stayed deliberately small:

```text
cost(axis) = stride_A(axis) + stride_B(axis) + 2 * stride_C(axis)
choose minimum (cost(inner), cost(middle), fixed_order_id)
```

No case-specific repairs or new heuristic branches were added after measurements.

## Routing and the exploratory amendment

The primary threshold grid was fixed at `0, .25, .5, .75, 1`, plus never/always. All six calibration confidences lay between .05 and .22, so every nonzero threshold routed all six cases. The 25% budget allowed at most one. The primary grid therefore forced a no-escalation result; that alone cannot show stronger advice lacks value.

Before inspecting calibration or held-out runtimes, we froze a visibly **exploratory, post-registration amendment**: consider midpoint thresholds between distinct calibration confidences, retain the same objective and budget, then evaluate the chosen rule once on the existing held-out measurements. It made a nonzero rule feasible: threshold .08 routed one of six calibration cases. That rule slightly worsened the calibration aggregate (ratio 1.00358), so the refined router also chose never. Larger thresholds exceeded the budget. Both held-out cascades consequently equal Jev exactly and fail the escalation success gate.

The primary result remains unchanged. The secondary result is not a preregistered success, and its rules were not revised after examining outcomes. Confidence here is distribution-derived model certainty, not a calibrated probability of a beneficial compiler decision.

## Inference and maintained policy

There were **30 valid responses, no failed attempts**: 15 from Jev (`jev-1.14.0`) and 15 requesting Codex `gpt-6-luna` at `max` reasoning. The Codex identity is the requested configuration, not an independently verified server model identity.

| Provider | Input tokens | Output tokens | Total response latency |
| --- | ---: | ---: | ---: |
| Jev | 47,193 | 885 | 3.69 seconds |
| Codex | 290,551 | 19,501 | 195.30 seconds |

Codex reported 66,048 cached input tokens within its input total. These are reported usage counts, not a cash-cost calculation. All-provider calls were offline counterfactual evaluation; a deployed no-escalation policy would not make these Codex calls. Timed native execution excludes model and compiler work.

The inventory includes one fitted constant, one stride expression, six legal schedules, **54 nearest-neighbor training examples with 30 numeric feature fields**, a routing threshold/rule, and the model prompt. Features derive mechanically from dimensions, strides and fixed target facts, rather than per-family lookup tables. Compiler mechanisms, correctness checking, data collection and experiment machinery remain substantial. Shortening a selector does not measure engineering effort saved; **maintenance or adaptation savings remain unproved**.

The strongest justified conclusion is that schedule selection offers meaningful opportunity, but this trial does not justify adding model inference over the small stride selector. A future trial needs a separately frozen hypothesis about information that the cheap selector misses and a routing signal predictive of the *incremental benefit* of stronger advice. More complex rules or a weaker baseline would not answer that question.

## Reproduction and audit trail

- [Design and boundary](../contraction-policy-design.md); [frozen configuration](../../experiments/contraction-policy-v1.json), design commit `0f38ef3`.
- Measured implementation `c0194c5`; blind choices and native timing caches carry hashes.
- [Exploratory amendment](../../experiments/contraction-routing-secondary-v1.json) and analysis frozen in `1b07182`; review-found design-path loading repair committed in `f077474` before analysis/outcome inspection.
- [Public evidence bundle](contraction-policy-v1.json): raw timing rounds, decisions, provider packets, build identities and both results. Absolute workspace paths are replaced by `$REPO`; the original artifacts remain under ignored `artifacts/` locally.
- Independent review checked the design, discovery gate, secondary integration and final interpretation. No cases were dropped or retimed to improve the result.

With the native validation gate available:

```sh
uv run python -m scripts.contraction_experiment --phase discovery \
  --validation-report artifacts/native-validation/report.json \
  --output artifacts/contraction-policy-v1
uv run python -m scripts.contraction_experiment --phase models \
  --validation-report artifacts/native-validation/report.json \
  --output artifacts/contraction-policy-v1
uv run python -m scripts.contraction_routing_secondary \
  --output artifacts/contraction-policy-v1
```

The model command returns a nonzero exit status when the frozen success gate fails, while retaining the completed report. Resume checks preserve implementation and artifact provenance. Results establish neither whole-application performance nor cross-target generality.
