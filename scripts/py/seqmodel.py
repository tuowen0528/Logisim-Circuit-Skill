#!/usr/bin/env python3
"""Spec-driven SEQUENTIAL circuit builder + clocked-simulation verifier.

Companion to `circmodel.py` (which is combinational). This adds clocked components and a way to
check a circuit's behaviour over a sequence of CLOCK CYCLES.

THE KEY MECHANISM (see references/00-probe-log.md section 14)

`--tty table` takes two different paths (`TtyInterface.java:339`, `:344`, `:520-558`):

  * NO pin labelled `halt`  -> doTableAnalysis = exhaustive COMBINATIONAL enumeration, clocks NEVER tick
  * a pin labelled `halt`   -> the clock-driven loop: toggleClocks(); propagate(); one row per tick

So a clocked circuit is exercised by giving it a `halt` pin, and every printed row is one cycle.

WHY THE RUN IS BOUNDED BY WALL-CLOCK, NOT BY `halt`

A `halt` pin that is constantly TRUE does NOT stop the loop - it only SELECTS the clocked path, so the
simulation would run forever. We therefore stop after a chosen number of cycles by holding the clock
low:
  * pump phase  - `higg` pin = 0, `halt` pin = 0  -> loop keeps ticking, rows accumulate
  * hold phase  - raise `clkhold`, wait, then set `halt` = 1 -> loop stops cleanly
Output is redirected to a FILE (never a pipe) so the JVM cannot deadlock on a full pipe - that bug
cost real time during development.

MEASURED COMPONENT PORTS (SamplePorts ground truth, not guessed)

  D/T/JK/S-R Flip-Flop, appearance logisim_evolution:
      D (or T / J,K / S,R)  (-10,+10)
      CK                    (-10,+50)
      Q                     (+50,+10)
      Qbar                  (+50,+50)
      preset                (+20,  0)
      reset                 (+20,+60)
  Register (width N):  Q (+60,+30)  D (0,+30)  clr (0,+50)  clk (0,+70)  en (+30,+90)
  Clock:    single OUTPUT at loc
  Constant: single OUTPUT at loc
  Probe:    single INPUT at loc  -> PASSIVE, so it observes an internal net without driving it

Port INDICES are not consistent across components (flip-flop CK is end[5], Counter CK is end[2]), so
this module resolves ports by NAME via an explicit table.

Usage:
    py seqmodel.py build <spec.json> <out.circ>
    py seqmodel.py verify <spec.json> <out.circ> [--cycles N]
Exit: 0 pass, 1 mismatch, 2 harness/spec error (never a pass), 3 spec invalid.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import circmodel as cm
import project_header as _ph  # noqa: E402

# circmodel already made stdout UTF-8 safe; wrapping it AGAIN here closes the shared buffer (the
# second wrapper's finalizer closes it), which surfaces as "I/O operation on closed file" and made
# the test module fail to import. Reuse the one-shot helper instead.
cm.ensure_utf8_stdout()

JAR = pathlib.Path(__file__).resolve().parents[1] / "bin" / "logisim-evolution-all.jar"
WIRING_LIB, GATES_LIB, MEMORY_LIB, ARITH_LIB = 0, 1, 5, 3

# ----------------------------------------------------------------------------------------------
# Measured port tables. Adding a component here REQUIRES a port dump to justify it.
# ----------------------------------------------------------------------------------------------

FF_NAMES = {
    "DFF": "D Flip-Flop", "TFF": "T Flip-Flop", "JKFF": "J-K Flip-Flop", "SRFF": "S-R Flip-Flop",
}
# (name, width_of_data_line, data_port_offset)
FF_PORTS = {
    "D": (-10, 10),
    "CK": (-10, 50),
    "Q": (50, 10),
    "QB": (50, 50),
    "PRESET": (20, 0),
    "RESET": (20, 60),
}
# Register ports. MEASURED with SamplePorts and cross-checked against Register.java:
#   ps[OUT] = (60, 30)  ps[IN] = (0, 30)  ps[CK] = (0, 70)  ps[CLR] = (30, 90)  ps[EN] = (0, 50)
# The `end` indices follow declaration order: end[0]=OUT end[1]=IN end[2]=CK end[3]=CLR end[4]=EN.
#
# WARNING: an earlier table had CLR and EN SWAPPED (CLR at (0,50), EN at (30,90)), and the unit test
# asserted those wrong values too, so the test agreed with the bug instead of catching it. The rule
# that would have caught it immediately: every port of a component must be at a DISTINCT coordinate,
# because two ports at one coordinate are electrically the same node. A test now asserts that.
REG_PORTS = {"Q": (60, 30), "D": (0, 30), "CLK": (0, 70), "CLR": (30, 90), "EN": (0, 50)}

# NOT gate. MEASURED (references/00-probe-log.md section 4): the output is at `loc` and a size-50
# inverter's single input is 30 px to the LEFT on the same row. A gate is needed here because a
# Register's CLR and EN are LEVEL-sensitive, so driving CLR from NOT(CLK) is how a load/hold cycle
# is expressed without any signal-generator primitive.
NOT_INPUT_DX = -30

# Adder. MEASURED (references/00-probe-log.md section 16) and identical to rtllib's table:
#   A (x-40,y-10)  B (x-40,y+10)  Cin (x-20,y-20)  Sum (loc)  Cout (x-20,y+20)
# Needed because WITHOUT arithmetic a clocked datapath cannot be expressed at all - an accumulator
# needs `Q + 1 -> D`, and seqmodel had no component that can add.
ADDER_PORTS = {
    "A": (-40, -10),
    "B": (-40, 10),
    "Cin": (-20, -20),
    "Sum": (0, 0),
    "Cout": (-20, 20),
}


def adder_port(loc: Tuple[int, int], port: str) -> Tuple[int, int]:
    if port not in ADDER_PORTS:
        raise SpecError(f"Adder has no port {port!r}")
    dx, dy = ADDER_PORTS[port]
    return (loc[0] + dx, loc[1] + dy)


def adder_port_width(port: str, width: int) -> int:
    """Cin/Cout are always 1 bit; A, B and Sum follow the component's width."""
    return 1 if port in ("Cin", "Cout") else width


