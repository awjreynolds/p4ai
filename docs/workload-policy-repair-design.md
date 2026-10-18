# Workload policy repair after a negative compiler-shift audit

The [compiler-shift audit](results/policy-shift-v1.md) ended negatively: changing compilers did not worsen the frozen policy's regret. The same audit exposed substantial regret on fresh workload recipes under both compilers. This separately identified experiment addresses that workload-generalization problem. It does not change the previous gate, seek another compiler shift or treat the earlier failure as success.

The [new manifest](../experiments/workload-policy-repair-v1.json) pins the earlier design, completed audit and public evidence bundle. Its six observed target audit cases are now development data. Six target adaptation cases and twelve final-test cases retain the exact previously specified recipes and layouts; none has been performance-measured at design time. Their action mechanism, CPU, compiler and feature meanings remain unchanged.

## Fixed candidates and information

The generic update fits one forest to 72 action rows from twelve target cases: the six historical audit cases plus six adaptation cases, always using first-block labels. It retains the original 64-tree, depth-six, minimum-two-leaf configuration and 42 features. No hyperparameter search or extra rules are allowed.

One separate adviser writes a direct heuristic patch to the harness scheduling choice, with exactly the same block-one 72-row target label packet, at most four bespoke predicates and twenty minutes of adviser work. Common additional evidence is limited to compiler/feature/action descriptions and the frozen source policy artifact; neither arm gets additional source labels. The patch adviser receives no block-two outcomes or hidden oracle analysis and must not inspect test specifications or timings to tailor the patch. Exact case/specification lookup tables are prohibited. One candidate is frozen before testing. This is an agent-written patch comparator, not a measurement of human compiler-engineering effort.

Unchanged source forest and unchanged stride selection remain controls. A general informed Jev reference gets the same 42-feature test packet but no adaptation labels, with three separately requested responses per case and at most three attempts per slot. This intentionally contrasts a pretrained prior with a model fitted from domain measurements; data access differs and label acquisition costs must be reported. Do not describe it as equal-data model training.

All local choices and Jev responses are frozen before final timing. Jev's per-case, per-round result is the arithmetic mean of its three selected runtimes, never the best response. If any slot remains unavailable after the declared attempts, report the entire Jev reference unavailable and preserve failures; the local primary and patch comparisons continue without imputed model decisions.

## Evaluation and stop

Measure two eleven-round blocks on the twelve final cases, with no concurrent compilation, fitting, tests or model requests. Six recipe groups receive equal weight, and their two layouts receive equal weight within each group. Evaluate paired per-round log-runtime ratios, then group-balanced aggregate ratios and MAD dispersion.

The primary comparison is target refit versus unchanged source forest. Predeclared secondary comparisons are direct patch versus unchanged source forest, target refit versus direct patch, and Jev versus target refit. For a comparison to pass, both blocks must independently have runtime ratio below 0.95, log improvement beyond `max(log(1.02), 3 * MAD)` of the round-level group-balanced log ratios, and at least four of six recipe groups faster. These descriptive gates are not confidence intervals or multiplicity-adjusted statistical tests.

Keep the unchanged stride comparison, per-case regressions and oracle regret visible even when a candidate passes the primary gate. Failure to prove superiority is not equivalence. Do not change the policy after observing final outcomes or add another candidate to rescue a failed gate.

A successful refit supports automatic policy updating on one narrow operation family. A successful patch supports that code change. Neither alone establishes less maintenance than the alternative. Count code, model artifacts, prompts, feature extraction, compilation, labels, provider usage, fitting, inference, validation and manual repairs. The completed deliverable is a runnable harness plus the observed verdict, including failure.
