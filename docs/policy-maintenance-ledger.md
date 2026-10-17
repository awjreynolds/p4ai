# What counts as maintained policy

The harness compiler does not remove LLVM's register-allocation machinery or all its selection policy. Candidate generation, interference and cost checks, hints, feature construction, splitting and spilling remain upstream dependencies. In particular, `use_def_density` already encodes a manual heuristic. A model choosing from those features inherits that work.

The quality experiment separates the following surfaces. Source sizes can describe them, but cannot turn them into comparable measures of engineering effort.

| Surface | Maintained content | Treatment in this experiment |
| --- | --- | --- |
| Shared LLVM boundary | Eligible actions, feature semantics, compiler guard and version compatibility | Identical for all external selectors; pinned throughout |
| First-eligible policy | First action in LLVM's eligible order | Frozen, no target-specific decision rules added |
| Density comparator | Minimum supplied use/definition density | Frozen one-feature choice; relies on LLVM's existing heuristic |
| Nearest-neighbour comparator | Feature schema, range normalization, distance/tie rules and outcome-labelled examples | Fit on separate families; optionally refit with shift labels |
| Jev advice | Decision objective, feature descriptions, menu format, provider/model contract | Fixed prompts and schema; service identity and usage recorded |
| Stronger advice | The same objective and observations, Codex response contract and requested model/effort | Fixed prompts and schema; exact serving snapshot unavailable |
| Escalation | Confidence interpretation, threshold calibration objective, budget and tie rules | Calibrated separately; optionally recalibrated with shift labels |
| Acceptance and fallback | Legal-action checks, replay identity, compiler verification, native references and unavailable-response handling | Common experiment infrastructure; never counted as selection-policy removal |

For adaptation, both approaches receive the same new 40-lane action-outcome measurements. The nearest-neighbour model adds examples and refits ranges; escalation recalibrates its threshold. The 48-lane workloads compare frozen and adapted versions. Source edits, prompt edits, example counts, threshold changes, labels and provider calls are recorded separately.

An unchanged prompt does not imply zero maintenance: the corpus, interpretation of features, provider compatibility and calibration process still require upkeep. Conversely, adding automatically collected examples is not equivalent to writing bespoke compiler rules. This exercise records those differences without claiming that one example costs as much to maintain as one threshold or one prompt.

Reduced long-term maintenance would need repeated independent changes with comparable quality targets and measured engineering effort, including diagnosis, label acquisition, prompt/configuration changes and regression work. This experiment can show whether the frozen policy transfers and whether automated adaptation helps. It cannot establish human maintenance savings from line counts or a single pressure shift.
