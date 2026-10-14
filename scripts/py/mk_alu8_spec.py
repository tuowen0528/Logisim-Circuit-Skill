#!/usr/bin/env python3
"""Build the 8-bit ALU spec: logic + adder + subtractor + multiplier, selected by a 4-bit opcode.

    Y   = low 8 bits of the result
    Yhi = high 8 bits (non-zero only for MUL, which is a full 8x8 -> 16-bit product)

    0 AND  1 OR  2 XOR  3 NOT(A)  4 NAND  5 NOR  6 ADD  7 SUB  8 MUL  9..15 reserved (0)

The golden model is a table lookup over the ARITHMETIC requirement - it never names a gate, a
component or a net - so it is independent of how the circuit is wired.

Every component port used here comes from references/component-catalog.json, which was measured out of the
shipped jar. The Multiplier's 5-port layout in particular was NOT assumed: an exhaustive 65536-row
simulation of an 8x8 multiply matched a*b on every row, which is what established that `out` is the
low half and `hi` is the high half.

Usage:  py mk_alu8_spec.py [out.json]
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
W = 8
OPS = 16
HIGH = (1 << W) - 1

# Human-readable opcode names, used only to build the net names and the documentation.
NAMES = ['and', 'or', 'xor', 'not', 'nand', 'nor', 'add', 'sub', 'mul']


def build() -> dict:
    parts = []
    nets = {}

    # --- the six logic gates, each a single width-8 gate --------------------------------
    for nm, gate in (('and', 'AND'), ('or', 'OR'), ('xor', 'XOR'), ('nand', 'NAND'),
                     ('nor', 'NOR')):
        parts.append({'ref': f'g_{nm}', 'kind': 'GATE', 'gate': gate,
                      'loc': [520, 200 + 120 * len(parts)], 'inputs': 2, 'width': W})
    # NOT takes one input; `inputs` must not be emitted for it (rtllib only emits when != 2,
    # and a 1-input gate is expressed by omitting the attribute entirely).
    parts.append({'ref': 'g_not', 'kind': 'GATE', 'gate': 'NOT',
                  'loc': [560, 200 + 120 * len(parts)], 'width': W})

    # --- the arithmetic components ------------------------------------------------------
    parts.append({'ref': 'add', 'kind': 'ADDER', 'loc': [1000, 200], 'width': W})
    parts.append({'ref': 'sub', 'kind': 'SUBTRACTOR', 'loc': [1000, 460], 'width': W})
    parts.append({'ref': 'mul', 'kind': 'MULTIPLIER', 'loc': [1000, 720], 'width': W,
                  'mode': 'unsigned'})

    # --- two 16-way multiplexers, one per result byte -----------------------------------
    # select=4 needs four data pins of 8 bits each; only 9 opcodes are defined and the rest read 0.
    parts.append({'ref': 'm_lo', 'kind': 'MUX', 'loc': [1560, 440], 'select': 4, 'width': W})
    parts.append({'ref': 'm_hi', 'kind': 'MUX', 'loc': [1560, 900], 'select': 4, 'width': W})

    # --- one shared zero constant --------------------------------------------------------
    # A single Constant feeds every reserved opcode on BOTH multiplexers and the high byte of
    # every non-MUL operation. Nets connect by label, so one source is enough.
    parts.append({'ref': 'zero', 'kind': 'CONST', 'loc': [300, 1400], 'width': W, 'value': 0})

    # --- nets ---------------------------------------------------------------------------
    gate_uses = []
    for nm in ('and', 'or', 'xor', 'not', 'nand', 'nor'):
        gate_uses.append([f'g_{nm}', 'in0' if nm != 'not' else 'in0'])
    two_input = ('and', 'or', 'xor', 'nand', 'nor')

    a_use = [[f'g_{nm}', 'in0'] for nm in ('and', 'or', 'xor', 'not', 'nand', 'nor')]
    a_use += [['add', 'A'], ['sub', 'A'], ['mul', 'A']]
    b_use = [[f'g_{nm}', 'in1'] for nm in two_input]
    b_use += [['add', 'B'], ['sub', 'B'], ['mul', 'B']]

    nets['A'] = {'drive': 'in:A', 'use': a_use}
    nets['B'] = {'drive': 'in:B', 'use': b_use}
    nets['Op'] = {'drive': 'in:Op', 'use': [['m_lo', 'select'], ['m_hi', 'select']]}

    # the low byte multiplexer: defined opcodes first, then the reserved slots all read 0
    for i, nm in enumerate(NAMES):
        src = {'add': 'add:Sum', 'sub': 'sub:out', 'mul': 'mul:out'}.get(nm, f'g_{nm}:out')
        nets[f'n_{nm}'] = {'drive': src, 'use': [['m_lo', f'd{i}']]}
    zero_lo = [[ 'm_lo', f'd{i}'] for i in range(len(NAMES), OPS)]

    # the high byte: only MUL produces one; everything else is 0, including the reserved slots
    nets['n_mulhi'] = {'drive': 'mul:hi', 'use': [['m_hi', f'd{NAMES.index("mul")}']]}
    zero_hi = [['m_hi', f'd{i}'] for i in range(OPS) if i != NAMES.index('mul')]

    nets['zero'] = {'drive': 'zero:-', 'use': zero_lo + zero_hi}

    nets['Y'] = {'drive': 'm_lo:out', 'use': ['out:Y']}
    nets['Yhi'] = {'drive': 'm_hi:out', 'use': ['out:Yhi']}

    # --- golden model: arithmetic only, no gate/component/net name ----------------------
    not_a = f"((~A) & {HIGH})"
    expr_y = ('[' + ', '.join([
        '(A & B)', '(A | B)', '(A ^ B)', not_a,
        f'((~(A & B)) & {HIGH})', f'((~(A | B)) & {HIGH})',
        f'((A + B) & {HIGH})', f'((A - B) & {HIGH})', f'((A * B) & {HIGH})',
    ] + ['0'] * (OPS - len(NAMES))) + '][Op]')
    expr_hi = ('[' + ', '.join(
        [f'((A * B) >> {W})' if i == NAMES.index('mul') else '0' for i in range(OPS)]) + '][Op]')

    return {
        'name': 'main',
        'inputs': [['A', W], ['B', W], ['Op', 4]],
        'outputs': [['Y', W], ['Yhi', W]],
        'parts': parts,
        'nets': nets,
        # Unconnected inputs are UNDEFINED, not zero. Each of these three is a carry/borrow/high-word
        # input that must be driven low for the plain single-width operation.
        'tie': {'add:Cin': 0, 'sub:Bin': 0, 'mul:upper': 0},
        'expression': {'Y': expr_y, 'Yhi': expr_hi},
        '_note': (
            '8-bit ALU. Y = low byte, Yhi = high byte (MUL only). Op: '
            '0 AND, 1 OR, 2 XOR, 3 NOT(A), 4 NAND, 5 NOR, 6 ADD, 7 SUB, 8 MUL, 9-15 reserved = 0. '
            'The golden model is the arithmetic requirement, never the topology. '
            'Multiplier `out` is the LOW half and `hi` the HIGH half - measured, then confirmed by '
            'an exhaustive 65536-row 8x8 multiply that matched a*b on every row.'
        ),
        '_requirement': (
            'An 8-bit ALU taking A[8], B[8] and a 4-bit opcode, producing the 8-bit result Y plus '
            'the high byte Yhi. It must support the basic logic gates (AND, OR, XOR, NOT, NAND, '
            'NOR), addition, subtraction and multiplication of the two 8-bit operands.'
        ),
    }


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / 'tests' / 'specs' / 'rtl' / 'alu8.json'
    spec = build()
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'  wrote {out.name}')
    print(f'    inputs  : {spec["inputs"]}   -> {2 ** sum(w for _n, w in spec["inputs"])} rows')
    print(f'    outputs : {spec["outputs"]}')
    print(f'    parts   : {len(spec["parts"])}')
    for p in spec['parts']:
        extra = {k: v for k, v in p.items() if k not in ('ref', 'kind', 'loc')}
        print(f'      {p["ref"]:8} {p["kind"]:12} {extra}')
    print(f'    nets    : {len(spec["nets"])}')
    for n, d in spec['nets'].items():
        print(f'      {n:10} drive={d["drive"]:12} sinks={len(d["use"])}')
    print(f'    tie     : {spec["tie"]}')
    total_sinks = sum(len(d["use"]) for d in spec['nets'].values())
    print(f'    总 sink 数 = {total_sinks}')
    # Every multiplexer data input must be driven exactly once, or the mux reads undefined.
    sinks = {}
    for n, d in spec['nets'].items():
        for u in d['use']:
            key = (u[0], u[1]) if isinstance(u, list) else u
            sinks[key] = sinks.get(key, 0) + 1
    missing = []
    for p in spec['parts']:
        if p['kind'] == 'MUX':
            for i in range(1 << p['select']):
                if (p['ref'], f'd{i}') not in sinks:
                    missing.append(f'{p["ref"]}.d{i}')
    dupes = {k: v for k, v in sinks.items() if v > 1}
    print(f'    未连接的 mux 数据端: {missing if missing else "无"}')
    print(f'    被多个 net 驱动的端: {dupes if dupes else "无"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
