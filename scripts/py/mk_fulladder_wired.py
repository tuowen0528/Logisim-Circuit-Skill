#!/usr/bin/env python3
"""Build the 1-bit full adder with REAL WIRES - no tunnels at all.

    Sum  = A XOR B XOR Cin
    Cout = (A AND B) OR (Cin AND (A XOR B))

Components: 2 XOR, 2 AND, 1 OR, 3 input Pins, 2 output Pins. Exactly as the requirement states.

EVERY coordinate comes from `geom.py`, which reads measured geometry (knowledge base for defaults and
single-attribute variations, `PortsAt` against the shipped jar for arbitrary combinations). Nothing
here assumes a port offset: the router is handed real port coordinates, so a wire endpoint either
lands ON a port or the audit says so.

Output: a .circ whose connections are <wire> segments. Tunnel count must be 0.
"""
from __future__ import annotations

import io
import pathlib
import sys
from typing import Dict, List, Tuple

# Resolved from this module location so the package runs from any
# working directory.
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts' / 'py'))
# `geom` already made sys.stdout UTF-8 safe; wrapping here too would close the shared buffer.
import geom  # noqa: E402
import project_header as _ph  # noqa: E402
from router import Router, RoutedNet  # noqa: E402

WIRING_LIB, GATES_LIB = 0, 1

# --- layout ------------------------------------------------------------------------------------
# Columns by dependency depth: pins | x1,a1 | Sum,a2 | Cout | output pins
COL_PIN, COL_0, COL_1, COL_2 = 120, 460, 900, 1340
COL_OUT = 1780
ROWS = (200, 480)          # two rows, generous spacing so long runs have a free lane

