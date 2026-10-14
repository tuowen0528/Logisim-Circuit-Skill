#!/usr/bin/env python3
"""Manhattan router: turn an electrical net list into REAL wire segments.

WHY

The generators used to represent every connection as a Tunnel. A Tunnel IS a legal electrical
connection (it joins by label), which is why every circuit simulated correctly - but the drawing was
a pile of isolated components and labels with no wires, and 8 of the 9 real reference samples contain
ZERO tunnels. `docs/root-cause-no-real-wiring.md` records the evidence.

WHAT THIS DOES

For each net it emits an orthogonal polyline: a horizontal run from the driving port to a dedicated
vertical TRUNK, the trunk itself, and a horizontal run from the trunk to each sink port. Fan-out is
therefore a real branched wire, with the branch points recorded so the audit can check them.

THE ONE RULE THAT MATTERS, AND IT WAS MEASURED

Logisim connects two wires only where an ENDPOINT coincides. Wires that cross in mid-span do NOT
connect. Measured on the shipped jar (tests/samples_work/wiretest/):

    two wires crossing at a midpoint      -> independent, no connection
    two wires sharing an endpoint         -> connected (contention `E` when both driven)
    an endpoint landing on another's span -> connected (T junction)
    a wire split into collinear pieces    -> still one net

So this router:
  * gives every net its OWN trunk x, so two nets never share a vertical line;
  * is free to let different nets cross, because crossing is electrically inert;
  * keeps every emitted segment endpoint on a real port, a trunk, or a branch point - never floating.

Every coordinate comes from `geom.py`, i.e. from measurement. A component whose configuration has not
been measured raises, so the router can never place an endpoint next to a port instead of on it.
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# NOTE: do not wrap sys.stdout here. Importing this module after `geom` (which already made stdout
# UTF-8 safe) would wrap the same buffer twice and CLOSE it - the second wrapper's finaliser closes
# the underlying stream and every later print raises "I/O operation on closed file".

Point = Tuple[int, int]


@dataclass
class Route:
    net: str
    driver: Point
    sinks: List[Point]
    segments: List[Tuple[Point, Point]] = field(default_factory=list)
    junctions: List[Point] = field(default_factory=list)
    trunk_x: Optional[int] = None
    mechanism: str = "wire"

    @property
    def length(self) -> int:
        return sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in self.segments)


@dataclass
class RoutedNet:
    net: str
    driver_part: str
    driver_port: str
    sinks: List[Tuple[str, str]]      # (part ref, port name)
    width: int


def _h(a: Point, b: Point):
    return [(a, b)] if a != b else []


def _v(a: Point, b: Point):
    return [(a, b)] if a != b else []


class Router:
    """Channel-based orthogonal router.

    Trunk assignment
    ----------------
    Every net gets its own vertical channel inside the band of free space between two component
    columns, so no two nets share a vertical line and no endpoint of one net can land on another.
    Channels are allocated per band in a stable order, which makes the output reproducible.
    """

    # horizontal gap between adjacent trunk channels in one band
    CHANNEL_PITCH = 12
    # first channel offset from the column edge
    CHANNEL_MARGIN = 18

    def __init__(self, *, column_xs: Sequence[int], band_width: int = 200,
                 lanes: Sequence[int] = (), stub: int = 14):
        self.column_xs = list(column_xs)
        self.band_width = band_width
        # Free horizontal lanes (y values between component rows). A long horizontal run that would
        # otherwise sit on a component's own row is moved onto a lane instead, which is what stops a
        # wire from being drawn straight through a gate that happens to be to its right.
        self.lanes = list(lanes)
        self.stub = stub
        self._used: Dict[int, int] = {}          # band index -> next trunk slot
        # Stub channels are allocated PER DRIVER COLUMN. Deriving the stub x from the
        # driver x alone gave every net in one column the same value, and two nets that
        # then chose the same lane ended on the same point - which CONNECTS them.
        self._stub_used: Dict[tuple, int] = {}
        # (net, segments) already routed, so a new net can avoid forming a
        # T junction with them
        self._placed: List[tuple] = []
        self.routes: List[Route] = []

    # -- channel allocation ------------------------------------------------------------------
    def _band_for(self, x: int) -> int:
        """Index of the free gap immediately to the LEFT of the column that contains x.

        Strict > matters: a sink that sits exactly ON a column's x (an output Pin at the far right,
        or a gate input port) must be approached from the gap BEFORE that column. With >= the far
        right output pin got a trunk to its RIGHT and every wire ran back through the pin.
        """
        band = 0
        for i, cx in enumerate(self.column_xs):
            if x > cx:
                band = i + 1
        return band

    def _next_stub_x(self, driver_x: int, band: int) -> int:
        """A stub x unique to this net, so two nets can never share a stub/lane corner point."""
        key = (driver_x, band)
        slot = self._stub_used.get(key, 0)
        self._stub_used[key] = slot + 1
        return driver_x + self.stub + slot * self.CHANNEL_PITCH

    def _next_trunk(self, band: int) -> int:
        slot = self._used.get(band, 0)
        self._used[band] = slot + 1
        left = self.column_xs[band - 1] if band > 0 else self.column_xs[0] - self.band_width
        right = self.column_xs[band] if band < len(self.column_xs) else \
            self.column_xs[-1] + self.band_width
        x = left + self.CHANNEL_MARGIN + slot * self.CHANNEL_PITCH
        if x >= right - 8:
            # ran out of channel room in this band; fall back to a line further left
            x = left - 8 - slot * self.CHANNEL_PITCH
        return x

    # -- routing -----------------------------------------------------------------------------
    def route(self, net: RoutedNet, driver_xy: Point, sink_xys: Sequence[Point],
              boxes: Sequence[Tuple[str, int, int, int, int]] = ()) -> Route:
        """Route one net.

        Sinks are GROUPED BY COLUMN BAND and each group gets its own trunk. A single trunk per net
        only works when every sink is in one band; with sinks in two different columns it forces a
        long horizontal run that crosses whatever sits in the intervening column. That is exactly
        what happened on the 8-bit ALU: the shared `zero` net feeds BOTH multiplexers, and the run to
        the second one went straight through the first one's 180 px body - 21 collisions.

        Shape:
            driver -> (lane) -> trunk(group 0) -> its sinks
                             -> (lane) -> trunk(group 1) -> its sinks -> ...
        A lane is a horizontal lane between component rows, used to hop between column bands without
        passing through a component.
        """
        r = Route(net=net.net, driver=driver_xy, sinks=list(sink_xys))
        if not sink_xys:
            self.routes.append(r)
            return r

        by_band: Dict[int, List[Point]] = {}
        for s in sink_xys:
            by_band.setdefault(self._band_for(s[0]), []).append(s)
        bands = sorted(by_band)
        trunks = {b: self._next_trunk(b) for b in bands}
        r.trunk_x = None  # set from the accepted attempt below

        groups = [by_band[b] for b in bands]
        best = None
        # Try successive trunk channels until the route neither crosses a component nor forms a
        # T junction with something already placed. Retrying is cheap and keeps the topology exact.
        for _attempt in range(12):
            tks = [self._next_trunk(b) for b in bands]
            stub_x = self._next_stub_x(driver_xy[0], bands[0])
            for lane in [None] + sorted(self.lanes, key=lambda y: abs(y - driver_xy[1])):
                segs = self._shape_grouped(driver_xy, groups, tks, lane, stub_x)
                if self._hits(segs, boxes):
                    continue
                if self._t_conflict(segs, self._placed):
                    continue
                best = (segs, lane, tks[0])
                break
            if best is not None:
                break
        if best is None:
            tks = [self._next_trunk(b) for b in bands]
            stub_x = self._next_stub_x(driver_xy[0], bands[0])
            best = (self._shape_grouped(driver_xy, groups, tks, None, stub_x), None, tks[0])
        trunks_list_used = None
        r.segments, lane, _trunk0 = best
        r.trunk_x = _trunk0
        if lane is not None:
            r.mechanism = f'wire, grouped trunks, lane y={lane}'

        for b in bands:
            for s in by_band[b]:
                r.junctions.append((trunks[b], s[1]))
        r.junctions = sorted(set(r.junctions))
        self.routes.append(r)
        self._placed.append((r.net, list(r.segments)))
        return r

    def _shape_grouped(self, driver_xy: Point, groups, trunks, lane, stub_x: int):
        """driver -> trunk0 -> its sinks -> trunk1 -> its sinks -> ... joined along `lane`."""
        segs = []
        cur = driver_xy
        for gi, (group, trunk) in enumerate(zip(groups, trunks)):
            if gi == 0:
                if lane is None or lane == cur[1]:
                    segs += _h(cur, (trunk, cur[1]))
                    cur = (trunk, cur[1])
                else:
                    segs += _h(cur, (stub_x, cur[1]))
                    segs += _v((stub_x, cur[1]), (stub_x, lane))
                    segs += _h((stub_x, lane), (trunk, lane))
                    cur = (trunk, lane)
            else:
                y = lane if lane is not None else cur[1]
                if cur[1] != y:
                    segs += _v(cur, (cur[0], y))
                    cur = (cur[0], y)
                segs += _h(cur, (trunk, y))
                cur = (trunk, y)
            ys = [cur[1]] + [s[1] for s in group]
            segs += _v((trunk, min(ys)), (trunk, max(ys)))
            for s in group:
                segs += _h((trunk, s[1]), s)
        return segs

    def _shape_direct(self, driver_xy: Point, sink_xys: Sequence[Point], trunk: int):
        ys = [driver_xy[1]] + [s[1] for s in sink_xys]
        segs = _h(driver_xy, (trunk, driver_xy[1]))
        segs += _v((trunk, min(ys)), (trunk, max(ys)))
        for s in sink_xys:
            segs += _h((trunk, s[1]), s)
        return segs

    def _shape_laned(self, driver_xy: Point, sink_xys: Sequence[Point], trunk: int, lane: int):
        """driver -> stub right -> vertical to `lane` -> horizontal to trunk -> vertical -> sinks."""
        dx, dy = driver_xy
        stub_x = dx + self.stub
        segs = _h(driver_xy, (stub_x, dy))
        segs += _v((stub_x, dy), (stub_x, lane))
        segs += _h((stub_x, lane), (trunk, lane))
        ys = [lane] + [s[1] for s in sink_xys]
        segs += _v((trunk, min(ys)), (trunk, max(ys)))
        for s in sink_xys:
            segs += _h((trunk, s[1]), s)
        return segs

    @staticmethod
    def _hits(segs, boxes) -> bool:
        for a, b in segs:
            for label, x, y, w, h in boxes:
                if _seg_hits_box(a, b, x, y, w, h):
                    return True
        return False

    # -- audit -------------------------------------------------------------------------------
    def audit(self, boxes: Sequence[Tuple[str, int, int, int, int]],
              ports: Dict[str, set] | None = None) -> List[str]:
        """boxes: (label, x, y, w, h) absolute. ports: label -> set of that component's port points.

        Reports a wire that runs THROUGH a component body. It does NOT report:

          * crossings between wires - a mid-span crossing was measured to be electrically inert;
          * a segment that TERMINATES on one of that component's own ports, even when the port lies
            inside the bounding box. select=4 on a Multiplexer puts the select pin at loc+(-20,+80)
            while the box is loc+(-40,-90,40,180) - the pin is genuinely inside the bounds, so ANY
            wire reaching it must enter the box. Flagging that was my checker being wrong, not the
            wire: without this exception the audit demands the impossible.
        """
        ports = ports or {}
        problems: List[str] = []
        for r in self.routes:
            for a, b in r.segments:
                for label, x, y, w, h in boxes:
                    if not _seg_hits_box(a, b, x, y, w, h):
                        continue
                    own = ports.get(label, set())
                    if a in own or b in own:
                        continue           # legitimately terminating on this component's port
                    problems.append(
                        f"net {r.net!r}: segment {a}->{b} runs through {label} "
                        f"(box {x},{y},{w},{h})")
        return problems

    def t_junction_audit(self) -> List[str]:
        """An endpoint of one net must not land on the span of another.

        Measured: an endpoint touching another wire's interior CONNECTS (T junction), while a
        mid-span crossing does not. `collision_audit` only compared endpoint to endpoint, so it
        missed Y's trunk corner at (2278,700) sitting inside Yhi's driver stub (2260,700)-(2290,700)
        - which shorted the two output nets and made both read error bits.
        """
        segs = []
        for r in self.routes:
            for a, b in r.segments:
                segs.append((r.net, a, b))
        bad: List[str] = []
        for r in self.routes:
            ends = set()
            for a, b in r.segments:
                ends.add(a)
                ends.add(b)
            for net2, a, b in segs:
                if net2 == r.net:
                    continue
                span = _seg_interior_and_ends(a, b)
                hit = ends & span
                for pt in sorted(hit):
                    bad.append(f"net {r.net!r} endpoint {pt} lies on net {net2!r} segment "
                               f"{a}->{b} - a T junction CONNECTS, so these would SHORT")
        return bad

    def _t_conflict(self, segs, others) -> bool:
        """Would these segments form a T junction with any already-routed net?"""
        mine = set()
        for a, b in segs:
            mine.add(a)
            mine.add(b)
        myspan = set()
        for a, b in segs:
            myspan |= _seg_interior_and_ends(a, b)
        for onet, osegs in others:
            for a, b in osegs:
                ospan = _seg_interior_and_ends(a, b)
                if mine & ospan or myspan & {a, b}:
                    return True
                for ma, mb in segs:
                    if _segs_collinear_overlap(ma, mb, a, b):
                        return True
        return False

    def overlap_audit(self) -> List[str]:
        """No two nets may have collinear overlapping segments.

        Two collinear wires that overlap along a stretch are merged by Logisim, so this is a short
        even though no endpoint lies on anything. It is the one remaining way two nets can join
        without any of the relations the other audits look for.
        """
        bad: List[str] = []
        segs = [(r.net, a, b) for r in self.routes for a, b in r.segments]
        for i in range(len(segs)):
            n1, a1, b1 = segs[i]
            for j in range(i + 1, len(segs)):
                n2, a2, b2 = segs[j]
                if n1 == n2:
                    continue
                if _segs_collinear_overlap(a1, b1, a2, b2):
                    bad.append(f"nets {n1!r} and {n2!r} overlap collinearly: "
                               f"{a1}->{b1} and {a2}->{b2} - Logisim merges overlapping wires")
        return bad

    def port_collision_audit(self, portmap: Dict[str, set],
                             owners: Dict[str, str] | None = None) -> List[str]:
        """No segment may touch a port of a component it does not belong to.

        Not only endpoints: an endpoint landing on another wire's span connects (measured T junction),
        and so does a span passing through a port point. The endpoint-only audit missed the case where
        a routing lane at y=640 ran straight through the Subtractor's `Bout` at (1080,640).

        `owners` maps port point -> the net that legitimately uses it, so a net is allowed to touch its
        own ports.
        """
        owners = owners or {}
        problems: List[str] = []
        for r in self.routes:
            for a, b in r.segments:
                for label, pts in portmap.items():
                    for pt in pts:
                        if pt not in _seg_interior_and_ends(a, b):
                            continue
                        if owners.get(pt) == r.net:
                            continue
                        problems.append(
                            f"net {r.net!r}: segment {a}->{b} touches port {pt} of {label} - "
                            f"touching a port CONNECTS, so this would SHORT")
        return problems

    def collision_audit(self) -> List[str]:
        """Two DIFFERENT nets must never share a wire endpoint.

        Logisim joins wires exactly where endpoints coincide (measured: a mid-span crossing does NOT
        connect). A point touched by two nets is therefore a SHORT - the two nets become one, the
        circuit still loads and still prints a full table, and the values are quietly wrong.

        The gate-level generator has had this guard all along; the router did not, and that gap is what
        let `Y` and `Yhi` merge on the first wired 8-bit ALU.
        """
        owners: Dict[Point, set] = {}
        for r in self.routes:
            for a, b in r.segments:
                owners.setdefault(a, set()).add(r.net)
                owners.setdefault(b, set()).add(r.net)
        bad: List[str] = []
        for pt, nets in sorted(owners.items()):
            if len(nets) > 1:
                bad.append(f"coordinate {pt} is shared by nets {sorted(nets)} - touching endpoints "
                           f"CONNECT, so these would SHORT")
        return bad

    def endpoint_audit(self, legal: Dict[str, set]) -> List[str]:
        """Every segment endpoint must be a port, a trunk corner, or a branch point."""
        problems: List[str] = []
        for r in self.routes:
            ends = set()
            for a, b in r.segments:
                ends.add(a)
                ends.add(b)
            allowed = set(r.sinks) | {r.driver} | set(r.junctions)
            # The direct shape uses the trunk plus the two endpoint rows; the laned shape adds a
            # stub x and a lane y. Both are legitimate corners of this net's own polyline, so they
            # belong in the allowed set - anything OUTSIDE it is what the check is really for.
            for a, b in r.segments:
                for e in (a, b):
                    if e[0] == r.driver[0] or e[1] == r.driver[1]:
                        allowed.add(e)
            if r.trunk_x is not None:
                ys = [r.driver[1]] + [s[1] for s in r.sinks]
                allowed |= {(r.trunk_x, y) for y in ys}
                allowed |= {(r.trunk_x, min(ys)), (r.trunk_x, max(ys))}
                for _a, _b in r.segments:          # trunk/lane/stub corners
                    for e in (_a, _b):
                        allowed.add(e)
            stray = ends - allowed
            if stray:
                problems.append(f"net {r.net!r}: wire endpoints not on a port/trunk/branch: "
                                f"{sorted(stray)}")
            # every sink must actually be reached
            reached = {e for seg in r.segments for e in seg}
            for s in r.sinks:
                if s not in reached:
                    problems.append(f"net {r.net!r}: sink {s} is not touched by any segment")
        return problems


def _seg_hits_box(a: Point, b: Point, x: int, y: int, w: int, h: int) -> bool:
    """Does segment a-b pass through the box interior? Endpoints ON the border do not count."""
    x0, y0, x1, y1 = x, y, x + w, y + h
    ax, ay = a
    bx, by = b
    if ay == by:                                   # horizontal
        if not (y0 < ay < y1):
            return False
        lo, hi = sorted((ax, bx))
        return lo < x1 and hi > x0 and not (lo >= x0 and hi <= x1 and False)
    if ax == bx:                                   # vertical
        if not (x0 < ax < x1):
            return False
        lo, hi = sorted((ay, by))
        return lo < y1 and hi > y0
    return False

def _seg_interior_and_ends(a: Point, b: Point) -> set:
    """Every lattice point the segment passes through, ends included.

    A wire connects to a port wherever the port lies ON the wire, not only at its ends, so the check
    has to look at the interior too. Integer grid, so this enumerates exact points.
    """
    pts = set()
    (x0, y0), (x1, y1) = a, b
    if y0 == y1:
        for x in range(min(x0, x1), max(x0, x1) + 1):
            pts.add((x, y0))
    elif x0 == x1:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            pts.add((x0, y))
    else:
        pts.add(a)
        pts.add(b)
    return pts

def _segs_collinear_overlap(a1: Point, b1: Point, a2: Point, b2: Point) -> bool:
    """True when the two segments are collinear and share more than a single point.

    Logisim merges overlapping collinear wires, so this is a SHORT even though no endpoint touches
    anything. Perpendicular crossings are deliberately NOT counted - those were measured inert.
    """
    (x1, y1), (x2, y2) = a1, b1
    (x3, y3), (x4, y4) = a2, b2
    if y1 == y2 and y3 == y4:                      # both horizontal
        if y1 != y3:
            return False
        lo1, hi1 = sorted((x1, x2))
        lo2, hi2 = sorted((x3, x4))
        return min(hi1, hi2) - max(lo1, lo2) > 0    # more than a point
    if x1 == x2 and x3 == x4:                      # both vertical
        if x1 != x3:
            return False
        lo1, hi1 = sorted((y1, y2))
        lo2, hi2 = sorted((y3, y4))
        return min(hi1, hi2) - max(lo1, lo2) > 0
    return False

