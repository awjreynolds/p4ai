# Tofino resource-fit discovery inputs

These are **unqualified discovery candidates**, not a demonstrated failure/repair
pair. The generator does not run a compiler. Tofino is an explicitly authorized
research testbed here, not the project's recommendation for a current ASIC career
target.

## Pinned SwitchML application variants

`prepare_switchml.py` prepares an existing application alongside three variants
that remove either or both of its two PHV allocation annotations. It uses only
the local checkout and Python's standard library:

```sh
git clone https://github.com/p4lang/p4app-switchML.git /tmp/p4ai-switchml-source
git -C /tmp/p4ai-switchml-source checkout --detach b5c74a04fa66faa82acab154a4e21c8f76d913f2
python3 examples/p4-fit/prepare_switchml.py \
  --source /tmp/p4ai-switchml-source \
  --output artifacts/p4-fit-discovery/switchml-reproduction
```

Choose fresh source and output paths if those paths already exist. Before any
output writes, the script checks all 17 original P4 file hashes and the upstream
LICENSE against `switchml-source.json`. It rejects an existing destination and
any source mismatch. Each variant preserves the original Apache headers and
copies the Apache LICENSE into its `source` directory. The generated upstream
files retain their upstream license; this repository's MIT license does not
replace it.

The only removed text is exactly:

```p4
@pa_container_size("ingress", "hdr.ib_bth.psn", 32)
@pa_no_overlay("ingress", "ig_md.switchml_md.simulate_egress_drop")
```

The script checks equality after removing these two known annotations, writes
per-file hashes for all four variants, and labels every compile status `not_run`.
It does not modify packet-processing statements, capacities or configuration.
Removing a protective annotation does not establish semantic safety: subsequent
fit and behavior checks are necessary. The original program might compile both
with and without these hints, or a variant might fail for an unrelated reason.

## Synthetic pressure family

`generate.py` creates an experimental service-policy packet pipeline. EtherType
`0x88b5` carries a 50-byte header with a flow identifier, tenant identifier, service
token, eight policy-result words and reserved bytes. Ethernet plus this header is
64 bytes. The pipeline forwards to the input port, performs independent
flow-and-tenant policy lookups, then applies dependent token translations. All
result words and the final token are emitted, so lookup actions have observable
behavior. Unknown EtherTypes pass through without these lookups. Misses leave the
corresponding packet fields unchanged. The egress pipeline parses and emits the
same headers without further changes.

The control plane can install arbitrary exact-match entries up to each declared
capacity. It assigns 32-bit result values to the independent tables and 32-bit
replacement tokens to the chain tables. Each chain lookup uses the token produced
by its predecessor. Keeping this contract fixed is necessary for any later
annotation-only fit claim. The generator is a synthetic workload construction,
not a production networking application.

```sh
python3 examples/p4-fit/generate.py \
  --output artifacts/p4-fit-discovery/qualification \
  --depth 1 --sides 1 --chain-entries 1024 --side-entries 1024 --policy all

python3 examples/p4-fit/generate.py \
  --output artifacts/p4-fit-discovery/chain8-side4-16k-32k \
  --depth 8 --sides 4 --chain-entries 16384 --side-entries 32768 --policy all
```

The generated `default.p4` has **no tuning annotations**. Each alternative changes
only table annotations; the generator checks byte equality after stripping those
annotation lines. A manifest records workload dimensions, annotation policy and
file hashes. No candidate reduces table capacity, changes packet actions, removes
dependencies, asserts mutual exclusion, disables diagnostics or changes hardware
limits. Do not overwrite a measured directory: use a new directory when changing
the generator or workload. The generator refuses any existing output destination,
including an empty directory, before writing candidate files.

The first case qualifies parsing, type checking and backend operation. The second
is an initial pressure probe. Subsequent discovery can increase chain depth,
capacity or independent lookup count until a natural boundary is found. The
supported annotation probes are placement priority, entries-per-word packing and
action-data immediate placement. These controls can make fit worse as well as
better. In particular, `immediate(1)` is already the compiler default; disabling
it is the nondefault probe. A discovered effect is not yet evidence that a model
can infer it.

Before recording a positive pair, retain the complete invocation, pinned compiler
and target, normal baseline resource diagnostic and successful annotated resource
report. A syntax error, unsupported operation, internal compiler error or timeout
does not qualify as a resource-fit failure. The alternative must have the same
capacities and packet/control-plane contract. Compiler fit does not alone prove
semantic equivalence; validate behavior separately.

The annotations and TNA interface were checked against upstream p4c revision
`ca5aef25de1f847b5c88a05af583506484927015`:

- [Pragma definitions](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/common/pragma/pragmas.cpp)
- [TNA interfaces](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/p4include/tofino1_base.p4)
- [Public stage/packing regression test](https://github.com/p4lang/p4c/blob/ca5aef25de1f847b5c88a05af583506484927015/backends/tofino/bf-p4c/test/gtest/pragma_stage.cpp)

These original generated programs and generator are covered by this repository's
MIT license. Upstream source is cited for API and annotation behavior; upstream
program files are not vendored here.
