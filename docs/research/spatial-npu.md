# Current spatial NPU candidate: IRON / MLIR-AIE

Research for [Assess a current spatial NPU compiler target](https://github.com/awjreynolds/p4ai/issues/8), checked 17 October 2026. Primary-source investigation only: no installation, compilation, hardware run or equivalence proof was performed. Recommendations below are technical inferences, not measured benefits or hiring claims.

**Recommendation: shortlist now as the spatial-compilation alternative to the GPU paths.** IRON/MLIR-AIE is a closer structural match to Tofino placement expertise: a physical tile array, local memory, DMA channels, locks and routed streams must fit simultaneously. It also has public mechanical placers worth competing against. The remaining gates concern reproducible installation and semantic validation, rather than whether a real public constrained target exists. The user's NVIDIA/Bristol preference is a separate reason to favour the NVIDIA path investigated in [Assess a current NVIDIA GPU compiler target](https://github.com/awjreynolds/p4ai/issues/9).

## Concrete target and current development

Use **AMD Ryzen AI 9 HX 370 (Strix Point), XDNA 2 / AIE2P**, with MLIR-AIE's `npu2` target; choose an explicitly declared supported column partition if the experiment needs a smaller boundary. AMD names the processor and codename, while MLIR-AIE maps Strix, Strix Halo and Krackan to `npu2`: eight columns, four compute rows, one memory-tile row and one shim-DMA row. Thus a partition is a supported physical resource boundary, not an invented machine. Confirm the chosen SKU's usable partition with the eventual runtime. [AMD product](https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-9-hx-370.html), [device models](https://github.com/Xilinx/mlir-aie/blob/main/docs/Devices.md).

Release **1.4.4, dated 6 October 2026**, contains allocation, routing, kernel and runtime changes; its notes explicitly describe continuing API evolution. This establishes active development, while warning that a floating main branch will undermine repetition. The project says AMD leads it and welcomes contributions. [Release](https://github.com/Xilinx/mlir-aie/releases/tag/v1.4.4), [contributing](https://xilinx.github.io/mlir-aie/1.4.4/CONTRIBUTING/).

## Public toolchain and host boundary

IRON provides Python construction of the dataflow design; MLIR-AIE lowers placement/data movement, while **Peano (`Xilinx/llvm-aie`)** compiles individual cores. Both repositories publish Apache-2.0 with LLVM exceptions, with third-party exceptions separately identified. The current installation guide explicitly permits skipping Vitis when targeting AIE2/AIE2P with Peano; the proprietary `xchesscc` path is optional. [Installation](https://xilinx.github.io/mlir-aie/dev/getting-started/), [MLIR-AIE license](https://github.com/Xilinx/mlir-aie/blob/main/LICENSE), [LLVM-AIE license](https://github.com/Xilinx/llvm-aie/blob/aie-public/LICENSE.TXT).

The 1.4.4 assets include CPython 3.11–3.14 wheels for Linux x86-64 (`manylinux_2_35`) and Windows AMD64; native macOS/Apple Silicon wheels were absent from that release listing. A Linux x86-64 CI runner is the conservative public host choice. Match repository, compiler wheel and pinned Peano dependency exactly; record hashes. [Release assets](https://github.com/Xilinx/mlir-aie/releases/expanded_assets/v1.4.4), [dependency setup](https://github.com/Xilinx/mlir-aie).

Explicit `iron.set_current_device(...)` plus `.compile()` supports cross-compilation without the target attached. Separately, upstream documents a hardware-free placer seed sweep, and its compile/test workflow uses hosted Ubuntu 24.04. These establish offline development paths; they do not constitute our own complete artifact-build validation. Actual execution needs compatible XDNA runtime/driver and an NPU. Avoid a JIT call that silently proceeds from compile to execution. [Configuration](https://github.com/Xilinx/mlir-aie/blob/main/programming_guide/iron_configuration.md), [offline checks](https://xilinx.github.io/mlir-aie/1.4.4/programming_guide/section-1/), [CI source](https://github.com/Xilinx/mlir-aie/blob/main/.github/workflows/buildAndTestMulti.yml).

## Authentic resource oracle and its limits

| Layer | Documented checks | Limitation |
| --- | --- | --- |
| Tile placement | Logical-to-physical placement; device model; failure when placement is unavailable | A heuristic's failure is not global infeasibility |
| ObjectFIFO allocation | Buffer capacity, reachable locks and DMA eligibility; pinned resources retained | Bounded retries; pairwise reachability does not establish joint placement feasibility |
| Core memory/linking | Stack/buffer overlap, static-data reservations, bank limits, linked-region capacity | Stack analysis has incomplete cases; overrides can assert unproved requirements |
| Stream routing | Circuit ownership, packet arbiters, routing conflicts and selected dependency/deadlock checks | Conservative assumptions may reject feasible designs; not a whole-program liveness proof |

Placement code propagates failure explicitly. The allocator documents bounded searches, spilling to reachable memory tiles, and failure without undoing placement. The harness should distinguish **resource violation**, **search exhausted**, **unsupported analysis**, and **compiler error**. [Placement source](https://raw.githubusercontent.com/Xilinx/mlir-aie/main/lib/Dialect/AIE/Transforms/AIEPlaceTiles.cpp), [allocation passes](https://xilinx.github.io/mlir-aie/1.4.4/AIEPasses/).

NPU2 compute tiles have 64 kB local data memory shared by stack, buffers and static data. The documentation identifies missing stack information, recursion and indirect calls, and explains that some annotations escape validation. Router assumptions about missing DMA programs or transfer lengths can create conservative conflicts. Keep checks enabled and exclude these incomplete cases initially. Development documentation may exceed release capabilities: verify each selected check at the pinned revision. [Core memory](https://xilinx.github.io/mlir-aie/dev/programming_guide/core_data_memory/), [routing](https://xilinx.github.io/mlir-aie/dev/programming_guide/routing/).

## Experiment, corpus and mechanical baseline

**Proposal:** freeze kernels, tensor shapes, FIFO depths and logical dataflow; allow escalation to propose placement hints or reorder the placement search after deterministic checks fail. Run the existing allocation/routing/linking pipeline as the authority. This limits semantic changes while testing whether diagnostic-guided escalation finds legal mappings at lower total cost.

Compare the default **SequentialPlacer** with **simulated annealing**, fixed seed sweeps and a bounded hint-search portfolio. Upstream already runs seeds 1–20 on a four-core fixture and MobileNet without hardware, and separately executes NPU2 MobileNet hardware checks. Reuse those fixtures plus public routing regressions and color-detection examples; reserve distinct graphs for evaluation. Count full downstream compilation failures, CPU/memory costs and model expenditure, not just placer score. [Placer options](https://xilinx.github.io/mlir-aie/1.4.3/AIEPasses/), [existing baseline and examples](https://xilinx.github.io/mlir-aie/1.4.4/programming_guide/section-1/).

## Correctness and decision boundary

Compilation success cannot certify tensor results, absence of every deadlock, or throughput. Taplib can check tensor access patterns offline, and transaction tracing compares normalized DMA events; these cover data movement, not complete core semantics. Preserve a logical-graph invariant and reference outputs, then obtain NPU execution before making end-to-end correctness/performance claims. No complete general-purpose public equivalence oracle was established. [Taplib and transaction checks](https://xilinx.github.io/mlir-aie/1.4.4/api/taplib/).

**Inference:** this is arguably the strongest bridge from the user's placement/resource expertise into a current AI compiler stack. Its first public result can be authenticated compile-feasibility coverage; hardware validation is a distinct milestone. Before choosing it, demonstrate one reproducible Peano build, confirm all required checks in the pinned release, and find difficult held-out cases where the existing annealer leaves room for improvement. Career value beyond transferable MLIR/dataflow/allocation skills remains a separate question.
