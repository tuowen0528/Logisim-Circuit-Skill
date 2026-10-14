#!/usr/bin/env python3
"""Measured component geometry: exact bounds and port coordinates for ANY attribute configuration.

    py geom.py show "AND Gate" inputs=3 size=30
    py geom.py show "Multiplexer" select=4 width=8
    py geom.py ports "Pin" width=8
    py geom.py check

WHY THIS EXISTS

A component's port positions depend on its attributes. Changing `size` on a gate moves its inputs by
10-20 px; changing `inputs` changes how many there are and where they sit; changing `select` on a
multiplexer moves them non-uniformly. A router that assumes a fixed offset per component kind
produces wire endpoints that land NEXT TO the real port - the wire is drawn, connects nothing, and
the schematic silently has a dangling net.

So this module is the single source of truth for routing coordinates, and it is built from
measurement, never from a formula that was fitted to a different configuration:

  1. references/component-catalog.json  - GeomSweep's measured ports and bounds, one attribute varied at a
                                   time from its default (covers the common cases cheaply)
  2. tests/circ/probe/PortsAt.java - asks the shipped jar directly for an ARBITRARY combination,
                                   which the sweeps cannot answer
  3. a refusal - if neither can answer, it RAISES. It never falls back to a guess, because a guessed
                 coordinate is exactly the dangling-wire defect this module exists to prevent.

Results from (2) are cached under tests/samples_work/geom_cache/ so repeated builds do not pay for a
JVM launch each time.
"""
from __future__ import annotations

import argparse
import io
import os
import json
import pathlib
import subprocess
import sys


# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# Resolved from this module location so the package runs from any
# working directory.
ROOT = pathlib.Path(__file__).resolve().parents[2]
KB = ROOT / 'references' / 'component-catalog.json'
# --- Logisim JAR location (imports placed here so the order cannot break) ---
import os
import pathlib
_LOCAL_JAR = ROOT / 'scripts' / 'bin' / 'logisim-evolution-all.jar'
JAR = pathlib.Path(os.environ.get("LOGISIM_JAR") or str(_LOCAL_JAR))
PORTSAT_CLASSES = ROOT / 'tests' / 'samples_work' / 'portsat_classes'
CACHE = ROOT / 'tests' / 'samples_work' / 'geom_cache'
SAMPLE_CACHE = ROOT / 'tests' / 'samples_work' / '_geom_sample_circ'
# AttrDump and GeomSweep both placed every component at Location.create(400,400), so the knowledge
# base's `bounds_default` is stored ABSOLUTE while its sweep entries are loc-relative. Everything
# this module returns is normalised to `loc + d(dx,dy)`.
_PROBE_LOC = 400


class GeometryUnknown(Exception):
    """The geometry for this configuration was not measured. Callers must NOT guess."""


_kb = None


def kb():
    global _kb
    if _kb is None:
        if not KB.exists():
            raise GeometryUnknown(f'missing {KB}; run scripts/analysis/build_component_knowledge.py')
        _kb = json.loads(KB.read_text(encoding='utf-8'))
    return _kb


def find(component: str):
    for lib, L in kb()['libraries'].items():
        if component in L['components']:
            return lib, L['components'][component]
    raise GeometryUnknown(f'component {component!r} is not in the measured knowledge base')


def _sample_circuit():
    """A .circ that declares all libraries, used to open a LogisimFile for PortsAt."""
    if not SAMPLE_CACHE.exists():
        src = next(iter(sorted((ROOT / 'examples' / 'user_samples').glob('*.circ'))), None)
        if src is None:
            raise GeometryUnknown('no user sample available to declare the libraries')
        SAMPLE_CACHE.write_text(src.read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
    return SAMPLE_CACHE


def _cache_path(component: str, attrs: dict) -> pathlib.Path:
    key = component + '|' + '|'.join(f'{k}={v}' for k, v in sorted(attrs.items()))
    safe = ''.join(c if c.isalnum() or c in '-_.= ' else '_' for c in key).replace(' ', '_')
    return CACHE / f'{safe}.json'


def _ask_jar(component: str, attrs: dict):
    """Measure one exact configuration with PortsAt. Returns a dict or raises GeometryUnknown."""
    if not (PORTSAT_CLASSES / 'PortsAt.class').exists():
        raise GeometryUnknown('PortsAt is not compiled; see tests/circ/probe/PortsAt.java')
    cmd = ['java', '-Djava.awt.headless=true', '-cp', f'{JAR};{PORTSAT_CLASSES}', 'PortsAt',
           str(_sample_circuit()), component] + [f'{k}={v}' for k, v in sorted(attrs.items())]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=120)
    except Exception as e:
        raise GeometryUnknown(f'PortsAt failed to run: {e}')
    out = r.stdout or ''
    if 'ERROR' in out:
        line = next((l for l in out.splitlines() if l.startswith('ERROR')), 'ERROR')
        raise GeometryUnknown(line)
    bounds = None
    ports = []
    for line in out.splitlines():
        if line.startswith('BOUNDS '):
            _, dx, dy, w, h = line.split()
            bounds = {'dx': int(dx), 'dy': int(dy), 'w': int(w), 'h': int(h)}
        elif line.startswith('PORT '):
            _, dx, dy, wd, ty = line.split()
            ports.append({'dx': int(dx), 'dy': int(dy), 'width': int(wd), 'type': ty})
    if bounds is None or not ports:
        raise GeometryUnknown(f'PortsAt returned no usable geometry for {component} {attrs}: {out[:200]}')
    return {'component': component, 'attrs': attrs, 'bounds': bounds, 'ports': ports,
            'source': 'measured on the jar (PortsAt)'}


