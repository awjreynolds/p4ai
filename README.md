# p4ai

A **harness compiler** that experiments with replacing handwritten compiler selection policy with a small mechanical selector, Typesafe.ai's Jev, and selective stronger-model advice. Compiler mechanisms and acceptance checks remain authoritative.

The first executable target is **native ARM64 on macOS**, using LLVM's existing register-allocation eviction advisor. Registers are the constrained resource. LLVM offers eligible candidate positions; an external policy selects one. A small C++ patch checks the response before indexing the candidate array. Python hosts the decision protocol and experiments.

The mechanical baseline picks the first eligible candidate. LLVM's normal Greedy allocator remains a separate production reference. Candidate filtering, hints, cost limits, splitting, spilling, and substantial surrounding policy remain in LLVM. This is one replaced decision boundary, not a new allocator or an assertion that all compiler policy has been simplified. See the [boundary inventory](docs/selector-boundary.md).

GPU access is permanently unavailable for this effort. NVIDIA scheduling was investigated, but GPU-free compilation cannot establish runtime correctness or performance. That experiment was retired before validation; eBPF and CPU scheduling remain alternatives. Tofino is excluded as the destination.

## Build and validate

Requires macOS ARM64, Xcode command-line tools, CMake, Git, and uv. The bootstrap downloads pinned LLVM source and builds a native compiler locally; it does not install system tools or require a GPU.

```sh
uv sync --locked
uv run python scripts/build_compiler.py setup
uv run python scripts/build_compiler.py build
uv run python scripts/validate.py --linker /usr/bin/clang --artifacts-dir artifacts/native-validation
uv run python -m pytest -q
```

LLVM is pinned to `32d702efa29d0fdd02cff7b79dea8fdbbcf059d4` in IREE's public LLVM fork. Existing research checkouts are reused when present; a fresh bootstrap creates `.cache/llvm-project`. Validation auto-detects either checkout; `--llvm-source` overrides that choice.

The validation compares stock, first-eligible, and last-eligible policies, checks native executable outputs against scalar references, verifies exact replay, and submits deliberately invalid actions to the C++ guard. Results and command logs are written under ignored `artifacts/`. [Validation details](docs/validation.md) distinguish real compiler checks from mocked protocol tests.

## Live pilot

The [first results](docs/results/native-pilot.md) establish real native compilation and a live Jev → Codex handoff. They do not establish an optimization or maintenance advantage.

After the mechanical gate passes, run one model intervention at the first ambiguous eviction state:

```sh
uv run python scripts/experiment.py --validation-report artifacts/native-validation/report.json --method cascade
```

The routing threshold of 0.5 is provisional and uncalibrated. This command demonstrates routing; it is not a validated escalation policy. All later decisions use the first-eligible selector.

## What this can establish

