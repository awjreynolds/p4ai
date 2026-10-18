# Compiler feedback and incremental-value routing

The previous contraction trial found substantial schedule sensitivity, but source-only model advice did not beat the tiny stride selector beyond noise. This experiment keeps the six legal loop orders and tests a specific information hypothesis: generated-code evidence may expose mistakes that dimensions and strides alone miss.

The [frozen configuration](../experiments/compiler-feedback-v1.json) contains 36 fresh cases, with padded leading dimensions and uneven shapes. Entire dimension/padding settings stay in one partition: four discovery settings (16 cases), two calibration settings (8 cases), and three held-out settings (12 cases). Four seeded layout combinations occur in each setting. None is a case from the previous measured corpus. This tests new settings of one operation on one CPU, not general application performance.

Each action has the existing 30 source/stride/target fields plus 12 automatically extracted compiler fields: static LLVM instruction/block/load/store/multiply/vector counts, maximum fixed vector width, distinct loop metadata IDs, successful vectorization remark counts/factors, and kernel object bytes. These are observations, not a handwritten dynamic cost model. Object bytes include metadata; IR vector widths do not prove the number of native operations. No spill count is inferred from an `alloca`. Missing or ambiguous evidence is explicit and never silently treated as a measurement of zero.

The controls are the unchanged stride expression, one fitted constant, nearest neighbor, and a small ridge predictor with fixed regularization. Both learned controls receive exactly the compiler-informed numeric fields supplied to the models. The predictor's coefficients are learned rather than target-specific decision branches.

Discovery uses two independent interleaved timing blocks. Fit constant and candidate-best choices from block one. For NN and ridge, predict each discovery structural group using only the other three groups; freeze those out-of-group predictions before block two. This avoids the circular test in which nearest neighbor is asked to predict its own training examples. The opportunity gate must beat every cheap control beyond the effect-size/noise requirements. Final deployment NN/ridge models are fitted on all discovery block-one data. If the gate fails, make no provider calls, but still evaluate the frozen cheap controls on held-out cases to establish whether reusable cheap policy is the useful result.

If the gate passes, query source-only Jev, compiler-informed Jev, and compiler-informed Codex on every calibration/test case for offline counterfactual comparison. Freeze all choices before their timing pilots. Deployment uses three stages:

```text
stride agrees with cheap ridge prediction → return that action
otherwise → ask compiler-informed Jev
if predicted regret of Jev's choice exceeds the calibrated threshold → ask Codex
```

The threshold uses the ridge predictor's estimated loss difference, not Jev confidence. Fit its threshold using calibration outcomes only, preserving a never-escalate option and a 25% stronger-call limit. Freeze the router before held-out timing. Report all-provider evaluation cost separately from the calls this cascade would make in deployment.

Require the cascade to beat every cheap control, informed Jev, and the identical cascade with Codex disabled. The last comparison isolates the third tier's contribution. Also compare an identical routed cascade that substitutes the cheap ridge choice for Codex: a signal saying Jev looks wrong must not earn a model-benefit claim when simply following the cheap predictor repairs it. Report the source/informed Jev ablation separately: routing success would not by itself confirm the information hypothesis.

The policy inventory includes the action menu, stride expression, 42 feature fields, learned examples/coefficients, agreement gate, threshold, and prompts. Extraction, legality and correctness mechanisms are also engineering cost. This trial does not measure reduced human maintenance or adaptation effort. A useful cheap predictor would advance simplification even if model escalation remains unjustified.
