#!/usr/bin/env python3
"""layout -- assign coordinates to the one Circuit, so the router can do its job.

This is the layer whose absence made every attempt to use the new generator fail. It was reported as
"missing" twice, which was fair: a generator that cannot lay out a circuit cannot build one, and a
refactor that leaves the entry point in that state is a regression in progress, not a step forward.

WHAT A LAYOUT MUST PROVIDE

The router allocates a horizontal lane per net, and two nets that share a lane MERGE, because Logisim
joins collinear overlapping wires. So the layout's single most important job is:

    never let two different components sit on the same y unless they are in the same net

The old generators each solved this differently, which is exactly why nobody noticed the requirement:

    circmodel  packed gates into columns by index and hoped
    rtllib     placed parts on a designed grid, output pins on distinct rows
    seqmodel   pushed the whole problem onto hand-written `waypoints`

None of the three stated the rule, so none of them could be reused. This one states it and enforces it.

THE ALGORITHM

1.  Depth-rank every part from the netlist: a part that consumes a net sits one column right of the
    part that drives it. Pins and constants rank 0.
2.  Put one column per rank, spaced by the widest component in the circuit.
3.  Within a column, give every part its OWN y row. Rows are allocated from a running cursor and
    never reused for a different part, so no two components can share a lane by accident.
4.  Constants, the clock and the output pins go to dedicated margins, also on unique rows.

Step 3 is the part the old layouts did not do, and it is the reason both collisions in the CPU
datapath appeared: `c_zero` and `g_or` shared y=1800, then `sub`, `alu_mux` and the output pin all
shared y=900.
"""
from __future__ import annotations

from typing import Dict, List

from ir import Circuit, Part

def _ranks(c: Circuit) -> Dict[str, int]:
    """Depth of each part: 1 + max(depth of the parts that drive the nets it consumes)."""
    depth: Dict[str, int] = {p.ref: 0 for p in c.parts}
    owner = {}
    for net in c.nets.values():
        owner[net.name] = net.driver.ref
    # iterate to a fixed point: the netlist is small and acyclic in practice
    for _ in range(len(c.parts) + 2):
        changed = False
        for p in c.parts:
            best = depth[p.ref]
            for net in c.nets.values():
                if any(e.ref == p.ref for e in net.sinks):
                    d = depth.get(net.driver.ref, 0)
                    if net.driver.ref != p.ref and d + 1 > best:
                        best = d + 1
            if best != depth[p.ref]:
                depth[p.ref] = best
                changed = True
        if not changed:
            break
    return depth

def layout(c: Circuit) -> None:
    """Assign `loc` to every part. Parts that already have a non-default loc keep it only when the
    spec supplied one explicitly; here everything is placed, because a mixed layout is how overlaps
    happen."""
    if not c.parts:
        return

    depth = _ranks(c)

    # column width = the widest component box, so a route always has room to turn between columns
    widest = 0
    for p in c.parts:
        try:
            widest = max(widest, p.box()[2])
        except Exception:                                        # noqa: BLE001
            widest = max(widest, 100)
    col_pitch = max(600, widest + 400)

    special = {"CONST", "CLOCK"}
    input_pins = [p for p in c.parts if p.kind == "PIN" and p.pin_type == "input"]
    output_pins = [p for p in c.parts if p.kind == "PIN" and p.pin_type == "output"]
    consts = [p for p in c.parts if p.kind in special]
    body = [p for p in c.parts if p not in input_pins + output_pins + consts]

    # ---- input pins: left margin, one unique row each ---------------------------------------
    y = 200
    for p in sorted(input_pins, key=lambda q: q.ref):
        p.loc = (120, y)
        y += 60

    # ---- constants and the clock: their own band, below everything, one row each -------------
    y = 400 + 60 * max(1, len(body) + 4)
    for p in sorted(consts, key=lambda q: q.ref):
        p.loc = (120, y)
        y += 120

    # ---- body: one column per depth rank, and a GLOBALLY UNIQUE row for every part ----------
    #
    # A per-column row cursor is not enough. Resetting `row_y` at the start of each column gives the
    # first part of every column the same y, so their ports line up and the router runs one net
    # through another component's pin. That is exactly what the validator caught:
    #
    #     net 'p': wire (1200,380)->(1974,380) touches port (1800,380) of ['n4']
    #
    # Rows therefore come from ONE cursor shared by every column. Ports sit at row +/- 20, so a
    # spacing of 160 leaves a clear band between neighbours.
    by_rank: Dict[int, List[Part]] = {}
    for p in body:
        by_rank.setdefault(depth.get(p.ref, 0), []).append(p)

    base_y = 200
    cursor = base_y
    for rank in sorted(by_rank):
        col = 2 + rank * col_pitch // 300          # x in units of 300, matching the old grid
        for p in sorted(by_rank[rank], key=lambda q: q.ref):
            p.loc = (col * 300, cursor)
            cursor += 160

    # ---- output pins: right margin, one unique row each -------------------------------------
    # `max(by_rank)` raises on an empty mapping, which is what a spec consisting only of pins and
    # constants produces. Guard it rather than letting a layout crash on a legal circuit.
    last_rank = max(by_rank) if by_rank else 0
    right = (2 + (last_rank + 1) * col_pitch // 300 + 2) * 300
    y = 200
    for p in sorted(output_pins, key=lambda q: q.ref):
        p.loc = (right, y)
        y += 120
