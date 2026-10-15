#!/usr/bin/env python3
"""GUI layout audit: would this schematic LOOK broken when opened in Logisim?

    py audit_visual.py <circuit.circ> [<circuit.circ> ...] [--json]

WHY THIS EXISTS

`rtllib.py` proves the ELECTRICAL topology: no two nets share a coordinate, every sink is reachable,
widths agree. `seqmodel.py` checks the wire paths it emits. Neither answers the question a human asks
when the canvas opens: **is anything drawn on top of anything else?**

A tunnel-first schematic is connected entirely by LABELS, so a wall of correctly-wired Tunnels can
still be unreadable - overlapping glyphs, labels running into each other, boxes colliding. Every one of
those passes an electrical check and fails the reader. That gap is what this tool closes.

WHAT IT MEASURES (all from `references/components.json`, measured out of the shipped jar)

    Tunnel    glyph width is 8*len(label)+10 px, height 22 px  <- width DEPENDS ON THE LABEL
    Pin       20 x 20
    Constant  14 x 16
    Splitter  21 x 21
    others    their own measured bounding box

The Tunnel rule is the important one: a long net name makes a wide glyph, which is how two correctly
placed Tunnels end up drawn over each other. A single fixed size cannot detect that.

WHAT IT REPORTS

    OVERLAP      two component boxes intersect          (hard - visibly broken)
    LABEL-CLASH  a drawn label runs into a neighbour    (hard - text unreadable)
    CROWDED      boxes touch or sit within 2 px          (advisory - tight but legible)
    metrics      component count, drawing extent, density

Exit code is 1 when a hard problem is found, so it can gate a build.
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import sys

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                                  line_buffering=True)

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts' / 'py'))

# measured: a Tunnel glyph is 8 px per character of label plus 10 px of padding, 22 px tall
TUNNEL_CHAR_W = 8
TUNNEL_PAD = 10
TUNNEL_H = 22
# measured: a Pin's label is drawn beside its 20x20 box
PIN_CHAR_W = 8
CROWD_GAP = 2


def parse(path: pathlib.Path):
    t = path.read_text(encoding='utf-8', errors='replace')
    comps = []
    for m in re.finditer(r'<comp lib="(\d+)" loc="\((-?\d+),(-?\d+)\)" name="([^"]+)"'
                         r'(?:/>|>(.*?)</comp>)', t, re.S):
        lib, x, y, name, body = m.groups()
        attrs = dict(re.findall(r'<a name="([^"]+)" val="([^"]*)"/>', body or ''))
        comps.append({'lib': int(lib), 'loc': (int(x), int(y)), 'name': name, 'attrs': attrs})
    return comps


def glyph_box(c, geom):
    """Absolute drawing box for one component, using measured geometry, plus its label box."""
    name, attrs, (x, y) = c['name'], c['attrs'], c['loc']
    if name == 'Tunnel':
        label = attrs.get('label', '')
        w = TUNNEL_CHAR_W * len(label) + TUNNEL_PAD
        # a Tunnel's port is at loc; facing decides which way the body extends.
        # east -> body extends LEFT of loc, west (default) -> RIGHT. Measured.
        if attrs.get('facing', 'west') == 'east':
            return (x - w, y - TUNNEL_H // 2, w, TUNNEL_H), None
        return (x, y - TUNNEL_H // 2, w, TUNNEL_H), None
    try:
        g = geom.geometry(name, attrs)
    except Exception:                                        # noqa: BLE001
        return None, None
    b = g['bounds']
    box = (x + b['dx'], y + b['dy'], b['w'], b['h'])
    label_box = None
    lab = attrs.get('label')
    if lab:
        lw = PIN_CHAR_W * len(lab) + 4
        # labels are drawn beside the pin: east-facing -> to the right, west -> to the left
        if attrs.get('facing', 'east') == 'west':
            label_box = (x - lw, y - 8, lw, 16)
        else:
            label_box = (x, y - 8, lw, 16)
    return box, label_box


def overlap(a, b, gap=0):
    return (a[0] < b[0] + b[2] + gap and b[0] < a[0] + a[2] + gap
            and a[1] < b[1] + b[3] + gap and b[1] < a[1] + a[3] + gap)


def audit(path: pathlib.Path, verbose=True):
    import geom
    comps = parse(path)
    boxes = []
    for i, c in enumerate(comps):
        box, lab = glyph_box(c, geom)
        if box is None:
            continue
        boxes.append((i, c['name'], c['loc'], c['attrs'].get('label', ''), box, lab))

    hard, soft = [], []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            ii, n1, l1, lb1, b1, t1 = boxes[i]
            jj, n2, l2, lb2, b2, t2 = boxes[j]
            tag_a = f'{n1}{"(" + lb1 + ")" if lb1 else ""}@{l1}'
            tag_b = f'{n2}{"(" + lb2 + ")" if lb2 else ""}@{l2}'

            # EXCUSED: an OUTPUT Pin co-located with its receiving Tunnel, carrying the same label.
            # This is the documented tunnel-first pattern (measured: output pins MAY share a
            # coordinate with their tunnel; input pins may NOT without an explicit wire), and the
            # two labels are the SAME text, so the reader sees one label rather than two fighting.
            # Flagging it was a false positive, and the project's rule already says not to build
            # checkers that cry wolf.
            if ('Pin' in (n1, n2) and 'Tunnel' in (n1, n2)
                    and l1 == l2 and lb1 and lb1 == lb2):
                continue

            if overlap(b1, b2):
                hard.append(f'OVERLAP: {tag_a} box{b1} intersects {tag_b} box{b2}')
            elif overlap(b1, b2, CROWD_GAP):
                soft.append(f'CROWDED: {tag_a} box{b1} and {tag_b} box{b2} touch within {CROWD_GAP}px')
            # a label drawn over a neighbour's body is unreadable - but the Pin/Tunnel pair above
            # draws one shared label, which is fine
            if t1 and overlap(t1, b2):
                hard.append(f'LABEL-CLASH: label of {tag_a} at box{t1} is drawn over {tag_b} box{b2}')
            if t2 and overlap(t2, b1):
                hard.append(f'LABEL-CLASH: label of {tag_b} at box{t2} is drawn over {tag_a} box{b1}')
            if t1 and t2 and overlap(t1, t2) and lb1 != lb2:
                hard.append(f'LABEL-CLASH: labels of {tag_a} and {tag_b} overlap '
                            f'({t1} vs {t2}) - different text will be unreadable')

    xs = [b[0] for *_r, b, _t in boxes] + [b[0] + b[2] for *_r, b, _t in boxes]
    ys = [b[1] for *_r, b, _t in boxes] + [b[1] + b[3] for *_r, b, _t in boxes]
    ext = (max(xs) - min(xs), max(ys) - min(ys)) if xs else (0, 0)

    report = {
        'circuit': str(path),
        'components': len(comps),
        'drawn_boxes': len(boxes),
        'tunnels': sum(1 for c in comps if c['name'] == 'Tunnel'),
        'drawing_extent': {'w': ext[0], 'h': ext[1]},
        'overlaps': hard,
        'crowded': soft,
    }
    if verbose:
        print(f"  {path.name}: {report['components']} components "
              f"({report['tunnels']} Tunnel), extent {ext[0]}x{ext[1]} px")
        for p in hard:
            print('    ! ' + p)
        for p in soft:
            print('    ~ ' + p)
        if not hard and not soft:
            print('    OK: no box overlap, no label clash')
        elif not hard:
            print(f'    OK electrically, but {len(soft)} tight spot(s)')
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('circuits', nargs='+')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    bad = 0
    reports = []
    for c in a.circuits:
        p = pathlib.Path(c)
        if not p.exists():
            print(f'  !! missing {c}')
            bad += 1
            continue
        r = audit(p, verbose=not a.json)
        reports.append(r)
        if r['overlaps']:
            bad += 1
    if a.json:
        print(json.dumps(reports, indent=2))
    else:
        total_hard = sum(len(r['overlaps']) for r in reports)
        total_soft = sum(len(r['crowded']) for r in reports)
        print(f'\n  {len(reports)} circuit(s): {total_hard} overlap/label-clash, {total_soft} tight spot(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
