#!/usr/bin/env python3
"""compdb - query the measured component knowledge base before generating or editing a circuit.

This is how the Skill USES the six-library research instead of only storing it. Ask it what exists,
what an attribute accepts, and where the pins actually are - rather than assuming any of it.

    py compdb.py libs
    py compdb.py list [--lib Plexers]
    py compdb.py show "Multiplexer"
    py compdb.py ports "AND Gate" --attr inputs=3 --attr size=30
    py compdb.py check "Adder" width=8 mode=unsigned
    py compdb.py traps
    py compdb.py where "Adder"                 # which library, and is it rotatable
    py compdb.py bench "Multiplier" --attr width=4

Every value comes from references/component-catalog.json, which was measured out of the shipped jar by
tests/circ/probe/AttrDump.java and GeomSweep.java. Anything not measured is reported as NOT MEASURED
rather than guessed, and a name or value that does not exist is an ERROR, never a silent fallback.
"""
from __future__ import annotations

import argparse
import difflib
import io
import json
import pathlib
import sys

# Only wrap when RUN, never when imported: a second wrapper around the
# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# package root, resolved from this file so the package is portable
import os
ROOT = pathlib.Path(__file__).resolve().parents[2]
KB_PATH = ROOT / 'references' / 'component-catalog.json'

LIBS = ['Wiring', 'Gates', 'Plexers', 'Arithmetic', 'FPArithmetic', 'Memory']
LIB_DISPLAY = {'Wiring': '#Wiring', 'Gates': '#Gates', 'Plexers': '#Plexers',
               'Arithmetic': '#Arithmetic', 'FPArithmetic': '#FPArithmetic', 'Memory': '#Memory'}

def kb():
    if not KB_PATH.exists():
        raise SystemExit(f'missing {KB_PATH} - run ')
    return json.loads(KB_PATH.read_text(encoding='utf-8'))

def find(d, name):
    """Exact XML-name lookup. Case-insensitive fallback is reported, never applied silently."""
    for lib in LIBS:
        C = d['libraries'][lib]['components']
        if name in C:
            return lib, C[name], True
    low = name.lower()
    for lib in LIBS:
        for k, v in d['libraries'][lib]['components'].items():
            if k.lower() == low:
                return lib, v, False
    # suggest close matches so a typo does not look like "component does not exist"
    names = [k for lib in LIBS for k in d['libraries'][lib]['components']]
    # difflib catches a single transposed or missing letter, which substring matching does not:
    # "Multiplexr" does not contain and is not contained by "Multiplexer".
    cand = difflib.get_close_matches(name, names, n=6, cutoff=0.6)
    if not cand:
        cand = [k for k in names if low in k.lower() or k.lower() in low]
    msg = f'no component named {name!r} in the six libraries'
    if cand:
        msg += f'; did you mean: {", ".join(sorted(cand)[:6])}'
    raise SystemExit(msg)

def parse_attrs(d, c, specs):
    """Validate --attr names against the MEASURED attribute set and values against legal values."""
    known = {a['name']: a for a in c['attributes']}
    out = {}
    for s in specs:
        if '=' not in s:
            raise SystemExit(f'--attr needs name=value, got {s!r}')
        k, v = s.split('=', 1)
        if k not in known:
            raise SystemExit(f'{c["xml_name"]} has no attribute {k!r}. Measured attributes: '
                             f'{sorted(known)}')
        a = known[k]
        legal = a.get('legal_values')
        if legal:
            vals = [x.split('(disp=')[0] for x in legal]
            if v not in vals:
                raise SystemExit(f'{c["xml_name"]}.{k} = {v!r} is not legal. Legal values: {vals}')
        b = a.get('bounds')
        if b:
            try:
                iv = int(v)
                lo = b.get('min', b.get('start'))
                hi = b.get('max', b.get('end'))
                if lo is not None and iv < lo:
                    raise SystemExit(f'{c["xml_name"]}.{k} = {iv} below measured minimum {lo}')
                if hi is not None and iv > hi:
                    raise SystemExit(f'{c["xml_name"]}.{k} = {iv} above measured maximum {hi}')
            except ValueError:
                raise SystemExit(f'{c["xml_name"]}.{k} expects an integer, got {v!r}')
        out[k] = v
    return out