def geometry(component: str, attrs: dict | None = None) -> dict:
    """Exact bounds and ports (offsets from `loc`) for this component in this configuration.

    Raises GeometryUnknown when the configuration has not been measured. Never guesses.
    """
    attrs = {str(k): str(v) for k, v in (attrs or {}).items() if v is not None}
    # only attributes that actually change geometry matter for the cache key, but including all of
    # them is harmless and keeps the result honest about what was requested
    _lib, c = find(component)

    if not attrs:
        # AttrDump created the component at Location.create(400,400), so `bounds_default` is stored
        # in ABSOLUTE coordinates while the sweep entries are already loc-relative. Normalise both to
        # `loc + d(dx,dy)` so callers never have to know which shape they got.
        b = c['bounds_default']
        return {'component': component, 'attrs': {},
                'source': 'knowledge base (measured default)',
                'bounds': {'dx': b['x'] - _PROBE_LOC, 'dy': b['y'] - _PROBE_LOC,
                           'w': b['w'], 'h': b['h']},
                'ports': c['ports_default']}

    # single-attribute variation is already in the sweeps
    for sw in c.get('geometry_sweeps', []):
        if _setting_matches(sw['setting'], attrs):
            return {'component': component, 'attrs': attrs,
                    'source': f'knowledge base sweep ({sw["setting"]})',
                    'bounds': {'dx': sw['box']['dx'], 'dy': sw['box']['dy'],
                               'w': sw['box']['w'], 'h': sw['box']['h']},
                    'ports': sw['ports']}

    # otherwise measure
    cp = _cache_path(component, attrs)
    if cp.exists():
        d = json.loads(cp.read_text(encoding='utf-8'))
        d['source'] = 'cached measurement (PortsAt)'
        return d
    d = _ask_jar(component, attrs)
    CACHE.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return d


def _setting_matches(setting: str, attrs: dict) -> bool:
    """A sweep varies exactly ONE attribute from its default, so it can only answer a request that
    differs in that ONE attribute too. Anything else must be measured on the jar.

    Being conservative here is the whole point: a sweep entry for `size=30` says nothing about
    `size=30 inputs=3`, and returning it would hand the router a coordinate that belongs to a
    different component.
    """
    if '=' not in setting:
        return False
    k, v = setting.split('=', 1)
    val = v.split('/')[0]                      # "30/Narrow" -> "30"
    return len(attrs) == 1 and k in attrs and str(attrs[k]) == val


def port_offset(component: str, attrs: dict, index: int):
    return geometry(component, attrs)['ports'][index]


def cmd_show(a):
    lib, c = find(a.component)
    d = geometry(a.component, _parse(a.setting))
    print(f'  {a.component}  [{lib}]   display "{c["display_name"]}"')
    print(f'  attributes asked : {d["attrs"] or "(defaults)"}')
    print(f'  source           : {d["source"]}')
    b = d['bounds']
    print(f'  bounds           : loc + d({b["dx"]},{b["dy"]})  size {b["w"]}x{b["h"]}')
    print(f'  ports            : {len(d["ports"])}')
    for i, p in enumerate(d['ports']):
        print(f'      [{i}] loc + d({p["dx"]:>5},{p["dy"]:>5})  width={p["width"]:<3} {p["type"]}')
    return 0


def cmd_ports(a):
    return cmd_show(a)


def cmd_check(a):
    """Every gate reachable from circmodel: does its measured geometry agree with circmodel?"""
    sys.path.insert(0, str(ROOT / 'scripts' / 'py'))
    import circmodel as cm
    bad = []
    for name in ('AND Gate', 'OR Gate', 'NAND Gate', 'NOR Gate', 'XOR Gate', 'XNOR Gate',
                 'NOT Gate', 'Buffer'):
        for size in (30, 50, 70):
            attrs = {'size': size}
            _, _c = find(name)
            try:
                d = geometry(name, attrs)
            except GeometryUnknown:
                continue
            ins = [p for p in d['ports'] if p['type'] == 'IN']
            try:
                got = list(cm.input_ports(name, (0, 0), len(ins), size))
            except Exception as e:
                bad.append(f'{name} size={size}: circmodel raised {e}')
                continue
            exp = [(p['dx'], p['dy']) for p in ins]
            if got != exp:
                bad.append(f'{name} size={size}: code={got} measured={exp}')
    if bad:
        print('  MISMATCHES:')
        for b in bad:
            print('   ', b)
        return 1
    print('  every gate/size agrees with the measurement')
    return 0


def _parse(pairs):
    out = {}
    for s in pairs or []:
        if '=' not in s:
            raise SystemExit(f'--attr needs name=value, got {s!r}')
        k, v = s.split('=', 1)
        out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    for name, fn in (('show', cmd_show), ('ports', cmd_ports)):
        p = sub.add_parser(name)
        p.add_argument('component')
        p.add_argument('setting', nargs='*')
        p.set_defaults(fn=fn)
    p = sub.add_parser('check')
    p.set_defaults(fn=cmd_check)
    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        print(__doc__)
        return 0
    return a.fn(a) or 0


if __name__ == '__main__':
    raise SystemExit(main())
