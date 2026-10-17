# Red-team review: novelty and competitive baseline

Checked: **17 October 2026**. Scope: proposed NVIDIA GPU schedule policy in an MLIR compiler; no implementation or measurements exist in this project. This is a selective primary-source review, not proof of novelty. Assessment for [Challenge the novelty and competitive baseline of adaptive compiler policy](https://github.com/awjreynolds/p4ai/issues/10).

**Verdict: NARROW, then proceed through evidence gates.** Reject the broad novelty claim that compiler policy can be separated from deterministic machinery and replaced by learned decisions. Retain the engineering hypothesis: a bounded policy with selective expensive reasoning can lower the total cost of adapting a real scheduling boundary while preserving production quality. A useful implementation need not invent its ingredients.

## What is already established

| Prior work | Established overlap and remaining distinction |
|---|---|
| [LLVM MLGO](https://llvm.org/docs/MLGO.html) | Explicitly separates correctness-preserving decisions from optimization decisions; exposes features and decisions through model-runner interfaces; supports learned inlining and register-allocation eviction. Cheap learned policy replacing handwritten heuristics is established. GPU schedule construction and adaptation from new target guidance are different evaluation problems, not automatically architectural novelty. |
| [MLIR Transform dialect](https://mlir.llvm.org/docs/Dialects/Transform/) | Separates transformation control IR from payload IR, with extensible operations and explicit failure handling. This is a plausible mechanism layer already available. Its verifier is not semantic equivalence proof: documentation says correctness must not depend on `transform.verify`, and some sequences lack rollback. |
| [Halide Adams2019](https://halide-lang.org/papers/halide_autoscheduler_2019.pdf) and [Anderson2021 GPU autoscheduling](https://cseweb.ucsd.edu/~tzli/gpu_autoscheduler.pdf) | Learned cost models combine symbolic analysis and search. The GPU work combines architecture-aware features with a lightweight neural network, freezes cheaper stages, memoizes partial schedules, and offers one-shot versus measured tuning modes. Cheap common-path decisions and amortized search are established; this does not establish language-driven adaptation. |
| [Ansor](https://www.usenix.org/conference/osdi20/presentation/zheng) | Hierarchical program sampling, evolutionary search, learned ranking, and task-level tuning allocation already attack handwritten platform optimization effort, including NVIDIA GPU evaluation. An LLM must beat an effective search policy, not random action selection. |
| [MetaSchedule](https://arxiv.org/abs/2205.13603v2) | Domain experts compose program transformations and stochastic choices through probabilistic programs; learned search chooses within the resulting space. It directly challenges claims about extensible action spaces and modular target policy. Handwritten search-space construction remains, so reducing that maintenance burden could still matter. |

A [2025 LLVM developer talk](https://llvm.swoogo.com/2025devmtg/session/3366592/automating-the-search-aspects-of-compiler-engineering-ir-for-auto-tuning-beyond) describes nondeterministic choice operations in Transform IR: a solver chooses schedules, executes them, and receives performance feedback. Thus even “solver over an explicit MLIR schedule” has close prior art. The talk is architectural evidence, not proof of production maturity.

## What recent LLM work does—and does not—establish

[Meta LLM Compiler](https://arxiv.org/abs/2407.02524) trains models for compiler representations and optimization tasks, including code-size optimization. It establishes LLM compiler-policy precedent; code-size results do not establish NVIDIA scheduling quality or cheap online inference.

[Ramstad et al., SC25 poster](https://sc25.supercomputing.org/proceedings/posters/poster_files/post154s2-file3.pdf) generates MLIR Transform schedules from kernels, compact hardware specifications, objectives, and operation examples. This is especially close to the target-profile proposal. Its FPGA/ASIC example does not establish broad GPU transfer, bounded inference, or lower lifetime maintenance cost. Merely adding an LLM to Transform IR is therefore an inadequate contribution claim.

[FastKernels v2](https://arxiv.org/abs/2605.23215v2), revised 1 October 2026, reports isolated kernel gains shrinking substantially end to end and only 20% of winning kernel sets running correctly as-is. This is evidence against weak production comparisons, not evidence that constrained schedule selection cannot work. Do not repeat the older version's headline as the current result.

## The actual unproved contribution

The candidate contribution is **lower adaptation effort at equivalent delivered quality and bounded recurring cost**. It needs evidence that new target facts or practices change useful decisions without equivalent manual policy engineering, and that validated expensive decisions improve future cheap decisions beyond exact cache hits.

A tiny mechanical selector can conceal considerable policy in action enumeration, feature extraction, prompts, examples, legal-action filters, search budgets, and fallback rules. Count those artifacts and their maintenance. “Target facts” also need separation from performance advice: a capacity limit is different from a heuristic such as preferring a particular tile. Updating the latter may simply relocate handwritten policy into prose.

An LLM cannot choose an unavailable action. New hardware instructions or synchronization mechanisms still require implementation and correctness work. Hold mechanism changes constant when measuring policy adaptation. Typesafe Jev is an optional component, not a contribution or a presumed advantage.

## Strongest cheap comparator

Use the **same action space, observations, legality checks, measurements, and reuse store**, with a compact learned ranker or cost model, bounded beam/evolutionary search, and a deterministic fallback. Train it on the same validated outcomes. Compare its quality–cost frontier against the hybrid; cheaper inference alone is insufficient if extra measurements dominate cost. This is a Halide/MetaSchedule-style comparator, not a claim that one stock implementation is universally strongest.

Also retain the unchanged production compiler and a simple analytic/table policy. Keep the proposal's mechanical selector tiny, but do not artificially weaken external competitors. Include exact-cache and nearest-neighbour reuse controls, plus cheap-policy-only and expensive-policy-only ablations. An offline measured oracle diagnoses whether failures arise from the action space or selection.

## Evidence gates and falsifiers

1. **Boundary gate:** identify an actual internal schedule-construction decision and a representative corpus. Show the permitted actions cover competitive schedules. Otherwise narrow the claim to configuration selection or reject that boundary.
2. **Quality gate:** preregister correctness, numerical tolerances, coverage, end-to-end performance, tail regressions, and compile-latency limits against the unchanged production reference. Include compile and validation failures in results. Passing IR verification alone fails this gate.
3. **Adaptation gate:** freeze selectors; introduce held-out GPU generations, workload families, or documented best-practice changes. Give competitors equivalent information and adaptation budgets. Record engineer hours, changed policy artifacts, training, inference, compilation, and GPU measurements. Falsifier: comparable manual effort or hidden per-case prompt repair.
4. **Escalation gate:** test whether escalation captures expensive-to-miss decisions better than uncertainty-triggered non-LLM search at matched total cost. Falsifier: gains vanish with matched budgets, or fallback supplies almost every good result.
5. **Reuse gate:** distinguish exact replay from generalization on unseen programs; version compiler, target, and validation dependencies. Falsifier: escalation stays frequent, stale reuse causes regressions, or cheap retraining/cache-only controls recover the same benefit.

Proceed if the adaptation and reuse gates show practical value. Remove the LLM if cheaper policy matches outcomes. Reject the broad policy-replacement thesis if production quality requires recreating the original heuristic everywhere else; retain any useful scheduling infrastructure honestly as engineering.
