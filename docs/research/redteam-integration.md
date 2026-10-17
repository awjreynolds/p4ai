# Red-team integration assessment

Checked **17 October 2026** for [Challenge the feasibility of replacing NVIDIA GPU scheduling policy](https://github.com/awjreynolds/p4ai/issues/11). Source inspection only; no compiler build, GPU run, or model experiment. IREE was pinned using the live GitHub commit API to [`89ece87b89852100c0a4ac3d515e14842d090967`](https://github.com/iree-org/iree/commit/89ece87b89852100c0a4ac3d515e14842d090967), dated 15 October. Cached web versions of `main` differed from these downloaded files; the pinned source governs this assessment.

**Verdict: NARROW, with an unresolved integration gate.** There is a real NVIDIA compiler scheduling decision to investigate. There is not yet evidence for replacing the scheduling policy wholesale while retaining a complete independent legality oracle. Reject “MLIR Transform dialect is the foundation” as an established integration conclusion. The safest existing ranking seam is too small for the proposed NVIDIA experiment.

## A concrete NVIDIA path

Use static, ordinary FP16-input/FP32-output matmul on **Ampere `sm_80`**, subject to access to matching hardware. This is grounded in an actual upstream test: [`config_tile_and_fuse_sm80.mlir`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/LLVMGPU/test/NVVM/config_tile_and_fuse_sm80.mlir#L1) runs the lowering selector for `sm_80` and checks `TileAndFuse`, `NV_MMA_SYNC_F32_16x8x16_F16`, workgroup size 128 and concrete tiling. This establishes intended compiler configuration behavior, not measured GPU correctness or performance.

The source call chain is:

`LLVMGPUSelectLoweringStrategyPass::runOnOperation` → `initGPULaunchConfig` → `setRootConfig` → `IREE::GPU::setMatmulLoweringConfig` → `getMatmulOrIGEMMLoweringConfigAndWorkgroupSize` → `getMmaScheduleFromProblemAndTarget` → `deduceMMASchedule`.

The selector and launch/root handling are in [`LLVMGPUSelectLoweringStrategy.cpp`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/LLVMGPU/LLVMGPUSelectLoweringStrategy.cpp#L81) and [`KernelConfig.cpp`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/LLVMGPU/KernelConfig.cpp#L2398); matmul configuration lives in [`TargetUtils/ConfigUtils.cpp`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/Dialect/GPU/TargetUtils/ConfigUtils.cpp#L1275).

## Where replacement becomes misleading

[`deduceMMASchedule`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/Common/GPU/GPUHeuristics.cpp#L959) combines intrinsic ordering, compatibility filtering, architecture-adjusted seeds, schedule construction, batch tiling, alignment/distributability checks, shared-memory estimation and iterative shrinking. Deleting this function deletes constraints alongside preferences.

Its narrow ordering hook, `sortMMAIntrinsics`/`compareIntrinsics`, is separable from later checks. However, it is a poor NVIDIA research target: [`getAmpereWgpDetails`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/Dialect/GPU/TargetUtils/KnownTargets.cpp#L1071) exposes MMA_SYNC and WMMA variants, but [`MMAAttr::getDistributionMappingKind`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/Dialect/GPU/IR/IREEGPUAttrs.cpp#L1128) excludes NVIDIA WMMA from explicit distribution. Configuration filters those intrinsics out. For the proposed fixed FP16→FP32 case, one matching MMA_SYNC remains. Reordering a singleton cannot improve selection.

Even where multiple intrinsics exist, this hook replaces only their preference order. `getOptimalMMASchedule`, seed selection/adjustment, batch decisions and `fitScheduleInSharedMemory` still choose the schedule. A “learned scheduler replaces the policy” claim would be false.

## Conditional wider boundary

The defensible **proposed**, not existing plug-in, boundary is inside `deduceMMASchedule`: hold the compatible MMA intrinsic, target, operation class and pipeline fixed; replace construction and preference among bounded single-dimension tile/subgroup tuples. An action could be a candidate ID naming `(mTileCount, nTileCount, kTileCount, mSubgroupCount, nSubgroupCount)`, materialized as `GPUMMASchedule`. Intrinsic shapes/types and batch sizes remain fixed. Do not expose arbitrary MLIR or free-form lowering attributes.

Extract and retain `canTargetIntrinsic`, [`isValidMMASchedule`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/Common/GPU/GPUHeuristics.cpp#L199), and the shared-memory predicate. Add explicit tuple bounds and positivity before arithmetic. `fitScheduleInSharedMemory` is itself a preference policy—ordered shrinking—not merely a legality check. Either disclose its continued influence or reject candidates without silently repairing them. Existing schedule generation may supply a fallback/baseline, but retaining it as the sole candidate generator leaves most target knowledge and choices handwritten.

Substantial downstream coupling remains: prefetch stages, transposition, padding, accumulator promotion, operand promotion and workgroup size affect validity and resource estimates. [`ConfigUtils.cpp`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/Dialect/GPU/TargetUtils/ConfigUtils.cpp#L915) also retains direct-load rejection, occupancy decisions and NVIDIA accumulator conversion. This would replace a tile/subgroup selector, not all GPU scheduling or cost models.

**There is no demonstrated complete validator.** The selection pass only dispatches its special configuration verifier for `VectorDistribute`; [`Verifiers.cpp`](https://github.com/iree-org/iree/blob/89ece87b89852100c0a4ac3d515e14842d090967/compiler/src/iree/compiler/Codegen/LLVMGPU/Verifiers.cpp#L15) checks limited matmul tile-axis properties. These checks do not certify the proposed `TileAndFuse` action space. Full lowering, numerical checks and hardware execution remain necessary; passing them on samples is not a universal proof.

## Transform dialect and alternatives

[IREE tuning specs](https://iree.dev/reference/tuning/) genuinely use Transform dialect to match dispatches and attach compilation information. That is useful for replaying configurations. It does not expose this C++ decision as a policy interface or supply NVIDIA legality. `initGPULaunchConfig` skips automatic selection when translation information already exists: external spec injection bypasses the selector; it does not remove its implementation. AMD tuner examples are not NVIDIA integration evidence.

[Triton `Autotuner.prune_configs`](https://github.com/triton-lang/triton/blob/2122e24efc1886bfa36337c1f5b07c47d827b1d4/python/triton/runtime/autotuner.py#L273) is an explicit alternative with candidate configs, an optional performance model and top-k pruning. It is a tuning wrapper; adopting it changes the research claim. It offers a useful ranker baseline, not evidence of internal compiler-policy replacement. IREE's SIMT `setContractConfig` is another narrower selector, but its fallback status and relevance must be demonstrated on the intended workload before adopting it.

## Smallest decisive spike and falsifiers

1. On the pinned revision, reproduce the existing `sm_80` test unchanged, then instrument its path: confirm the hook is reached and count distinct compatible schedules. Bypass the selector with its recorded original configuration and verify that downstream lowering is unchanged before trying alternatives.
2. Separate bounded tuple enumeration from retained constraints; force two distinct accepted choices through unchanged lowering. Add invalid-action cases proving deterministic rejection/fallback. Record which handwritten preferences remain.
3. Compile and run both on the selected NVIDIA GPU, compare outputs to an independent reference, and measure repeated timings against the unmodified compiler plus a small exhaustive-search baseline.

Stop or narrow again if there is only one effective action, changing choices requires disabling checks, retained heuristics determine nearly everything, malformed actions reach unchecked arithmetic, or correct alternatives lack useful performance variation. If transparent separation cannot be established, the source gate fails closed. No learned-model or strong-model escalation experiment is justified before this spike passes. No source inspection can establish model advantage, compile-time cost, runtime speedup, or numerical acceptability on unavailable hardware.
