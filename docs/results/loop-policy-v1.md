# Loop vectorization: insufficient confirmed headroom

The loop-local VF/IC experiment stopped before model calls. The first block chose stock LLVM as the best fixed action. The per-case choices selected in that block reduced runtime by only **1.50%** in the independent confirmation block, below the frozen 5% opportunity requirement. Jev and Codex were not called.

All **192 compiled variants** (24 cases × eight actions) passed native scalar-reference and cross-action checksum checks. All nine discovery cases had distinct generated code. The failure was insufficient quality headroom, not a nonfunctional decision interface.

| Discovery layout-free kernel family | Confirmed runtime reduction versus fitted constant |
| --- | ---: |
| Affine | 1.50% |
| Reduction | 2.35% |
| 64-bit hash | 0.64% |
| Equally weighted geometric aggregate | **1.50%** |

The conservative noise gate also failed: the maximum within-case paired log-ratio MAD was 0.09423, dominated by affine length 4099, giving an `expm1(3 × MAD)` bound of 32.67%. This is a descriptive dispersion bound, not a confidence interval. The aggregate would still fail the independent 5% effect-size requirement even if that noisy case did not determine the bound. We did not rerun or remove cases to change the outcome.

The design was recorded and reviewed before timing, ending with three fixed trip counts (257, 1027, 4099), family-disjoint discovery/calibration/test partitions, and two independent 11-round discovery blocks. Only discovery performance was measured; calibration and test binaries were built and correctness-checked. The measured implementation is commit `fa7eb13`. The host is an Apple M3 Max; all experimental kernels used the pinned LLVM and explicit M3 target/tuning flags. LLVM canonicalizes M3 to `apple-a16` in the target CPU attribute, retaining M3 tuning. The system Clang built shared unvectorized reference drivers.

The mechanical selector remains one fitted action. Selecting stock delegates to LLVM's existing policy; this experiment neither reduced LLVM's maintained policy nor demonstrated escalation value. Its source features were also manually specified for the synthetic families. See [policy inventory and design](../loop-policy-design.md), [frozen configuration](../../experiments/loop-policy-v1.json), and [public evidence with raw rounds and build identities](loop-policy-v1.json).

The next separately frozen candidate is loop-order scheduling in the harness compiler's structured-IR lowering pass. It must beat both a fixed choice and a predefined stride heuristic before any model calls. This is a new boundary, not a relaxed gate on the failed vectorization experiment.