# RAM. MEASURED for addrWidth=2, dataWidth=4 at loc (300,200) - bounds 240x170:
#   end[0] (300,210) w=2 IN   address
#   end[1] (540,290) w=4 OUT  data out
#   end[2] (300,290) w=4 IN   data in
#   end[3] (300,260) w=1 IN   output enable
#   end[4] (300,250) w=1 IN   write enable
#   end[5] (300,270) w=1 IN   clock
# Port NAMES and the index ORDER come from RamAppearance.configurePorts, which fills the array in
# this order: addr, data-out, data-in, OE, WE, clk, LE, BE, CLR. Every is INPUT_ONLY except the
# data-out, so a RAM is written and read through the SAME data pins only when `seperatedBus` is
# false; with the default attributes they are separate ports.
RAM_PORTS = {
    "A": (0, 10),
    "Dout": (240, 90),
    "Din": (0, 90),
    "OE": (0, 60),
    "WE": (0, 50),
    "CK": (0, 70),
}
# NOTE the y order on the left edge is A(10) < WE(50) < OE(60) < CK(70) < Din(90). WE sits ABOVE OE.


def ram_port(loc: Tuple[int, int], port: str) -> Tuple[int, int]:
    if port not in RAM_PORTS:
        raise SpecError(f"RAM has no port {port!r}")
    dx, dy = RAM_PORTS[port]
    return (loc[0] + dx, loc[1] + dy)


def ram_port_width(port: str, addr_width: int, data_width: int) -> int:
    if port == "A":
        return addr_width
    if port in ("Din", "Dout"):
        return data_width
    return 1


def not_port(loc: Tuple[int, int], port: str) -> Tuple[int, int]:
    if port == "out":
        return loc
    if port == "in":
        return (loc[0] + NOT_INPUT_DX, loc[1])
    raise SpecError(f"NOT gate has no port {port!r}")


class SpecError(Exception):
    """The spec cannot produce a valid circuit."""


def _ff_pin_index(name: str) -> int:
    """Named port -> `end` index for a 1-input flip-flop (order from AbstractFlipFlop)."""
    order = ["D", "CK", "Q", "QB", "PRESET", "RESET"]
    return order.index(name)


FF_ORDER = ["D", "CK", "Q", "QB", "PRESET", "RESET"]


def ff_port(loc: Tuple[int, int], port: str) -> Tuple[int, int]:
    """Absolute coordinate of a named flip-flop port.

    Offsets are MEASURED (`SamplePorts` against the real loader), not derived: for
    `appearance = logisim_evolution` and one data input the ports are
    D (-10,+10), CK (-10,+50), Q (+50,+10), Qbar (+50,+50), preset (+20,0), reset (+20,+60).
    `D` is the generic data input: it is T for a T flip-flop.
    """
    key = "D" if port in ("D", "T") else port
    if key not in FF_PORTS:
        raise SpecError(f"flip-flop has no port {port!r}; known: {sorted(FF_PORTS)}")
    dx, dy = FF_PORTS[key]
    return loc[0] + dx, loc[1] + dy


def reg_port(loc: Tuple[int, int], port: str) -> Tuple[int, int]:
    dx, dy = REG_PORTS[port]
    return loc[0] + dx, loc[1] + dy


def const_port(loc): return loc
def clock_port(loc): return loc
def probe_port(loc): return loc


# ----------------------------------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------------------------------

@dataclass
class Part:
    kind: str                       # "DFF" | "TFF" | "REG" | "CONST" | "CLOCK" | "PROBE" | "PIN"
    ref: str
    loc: Tuple[int, int] = (0, 0)
    width: int = 1
    addr_width: int = 0            # for RAM: address bits; `width` is the DATA width
    value: int = 0                 # for CONST
    label: str = ""                 # for TUNNEL / PIN
    facing: Optional[str] = None
    pin_type: str = "output"        # for PIN
    trigger: str = "rising"         # for REG / flip-flops: rising | falling | high | low

    @property
    def xml_name(self) -> str:
        return {"DFF": "D Flip-Flop", "TFF": "T Flip-Flop", "JKFF": "J-K Flip-Flop",
                "SRFF": "S-R Flip-Flop", "REG": "Register", "CONST": "Constant",
                "CLOCK": "Clock", "PROBE": "Probe", "TUNNEL": "Tunnel", "PIN": "Pin",
                "NOT": "NOT Gate", "ADDER": "Adder", "RAM": "RAM"}[self.kind]

    @property
    def lib(self) -> int:
        if self.kind in ("DFF", "TFF", "JKFF", "SRFF", "REG"):
            return MEMORY_LIB
        if self.kind == "NOT":
            return GATES_LIB
        if self.kind == "ADDER":
            return ARITH_LIB
        if self.kind == "RAM":
            return MEMORY_LIB
        return WIRING_LIB

    def port(self, name: str) -> Tuple[int, int]:
        if self.kind in ("DFF", "TFF", "JKFF", "SRFF"):
            return ff_port(self.loc, name)
        if self.kind == "REG":
            return reg_port(self.loc, name)
        if self.kind == "NOT":
            return not_port(self.loc, name)
        if self.kind == "ADDER":
            return adder_port(self.loc, name)
        if self.kind == "RAM":
            return ram_port(self.loc, name)
        if self.kind == "CLOCK":
            return clock_port(self.loc)
        if self.kind == "CONST":
            return const_port(self.loc)
        if self.kind in ("PROBE", "TUNNEL", "PIN"):
            return self.loc
        raise SpecError(f"{self.kind} has no named port {name!r}")


@dataclass
class SeqCircuit:
    name: str = "main"
    parts: List[Part] = field(default_factory=list)
    wires: List[Tuple[Tuple[int, int], Tuple[int, int]]] = field(default_factory=list)


# ----------------------------------------------------------------------------------------------
# Builder: a spec lists parts, then nets as explicit wire point-paths.
# ----------------------------------------------------------------------------------------------

