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
