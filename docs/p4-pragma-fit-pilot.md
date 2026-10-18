# Proposed P4 pragma-guided fit experiment

Status: [compiler-fit qualification passed](results/p4-pragma-fit-qualification-v1.md); the model comparison is not yet executed. The existing native CPU experiments and their negative results remain unchanged. The user's new hypothesis is that a harness can infer the compiler steering decisions a P4 developer would otherwise express through pragmas to make a program fit a resource-constrained target.

## Decision boundary

Input: the original P4 source, fixed target/compiler/options, required table capacities and interfaces, compiler resource/placement diagnostics, and a documented menu of admissible allocation directives.

Output: a small typed patch containing only allowlisted directives and their validated parameters. The compiler performs the actual allocation and determines whether the result fits. The model never declares fit or changes resource limits itself.

```text
original program + fixed target → compile → resource allocation failure
failure + resource evidence + admissible directives → candidate pragma patch
patched program → compile → fit evidence or next diagnostic
```

This tests inference of developer steering decisions across interacting constraints. It does not replace the compiler's allocator, nor infer that every resource failure is repairable. One confirmed failure-to-fit → pragma-only fit pair qualifies the mechanism; it does not establish model advantage.

## Qualify the first example before model calls

1. Pin a publicly reproducible compiler revision, target architecture, include files and full command line.
2. Select a documented program with a resource/placement failure under those exact settings. Reject syntax/type failures, compiler crashes and unsupported features as resource-fit evidence.
3. Reproduce a known expert directive-only repair under the same settings. Confirm the baseline had no deliberately harmful placement annotations. Preserve both logs and artifact hashes. A directive's presence in a passing upstream test is only a candidate, not proof the unannotated program fails.
4. Verify the permitted annotation delta, unchanged source semantics and required interface/capacity contract. Use the same source basename and preserve line positions, or run a whitespace-only baseline control: generated table names and allocation ordering must not turn a filename/line-number change into apparent pragma benefit. Classify directives that assert programmer knowledge separately from allocation steering. Exclude unchecked facts about mutual exclusion, initialization, traffic or control-plane behaviour from the first menu.
5. Retain compiler errors, resource reports, table placements, effective table capacities and emitted artifacts. Compilation establishes compiler-reported fit; it is not hardware execution or a complete semantic-equivalence proof. Use available semantic/model tests and report their scope separately.

The desired specimen should be small enough to inspect but involve genuine allocation interactions. Reducing table sizes, deleting functionality, relaxing required throughput, increasing stages/resource limits, changing targets, suppressing errors or introducing recirculation outside the fixed contract does not count as a repair.

## Compare advice with a small mechanical search

Use the same admissible patch grammar and compiler feedback for all policies. Keep the mechanical selector fixed: canonical enumeration or seeded sampling over the bounded menu, with a cache of attempted patches and no accumulating diagnosis rules. Count all qualification work separately; never give a model the withheld expert repair during evaluation.

Compare default compilation, the bounded mechanical search, Jev, a stronger adviser, and a calibrated cascade only when independent development episodes support a routing rule. Do not force escalation simply to obtain a three-tier demonstration. A known expert repair is a feasibility reference, not necessarily the optimum.

The principal outcomes are verified fit rate and compiler invocations/time to first valid fit under equal compiler-query budgets. Also record directive count, remaining resource headroom, provider latency/tokens, invalid or unsupported patches, and required human intervention. A model may be useful by finding a valid configuration with fewer expensive compiler attempts even when exhaustive search could eventually find one. If trivial search wins within the relevant budget, preserve that result.

Freeze prompts, menu, budgets, routing and held-out program families before evaluating model quality. A successful single example establishes plumbing only; generalization requires independent failure episodes and controls, not repeated minor variants counted as independent programs. Include unrepaired failures without claiming they are mathematically impossible unless independently certified.

## Present feasibility and target decision

The public Tofino backend provides documented placement and allocation controls; current source and candidate evidence are recorded in [the boundary research](research/p4-pragma-fit-boundary.md) and [the example search](research/p4-pragma-fit-examples.md). The pinned public compiler built in a local ARM64 Linux container and qualified an annotation-only pair: 14 stages rejected, then 12 stages and a target binary after eight side-table `@pack(3)` directives. Required capacities and the control-plane schema were preserved; SRAM increased from 880 to 936 blocks. This synthetic discovery case is development evidence, not a held-out model evaluation. [Prepared discovery inputs and build instructions](../examples/p4-fit/README.md) include the SwitchML application with and without its two allocation directives, plus synthetic table-pressure cases.

The user explicitly authorized Tofino as this research laboratory on 18 October 2026: “Use it then.” Public compiler qualification and local resource-fit experiments may proceed. The eventual current/career target remains open; transfer requires that target's actual resource-fit toolchain. A software-switch compile is not a substitute for hardware placement constraints.

MCP is optional transport for operations such as compile, inspect diagnostics and evaluate a patch. It neither supplies the selection policy nor proves a fit. The first implementation should use direct local calls unless a separate integration requirement emerges.
