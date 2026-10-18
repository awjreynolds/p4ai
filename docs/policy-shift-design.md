# Reproducible harness policy under a compiler shift

This is a separately frozen experiment, not a modification of earlier results. The [manifest](../experiments/policy-shift-v1.json) fixes cases, toolchains, flags, training and acceptance rules before new timings. The user authorized a complete proof of concept or negative result.

## Claim and boundary

The harness lowers a structured, unsigned modular matrix contraction to one of six legal loop nests. It owns this scheduling choice; it does not replace an internal LLVM heuristic. The downstream compiler remains responsible for normal optimization and code generation. Existing independent scalar-reference checks remain authoritative. The 42-feature policy uses source properties and compiler-derived candidate evidence, so it compiles alternatives before selection. Report those costs explicitly.

The proposed contribution is a replaceable, frozen selection policy plus an evidence-driven update procedure. Learned heuristics and autotuning are established prior art. A successful update here would demonstrate a mechanism on one operation, not novelty by itself, a generally better compiler, or lower human maintenance.

## Fresh split and shift

Keep the source operation, M3 CPU, driver compiler, SDK, linker and action menu fixed. Switch the kernel compiler from the pinned LLVM build to the installed Apple Clang. The manifest hashes both compiler binaries. This is a toolchain change rather than an upgrade claim.

Twelve distinct dimension/padding recipes supply two seeded layout variants each: three audit recipes, three adaptation recipes and six final-test recipes. All 24 full specifications and all twelve base recipes are distinct from the previous compiler-feedback and replication corpora. Never split action or layout rows from a recipe across partitions. This remains within one kernel family.

## Audit before repair

Reuse the hash-verified forest already fitted on the original 36 development cases with the existing fixed configuration. Its exported weights are embedded in the published replication bundle; no refit changes the source policy. Evaluate it with each compiler's own 42-feature packets. Compile and validate all audit actions before measuring. Unsupported compiler evidence is a technical failure, never a reason to substitute guessed fields.

Measure source and target audit choices against their respective measured best legal actions. Use two blocks of eleven interleaved rounds. Within each block, compute group-balanced policy/oracle ratios and a source-to-target ratio of those regrets. With two layouts per recipe, equal recipe and case weighting coincide.

Both blocks must satisfy:

- Target policy/oracle geometric ratio exceeds 1.05.
- Target minus source policy/oracle ratios is at least 0.03.
- Both target log-regret and the log ratio of target/source regret exceed their dispersion thresholds: `max(log(1.02), 3 * MAD)`, calculated over round-level group-balanced log ratios.

These are descriptive stability gates, not confidence intervals. Each round's oracle is the candidate with minimum elapsed time in that round; publish its optimistic selection bias and the complete measurements. The source/target comparison uses corresponding round numbers collected in the same audit block, but separate compiler runs; it is not a simultaneous paired hardware observation.

If either block fails, stop adaptation and final-test measurement and report **no demonstrated adaptation opportunity under this shift**. Do not try another shift in this experiment. The frozen-policy execution demonstration still completes.

## Repair if the audit passes

Both repair arms receive the same six target adaptation cases with all six action measurements, plus the same source development evidence. Test outcomes remain unavailable until choices are frozen.

- Generic update: fit a new forest on target adaptation labels only, with the same 42 features and fixed 64-tree, depth-six, minimum-two-leaf configuration. No hyperparameter tuning or new policy code.
- Direct repair: one adviser-developed heuristic candidate, at most four bespoke predicates, twenty minutes of adviser effort, no case identifiers, exact-specification lookup tables or test feedback. This is a code patch to the same harness decision boundary, not a claim of a human-maintained LLVM patch.
- Unchanged source forest and original stride selector remain controls.

The generic arm receives no bespoke adviser optimization; it follows the frozen refit rule. Any additional adviser-assisted learned arm would require a separate design, equal adviser assistance for the patch arm, and new evaluation data. Do not add it opportunistically after seeing test results.

Freeze all policy artifacts and test choices before two final eleven-round blocks. Automatic adaptation succeeds only if refit reduces group-balanced runtime versus unchanged policy by at least 5%, exceeds the aggregate dispersion threshold in both blocks, improves at least four of six recipe groups, and passes correctness checks without new selection code/features. Evaluate superiority to direct repair separately using the same rules. Failure to establish superiority is not equivalence.

## Deliverable and accounting

Publish the design, raw timing evidence, policies, choices and terminal verdict. The offline policy evaluator must run without scikit-learn or a model service, match the training library's exported-forest predictions, reject malformed or incompatible inputs, and use stable tie-breaking. Its identity checks detect mismatch; they are not a signature or a trust guarantee.

Demonstrate selecting and rebuilding a checked kernel from a frozen artifact, then compare repeated build hashes. Keep decision replay, inference repeatability and byte-identical output as separate reported checks. No successful inference test alone establishes reproducible binaries.

Count labels, candidate compilation, correctness work, feature extraction, fitting, inference, dependencies, bespoke edits, adviser use and manual interventions. One update cannot demonstrate reduced human maintenance. A negative audit or repair result is a complete result for this bounded experiment; it must not be redescribed as proof that harness compilers cannot work in other settings.
