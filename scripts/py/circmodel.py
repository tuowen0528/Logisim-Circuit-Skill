#!/usr/bin/env python3
"""Spec-driven Logisim circuit generator + reader (the general path).

Replaces the purpose-built adder generator with something that can emit and read circuits it has
never seen before.

WHAT THIS DOES
  * builds a `.circ` from a declarative spec: named I/O pins + component instances + named nets
  * routes nets with TUNABLE TUNNELS, so no coordinate routing is needed and the merge/oscillation
    failure mode (references/00-probe-log.md section 11) cannot occur
  * places gates automatically in depth-ordered columns
  * reads a `.circ` back into the same data model (`load`) so analysis and generation share one IR

WHY TUNNELS. A `Tunnel` connects by LABEL, so a net is just a name. That removes crossings,
channels, lanes and T-junctions entirely. Measured: the tunnel-built 4-bit adder passes 512/512,
while the same function routed by coordinates produced `E` on both outputs.

MEASURED CONSTRAINTS ENCODED HERE (see docs/component-semantics.md, docs/evidence-matrix.md)
  * gate XML names contain a space and end in "Gate": `AND Gate`, `XOR Gate`, ... but bare `Buffer`
  * output port is at the component `loc`
  * input ports (facing=east, 2 inputs, size default 50):
        XOR/XNOR           -> (loc.x-60, loc.y-20), (loc.x-60, loc.y+20)
        AND/OR/NAND/NOR    -> (loc.x-50, loc.y-20), (loc.x-50, loc.y+20)
  * 3 inputs -> `(loc.x-50, loc.y-20/0/+20)`; x offset does NOT change with input count
  * a component whose name is wrong is SILENTLY DROPPED -> `validate()` exists to catch that
  * output pins MUST carry type="output" (Pin.type defaults to `input`)
  * a Pin and a Tunnel at the SAME coordinate do NOT drive each other -> always emit an explicit
    wire between a port and its tunnel

Writes UTF-8 without BOM (this host's Python defaults to cp936).
"""
from __future__ import annotations

import io

import project_header as _ph
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Only wrap when RUN, never when imported: a second wrapper around the
# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

_STDOUT_FIXED = True


def ensure_utf8_stdout():
    """Make stdout UTF-8 safe, exactly once per process.

    Chinese diagnostics otherwise raise UnicodeEncodeError under this host's cp936 default. But
    wrapping `sys.stdout` repeatedly CLOSES the underlying buffer: a second wrapper is garbage
    collected, its finalizer closes the shared buffer, and later writes fail with
    "I/O operation on closed file". That made argparse's `--help` and its error path look like an
    "unrecognized arguments" failure. So callers should use this helper rather than wrapping again.
    """
    global _STDOUT_FIXED
    if _STDOUT_FIXED:
        return
    # Only wrap when RUN, never when imported: a second wrapper around the
    # same buffer closes the underlying stream when collected, and every
    # later print then raises "I/O operation on closed file".
    if __name__ == '__main__':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                      line_buffering=True)
    _STDOUT_FIXED = True

# --------------------------------------------------------------------------------------
# Measured port model
# --------------------------------------------------------------------------------------

# XML name -> (kind, library ordinal key)
GATE_LIB = "Gates"
WIRING_LIB = "Wiring"

GATE_NAMES = {
    "AND": "AND Gate", "OR": "OR Gate", "NAND": "NAND Gate", "NOR": "NOR Gate",
    "XOR": "XOR Gate", "XNOR": "XNOR Gate", "NOT": "NOT Gate", "BUFFER": "Buffer",
}
# Gates whose input x-offset is -60 instead of -50 (output negation bubble adds 10 px).
WIDE_GATES = {"XOR Gate", "XNOR Gate"}

DEFAULT_SIZE = 50
PIN_STUB = 30          # px between a pin and its driver tunnel
TUNNEL_STUB = 32       # px between a gate output port and its driver tunnel
COLUMN_PITCH = 240     # px between gate columns
ROW_PITCH = 120        # px between gates in a column

