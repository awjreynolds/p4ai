# p4ai

Exploring an evidence-gated escalation approach to compilation for resource-constrained targets: mechanical methods first, Typesafe.ai's Jev for structured decisions where justified, and a high-reasoning language model for unresolved decisions.

The goal is to test whether bounded decision escalation can replace growing handwritten compiler selection policy and adapt to changing target profiles and best-known practices. Mechanical transformations and acceptance checks remain authoritative. The earlier speed-first eBPF pilot is under reconsideration: the current priority is the best compiler architecture for genuine policy replacement.

The mechanical selector must stay deliberately small. The existing compiler's normal behaviour remains an unchanged reference; selecting configurations around it is a narrower claim than replacing an internal heuristic. The exact first decision boundary is being specified. There is no token cap; usage and effort will be recorded. A simple policy matching the relevant outcomes is an early-stop result, while eventual mechanical solvability alone is not.

NVIDIA GPU scheduling remains a candidate, reflecting the author's proximity to Bristol and interest in NVIDIA. Independent red-team reviews recommend narrowing and validating it: learned compiler policies are established prior art, the reviewed IREE path mixes selection with legality, and the simplest inspected ranking hook offers no meaningful choice. A broader tile/subgroup selector needs a bounded integration test. Tofino is excluded as the destination. No pilot implementation or toolchain validation has been performed yet.

This repository is currently in the research and decision-planning stage. It does not yet contain a compiler implementation.

## Decision map

Follow [Choose a constrained compilation target and a reproducible first experiment](https://github.com/awjreynolds/p4ai/issues/1). The reopened decision is [Choose the first target and compiler intervention](https://github.com/awjreynolds/p4ai/issues/5), followed by [Agree the first experiment and evidence required for success](https://github.com/awjreynolds/p4ai/issues/6). See the [glossary](GLOSSARY.md) and [tracker conventions](docs/issue-tracker.md) for collaboration details.

The next prerequisite is [Validate a bounded NVIDIA scheduling decision before testing models](https://github.com/awjreynolds/p4ai/issues/13). Red-team evidence is indexed in the map; closing the research reviews does not mean the architecture has passed empirical validation.

## License

MIT. See [LICENSE](LICENSE).
