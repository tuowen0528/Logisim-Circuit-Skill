#!/usr/bin/env python3
"""Read a Tunnel-based .circ back into its electrical netlist.

    py circ2netlist.py <circuit.circ> [out.json]

WHY

A .circ that already simulates correctly is a GROUND-TRUTH netlist. `output/alu8.circ` passes
1,048,576/1,048,576, so whatever connections it declares are right by construction. Extracting them
and diffing against the netlist a wired builder constructs turns "the wired ALU is wrong somehow"
into "these two netlists differ here" - a comparison instead of a search.

MECHANISM (from the generation rules, and visible in the file)

A Tunnel joins by LABEL, so the topology is: one driver tunnel per net, one consumer tunnel per sink.

  * a driver tunnel is placed a fixed stub to the RIGHT of the driving port and joined to it by a wire
    (facing="west"), so following that wire back gives the driving port;
  * a consumer tunnel is placed exactly ON the port it feeds (facing="east"), so its own coordinate IS
    the sink port.

Widths come from the tunnel's `width` attribute, so a net that silently lost bits shows up as a width
mismatch rather than as a mystery.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
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

Point = tuple


def parse(path: pathlib.Path):
    t = path.read_text(encoding='utf-8', errors='replace')
    comps = []
    for m in re.finditer(r'<comp lib="(\d+)" loc="\((-?\d+),(-?\d+)\)" name="([^"]+)"'
                         r'(?:/>|>(.*?)</comp>)', t, re.S):
        lib, x, y, name, body = m.groups()
        attrs = dict(re.findall(r'<a name="([^"]+)" val="([^"]*)"/>', body or ''))
        comps.append({'lib': int(lib), 'loc': (int(x), int(y)), 'name': name, 'attrs': attrs})
    wires = []
    for m in re.finditer(r'<wire from="\((-?\d+),(-?\d+)\)" to="\((-?\d+),(-?\d+)\)"/>', t):
        x1, y1, x2, y2 = map(int, m.groups())
        wires.append(((x1, y1), (x2, y2)))
    return comps, wires


def port_index(comps):
    """{(x,y): (comp_index, port_index, width, type)} via MEASURED geometry, plus Pin specials."""
    import geom
    at = {}
    for i, c in enumerate(comps):
        # A Tunnel is the WIRING mechanism, never an endpoint. Indexing it made every sink
        # coordinate resolve to `Tunnel[0]INOUT` instead of the port it actually feeds.
        if c['name'] in ('Tunnel', 'Splitter'):
            continue
        if c['name'] == 'Pin':
            pts = [(0, 0)]
            w = int(c['attrs'].get('width', '1'))
            ty = 'OUT' if c['attrs'].get('type') == 'input' else 'IN'
            at[(c['loc'][0], c['loc'][1])] = (i, 0, w, ty)
            continue
        try:
            g = geom.geometry(c['name'], c['attrs'])
        except Exception:                                   # noqa: BLE001
            continue
        for pi, p in enumerate(g['ports']):
            at[(c['loc'][0] + p['dx'], c['loc'][1] + p['dy'])] = (i, pi, p['width'], p['type'])
    return at


def extract(path: pathlib.Path):
    import geom
    comps, wires = parse(path)
    at = port_index(comps)
    # wires as adjacency for following a driver stub
    adj = collections.defaultdict(set)
    for a, b in wires:
        adj[a].add(b)
        adj[b].add(a)

    nets = collections.defaultdict(lambda: {'driver': None, 'sinks': [], 'width': None})
    tunnels = [c for c in comps if c['name'] == 'Tunnel']
    for tu in tunnels:
        lab = tu['attrs'].get('label', '?')
        w = tu['attrs'].get('width')
        facing = tu['attrs'].get('facing', 'west')
        n = nets[lab]
        if w and n['width'] is None:
            n['width'] = int(w)
        p = tu['loc']
        if facing == 'east':
            # consumer: sits ON the port it feeds
            if p in at:
                n['sinks'].append(p)
            else:
                n['sinks'].append(p)
        else:
            # driver: follow the stub wire to the component port
            port = None
            for nb in adj.get(p, ()):
                if nb in at:
                    port = nb
                    break
                for nb2 in adj.get(nb, ()):
                    if nb2 in at and nb2 != p:
                        port = nb2
                        break
                if port:
                    break
            n['driver'] = port if port else p
    return comps, at, nets


def label_of(at, comps, pt):
    e = at.get(pt)
    if not e:
        return '?'
    i, pi, w, ty = e
    return f"{comps[i]['name']}@{comps[i]['loc']}[{pi}]{ty}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('circuit')
    ap.add_argument('out', nargs='?')
    a = ap.parse_args()
    p = pathlib.Path(a.circuit)
    comps, at, nets = extract(p)
    counts = collections.Counter(c['name'] for c in comps)
    print(f'  {p.name}: {len(comps)} components {dict(counts)}')
    print(f'  nets: {len(nets)}')
    bad = []
    for lab in sorted(nets):
        n = nets[lab]
        if n['driver'] is None:
            bad.append(f"net {lab!r} has NO driver")
        if not n['sinks']:
            bad.append(f"net {lab!r} has NO sinks")
    for b in bad:
        print('  ! ' + b)
    print()
    print(f'  {"net":10} {"w":>3} {"driver":>34}  sinks')
    for lab in sorted(nets):
        n = nets[lab]
        print(f'  {lab:10} {str(n["width"]):>3} {label_of(at, comps, n["driver"]):>34}  '
              f'{len(n["sinks"])}')
    if a.out:
        out = []
        for lab in sorted(nets):
            n = nets[lab]
            out.append([lab, list(n['driver']) if n['driver'] else None,
                        [list(s) for s in n['sinks']], n['width']])
        pathlib.Path(a.out).write_text(json.dumps(out, indent=1), encoding='utf-8')
        print(f'  wrote {a.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