class SeqBuilder:
    """A sequential spec gives explicit geometry, because clocked wiring is not auto-placeable yet.

        {
          "name": "main",
          "parts": [
            {"ref":"ff0","kind":"DFF","loc":[400,300]},
            {"ref":"clk","kind":"CLOCK","loc":[120,300]},
            {"ref":"stop","kind":"CONST","loc":[120,80],"value":0},
            {"ref":"obs0","kind":"PROBE","loc":[500,310]}
          ],
          "nets": {
            "clk":  [["clk","OUT"], ["ff0","CK"]],
            "q0":   [["ff0","Q"], ["obs0", null]],
            ...
          }
        }

    A net is a list of endpoints. An endpoint is `[ref, portName]` (port resolved from the
    measured table) or `[ref, null]` meaning "the component's own loc". Paths are expanded
    orthogonally: consecutive endpoints are joined by an L-shaped route.
    """

    def __init__(self, spec: dict):
        self.spec = spec
        self.circuit = SeqCircuit(name=spec.get("name", "main"))
        self._by_ref: Dict[str, Part] = {}
        self._net_points: Dict[str, List[Tuple[int, int]]] = {}
        self._build()

    def _infer_pin_widths(self):
        """Make each Pin as wide as the net it sits on.

        A spec's blanket `width` applies to declared parts, so Pins defaulted to 1 bit. Driving an
        8-bit net into a 1-bit output Pin left every value undefined: the harness reported a
        single-row stream of `U` for a correctly wired 8-bit register. The Pin has to match the net
        or the observation is meaningless, so infer it from whichever part shares the net.
        """
        for part in self.circuit.parts:
            if part.kind != "PIN":
                continue
            for _net, endpoints in self.spec.get("nets", {}).items():
                if not any(e[0] == part.ref for e in endpoints):
                    continue
                for ref, port in endpoints:
                    if ref == part.ref:
                        continue
                    other = self._by_ref.get(ref)
                    if other is None:
                        continue
                    if port is None:
                        w = other.width
                    elif other.kind in ("REG", "DFF", "TFF", "JKFF", "SRFF"):
                        w = other.width if port in ("Q", "D", "QB") else 1
                    elif other.kind == "ADDER":
                        # Cin/Cout are 1 bit regardless of the adder's width; A, B and Sum follow it.
                        w = adder_port_width(port, other.width)
                    elif other.kind == "RAM":
                        w = ram_port_width(port, other.addr_width, other.width)
                    elif other.kind == "CONST":
                        w = other.width
                    else:
                        w = other.width
                    if w > 1:
                        part.width = max(part.width, w)
                break

    def _build(self):
        for p in self.spec.get("parts", []):
            ref, kind = p["ref"], p["kind"].upper()
            if kind not in ("DFF", "TFF", "JKFF", "SRFF", "REG", "CONST", "CLOCK", "PROBE",
                            "TUNNEL", "PIN", "NOT", "ADDER", "RAM"):
                raise SpecError(f"part {ref!r}: unknown kind {kind!r}")
            loc = tuple(p.get("loc", (0, 0)))
            part = Part(kind=kind, ref=ref, loc=loc, width=int(p.get("width", 1)),
                        addr_width=int(p.get("addr_width", 0)),
                        value=int(p.get("value", 0)), label=p.get("label", ""),
                        facing=p.get("facing"), pin_type=p.get("pin_type", "output"),
                        trigger=p.get("trigger", "rising"))
            self._by_ref[ref] = part
            self.circuit.parts.append(part)

        self._infer_pin_widths()

        for net, endpoints in self.spec.get("nets", {}).items():
            if len(endpoints) < 1:
                raise SpecError(f"net {net!r} has no endpoints")
            pts: List[Tuple[int, int]] = []
            for ep in endpoints:
                ref, portname = ep[0], ep[1]
                if ref not in self._by_ref:
                    raise SpecError(f"net {net!r} references unknown part {ref!r}")
                part = self._by_ref[ref]
                pts.append(part.loc if portname is None else part.port(portname))
            # `waypoints` lets a spec force an exact route. This exists because an L-shaped
            # route between two ports cannot always avoid a THIRD port: a flip-flop's data input
            # and clock share an x, so a "horizontal first" path to the data pin passes straight
            # through the clock port and merges the nets.
            #
            # CRITICAL: route through EVERY endpoint, not just the first and last. An earlier
            # version used `[pts[0]] + route + [pts[-1]]`, which SILENTLY DROPPED any middle
            # endpoint - the net looked connected in the spec but the component was left floating
            # on the board. A NOT gate whose input was a middle endpoint was never wired at all,
            # so the register's CLR floated and the test still "passed" for the wrong reason.
            route = list(self.spec.get("waypoints", {}).get(net, []))
            if route:
                full: List[Tuple[int, int]] = [pts[0]] + [tuple(w) for w in route] + [pts[-1]]
            else:
                full = list(pts)
            for a, b in zip(full, full[1:]):
                for seg in _orthogonal(a, b):
                    self.circuit.wires.append(seg)
            self._net_points[net] = full

    def validate(self) -> List[str]:
        problems: List[str] = []
        # exactly one driving source per net
        for net, endpoints in self.spec.get("nets", {}).items():
            drivers = 0
            for ep in endpoints:
                part = self._by_ref[ep[0]]
                pn = ep[1]
                if part.kind in ("CLOCK", "CONST"):
                    drivers += 1
                elif part.kind in ("DFF", "TFF", "JKFF", "SRFF") and pn in ("Q", "QB"):
                    drivers += 1
                elif part.kind == "NOT" and pn == "out":
                    drivers += 1
                elif part.kind == "REG" and pn == "Q":
                    drivers += 1
                elif part.kind == "ADDER" and pn in ("Sum", "Cout"):
                    drivers += 1
                elif part.kind == "RAM" and pn == "Dout":
                    drivers += 1
                elif part.kind == "PROBE":
                    pass                                  # passive
            if drivers != 1:
                problems.append(f"net {net!r} has {drivers} drivers, expected exactly 1")

        # An INPUT Pin cannot act as a control in a sequential spec, so refuse it explicitly rather
        # than letting it build an untestable circuit.
        #
        # `--tty table` DOES have two modes, and input pins work in only one of them:
        #   * the COMBINATIONAL path (`doTableAnalysis`) enumerates input pins and drives them via
        #     `Pin.FACTORY.driveInputPin` - that is how the 512-row adder acceptance case works;
        #   * the CLOCKED path (`runSimulation`, selected by a `halt` pin) never drives them, so an
        #     input pin just sits at U for the whole run.
        # Worse, the combinational path sets VALUES rather than EDGES: measured, a Register with
        # `din/en/clk` input pins and `halt` absent produced all 64 rows with `q` = 0000 on every
        # one, because an edge-triggered part never sees a clock transition. So a pin-driven
        # edge-triggered design silently tests nothing.
        pin_inputs = [p.ref for p in self.circuit.parts
                      if p.kind == "PIN" and p.pin_type == "input"]
        if pin_inputs:
            problems.append(
                f"input Pin(s) {sorted(pin_inputs)} cannot drive a clocked circuit: the `halt` "
                f"loop never drives input pins, and the combinational table sets VALUES not EDGES, "
                f"so edge-triggered parts never latch (measured: a Register stayed 0000 on all 64 "
                f"rows). Use a CLOCK and CONST parts instead."
            )

        # every clocked part's clock input must be driven. A Register names its clock `CLK`, not
        # `CK`, so checking only flip-flops left registers completely unvalidated - a register with
        # a floating clock would build happily and then never latch.
        clocked = [(p.ref, "CK") for p in self.circuit.parts
                   if p.kind in ("DFF", "TFF", "JKFF", "SRFF")]
        clocked += [(p.ref, "CLK") for p in self.circuit.parts if p.kind == "REG"]
        all_nets = self.spec.get("nets", {})
        for ref, port in clocked:
            if not any(any(e[0] == ref and e[1] == port for e in all_nets.get(n, []))
                       for n in all_nets):
                problems.append(f"{ref}: {port} is not connected (it would never clock)")

        # EVERY declared endpoint must actually lie on the wire that was emitted. A dropped
        # endpoint means the component is floating on the board while the spec claims it is
        # connected - the net LOOKS right and validates clean. This is the check that would have
        # caught the silent middle-endpoint drop immediately.
        #
        # Only nets WITH `waypoints` can drop an endpoint: without waypoints the serializer joins
        # the endpoints directly, so every one of them is on the path by construction.
        for net, endpoints in self.spec.get("nets", {}).items():
            if not self.spec.get("waypoints", {}).get(net):
                continue
            pts = self._net_points.get(net, [])
            if len(pts) < 2:
                continue
            # Orthogonalise exactly as the serialiser does BEFORE testing membership. Testing the
            # raw point pairs compares against diagonal "segments" that were never emitted, and
            # `_on_segment` only understands axis-aligned ones - so valid wires were reported as
            # floating. The check must run on the emitted geometry, not on the spec's intent.
            emitted = [seg for a, b in zip(pts, pts[1:]) for seg in _orthogonal(a, b)]
            for ep in endpoints:
                ref, portname = ep[0], ep[1]
                part = self._by_ref.get(ref)
                if part is None:
                    continue
                where = part.loc if portname is None else part.port(portname)
                if not any(_on_segment(where, seg) for seg in emitted):
                    problems.append(
                        f"net {net!r}: endpoint {ref}:{portname or '-'} at {where} is NOT on the "
                        f"routed path {emitted} - that component would be left FLOATING while the "
                        f"spec claims it is connected"
                    )

        problems.extend(self._geometry_problems())
        return problems

    # -- geometry: the failure mode that is SILENT -------------------------------------------
    def _wire_net(self) -> List[Tuple[Tuple[int, int], Tuple[int, int], str]]:
        """The wires actually generated, tagged with the net that produced them.

        Recomputed here through the SAME route logic the serialiser uses (endpoints + waypoints),
        because checking a different route than the one emitted is how a validator lies.

        IMPORTANT: the pairs must go through `_orthogonal`, exactly as the serialiser does. An
        earlier version emitted the RAW endpoint pair, so a non-axis-aligned pair such as
        `(200,700) -> (250,400)` appeared here as one diagonal "segment" while the file contained
        the two real segments. `_on_segment` only understands axis-aligned segments, so it rejected
        a perfectly valid wire - the validator was inspecting something that was never emitted.
        """
        route_map = self.spec.get("waypoints", {})
        out: List[Tuple[Tuple[int, int], Tuple[int, int], str]] = []
        for net, endpoints in self.spec.get("nets", {}).items():
            pts = [self._by_ref[e[0]].loc if e[1] is None else self._by_ref[e[0]].port(e[1])
                   for e in endpoints]
            route = [tuple(w) for w in route_map.get(net, [])]
            if route:
                full: List[Tuple[int, int]] = [pts[0]] + route + [pts[-1]]
            else:
                full = list(pts)
            for a, b in zip(full, full[1:]):
                for seg in _orthogonal(a, b):
                    out.append((seg[0], seg[1], net))
        return out

    def _geometry_problems(self) -> List[str]:
        """Detect wires that touch a port they do not belong to.

        A wire ENDPOINT landing on another net's port, or a wire segment passing THROUGH a port,
        merges the two nets (a T-junction). The result is contention, a hang, or - as happened
        while developing this module - a circuit that printed ZERO rows with no diagnostic.
        Geometry must be checked, not hoped for.
        """
        problems: List[str] = []
        owners: Dict[Tuple[int, int], List[Tuple[str, str, str]]] = {}
        for net, endpoints in self.spec.get("nets", {}).items():
            for ep in endpoints:
                part = self._by_ref[ep[0]]
                pn = ep[1] if ep[1] is not None else "-"
                coord = part.loc if ep[1] is None else part.port(ep[1])
                owners.setdefault(coord, []).append((net, ep[0], pn))

        for coord, claims in owners.items():
            nets = {c[0] for c in claims}
            if len(nets) > 1:
                problems.append(
                    f"port {coord} is claimed by {len(nets)} different nets "
                    f"({sorted(nets)}) - they would MERGE"
                )

        seen: Dict[Tuple[Tuple[int, int], Tuple[int, int]], str] = {}
        segs: List[Tuple[Tuple[int, int], Tuple[int, int], str]] = list(self._wire_net())
        for a, b, net in segs:
            seg = (a, b)
            if seg in seen and seen[seg] != net:
                problems.append(f"nets {seen[seg]!r} and {net!r} share the wire {seg}")
            seen[seg] = net
            for coord, claims in owners.items():
                others = sorted({c[0] for c in claims if c[0] != net})
                if others and _on_segment(coord, seg):
                    problems.append(
                        f"net {net!r} wire {seg} passes through port {coord} owned by {others} "
                        f"- that merges the nets"
                    )

        # COLLINEAR OVERLAP between wires of DIFFERENT nets. Comparing whole segments misses a
        # partial overlap, and Logisim merges collinear wires regardless of which net they belong
        # to, so this must be an error rather than a warning.
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a1, b1, n1 = segs[i]
                a2, b2, n2 = segs[j]
                if n1 == n2:
                    continue
                ov = _collinear_overlap(a1, b1, a2, b2)
                if ov:
                    problems.append(
                        f"nets {n1!r} and {n2!r} OVERLAP on the collinear span {ov} "
                        f"({a1}->{b1} vs {a2}->{b2}) - Logisim merges collinear wires, so this "
                        f"shorts the two nets"
                    )

        # A wire segment that passes THROUGH a port already merges (checked above). A subtler
        # variant, found the hard way: a segment whose y (or x) EQUALS a port's row while running
        # past it also lands on the port.
        #
        # CRITICAL: this must consider EVERY port of every component, not only ports that some net
        # already claims. An UNCONNECTED port is still electrically present, and a wire running
        # through it merges with that component. This exact gap hid the counter bug for a whole
        # round: `q0` ran along y=350 straight through ff1's UNCONNECTED Qbar port at (750,350), so
        # the inverse of ff1.Q fought ff0.Q and the counter read `E` forever, while this validator
        # reported "no problems" because Qbar belonged to no net.
        all_ports: Dict[Tuple[int, int], List[str]] = {}
        for ref, part in self._by_ref.items():
            for pname, coord in self._known_ports(part):
                all_ports.setdefault(coord, []).append(f"{ref}:{pname}")

        for a, b, net in segs:
            own_nets = {c[0] for c in owners.get(a, [])} | {c[0] for c in owners.get(b, [])}
            lo = (min(a[0], b[0]), min(a[1], b[1]))
            hi = (max(a[0], b[0]), max(a[1], b[1]))
            for coord, refs in all_ports.items():
                if not (lo[0] <= coord[0] <= hi[0] and lo[1] <= coord[1] <= hi[1]):
                    continue
                if net in {c[0] for c in owners.get(coord, [])}:
                    continue                       # legitimately this net's own port
                if net in own_nets and net in {c[0] for c in owners.get(coord, [])}:
                    continue
                who = ", ".join(sorted(set(refs)))
                problems.append(
                    f"net {net!r} wire {a}->{b} touches port {coord} of {who} - that port is not "
                    f"part of net {net!r} (an unconnected port is still electrically present), so "
                    f"the wire merges with that component"
                )
        return problems

    def _known_ports(self, part) -> List[Tuple[str, Tuple[int, int]]]:
        """Every port this component exposes, measured.

        Used so geometry can be checked against ports that no net has claimed yet. Flip-flops must
        include Qbar: it is an output that is very often left unconnected, which is exactly the case
        that slipped through.
        """
        kind = getattr(part, "kind", "")
        if kind in ("PIN", "PROBE", "CLOCK", "CONST"):
            return [("-", part.loc)]
        if kind == "NOT":
            return [("out", not_port(part.loc, "out")), ("in", not_port(part.loc, "in"))]
        if kind == "ADDER":
            return [(p, adder_port(part.loc, p)) for p in ADDER_PORTS]
        if kind == "RAM":
            return [(p, ram_port(part.loc, p)) for p in RAM_PORTS]
        names: List[str] = []
        if kind in ("TFF", "DFF", "SRFF", "JKFF"):
            # QB matters most: it is an OUTPUT that is very often left unconnected, and a wire
            # running through it merges with the flip-flop. Omitting it from this list is exactly
            # what let a counter read `E` for a whole round while the validator said "no problems".
            names = ["D", "CK", "Q", "QB", "PRESET", "RESET"]
        elif kind == "REG":
            names = ["D", "CLK", "Q", "CLR", "EN"]
        else:
            names = ["A", "B", "C", "D", "CK", "Q", "Qbar", "out", "in", "EN", "CLR", "RST"]
        found: Dict[Tuple[int, int], str] = {}
        for p in names:
            try:
                found.setdefault(part.port(p), p)
            except Exception:
                continue
        return [(p, c) for c, p in found.items()]
        return problems


