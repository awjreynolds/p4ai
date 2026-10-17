# Escalation quality screening: no demonstrated advantage

The next experiment did **not** establish that escalation improves decisions or reduces maintained policy at this LLVM eviction boundary. Calibration selected **never escalate**, both before and after the pressure shift. The small learned selector matched Jev's generated-code outcomes on every held-out site. Stronger advice supplied no improvement beyond the predeclared noise floor.

This is a negative result for the tested policy, observation packet and single-decision intervention. It does not disprove escalation at a different compiler boundary or with better observations. It also does not justify continuing to add escalation machinery to this boundary on the present evidence.

## What ran

The [design](../escalation-quality-design.md) was committed before measurement at `2f0606a`; the measured implementation is `7de3d37`. The [public evidence](escalation-quality-v1.json) records the design and source hashes, calibration candidates, decisions, provider usage, executable hashes, all paired timing samples and family aggregates. Full compiler traces and logs remain under local `artifacts/escalation-quality-v1/`.

- Six synthetic kernel families, 16 workload variants and 32 preselected compiler states.
- Separate families for fitting, routing calibration and untouched testing; increased pressure for adaptation and final shift evaluation.
- 112 state/action outcomes in the restricted candidate pools, plus stock references. Identical generated code shares measurements. There were 91 distinct timing groups across sites, each measured in 11 interleaved rounds after warm-up.
- 32 valid Jev responses and 32 valid Codex responses. Each pair used the same frozen packet. Cascade results reuse these answers rather than re-querying a model.
- All completed compilations and native outputs passed the compiler and reference checks. Every changed action used a checked baseline prefix and fresh continuation.

Jev and Codex chose different actions at 13 of 32 states, but those disagreements generated different code at only four states. This distinction matters: different choices are not automatically better decisions.

## Held-out decision quality

Ratios below 1 favour the named candidate. Results are geometric means of paired runtime ratios, equally weighted by state within each family and then by family. There are only two held-out families; these are descriptive screening results.

| Candidate / reference | Eight held-out states | Four final pressure-shift states |
| --- | ---: | ---: |
| Calibrated cascade / Jev | 1.00000 | 1.00000 |
| Calibrated cascade / frozen learned selector | 1.00000 | 1.00000 |
| Always Codex / Jev | 0.99900 | 1.00000 |
| Calibrated cascade / first eligible | 1.00100 | 1.00000 |
| Calibrated cascade / density comparator | 1.00136 | 1.00000 |
| Calibrated cascade / stock LLVM | 1.01286 | 1.01186 |

The cascade made zero stronger-model calls under its calibrated rule. The only nonzero threshold within the 25% calibration budget routed one of eight states and produced no benefit beyond the 2% floor. Broader thresholds exceeded that budget and slightly worsened the calibration objective. Recalibration on four shift states again chose never escalate.

Always using Codex improved the held-out aggregate by approximately **0.10%**, below the 2% floor. The stock comparison also does not establish an overall advantage: the cascade was 1.29% slower in aggregate, with different directions across the two families. We do not interpret these small differences as reliable performance gains or losses. Maximum per-state paired timing MAD is retained so identical reused samples cannot artificially suppress the reported noise.

The learned selector and Jev shared identical generated-code timing groups at all eight held-out sites. At the four final pressure-shift sites, first eligible, density, frozen/adapted learned selection, Jev and Codex all converged to identical code. Consequently, neither the original nor adapted cascade passed the declared success criteria.

## Policy adaptation and maintenance

The learned selector initially used 28 labelled candidate examples. Both approaches received the same 14 additional action-outcome labels from four 40-lane states. The learned model added those examples and refitted ranges; the router recalibrated its threshold. Both were evaluated at 48 lanes with doubled loop rounds.

Neither approach needed source or prompt edits. The routing rule remained unchanged, and neither adaptation changed final generated-code outcomes or improved runtime. Storing 14 extra examples versus retaining one threshold is not a measurement of engineering effort. **Reduced maintenance is not established.** The [policy ledger](../policy-maintenance-ledger.md) includes prompts, features, calibration, fallback and retained LLVM policy rather than treating the model prompt as the entire maintained surface.

## Cost, failures and limitations

Successful responses used **791,724 input and 33,603 output tokens**. Jev calls took 11.53 seconds in total; Codex calls took 338.33 seconds. These are offline screening costs, including stronger-model counterfactuals, rather than the cost of deploying the never-escalate rule. Jev reported `jev-1.14.0`; Codex was requested as `gpt-6-luna` with max reasoning. The exact Codex serving snapshot was unavailable.

One additional Jev response was rejected as invalid. It was recorded, then retried; completed valid responses were preserved. Usage for that rejected response was unavailable and is excluded from the totals above.

There was one protocol deviation: resuming recalibrated the iteration count for the last dot-product calibration case. Its two sites used **136,036** and **134,391** iterations. Every comparator within each site used the same count, so paired comparisons remain intact, but the different iteration counts also change the salt sequence and workload weighting between sites. The original measurements are retained. The runner now persists the per-case count for future resumes, with a regression test; this repair was made after measurement and did not alter the frozen policy or reported results.

Linux CI also required explicit parentheses around an existing addition/XOR expression in the generated C. This post-run portability repair preserves operator grouping. Recompiling all six 24-lane kernels with the pinned compiler produced byte-identical LLVM IR before and after the repair; the measured source remains available at its original commit.

These are synthetic families, one machine, one compiler revision and one changed decision per compile. Most model disagreements converged to the same code, limiting the opportunities to detect a useful escalation. No human maintenance time, real application corpus or multi-decision policy rollout was measured. The evidence supports keeping the simple policy as the default here. A further positive-value experiment should first establish a boundary where cheap choices leave reproducible quality gaps, rather than tune prompts on these held-out results.
