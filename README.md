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

## Decision map

Follow [Choose a constrained compilation target and a reproducible first experiment](https://github.com/awjreynolds/p4ai/issues/1). The reopened decision is [Choose the first target and compiler intervention](https://github.com/awjreynolds/p4ai/issues/5), followed by [Agree the first experiment and evidence required for success](https://github.com/awjreynolds/p4ai/issues/6). See the [glossary](GLOSSARY.md) and [tracker conventions](docs/issue-tracker.md) for collaboration details.

The technical implementation evidence is recorded in [Validate native ARM64 compiler policy and bounded escalation](https://github.com/awjreynolds/p4ai/issues/14). Red-team evidence and superseded target investigations remain indexed in the map.

## License

Project code is MIT. See [LICENSE](LICENSE). LLVM source, its imported log reader and the LLVM patch retain Apache-2.0 WITH LLVM-exception terms; downloaded sources retain their upstream license files. Model services have separate terms. No model weights are distributed.