def _collinear_overlap(a1, b1, a2, b2):
    """Return the overlapping span if two axis-aligned segments are collinear and overlap.

    Only segments that share the same line count: two verticals must have equal x, two horizontals
    equal y. A single shared endpoint does NOT count - Logisim treats a corner touch as a
    junction only when a port is involved, and flagging every touch would drown the real problem.
    """
    def vert(a, b):
        return a[0] == b[0] and a[1] != b[1]

    def horiz(a, b):
        return a[1] == b[1] and a[0] != b[0]

    if vert(a1, b1) and vert(a2, b2) and a1[0] == a2[0]:
        lo1, hi1 = sorted((a1[1], b1[1]))
        lo2, hi2 = sorted((a2[1], b2[1]))
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        if hi > lo:
            return f"x={a1[0]}, y={lo}..{hi}"
    if horiz(a1, b1) and horiz(a2, b2) and a1[1] == a2[1]:
        lo1, hi1 = sorted((a1[0], b1[0]))
        lo2, hi2 = sorted((a2[0], b2[0]))
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        if hi > lo:
            return f"y={a1[1]}, x={lo}..{hi}"
    return None


def _orthogonal(a: Tuple[int, int], b: Tuple[int, int]):
    if a == b:
        return []
    if a[0] != b[0] and a[1] != b[1]:
        mid = (b[0], a[1])
        return [(a, mid), (mid, b)]
    return [(a, b)]


