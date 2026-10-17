# Transferable harness contract and TypeSafe.ai middle-tier fit

Research date: 17 October 2026. Decision context: [Establish the transferable harness contract and Typesafe.ai middle-tier fit](https://github.com/awjreynolds/p4ai/issues/2). Scope: a reproducible public demonstration of authentic compilation constraints, without requiring hardware. This is source research and a proposed experiment, not evidence of a working integration.

## Recommendation

Retain mechanical → Jev → high-reasoning LLM as an **experimental search policy**, with mechanical validation surrounding every tier. Jev is a plausible inexpensive selector or escalation router when the compiler already supplies meaningful alternatives. It must earn its place against deterministic heuristics and a two-tier mechanical → LLM baseline. It should never certify numerical resource feasibility or program equivalence.

The important transfer from the transport compiler is the authority boundary: models advise; deterministic code defines legal actions, applies transformations and accepts results. “Resource restricted” must distinguish target resources, such as stages or memory, from search resources, such as compiler invocations, time and inference expenditure. Both should be measured independently.

## What the transport repository establishes

The inspected repository is `awjreynolds/agentic-satn-compiler`, HEAD `6c1be4e02738bfc1cea364ff0a2b1508f492abe2`. Existing uncommitted edits were left untouched. The unchanged [compilation gate](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/src/satn/agents.py#L492) first applies configured deterministic policy. Where review is required, it builds a finite menu from governed evidence and findings. Responses select an offered action; they cannot override mandatory Red gates.

The [stop/validate/restart protocol](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/docs/compiler-architecture.md#L320) binds requests and decisions to dependency fingerprints. Fresh compilation regenerates requests before replaying accepted choices. Malformed or stale responses stop at an explicit unresolved state. Publication is a complete validated transaction that preserves the previous valid result on failure.

The richer [alignment contracts](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/src/satn/alignment_selection.py#L4306) distinguish a primary decision agent from an independently recorded critic. Critique, revision, evidence lineage and human intervention are typed. A recorded invocation is explicitly not proof of external identity. [Replay tests](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/tests/test_agent_decision_replay.py#L64) and [runtime tests](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/tests/test_direct_runtime_decisions.py#L140) cover stale choices, mandatory-gate rejection and failure boundaries; they were inspected, not rerun here.

This does **not** establish an implemented Jev cascade. No Jev integration was found in the inspected code. The [architecture](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/docs/compiler-architecture.md#L55) identifies published proofs of concept as fake-runtime demonstrations. The [README](https://github.com/awjreynolds/agentic-satn-compiler/blob/6c1be4e02738bfc1cea364ff0a2b1508f492abe2/README.md#L6) says the current publication step is source-baseline mapping that bypasses the compiler. Transfer the implemented contracts without claiming proven live escalation performance.

## What Jev currently provides

TypeSafe describes Jev as a trained System One model, using Reinforcement Learning for Calibrated Decisions and parallel rather than autoregressive output. That makes it probabilistic learned judgment, not a theorem prover. The vendor’s schema guarantee concerns output shape; it is not a guarantee that the selected answer is correct. Its workflow comparisons use strong-model reference probabilities, not compiler ground truth. [Introducing System One Models and Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)

Jev consumes state plus typed questions. Choice selects an offered option; Score evaluates an ordered rubric; Noul returns a probability for a yes/no question. Questions share state but are evaluated independently; the application combines their results. TypeSafe recommends narrow atomic questions over extended reasoning. [Introduction](https://docs.typesafe.ai/introduction)

Jev does not generate source code, call tools, edit files or substitute for a coding-agent LLM. In this experiment it could classify a diagnostic, rank a transformation family, or choose among already constructed candidates. Execution remains the harness’s responsibility. [Jev with coding agents](https://docs.typesafe.ai/introduction/coding-agents)

Choice confidence is `(p_max - 1/n) / (1 - 1/n)`, where `n` is the number of options. Thus confidence changes with menu size and is not an independent correctness probability. Noul has no separate confidence field. Preserve full distributions and validate routing thresholds on held-out compilation cases; do not transplant illustrative thresholds from documentation. [Confidence](https://docs.typesafe.ai/confidence)

The documented Jev 1.13 weaknesses are directly relevant: unreliable arithmetic/counting, low-level instruction representations, indirection, irrelevant context and option-order sensitivity. Supply precomputed resource facts and concise named transformations rather than asking Jev to calculate fit or interpret large assembly dumps. These are version-specific warnings, not measured defects of every subsequent model. [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

There is a first-party precedent for the cascade: TypeSafe’s extraction cookbook uses a cheap generative model, Jev per-field error judgments, then a high-reasoning model when a signal fires. This establishes an integration pattern, not a compiler result; its model versions and thresholds are example settings. [SDE cascade](https://docs.typesafe.ai/cookbooks/sde_cascade)

## Access, reproducibility and licensing

The documented service is authenticated HTTP: `POST /v1/systemone`, with state, model and questions. Responses include model identity and usage. Choice supports at most 255 options; HTTP errors include authentication, invalid requests, rate limiting and overload. The wrapper must count retries in its resource accounting and produce a typed unavailable outcome rather than silently accepting an answer. [API reference](https://docs.typesafe.ai/api)

On the research date, the models page lists `jev-1.13.0` and `jev-1.14.0`, at $0.042 per million input tokens, with free output. Normal limits are 64k tokens per request and 32k per question. Organization rate limits are visible in the console. `jev-latest` and `jev-preview` move; pin a version and record the returned ID. Experimental long context is unnecessary for a small decision packet. [Models](https://docs.typesafe.ai/models)

An API key comes from the console. No key was requested or used in this research. [Quick start](https://docs.typesafe.ai/introduction/quickstart) The official [Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python) is MIT-licensed, but that does not license model weights or make inference offline. No public downloadable weights or reproducible self-hosted Jev runtime were established from the inspected sources. Treat live inference as an optional external dependency.

The service agreement permits API integration, assigns its interest in outputs to the customer, and separately restricts activities including model imitation/distillation and competing services. It also reserves service ownership and permits updates. An MIT repository does not supersede those terms. Publish harness code and independently licensed fixtures; keep credentials private, and confirm applicable account terms before distributing substantial captured response datasets. [Master Customer Agreement](https://typesafe.ai/legal/mca)

## Proposed target-independent contract

The following is a design inference from the evidence, not an existing shared library:

1. **Problem identity:** source/IR hash, semantics assumptions, toolchain build, target description, resource limits, objective and benchmark identity. Every limit comes from the actual target contract or an explicit experiment budget.
2. **Mechanical observation:** compile/verifier result, structured diagnostics, resource vector, eligible transformations and unresolved uncertainty. Parsing and numerical comparisons remain ordinary code.
3. **Decision request:** immutable observation fingerprint, finite action IDs with preconditions, relevant structural evidence, and explicit abstain/escalate choices. The model cannot amend capacity or semantics.
4. **Decision response:** action ID, full probability distribution where available, provider/model/version, request hash, latency, usage and error/retry provenance. No free-form answer becomes an executable command.
5. **Application and validation:** apply a defined transformation, recompile, check semantic preservation and authentic target limits, then compare the measured objective. Invalid candidates are rejected and recorded, regardless of model confidence.
6. **Termination:** accepted result, verified infeasible within the stated search scope, exhausted budget, or unresolved result. Search failure alone must not become a proof of impossibility.

A high-reasoning LLM may initially choose from the same menu. If later allowed to generate new transformations, that is a separate experimental condition with a larger search space and validation burden.

## Fair evaluation and minimum integration evidence

Use identical public programs, authentic target profiles, compiler versions, validity checks and transformation sets for the primary comparison. Compare deterministic-only search, mechanical → Jev, mechanical → high-reasoning LLM, and the full cascade. Add random or cheap static ranking to reveal whether Jev improves search ordering beyond any selector. Compare both equal wall-clock and equal measured expenditure frontiers; also report compiler evaluations, because inference savings can hide expensive downstream searches.

Choose routing thresholds on development workloads and freeze them before held-out evaluation. Hold out program families or structural variants, not merely filenames. Include already-fitting cases, repairable failures, infeasible cases and misleading candidate descriptions. Measure verified success, resource improvement, regret where an exhaustive small-instance oracle is available, false acceptance, escalation rate, latency distribution and total cost. Report selective error against coverage, with option-order perturbations and repeated live calls. No unsupported universal threshold or sample count is prescribed here.

Minimum evidence before claiming integration is: one public end-to-end workload with a genuine target resource failure; a finite candidate packet; a real recorded Jev response; a validated chosen transformation; an exercised escalation; and mechanical rejection of a deliberately invalid candidate. Add unavailable-provider and stale-response checks. A demonstration of plumbing alone does not establish optimization benefit.

Provide an offline deterministic/replay mode so anyone can inspect and reproduce artifact generation, plus an explicitly marked live mode for evaluating present model behavior. Replayed decisions prove replay, not fresh model reproducibility. Retain raw public-safe packets, response provenance and validation results so a third party can distinguish vendor judgment from mechanically established facts.

The unresolved question is empirical: does Jev predict useful next actions cheaply enough on constrained compilation to outperform a simple heuristic and direct LLM escalation? None of the reviewed primary sources settles it. That is the middle tier’s experiment, not a premise of the project.
