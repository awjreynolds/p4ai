# p4-multirepair-v2

**Escalation did not add repair coverage over the three-choice mechanical selector.** Each repaired two of four independently qualified repair cases. Escalation repaired one more case than cheap continuation, but that case recovered from an invalid Jev response and was already repaired by the mechanical selector. The frozen descriptive gate was not met.

The two remaining repair cases had curator-verified `a-pack-6` witnesses; no evaluated arm chose those witnesses or found another accepted repair within its budget. The successful model choices used `a-no-immediate`, a mechanism supplied in the shared development knowledge. This establishes a real unclosed repair gap, not intrinsic model incapability or a need for additional hand-coded rules.

This is the terminal result for v2: no extra retries, easier gate, or post-outcome selector changes were made. A domain-specific policy might do better, but this trial supplies no evidence that it would, and does not measure policy-maintenance savings.

This is compiler-reported resource fitting on synthetic programs. It does not establish hardware correctness, statistical superiority, or reduced policy maintenance.

The [self-contained evidence bundle](p4-multirepair-v2.json) includes every request, normalized response, source variant, compiler receipt, and capacity/interface audit. The trial bundle excludes the curator’s answers; those were released only after all choices finished and are linked separately below.

## Scope and qualification

The pre-call freeze is [b6ab706](https://github.com/awjreynolds/p4ai/commit/b6ab706), with [contract](../p4-multirepair-v2.md) and [complete hashes](../../experiments/p4-multirepair-v2-freeze.json). V1 required six repaired cases across three structures and failed that qualification screen. Before any adviser call, v2 separately froze four repaired cases across two structures plus a default-fitting control and a no-known-repair control. The policy, knowledge, three-attempt budget and positive gate remained unchanged. The six visible sources include a third structure only in a control; it is not a third repair structure.

These are previously untested source variants for the policy designer and advisers, deliberately selected by a separate curator using compiler outcomes. They are clustered synthetic descendants of a common scaffold, not a representative sample of production programs. Related development examples were available equally to the selector designer and both providers.

The [released qualification report and evidence](p4-multirepair-curation-v1/README.md) records all candidates, exclusions, prospective addenda and witness checks. Qualification used 150 completed compiler invocations plus two interrupted invocations, including three timeouts; 6,801.24 aggregate compiler seconds were measured, with interrupted durations unknown. The earlier 40 development probes and two helper probes are separate again. Concurrent compilation timings are not performance benchmarks.

| Case | Released role / verified witness | Trial result |
| --- | --- | --- |
| heldout-01 | overlapping admission; `a-no-immediate` | All arms repaired |
| heldout-02 | fork/join default-fit control | All arms accepted default; zero attempts |
| heldout-03 | accumulating-chain no-known-repair control | No arm repaired; curator exhausted the allowed menu |
| heldout-04 | accumulating chain; `a-pack-6` | No arm repaired |
| heldout-05 | accumulating chain; `a-no-immediate` | All except cheap continuation repaired |
| heldout-06 | overlapping admission; `a-pack-6` | No arm repaired |

A witness is a verified repair, not proof it is unique. No-known-repair is limited to the allowed menu and bounded compiler runs.

## Arms

| Arm | Accepted repairs | Attempts | Virtual queries | Provider errors |
| --- | ---: | ---: | ---: | ---: |
| cascade | 2 | 15 | 12 | 3 |
| cheap_continuation | 1 | 15 | 10 | 5 |
| codex | 2 | 11 | 11 | 0 |
| jev | 2 | 11 | 6 | 5 |
| mechanical | 2 | 13 | 13 | 0 |

## Paired escalation comparison

| Comparator | Wins | Losses | Net | Structures with wins |
| --- | ---: | ---: | ---: | --- |
| cheap_continuation | 1 | 0 | 1 | accumulating-chain |
| mechanical | 0 | 0 | 0 | none |

The gate requires at least two net additional repairs and wins across at least two structures against each comparator. The escalation and cheap-continuation arms share the exact recorded mechanical/Jev prefix.

## All case outcomes

| Case | Structure | Arm | Choices or errors | Raw fit | Accepted fit | Rejections |
| --- | --- | --- | --- | --- | --- | --- |
| heldout-01 | overlapping-admission | cascade | a-pack-3 → invalid_response → a-no-immediate | Yes | Yes | none |
| heldout-01 | overlapping-admission | cheap_continuation | a-pack-3 → invalid_response → a-no-immediate | Yes | Yes | none |
| heldout-01 | overlapping-admission | codex | a-no-immediate | Yes | Yes | none |
| heldout-01 | overlapping-admission | jev | a-no-immediate | Yes | Yes | none |
| heldout-01 | overlapping-admission | mechanical | a-pack-3 → a-no-immediate | Yes | Yes | none |
| heldout-02 | fork-join-chain | cascade | default fits | Yes | Yes | none |
| heldout-02 | fork-join-chain | cheap_continuation | default fits | Yes | Yes | none |
| heldout-02 | fork-join-chain | codex | default fits | Yes | Yes | none |
| heldout-02 | fork-join-chain | jev | default fits | Yes | Yes | none |
| heldout-02 | fork-join-chain | mechanical | default fits | Yes | Yes | none |
| heldout-03 | accumulating-chain | cascade | a-pack-3 → http_error → a-no-immediate | No | No | none |
| heldout-03 | accumulating-chain | cheap_continuation | a-pack-3 → http_error → http_error | No | No | none |
| heldout-03 | accumulating-chain | codex | a-no-immediate → a-pack-3 → a-pack-2 | No | No | none |
| heldout-03 | accumulating-chain | jev | a-pack-3 → http_error → http_error | No | No | none |
| heldout-03 | accumulating-chain | mechanical | a-pack-3 → a-no-immediate → a-pack-2 | No | No | none |
| heldout-04 | accumulating-chain | cascade | a-pack-3 → a-no-immediate → a-pack-4 | No | No | none |
| heldout-04 | accumulating-chain | cheap_continuation | a-pack-3 → a-no-immediate → a-pack-2 | No | No | none |
| heldout-04 | accumulating-chain | codex | a-no-immediate → a-pack-3 → a-pack-2 | No | No | none |
| heldout-04 | accumulating-chain | jev | a-pack-3 → a-no-immediate → a-pack-2 | No | No | none |
| heldout-04 | accumulating-chain | mechanical | a-pack-3 → a-no-immediate → a-pack-2 | No | No | none |
| heldout-05 | accumulating-chain | cascade | a-pack-3 → invalid_response → a-no-immediate | Yes | Yes | none |
| heldout-05 | accumulating-chain | cheap_continuation | a-pack-3 → invalid_response → invalid_response | No | No | none |
| heldout-05 | accumulating-chain | codex | a-no-immediate | Yes | Yes | none |
| heldout-05 | accumulating-chain | jev | a-no-immediate | Yes | Yes | none |
| heldout-05 | accumulating-chain | mechanical | a-pack-3 → a-no-immediate | Yes | Yes | none |
| heldout-06 | overlapping-admission | cascade | a-pack-3 → a-no-immediate → a-pack-4 | No | No | none |
| heldout-06 | overlapping-admission | cheap_continuation | a-pack-3 → a-no-immediate → a-pack-2 | No | No | none |
| heldout-06 | overlapping-admission | codex | a-no-immediate → a-pack-3 → a-pack-4 | No | No | none |
| heldout-06 | overlapping-admission | jev | invalid_response → invalid_response → invalid_response | No | No | none |
| heldout-06 | overlapping-admission | mechanical | a-pack-3 → a-no-immediate → a-pack-2 | No | No | none |

## Provider reliability and interpretation

There were 21 unique Jev attempts and 16 Codex attempts. Jev returned ten failures: six `invalid_response` and four `http_error`; Codex returned no provider failures. Shared-prefix failures appear in both paired arm totals but are counted once in these provider totals. Failure categories were retained, not raw response bodies or HTTP status details, so their underlying causes cannot be established from this evidence. Failed-call token usage is unknown.

The sole paired win was heldout-05: both arms shared `a-pack-3` then an invalid Jev response; Jev continuation returned another invalid response while Codex chose the accepted `a-no-immediate`. This is observed reliability recovery, not demonstrated superiority between two valid repair choices. On both cases with a `pack(6)` witness, escalation still failed. The same two cases were repaired by mechanical, standalone Jev, standalone Codex and escalation.

## Evidence and usage

21 physical compilations, 37 recorded provider attempts, and 0 interrupted attempts with unknown usage. Shared-prefix requests are counted once in provider totals; each arm retains its virtual query charges.

- jev: 318,804 input + 2,203 output tokens reported. Missing usage is unknown, not measured zero.
- codex: 679,589 input + 42,904 output tokens reported. Missing usage is unknown, not measured zero.

Reasoning-output and cached-input counts in the bundle are subsets, not extra tokens. Only normalized provider responses were retained, not raw HTTP bodies, Codex events or failed-call latencies. Recorded elapsed times are not a latency comparison.

Immutable compiler image: `sha256:5b27c37f3835c646c4a7f87c64de986a5fd5d71170b3f8b91e4bfbb908763af9`. Frozen source, code, executable and support-file hashes are embedded. Only annotation metadata, ordering, and the numeric top-level ID of `pipe.snapshot.cfg` of type `SnapshotCfg` are excluded from BFRT comparison; application IDs remain checked.

Reported successful-call usage totals **1,043,500 input plus output tokens**; this excludes unknown usage from ten failed calls. Cached-input and reasoning-output counts are subsets already included.

The independently reviewed runner and exporter passed 70 focused tests (55 experiment-related plus 15 exporter tests). A real cached replay with both providers and compiler invocation disabled reproduced the completed result with all 1,610 run files byte-identical. No additional quality calls or compiles were used for reporting. The final independent evidence audit passed: all 30 case-arm records, 55 unique terminal decisions, shared prefixes, accepted capacities/interfaces, provider counts and released qualification witnesses were verified. The public curation archive’s 2,295 inventory hashes passed; every selected witness was matched to the frozen trial source. No additional model or compiler calls were made during that audit.

The experiment demonstrates a replayable, compiler-authoritative harness with equal supplied knowledge and a deliberately simple selector. It does not demonstrate broad program generalization, packet-level execution or semantic equivalence, reduced maintained policy, or a commercially current hardware destination.
