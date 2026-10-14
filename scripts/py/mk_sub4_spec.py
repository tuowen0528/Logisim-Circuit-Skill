#!/usr/bin/env python3
"""Build the 4-bit full-subtractor spec from the REQUIREMENT, not from the adder spec file.

    D = A - B - Bin          (mod 16, A and B unsigned 4-bit)
    Bout = 1 if A - B - Bin < 0 else 0

WHY A SCRIPT AND NOT A HAND-WRITTEN JSON

The adder spec is 20 gates and 29 nets typed by hand; a subtractor needs 26 gates and ~35 nets, and a
single transposed index or a mis-wired carry is exactly the class of defect that still produces a
plausible-looking table. Generating the ripple chain in a loop makes the structure correct by
construction, and the interesting content - the arithmetic - stays in the `expression` field, which
never mentions a gate.

THE DESIGN, and why it is the adder with three changes

Two's complement: `-B == ~B + 1` at 4 bits, so `A - B - Bin == A + ~B + 1 - Bin`. A ripple-carry
adder computes `A + ~B + c_in`; setting `c_in = ~Bin` gives

    A + (15 - B) + (1 - Bin)  ==  A - B - Bin + 16

so the low four bits are the difference and the final carry is 1 exactly when `A - B - Bin >= 0`.
Therefore `Bout = NOT(final carry)`.

Changes from the adder:
  1. every `B_i` passes through a `NOT` gate before feeding the full adder;
  2. the stage-0 carry-in is `NOT(Bin)` rather than `Bin`;
  3. one extra `NOT` turns the final carry into `Bout`.

NEGATIVE CONTROLS

A verified pass is worthless without a variant that must fail, otherwise "the model is satisfied"
cannot be told apart from "the model is blind". Three are provided, at deliberately different
granularity:

  no-bin-invert   feed `Bin` straight to stage-0's carry-in (omit its NOT). Computes
                  A + ~B + Bin == A - B - 1 + Bin, which is wrong on every row. A gross control.
  swap-b01        invert B0 and B1 as usual but cross them, so the two low bits drive the wrong
                  stage. Correct exactly when B0 == B1, so roughly half the rows fail - this is what
                  shows the model discriminates PER ROW rather than merely noticing something gross.
  no-bout-invert  drop the final NOT, leaving Bout = final carry. Only Bout is wrong; D0..D3 stay
                  correct, so this isolates a SINGLE output.

Usage:
    py mk_sub4_spec.py                                  # correct spec -> specs/comb/sub4.json
    py mk_sub4_spec.py out.json
    py mk_sub4_spec.py --control swap-b01 out.json
"""
from __future__ import annotations

import io
import json
import pathlib
import sys

# Only wrap when RUN, never when imported: a second wrapper around the
# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Resolved from this module location so the package runs from any
# working directory.
ROOT = pathlib.Path(__file__).resolve().parents[2]
BITS = 4
CONTROLS = ('no-bin-invert', 'swap-b01', 'no-bout-invert')


