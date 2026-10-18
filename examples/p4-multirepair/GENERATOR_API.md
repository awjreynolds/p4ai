# Generator handoff

`generate.py` is an input generator only. No test selection or test qualification was performed by its developer. The developer received a tentative policy ordering and then stopped; a fresh curator must own test selection. Existing outputs under `artifacts/p4-multirepair-curation/development/` are developmental, including all failed searches. The initial40 probes are indexed in `development-search-summary.json` one directory above them.

Import `render(spec, policy)` for `(source_text, required_table_inventory)`, or `generate(spec, fresh_output_path)` to write all26 policy sources and a generation manifest. Existing destinations are refused. CLI: `python3 examples/p4-multirepair/generate.py --spec SPEC.json --output FRESH_DIRECTORY`.

| Spec key | Allowed meaning |
| --- | --- |
| `structure` | `diamonds`, `exclusive`, or `wide-actions` |
| `rounds` | Integer1–12: diamond iterations, tables per exclusive branch, or accumulating updates |
| `entries` | Positive required size for each primary lookup/update/route table |
| `key_bits` | Multiples of8,40–96: 32-bit token plus tenant field |
| `join_entries` | Positive required size for diamond join tables; default16384 |
| `outputs` |1–8 accumulating result fields for wide actions; default4 |
| `parameter_bits` |8,16,32 for wide-action parameters; default32 |
| `extras` |0–5 independent policy lookups; default0 |
| `extra_entries` | Positive required size of each independent policy table; default65536 |

When extras are present, wide-actions requires outputs≤1 so result fields do not overlap. Every family emits its packet-header results. Diamond fork outputs are inputs to join lookups, which update the token for the next diamond. Exclusive branches update the token and accumulate their branch result. Wide-action updates accumulate token and result fields. Independent lookups use the original packet's result7+tenant as key and write/accumulate result2 onward. No execution semantics or real hardware performance is proven by these structural checks.

All26 policies (including default) are in `POLICIES`. Group mapping is source-defined. Without extras: diamonds a=forks/b=joins; exclusive a=branch0/b=branch1; wide-actions a=even/b=odd updates. With extras: a=independent policy tables/b=all primary graph tables. The emitted manifest records exact table capacities and groups. Four80-character slots precede each table; all variants preserve basename `program.p4`, code positions and all bytes outside slots.

The runner manifest exporter is not implemented. Its approved API is `schema_version:1,cases:[{id,structure,requirements,default,candidates}]`, with exact context names `Ingress.<table>`, source paths relative to the corpus manifest, and each candidate containing `id,source,edits:[{line,text}]`. Edits are1-based annotation insertion into blank fixed-width slots. Use `scripts.p4_multirepair_experiment.validate_manifest` and `acceptance` to validate inventory/interface/capacities, plus `scripts.p4_compile_probe.compile_case` for actual manifest/binary fit. The application-interface gate excludes only annotation metadata/list order and the generated SnapshotCfg object's own ID. A capacity/interface pass alone does not mean the compiler succeeded.

The existing TNA parser/deparser is reused from `examples/p4-fit/generate.py`; the generation manifest pins both scripts. New program topology and action dataflow are produced by this generator. Run `python3 examples/p4-multirepair/selfcheck.py` and Ruff before freezing. Small16-bit action-parameter development variants emitted invalid-container-action warnings, so those are not qualified positive examples; 32-bit variants have valid low-pressure compiler fits.

Separate development work by another agent qualified an overlapping-admission immediate-data repair under `examples/p4-immediate-dev/`. Its README and qualification evidence are public development information and do not constitute hidden evaluation cases. The canonical shared development packet is `development.md`; do not alter it after the coordinator freezes its hash.
