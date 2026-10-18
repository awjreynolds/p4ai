# P4 information-access diagnostic v1

Both advisers selected the demonstrated `side-pack-3` strategy when given the worked example. It repaired tenant56 and failed on tenant8, exactly matching the fixed one-choice mechanical reference. **There was no improvement over that mechanical choice.** Without the example, each adviser chose other policies that failed on both cases.

This is consistent with information access affecting choices, but one observation per cell cannot separate that effect from response variability. The result demonstrates neither independent discovery nor an ability to discriminate when the example applies. It also does not show that the models are incapable of making useful compiler decisions.

The [frozen diagnostic contract](../p4-information-diagnostic-v1.md) and [complete evidence bundle](p4-information-diagnostic-v1.json) accompany this report. The [original v1 results](p4-fit-advice-v1.md) remain unchanged.

## Setup

The original mechanical recipe incorporated a development repair that advisers were denied. Its comparison evaluates those deployed strategies; it does not isolate model capability under equal development knowledge.

This retrospective diagnostic deliberately selects two already-measured cases: tenant56, where a repair exists, and tenant8, where that same choice fails. Eight isolated requests cover two providers × two cases × two information conditions, with one choice per cell. Jev is pinned to `jev-1.14.0`; Codex uses `gpt-6-luna`, effort `max`. A counterbalanced schedule varies condition order; requests share no conversational history. There is no seed control or variability estimate.

Both conditions receive the original baseline packet, admissible menu and neutral pinned compiler documentation. Only the worked-example condition receives the verified development tenant48 repair: eight side-table `@pack(3)` annotations turn a 14-stage rejection into a 12-stage fit. Neither condition contains the tested cases’ candidate outcomes. All choices were saved before evaluation. Candidates replace the original proposal; annotations do not accumulate.

## All eight decisions

“Fit” means both raw compiler acceptance and the original strict capacity/full-BFRT gate pass. None of these selected candidates had a raw-fit/schema-rejection discrepancy.

| Case | Provider | Information | Chosen policy | Raw fit | Strict fit | Input tokens | Output tokens |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| tenant56 | Jev | Baseline | side-priority | No | No | 8,894 | 199 |
| tenant56 | Codex | Baseline | chain-priority | No | No | 25,068 | 1,942 |
| tenant56 | Jev | Worked example | side-pack-3 | Yes | Yes | 9,117 | 200 |
| tenant56 | Codex | Worked example | side-pack-3 | Yes | Yes | 25,268 | 856 |
| tenant8 | Jev | Baseline | side-later | No | No | 8,903 | 199 |
| tenant8 | Codex | Baseline | chain-priority | No | No | 25,068 | 1,999 |
| tenant8 | Jev | Worked example | side-pack-3 | No | No | 9,126 | 200 |
| tenant8 | Codex | Worked example | side-pack-3 | No | No | 25,270 | 890 |

Each provider changes from zero to one strict fit across these two cases when shown the example. The equal-budget mechanical reference chooses `side-pack-3` once per case and also gets one fit. This diagnostic is not a comparison against three mechanical attempts or an escalation trajectory.

## Evidence and usage

All eight evaluations used verified cached v1 compiler evidence: **zero new compilations**, eight virtual queries, eight valid responses, zero provider errors and no selected-probe timeouts. The accepted tenant56 repair occupies 12 stages and 936 SRAM blocks, preserves logical capacities and full BFRT equality excluding annotation metadata, and has sufficient physical capacity. Other selected policies fail stage capacity. No packets were executed on hardware.

Jev reported 36,040 input and 798 output tokens; Codex reported 100,674 input and 5,687 output tokens. Total: **136,714 input + 6,485 output = 143,199 tokens**. Codex reported zero cached-input tokens; Jev does not report that field. The 5,404 reasoning-output tokens are already included in output and must not be added again. Elapsed values are observations, not comparative latency measurements.

The bundle embeds all eight packets, eight decisions, freeze, choices summary, evaluation, config, exact source text, original compiler receipts, manifests/binary hashes, diagnostics and capacity/schema audits for six selected candidate probes plus two baselines. It includes the development-example evidence and full frozen compiler identities. Host paths are normalized to `$REPO`; hashes identify original bytes. Binary bytes and full compiler IR are not embedded.

The compiler is p4c Tofino 9.13.4 at `ca5aef25de1f847b5c88a05af583506484927015`, using local immutable image `sha256:5b27c37f3835c646c4a7f87c64de986a5fd5d71170b3f8b91e4bfbb908763af9`, target `tofino`, architecture `tna`, flags `-g -o /output/compiler`. Reproduction requires the pinned toolchain; a rebuilt image needs a separate replication configuration. Recorded model decisions are auditable without calling providers again, but new responses may differ.

Frozen inputs, packet derivations, response mappings and referenced compiler receipts passed independent audit. All original v1 evidence remained unchanged. Focused validation passed 27 tests and Ruff; cached choose/evaluate resume passed with provider and compiler calls disabled.

These observations do not establish causality, generalization, escalation benefit, or reduced policy maintenance. A stronger next quality experiment would need independent application families, equal development information, a frozen application-interface contract and fresh cases hidden from policy designers. This diagnostic alone does not justify a custom decision model, MCP service or additional model infrastructure.