Learned compiler policies already exist: this uses [LLVM MLGO](https://llvm.org/docs/MLGO.html), rather than claiming to invent it. The research hypothesis is useful selective escalation and adaptation with less maintained selection policy.

A live model choosing a legal action demonstrates integration only. It does not establish better code, useful routing, generalisation, or reduced maintenance. Those require held-out workloads, fair comparisons, repeated measurements and accounting for policy moved into features, prompts and fallback. A small mechanical policy matching the relevant outcomes is a valid negative result for additional inference; eventual exhaustive solvability alone is not.

Jev credentials are loaded from `TYPESAFE_API_KEY` or `~/.config/typesafe/api-key`; Codex uses its existing login. Credentials and generated artifacts are not committed. There is no token cap; model identity, latency and usage are recorded when available.

## Quality and adaptation experiment

The [frozen screening design](docs/escalation-quality-design.md) tests same-state decision outcomes across separate fitting, calibration and held-out workload families. It compares model advice with stock LLVM, simple selectors and a small learned scorer, then tests adaptation to increased register pressure.

The [completed screening results](docs/results/escalation-quality-v1.md) found no demonstrated escalation advantage: calibration chose never escalate, and the cheap learned selector matched Jev on held-out code outcomes. Maintenance savings remain unproved.

```sh
uv run python -m scripts.quality_experiment \
  --validation-report artifacts/native-validation/report.json \
  --output artifacts/escalation-quality-v1
```

This makes live provider calls and builds native counterfactual executables. Completed responses are reused on resume. The run refuses to resume if its implementation hashes change. Its maintenance measurements are a controlled adaptation proxy; they do not measure long-term engineering effort.

## Finding a boundary with quality headroom

The [completed loop-order experiment](docs/results/contraction-policy-v1.md) found a substantial scheduling opportunity in the harness compiler's structured-IR lowering pass. Confirmed discovery choices reduced runtime by 41.48% versus the fitted constant and 26.73% versus a small stride selector. The earlier [vectorization screen](docs/results/loop-policy-v1.md) failed its gate and made no model calls.

On held-out contraction layouts, Jev beat the constant and nearest-neighbor controls, but its 1.66% advantage over the stride selector was below the noise gate. Always-Codex was 6.54% slower than Jev; both the primary and explicitly exploratory routing analyses selected never escalate. This establishes a useful decision boundary, **not useful escalation or maintenance savings**.

The [contraction design](docs/contraction-policy-design.md) preserves six legal schedules and LLVM downstream optimization. It uses the actual Apple M3 target and native execution; canonical `ijk` is a source-order reference, not LLVM's preferred scheduling policy. Reproduction commands and public evidence are in the result report.

## Compiler-informed decisions

The [compiler-feedback experiment](docs/results/compiler-feedback-v1.md) found a useful middle tier on 12 fresh held-out contraction settings: compiler-informed Jev reduced runtime by **17.56% versus stride** and **12.06% versus a cheap ridge predictor**, beyond the declared dispersion thresholds. Adding compiler evidence improved Jev by **34.86% versus source-only input**. The mechanical stride rule stayed unchanged.

The full cascade still failed. Its agreement shortcut made one costly mistake, and calibration's predicted-regret signal never triggered Codex. Always-Codex was 56.10% slower than informed Jev. The current informed policy also compiles every candidate to extract features; native runtime gains do not establish end-to-end cost or maintenance savings. The [design](docs/compiler-feedback-design.md) and [public evidence](docs/results/compiler-feedback-v1.json) preserve both the positive information result and negative three-tier result.

The [fresh replication](docs/results/feedback-replication-v1.md) retains an aggregate Jev advantage: about 23% less runtime than stride and 8.5% less than a fixed random forest in both timing blocks. It nevertheless **fails the frozen success gate**, improving only three of six structural groups against forest and ridge where four were required. The wide group is about 13.3% worse. All cases, provider failures and a cache-only report repair are preserved; no third-tier or maintenance benefit is claimed.

The [open decision-policy review](docs/research/open-decision-policy-options.md) recommends testing automatic policy repair against a direct compiler heuristic change. Open typed models are potential implementations, but moving handwritten rules into another engine does not establish simpler maintenance. A small learned scorer can live inside the compiler; a separate model service is not required by the architecture.

The [System One architecture deep dive](docs/research/system-one-domain-policies.md) separates model specialization, deployment and MCP integration. It recommends testing versioned policy updates against direct heuristic repairs, preserving reproducible builds and using general advisers where their measured value justifies them. It also audits the small number of independent workload recipes behind the current results.

The [application feasibility gate](docs/results/sqlite-feasibility-v1.md) passes historical SQLite's reference workload on both compilers but rejects that source for optimization evidence after a UBSan finding. A separate toy check confirms LLVM's interactive inliner through the exact pinned `opt` tool and Apple Clang. These are technical prerequisites for later application-level repair, not a demonstrated repair policy. The subsequent [current SQLite qualification](docs/results/current-sqlite-feasibility-v1.md) passes both compiler baselines and Apple ASan/UBSan against the unchanged workload/reference, clearing that application prerequisite without an optimization claim.

## Decision map

Follow [Choose a constrained compilation target and a reproducible first experiment](https://github.com/awjreynolds/p4ai/issues/1). The reopened decision is [Choose the first target and compiler intervention](https://github.com/awjreynolds/p4ai/issues/5), followed by [Agree the first experiment and evidence required for success](https://github.com/awjreynolds/p4ai/issues/6). See the [glossary](GLOSSARY.md) and [tracker conventions](docs/issue-tracker.md) for collaboration details.

The technical implementation evidence is recorded in [Validate native ARM64 compiler policy and bounded escalation](https://github.com/awjreynolds/p4ai/issues/14). Red-team evidence and superseded target investigations remain indexed in the map.

## License

Project code is MIT. See [LICENSE](LICENSE). LLVM source, its imported log reader and the LLVM patch retain Apache-2.0 WITH LLVM-exception terms; downloaded sources retain their upstream license files. Model services have separate terms. No model weights are distributed.
