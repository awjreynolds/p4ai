#!/usr/bin/env python3
"""Static generation checks; these do not establish executed packet semantics."""
import importlib.util
import re
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("generator", Path(__file__).with_name("generate.py"))
assert _spec and _spec.loader
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)
for structure in ("diamonds", "exclusive", "wide-actions"):
    spec = {"structure": structure, "rounds": 3, "entries": 1024, "key_bits": 80, "outputs": 1, "extras": 2}
    baseline, required = module.render(spec, {})
    declared = re.findall(r"table (\w+) \{", baseline)
    assert len(declared) == len(set(declared)) == len(required)
    assert set(declared) == set(required)
    for name, requirement in required.items():
        table = re.search(rf"table {name} \{{(.*?)\n    \}}", baseline, re.S)
        assert table and f'size = {requirement["size"]};' in table.group(1)
    for policy in module.POLICIES.values():
        source, inventory = module.render(spec, policy)
        assert inventory == required
        assert len(source) == len(baseline)
        assert module._scaffold.blank_annotations(source) == baseline
    if structure != "diamonds":
        # Each action parameter updates the chain token or an emitted accumulator.
        for parameters, body in re.findall(r"action set_\w+\((.*?)\) \{ (.*?) \}", baseline):
            for parameter in re.findall(r"bit<32> (\w+)", parameters):
                assert parameter in body
            if "hdr.fit.token" in body:
                assert ("hdr.fit.token = hdr.fit.token + value0;" if structure == "wide-actions"
                        else "hdr.fit.token = value0;") in body
            for field in re.findall(r"hdr.fit.(result\d+) =", body):
                assert f"hdr.fit.{field} = hdr.fit.{field} + " in body
    else:
        assert "hdr.fit.result0 : exact; hdr.fit.result1 : exact;" in baseline
    assert baseline.count("pkt.emit(hdr)") == 2
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "case"
        module.generate(spec, output)
        try:
            module.generate(spec, output)
        except ValueError:
            pass
        else:
            raise AssertionError("existing destination must be refused")
print("3 structures ×26 policies: stable annotation-only layout, inventory, emitted dataflow and fresh-output checks passed")
