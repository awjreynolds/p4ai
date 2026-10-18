# Shared development knowledge for p4-multirepair-v1

This packet contains DEVELOPMENT knowledge available equally to the fixed-policy designer and all advisers. It contains no held-out case identities, dimensions, outcomes or repair labels. It supplements the four previously published background documents included verbatim by the experiment coordinator: the original pragma qualification, fit-advice results, information-access diagnostic results, and its design. These are synthetic research examples, not independent production applications.

## Uniform candidate grammar and groups

Every final case has the same 25 nondefault candidate IDs: `a-pack-1` through `a-pack-9`, `b-pack-1` through `b-pack-9`, `a-no-immediate`, `b-no-immediate`, `all-no-immediate`, `a-priority`, `b-priority`, `a-later`, `b-later`. The unannotated `default` is additional. Each candidate replaces the baseline proposal; annotations never accumulate. No candidate reduces capacity or changes packet code.

`a-pack-N` applies `@pack(N)` uniformly to group a; b is analogous. `*-no-immediate` applies `@immediate(0)`, and `all-no-immediate` covers both groups. `*-priority` applies `@placement_priority(10)` and `*-later` applies `@placement_priority(-10)`. Concrete candidate edits and source show which tables each group contains; IDs carry no fit labels.

In the two qualified development anchors below, **a = side tables; b = chain tables**. The newly explored templates use these explicit mappings: without independent policy lookups, diamonds have a=fork lookups/b=join tables; exclusive chains have a=first branch/b=second branch; accumulating action chains have a=even steps/b=odd steps. With independent policy lookups enabled, a=those independent tables and b=the whole primary dependency graph. A group label is a source-defined set, not a universal semantic role.

## Verified packing development anchor (previously published)

The old tenant48 program has twelve dependent chain tables, each requiring 16,384 entries, and eight independent side tables, each requiring 114,688 entries. Side keys are 80 bits (flow32+tenant48), with 32-bit action values. The unannotated source fails at14 occupied stages/880 SRAM blocks. Applying `@pack(3)` to all eight side tables, now named **a-pack-3**, yields a12-stage fit/936 SRAM blocks. Required logical capacities and the full control-plane interface excluding annotations match. Physical slots can change but satisfy requested capacities. This was a development discovery, not an unseen evaluation result.

The published v1 report also describes old tenant64 raw fits for side packing2 and4 (here a-pack-2/a-pack-4), rejected by the original stricter gate only because generated SnapshotCfg metadata differed. These observations are old development knowledge. They are not proof that those policies generalize.

## Verified immediate-data development anchor (new control graph)

The new overlapping-admission example descends from the published tenant8 chain/side program. Two overlapping conditions alter its control graph: tenant bit0 enables side tables0–3, and bit1 enables side tables4–7. Either group, both groups, or neither may execute. All twelve dependent chain tables still execute. This is a meaningful control change within the same ancestry, not an independent application.

Required sizes are twelve chain tables×16,384 and eight side tables×196,608. Side exact-match keys are40 bits (flow32+tenant8), action values32 bits. The two sources differ only in eight fixed-width annotation slots, retaining the basename and all source positions.

| Proposal | Raw compiler/assembler | Occupied stages | SRAM blocks | Logical/physical capacities and new full-BFRT gate |
| --- | --- | ---: | ---: | --- |
| Unannotated | Failure: stage overflow |15|942|Preserved, but compiler rejects |
| **a-no-immediate** | Successful manifest and nonempty binary |12|944|Pass |

The new gate ignores annotation metadata and list order plus only the generated `pipe.snapshot.cfg` SnapshotCfg object's own ID. It preserves application IDs and all other schema fields. Exactly two compilations qualified this development pair. No model or hidden-test result informed it. Source and evidence identities are recorded in `examples/p4-immediate-dev/qualification.json`; the designer need not inspect that raw evidence because the relevant facts are reproduced here.

## Other development observations already disclosed

