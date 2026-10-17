# Current P4 targets: career relevance versus public reproducibility

Research for [Assess current P4 targets and public toolchain viability](https://github.com/awjreynolds/p4ai/issues/3), checked 17 October 2026. Primary vendor documentation, source and recruitment evidence only. **No compiler, SDK, simulator or benchmark was installed or executed.** The user has ruled Tofino out as the destination: an active target and plausible career opportunity are requirements alongside authentic constraints and a reproducible public demonstration. Recommendations below are research judgments, not the user's target choice.

## Assessment

P4 experience remains commercially relevant, particularly around AMD Pensando. However, this investigation did **not establish a current P4 hardware platform that clearly satisfies all three gates**: active investment, public access to the necessary compiler and authentic target model, and an intervention surface suitable for compiler research. That is an evidence gap, not a claim that no such platform exists.

**Pensando is the strongest relationship-led P4 candidate; a publicly accessible target in eBPF or another compiler ecosystem is the stronger default for an independently reproducible demonstration until Pensando access is established.** Vitis Networking P4 is active but commercially licensed. NVIDIA DPL has an explicit maintenance-mode announcement. Intel IPU E2100 requires manufacturer-supplied tooling and has additional lifecycle warning signs.

| Candidate | Current relevance evidence | Compiler/model access finding | Implication |
|---|---|---|---|
| AMD Pensando Salina / SSDK | Current AMD platform, active P4 compiler recruitment | SSDK compiler/simulator documented; unrestricted current-generation access and backend source unverified | Best P4 contact investigation; not yet a public-demo commitment |
| AMD Vitis Networking P4 | 2026.1 compiler documentation; current ESnet integration | Compiler license through sales/FAE; additional CAM license | Viable licensed collaboration, weaker frictionless reproduction |
| NVIDIA DPL | P4-derived BlueField tooling exists | Compiler container documented; new-feature development ending | Do not select for the user's forward-looking aim |
| Intel IPU E2100 | Public control-plane documentation | SDK/compiler obtained from manufacturer; confidential documentation references | No established public gate pass; lifecycle needs checking |
| P4→eBPF / software targets | Public p4c backends | Public source, with target-specific restrictions | Potential bridge, but evaluate actual eBPF verifier constraints rather than inventing ASIC limits |

## AMD Pensando: strongest career lead, unresolved access gate

AMD's current Helios page includes a Salina DPU with a P4-programmable datapath and Arm cores. Its October 2026 discussion of agentic infrastructure also explicitly identifies Salina as P4 programmable. This establishes continuing product relevance; it does not establish public compiler access. [Helios platform](https://www.amd.com/en/products/rackscale-solutions/helios.html), [current Salina discussion](https://www.amd.com/en/blogs/2026/how-agentic-ai-is-expanding-the-role-of-the-dpu-.html)

A currently advertised AMD 2027 compiler internship describes work on a P4 compiler targeting the company's ASIC family, using LLVM and P4C. This is direct evidence that P4 compiler skills are being recruited. It is not a recommendation that this particular junior US-based role suits the user, nor a guarantee of senior roles or geographical fit. [AMD compiler role](https://careers.amd.com/careers-home/jobs/91865)

The SSDK brief documents an x86 container environment, P4_16 compiler, DPU simulator, libraries, debugging tools and reference pipelines. It directs prospective users to sign up. AMD's developer page links documentation and the brief rather than demonstrating an unrestricted compiler download. Its published pipeline guides discuss supplied binary DMA libraries alongside editable ingress/egress P4. **Do not equate public documentation, a containerized SDK or a P4-programmable product with an open compiler backend.** [SSDK brief](https://www.amd.com/content/dam/amd/en/documents/pensando-technical-docs/product-briefs/pensando-ssdk-product-brief.pdf), [developer tools](https://www.amd.com/en/developer/browse-by-resource-type/software-tools.html), [pipeline composition](https://docs.amd.com/r/en-US/ug1668-amd-pensando-ssdk-sdn-policy-offload-user-guide/Introduction)

The brief and accessible guides are predominantly 2023–2024 material associated with earlier generations. I have not verified that an external researcher can download a Salina-capable compiler and simulator, publish resource diagnostics and benchmarks, redistribute a runnable environment, or modify heuristic selection inside the backend. These are concrete questions for existing contacts, with a binary gate: obtain a documented public reproduction route, or keep the target as a private collaboration opportunity rather than the public demonstrator. No contacts were messaged.

## AMD Vitis Networking P4: current, but licensed

The June 2026 installation guide requires the `sdnet_p4` compiler license; using CAMs additionally requires `hcam_base`. Licenses are requested through AMD sales/FAE, with an academic donation route through XUP. The product page lists current FPGA families including Versal and describes a Core License Agreement. [2026.1 license instructions](https://docs.amd.com/r/en-US/ug1307-vitis-p4-install/Installing-the-License-File), [product](https://www.amd.com/en/products/adaptive-socs-and-fpgas/intellectual-property/ef-di-vitisnetp4.html)

ESnet's public SmartNIC repository is a valuable concrete integration: RTL, verification tests, build scripts and a P4 processing core targeting Alveo. Its current README uses Vivado 2025.2/Ubuntu 24.04 and explicitly says VitisNetP4 needs a site-specific commercial license. OpenNIC/ESnet source therefore does not remove the compiler gate. [ESnet SmartNIC](https://github.com/esnet/esnet-smartnic-hw)

Inference: this is attractive for FPGA/networking collaboration but less suitable for a clone-and-run portfolio artifact. A black-box compiler could support selecting source-preserving configuration or generated alternatives, but does not automatically expose backend allocation decisions. Actual FPGA resource fit and timing closure need the synthesis/implementation flow; a P4 frontend success alone is insufficient. Before committing, establish licensed automation, permissible public benchmark publication, inspectable resource reports and an executable no-hardware correctness path. These were not verified here.

## NVIDIA: distinguish an active networking ecosystem from DPL's future

DPL genuinely derives from P4_16 and has NVIDIA-specific BlueField semantics. NVIDIA documented its compiler as a self-contained `dpl-dev` image available to partners via NGC; that archived documentation is evidence of a real compiler, not proof of present unrestricted access. [DPL model](https://networking-docs.nvidia.com/doca/archive/3-0-0-vgt-update/dpl-system), [compiler distribution](https://docs.nvidia.com/doca/archive/3-1-0/compiling%2Bdpl%2Bapplications/index.html)

More decisively, the current DOCA release notes, updated 22 July 2026, say DPL is moving into maintenance mode, with no new features from the following release and BlueField-4 limited to the BlueField-3 feature set/performance. They recommend **DOCA Flow** for new solutions and platforms. DPL therefore fails the user's forward-looking criterion even though BlueField itself remains relevant. DOCA Flow may merit separate networking research, but substituting its API for P4 changes the proposed compiler experiment; public documentation does not establish a hardware-free resource oracle. [DOCA lifecycle notice](https://networking-docs.nvidia.com/doca/sdk/changes-and-new-features)

## Intel IPU and public P4 alternatives

IPDK's E2100 setup requires obtaining the IPU SDK and P4 compiler from the manufacturer. Its compilation guide refers to an RDC software guide and an SDK tarball. Intel's own E2100 Redfish repository states the SDK is under NDA and now declares that **that project** is discontinued. This is not evidence that every E2100 component is discontinued, but it is sufficient to reject an assumption of easy public access or guaranteed lifecycle. [IPDK setup](https://ipdk.io/p4cp-userguide/guides/setup/es2k-setup-guide.html), [compiler guide](https://ipdk.io/p4cp-userguide/guides/es2k/compiling-p4-programs.html), [Intel Redfish status](https://github.com/intel/ipu-redfish)

Public p4c includes eBPF, DPDK, TC and other backends. These can preserve a P4 frontend while targeting a living software ecosystem. BMv2 is a reference software switch, not an authentic Tofino stage/PHV model. If the experiment targets eBPF, its actual kernel verifier, stack/instruction restrictions and semantics must define success; choosing P4 syntax alone provides no resource model. Whether P4 adds useful scope compared with direct LLVM→eBPF is a separate decision. [p4c](https://github.com/p4lang/p4c), [BMv2](https://github.com/p4lang/behavioral-model)

## Experiment requirements that survive the target change

Prefer a narrow decision seam: select among compiler-generated legal candidates or bounded retry configurations while keeping the input program's semantics and required capacity fixed. Mechanical validation must remain authoritative; escalation may rank or abstain. Source rewriting creates broader equivalence obligations. Backend changes can also miscompile despite unchanged source.

Compare the ladder against stock compiler retries, deterministic portfolios, equal-budget beam/backtracking search and random controls. Include exact enumeration or SMT/ILP/CP-SAT for small subproblems whose constraints are faithfully modeled. Report valid fit/acceptance rate, incremental repairs, elapsed time, compiler invocations, reasoning cost and false acceptances. A solver timeout or compiler failure is not a proof of infeasibility.

Keep resource acceptance and behavioral correctness as separate gates. Packet/model tests provide evidence, not semantic proof; source-level oracles must cover cases where the baseline cannot produce a binary. The same fixed resource limits, functionality, table capacities and permitted recirculation must apply to every competitor. These are proposed evaluation requirements, not measured findings.

## Historical Tofino reference, excluded from destination

The public Tofino backend and Open P4Studio model remain useful reference implementations. They expose production allocation heuristics, backtracking and structured resource logs. Current source also contains a public Tofino P4Testgen extension despite its omission from the general README. Its upstream test configuration disables power/parse-depth checks, so those flags must never silently define authentic fit. [Placement code](https://github.com/p4lang/p4c/blob/main/backends/tofino/bf-p4c/mau/table_placement.cpp), [schemas](https://github.com/p4lang/p4c/blob/main/backends/tofino/compiler_interfaces/README.md), [test configuration](https://github.com/p4lang/p4c/blob/main/backends/p4tools/modules/testgen/targets/tofino/test/P4Tests.cmake)

The full public SDE retains x86_64 binary-only dependencies, and new Intel hardware software-access requests ended in January 2026. These reinforce reproduction/lifecycle caution but do not change the user's exclusion. [Binary inventory](https://github.com/p4lang/open-p4studio/blob/main/docs/processor-specific-binary-files.md)

**Proposed resolution:** do not choose P4 simply because Tofino is familiar. Retain Pensando as the strongest current P4 opportunity subject to an explicit access/publishing gate; keep Vitis as a licensed alternative; exclude Tofino and maintenance-mode DPL from the destination. Proceed with public alternatives in parallel. P4 experience transfers to constrained scheduling, allocation, validation and networking compiler work even if the first public target uses another language.
