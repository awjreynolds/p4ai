# Exported source ancestry and groups

This document describes source construction only. It contains no per-case compiler outcomes, repair labels, case roles or adviser choices. Case IDs are assigned by ascending SHA256 of baseline source bytes; they encode no qualification role.

All sources are synthetic derivatives of the same public TNA parser/deparser and table scaffold in `examples/p4-fit/generate.py`. They are not independent production applications. New action, control and dependency graphs were constructed by the independent curator. Width or capacity changes within a graph are counted as one structure.

Every source has eight side policy tables and twelve primary graph tables. Group **a** always means the eight side policy tables; group **b** always means all twelve primary graph tables. Groups were fixed by source semantics before any qualification. Every declared application table and its requested logical capacity appears in the manifest.

The uniform menu contains 25 nondefault candidates plus the unannotated default. Each source uses four 80-character annotation slots per table. Alternatives retain `program.p4`, all source-line positions and every byte outside the declared slots. They are fresh alternatives, not cumulative edits.

| Case | Source structure | Primary action/control dataflow |
|---|---|---|
| heldout-01 | overlapping-admission | Twelve dependent token-rewrite lookups. Even/odd side-table subsets are admitted by distinct overlapping tenant-bit predicates (bits 0 and 2). Either, both or neither subset may execute. |
| heldout-02 | fork-join-chain | Four repeated diamonds: two parallel token-keyed lookups produce separate fork fields; a join lookup keys on both fields and rewrites the token. Eight side lookups remain independent. |
| heldout-03 | accumulating-chain | Twelve dependent lookups rewrite the token and accumulate an observable packet field. Independent 32-bit action parameters supply the token and accumulator updates. |
| heldout-04 | accumulating-chain | Twelve dependent lookups rewrite the token and accumulate an observable packet field. One 32-bit action parameter supplies both updates. |
| heldout-05 | accumulating-chain | Twelve dependent lookups rewrite the token and accumulate an observable packet field. One 32-bit action parameter supplies both updates. |
| heldout-06 | overlapping-admission | Twelve dependent token-rewrite lookups. Even/odd side-table subsets are admitted by distinct overlapping tenant-bit predicates (bits 0 and 2). Either, both or neither subset may execute. |

All packet results are emitted. The source construction and annotation-only checks do not establish executed packet equivalence, hardware performance or production generalization. Compiler qualification and adviser evaluation are separate evidence.

The initial curation grid and two prospective addenda are identified by SHA256 in `curation-metadata.json`. Original failed, interrupted and excluded searches remain retained in the sealed evidence ledger. Expert outcomes remain sealed until all adviser choices finish.
