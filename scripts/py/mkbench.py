#!/usr/bin/env python3
"""Auto-build an exhaustive testbench for any purely combinational component, from its MEASURED ports.

    py mkbench.py <ComponentXmlName> <out.circ> [--lib N] [--attr name=value ...] [--max-in-bits 16]

WHY THIS SHAPE

Logisim connects Tunnels by LABEL, not by position. So a testbench needs no routed wires at all: put
a Tunnel on each input port and a matching Tunnel on a Pin far to the left, and the net is made. That
is what makes this generator short enough to trust, and it is the same mechanism the project's
ir already relies on.

The port list comes from references/component-catalog.json, which was measured out of the shipped jar - not
from source reading and not from assumption. Inputs are enumerated exhaustively by `--tty table`, so
the verification is complete over the declared input space rather than sampled.

REFUSES rather than guesses:
  * a component with no IN ports, or no OUT ports, is not combinational-testable here;
  * total input bits above --max-in-bits would make the table astronomically large;
  * an attribute name not in the measured set is reported and skipped, never silently dropped.
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import subprocess
import sys

# Only wrap when RUN, never when imported: a second wrapper around the
# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# package root, resolved from this file so the package is portable
import os
ROOT = pathlib.Path(__file__).resolve().parents[2]
KB = ROOT / 'references' / 'component-catalog.json'

LIB_ORDINAL = {'Wiring': 0, 'Gates': 1, 'Plexers': 2, 'Arithmetic': 3,
               'FPArithmetic': 4, 'Memory': 5}
LIB_DESC = {'Wiring': '#Wiring', 'Gates': '#Gates', 'Plexers': '#Plexers',
            'Arithmetic': '#Arithmetic', 'FPArithmetic': '#FPArithmetic', 'Memory': '#Memory'}

def load_kb():
    return json.loads(KB.read_text(encoding='utf-8'))

def find_comp(kb, name):
    for lib, L in kb['libraries'].items():
        if name in L['components']:
            return lib, L['components'][name]
    raise SystemExit(f'component {name!r} not in the measured knowledge base')

PORTSAT_CP = None

def measure_ports(component: str, attrs: dict):
    """Ask the jar for the real ports under this exact attribute combination.

    GeomSweep only varies one attribute at a time from its default, so a COMBINATION is genuinely
    unmeasured. `PortsAt` sets every attribute on a fresh AttributeSet and prints the resulting ends,
    which is the only honest way to answer. Returns None on any failure so the caller refuses rather
    than substituting a default that belongs to a different configuration.
    """
    global PORTSAT_CP
    jar = ROOT / 'scripts' / 'bin' / 'logisim-evolution-all.jar'
    classes = ROOT / 'tests' / 'samples_work' / 'portsat_classes'
    if not (classes / 'PortsAt.class').exists():
        return None
    sample = None
    for cand in sorted((ROOT / 'examples' / 'user_samples').glob('*.circ')):
        sample = cand
        break
    if sample is None:
        return None
    cmd = ['java', '-Djava.awt.headless=true', '-cp', f'{jar};{classes}', 'PortsAt',
           str(sample), component] + [f'{k}={v}' for k, v in attrs.items()]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=120)
    except Exception:
        return None
    out = (r.stdout or '')
    if 'ERROR' in out:
        return None
    ports = []
    for line in out.splitlines():
        m = re.match(r'^PORT (-?\d+) (-?\d+) (\d+) (\S+)$', line)
        if m:
            ports.append({'dx': int(m.group(1)), 'dy': int(m.group(2)),
                          'width': int(m.group(3)), 'type': m.group(4)})
    return ports or None

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('component')
    ap.add_argument('out')
    ap.add_argument('--comp-loc', default='1200,400')
    ap.add_argument('--attr', action='append', default=[])
    ap.add_argument('--max-in-bits', type=int, default=16)
    ap.add_argument('--facing', default=None)
    a = ap.parse_args()

    kb = load_kb()
    lib, c = find_comp(kb, a.component)

    # Libraries must be declared DENSELY from ordinal 0 and every referenced ordinal must exist.
    # FIRST ATTEMPT AT THIS FILE declared only the component's own library, so `lib="0"` (Wiring,
    # which owns Pin and Tunnel) did not resolve and every pin and tunnel was SILENTLY DROPPED:
    # `--tty stats` reported `1 1 TOTAL` and the table came back empty. Silent, not an error.
    need = ['Wiring'] + ([] if lib == 'Wiring' else [lib])
    ord_of = {name: i for i, name in enumerate(need)}
    ordinal = ord_of[lib]
    wiring = ord_of['Wiring']

    # ATTRIBUTES FIRST: the port list depends on them, so they must be resolved before any measurement.
    known = {x['name'] for x in c['attributes']}
    attrs = {}
    for spec in a.attr:
        if '=' not in spec:
            raise SystemExit(f'--attr needs name=value, got {spec!r}')
        k, v = spec.split('=', 1)
        if k not in known:
            raise SystemExit(f'{a.component} has no attribute {k!r}; measured attributes are '
                             f'{sorted(known)}')
        attrs[k] = v
    if a.facing:
        if 'facing' not in known:
            raise SystemExit(f'{a.component} has no facing attribute - it cannot be rotated')
        attrs['facing'] = a.facing

    # Ports for the REQUESTED configuration. The knowledge base's sweeps vary one attribute at a
    # time, so a combination cannot be answered from them - the jar is asked directly instead. This
    # is what makes `--attr inputs=3 --attr size=30` produce a bench with three input pins rather
    # than silently reverting to the default two.
    if attrs:
        measured = measure_ports(a.component, attrs)
        if measured is None:
            raise SystemExit(
                f'could not measure ports for {a.component} with {attrs}; refusing to fall back to '
                f'the default port list, which would build a bench for a DIFFERENT component')
        ins_raw = [p for p in measured if p['type'] == 'IN']
        outs_raw = [p for p in measured if p['type'] == 'OUT']
        ports_src = f'measured on the jar for {attrs}'
    else:
        ins_raw = [p for p in c['ports_default'] if p['type'] == 'IN']
        outs_raw = [p for p in c['ports_default'] if p['type'] == 'OUT']
        ports_src = 'knowledge base defaults'
    ins, outs = ins_raw, outs_raw
    if not ins:
        raise SystemExit(f'{a.component} has no measured IN port - not combinational-testable here')
    if not outs:
        raise SystemExit(f'{a.component} has no measured OUT port - not combinational-testable here')
    total_bits = sum(p['width'] for p in ins)
    if total_bits > a.max_in_bits:
        raise SystemExit(f'{a.component}: {total_bits} input bits exceeds --max-in-bits '
                         f'{a.max_in_bits} ({2 ** total_bits} rows would be enumerated)')

    locx, locy = (int(v) for v in a.comp_loc.split(','))

    L = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
         '<project source="5.1.0dev" version="1.0">']
    for name in need:
        L.append(f'  <lib desc="{LIB_DESC[name]}" name="{ord_of[name]}"/>')
    L += ['  <main name="main"/>',
          '  <circuit name="main">']

    # the component under test
    aL = [f'<a name="{k}" val="{v}"/>' for k, v in attrs.items()]
    L.append(f'    <comp lib="{ordinal}" loc="({locx},{locy})" name="{c["xml_name"]}">'
             + ''.join(aL) + '</comp>')

    # INPUT side: a source Pin far left with a co-located Tunnel, and a sink Tunnel placed exactly ON
    # the component's input port. A Tunnel ON an input port DOES drive it - that is the mechanism the
    # project's 512/512 circuits already rely on.
    y = 200
    for i, p in enumerate(ins):
        name = f'in{i}'
        L.append(f'    <comp lib="{wiring}" loc="(200,{y})" name="Pin">'
                 f'<a name="label" val="{name}"/><a name="type" val="input"/>'
                 f'<a name="width" val="{p["width"]}"/></comp>')
        # A WIRE from the Pin to its Tunnel is REQUIRED. Measured against the project's own 512/512
        # circuit, which emits `<wire from="(120,200)" to="(150,200)"/>` between them. Placing the
        # Tunnel 40 px from the Pin with no wire leaves the net undriven, the gate inputs float, and
        # the error value propagates until the whole table reads `E`.
        L.append(f'    <wire from="(200,{y})" to="(240,{y})"/>')
        L.append(f'    <comp lib="{wiring}" loc="(240,{y})" name="Tunnel">'
                 f'<a name="label" val="{name}"/><a name="facing" val="west"/>'
                 f'<a name="width" val="{p["width"]}"/></comp>')
        L.append(f'    <comp lib="{wiring}" loc="({locx + p["dx"]},{locy + p["dy"]})" name="Tunnel">'
                 f'<a name="label" val="{name}"/><a name="facing" val="east"/>'
                 f'<a name="width" val="{p["width"]}"/></comp>')
        y += 60

    # OUTPUT side: a WIRE must leave the output port before anything reads it.
    #
    # MEASURED, both variants on a NOT Gate:
    #   Pin placed directly on the output port   -> the table prints `E` (two outputs on one point)
    #   Tunnel placed directly on the output port-> the table prints `U` (the net stays undriven)
    # So neither a Pin nor a Tunnel co-located with an OUTPUT port works. A wire from the port to a
    # stub point does, which is exactly what the project's generator has always done.
    y = 200
    for i, p in enumerate(outs):
        name = f'out{i}'
        px, py = locx + p['dx'], locy + p['dy']
        sx = px + 40 * (1 if p['dx'] >= 0 else -1)
        L.append(f'    <wire from="({px},{py})" to="({sx},{py})"/>')
        L.append(f'    <comp lib="{wiring}" loc="({sx},{py})" name="Tunnel">'
                 f'<a name="label" val="{name}"/><a name="facing" val="west"/>'
                 f'<a name="width" val="{p["width"]}"/></comp>')
        # The far-right Pin and its Tunnel must be CO-LOCATED. Measured against the project's own
        # 512/512 circuit, which places both at one point, e.g. `loc="(2940,200)"` for `D0`. Putting
        # them 40 px apart with no wire between leaves the output net undriven and the whole table
        # reads `U` - which is exactly what the first version of this script produced.
        L.append(f'    <comp lib="{wiring}" loc="(2400,{y})" name="Pin">'
                 f'<a name="label" val="{name}"/><a name="type" val="output"/>'
                 f'<a name="facing" val="west"/>'
                 f'<a name="width" val="{p["width"]}"/></comp>')
        L.append(f'    <comp lib="{wiring}" loc="(2400,{y})" name="Tunnel">'
                 f'<a name="label" val="{name}"/><a name="facing" val="east"/>'
                 f'<a name="width" val="{p["width"]}"/></comp>')
        y += 60

    L.append('  </circuit>')
    L.append('</project>')

    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')

    # machine-readable pin legend so the table columns can be mapped back to real port names
    legend = {
        'component': c['xml_name'], 'library': lib, 'attributes_set': attrs,
        'comp_loc': [locx, locy],
        'inputs': [{'table_column': f'in{i}', 'port_offset': [p['dx'], p['dy']],
                    'width': p['width']} for i, p in enumerate(ins)],
        'outputs': [{'table_column': f'out{i}', 'port_offset': [p['dx'], p['dy']],
                     'width': p['width']} for i, p in enumerate(outs)],
        'exhaustive_rows': 2 ** total_bits,
        'note': 'port_offset is relative to comp_loc; the table columns are the TUNNEL LABELS, so '
                'they are positional, not the component\'s own port names (which the jar does not '
                'expose for most components).',
    }
    leg = out.with_suffix('.legend.json')
    leg.write_text(json.dumps(legend, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    print(f'  wrote {out.relative_to(ROOT)}')
    print(f'    component {c["xml_name"]} [{lib}] at ({locx},{locy})')
    print(f'    inputs  {len(ins)} ports, {total_bits} bits -> {2 ** total_bits} rows')
    for i, p in enumerate(ins):
        print(f'      in{i}  port_offset=({p["dx"]},{p["dy"]}) width={p["width"]}')
    print(f'    outputs {len(outs)} ports')
    for i, p in enumerate(outs):
        print(f'      out{i} port_offset=({p["dx"]},{p["dy"]}) width={p["width"]}')
    if attrs:
        print(f'    attributes set: {attrs}')
    print(f'    legend -> {leg.relative_to(ROOT)}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
