#!/usr/bin/env python3
"""portdb -- resolve a component's ports and box from the MEASURED catalogue, for any component.

WHY

`ir.py` hand-codes the geometry of 17 part kinds. The catalogue holds measured ports and bounds for all
74 components across the six libraries, plus 1402 single-attribute sweeps showing how they move. So the
generator could only express 17 of the 74 and had no way to reach the rest - which is what "support all
the components of the six libraries" means in practice.

Rather than hand-code 57 more port tables - the duplication that produced two contradicting NOT-gate
offsets in this project - the geometry is READ from the catalogue. One source, already measured.

HOW A LOOKUP WORKS

 1. start from the component's `ports_default`
 2. for each attribute the caller sets, find the sweep that changed THAT attribute to that value and
    take its ports; sweeps are the jar's own measurements, not interpolation
 3. if a requested (attribute, value) has no sweep, REFUSE. Guessing a coordinate puts a wire 20 px
    from the pin it was meant to reach, which floats an input and turns a gate's output into a constant.

A sweep is keyed `attr=value` or `attr=value/Display`, so matching is on the value part.

Ports are addressed by INDEX, because that is what the measurement gives: `portdb.port(xml, attrs, 2)`.
Names exist in the Java source (`Transistor.java:52-54` defines OUTPUT/INPUT/GATE) and are recorded per
component where they have been read, but the index is the coordinate's identity.
"""
from __future__ import annotations

import functools
import json
import pathlib
from typing import Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / 'references' / 'component-catalog.json'

class PortUnknown(Exception):
    """A configuration the jar has not been measured for. Raised instead of interpolating."""

@functools.lru_cache(maxsize=1)
def _catalogue() -> dict:
    return json.loads(CATALOGUE.read_text(encoding='utf-8'))

@functools.lru_cache(maxsize=1)
def _by_xml() -> Dict[str, dict]:
    out = {}
    for lib, l in _catalogue()['libraries'].items():
        for cname, c in l['components'].items():
            out[c.get('xml_name', cname)] = c
    return out

def component(xml_name: str) -> dict:
    c = _by_xml().get(xml_name)
    if c is None:
        raise PortUnknown(f'{xml_name!r} is not in the measured catalogue')
    return c

def known() -> List[str]:
    return sorted(_by_xml())

def _sweep_for(comp: dict, attr: str, value) -> Optional[dict]:
    """The measured sweep that set this one attribute to this value."""
    want = str(value)
    for s in comp.get('geometry_sweeps', []):
        setting = s.get('setting', '')
        if not setting.startswith(attr + '='):
            continue
        got = setting.split('=', 1)[1]
        # a sweep may be keyed `value/DisplayName`; match the value part
        if got.split('/')[0] == want or got == want:
            return s
    return None

