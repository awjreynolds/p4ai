# p4ai

Exploring an evidence-gated escalation approach to compilation for resource-constrained targets: mechanical methods first, Typesafe.ai's Jev for structured decisions where justified, and a high-reasoning language model for unresolved decisions.

The goal is to test whether bounded decision escalation can replace growing handwritten compiler selection policy and adapt to changing target profiles and best-known practices. Mechanical transformations and acceptance checks remain authoritative. The earlier speed-first eBPF pilot is under reconsideration: the current priority is the best compiler architecture for genuine policy replacement.

The mechanical selector must stay deliberately small. The existing compiler's normal behaviour remains an unchanged reference; selecting configurations around it is a narrower claim than replacing an internal heuristic. The exact first decision boundary is being specified. There is no token cap; usage and effort will be recorded. A simple policy matching the relevant outcomes is an early-stop result, while eventual mechanical solvability alone is not.

The current recommendation is NVIDIA GPU scheduling at a real selection boundary in an appropriate MLIR-based compiler, reflecting the author's proximity to Bristol and interest in NVIDIA. This is not yet a final target choice. Tofino is excluded as the destination. No pilot implementation or toolchain validation has been performed yet.

This repository is currently in the research and decision-planning stage. It does not yet contain a compiler implementation.

## Decision map

Follow [Choose a constrained compilation target and a reproducible first experiment](https://github.com/awjreynolds/p4ai/issues/1). The reopened decision is [Choose the first target and compiler intervention](https://github.com/awjreynolds/p4ai/issues/5), followed by [Agree the first experiment and evidence required for success](https://github.com/awjreynolds/p4ai/issues/6). See the [glossary](GLOSSARY.md) and [tracker conventions](docs/issue-tracker.md) for collaboration details.

## License

MIT. See [LICENSE](LICENSE).