def ports_for(c, attrs):
    """Ports under a setting.

    The sweeps measured ONE attribute changed at a time from its default. So a single-attribute
    override can be answered from measurement; anything beyond that falls back to the default port
    list and SAYS SO, because inventing a combined geometry would be a guess.
    """
    if not attrs:
        return c['ports_default'], 'default (measured)'
    if len(attrs) == 1:
        k, v = next(iter(attrs.items()))
        for sw in c['geometry_sweeps']:
            if sw['setting'] == f'{k}={v}':
                return sw['ports'], f'measured sweep "{sw["setting"]}"'
        # a swept attribute may normalise the value (e.g. BitWidth renders as 8)
        for sw in c['geometry_sweeps']:
            if sw['setting'].startswith(k + '='):
                sv = sw['setting'].split('=', 1)[1]
                if sv == v or sv.split('/')[0] == v:
                    return sw['ports'], f'measured sweep "{sw["setting"]}"'
    return None, (f'NOT MEASURED for {attrs} - the sweeps vary one attribute at a time from its '
                  f'default, so a combination is unknown. Do not guess: build a bench and measure.')

def fmt_attr(a):
    bits = [f"{a['name']:16} {a['impl']:22} default={a['default']}"]
    if 'legal_values' in a:
        bits.append(f"legal=[{', '.join(a['legal_values'])}]")
    if 'bounds' in a:
        b = a['bounds']
        rng = (b.get('start', b.get('min')), b.get('end', b.get('max')))
        bits.append(f"range={rng[0]}..{rng[1]}")
    if a.get('choice_arrays', {}).get('choices'):
        bits.append(f"choices={a['choice_arrays']['choices'][:8]}")
    return '\n      '.join(bits)

def cmd_libs(d, a):
    tot = 0
    print(f"  {'library':14} {'lib desc':16} {'components':>10}")
    print('  ' + '-' * 44)
    for lib in LIBS:
        n = d['libraries'][lib]['component_count']
        tot += n
        print(f'  {lib:14} {LIB_DISPLAY[lib]:16} {n:>10}')
    print('  ' + '-' * 44)
    print(f'  {"TOTAL":14} {"":16} {tot:>10}')
    print()
    print(f"  measured: {d['totals']['attributes']} attributes, "
          f"{d['totals']['geometry_sweeps']} geometry sweeps, {d['totals']['gap_count']} gaps")

def cmd_list(d, a):
    libs = [a.lib] if a.lib else LIBS
    for lib in libs:
        if lib not in LIBS:
            raise SystemExit(f'unknown library {lib!r}; known: {LIBS}')
        C = d['libraries'][lib]['components']
        print(f'  [{lib}] {len(C)} components')
        for n, c in C.items():
            fa = 'rotatable' if any(x['name'] == 'facing' for x in c['attributes']) else 'FIXED orientation'
            print(f'      {n:24} {len(c["attributes"]):2} attrs  {len(c["ports_default"]):2} ports  {fa}')
        print()

def cmd_purposes(d, a):
    """Every component's one-line purpose, so an agent can learn the whole catalogue in one call.

    `show` answers "what is this component"; this answers "what components are there and what does each
    one do", which is the question an agent actually starts with. The traps are printed too, because
    they are the difference between using a component and using it correctly.
    """
    want = [a.lib] if getattr(a, 'lib', None) else LIBS
    shown = 0
    for lib in want:
        if lib not in LIBS:
            raise SystemExit(f'unknown library {lib!r}; known: {LIBS}')
        C = d['libraries'][lib]['components']
        print(f'  === {lib} ({len(C)}) ===')
        for n in sorted(C):
            c = C[n]
            p = c.get('purpose') or '(no purpose recorded)'
            print(f'  {n}')
            print(f'      {p}')
            if getattr(a, 'traps', False) and c.get('traps'):
                print(f'      ! {c["traps"]}')
            shown += 1
        print()
    print(f'  {shown} components. `compdb show <name>` for attributes, ports and geometry.')