Three additional structures were explored: repeated parallel lookup/join diamonds; mutually exclusive service chains; and chains accumulating multiple observable outputs. Their low-pressure forms genuinely compile, confirming feasible dependency paths. High-pressure forms encounter real stage-capacity failures. Rigid layers leave little scheduling freedom and often already use efficient compiler-selected packing. Forcing packing3 worsened one baseline fit to18 stages. Large action payloads already reside in action-data memory, making `immediate(0)` inert; smaller total parameters were then explored. Static checks verify annotation-only fixed positions, full capacity inventory and emitted dataflow; no hardware packets were run.

Adding independent policy lookups alongside a diamond graph produced a near-boundary default14-stage failure; forcing group-a packing3 worsened it to15, and a-no-immediate still used14. Prioritizing the primary graph (b-priority) or delaying independent tables (a-later) also left that development case at14. Thus placement priority was a hypothesis, not a demonstrated repair. No new packing or priority rescue has been disclosed to the designer. All discovery attempts remain recorded separately and do not change these qualified anchors.

## Neutral pinned compiler documentation

These descriptions paraphrase `PragmaPack`, `PragmaPlacementPriority` and `PragmaImmediate` in p4c commit `ca5aef25de1f847b5c88a05af583506484927015`, `backends/tofino/bf-p4c/common/pragma/pragmas.cpp`.

`@pack(N)` requests1–9 exact-match entries per wide table word, not entries per SRAM unit. Wider packing may consume more memory units per word and can prevent placement. Larger N is not necessarily better. `@placement_priority(N)` changes consideration order: default0, positive earlier and negative later. `@immediate(0)` disables the default attempt to put action parameters in match overhead instead of a separate action-data table. It can change allocation geometry; it need not reduce total SRAM. None guarantees fit.

All claims are compiler-reported fit and declared interface/capacity preservation. They do not establish executed packet equivalence, hardware performance, model generalization, or reduced policy maintenance. No hidden test choice should be inferred from the development anchors.

## Verbatim additional document read by the policy designer

Source: `examples/p4-immediate-dev/README.md`

# Immediate-allocation development example

This is a **development example**, available equally to the mechanical-policy designer and model advisers. It is not an unseen evaluation case.

The baseline fails because its placement uses 15 stages on a target with 12. Adding `@immediate(0)` to all eight side tables produces a compiler-accepted binary using 12 stages. SRAM usage changes from 942 to 944 blocks. All 20 application tables retain their declared logical capacities and sufficient physical allocation; the new experiment's full BFRT comparison passes with its narrowly specified generated SnapshotCfg ID exclusion.

| Variant | Compiler result | Actual stages | SRAM blocks | Interface/capacity gate |
|---|---|---:|---:|---|
| `default/program.p4` | Stage overflow, exit 2 | 15 | 942 | Pass |
| `side-no-immediate/program.p4` | Successful manifest and nonempty binary, exit 0 | 12 | 944 | Pass |

The two sources differ only in eight fixed-width annotation slots. Source basename and all line positions remain unchanged. The generated manifest covers every application table. The table group mapping used by the new experiment is **a = eight side tables**, **b = twelve chain tables**; the demonstrated action is therefore **a-no-immediate**.

The baseline descends from the published tenant8 chain/side source. The structural change is two overlapping admission predicates: `tenant & 1` enables side0–3, and `tenant & 2` enables side4–7. A packet can execute either group, both, or neither; the twelve dependent token-chain tables still execute. This changes the control graph, but remains a synthetic derivative of the previous family, not an independent production application. Table dimensions remain eight side tables of 196608 entries with 40-bit keys and 32-bit action values, plus twelve chain tables of 16384 entries.

Exactly two development compilations were made. Both use the same immutable image, compiler executable/support hashes, target and flags. Baseline elapsed time was about 63 seconds; the repair about 8.4 seconds. These are provenance timings under concurrent development load, not performance measurements. No model calls or hidden test outcomes were used.

`qualification.json` records the source and receipt hashes, complete compiler identity, source edits, application capacities and acceptance audits. Raw receipts and compiler outputs are in `artifacts/p4-immediate-dev/overlapping-admission-196608/`. Run `.venv/bin/python examples/p4-immediate-dev/qualify.py` to repeat the evidence checks without compiling. `develop.py overlapping-admission` regenerates the development sources and compiles only if corresponding receipts are absent.

This establishes compiler-reported fit under the stated acceptance contract. It does not establish packet execution, full semantic equivalence, hardware performance or adviser generalization.
