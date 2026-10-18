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
