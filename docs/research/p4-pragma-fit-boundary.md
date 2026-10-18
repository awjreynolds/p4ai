# P4 pragma selection as a resource-fit boundary

Research status: primary-source inspection, 18 October 2026. No compiler builds, model calls or failing-to-passing fixture were run in this investigation. P4C source was inspected at commit `ca5aef25de1f847b5c88a05af583506484927015`.

The proposed experiment is materially different from replacing a small local compiler heuristic. A valid program can encounter a target resource allocation failure; a harness can inspect that failure, select a bounded set of implementation annotations, and ask the existing target compiler to validate a new allocation. This is a plausible research boundary, not yet evidence that a model will outperform search.

## Real controls in the public compiler

The following definitions exist in the pinned [Tofino pragma implementation](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/common/pragma/pragmas.cpp). They are target-specific, not portable P4 language features.

| Annotation | Documented control | Pilot interpretation |
|---|---|---|
| `placement_priority` | Changes the order of table placement; positive values favour earlier placement, negative later. | Strong first candidate: guide placement without changing table capacity. |
| `pack` | Requests 1–9 exact-match entries per wide table word; large values can prevent placement. | Search allocation tradeoffs, preserve requested entries. |
| `immediate` | Chooses whether to try packing action parameters into match overhead; default is 1. | Setting 1 alone is not an improvement over default. |
| `force_immediate` | Requires all parameters in match overhead; failure if unavailable; excludes indirect action tables. | A restrictive allocation choice, not guaranteed rescue. |
| `stage` | Forces placement and optionally splits entries across named stages. | Existing control, but its help recommends `placement_priority` instead. |

`pa_container_size` directs PHV field allocation into 8-, 16- or 32-bit containers, including splitting wider fields. It is another possible search control after table placement is established. Its help contains an inconsistent illustrative 28-bit container despite the stated allowed sizes; generated candidates must follow actual validation, not copy that example. [Implementation](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/phv/pragma/pa_container_size.cpp).

## Annotations are not all harmless compiler hints

`pa_no_init` explicitly relies on a control-plane guarantee: when a metadata value matters, it has been written before it is read. The compiler may be unable to establish that property. Model-generated use can therefore turn a failing compile into a successful but incorrectly configured program. Exclude it unless the harness independently verifies the required control-plane contract. [Implementation and documented precondition](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/phv/pragma/pa_no_init.cpp).

`pa_mutually_exclusive` tells PHV allocation that fields or headers can share storage because they are exclusive. The implementation records that relationship; its presence is not a proof of exclusivity. Treat it as an assertion requiring separate justification, not a free optimisation knob. [Implementation](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/phv/pragma/pa_mutually_exclusive.cpp).

Recommended initial contract: change only allowlisted allocation directives. Freeze parser/control logic, field widths, table sizes, actions, control-plane interface, target and compiler revision. Do not let a model reduce functionality, entries or precision to produce the word “success.” This contract is our proposed experimental safeguard, not a claim that all accepted annotations preserve every observable target property.

## Public toolchain and evidence

The public P4C tree includes the Tofino backend and assembler build subdirectories. Open-P4Studio documents standalone `p4c my_program.p4` compilation, `--target tofino2`, and `-g` for detailed placement logs. A hardware switch is not required to investigate compiler resource allocation; execution correctness and operational qualification are separate. Local build feasibility has not been tested here. [Build definition](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/CMakeLists.txt), [compilation guide](https://github.com/p4lang/open-p4studio/blob/main/docs/P4-Compilation.md).

P4C generally documents Ubuntu 22.04/24.04 support and unofficial macOS/Apple Silicon support. That does not establish that this particular backend builds successfully on this laptop. The backend README contains old private-repository setup instructions, so it should not be used as the sole current installation guide. [P4C setup](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/README.md), [backend README](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/README.md).

The manifest schema provides `compilation_succeeded` and references generated resource files. The resource schema describes structured allocation results including MAU stages and PHV resources. Preserve exit code, diagnostic category, manifest, assembly and resource reports; success alone is weaker evidence than a complete successful allocation. On failure, do not assume every report exists. [Manifest schema](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/compiler_interfaces/schemas/manifest_schema.py), [resource schema](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/compiler_interfaces/schemas/resources_schema.py).

An upstream test applies `@stage` and `@pack` to a 12,288-entry table, runs the full backend and checks the resulting stages and immediate allocation in MAU assembly. This is a concrete source-level example of annotation-driven allocation. **It is not an observed unannotated failure repaired by annotations**, and we did not run it. [Upstream test](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/test/gtest/pragma_stage.cpp).

## Proposed proof, rather than a claimed result

1. Qualify a pinned public backend and find a genuine resource failure with unchanged logical requirements. Reject syntax errors, unsupported constructs, compiler crashes and deliberately bad existing annotations.
2. Show at least one annotation-only repair verified by that backend. This establishes a useful search space before evaluating intelligence.
3. Freeze program families, permitted annotations, compile-attempt budget and simple search baseline. Hold entire families out; a near-identical table-size variant is weak generalisation evidence.
4. Give search and advisers the same diagnostics and budgets. Compare valid rescues, compiler attempts, elapsed time, inference usage, incorrect assertions and policy size. Include a domain-trained local selector only after there are enough independent examples.
5. A stronger-model success alone proves an expert-guided repair capability. Escalation earns a separate claim only if its routing beats the corresponding fixed advisers and mechanical search under the declared cost objective.

Tofino is a useful possible historical research testbed because its resource compiler and controls are inspectable. That is not a recommendation to reverse the user's rejection of Tofino as a career destination. Transfer to a current commercial target needs that target's accessible compiler, diagnostics and supported controls; no such transfer is established by this note.