def ports(xml_name: str, attrs: Optional[Dict] = None, allow_composed: bool = False) -> List[dict]:
    """Resolved ports as [{index, dx, dy, width, type}].

    THE COMPOSITION RULE, AND WHY IT REFUSES

    Every geometry sweep in the catalogue was measured by changing ONE attribute away from its default.
    So `width=4` was measured with `shift` at its default, and `shift=ll` was measured with `width` at
    its default 8. Setting BOTH is a configuration the jar was never measured in, and applying the two
    sweeps one after another silently produces the LAST one's geometry: a Shifter asked for width=4 and
    shift=ll comes back with 8-bit ports, because the shift sweep reset them.

    A wrong port coordinate puts a wire 20 px from the pin it meant to reach. That floats the input, and
    a floating input turns a gate's output into a constant - the failure this whole project is built to
    avoid. So a combination is REFUSED rather than composed.

    Vary one attribute per case, or pass `allow_composed=True` to opt in and get the last sweep's
    geometry with `composed=True` on the result.
    """
    comp = component(xml_name)
    attrs = {k: v for k, v in (attrs or {}).items() if v is not None}

    # which of these are actually away from the measured default?
    nondefault = []
    for attr, value in attrs.items():
        dflt = None
        for a in comp.get('attributes', []):
            if a['name'] == attr:
                dflt = a.get('default')
                dflt = dflt.get('value') if isinstance(dflt, dict) else dflt
                break
        if str(dflt) != str(value):
            nondefault.append(attr)

    if len(nondefault) > 1 and not allow_composed:
        raise PortUnknown(
            f'{xml_name}: {len(nondefault)} attributes are away from their defaults '
            f'({", ".join(nondefault)}), and every sweep in the catalogue changes exactly ONE. '
            f'Composing them would return whichever sweep ran last - a Shifter at width=4 shift=ll '
            f'comes back with 8-bit ports. Vary one attribute per lookup, or pass allow_composed=True '
            f'if you accept that the result is the last sweep\'s geometry.')

    result = [dict(p) for p in comp.get('ports_default', [])]
    unmeasured = []
    composed = False
    for attr, value in attrs.items():
        s = _sweep_for(comp, attr, value)
        if s is None:
            for a in comp.get('attributes', []):
                if a['name'] == attr:
                    dflt = a.get('default')
                    dflt = dflt.get('value') if isinstance(dflt, dict) else dflt
                    if str(dflt) == str(value):
                        s = '__default__'
                    break
            if s is None:
                unmeasured.append(f'{attr}={value}')
                continue
        if s == '__default__':
            continue
        result = [dict(p) for p in s.get('ports', result)]
        composed = len(nondefault) > 1
    if unmeasured:
        raise PortUnknown(
            f'{xml_name}: the jar has not been measured with {", ".join(unmeasured)}. Sweeps exist for: '
            + ', '.join(sorted({x['setting'].split('=')[0] for x in comp.get('geometry_sweeps', [])}))
            + '. Measure it before using it - an unmeasured coordinate floats the pin.')
    if composed:
        for p in result:
            p['composed'] = True
    return result

def box(xml_name: str, attrs: Optional[Dict] = None) -> Tuple[int, int, int, int]:
    """Resolved drawing box (dx, dy, w, h) relative to loc, from the default plus matching sweeps."""
    comp = component(xml_name)
    attrs = attrs or {}
    b = dict(comp.get('bounds_default', {}))
    dx = b.get('x', 0)
    dy = b.get('y', 0)
    w = b.get('w', 0)
    h = b.get('h', 0)
    for attr, value in attrs.items():
        if value is None:
            continue
        s = _sweep_for(comp, attr, value)
        if s and s.get('box'):
            sb = s['box']
            dx, dy, w, h = sb.get('dx', dx), sb.get('dy', dy), sb.get('w', w), sb.get('h', h)
    return (dx, dy, w, h)

def port(xml_name: str, attrs: Optional[Dict], index: int) -> Tuple[int, int]:
    """Offset (dx, dy) of the port at `index`, measured."""
    ps = ports(xml_name, attrs)
    if index < 0 or index >= len(ps):
        raise PortUnknown(f'{xml_name} has {len(ps)} ports; index {index} was asked for')
    p = ps[index]
    return (p['dx'], p['dy'])

def port_width(xml_name: str, attrs: Optional[Dict], index: int) -> int:
    ps = ports(xml_name, attrs)
    if index < 0 or index >= len(ps):
        raise PortUnknown(f'{xml_name} has {len(ps)} ports; index {index} was asked for')
    return ps[index].get('width', 1)

def sweep_axes(xml_name: str) -> Dict[str, List[str]]:
    """Which attributes have measured sweeps and for which values."""
    comp = component(xml_name)
    out: Dict[str, List[str]] = {}
    for s in comp.get('geometry_sweeps', []):
        setting = s.get('setting', '')
        if '=' not in setting:
            continue
        attr, val = setting.split('=', 1)
        out.setdefault(attr, []).append(val.split('/')[0])
    return out

if __name__ == '__main__':
    import sys
    print(f'  catalogue components: {len(known())}')
    if len(sys.argv) > 1:
        x = sys.argv[1]
        print(f'  {x}:')
        print(f'    sweeps      : {sweep_axes(x)}')
        for i, p in enumerate(ports(x)):
            print(f'    port[{i}]  d=({p["dx"]:>5},{p["dy"]:>5}) w={p["width"]:<3} {p["type"]}')
        print(f'    box         : {box(x)}')
