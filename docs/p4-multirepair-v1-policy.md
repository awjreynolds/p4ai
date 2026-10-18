# Fixed mechanical policy for p4-multirepair-v1

The policy tries **a-pack-3 → a-no-immediate → a-pack-2**, stopping when the experiment's acceptance gate passes. It permits at most three repair attempts, uses the same order for every case, and contains no case lookup, feature branch, conditional repair rule or learned model. Every attempt replaces the unannotated baseline; annotations do not accumulate. Baseline acceptance and failed-attempt accounting belong to the runner's common contract.

| Attempt | Canonical candidate | Development support |
| --- | --- | --- |
| 1 | `a-pack-3` | The established packing anchor changes a 14-stage failure into an accepted 12-stage fit with preserved required capacities and interface. |
| 2 | `a-no-immediate` | The new overlapping-admission anchor changes a 15-stage failure into an accepted 12-stage fit under the new interface/capacity gate. This provides a second repair mechanism. |
| 3 | `a-pack-2` | Previously published side-packing-2 produced a raw compiler fit with preserved application interface and capacity; the original strict gate rejected generated SnapshotCfg metadata changes. That original rejected score remains unchanged. |

Both qualified anchors map group a to side tables and b to chain tables. The generic corpus defines groups from each source; a does not universally mean side tables. The policy deliberately does not reinterpret groups or inspect source features. This limits transfer when group roles differ.

Packing-3 goes first because it has an established accepted development repair and a published subsequent accepted fit. The verified immediate repair occupies the second slot to cover a different allocation mechanism. The old packing-2 and packing-4 raw fits provide equal reported support; the lower numeric factor is a deterministic tie-break, not a claim that lower packing is generally faster or more likely to fit. Development failures and the absence of a demonstrated priority rescue do not justify substituting a priority candidate. No exhaustive ranking or parameter search was performed.

## Exact knowledge and equal-information requirement

The designer read exactly these six documents for the ordering. Development-only coordination messages supplied candidate grammar, group mappings, qualification status and failed-development observations; the canonical development packet reproduces their substantive facts. No new sealed test source, dimensions, labels, manifest, chosen candidate or result was read. Prior experiment results are explicitly development knowledge in this new experiment.

| Source | SHA-256 at policy freeze |
| --- | --- |
| `docs/results/p4-pragma-fit-qualification-v1.md` | `c63e69760f5d5b1874358a9404bff4c4b51833ac8ca80adf158f08016156fa26` |
| `docs/results/p4-fit-advice-v1.md` | `ec5550b079e50e98f802da22886de7f9c2aa1abc8367ab22c7cfaaf7ef17d4a7` |
| `docs/results/p4-information-diagnostic-v1.md` | `c7fec3592c2e93bf8e0ad3ba17c181ece22354ea1491ad83dd2c0e19ce919868` |
| `docs/p4-information-diagnostic-v1.md` | `95be9773fd3839cefe886b3cb852d7ab94cc63a627c5558af21804ca455d1a47` |
| `examples/p4-immediate-dev/README.md` | `b891f9f2f36ff570aa93ccbb83eb8f7f0bc1efdedfc81592f59f1264aa3989b4` |
| `examples/p4-multirepair/development.md` | `24fcedf472110a50ad6e0d64b9d0f1fec7e3c393423e5e7130d87ad34ef0d685` |

The coordinator's `experiments/knowledge/p4-multirepair-v1.md` must contain the four prior documents and the whole canonical development packet verbatim before adviser requests are frozen. The packet itself embeds the immediate README verbatim, so every source the designer consumed is available to both advisers. The source list in the policy is also frozen by the runner. Raw qualification JSON, helper sources and compiler outputs were not read by the policy designer.

The designer communicated a tentative order to the development curator before receiving the request not to send the final order. No test information was returned or used. That curator was therefore not fully blinded to policy hypotheses. The coordinator instructed them to stop before test selection and will assign test selection to a fresh-context curator without the policy order. The final order and file contents were not sent to the development curator. This provenance correction does not change the policy bytes.

## Freeze and scope

Machine-readable policy: `experiments/p4-multirepair-v1-policy.json`.

Policy SHA-256: `09cdc64f9f2502bf8b55e94c6274d9173cc0159f41f60a0a596658131abf558c`.

The development packet was pinned to `24fcedf472110a50ad6e0d64b9d0f1fec7e3c393423e5e7130d87ad34ef0d685` before the policy was written. No later development or test outcomes may change these bytes within this experiment. A changed policy requires a separately identified experiment. Local verification checked valid JSON, three unique canonical IDs and the three-attempt limit; no live provider calls or compiler runs were made by this designer.

These choices are an agent-authored mechanical comparator, not measured human maintenance effort. The two repair mechanisms share synthetic chain/side ancestry, and successful compiler fit is not executed packet equivalence or hardware performance. The third choice's historical evidence remains a raw fit with its original gate rejection, rather than a retrospectively rescored success.
