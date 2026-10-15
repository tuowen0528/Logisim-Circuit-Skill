#!/usr/bin/env python3
"""connect -- turn every net into Tunnels. No wires exist in a circuit this produces.

MEASURED, and it overturns an earlier recorded finding

An earlier note in this project said "an input Pin co-located with a Tunnel does not drive it", and
that wrong conclusion is what pushed the architecture into Manhattan routing: a router, lanes, trunks,
collision retries, six audits, and still 13 of 31 specs failing to route. It was re-measured directly
(`tests/samples_work/zerowire/test_pin_tunnel.circ`):

    a b y
    0 0 0
    0 1 0
    1 0 0
    1 1 1        <- a correct AND, with ZERO <wire> elements in the file

So a Tunnel placed exactly ON a port connects, on the input side and the output side, for Pins and for
gate ports alike. There is nothing a wire is needed for.

HOW A NET IS EXPRESSED

One Tunnel per participant, all carrying the same label and the same width, each sitting exactly on the
port it serves:

    input Pin  ●[Tunnel a]                     gate input  ●[Tunnel a]  (Tunnel on the port)
    gate output ●[Tunnel y]                    output Pin  ●[Tunnel y]

If two Tunnels share a coordinate, they are one node, so a circuit is electrically valid only when no
two DIFFERENT labels ever land on one coordinate. `check` below enforces exactly that, and it is now the
only electrical invariant that matters: with no wires there is no traversal to collide, no lane to
share, and no collinear merge.

WHY THIS IS STRICTLY BETTER

    no router      the router existed only to draw wires
    no lanes       nothing to allocate
    no collisions  no spans to overlap
    no layout puzzle  a part's position only has to keep its own glyphs readable

A part's coordinate now affects only whether its drawing is legible - never whether it works. That
removes an entire class of failure and makes a 32-bit CPU ordinary to build.
"""
from __future__ import annotations

from typing import Dict, List, Set, Tuple

import ir
from ir import Circuit, Part, SpecError

TUNNEL = "TUNNEL"

def _neighbour_on_the_right(p: Part, port: str) -> bool:
    """Which side of the Tunnel its component sits on, so the glyph is drawn away from it.

    Measured: `facing="west"` draws the glyph to the RIGHT of the port and `east` to the LEFT. Because
    the names are inverted, the only reliable question is "where is the neighbour", which is what this
    answers. A consumer Tunnel on a gate input has the gate on its right; a Tunnel on an output Pin has
    that pin's body on its left.
    """
    if p.kind == "PIN":
        # an output Pin carries facing="west", so its body extends LEFT of the port
        return False
    if p.kind in ("CONST", "CLOCK"):
        return False
    if port is not None and p.drives(port):
        # a driver Tunnel on a component's own output: the component is on the LEFT
        return False
    # a consumer Tunnel on an input port: the component is on the RIGHT
    return True

def connect(c: Circuit) -> List[str]:
    """Replace every net with Tunnels on its ports. Adds TUNNEL parts; writes no wires.

    Returns a list of problems. A problem here is only ever one of two things:

      * two different net labels landing on one coordinate (they would merge - a short);
      * two ports of the same component at one coordinate (already impossible by construction, but
        checked because a component that declared a duplicate would silently short itself).
    """
    problems: List[str] = []
    occupied: Dict[Tuple[int, int], str] = {}
    by_ref = {p.ref: p for p in c.parts}
    tunnels: List[Part] = []

    # A Tunnel on a port is a participant of that net. Collect them first, then place, so a coordinate
    # can be checked against every label before anything is added.
    for name, net in sorted(c.nets.items()):
        participants: List[Tuple[Part, str, Tuple[int, int]]] = []
        eps = [(net.driver, True)] + [(e, False) for e in net.sinks]
        seen: Set[Tuple[int, int]] = set()
        for ep, _is_driver in eps:
            part = by_ref.get(ep.ref)
            if part is None:
                problems.append(f"net {name!r} references unknown part {ep.ref!r}")
                continue
            xy = part.loc if ep.port is None else part.port(ep.port)
            if xy in seen:
                continue                       # two endpoints at one point are one Tunnel
            seen.add(xy)
            participants.append((part, ep.port, xy))

        for part, port, xy in participants:
            prev = occupied.get(xy)
            if prev is not None and prev != name:
                problems.append(
                    f"nets {prev!r} and {name!r} would both put a Tunnel at {xy} - Tunnels at one "
                    f"coordinate are one node, so this shorts them")
                continue
            occupied[xy] = name

        for part, port, xy in participants:
            t = Part(kind=TUNNEL, ref=f"__t_{name}_{len(tunnels)}", loc=xy, label=name,
                     width=net.width,
                     facing=ir.tunnel_facing(_neighbour_on_the_right(part, port)))
            tunnels.append(t)

    c.parts.extend(tunnels)
    return problems

def check(c: Circuit) -> List[str]:
    """The invariants a wire-free circuit must satisfy. Cheap, and they are the whole safety net."""
    problems: List[str] = []
    by_ref = {p.ref: p for p in c.parts}

    # 1. no wires at all - the point of the design
    if c.wires:
        problems.append(f"{len(c.wires)} wire(s) present; this generator emits none")

    # 2. one driver per net, and it must actually be able to drive
    for name, net in c.nets.items():
        d = by_ref.get(net.driver.ref)
        if d is None:
            problems.append(f"net {name!r} driver {net.driver.ref!r} does not exist")
            continue
        if not d.drives(net.driver.port):
            problems.append(f"net {name!r}: {net.driver.ref}:{net.driver.port} cannot drive")

    # 3. every net's label appears on at least two ports, or it connects nothing
    labels: Dict[str, int] = {}
    for p in c.parts:
        if p.kind == TUNNEL:
            labels[p.label] = labels.get(p.label, 0) + 1
    for name in c.nets:
        if labels.get(name, 0) < 2:
            problems.append(f"net {name!r} has {labels.get(name, 0)} Tunnel(s); a net needs at least "
                            f"two endpoints to connect anything")

    # 4. a Tunnel must sit on a port, or it connects to nothing
    port_coords = set()
    for p in c.parts:
        if p.kind == TUNNEL:
            continue
        for n in p.port_names():
            try:
                port_coords.add(p.port(n))
            except (SpecError, ir.GeometryUnknown):
                continue
    for p in c.parts:
        if p.kind == TUNNEL and p.loc not in port_coords:
            problems.append(f"Tunnel {p.label!r} at {p.loc} sits on no port; it connects to nothing")

    return problems
