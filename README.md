# p4ai

Exploring an evidence-gated escalation approach to compilation for resource-constrained targets: mechanical methods first, Typesafe.ai's Jev for structured decisions where justified, and a high-reasoning language model for unresolved decisions.

The first goal is to choose a current, actively developing target with plausible career relevance and define a reproducible public experiment, including the compiler boundary, baseline, hard constraints, and success criteria. Current P4 targets, eBPF, and GPU/AI compiler stacks are candidates; the target choice is open. Tofino is excluded as the destination.

The current research preference is NVIDIA-aligned GPU compiler work, reflecting the author's proximity to Bristol and interest in NVIDIA. Triton shared-memory allocation is a provisional candidate; research findings and alternatives are recorded in the decision map. No target has been selected or toolchain validated yet.

This repository is currently in the research and decision-planning stage. It does not yet contain a compiler implementation.

## Decision map

Follow [Choose a constrained compilation target and a reproducible first experiment](https://github.com/awjreynolds/p4ai/issues/1). Research evidence is linked from the map's child issues; target selection remains a human decision. See [tracker conventions](docs/issue-tracker.md) for collaboration details.

## License

MIT. See [LICENSE](LICENSE).
