# P4 multi-repair escalation experiment v1

Status: design under preparation; no evaluation calls permitted until the complete design, shared knowledge, fixed policy, source manifest and runner are frozen. Earlier experiments and acceptance rules remain unchanged.

## Question

Does bounded model escalation repair previously unseen P4 programs more often than an equally informed tiny mechanical recipe and cheap-model continuation, within the same three-query budget? This tests compiler-reported resource fitting. It does not test hardware execution, full semantic equivalence, commercial relevance or policy-maintenance savings.

## Corpus qualification and separation

An independent curator constructs new synthetic source programs with at least three different dependency/control/action structures. Width or capacity variants of one template are not independent structures. The target evaluation set is six baseline-failing programs plus one default-fitting control and one failure with no known repair in the allowed menu. At least two distinct directive mechanisms must have compiler-verified development repairs and be represented among curator-verified test repairs. Different packing values alone do not constitute different mechanisms. No-known-repair is not a proof of impossibility.

Development examples, relevant directive documentation and every fact used to design the fixed recipe form one shared knowledge document. The mechanical-policy designer, prompt author and advisers receive the same document. The curator keeps test repairs and compiler outcomes in a separate sealed directory; neither policy design nor prompts may depend on those outcomes. This is procedural separation in a shared workspace, not a cryptographic security boundary.

The curator records all attempted candidates, template ancestry and qualification costs. Test inclusion and any exclusions must be fixed before adviser calls. Test source and baseline diagnostics become visible only after selector design is frozen. Cases are synthetic and deliberately resource-stressed; do not describe them as independent production applications or as statistically representative of P4 workloads. If the diversity/repair qualification fails, report corpus feasibility failure rather than run a mislabeled generalization trial.

## Actions and acceptance

All strategies choose from exactly the same pre-generated annotation-only alternatives. A candidate changes only allowlisted directives in fixed-width blank source slots: `pack(1..9)`, `immediate(0)`, or `placement_priority(-10|10)`. Keep source basename, line positions, table sizes, packet operations, compiler binary/support files, target and flags unchanged. No recirculation, weakened requirements or unchecked programmer assertions are allowed. Candidates are alternatives applied afresh, not cumulative patches.

A repair requires compiler exit success, successful manifest and nonempty target binary. The frozen source/manifest contains the complete requested application-table inventory. Every declared application table must retain its requested logical capacity and at least that physical capacity. This version supports direct exact-match tables with explicit stage allocation sizes; missing, duplicate or unsupported allocation evidence fails closed. Never infer required capacities from a candidate output. Compare the full BFRT control-plane description after removing annotation metadata and normalizing order. The sole additional exclusion is the top-level generated numeric ID of a table with both exact name `pipe.snapshot.cfg` and type `SnapshotCfg`. Preserve that table's remaining fields and every other table/action/key/data ID and signature. This separately identified contract addresses the previously observed generated-debug-ID issue without rescoring any earlier experiment. Reject unavailable schema/capacity evidence. Record raw compiler fits and acceptance rejections separately.

## Arms and budgets

Each program has a shared default compilation, then at most three virtual repair attempts per arm:

- Mechanical: the same fixed three-candidate ordering on every program; no diagnostic-specific branches or exceptions.
- Jev: up to three sequential choices using only shared knowledge, baseline evidence and its own earlier feedback.
- Codex: the same protocol with the stronger adviser.
- Cheap continuation: mechanical first choice, then Jev, then Jev if still failing.
- Escalation: the exact same recorded mechanical/Jev prefix as cheap continuation, then Codex if still failing.

The last two arms fork from one recorded prefix, so their comparison isolates the final provider choice rather than different earlier samples. Failures trigger continuation; fits stop the arm. Default-fitting controls use zero repair attempts. Invalid responses, interrupted/provider failures and timeouts consume an attempt; do not retry a failed cell. Previously tried policies are removed from the menu. A physical cache hit still consumes a virtual compiler query. Record attempted decisions separately when no valid action reaches the compiler.

Use the existing pinned Jev `jev-1.14.0` and Codex `gpt-6-luna` at `max` adapters. Each compiler invocation has the existing 120-second timeout. No token cap; record reported input/output usage, cached/reasoning subsets, latency and unknown usage. Only the shared knowledge, current source/menu, baseline and that arm's prior feedback enter a request; never include curator repairs or other-arm outcomes. Caching saves execution but supplies no extra feedback.

## Frozen interpretation

The practical positive gate requires at least two net additional accepted repairs by escalation over cheap continuation and at least two over the fixed mechanical arm, with incremental rescues against each comparator represented in at least two source structures and no accepted interface/capacity violations. For each comparator separately, a rescue is a case accepted by escalation and not accepted by that comparator; net additional repairs subtract the converse losses. Report paired rescues and losses, rather than only totals. This is a descriptive screen, not a statistical significance claim.

Failure to meet the gate means useful escalation was not demonstrated under this contract. Still report any lesser incremental contribution, independent adviser performance, baseline-fitting controls, unsolved cases and provider/grammar/acceptance failures. Do not change prompts, policy order, menu, case selection or thresholds after test outcomes. Record physical compilations separately from each arm's virtual budget and report curation work separately from evaluation cost. No maintenance advantage follows from this experiment.

## Evidence and reproducibility

Before calls, commit the contract, configuration, shared knowledge, mechanical policy, complete source/manifest hashes and runner/adapters/toolchain identities. Save each adviser observation packet and action mapping before calling its frozen adapter. Save the adapter’s normalized response or sanitized terminal error before compiling that choice. Successful responses retain the chosen action and reported usage/latency (plus provider-specific reason or distribution); failed or interrupted calls retain an error category and unknown usage. Raw provider bodies, Codex event streams and failed-call latency are not retained, so these records support decision replay and normalized-response audit, not a complete provider transcript. The hashed adapter determines the provider request envelope. Then validate cached compiler receipts and frozen provenance on resume. Keep original data and interrupted-call records. Publish sanitized source, decisions, usage, compiler identities, acceptance audits and an independently reviewed result. Do not publish credentials. New runs with rebuilt compiler identities or changed inputs require separately identified replications.