GATES = {
    #  ref     kind          loc                    (x1 and a1 share a column at different rows)
    'x1':   ('XOR Gate', (COL_0, ROWS[0])),
    'a1':   ('AND Gate', (COL_0, ROWS[1])),
    'sg':   ('XOR Gate', (COL_1, ROWS[0])),
    'a2':   ('AND Gate', (COL_1, ROWS[1])),
    'cg':   ('OR Gate',  (COL_2, (ROWS[0] + ROWS[1]) // 2)),
}
# Port names used by the netlist, in the order the measured port list reports them.
GATE_PORTS = {
    'XOR Gate': [('out', 'OUT', 0), ('in0', 'IN', 1), ('in1', 'IN', 2)],
    'AND Gate': [('out', 'OUT', 0), ('in0', 'IN', 1), ('in1', 'IN', 2)],
    'OR Gate':  [('out', 'OUT', 0), ('in0', 'IN', 1), ('in1', 'IN', 2)],
}

INPUT_PINS = [('A', 200), ('B', 300), ('Cin', 400)]
OUTPUT_PINS = ['Sum', 'Cout']


def gate_ports(ref: str) -> Dict[str, Tuple[int, int]]:
    """Absolute coordinates of every named port, straight from the measured geometry."""
    kind, loc = GATES[ref]
    g = geom.geometry(kind, {})
    out = {}
    for name, _ty, idx in GATE_PORTS[kind]:
        p = g['ports'][idx]
        out[name] = (loc[0] + p['dx'], loc[1] + p['dy'])
    return out


def gate_box(ref: str):
    kind, loc = GATES[ref]
    b = geom.geometry(kind, {})['bounds']
    return (ref, loc[0] + b['dx'], loc[1] + b['dy'], b['w'], b['h'])


def pin_ports() -> Dict[str, Tuple[int, int]]:
    """A Pin's single port is at its own loc (measured)."""
    return {name: (COL_PIN, y) for name, y in INPUT_PINS}


def output_pin_xy(name: str, driver: Tuple[int, int]) -> Tuple[int, int]:
    """Output pins sit on their driver's row, far right (the placement the project already proves)."""
    return (COL_OUT, driver[1])


def build_netlist():
    gp = {ref: gate_ports(ref) for ref in GATES}
    pp = pin_ports()
    out_xy = {
        'Sum': output_pin_xy('Sum', gp['sg']['out']),
        'Cout': output_pin_xy('Cout', gp['cg']['out']),
    }
    nets: List[RoutedNet] = []

    def add(net, driver_xy, sinks):
        nets.append((net, driver_xy, [s[1] for s in sinks], [s[0] for s in sinks]))

    add('A', pp['A'], [('x1.in0', gp['x1']['in0']), ('a1.in0', gp['a1']['in0'])])
    add('B', pp['B'], [('x1.in1', gp['x1']['in1']), ('a1.in1', gp['a1']['in1'])])
    add('Cin', pp['Cin'], [('sg.in1', gp['sg']['in1']), ('a2.in1', gp['a2']['in1'])])
    add('n_x1', gp['x1']['out'], [('sg.in0', gp['sg']['in0']), ('a2.in0', gp['a2']['in0'])])
    add('n_a1', gp['a1']['out'], [('cg.in0', gp['cg']['in0'])])
    add('n_a2', gp['a2']['out'], [('cg.in1', gp['cg']['in1'])])
    add('Sum', gp['sg']['out'], [('out:Sum', out_xy['Sum'])])
    add('Cout', gp['cg']['out'], [('out:Cout', out_xy['Cout'])])
    return nets, gp, pp, out_xy


def to_xml(routes, out_xy) -> str:
    L = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
         '<project source="5.1.0dev" version="1.0">',
         f'  <lib desc="#Wiring" name="{WIRING_LIB}"/>',
         f'  <lib desc="#Gates" name="{GATES_LIB}"/>',
         '  <main name="main"/>',
         '  <circuit name="main">']
    # components
    for name, y in INPUT_PINS:
        L.append(f'    <comp lib="{WIRING_LIB}" loc="({COL_PIN},{y})" name="Pin">'
                 f'<a name="label" val="{name}"/><a name="type" val="input"/></comp>')
    for ref, (kind, loc) in GATES.items():
        L.append(f'    <comp lib="{GATES_LIB}" loc="({loc[0]},{loc[1]})" name="{kind}"></comp>')
    for name in OUTPUT_PINS:
        x, y = out_xy[name]
        L.append(f'    <comp lib="{WIRING_LIB}" loc="({x},{y})" name="Pin">'
                 f'<a name="label" val="{name}"/><a name="type" val="output"/>'
                 f'<a name="facing" val="west"/></comp>')
    # wires: every connection is a real segment
    for r in routes:
        for a, b in r.segments:
            L.append(f'    <wire from="({a[0]},{a[1]})" to="({b[0]},{b[1]})"/>')
    L += ['  </circuit>', '</project>']
    return '\n'.join(L) + '\n'


_NETS_FOR_CHECK = []

def main() -> int:
    nets, gp, pp, out_xy = build_netlist()
    global _NETS_FOR_CHECK
    _NETS_FOR_CHECK = [(n, d, ss) for n, d, ss in nets]
    column_xs = [COL_PIN, COL_0, COL_1, COL_2, COL_OUT]
    # Horizontal lanes BETWEEN the component rows: a long run that would otherwise sit on a
    # component's own row is moved onto one of these.
    lanes = [60, 340, 620]
    r = Router(column_xs=column_xs, lanes=lanes)
    boxes = [gate_box(ref) for ref in GATES]
    # input/output pins are components too - the router must not run a wire through them either
    for _n, _y in INPUT_PINS:
        boxes.append((f'Pin({_n})', COL_PIN - 20, _y - 10, 20, 20))
    for _n, (_x, _y) in out_xy.items():
        boxes.append((f'Pin({_n})', _x, _y - 10, 20, 20))
    routes = []
    for net, driver_xy, sink_xys, sink_names in nets:
        routes.append(r.route(RoutedNet(net, '', '', [], 1), driver_xy, sink_xys, boxes))
    problems = r.audit(boxes) + r.endpoint_audit({})

    print('  元件与端口（全部来自实测几何）')
    for ref, (kind, loc) in GATES.items():
        b = geom.geometry(kind, {})
        print(f'    {ref:4} {kind:9} loc={loc}  bounds d({b["bounds"]["dx"]},{b["bounds"]["dy"]}) '
              f'{b["bounds"]["w"]}x{b["bounds"]["h"]}')
        print('         ' + '  '.join(f'{n}={gate_ports(ref)[n]}' for n, _t, _i in GATE_PORTS[kind]))
    print()
    print(f'  {"net":6} {"driver":>12} {"sinks":>26}  segs  len  trunk')
    for rr in routes:
        print(f'  {rr.net:6} {str(rr.driver):>12} {str(rr.sinks):>26}  {len(rr.segments):4} '
              f'{rr.length:4}  {rr.trunk_x}')
    total_segs = sum(len(rr.segments) for rr in routes)
    print()
    print(f'  网络数={len(routes)}  导线段数={total_segs}  Tunnel 数=0')
    if problems:
        print(f'  !! {len(problems)} 个布线问题')
        for p in problems[:12]:
            print('    - ' + p)
    else:
        print('  OK: 没有导线穿过元件，所有端点都在端口/干线/分支点上')

    out = ROOT / 'output' / 'fulladder_wired.circ'
    out.parent.mkdir(parents=True, exist_ok=True)
    _ph.write_circ(out, to_xml(routes, out_xy))
    print(f'  wrote {out.relative_to(ROOT)}')

    import json as _json
    import netcheck as _nc
    nl = [[n, list(d), [list(s) for s in ss]] for n, d, ss in _NETS_FOR_CHECK]
    (out.parent / (out.stem + '.netlist.json')).write_text(
        _json.dumps(nl, indent=1), encoding='utf-8')
    rep = _nc.check(out, _NETS_FOR_CHECK, verbose=True)
    if rep['problems']:
        print(f"  !! NETCHECK: {len(rep['problems'])} problem(s)")
        return 1
    print('  NETCHECK OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
