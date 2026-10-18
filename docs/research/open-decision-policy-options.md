# Open decision policies and compiler maintenance

Reviewed 18 October 2026. This is a source review and architectural recommendation, not a benchmark of these projects. No model was installed or run.

The subsequent [System One architecture deep dive](system-one-domain-policies.md) develops the distinction between general priors and domain learning, MCP integration, reproducible builds and the evidence needed to claim easier maintenance.

The user's question is whether a domain-specific Jev-like engine would simplify compiler updates, or merely move compiler heuristics into another rules system. The recommendation is to preserve a small compiler-owned decision boundary and first test a cheap policy trained on measured compiler outcomes. A larger open decision model is a candidate only if it supplies useful generalization or adaptation that this baseline lacks.

## Separate constraints from selection

| Component | Responsibility | Maintenance consequence |
| --- | --- | --- |
| Compiler legality and resource checks | Define eligible transformations and enforce correctness and hard limits | Keep these authoritative in the compiler; duplicating them in prompts or an external rules engine creates drift |
| Handwritten optimization heuristic | Choose among legal alternatives | A small durable heuristic may be the simplest solution; moving it into a DSL does not remove it |
| Domain-trained numeric policy | Rank alternatives from compiler features and outcome labels | Can change through data and retraining, but still requires feature compatibility, corpus upkeep and validation |
| Typed language-model adviser | Interpret context or propose bounded choices and policy repairs | Adds a model interface, variability and inference cost; type-safe output does not establish a correct or fast choice |

LLVM's [MLGO documentation](https://www.llvm.org/docs/MLGO.html) and [training framework](https://github.com/google/ml-compiler-opt) are the relevant prior art. They replace particular compiler heuristics with learned policies, including inlining for size and register-allocation eviction for performance. This project must demonstrate additional decision quality or adaptation value rather than claim that a learned compiler policy is itself new.

## Open implementations

These are independent alternatives, not published weights for TypeSafe's Jev. TypeSafe's inspected [official organization](https://github.com/typesafe-ai) publishes SDKs, examples and an [LLM-backed adapter](https://github.com/typesafe-ai/system-one-adapter-python); those alone do not provide an open Jev model.

| Candidate | What is open and what it does | Implication for this project |
| --- | --- | --- |
| [Laya](https://github.com/NandhaKishorM/laya) | Apache-2.0 code and [open checkpoints](https://huggingface.co/convaiinnovations/laya), with typed decision heads and public 322M–421M variants | Most plausible small open model to investigate if a language-based policy proves necessary. It is not a trained compiler policy. An [independent MLX port](https://github.com/mizorewww/laya-mlx) documents Apple Silicon support; its M3 Max speed figures are author-reported Apple-GPU measurements, not measured CPU performance in this project |
| [AnyJev](https://github.com/nokia-applied-research/AnyJev) | A training-free decision readout over a separately licensed causal LLM, plus the Tacit self-distilled model family | A typed interface does not remove the backing model or its inference costs. Option-bias correction can require multiple rotations. No validated M3 deployment was established in this review; check code and selected model licensing separately |
| [NeoHorse-Jev-4B](https://huggingface.co/TokenRhythm/NeoHorse-Jev-4B) | Apache-2.0 open weights and [inference code](https://github.com/TokenRhythm/NeoHorse/blob/main/jev/README.md) for typed decisions | Source includes CPU/MPS hooks, but the [documented deployment](https://github.com/TokenRhythm/NeoHorse/blob/main/jev/DEPLOYMENT.md) uses Linux/CUDA. This review found no validated M3 result or turnkey compiler-task fine-tuning recipe |

[JevLang](https://github.com/TimMikeladze/JevLang) is a different category: its [documented policy engine](https://jevlang.sh/) applies ordered handwritten rules and confidence gates. It can make policy explicit and traceable. It does not show that the compiler-specific policy becomes smaller or easier to update.

For any candidate, evaluate compiler outcome quality, out-of-distribution behavior, option sensitivity, cost and calibration on held-out compiler cases. General classification accuracy, an API-compatible response and a confidence number do not answer those questions. Training should ultimately target measured outcomes, not merely imitate Jev's choices.

## Compare with changing the compiler directly

A small direct heuristic patch is a serious comparator. A separate policy is worthwhile only when its reuse, adaptation or quality pays for its extra machinery. Keep the policy callable through the same boundary whether implemented in compiler code, a small fitted model or a service.

The smallest useful subsequent comparison is a separately frozen compiler-shift trial:

1. Hold source semantics, legal actions and correctness checks fixed. Audit whether a compiler change causes reproducible policy regret relative to that compiler's own best measured action. Stop if there is no meaningful degradation or repair opportunity.
2. Compare the unchanged heuristic, a direct heuristic repair, and automatic learned-policy repair. Give repairs the same adaptation measurements, compiler evidence and declared build/elapsed-time budget. Prohibit case-identity exceptions.
3. Freeze repairs before fresh held-out evaluation. Include all attempts and failures. Only add stronger-model repair if ordinary retraining leaves validated headroom; compare it against retraining directly from the same measured labels.
4. Report correctness, generated-code runtime, compilation and inference costs, labels, dependencies and bespoke changes. A successful automated update supports an automatic-adaptation claim. Human maintenance savings require repeated independent update tasks and recorded practitioner effort.

An offline adviser that helps produce one cheap deployed policy is a stronger present hypothesis than requiring all three tiers during every compilation. This is a recommendation, not a measured result. Our [compiler-feedback experiment](../results/compiler-feedback-v1.md) found a useful informed Jev policy but no useful stronger-tier route. The [fresh replication](../results/feedback-replication-v1.md) retains an 8.5% aggregate advantage over a fixed forest but fails its group-consistency gate. Neither result establishes lower maintenance. The [maintenance ledger](../policy-maintenance-ledger.md) records why prompts, features, data and validation all count as maintained policy.

Do not add another rule language, model dependency or training stack merely to preserve the escalation story. If a small compiler patch or generic learned selector matches the relevant quality and adaptation outcomes, retain it and report the negative result for extra escalation.
