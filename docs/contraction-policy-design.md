# Choosing a lowering schedule for a tensor contraction

This is a separately frozen experiment after [loop vectorization failed its opportunity gate](results/loop-policy-v1.md). The harness compiler receives a structured description of a dense unsigned contraction: dimensions and the physical strides of A, B and C. Its lowering pass selects one of six loop orders, emits C, and uses the pinned LLVM compiler to produce native M3 code. LLVM's downstream optimizations remain enabled. This is a real scheduling choice in the harness compiler, not a replacement of an existing LLVM scheduling callback.

The semantic contract is deliberately narrow: initialized output, disjoint buffers, and multiplication and accumulation modulo 2^32. Under those conditions every permutation of the i, j and k loops produces the same result. The generator admits only those six schedules. Separate reference code computes outputs using independent indexing and traversal, with edge and random inputs. Every candidate must pass native reference and cross-action checksum checks. Object identity detects distinct source schedules that LLVM compiles to identical code; those share timing observations.

The action called `stock` in the reused measurement machinery is canonical `ijk` source order. It is not an LLVM scheduling oracle. A separately compiled `ijk` action must have identical code identity. Keep that distinction when interpreting tables.

## Simple controls

The fixed selector returns the best constant action fitted on discovery data. A second selector uses only physical strides:

```python
cost(axis) = stride_A(axis) + stride_B(axis) + 2 * stride_C(axis)
choice = min(orders, key=lambda p: (cost(p.inner), cost(p.middle), p.id))
```

Strides are bytes; absent axes have stride zero. The factor two counts the read and write of C. This is a small locality heuristic, not a calibrated target cost model. It was fixed before timing. A discovery-fitted nearest-neighbor predictor is the additional cheap learned control. Models must improve on these controls; success cannot come from an intentionally weak fixed loop order.

## Frozen evidence gates

The [configuration](../experiments/contraction-policy-v1.json), frozen in commit `0f38ef3`, defines all eight row/column layout groups, three shapes, splits, actions, thresholds and timing rules. The layout groups were shuffled using the existing experiment seed before measurements. All shapes of a layout stay in its assigned partition. These are held-out layouts of **one operation**, not independent application families.

Discovery has two independent 11-round blocks. Select a fixed action and per-case candidate-best actions in the first block and freeze them before confirmation. The second block must show at least 5% aggregate improvement over both the fitted constant and stride control, beyond the declared paired-dispersion bound, with gains in at least two discovery groups. Otherwise the experiment stops with zero model calls.

If discovery passes, freeze Jev, Codex and nearest-neighbor decisions for calibration and test cases before measuring those cases. Calibrate one routing rule using calibration outcomes only, retaining `never` as a candidate. Held-out cascade success requires gains beyond the same noise bound over the fixed choice, stride heuristic, nearest-neighbor and Jev, positive direction in at least two held-out groups for every comparison, and at most 25% escalation on both calibration and test sets.

Every distinct executable receives a 32-iteration timing pilot. Shared iterations target 100 ms for the fastest pilot, preventing fast schedules from being measured only for a few microseconds. Cache this count across blocks; abort rather than selectively exclude a case if a predicted sample exceeds 20 seconds. Pilots for calibration and held-out cases occur after blind decisions. Timings are interleaved, and builds and model work occur outside timing blocks.

Dimensions and strides are derived directly from the structured input rather than maintained per-family feature rules. Count the stride expression, fixed action, learned examples, routing parameter, action menu and prompt content as maintained policy. Native correctness, a faster schedule or a shorter selector still does not establish reduced engineering maintenance effort, whole-application benefit or cross-target generalization.