def _on_segment(p: Tuple[int, int], seg) -> bool:
    (x1, y1), (x2, y2) = seg
    if x1 == x2:                      # vertical
        return p[0] == x1 and min(y1, y2) <= p[1] <= max(y1, y2)
    if y1 == y2:                      # horizontal
        return p[1] == y1 and min(x1, x2) <= p[0] <= max(x1, x2)
    return False


# ----------------------------------------------------------------------------------------------
# Serialisation
# ----------------------------------------------------------------------------------------------

def tunnel_facing(neighbour_on_the_right: bool) -> str:
    """Which `facing` draws a Tunnel glyph AWAY from its neighbour.

    MEASURED on the shipped jar (see `geom.py show Tunnel facing=... label=...`):

        facing="west"   box = loc + (   0, -11)   width 8*len(label)+10   -> glyph extends RIGHT
        facing="east"   box = loc + (-w,  -11)                           -> glyph extends LEFT

    The names read backwards, which is why choosing a constant per role kept regressing: the right
    answer depends on WHERE THE NEIGHBOUR IS, not on whether the Tunnel drives or receives.

        neighbour on the RIGHT  ->  "east"   (glyph extends LEFT, into free space)
        neighbour on the LEFT   ->  "west"   (glyph extends RIGHT, into free space)

    A consumer Tunnel asks about the component it sits on: a gate is to its RIGHT, an output Pin (whose
    own body extends LEFT) is to its LEFT. A driver Tunnel always has its source on the LEFT.
    """
    return "east" if neighbour_on_the_right else "west"