def cmd_purposes_traps(d, a):
    """Every recorded trap, on its own. These are the silent-failure modes."""
    n = 0
    for lib in LIBS:
        for name in sorted(d['libraries'][lib]['components']):
            c = d['libraries'][lib]['components'][name]
            if not c.get('traps'):
                continue
            print(f'  [{lib}] {name}')
            print(f'      {c["traps"]}')
            n += 1
    print(f'\n  {n} components have a recorded trap.')

def cmd_show(d, a):
    lib, c, exact = find(d, a.component)
    if not exact:
        print(f'  NOTE: matched case-insensitively; the XML name is exactly {c["xml_name"]!r}')
    print(f'  {c["xml_name"]}   [{lib}]   display "{c["display_name"]}"')
    print(f'  factory class : {c["factory_class"]}')

    # WHAT IT DOES AND HOW TO USE IT. Without these the entry says a component exists and what its
    # ports are, and says nothing about what it computes - which is how a circuit gets built that
    # loads, simulates, and computes the wrong thing.
    if c.get('purpose'):
        print(f'  purpose       : {c["purpose"]}')
    if c.get('usage'):
        print(f'  usage         : {c["usage"]}')
    if c.get('traps'):
        print(f'  ⚠ traps       : {c["traps"]}')
    if c.get('behaviour_at'):
        print(f'  behaviour at  : {c["behaviour_at"]}')
    sv = c.get('sim_verified')
    if sv:
        print(f'  VERIFIED      : {sv["rows_compared"]} rows compared against an independent golden '
              f'expression, in the real simulator  (cases: {", ".join(sv["cases"])})')
    else:
        print(f'  VERIFIED      : NO - the recorded behaviour has not been proven by simulation. '
              f'Treat it as a sourced claim, not a measured one.')

    print(f'  rotatable     : {"yes - has facing" if any(x["name"]=="facing" for x in c["attributes"]) else "NO - orientation is fixed"}')
    print(f'  appearance    : {"present" if any(x["name"]=="appearance" for x in c["attributes"]) else "absent"}')
    print(f'  attributes    : {len(c["attributes"])}')
    for x in c['attributes']:
        print(f'    - {fmt_attr(x)}')
    b = c['bounds_default']
    print(f'  default box   : x{b["x"]} y{b["y"]} w{b["w"]} h{b["h"]}   '
          f'(loc-relative: {b["x"] and ""}d({b["x"] - 0},{b["y"]}))')
    print(f'  ports @default: {len(c["ports_default"])}')
    for p in c['ports_default']:
        print(f'      d=({p["dx"]:>5},{p["dy"]:>5}) width={p["width"]:<3} {p["type"]}')
    print(f'  geometry sweeps available: {len(c["geometry_sweeps"])}'
          f'  (use `ports` to query one)')
    if c.get('mutable_attribute_set'):
        m = c['mutable_attribute_set']
        print(f'  ⚠ MUTABLE attribute set: {m["count_before"]} -> {m["count_after_sweep"]}')
    if c.get('gaps'):
        for g in c['gaps']:
            print(f'  ⚠ GAP: {g}')

def cmd_ports(d, a):
    lib, c, _ = find(d, a.component)
    attrs = parse_attrs(d, c, a.attr)
    ports, how = ports_for(c, attrs)
    print(f'  {c["xml_name"]} [{lib}]' + (f'  attrs={attrs}' if attrs else ''))
    if ports is None:
        print(f'  ⚠ {how}')
        print(f'  default ports for reference (NOT the requested configuration):')
        ports = c['ports_default']
        how = 'default - shown only for reference'
    print(f'  source: {how}')
    print(f'  {"offset":>16} {"width":>6}  type')
    for p in ports:
        print(f'    d=({p["dx"]:>5},{p["dy"]:>5}) {p["width"]:>6}  {p["type"]}')