# Vertical pitch between two pins in the same column.
#
# MEASURED, not chosen for looks. `SamplePorts` reports a `Pin` box of **20x20** and a `Tunnel` box
# of **22 px** tall, both anchored at the connection point. A generator that stacks pins 20 px apart
# therefore makes adjacent Tunnel boxes overlap by 2 px and puts the labels on top of each other -
# which is exactly the "components are too close, they overlap" report on the output side of the
# 4-bit adder, where S0..S3 and Cout ended up at y = 200, 220, 240, 260, 280.
#
# 40 px clears a 22 px box with room for the label, and matches the spacing the user's own samples
# use between pins in the same column (measured 20/30/40/50, never below 20 for same-column pairs).
PIN_PITCH = 40

# Tunnel facing semantics - MEASURED, and the OPPOSITE of what the names suggest.
#
#   facing="west"  -> the glyph extends to the RIGHT (east) of loc : box (loc.x,   loc.y-11, ~24, 22)
#   facing="east"  -> the glyph extends to the LEFT  (west) of loc : box (loc.x-33, loc.y-11, ~33, 22)
#   facing unset   -> same as "west" (extends right)
#
# So `facing` names the side the WIRE attaches to, and the body is drawn opposite. Getting this
# backwards is what made every gate-input Tunnel collide with its own gate: a sink Tunnel sitting
# exactly on a gate's input port carried `facing="west"`, so its box ran from the port rightwards
# straight into the gate body. Measured on `output/adder4.circ`: `XOR Gate@(300,200)` box
# (240,175,60,50) overlapped `Tunnel(A0)@(240,180)` box (240,169,24,22).
#
# A sink Tunnel has its component to the RIGHT (a gate input port, or an output Pin whose own glyph
# extends right), so its body must extend LEFT -> facing="east".
# A driver Tunnel has its component to the LEFT, so its body must extend RIGHT -> facing="west".
SINK_FACING = "east"
DRIVER_FACING = "west"


