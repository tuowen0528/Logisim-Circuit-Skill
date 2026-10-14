#!/usr/bin/env python3
"""Convert a Tunnel-based .circ into a PURE-WIRE .circ, keeping the verified component layout.

    py tunnel2wire.py output\alu8.circ output\alu8_wired.circ

WHY THIS ROUTE

`output/alu8.circ` simulates correctly on all 1,048,576 combinations, so its netlist is ground truth.
`circ2netlist` extracts it, and diffing it against what the spec-driven wired builder constructs showed
the two topologies are IDENTICAL - every driver and every sink, including the 22-sink `zero` net. So
the topology was never the problem, and rebuilding it from a spec only adds a second chance to get it
wrong.

This keeps the tunnel circuit's components exactly where they are (positions that are already known to
work) and replaces only the WIRING: every Tunnel is removed and each net is drawn as real Manhattan
wire between the ports that were already connected.

Logisim's joining rules were measured (tests/samples_work/wiretest/): endpoints coinciding connect, an
endpoint on another's span connects (T junction), collinear overlap merges, and a perpendicular
mid-span crossing does NOT connect. All four are enforced by the router's audits plus `netcheck`.
"""
from __future__ import annotations

import argparse
import collections
import io
import pathlib
import re
import sys

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                                  line_buffering=True)

# Resolved from this module location so the package runs from any
# working directory.
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts' / 'py'))

LIB_ORD = {'#Wiring': 0, '#Gates': 1, '#Plexers': 2, '#Arithmetic': 3, '#FPArithmetic': 4,
           '#Memory': 5, '#I/O': 6, '#TTL': 7, '#TCL': 8, '#Base': 9, '#BFH-Praktika': 10,
           '#Input/Output-Extra': 11, '#Soc': 12}
NAME_TO_LIB = {}
for _n, _l in (('Pin', 0), ('Tunnel', 0), ('Constant', 0), ('Splitter', 0), ('Clock', 0),
               ('AND Gate', 1), ('OR Gate', 1), ('NOT Gate', 1), ('NAND Gate', 1), ('NOR Gate', 1),
               ('XOR Gate', 1), ('XNOR Gate', 1),
               ('Multiplexer', 2), ('Decoder', 2), ('Demultiplexer', 2),
               ('Adder', 3), ('Subtractor', 3), ('Multiplier', 3), ('Comparator', 3)):
    NAME_TO_LIB[_n] = _l


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--verify-spec')
    a = ap.parse_args()
    src = pathlib.Path(a.src)
    dst = pathlib.Path(a.dst)

    import geom
    import netcheck as nc
    import project_header as _ph
    from router import Router, RoutedNet
    import circ2netlist as c2n

    comps, at, nets = c2n.extract(src)
    parts = [c for c in comps if c['name'] != 'Tunnel']
    print(f'  源: {len(comps)} 元件（{len(comps) - len(parts)} 个 Tunnel 将被移除） -> '
          f'{len(parts)} 个真实元件, {len(nets)} 条网络')

    # component boxes for collision checks, from MEASURED geometry
    boxes = []
    portmap = {}
    for i, c in enumerate(parts):
        key = f'{c["name"]}@{c["loc"]}'
        if c['name'] == 'Pin':
            x, y = c['loc']
            w = int(c['attrs'].get('width', '1'))
            ty = 'OUT' if c['attrs'].get('type') == 'input' else 'IN'
            boxes.append((key, x - 20, y - 10, 20, 20))
            portmap[key] = {(x, y)}
            continue
        try:
            g = geom.geometry(c['name'], c['attrs'])
        except Exception:                                     # noqa: BLE001
            g = None
        if g:
            b = g['bounds']
            boxes.append((key, c['loc'][0] + b['dx'], c['loc'][1] + b['dy'], b['w'], b['h']))
            portmap[key] = {(c['loc'][0] + p['dx'], c['loc'][1] + p['dy']) for p in g['ports']}
        else:
            portmap[key] = set()

    # lanes: every y that clears ALL component boxes by a margin, sampled on the 10 px grid
    ys = sorted({y for _k, _x, y, _w, h in boxes} | {y + h for _k, _x, y, _w, h in boxes})
    miny, maxy = (ys[0] - 200) if ys else 0, (ys[-1] + 200) if ys else 2000
    free = []
    for y in range(miny - (miny % 10), maxy, 10):
        if all(not (by - 12 < y < by + bh + 12) for _k, _bx, by, _bw, bh in boxes):
            free.append(y)
    # spread the lanes out so the router has real choices
    lanes = free[::max(1, len(free) // 40)][:60]
    print(f'  候选水平通道: {len(lanes)} 条')

    column_xs = sorted({c['loc'][0] for c in parts})
    r = Router(column_xs=column_xs, lanes=lanes)

    netlist = []
    routes = []
    for lab in sorted(nets):
        n = nets[lab]
        driver = n['driver']
        sinks = [s for s in n['sinks']]
        if driver is None or not sinks:
            print(f'  ! 网络 {lab} 缺驱动或汇点，跳过')
            continue
        w = n['width'] or 1
        routes.append(r.route(RoutedNet(lab, '', '', [], w), driver, sinks, boxes))
        netlist.append((lab, driver, sinks))

    # which net legitimately owns each port point
    owners = {}
    for lab, d, ss in netlist:
        owners[tuple(d)] = lab
        for s in ss:
            owners[tuple(s)] = lab
    problems = (r.audit(boxes, portmap) + r.endpoint_audit({}) + r.collision_audit()
                + r.port_collision_audit(portmap, owners) + r.t_junction_audit()
                + r.overlap_audit())
    print(f'  布线: {sum(len(x.segments) for x in routes)} 段导线, Tunnel 0')
    for p in problems:
        print('  ! ' + p)

    # ---- emit -------------------------------------------------------------------------
    header = src.read_text(encoding='utf-8', errors='replace').split('<circuit')[0]
    L = [header.rstrip('\n')]
    L.append('  <circuit name="main">')
    for c in parts:
        lib = NAME_TO_LIB.get(c['name'], c['lib'])
        at_s = ''.join(f'<a name="{k}" val="{v}"/>' for k, v in c['attrs'].items())
        L.append(f'    <comp lib="{lib}" loc="({c["loc"][0]},{c["loc"][1]})" '
                 f'name="{c["name"]}">{at_s}</comp>')
    seen = set()
    total = 0
    for rr in routes:
        for p, q in rr.segments:
            key = (p, q) if p <= q else (q, p)
            if key in seen:
                continue
            seen.add(key)
            L.append(f'    <wire from="({p[0]},{p[1]})" to="({q[0]},{q[1]})"/>')
            total += 1
    L += ['  </circuit>', '</project>']
    _ph.write_circ(dst, '\n'.join(L) + '\n')
    print(f'  写 {dst.relative_to(ROOT)}: {len(parts)} 元件, {total} 导线, 0 Tunnel')

    import json
    (dst.parent / (dst.stem + '.netlist.json')).write_text(
        json.dumps([[n, list(d), [list(s) for s in ss]] for n, d, ss in netlist], indent=1),
        encoding='utf-8')
    rep = nc.check(dst, netlist, verbose=True)
    if rep['problems']:
        print(f"  !! NETCHECK: {len(rep['problems'])} problem(s)")
        return 1
    print('  NETCHECK OK')
    if problems:
        return 1
    if a.verify_spec:
        print(f'  下一步: py scripts\\py\\verify_model.py {dst} --spec {a.verify_spec} '
              f'--max-rows 1200000')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
