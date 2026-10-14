#!/usr/bin/env python3
"""Connectivity audit: does the GENERATED FILE actually join every net it was supposed to?

    py netcheck.py <circuit.circ> --netlist <netlist.json>
    py netcheck.py <circuit.circ> --netlist <netlist.json> --verbose

WHY THIS EXISTS

Every audit before this one answered a WEAKER question. `endpoint_audit` asked "does some wire
segment end on this port?", which is satisfied by a segment that is not electrically joined to the
driver. The 8-bit ALU proved the gap: all 32 multiplexer data pins had a wire endpoint on them, all
gate inputs had a wire endpoint on them, and yet 7 of 9 operations read as error values. "A wire
touches the pin" is not "the pin is connected".

WHAT THIS DOES

It reads the .circ file that was actually written - not the router's intentions - and reconstructs the
real electrical graph, then answers the question that matters: are the driver and every sink of a net
in ONE connected component?

Logisim's joining rules were MEASURED (tests/samples_work/wiretest/, shipped jar):

    endpoints coincide                 -> CONNECT
    endpoint on another's interior     -> CONNECT (T junction)
    collinear and overlapping          -> CONNECT (merged)
    mid-span perpendicular crossing    -> NOT connected

All four are modelled, because using fewer would reproduce exactly the false confidence this tool is
meant to remove. A component port is a connection point too: a wire passing through it joins it.

The union-find runs over lattice points, and two different intended nets landing in one component is
reported as a SHORT - the failure mode that made Y and Yhi read error bits together.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import re
import sys

# Resolved from this module location so the package runs from any
# working directory.
ROOT = pathlib.Path(__file__).resolve().parents[2]
# Only wrap when RUN, never when imported. `geom` and the builders already make stdout UTF-8 safe;
# a second wrapper around the same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                                  line_buffering=True)
sys.path.insert(0, str(ROOT / 'scripts' / 'py'))

Point = tuple


# ---------------------------------------------------------------------------------------------
# union-find over lattice points


class UF:
    def __init__(self):
        self.p: dict = {}

    def add(self, x):
        self.p.setdefault(x, x)
        return x

    def find(self, x):
        self.add(x)
        r = x
        while self.p[r] != r:
            r = self.p[r]
        while self.p[x] != r:
            self.p[x], x = r, self.p[x]
        return r

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

    def groups(self):
        out = collections.defaultdict(set)
        for x in self.p:
            out[self.find(x)].add(x)
        return out


# ---------------------------------------------------------------------------------------------


def lattice_points(a, b):
    """Every lattice point on segment a-b, ends included. Wires are axis-aligned in Logisim."""
    (x0, y0), (x1, y1) = a, b
    if y0 == y1:
        return [(x, y0) for x in range(min(x0, x1), max(x0, x1) + 1)]
    if x0 == x1:
        return [(x0, y) for y in range(min(y0, y1), max(y0, y1) + 1)]
    return [a, b]


def collinear_overlap(a1, b1, a2, b2) -> bool:
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = a1, b1, a2, b2
    if y1 == y2 and y3 == y4 and y1 == y3:
        lo1, hi1 = sorted((x1, x2))
        lo2, hi2 = sorted((x3, x4))
        return min(hi1, hi2) >= max(lo1, lo2)          # touching counts: they share a point
    if x1 == x2 and x3 == x4 and x1 == x3:
        lo1, hi1 = sorted((y1, y2))
        lo2, hi2 = sorted((y3, y4))
        return min(hi1, hi2) >= max(lo1, lo2)
    return False


class Circuit:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.text = path.read_text(encoding='utf-8', errors='replace')
        self.components = []      # (ref_index, lib, loc, name, attrs)
        self.wires = []           # ((x1,y1),(x2,y2))
        self._parse()

    def _parse(self):
        for m in re.finditer(r'<comp lib="(\d+)" loc="\((-?\d+),(-?\d+)\)" name="([^"]+)"'
                             r'(?:/>|>(.*?)</comp>)', self.text, re.S):
            lib, x, y, name, body = m.groups()
            attrs = dict(re.findall(r'<a name="([^"]+)" val="([^"]*)"/>', body or ''))
            self.components.append((len(self.components), int(lib), (int(x), int(y)), name, attrs))
        for m in re.finditer(r'<wire from="\((-?\d+),(-?\d+)\)" to="\((-?\d+),(-?\d+)\)"/>',
                             self.text):
            x1, y1, x2, y2 = map(int, m.groups())
            self.wires.append(((x1, y1), (x2, y2)))

    def port_map(self):
        """{component index: [(port name/idx, point)]} using MEASURED geometry."""
        import geom
        out = {}
        for idx, _lib, loc, name, attrs in self.components:
            try:
                g = geom.geometry(name, attrs)
            except Exception as e:                       # noqa: BLE001
                out[idx] = []
                out.setdefault('_errors', []).append(f'{name}@{loc}: {e}')
                continue
            pts = []
            for i, p in enumerate(g['ports']):
                pts.append((i, (loc[0] + p['dx'], loc[1] + p['dy']), p['width'], p['type']))
            out[idx] = pts
        return out


def build_graph(c: Circuit, ports):
    uf = UF()
    for a, b in c.wires:
        uf.union(a, b)
    # T junctions and overlaps: compare every pair (charts are small; O(n^2) on ~100 wires is fine)
    for i in range(len(c.wires)):
        a1, b1 = c.wires[i]
        s1 = set(lattice_points(a1, b1))
        for j in range(i + 1, len(c.wires)):
            a2, b2 = c.wires[j]
            if a2 in s1 or b2 in s1 or a1 in set(lattice_points(a2, b2)) or \
                    b1 in set(lattice_points(a2, b2)) or collinear_overlap(a1, b1, a2, b2):
                uf.union(a1, a2)
    # component ports join whatever wire passes through or ends on them
    allpts = set()
    for a, b in c.wires:
        allpts.update(lattice_points(a, b))
    for idx, pts in ports.items():
        if idx == '_errors':
            continue
        for _i, pt, _w, _t in pts:
            uf.add(pt)
            if pt in allpts:
                # find some wire point to union with
                for a, b in c.wires:
                    if pt in set(lattice_points(a, b)):
                        uf.union(pt, a)
                        break
    return uf


def driver_audit(circ_path: pathlib.Path, netlist, verbose: bool = False):
    """Every electrical node must contain at most ONE output (driving) port.

    Two drivers in one node is contention: Logisim returns the value while they agree and `E` once
    they disagree, which is exactly a wrong answer that depends on the operands. A node can acquire a
    second driver without two INTENDED nets merging - if an output port the builder never meant to use
    gets swallowed by a wire passing through or ending on it. Intended-net merging would not catch
    that; counting output ports per node does.
    """
    c = Circuit(circ_path)
    ports = c.port_map()
    ports.pop('_errors', None)
    uf = build_graph(c, ports)

    # which intended net owns each driver/sink point
    owner = {}
    for name, driver, sinks in netlist:
        owner.setdefault(driver, set()).add(name)
        for s in sinks:
            owner.setdefault(s, set()).add(name)

    by_root = {}
    for idx, pts in ports.items():
        comp = c.components[idx]
        for i, pt, width, ptype in pts:
            root = uf.find(pt)
            by_root.setdefault(root, []).append(
                {'comp': idx, 'name': comp[3], 'loc': comp[2], 'port': i,
                 'point': pt, 'width': width, 'type': ptype,
                 'nets': sorted(owner.get(pt, []))})

    problems = []
    for root, plist in sorted(by_root.items(), key=lambda kv: str(kv[0])):
        drivers = [p for p in plist if p['type'] == 'OUT']
        if len(drivers) <= 1:
            continue
        detail = '; '.join(
            f"{p['name']}@{p['loc']} port[{p['port']}] w{p['width']}"
            + (f" (net {p['nets']})" if p['nets'] else ' (NOT part of any intended net)')
            for p in drivers)
        inns = sorted({n for p in plist for n in p['nets']})
        problems.append(f'CONTENTION on node {root}: {len(drivers)} driving ports - {detail} | '
                        f'intended nets in this node: {inns}')
        if verbose:
            print(f'    node {root}: {len(drivers)} drivers')
            for p in drivers:
                print(f"      {p['type']:3} {p['name']}@{p['loc']} port[{p['port']}] "
                      f"w{p['width']} nets={p['nets']}")

    report = {
        'nodes_with_drivers': sum(1 for v in by_root.values()
                                  if any(p['type'] == 'OUT' for p in v)),
        'contention': problems,
    }
    if verbose:
        print(f"  nodes containing a driving port: {report['nodes_with_drivers']}")
        if not problems:
            print('  no node has more than one driving port')
    return report


def check(circ_path: pathlib.Path, netlist, verbose=False):
    c = Circuit(circ_path)
    ports = c.port_map()
    geom_errors = ports.pop('_errors', [])
    uf = build_graph(c, ports)

    # map every port point -> owning component
    port_owner = {}
    for idx, pts in ports.items():
        for _i, pt, _w, _t in pts:
            port_owner.setdefault(pt, []).append(idx)

    problems = []
    if geom_errors:
        problems += [f'GEOMETRY: {e}' for e in geom_errors]

    # 1) each intended net must have its driver and ALL sinks in one component
    net_roots = {}
    for name, driver, sinks in netlist:
        droot = uf.find(driver)
        unreachable = [s for s in sinks if uf.find(s) != droot]
        if unreachable:
            problems.append(f'net {name!r}: driver {driver} is NOT connected to '
                            f'{len(unreachable)}/{len(sinks)} sink(s): {unreachable[:6]}')
        net_roots.setdefault(droot, set()).add(name)

    # 2) two intended nets in one component = SHORT
    for root, nets in sorted(net_roots.items(), key=lambda kv: sorted(kv[1])):
        if len(nets) > 1:
            problems.append(f'SHORT: nets {sorted(nets)} all resolve to one electrical node '
                            f'(root {root})')

    # 3) a component with sinks but no wire at all is simply undriven
    wire_pts = set()
    for a, b in c.wires:
        wire_pts.update(lattice_points(a, b))
    for name, driver, sinks in netlist:
        if driver not in wire_pts:
            problems.append(f'net {name!r}: driver {driver} has NO wire touching it')

    groups = uf.groups()
    report = {
        'circuit': str(circ_path),
        'components': len(c.components),
        'wires': len(c.wires),
        'nets': len(netlist),
        'electrical_nodes': len(groups),
        'problems': problems,
    }
    if verbose:
        print(f"  components={report['components']} wires={report['wires']} "
              f"nets={report['nets']} electrical_nodes={report['electrical_nodes']}")
        for name, driver, sinks in netlist:
            droot = uf.find(driver)
            ok = sum(1 for s in sinks if uf.find(s) == droot)
            print(f'    {name:9} driver={driver!s:>13} sinks={len(sinks):3} reachable={ok:3}'
                  f'{"  OK" if ok == len(sinks) else "  ** BROKEN **"}')
        for p in problems:
            print('    ! ' + p)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('circuit')
    ap.add_argument('--netlist', required=True,
                    help='JSON list of [name, [x,y] driver, [[x,y], ...] sinks]')
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()
    raw = json.loads(pathlib.Path(a.netlist).read_text(encoding='utf-8'))
    netlist = [(n, tuple(d), [tuple(s) for s in ss]) for n, d, ss in raw]
    r = check(pathlib.Path(a.circuit), netlist, verbose=True)
    print(json.dumps({k: v for k, v in r.items() if k != 'problems'}, indent=2))
    if r['problems']:
        print(f"  {len(r['problems'])} PROBLEM(S)")
        return 1
    print('  OK: every intended net is one connected electrical node, and no two nets are joined')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
