# First native harness compiler pilot

The harness compiler builds real ARM64 executables and successfully routes a bounded LLVM eviction decision through Jev and Codex. **This establishes integration, not a quality advantage.** Stock LLVM was faster in the initial interleaved measurements, and escalation selected the same action as Jev alone.

The [machine-readable evidence](native-pilot.json) contains check outcomes, model usage, action identities, executable hashes and all timing samples. Full command logs, decision traces and build provenance remain in the local ignored `artifacts/` directory. These results were collected on an Apple M3 Mac, Darwin 25.6.0, using LLVM revision `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4` from IREE's public fork. This is a single-machine pilot.

## What passed

- A pinned native Release compiler build with assertions enabled, source/patch/binary provenance checks, and LLVM machine-instruction verification.
- Stock, first-eligible and last-eligible compilation; different eligible actions reached the real allocator and produced correct executables.
- Exact trace replay and refusal of mismatched replay identities.
- Negative, out-of-range and masked actions rejected by the C++ runtime guard, with two live attempts per case. Assertion-disabled builds were not separately run.
- Three synthetic register-pressure kernels checked against independent scalar references, including edge inputs and deterministic generated inputs.
- Live Jev, Codex and Jev→Codex interventions, followed by correct native execution.
- 58 local tests and Ruff checks passed. The automated tests include mocked provider/protocol checks; they are separate from the live compiler and provider evidence above.

The harness keeps the selector small: choose the first eligible action except for one model intervention. LLVM still supplies eligibility, features and substantial surrounding allocator policy. The [boundary inventory](../selector-boundary.md) is part of the claim, not an implementation footnote.

## Live decisions

All three conditions intervened once at observation 0 in `pressure_poly`, with the same feature fingerprint and 30 eligible actions. Later decisions used first-eligible. Column 32 is LLVM's no-physical-eviction sentinel. Each condition was compiled afresh; a changed action can change the subsequent allocation trajectory.

| Condition | Action | Model-call latency | Input / output tokens |
| --- | ---: | ---: | ---: |
| Jev (`jev-1.14.0`) | 22 | 433 ms | 11,576 / 248 |
| Codex (requested `gpt-6-luna`, max) | 27 | 6,575 ms | 24,387 / 373 |
| Cascade: Jev | 22 | 348 ms | 11,576 / 248 |
| Cascade: Codex | 22 | 6,322 ms | 24,389 / 450 |

The cascade's Jev confidence was 0.28, below the provisional 0.5 threshold, so the harness called Codex. This is a distribution-derived confidence score, not a correctness probability. The threshold is uncalibrated. Codex's model name is the requested CLI alias; the exact serving snapshot was not available. Reasoning-output tokens are included in output totals, not added again.

Four successful provider calls used 71,928 input and 1,319 output tokens. This excludes development work and subagents. Jev and Codex authentication both worked with the existing credentials. No GPU or rented hardware was used.

## Interleaved runtime measurements

One warm-up per executable, then nine seeded, shuffled rounds, each with 100,000 driver iterations. Every run produced checksum `f06d6f854d881fb9`. Times cover the driver workload, not model calls or compilation. The driver checks correctness as well as running the kernels; this is not an isolated per-kernel throughput benchmark.

| Policy | Median | Observed range |
| --- | ---: | ---: |
| Stock LLVM Greedy | 231.779 ms | 229.588–241.135 ms |
| First eligible | 241.673 ms | 239.169–247.996 ms |
| Last eligible | 250.941 ms | 249.856–257.436 ms |
| One Jev intervention | 240.835 ms | 240.221–253.707 ms |
| One Codex intervention | 241.056 ms | 240.354–258.724 ms |
| One cascade intervention | 240.923 ms | 239.334–251.412 ms |

Stock LLVM has the lowest median. The small differences between first-eligible and the model conditions do not establish an improvement. There is one synthetic corpus, one selected decision state, no held-out evaluation and no statistical performance claim. The stronger call in the cascade did not change Jev's action and therefore supplies no evidence of added decision value in this run.

## Reproduction and next decision

Follow the [build and validation instructions](../../README.md). Run `scripts/experiment.py` separately with `--method jev`, `--method codex` and `--method cascade`, pointing each at the passing validation report. Provider output and service versions can change; recorded usage is observational. `scripts/benchmark.py --help` describes the interleaved runner; provide the stock, first, last and model executable paths with repeated `--executable label=path` arguments.

The next substantive experiment must test whether escalation earns its added latency and policy complexity. It needs held-out workloads, useful decision states beyond the first ambiguous one, stock and simple learned/reuse comparators, a routing rule calibrated away from the test set, and explicit accounting for policy contained in features, prompts and fallback. Changes in workload or target configuration should test adaptation effort rather than merely rerunning the same examples.

The current evidence supports this boundary as an executable research vehicle. It does not yet support calling escalation an effective compiler policy or claiming less maintenance than conventional selection.