def to_xml(c: SeqCircuit) -> str:
    L = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
         '<project source="5.1.0dev" version="1.0">',
         '  <lib desc="#Wiring" name="0"/>',
         '  <lib desc="#Gates" name="1"/>',
         '  <lib desc="#Memory" name="5"/>',
         '  <lib desc="#Arithmetic" name="3"/>',
         f'  <main name="{c.name}"/>',
         f'  <circuit name="{c.name}">']
    for p in c.parts:
        attrs = ""
        if p.kind == "CONST":
            w = f'0x{p.value:x}'
            attrs = f'<a name="value" val="{w}"/>'
            if p.width > 1:
                attrs += f'<a name="width" val="{p.width}"/>'
        elif p.kind == "RAM":
            # addrWidth and dataWidth are SEPARATE attributes. Omitting either makes Logisim use its
            # own default, so the port widths silently disagree with the wiring - the same silent
            # default family as the Tunnel's 1 bit, the Constant's TRUE and the Adder's 8 bits.
            attrs = (f'<a name="addrWidth" val="{p.addr_width}"/>'
                     f'<a name="dataWidth" val="{p.width}"/>')
        elif p.kind in ("DFF", "TFF", "JKFF", "SRFF", "REG", "ADDER"):
            # The Adder MUST carry its width: omitting it makes Logisim default to EIGHT bits, so
            # a 4-bit register feeding it produced a width mismatch and every output read `UUU U`.
            # Measured with SamplePorts - the A/B/Sum ports came back width=8.
            if p.kind == "ADDER":
                attrs += f'<a name="width" val="{p.width}"/>'
            else:
                if p.width > 1:
                    attrs += f'<a name="width" val="{p.width}"/>'
                # `trigger` is written EXPLICITLY. The default is `rising`
                # (Register.java:225 -> StdAttr.TRIG_RISING), which is what every spec here assumes,
                # but relying on it means an upstream default change would silently redefine the
                # edge every sequential circuit is built around.
                #
                # Writing it is the lesson of the Comparator defect: a relied-upon default is an
                # unmeasured assumption until it is written or measured, and the four known silent
                # defaults (Tunnel width 1, Constant TRUE, Adder width 8, Comparator signed) were all
                # found the hard way.
                attrs += f'<a name="trigger" val="{p.trigger}"/>'
        elif p.kind == "TUNNEL":
            attrs = f'<a name="label" val="{p.label}"/>'
            if p.facing:
                attrs += f'<a name="facing" val="{p.facing}"/>'
        elif p.kind == "PIN":
            # Pin.type defaults to `input`, so an output pin MUST say so explicitly.
            attrs = f'<a name="label" val="{p.label}"/><a name="type" val="{p.pin_type}"/>'
            if p.pin_type == "output":
                attrs += '<a name="facing" val="west"/>'
            if p.width > 1:
                attrs += f'<a name="width" val="{p.width}"/>'
        if p.kind == "DFF":
            p_xml = "D Flip-Flop"
        else:
            p_xml = p.xml_name
        L.append(f'    <comp lib="{p.lib}" loc="({p.loc[0]},{p.loc[1]})" name="{p_xml}">{attrs}</comp>')
    for a, b in c.wires:
        L.append(f'    <wire from="({a[0]},{a[1]})" to="({b[0]},{b[1]})"/>')
    L += ['  </circuit>', '</project>']
    return "\n".join(L) + "\n"


def write(c: SeqCircuit, path) -> pathlib.Path:
    path = pathlib.Path(path)
    _ph.write_circ(path, to_xml(c))
    return path


# ----------------------------------------------------------------------------------------------
# Run + verify
# ----------------------------------------------------------------------------------------------

def decode(raw: bytes) -> str:
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be", errors="replace").lstrip("\ufeff")
    return raw.decode("utf-8-sig", errors="replace")