def cmd_check(d, a):
    pairs = []
    for s in a.assignments:
        if '=' not in s:
            raise SystemExit(f'expected name=value, got {s!r}')
        pairs.append(s)
    lib, c, _ = find(d, a.component)
    attrs = parse_attrs(d, c, pairs)
    print(f'  OK - {c["xml_name"]} accepts {attrs}')
    ports, how = ports_for(c, attrs)
    if ports is None:
        print(f'  ⚠ ports: {how}')
    else:
        print(f'  ports ({how}):')
        for p in ports:
            print(f'    d=({p["dx"]:>5},{p["dy"]:>5}) width={p["width"]:<3} {p["type"]}')

def cmd_traps(d, a):
    idx = json.loads((ROOT / 'references' / 'component-index.json').read_text(encoding='utf-8'))
    print('  ⚠ The XML name is NOT the display name. Using the display name is SILENTLY DROPPED.')
    print()
    for xmlname, disp in idx['naming_traps']['use_xml_name_not_display'].items():
        print(f'    use {xmlname:16} not "{disp}"')
    print()
    print('  ⚠ Hyphenated names the project got wrong (measured: silently dropped):')
    for n, note in idx['naming_traps']['hyphenated_names_the_project_currently_gets_wrong'].items():
        print(f'    {n:16} {note}')
    print()
    print('  ⚠ Components with NO facing attribute cannot be rotated:')
    for lib in LIBS:
        fixed = [n for n, c in d['libraries'][lib]['components'].items()
                 if not any(x['name'] == 'facing' for x in c['attributes'])]
        if fixed:
            print(f'    [{lib}] {len(fixed)}: {", ".join(sorted(fixed))}')

def cmd_where(d, a):
    lib, c, _ = find(d, a.component)
    print(f'  {c["xml_name"]} -> library {lib} (file lib desc {LIB_DISPLAY[lib]})')

def cmd_bench(d, a):
    import subprocess
    cmd = [sys.executable, str(ROOT / 'scripts' / 'py' / 'mkbench.py'), a.component,
           str(ROOT / 'tests' / 'samples_work' / 'bench' / f'{a.component.replace(" ", "_")}.circ')]
    for s in a.attr:
        cmd += ['--attr', s]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout or r.stderr)
    return r.returncode

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('libs'); p.set_defaults(fn=cmd_libs)
    p = sub.add_parser('list'); p.add_argument('--lib'); p.set_defaults(fn=cmd_list)
    p = sub.add_parser('purposes', help='every component and what it does')
    p.add_argument('--lib'); p.add_argument('--traps', action='store_true')
    p.set_defaults(fn=cmd_purposes)
    p = sub.add_parser('alltraps', help='every recorded silent-failure mode')
    p.set_defaults(fn=cmd_purposes_traps)
    p = sub.add_parser('show'); p.add_argument('component'); p.set_defaults(fn=cmd_show)
    p = sub.add_parser('ports'); p.add_argument('component')
    p.add_argument('--attr', action='append', default=[]); p.set_defaults(fn=cmd_ports)
    p = sub.add_parser('check'); p.add_argument('component')
    p.add_argument('assignments', nargs='+'); p.set_defaults(fn=cmd_check)
    p = sub.add_parser('traps'); p.set_defaults(fn=cmd_traps)
    p = sub.add_parser('where'); p.add_argument('component'); p.set_defaults(fn=cmd_where)
    p = sub.add_parser('bench'); p.add_argument('component')
    p.add_argument('--attr', action='append', default=[]); p.set_defaults(fn=cmd_bench)

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        print(__doc__)
        return 0
    d = kb() if a.cmd != 'bench' or True else None
    return a.fn(d, a) or 0

if __name__ == '__main__':
    raise SystemExit(main())
