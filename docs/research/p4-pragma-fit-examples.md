# P4 pragma fit examples: source qualification

Research date: 2026-10-18. Scope: a public P4 program that fails target resource allocation and succeeds after semantics-preserving pragma changes alone.

**No verified failure-to-success pair was found in this bounded source search.** The public Tofino backend has concrete pragma actuation tests and an authentic resource allocator, but the inspected tests do not establish the unannotated program fails. They are qualification seeds, not a positive experimental result. No compiler was built or run for this note.

All source links below pin `p4lang/p4c` revision `ca5aef25de1f847b5c88a05af583506484927015`, the `main` tree returned during this investigation. This is a source pin, not a claim that this revision has been locally qualified.

## Strongest concrete seeds

| Seed | What the primary source establishes | What remains unproved |
| --- | --- | --- |
| `PragmaNoGatewayConversion.WithoutPragma` and `.WithPragma` | Identical table/program structure, a single annotation change, and full-backend assertions for different emitted resource implementations. Without the annotation, the small constant ternary table is converted to a gateway; with it, the backend emits a ternary match table. | Both tests expect success. They do **not** demonstrate repairing resource exhaustion. |
| `PragmaStage.PragmaStage` | A 12,288-entry exact table has `@stage(1, 4096, "noimmediate")`, `@stage(2, 4096, "immediate")`, `@stage(3, 4096, "noimmediate")`, and `@pack(1)`. The test runs the full backend and checks the specified table fragments/action representation in stages 1, 2 and 3. | There is no without-pragma arm or baseline fit failure in the test. Removing annotations may simply compile successfully. |
| `PaContainerSizePragmaTest.SliceRequirement` | Explicit PHV container-size annotation parsing and slice requirements have a focused public test. | This test runs mock analysis passes; it does not prove full-program allocation success or failure. |

Sources: [gateway paired tests](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/test/gtest/pragma_no_gateway_conversion.cpp), [stage/packing test](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/test/gtest/pragma_stage.cpp), [PHV slice-requirement test](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/test/gtest/pa_container_size.cpp).

The gateway pair is the cleanest starting point for verifying that the harness changes an actual backend decision. Its constant table and empty actions also make it a poor standalone demonstration of application value. A resource-pressure qualification must use observable packet behavior and retain the complete program and table/control-plane contract.

## What the pragma interface actually promises

The backend's documentation describes `placement_priority` as changing the order in which tables are considered for placement. It describes `pack` as the number of exact-match entries per wide table word, with permitted values 1 through 9, and warns that larger values can make placement impossible. The `stage` documentation recommends `placement_priority` as its replacement. Thus an adviser could search meaningful combinations, but a higher numeric setting is not generically an improvement. These are backend-specific controls, not a portable P4 language optimization API. [Pinned pragma definitions](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/common/pragma/pragmas.cpp).

The backend itself documents interdependent resource allocation, table placement, and backtracking. This supports the user's proposed boundary: advice can influence a global fit search without replacing the compiler's legality and allocation machinery. It does not establish that a model will outperform the backend or a bounded mechanical search. [Backend architecture](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/README.md).

## Authentic failure report rejected as a benchmark

[p4c issue 5046](https://github.com/p4lang/p4c/issues/5046) reports a Tofino2 PHV-allocation failure involving 32-bit subtraction followed by a 24-bit assignment. The submitted fragment is incomplete, and the discussion requests the full source and compiler version. A follow-up suggests trying `--alt-phv-alloc`; the retrieved comments contain no confirmed successful result. This is a compiler option rather than a demonstrated pragma-only repair. It must not be presented as the required pair. [Source/version request](https://github.com/p4lang/p4c/issues/5046#issuecomment-2513652296), [proposed option](https://github.com/p4lang/p4c/issues/5046#issuecomment-2516423298).

## Qualification experiment before any model comparison

This is a proposed experiment, not an observed result:

1. Pin a publicly buildable compiler/backend and run the official gateway pair and stage test unchanged. Save compiler identity, complete inputs and emitted allocation artifacts.
2. Construct a small application-derived family that approaches a real resource boundary while preserving useful packet behavior. The gateway pair can verify actuation; a table-placement or PHV-heavy program is likely a better substantive workload. Do not create failure solely by adding deliberately bad annotations.
3. Require the normal unannotated compiler invocation to fail with an allocation diagnostic, not a parser/type error, internal error, timeout or harness failure.
4. Require at least one allowed annotation-only patch to compile under the same hardware limits, compiler and options. Keep table capacities, action semantics and permitted control-plane entries fixed. Do not relax limits with `phv_limit`, remove work, shrink tables, or assert unproved mutual exclusion to obtain a pass.
5. Compare output behavior against the same specification, using an available target model or a justified equivalence check; successful resource allocation alone is not a complete semantic oracle. If such checking is unavailable, report that limitation separately from fit.
6. Only then freeze an annotation action space and compare default compilation, a small fixed-order mechanical budget, Jev, and stronger reasoning at equal compiler-call budgets. A one-case annotation discovery is an existence proof; a held-out family is needed for generalization and escalation claims.

The unknown that matters first is whether an authentic public program exists in the narrow gap between the default allocator's failure and a semantics-preserving annotation-guided solution. The above sources give us the knobs and seeds, not that missing measurement.

## Target constraint

The concrete seeds found here target Tofino. They can support an explicitly labelled historical research testbed, but this note does not recommend Tofino as the user's current industry/career destination. No current non-Tofino public ASIC resource-fit toolchain or confirmed pragma repair pair was established by this bounded investigation. BMv2 compilation would not validate a Tofino PHV/stage-capacity claim: it is a different target. The upstream testing guide explicitly distinguishes behavioral-switch execution from the other backends. [P4C testing documentation](https://p4lang.github.io/p4c/repository_structure.html).

The user previously rejected Tofino as the long-term target. A later experiment should keep the harness/adviser contract portable while stating precisely which target supplied the resource-fit evidence. A successful old-target demonstration does not by itself establish transfer to a current ASIC.