def run_clocked(circ: pathlib.Path, seconds: float, blob: pathlib.Path) -> List[List[str]]:
    """Run the clocked loop for `seconds`, writing stdout to a FILE (never a pipe).

    Two things here are load-bearing and were both bugs at some point:

    * **Output goes to a file, not a pipe.** With a pipe the JVM blocks once the buffer fills and
      the run deadlocks.
    * **Wait for the writer to exit, then settle before reading.** `taskkill /T` kills `java.exe`
      while the JVM still has buffered stdout. Reading the file immediately truncates the evidence:
      this made a register that *does* latch (`0000` -> `1010`) look like it produced a single row.
      We therefore poll for a stable size instead of assuming the bytes have landed.
    """
    if blob.exists():
        blob.unlink()
    with open(blob, "wb") as fh:
        proc = subprocess.Popen(
            ["java", "-Djava.awt.headless=true", "-jar", str(JAR),
             "--locale", "en", "--tty", "table", str(circ)],
            stdout=fh, stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            proc.wait(timeout=20)

    # settle: wait until the file stops growing (or we give up)
    prev = -1
    for _ in range(40):
        size = blob.stat().st_size if blob.exists() else 0
        if size == prev and size > 0:
            break
        prev = size
        time.sleep(0.05)

    lines = [l for l in decode(blob.read_bytes()).replace("\r\n", "\n").split("\n") if l.strip()]
    rows = [l.split() for l in lines]

    # The clocked loop does not self-terminate, so a few seconds yields ~1e6 rows and a
    # multi-megabyte blob. Keep a bounded prefix as the retained evidence; we only ever compare a
    # bounded number of cycles anyway.
    KEEP = 500
    if len(rows) > KEEP:
        blob.write_text("\n".join(" ".join(r) for r in rows[:KEEP]) + "\n... (truncated)\n",
                        encoding="utf-8", newline="\n")
    return rows


def verify(spec: dict, circ_path: pathlib.Path, seconds: float, cycles: int,
           blob_dir: pathlib.Path) -> Tuple[int, dict]:
    """Check the clocked run against a golden model of the STATE SEQUENCE.

    The `halt` pin is excluded from the printed columns, so the printed tokens are the observed
    signals in canonical (top-down) order. Rather than trust header NAMES - which fall back to
    positional names when a pin is unlabelled - the watched value is taken by COLUMN INDEX, and the
    header row is treated as the row-0 state because a row is printed before the first clock edge.

    The golden model lives in the spec as `sequence`: the expected repeating period. Any rotation is
    accepted ONLY if the observed stream is a clean repetition of that period, and the rotation is
    reported so the result can be judged.
    """
    blob = blob_dir / (circ_path.stem + ".table.txt")
    rows = run_clocked(circ_path, seconds, blob)
    if not rows:
        return 2, {"status": "harness_error", "error": "empty table output (no rows at all)"}

    header = rows[0]
    data = rows[1:]
    ncols = len(header)

    # WATCHED COLUMNS. A single-column watch cannot validate a multi-bit state machine: for a
    # ripple counter, watching only bit 0 still "passes" even if the ripple link to bit 1 is
    # broken. So the spec may watch several columns, and each observed state is the tuple of those
    # columns. `sequence` is then a period of STATE TUPLES.
    cols = spec.get("watch_columns")
    if cols is None:
        cols = [int(spec.get("watch_column", 0))]
    cols = [int(c) for c in cols]
    bad_col = [c for c in cols if c < 0 or c >= ncols]
    if bad_col:
        return 2, {"status": "harness_error",
                   "error": f"watch columns {bad_col} out of range for {ncols} columns; "
                            f"header={header}"}
    if not cols:
        return 2, {"status": "harness_error", "error": "no watched columns"}

    def state_of(tokens) -> Optional[List[str]]:
        vals = []
        for c in cols:
            if c >= len(tokens):
                return None
            v = tokens[c]
            vals.append(v)
        return vals

    # Row printed before the first edge = the row-0 state, so the stream starts with the header.
    first = state_of(header)
    if first is None:
        return 2, {"status": "harness_error", "error": "header too short for watched columns"}
    stream: List[List[str]] = [first]
    for r in data:
        st = state_of(r)
        if st is None:
            continue
        if any(v.startswith("U") or v.startswith("E") for v in st):
            return 2, {"status": "harness_error",
                       "error": f"a watched signal is undefined ({st}); it was not driven",
                       "header": header, "rows": len(data)}
        stream.append(st)

    # Flatten to the shape the comparison uses: a single watched column stays a flat list of
    # strings (so existing specs keep working), several columns become a list of tuples.
    want = spec.get("sequence")

    # Normalise BOTH sides to tuples before any comparison. `state_of` yields lists and `period`
    # was built as tuples, so `[0,0] == (0,0)` is False and the rotation search never matched - the
    # comparator then reported a perfectly correct counter as `failed`. Type-normalising here is the
    # fix; comparing container types implicitly is what broke it.
    if len(cols) == 1:
        flat: List = [s[0] for s in stream]
        period: List = ([] if want is None
                        else [p[0] if isinstance(p, (list, tuple)) else p for p in want])
    else:
        flat = [tuple(s) for s in stream]
        period = ([] if want is None
                  else [tuple(p) if isinstance(p, (list, tuple)) else (p,) for p in want])

    if want is None:
        return 0, {"status": "ran", "header": header, "columns": ncols, "watched": cols,
                   "rows": len(data), "unique_states": sorted({str(s) for s in flat})[:16],
                   "stream_head": flat[:16], "blob": str(blob)}

    if period and any(len(p) != len(cols) for p in period if isinstance(p, tuple)):
        return 2, {"status": "harness_error",
                   "error": f"expected each period entry to hold {len(cols)} values (one per "
                            f"watched column {cols}), but got entries of length "
                            f"{sorted({len(p) for p in period if isinstance(p, tuple)})}; "
                            f"period={period}",
                   "header": header}
    if not period:
        return 2, {"status": "harness_error", "error": "sequence is empty"}
    stream = flat
    # A stream that is a rotation of the period counts as matching, and the rotation is reported.
    # IMPORTANT: a definite but WRONG sequence must be reported as `failed`, never as a harness
    # error. Mixing those up would let a broken circuit masquerade as a tooling problem.
    # ROTATION SEARCH STARTS AT ROW 0 OR ROW 1, and the choice matters.
    #
    # The clocked loop prints a row BEFORE the first clock edge, so `stream[0]` is the POWER-ON state
    # and is not part of the repeating cycle. When that power-on state happens to be a value the
    # period itself contains, starting the search at row 0 is fine and additionally verifies it -
    # that is the counter case, whose first row is a genuine member of the period. When it does NOT
    # (a register that powers up at 0 and then holds 0xa forever), row 0 can never match a rotation
    # and searching from row 0 rejects a correct circuit, because the period is then just the
    # settled value `['1010']`. Try row 0 first, then row 1.
    def _rotation_start(at: int):
        return next((k for k in range(len(period))
                     if stream[at:at + len(period)] == period[k:] + period[:k]), None)

    rot = _rotation_start(0)
    header_is_periodic = rot is not None
    if rot is None and len(stream) > 1:
        rot = _rotation_start(1)

    # IMPORTANT: rows are emitted only when an output CHANGES, so a signal that never moves
    # produces exactly ONE row. A short stream therefore means "the output never changed", which
    # is a definite (and usually wrong) behaviour - NOT a harness problem. Verified: the
    # T-tied-high toggle emits 583,094 rows, while T-tied-low emits exactly 1.
    if rot is None:
        return 1, {"status": "failed", "header": header,
                   "stream": stream[:24],
                   "cycles_observed": len(stream),
                   "expected_period": period,
                   "reason": "the observed value stream is not a rotation of the expected period",
                   "note": ("only 1 row means the output NEVER CHANGED during the whole run"
                            if len(stream) == 1 else
                            "rows are emitted only when an output changes"),
                   "total_rows_captured": len(data), "blob": str(blob)}

    n = min(cycles, len(stream))
    full = n // len(period)

    # How much evidence is ENOUGH depends on whether the circuit keeps changing.
    #
    # * A free-running circuit yields far more rows than requested, so demanding at least two full
    #   periods is right: it catches a capture that is too short to be meaningful.
    # * A circuit that CONVERGES AND HOLDS yields exactly as many rows as it has real changes and
    #   then stops. A register that latches once produces exactly `len(period)` rows. Demanding two
    #   periods there rejected a perfectly correct, fully observed load-and-hold as a harness error.
    #
    # So: when the stream is shorter than what we asked for, it exhausts itself and ONE complete
    # period is sufficient evidence. Require two periods only when the run was still producing rows.
    exhausted = len(stream) < cycles
    needed = 1 if exhausted else 2
    if full < needed:
        return 2, {"status": "harness_error",
                   "error": (f"matched the period start but only captured {len(stream)} rows; need "
                             f"at least {needed * len(period)} value changes. This can happen when "
                             f"the circuit is still converging on its first edge - increase "
                             f"--seconds"),
                   "header": header, "stream": stream[:16]}

    # `rot` is found as: stream[at:at+len(period)] == period[rot:] + period[:rot]. So the stream
    # begins at period[rot], which means stream[i] must equal period[(i + rot) % len] for i >= at.
    # Using `(i - rot)` instead was a latent bug: it happened to work for the 1-bit period [1,0]
    # because that period is symmetric under a shift, but it reported a CORRECT 2-bit counter as
    # failed. The orientation must follow directly from how the rotation was matched.
    #
    # Row 0 is the power-on state. When the period was matched from row 0 it must also satisfy the
    # pattern; when it was matched from row 1 it is the pre-edge state and is EXCLUDED from the
    # comparison, because by construction it is not part of the cycle.
    start = 0 if header_is_periodic else 1

    def expect(i: int):
        return period[(i - start + rot) % len(period)]

    # Rows BEFORE `start` are pre-edge states with no defined expectation, so they must be EXCLUDED
    # from the comparison rather than checked against `expect(0)`. Checking them reported the
    # power-on row itself as a mismatch.
    ok = all(stream[i] == expect(i) for i in range(start, n))
    bad = [i for i in range(start, n) if stream[i] != expect(i)][:8]
    return (0 if ok else 1), {
        "status": "passed" if ok else "failed",
        "header": header,
        "watched": cols,
        "cycles_checked": n,
        "periods_exercised": full,
        "rotation": rot,
        "power_on_row_excluded": not header_is_periodic,
        "expected_period": period,
        "stream_head": stream[:24],
        "mismatches": [{"cycle": i, "expected": expect(i), "actual": stream[i]} for i in bad],
        "total_rows_captured": len(data),
        "blob": str(blob),
    }


def _build_or_die(spec: dict) -> SeqBuilder:
    b = SeqBuilder(spec)
    probs = b.validate()
    if probs:
        raise SpecError("; ".join(probs))
    return b


def main() -> int:
    ap = argparse.ArgumentParser(description="Build or verify a SEQUENTIAL .circ (clocked simulation).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="write a .circ from a sequential spec")
    pb.add_argument("spec"); pb.add_argument("out")

    pv = sub.add_parser("verify", help="run the clocked loop and check the state sequence")
    pv.add_argument("spec"); pv.add_argument("out")
    pv.add_argument("--seconds", type=float, default=3.0, help="wall-clock bound for the run")
    pv.add_argument("--cycles", type=int, default=16, help="how many cycles to compare")

    a = ap.parse_args()
    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    try:
        b = _build_or_die(spec)
    except SpecError as e:
        print(json.dumps({"status": "spec_error", "error": str(e)}, indent=2, ensure_ascii=False))
        return 3

    out = pathlib.Path(a.out)
    if a.cmd == "build":
        print("wrote", write(b.circuit, out),
              f"({len(b.circuit.parts)} parts, {len(b.circuit.wires)} wires)")
        return 0

    write(b.circuit, out)
    code, result = verify(spec, out, a.seconds, a.cycles, out.parent)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