def build(control: str | None = None) -> dict:
    if control is not None and control not in CONTROLS:
        raise SystemExit(f'unknown control {control!r}; known: {", ".join(CONTROLS)}')
    gates = []
    nets = {}

    # 1. Invert every B bit, plus Bin. `swap-b01` inverts the right bits but feeds them from the
    #    wrong pin, so nb0 inverts B1 and nb1 inverts B0.
    for i in range(BITS):
        gates.append([f"nb{i}", "NOT", 1, 1])
        src = f"B{i}"
        if control == 'swap-b01':
            src = "B1" if i == 0 else ("B0" if i == 1 else f"B{i}")
        nets[f"b{i}"] = {"drive": f"in:{src}", "use": [[f"nb{i}", 0]]}
    if control == 'no-bin-invert':
        bin_carry = "in:Bin"
    else:
        gates.append(["nbin", "NOT", 1, 1])
        nets["Bin"] = {"drive": "in:Bin", "use": [["nbin", 0]]}
        bin_carry = "g:nbin"

    # 2. Four full adders over A_i and NOT(B_i), with the carry chain fed by NOT(Bin).
    #    `drive` names a SOURCE - "in:<pin>" or "g:<gate>" - never a net name.
    carry = bin_carry
    for i in range(BITS):
        x, ca, s, cb, k = f"x{i}", f"ca{i}", f"s{i}", f"cb{i}", f"k{i}"
        gates += [[x, "XOR", 2, 1], [ca, "AND", 2, 1], [s, "XOR", 2, 1],
                  [cb, "AND", 2, 1], [k, "OR", 2, 1]]
        nets[f"A{i}"] = {"drive": f"in:A{i}", "use": [[x, 0], [ca, 0]]}
        nets[f"ni{i}"] = {"drive": f"g:nb{i}", "use": [[x, 1], [ca, 1]]}
        nets[f"w{i}"] = {"drive": f"g:{x}", "use": [[s, 0], [cb, 0]]}
        nets[f"c{i}"] = {"drive": carry, "use": [[cb, 1], [s, 1]]}
        nets[f"u{i}"] = {"drive": f"g:{ca}", "use": [[k, 0]]}
        nets[f"v{i}"] = {"drive": f"g:{cb}", "use": [[k, 1]]}
        nets[f"D{i}"] = {"drive": f"g:{s}", "use": [f"out:D{i}"]}
        carry = f"g:{k}"

    # 3. Bout = NOT(final carry). `no-bout-invert` drops this NOT, isolating one output.
    if control == 'no-bout-invert':
        nets["Bout"] = {"drive": carry, "use": ["out:Bout"]}
    else:
        gates.append(["nbo", "NOT", 1, 1])
        nets["co"] = {"drive": carry, "use": [["nbo", 0]]}   # short label: see the note below
        nets["Bout"] = {"drive": "g:nbo", "use": ["out:Bout"]}

    # 4. Golden model, purely arithmetic - it names no gate and no wiring.
    a = " + ".join(f"{2 ** i}*A{i}" for i in range(BITS))
    b = " + ".join(f"{2 ** i}*B{i}" for i in range(BITS))
    diff = f"(({a}) - ({b}) - Bin)"
    expr = {f"D{i}": f"({diff} % 16 >> {i}) & 1" for i in range(BITS)}
    expr["Bout"] = f"1 if {diff} < 0 else 0"

    spec = {
        "name": "main",
        "inputs": [f"A{i}" for i in range(BITS)] + [f"B{i}" for i in range(BITS)] + ["Bin"],
        "outputs": [f"D{i}" for i in range(BITS)] + ["Bout"],
        "gates": gates,
        "nets": nets,
        "expression": expr,
        "_note": ("A0..A3 and B0..B3 are LSB-first. D = A - B - Bin (mod 16); Bout = 1 when the "
                  "true difference is negative. Golden model is the arithmetic requirement, never "
                  "the gate topology. Bin's NOT is what makes this chainable like the adder's Cin."),
        "_design": ("Ripple-carry adder over A_i and NOT(B_i) with stage-0 carry-in = NOT(Bin): "
                    "A + (15-B) + (1-Bin) = A - B - Bin + 16, so Bout = NOT(final carry)."),
    }
    if control:
        spec["_control"] = control
        spec["_note"] = (f"NEGATIVE CONTROL ({control}) - deliberately defective, MUST FAIL. "
                         + spec["_note"])
    return spec


def main() -> int:
    argv = sys.argv[1:]
    # WITHOUT this, `--help` falls through to the "no positional argument" path and OVERWRITES the
    # committed spec at specs/comb/sub4.json. A help flag that destroys data is worse than no
    # help flag, and the self-audit runs `--help` on every script in scripts/py.
    if '--help' in argv or '-h' in argv:
        print(__doc__)
        return 0
    control = None
    if '--control' in argv:
        i = argv.index('--control')
        if i + 1 >= len(argv):
            raise SystemExit('--control needs a value')
        control = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    positional = [a for a in argv if not a.startswith('--')]
    out = pathlib.Path(positional[0]) if positional else \
        ROOT / 'tests' / 'specs' / 'comb' / 'sub4.json'

    spec = build(control)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding='utf-8')

    tag = f'   [NEGATIVE CONTROL: {control}]' if control else ''
    print(f'  wrote {out.name}{tag}')
    print(f'    inputs  : {len(spec["inputs"])}  ({", ".join(spec["inputs"])})')
    print(f'    outputs : {len(spec["outputs"])}  ({", ".join(spec["outputs"])})')
    print(f'    gates   : {len(spec["gates"])}')
    print(f'    nets    : {len(spec["nets"])}')
    print(f'    exhaustive rows = 2**{len(spec["inputs"])} = {2 ** len(spec["inputs"])}')
    # Two nets sharing one driver source, or a net nobody drives, is the failure mode a generated
    # spec must not have. Reported rather than assumed.
    drivers = {}
    for name, d in spec["nets"].items():
        drivers.setdefault(d["drive"], []).append(name)
    dupes = {k: v for k, v in drivers.items() if len(v) > 1}
    print(f'    同一驱动源被两个 net 使用: {dupes if dupes else "无"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