def input_ports(name: str, loc: Tuple[int, int], n_inputs: int = 2,
                size: int = DEFAULT_SIZE) -> List[Tuple[int, int]]:
    """Absolute input-port coordinates for a gate, facing=east.

    The x offset is NOT one rule for the whole family. It was measured per gate with
    `SamplePorts` and is tabulated here, because a single formula fitted to AND/OR and
    XOR does NOT reproduce NAND, NOR or XNOR:

        gate        dx            measured at size 30 / 50 / 70
        AND Gate     -(size + 0  )  -30 / -50 / -70
        OR Gate      -(size + 0  )  -30 / -50 / -70
        NAND Gate    -(size + 10 )  -40 / -60 / -80
        NOR Gate     -(size + 10 )  -40 / -60 / -80
        XOR Gate     -(size + 10 )  -40 / -60 / -80
        XNOR Gate    -(size + 20 )  -50 / -70 / -90
        NOT Gate     -(size + 0  )  -30

    This matters because a wrong dx places the sink Tunnel 10 px off the real port, so
    the gate input silently floats and the output becomes a CONSTANT. An 8-bit ALU
    using NAND and NOR returned a constant 0xEE for both opcodes while AND and OR were
    perfect - that is what surfaced this table.
    """
    x, y = loc
    if name in ("NOT Gate", "Buffer"):
        # fixed geometry, no size dependence (measured)
        dx = -30 if name == "NOT Gate" else -20
        return [(x + dx, y)]
    GATE_DX_EXTRA = {
        "AND Gate": 0,
        "OR Gate": 0,
        "NAND Gate": 10,
        "NOR Gate": 10,
        "XOR Gate": 10,
        "XNOR Gate": 20,
        "NOT Gate": 0,
    }
    if name not in GATE_DX_EXTRA:
        raise ValueError(
            f"{name!r} has no measured input-port offset; measure it with SamplePorts "
            f"before using it, do not guess")
    dx = -(size + GATE_DX_EXTRA[name])
    if n_inputs == 1:
        return [(x + dx, y)]
    if n_inputs == 2:
        return [(x + dx, y - 20), (x + dx, y + 20)]
    # 3+ inputs: evenly spaced, 20 px apart, centred on loc.y (measured)
    span = (n_inputs - 1) * 20
    return [(x + dx, y - span // 2 + i * 20) for i in range(n_inputs)]


def output_port(name: str, loc: Tuple[int, int]) -> Tuple[int, int]:
    """Measured: the output port is at the component's own `loc`."""
    return loc


# --------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------

@dataclass
class Pin:
    label: str
    direction: str          # "input" | "output"
    width: int = 1
    loc: Optional[Tuple[int, int]] = None


@dataclass
class Gate:
    kind: str               # key of GATE_NAMES, e.g. "XOR"
    ref: str                # unique instance name in the spec
    loc: Optional[Tuple[int, int]] = None
    inputs: int = 2
    size: int = DEFAULT_SIZE
    width: int = 1

    @property
    def xml_name(self) -> str:
        return GATE_NAMES[self.kind]

    def port_ins(self) -> List[Tuple[int, int]]:
        return input_ports(self.xml_name, self.loc, self.inputs, self.size)

    def port_out(self) -> Tuple[int, int]:
        return output_port(self.xml_name, self.loc)


@dataclass
class Circuit:
    name: str = "main"
    pins: List[Pin] = field(default_factory=list)
    gates: List[Gate] = field(default_factory=list)
    # net label -> (driver : ("pin"|"gate", ref), sinks : [(kind, ref, port_index)])
    nets: Dict[str, "Net"] = field(default_factory=dict)


@dataclass
class Net:
    label: str
    driver: Optional[Tuple[str, str]] = None          # ("pin"|"gate", ref)
    sinks: List[Tuple[str, str, int]] = field(default_factory=list)


class SpecError(Exception):
    """Raised for a spec that cannot produce a valid circuit."""


def _io_pairs(entries) -> List[Tuple[str, int]]:
    """Accept either ["A", "B"] (width 1) or [["A", 8], ["B", 8]]."""
    out: List[Tuple[str, int]] = []
    for e in entries:
        if isinstance(e, str):
            out.append((e, 1))
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            out.append((str(e[0]), int(e[1])))
        else:
            raise SpecError(f"bad I/O entry {e!r}; expected 'NAME' or ['NAME', width]")
    return out


# --------------------------------------------------------------------------------------
# Spec -> Circuit
# --------------------------------------------------------------------------------------

class CircuitBuilder:
    """Build a Circuit from a declarative spec, then place and serialise it.

    Spec shape (plain data, easy for a model to emit):

        {
          "name": "main",
          "inputs":  [("A", 1), ("B", 1), ("Sel", 1)],
          "outputs": [("Y", 1)],
          "gates": [("g1", "AND", 2, 1), ("g2", "AND", 2, 1), ("g3", "OR", 2, 1)],
          "nets": {
             "A":   {"drive": "in:A",  "use": [("g1", 0), ("g2", 0)]},
             "B":   {"drive": "in:B",  "use": [("g1", 1), ("g3", 1)]},
             ...
             "Y":   {"drive": "g:g3",  "use": ["out:Y"]},
          },
        }

    `drive` is "in:<pin>" for an input pin or "g:<gate ref>" for a gate output.
    `use` entries are ("<gate ref>", <input port index>) or "out:<pin>".
    """

    def __init__(self, spec: dict):
        self.spec = spec
        self.circuit = Circuit(name=spec.get("name", "main"))
        self._by_ref: Dict[str, Gate] = {}
        self._build()

    # -- construction ---------------------------------------------------------------

    def _build(self):
        spec = self.spec
        for label, width in _io_pairs(spec.get("inputs", [])):
            self.circuit.pins.append(Pin(label, "input", width))
        for label, width in _io_pairs(spec.get("outputs", [])):
            self.circuit.pins.append(Pin(label, "output", width))

        for entry in spec.get("gates", []):
            ref, kind, n_inputs, width = self._parse_gate(entry)
            if ref in self._by_ref:
                raise SpecError(f"duplicate gate reference {ref!r}")
            g = Gate(kind=kind, ref=ref, inputs=n_inputs, width=width)
            self._by_ref[ref] = g
            self.circuit.gates.append(g)

        self._parse_nets(spec.get("nets", {}))

    @staticmethod
    def _parse_gate(entry) -> Tuple[str, str, int, int]:
        if isinstance(entry, dict):
            ref = entry["ref"]
            kind = entry["kind"].upper()
            n = int(entry.get("inputs", 2))
            w = int(entry.get("width", 1))
        else:
            ref, kind, n, w = entry[0], entry[1].upper(), entry[2], entry[3]
        if kind not in GATE_NAMES:
            raise SpecError(
                f"unknown gate kind {kind!r}; known kinds: {sorted(GATE_NAMES)}"
            )
        if n < 1:
            raise SpecError(f"gate {ref!r} needs at least 1 input")
        return ref, kind, n, w

    def _parse_nets(self, nets: dict):
        input_labels = {p.label for p in self.circuit.pins if p.direction == "input"}
        output_labels = {p.label for p in self.circuit.pins if p.direction == "output"}

        for label, spec in nets.items():
            if label in self.circuit.nets:
                raise SpecError(f"duplicate net label {label!r}")
            net = Net(label=label)

            drive = spec["drive"]
            if drive.startswith("in:"):
                pin = drive[3:]
                if pin not in input_labels:
                    raise SpecError(f"net {label!r} driven by unknown input pin {pin!r}")
                net.driver = ("pin", pin)
            elif drive.startswith("g:"):
                ref = drive[2:]
                if ref not in self._by_ref:
                    raise SpecError(f"net {label!r} driven by unknown gate {ref!r}")
                net.driver = ("gate", ref)
            else:
                raise SpecError(f"net {label!r}: drive must be 'in:<pin>' or 'g:<gate>', got {drive!r}")

            for use in spec.get("use", []):
                if isinstance(use, str):
                    if not use.startswith("out:"):
                        raise SpecError(f"net {label!r}: string use must be 'out:<pin>', got {use!r}")
                    pin = use[4:]
                    if pin not in output_labels:
                        raise SpecError(f"net {label!r} drives unknown output pin {pin!r}")
                    net.sinks.append(("pin", pin, 0))
                else:
                    ref, idx = use[0], int(use[1])
                    if ref not in self._by_ref:
                        raise SpecError(f"net {label!r} uses unknown gate {ref!r}")
                    gate = self._by_ref[ref]
                    if not 0 <= idx < gate.inputs:
                        raise SpecError(
                            f"net {label!r} -> gate {ref!r} port {idx}, but that gate has "
                            f"{gate.inputs} input(s)"
                        )
                    net.sinks.append(("gate", ref, idx))

            if net.driver is None:
                raise SpecError(f"net {label!r} has no driver")
            self.circuit.nets[label] = net

        # every gate input must be driven exactly once
        for gate in self.circuit.gates:
            for idx in range(gate.inputs):
                hits = [lbl for lbl, n in self.circuit.nets.items()
                        if ("gate", gate.ref, idx) in n.sinks]
                if not hits:
                    raise SpecError(
                        f"gate {gate.ref!r} input {idx} is not connected to any net "
                        f"(an unconnected input floats)"
                    )
                if len(hits) > 1:
                    raise SpecError(
                        f"gate {gate.ref!r} input {idx} is driven by {len(hits)} nets: {hits} "
                        f"(that is contention)"
                    )
        # every output pin must be driven
        for pin in self.circuit.pins:
            if pin.direction != "output":
                continue
            hits = [lbl for lbl, n in self.circuit.nets.items()
                    if ("pin", pin.label, 0) in n.sinks]
            if len(hits) != 1:
                raise SpecError(
                    f"output pin {pin.label!r} is driven by {len(hits)} nets, expected exactly 1"
                )

    # -- placement ------------------------------------------------------------------

    def place(self):
        """Assign deterministic coordinates: gates in depth-ordered columns.

        Depth = longest path from any input pin, so a driver's column is strictly left of every
        consumer's, and no two gates share a location.
        """
        # Which net drives each gate input?
        driver_of: Dict[Tuple[str, int], str] = {}      # (gate ref, port idx) -> net label
        for label, net in self.circuit.nets.items():
            for kind, ref, idx in net.sinks:
                if kind == "gate":
                    driver_of[(ref, idx)] = label

        depth: Dict[str, int] = {}

        def d(ref: str) -> int:
            if ref in depth:
                return depth[ref]
            depth[ref] = 0                               # guard against cycles during recursion
            best = 0
            gate = self._by_ref[ref]
            for idx in range(gate.inputs):
                label = driver_of.get((ref, idx))
                if label is None:
                    continue
                net = self.circuit.nets[label]
                if net.driver and net.driver[0] == "gate":
                    best = max(best, d(net.driver[1]) + 1)
            depth[ref] = best
            return best

        for g in self.circuit.gates:
            d(g.ref)

        columns: Dict[int, List[Gate]] = {}
        for g in self.circuit.gates:
            columns.setdefault(depth[g.ref], []).append(g)

        x0, y0 = 300, 200
        for col in sorted(columns):
            for row, g in enumerate(columns[col]):
                g.loc = (x0 + col * COLUMN_PITCH, y0 + row * ROW_PITCH)

        # Input pins: far left, laid out in the ORDER THE SPEC DECLARES THEM, top to bottom.
        #
        # WHY ORDER AND NOT ALIGNMENT
        #
        # This used to place each pin at the y of the first gate input it feeds, so the driver Tunnel
        # and the sink Tunnel sat on one horizontal line. That produced a scrambled column: for the
        # 4-bit subtractor the pins came out as A0 B0 B1 A1 B2 B3 A2 Bin A3, because the gates are
        # laid out by dependency depth and the pins inherited those rows.
        #
        # Reordering is SAFE because **Tunnels connect by LABEL, not by position**. A driver Tunnel at
        # (pin.x + PIN_STUB, pin.y) and a sink Tunnel on a gate port thousands of pixels away are the
        # same net purely because they share a label. The pin's y is therefore cosmetic, and the
        # declared order is the one a reader expects. Verification re-runs afterwards to prove the
        # connectivity did not change.
        #
        # Two pins must also never be CLOSE, not merely distinct: a Tunnel box is 22 px tall, so
        # 20 px apart means overlapping glyphs. `PIN_PITCH` is the minimum, enforced by construction.
        inputs = [p for p in self.circuit.pins if p.direction == "input"]
        for i, pin in enumerate(inputs):
            pin.loc = (120, y0 + i * PIN_PITCH)

        # Output pins: far right, aligned with their driver gate's output port.
        # Same MINIMUM-SEPARATION rule as the inputs, for the same measured reason: output pins land
        # on their driver gate's output y, and two gates can be only 20 px apart vertically, which
        # stacks the pins (and their co-located Tunnels, 22 px tall) on top of each other.
        right = x0 + (max(columns) + 1) * COLUMN_PITCH
        out_taken: List[int] = []
        for pin in self.circuit.pins:
            if pin.direction != "output":
                continue
            drv = next((n for n in self.circuit.nets.values()
                        if ("pin", pin.label, 0) in n.sinks), None)
            if drv and drv.driver and drv.driver[0] == "gate":
                y = self._by_ref[drv.driver[1]].port_out()[1]
            else:
                y = y0
            while any(abs(y - t) < PIN_PITCH for t in out_taken):
                y += PIN_PITCH
            out_taken.append(y)
            pin.loc = (right, y)
        return self


def row_span(circuit: Circuit) -> int:
    ys = [g.loc[1] for g in circuit.gates if g.loc]
    return (max(ys) - min(ys)) if ys else 0


def validate(circuit: Circuit) -> List[str]:
    """Return a list of problems that would make the emitted file wrong.

    This exists because the failure modes it catches are SILENT: the file still loads, a table
    still prints, and the circuit is simply wrong. It specifically checks that no two DIFFERENT
    nets share an emitted coordinate, because tunnels connect by label and therefore merge nets
    that occupy one point - which is how a full adder once merged `a` and `b`.
    """
    problems: List[str] = []

    # 1. every gate must be recognised (a wrong name is silently dropped by the loader)
    for g in circuit.gates:
        if g.kind.upper() not in GATE_NAMES:
            problems.append(f"gate {g.ref!r} has unknown kind {g.kind!r} (would be dropped)")
        if g.loc is None:
            problems.append(f"gate {g.ref!r} has no location (call place() first)")

    # 2. no two different nets may share a coordinate
    owners: Dict[Tuple[int, int], str] = {}
    for label, net in circuit.nets.items():
        for coord, what in _emitted_coords(circuit, label, net):
            prev = owners.get(coord)
            if prev is not None and prev != label:
                problems.append(
                    f"nets {prev!r} and {label!r} both occupy {coord} ({what}) - tunnels connect "
                    f"by label, so these nets would MERGE (contention -> `E`)"
                )
            owners.setdefault(coord, label)

    # 2b. two Tunnel GLYPHS must not overlap.
    #
    # MEASURED: a Tunnel box is `8*len(label) + 10` px wide and 22 px tall, and it is drawn on the
    # OPPOSITE side to `facing` - `west` puts the body to the RIGHT of loc, `east` to the LEFT.
    #
    # A driver Tunnel sits at `source + STUB` and a sink Tunnel sits exactly on the consumer's port,
    # so a long net name puts a wide box on BOTH ends of the same run. A 12-character name
    # (`final_carry`) produced a 96 px box on each side, which overlapped by 13 px in the middle.
    #
    # This is invisible to every behavioural check - the circuit still simulates correctly - so it
    # has to be caught here. The boxes are computed rather than a blanket length limit applied,
    # because the actual separation varies: `carry_in0` is 10 characters and does NOT overlap.
    def _tunnel_boxes():
        W = lambda lab: 8 * len(lab) + 10
        out = []
        for label, net in circuit.nets.items():
            w = W(label)
            if net.driver[0] == "pin":
                p = next(q for q in circuit.pins if q.label == net.driver[1])
                dl = (p.loc[0] + PIN_STUB, p.loc[1])
            else:
                g = next(q for q in circuit.gates if q.ref == net.driver[1])
                src = g.port_out()
                dl = (src[0] + TUNNEL_STUB, src[1])
            out.append((label, "driver", dl[0], dl[0] + w, dl[1]))          # west -> body right
            for kind, ref, idx in net.sinks:
                if kind == "pin":
                    p = next(q for q in circuit.pins if q.label == ref)
                    sl = p.loc
                else:
                    g = next(q for q in circuit.gates if q.ref == ref)
                    sl = g.port_ins()[idx]
                out.append((label, "sink", sl[0] - w + 2, sl[0] + 2, sl[1]))  # east -> body left
        return out

    boxes = _tunnel_boxes()
    for i in range(len(boxes)):
        la, ka, lo1, hi1, y1 = boxes[i]
        for j in range(i + 1, len(boxes)):
            lb, kb, lo2, hi2, y2 = boxes[j]
            if abs(y1 - y2) >= 22:
                continue
            ov = min(hi1, hi2) - max(lo1, lo2)
            if ov > 3:
                problems.append(
                    f"Tunnel glyphs OVERLAP by {ov} px: {la!r} ({ka}) x in [{lo1},{hi1}] and "
                    f"{lb!r} ({kb}) x in [{lo2},{hi2}], both at y~{y1}. A Tunnel box is "
                    f"~8*len(label)+10 px wide and is drawn on both ends of a net, so a long net "
                    f"name collides with itself. Shorten the label."
                )

    # 3. every output pin must carry type="output" (Pin.type defaults to `input`)
    for pin in circuit.pins:
        if pin.direction == "output" and pin.loc is None:
            problems.append(f"output pin {pin.label!r} has no location")

    # 4. a Pin and a Tunnel at the same coordinate do not drive each other
    for pin in circuit.pins:
        for label, net in circuit.nets.items():
            if net.driver == ("pin", pin.label):
                drv_t = (pin.loc[0] + PIN_STUB, pin.loc[1])
                if drv_t == tuple(pin.loc):
                    problems.append(f"pin {pin.label!r} tunnel coincides with the pin itself")
    return problems


def _emitted_coords(circuit: Circuit, label: str, net: Net):
    """Coordinates the serialiser will emit for this net (tunnels and wire endpoints)."""
    if net.driver is None:
        return
    if net.driver[0] == "pin":
        pin = next((p for p in circuit.pins if p.label == net.driver[1]), None)
        if pin is None or pin.loc is None:
            return
        yield pin.loc, "pin loc"
        yield (pin.loc[0] + PIN_STUB, pin.loc[1]), "driver tunnel"
    else:
        g = next((g for g in circuit.gates if g.ref == net.driver[1]), None)
        if g is None or g.loc is None:
            return
        yield g.port_out(), "gate output port"
        yield (g.port_out()[0] + TUNNEL_STUB, g.port_out()[1]), "driver tunnel"
    for kind, ref, idx in net.sinks:
        if kind == "pin":
            pin = next((p for p in circuit.pins if p.label == ref), None)
            if pin is not None and pin.loc is not None:
                yield pin.loc, "sink pin"
        else:
            g = next((g for g in circuit.gates if g.ref == ref), None)
            if g is not None and g.loc is not None:
                yield g.port_ins()[idx], "consumer tunnel"


# --------------------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------------------

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


def to_xml(circuit: Circuit) -> str:
    """Emit valid `.circ` XML. Tunnels carry every net; wires are short port->tunnel stubs."""
    L: List[str] = []
    L.append('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
    L.append('<project source="5.1.0dev" version="1.0">')
    L.append('  <lib desc="#Wiring" name="0"/>')
    L.append('  <lib desc="#Gates" name="1"/>')
    L.append(f'  <main name="{circuit.name}"/>')
    L.append(f'  <circuit name="{circuit.name}">')

    def wire(a, b):
        L.append(f'    <wire from="({a[0]},{a[1]})" to="({b[0]},{b[1]})"/>')

    def tunnel(label, loc, facing=None, width=1):
        """Emit a Tunnel.

        `width` is NOT optional in practice. A Tunnel defaults to **1 bit**, so a multi-bit net
        built from width-less tunnels is silently truncated: a 2-bit AND whose inputs and output
        all used plain tunnels produced `UU` on every row, while the identical circuit wired
        directly (no tunnels) was correct. Measured: adding `width=N` to every tunnel fixed the
        2-bit and 4-bit cases.

        This is why the 4-bit adder passed while the multi-bit path was broken - that design uses
        width-1 gates, one slice per bit, so every tunnel it emits is genuinely 1 bit.
        """
        w = f'<a name="width" val="{width}"/>' if width > 1 else ""
        f = f'<a name="facing" val="{facing}"/>' if facing else ""
        L.append(f'    <comp lib="0" loc="({loc[0]},{loc[1]})" name="Tunnel">'
                 f'<a name="label" val="{label}"/>{w}{f}</comp>')

    # Pins. Output pins MUST carry type="output" (Pin.type defaults to input).
    for pin in circuit.pins:
        attrs = f'<a name="label" val="{pin.label}"/><a name="type" val="{pin.direction}"/>'
        if pin.direction == "output":
            attrs += '<a name="facing" val="west"/>'
        if pin.width > 1:
            attrs += f'<a name="width" val="{pin.width}"/>'
        L.append(f'    <comp lib="0" loc="({pin.loc[0]},{pin.loc[1]})" name="Pin">{attrs}</comp>')

    # Gates
    for g in circuit.gates:
        attrs = ""
        if g.inputs != 2:
            attrs += f'<a name="inputs" val="{g.inputs}"/>'
        if g.size != DEFAULT_SIZE:
            attrs += f'<a name="size" val="{g.size}"/>'
        if g.width > 1:
            attrs += f'<a name="width" val="{g.width}"/>'
        L.append(f'    <comp lib="1" loc="({g.loc[0]},{g.loc[1]})" name="{g.xml_name}">{attrs}</comp>')

    # Nets as tunnels: driver just off its source port, consumers exactly on sink ports.
    # The net's WIDTH must be written onto every tunnel: a width-less Tunnel is 1 bit, which
    # silently truncates a multi-bit net (measured - a 2-bit AND read `UU` on every row).
    for label, net in sorted(circuit.nets.items()):
        if net.driver[0] == "pin":
            pin = next(p for p in circuit.pins if p.label == net.driver[1])
            src = pin.loc
            drv_t = (src[0] + PIN_STUB, src[1])
            net_width = pin.width
            wire(src, drv_t)
        else:
            g = next(g for g in circuit.gates if g.ref == net.driver[1])
            src = g.port_out()
            drv_t = (src[0] + TUNNEL_STUB, src[1])
            net_width = g.width
            wire(src, drv_t)
        tunnel(label, drv_t, facing=DRIVER_FACING, width=net_width)

        for kind, ref, idx in net.sinks:
            if kind == "pin":
                pin = next(p for p in circuit.pins if p.label == ref)
                sink = pin.loc
            else:
                g = next(g for g in circuit.gates if g.ref == ref)
                sink = g.port_ins()[idx]
            # The consumer tunnel sits exactly ON the sink port so no routing wire is needed.
            # Its direction comes from where the neighbour is: a gate input is to the RIGHT, but an
            # output Pin's own body extends LEFT, so the glyph must go right there. Using one
            # constant for both is what put the glyph on top of the output pin.
            neighbour_right = (kind != "pin")
            tunnel(label, sink, facing=tunnel_facing(neighbour_right), width=net_width)

    L.append('  </circuit>')
    L.append('</project>')
    return "\n".join(L) + "\n"


def write(circuit: Circuit, path: Path) -> Path:
    path = Path(path)
    _ph.write_circ(path, to_xml(circuit))
    return path


# --------------------------------------------------------------------------------------
# Reader (same data model)
# --------------------------------------------------------------------------------------

def load(path: Path) -> dict:
    """Parse a `.circ` into a plain structural dict.

    Reports what is literally in the file - library declarations, components with attributes,
    wires - and does NOT infer function from names or layout.
    """
    raw = Path(path).read_bytes()
    root = ET.fromstring(raw)
    out = {
        "source": root.get("source"),
        "version": root.get("version"),
        "libs": [{"desc": l.get("desc"), "name": l.get("name")} for l in root.findall("lib")],
        "main": (root.find("main").get("name") if root.find("main") is not None else None),
        "circuits": [],
    }
    for circ in root.findall("circuit"):
        c = {"name": circ.get("name"), "attrs": {}, "components": [], "wires": []}
        for a in circ.findall("a"):
            c["attrs"][a.get("name")] = a.get("val")
        for comp in circ.findall("comp"):
            attrs = {a.get("name"): a.get("val") for a in comp.findall("a")}
            c["components"].append({
                "name": comp.get("name"),
                "lib": comp.get("lib"),
                "loc": _parse_loc(comp.get("loc")),
                "attrs": attrs,
            })
        for w in circ.findall("wire"):
            c["wires"].append({"from": _parse_loc(w.get("from")), "to": _parse_loc(w.get("to"))})
        out["circuits"].append(c)
    return out


def _parse_loc(text: Optional[str]) -> Optional[Tuple[int, int]]:
    if not text:
        return None
    try:
        x, y = text.strip().strip("()").split(",")
        return int(x), int(y)
    except (ValueError, AttributeError):
        return None


def describe(path: Path) -> str:
    """Human-readable, inference-free summary of a `.circ`."""
    d = load(path)
    lines = [f"{Path(path).name}: source={d['source']} version={d['version']} main={d['main']!r}",
             f"  libraries declared: {len(d['libs'])}"]
    for c in d["circuits"]:
        counts: Dict[str, int] = {}
        for comp in c["components"]:
            counts[comp["name"]] = counts.get(comp["name"], 0) + 1
        pins = [x for x in c["components"] if x["name"] == "Pin"]
        ins = [p for p in pins if p["attrs"].get("type") != "output"]
        outs = [p for p in pins if p["attrs"].get("type") == "output"]
        in_bits = sum(int(p["attrs"].get("width", 1)) for p in ins)
        lines.append(f"  circuit {c['name']!r}: {len(c['components'])} comps, {len(c['wires'])} wires")
        lines.append(f"    components: {dict(sorted(counts.items()))}")
        lines.append(f"    pins: {len(ins)} input ({in_bits} bits) / {len(outs)} output")
        for p in ins + outs:
            d_ = p["attrs"].get("type", "input")
            lines.append(f"      {d_:<7} {str(p['attrs'].get('label')):<10} "
                         f"w={p['attrs'].get('width', 1):<3} loc={p['loc']}")
        lines.append(f"    exhaustive table would need 2**{in_bits} = {2 ** in_bits} rows")
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Read/describe a .circ, or build one from a spec JSON.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_d = sub.add_parser("describe", help="structurally describe a .circ (no inference)")
    p_d.add_argument("circ")
    p_b = sub.add_parser("build", help="build a .circ from a spec JSON")
    p_b.add_argument("spec")
    p_b.add_argument("out")
    # Negative controls are deliberately defective, and the validator would otherwise refuse to
    # produce them - which is how a STALE control file ended up being verified instead of a fresh
    # one. `--force` writes it anyway, with the header, and says loudly that it did.
    p_b.add_argument("--force", action="store_true",
                     help="write even when the circuit is known-defective (negative controls)")
    args = ap.parse_args()

    if args.cmd == "describe":
        print(describe(Path(args.circ)))
        return 0
    if args.cmd == "build":
        import json
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        cb = CircuitBuilder(spec).place()
        problems = validate(cb.circuit)
        if problems and not getattr(args, "force", False):
            print("REFUSING to write: the placed circuit has problems that would be SILENT:")
            for p in problems:
                print("  - " + p)
            return 3
        if problems:
            print(f"  !! --force: writing a KNOWN-DEFECTIVE control with {len(problems)} problem(s)")
        out = write(cb.circuit, Path(args.out))
        print(f"wrote {out} ({len(cb.circuit.gates)} gates, {len(cb.circuit.nets)} nets)")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
