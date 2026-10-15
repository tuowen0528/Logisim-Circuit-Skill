#!/usr/bin/env python3
"""ir — the single model for a Logisim circuit, and the single source of measured geometry.

WHY THIS FILE EXISTS

There used to be three generators (circmodel, rtllib, seqmodel), each with its own Part class, its own
spec vocabulary, and its own copy of tables measured off the jar. The Adder port table was written
twice; the Tunnel facing rule three times; the JAR path five times. Copying a measured table copies
every error already in it, and this project has paid for that twice: the NAND/NOR input offsets were
wrong in two files that then agreed with each other, and `audit_width.py` carried a second copy of the
same wrong rule, so it reported "undriven" on a circuit that was correct.

The capability matrix showed the real cost. `circmodel` could do neither combinational nor sequential
work; `rtllib` could do combinational but not sequential; `seqmodel` could do sequential but almost no
combinational logic. A CPU needs both at once, in one circuit, and there was no way to say so - the
architecture, not the effort, made it impossible.

So there is now ONE model. A part is a part; whether it is combinational or clocked is a property, not a
different language.

WHAT IS MEASURED HERE, AND HOW

Every offset below came from a port dump against the real jar (`--tty ports` / SamplePorts), not from
reading Logisim's source and not from guessing. Where a configuration has not been measured the code
RAISES rather than interpolating - see `gate_inputs`. The measured ranges are named in each docstring.

    Multiplexer   select 1..8        data x-40 (x-30 for select=1), block starts -10*2^(k-1),
                                     step 10, select pin at x-20 / +10*2^(k-1), out at loc
    Decoder       select 1,3         outputs 20 px apart at x+20, last at y-10, select at loc
    Adder         A B Cin Sum Cout   (-40,-10) (-40,10) (-20,-20) (0,0) (-20,20)
    Subtractor    A B Bin out Bout   (-40,-10) (-40,10) (-20,-20) (0,0) (-20,20)
    Multiplier    A B upper out hi   (-40,-10) (-40,10) (-20,-20) (0,0) (-20,20)
    Comparator    A B > = <          (-40,-10) (-40,10) (0,-10) (0,0) (0,10)
    Register      D CLK EN CLR Q     (0,30) (0,70) (0,50) (30,90) (60,30)
    Flip-flop     D CK Q QB PRESET RESET  (-10,10) (-10,50) (50,10) (50,50) (20,0) (20,60)
    RAM           Din WE OE CK Dout  (0,90) (0,50) (0,60) (0,70) (240,90)
    NOT Gate      in at -size, out at loc
    other gates   in at -(size + extra), extra per kind: AND/OR 0, NAND/NOR/XOR 10, XNOR 20
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[int, int]

# --------------------------------------------------------------------------------------------------
# Libraries. The ordinals are canonical and dense from zero: a component written under a mismatched
# ordinal is SILENTLY DROPPED by the loader - the file opens, the table prints, the part is not there.
# --------------------------------------------------------------------------------------------------
WIRING_LIB = 0
GATES_LIB = 1
PLEXERS_LIB = 2
ARITH_LIB = 3
FP_ARITH_LIB = 4
MEMORY_LIB = 5
IO_LIB = 6

LIB_ORDINALS: Dict[str, int] = {
    "Wiring": 0, "Gates": 1, "Plexers": 2, "Arithmetic": 3, "FPArithmetic": 4,
    "Memory": 5, "I/O": 6, "TTL": 7, "TCL": 8, "Base": 9,
    "BFH-Praktika": 10, "Input/Output-Extra": 11, "Soc": 12,
}

DEFAULT_SIZE = 50

class SpecError(Exception):
    """A spec that cannot be built. Raised instead of guessing."""

class GeometryUnknown(Exception):
    """A configuration that has not been measured. Raised instead of interpolating."""

# --------------------------------------------------------------------------------------------------
# Measured port tables
# --------------------------------------------------------------------------------------------------

ADDER_PORTS = {"A": (-40, -10), "B": (-40, 10), "Cin": (-20, -20), "Sum": (0, 0), "Cout": (-20, 20)}
SUB_PORTS = {"A": (-40, -10), "B": (-40, 10), "Bin": (-20, -20), "out": (0, 0), "Bout": (-20, 20)}
MUL_PORTS = {"A": (-40, -10), "B": (-40, 10), "upper": (-20, -20), "out": (0, 0), "hi": (-20, 20)}
COMP_PORTS = {"A": (-40, -10), "B": (-40, 10), ">": (0, -10), "=": (0, 0), "<": (0, 10)}

# Register.java order: ps[OUT]=(60,30) ps[IN]=(0,30) ps[CK]=(0,70) ps[CLR]=(30,90) ps[EN]=(0,50).
# An earlier table had CLR and EN SWAPPED and the unit test asserted the wrong values too, so the test
# agreed with the bug. The assertion that catches it: every port of a component must be at a DISTINCT
# coordinate, because two ports at one coordinate are electrically the same node.
REG_PORTS = {"Q": (60, 30), "D": (0, 30), "CLK": (0, 70), "CLR": (30, 90), "EN": (0, 50)}
REG_PORT_WIDTH_RULE = {"Q": "data", "D": "data", "CLK": 1, "CLR": 1, "EN": 1}

FF_PORTS = {"D": (-10, 10), "CK": (-10, 50), "Q": (50, 10), "QB": (50, 50),
            "PRESET": (20, 0), "RESET": (20, 60)}

RAM_PORTS = {"Din": (0, 90), "WE": (0, 50), "OE": (0, 60), "CK": (0, 70), "Dout": (240, 90),
             "A": (0, 30)}

NOT_INPUT_DX = -30
BUFFER_INPUT_DX = -20

# Per-kind extra offset for a gate's input column. Getting this wrong makes an input FLOAT, and a
# floating input turns the gate's output into a CONSTANT - which is how a circuit reads 0xEE / UU and
# still "passes" a rule that only checks a sample.
#
# NOT and BUFFER are NOT in this table because their input offset is FIXED, not derived from `size`.
# Measured: NOT Gate has its IN at loc+(-30,0) at the default size AND at size=30 - the same -30. A
# triangular gate does not widen the way a box does.
#
# This disagreement is worth recording. The three old generators carried two tables for this and they
# CONTRADICTED each other: `circmodel` computed -(size+0) = -50 for NOT, while `seqmodel` used a
# constant -30. Measurement says -30, so `circmodel`'s rule was wrong for every NOT gate it built.
GATE_INPUT_EXTRA = {
    "AND Gate": 0, "OR Gate": 0, "NAND Gate": 10, "NOR Gate": 10, "XOR Gate": 10,
    "XNOR Gate": 20,
}
ONE_INPUT_GATES = {"NOT", "BUFFER"}
GATE_KINDS = ("AND", "OR", "NAND", "NOR", "XOR", "XNOR", "NOT", "BUFFER")

# Tunnel facing, MEASURED, and named backwards relative to what they draw:
#   facing="west" -> glyph extends RIGHT of loc,  box (loc.x,      loc.y-11, 8*len(label)+10, 22)
#   facing="east" -> glyph extends LEFT  of loc,  box (loc.x - w,  loc.y-11, 8*len(label)+10, 22)
#   unset         -> same as "west"
# Because the names are inverted, the only rule worth remembering is "point the glyph away from the
# neighbour". `tunnel_facing` turns that question into the answer so callers never pick a constant.
TUNNEL_CHAR_W = 8
TUNNEL_PAD = 10
TUNNEL_H = 22

def tunnel_facing(neighbour_on_the_right: bool) -> str:
    """The `facing` that draws a Tunnel glyph AWAY from its neighbour.

    A consumer Tunnel asks about the component it sits on: a gate is to its RIGHT, an output Pin
    (whose own body extends LEFT) is to its LEFT. A driver Tunnel always has its source on the LEFT.
    """
    return "east" if neighbour_on_the_right else "west"

def tunnel_box(loc: Point, label: str, facing: Optional[str] = None) -> Tuple[int, int, int, int]:
    """Absolute drawing box of a Tunnel glyph. Width DEPENDS ON THE LABEL: 8*len(label)+10."""
    w = TUNNEL_CHAR_W * len(label) + TUNNEL_PAD
    if (facing or "west") == "east":
        return (loc[0] - w, loc[1] - TUNNEL_H // 2, w, TUNNEL_H)
    return (loc[0], loc[1] - TUNNEL_H // 2, w, TUNNEL_H)

def gate_inputs(gate: str, explicit: Optional[int]) -> int:
    """Input count for a gate. Raises for an unmeasured gate kind rather than inventing one."""
    if gate in ONE_INPUT_GATES:
        return 1
    if gate not in ("AND", "OR", "NAND", "NOR", "XOR", "XNOR"):
        raise GeometryUnknown(f"gate kind {gate!r} has not been measured")
    return max(2, explicit or 2)

def gate_input_ports(loc: Point, gate: str, n_inputs: int, size: int = DEFAULT_SIZE) -> List[Point]:
    """Absolute input-port coordinates for a gate. Refuses an unmeasured kind.

    NOT and BUFFER take a FIXED offset (measured: NOT is -30 at every size), so `size` does not enter.
    Every other gate's input column is -(size + per-kind extra).
    """
    name = gate if gate.endswith(" Gate") else f"{gate} Gate"
    if gate == "NOT":
        return [(loc[0] + NOT_INPUT_DX, loc[1])]
    if gate == "BUFFER":
        return [(loc[0] + BUFFER_INPUT_DX, loc[1])]
    if name not in GATE_INPUT_EXTRA:
        raise GeometryUnknown(
            f"{name!r} has no measured input-port offset; measure it with SamplePorts before "
            f"using it, do not guess")
    dx = -(size + GATE_INPUT_EXTRA[name])
    x = loc[0] + dx
    if n_inputs == 1:
        return [(x, loc[1])]
    if n_inputs == 2:
        return [(x, loc[1] - 20), (x, loc[1] + 20)]
    span = (n_inputs - 1) * 20
    return [(x, loc[1] - span // 2 + i * 20) for i in range(n_inputs)]

def mux_ports(loc: Point, k: int, w: int) -> dict:
    """Measured for select = 1..8; raises outside that range."""
    if not 1 <= k <= 8:
        raise GeometryUnknown(f"Multiplexer select={k} has not been measured; range is 1..8")
    n = 1 << k
    if k == 1:
        dx, base, step, sel_y = -30, -10, 20, 20
    else:
        base = -10 * (1 << (k - 1))
        dx, step, sel_y = -40, 10, -base
    h = 20 * (1 + (1 << (k - 1)))
    return {
        "data": [(loc[0] + dx, loc[1] + base + step * i) for i in range(n)],
        "select": (loc[0] - 20, loc[1] + sel_y),
        "out": loc,
        "n": n,
        "box_wh": (40, h),
    }

def decoder_ports(loc: Point, k: int) -> dict:
    """Measured for select = 1 and 3: outputs 20 px apart at x+20, the LAST at y-10."""
    if k not in (1, 3):
        raise GeometryUnknown(f"Decoder select={k} has not been measured; range is 1, 3")
    n = 1 << k
    return {
        "out": [(loc[0] + 20, loc[1] - 10 - 20 * (n - 1 - i)) for i in range(n)],
        "select": loc,
        "enable": (loc[0] - 10, loc[1]),
        "n": n,
    }

def splitter_ports(loc: Point, fanout: int, incoming: int, spacing: int) -> dict:
    """fanout i is 1-based and sits at (loc.x+20, loc.y-10 - spacing*10*(fanout-i))."""
    ports = {"bus": loc}
    for i in range(1, fanout + 1):
        ports[f"f{i}"] = (loc[0] + 20, loc[1] - 10 - spacing * 10 * (fanout - i))
    return ports

# --------------------------------------------------------------------------------------------------
# Kinds
# --------------------------------------------------------------------------------------------------

COMBINATIONAL_KINDS = ("GATE", "MUX", "DECODER", "ADDER", "SUBTRACTOR", "MULTIPLIER",
                       "COMPARATOR", "SPLITTER", "CONST", "PIN", "TUNNEL", "PROBE")
SEQUENTIAL_KINDS = ("DFF", "TFF", "JKFF", "SRFF", "REG", "RAM", "CLOCK")

KIND_XML_NAME = {
    "MUX": "Multiplexer", "DECODER": "Decoder", "ADDER": "Adder", "SUBTRACTOR": "Subtractor",
    "MULTIPLIER": "Multiplier", "COMPARATOR": "Comparator", "SPLITTER": "Splitter",
    "CONST": "Constant", "PIN": "Pin", "TUNNEL": "Tunnel", "PROBE": "Probe",
    "REG": "Register", "RAM": "RAM", "CLOCK": "Clock",
    "DFF": "D Flip-Flop", "TFF": "T Flip-Flop", "JKFF": "J-K Flip-Flop", "SRFF": "S-R Flip-Flop",
}

KIND_LIB = {
    "GATE": GATES_LIB, "MUX": PLEXERS_LIB, "DECODER": PLEXERS_LIB,
    "ADDER": ARITH_LIB, "SUBTRACTOR": ARITH_LIB, "MULTIPLIER": ARITH_LIB, "COMPARATOR": ARITH_LIB,
    "SPLITTER": WIRING_LIB, "CONST": WIRING_LIB, "PIN": WIRING_LIB, "TUNNEL": WIRING_LIB,
    "PROBE": WIRING_LIB, "CLOCK": WIRING_LIB,
    "REG": MEMORY_LIB, "RAM": MEMORY_LIB,
    "DFF": MEMORY_LIB, "TFF": MEMORY_LIB, "JKFF": MEMORY_LIB, "SRFF": MEMORY_LIB,
}

# Output port names, so a driver can be identified without re-deriving it per call site.
OUTPUT_PORTS = {
    "GATE": {"out"}, "MUX": {"out"}, "DECODER": {"out0", "out1", "out2", "out3", "out4", "out5",
                                                 "out6", "out7"},
    "ADDER": {"Sum", "Cout"}, "SUBTRACTOR": {"out", "Bout"}, "MULTIPLIER": {"out", "hi"},
    "COMPARATOR": {">", "=", "<"}, "SPLITTER": {"bus"},
    "REG": {"Q"}, "DFF": {"Q", "QB"}, "TFF": {"Q", "QB"}, "JKFF": {"Q", "QB"}, "SRFF": {"Q", "QB"},
    "RAM": {"Dout"}, "CONST": {"out"}, "CLOCK": {"out"}, "PIN": {"p"}, "PROBE": {"p"},
    "TUNNEL": {"p"},
}
INPUT_PORT_PREFIXES = ("in", "d", "f")

# --------------------------------------------------------------------------------------------------
# Part
# --------------------------------------------------------------------------------------------------

@dataclass
class Part:
    """One component. `kind` decides which fields matter."""

    kind: str
    ref: str
    loc: Point = (0, 0)

    width: int = 1                  # data width for anything with a bus
    gate: str = "AND"               # GATE: AND/OR/NAND/NOR/XOR/XNOR/NOT/BUFFER
    inputs: int = 2                 # GATE: number of input pins
    select: int = 1                 # MUX/DECODER: select bits
    fanout: int = 2                 # SPLITTER
    spacing: int = 1                # SPLITTER
    addr_width: int = 0             # RAM: address bits (`width` is the data width)
    value: int = 0                  # CONST
    label: str = ""                 # PIN / TUNNEL
    facing: Optional[str] = None
    pin_type: str = "output"        # PIN: the default is `input`, so an output pin MUST say so
    trigger: str = "rising"         # REG / flip-flops
    size: int = DEFAULT_SIZE        # GATE
    mode: str = "unsigned"          # COMPARATOR: the default is SIGNED and is written explicitly
    xml: str = ""                   # CATALOG: the component's XML name in the catalogue
    cat_attrs: Dict[str, object] = field(default_factory=dict)   # CATALOG: attribute settings

    # ---- catalogue-backed parts -------------------------------------------------------------
    #
    # `ir` hand-codes 17 part kinds. The catalogue holds measured ports and bounds for all 74
    # components of the six libraries, so a kind of "CATALOG" reaches the rest WITHOUT a second hand-
    # written table - which is the duplication that produced two contradicting NOT-gate offsets here.
    # Geometry comes from `portdb`, which reads the jar's own measurements.
    def _cat(self):
        import portdb
        return portdb

    @property
    def is_catalogue(self) -> bool:
        return self.kind == "CATALOG"

    # ---- identity ------------------------------------------------------------------------------
    @property
    def xml_name(self) -> str:
        if self.kind == "CATALOG":
            if not self.xml:
                raise SpecError("a CATALOG part needs `xml`, the component's XML name")
            return self.xml
        if self.kind == "GATE":
            return f"{self.gate} Gate"
        if self.kind not in KIND_XML_NAME:
            raise SpecError(f"unknown part kind {self.kind!r}")
        return KIND_XML_NAME[self.kind]

    @property
    def lib(self) -> int:
        if self.kind == "CATALOG":
            c = self._cat().component(self.xml_name)
            name = c.get('library')
            if name not in LIB_ORDINALS:
                raise SpecError(f"{self.xml_name!r} reports library {name!r}, which has no ordinal")
            return LIB_ORDINALS[name]
        if self.kind not in KIND_LIB:
            raise SpecError(f"unknown part kind {self.kind!r}")
        return KIND_LIB[self.kind]

    @property
    def is_sequential(self) -> bool:
        return self.kind in SEQUENTIAL_KINDS

    @property
    def is_driver(self) -> bool:
        """True when this part can source a net at some port. CLOCK/CONST always can."""
        return self.kind in ("CLOCK", "CONST") or self.kind in SEQUENTIAL_KINDS \
            or self.kind in COMBINATIONAL_KINDS

    def drives(self, port: str) -> bool:
        """Can this part source a net at `port`?

        An INPUT Pin IS a driver: the spec drives the circuit from it. Only an OUTPUT Pin is a sink.
        Treating every Pin alike made the validator reject every combinational spec, because the net
        `a` -> `__in_a` had no recognised source.

        For a catalogue-backed part the direction comes from the MEASURED port type - the jar reports
        OUT/IN/INOUT per port - so no hand-written table is needed and none can contradict it.
        """
        if self.kind == "CATALOG":
            key = str(port) if port is not None else "0"
            if key.startswith("p") and key[1:].isdigit():
                key = key[1:]
            try:
                ps = self._cat().ports(self.xml_name, self.cat_attrs)
                return ps[int(key)].get('type', '').startswith('OUT')
            except Exception:                                        # noqa: BLE001
                return False
        if port is None:
            return self.kind in ("CLOCK", "CONST") or \
                (self.kind == "PIN" and self.pin_type == "input")
        return port in OUTPUT_PORTS.get(self.kind, set())

    # ---- geometry ------------------------------------------------------------------------------
    def port(self, name: str) -> Point:
        """Absolute coordinate of a named port. Raises for an unknown name rather than guessing."""
        k, loc = self.kind, self.loc
        if k == "CATALOG":
            # ports are addressed by INDEX, because that is what the measurement gives: the jar reports
            # coordinates and types, not names. Accepts "p3" or 3.
            key = str(name)
            if key.startswith("p") and key[1:].isdigit():
                key = key[1:]
            if not key.isdigit():
                raise SpecError(
                    f"CATALOG part {self.xml!r}: ports are addressed by index (`p0`, `p1`, ... or 0, 1, "
                    f"...); {name!r} is not an index. Names live in the Java source and are recorded in "
                    f"the catalogue's `usage` where they have been read.")
            dx, dy = self._cat().port(self.xml_name, self.cat_attrs, int(key))
            return (loc[0] + dx, loc[1] + dy)
        if k == "GATE":
            n = gate_inputs(self.gate, self.inputs)
            if name == "out":
                return loc
            if name == "in":                      # the sequential format spelled it "in"
                name = "in0"
            if name.startswith("in"):
                idx = int(name[2:])
                ps = gate_input_ports(loc, self.gate, n, self.size)
                if idx >= len(ps):
                    raise SpecError(f"gate {self.ref!r} has no input {idx}")
                return ps[idx]
            raise SpecError(f"GATE has no port {name!r}")
        if k == "MUX":
            t = mux_ports(loc, self.select, self.width)
            if name == "out":
                return t["out"]
            if name == "select":
                return t["select"]
            if name.startswith("d"):
                return t["data"][int(name[1:])]
            raise SpecError(f"MUX has no port {name!r}")
        if k == "DECODER":
            t = decoder_ports(loc, self.select)
            if name == "select":
                return t["select"]
            if name == "enable":
                return t["enable"]
            if name.startswith("out"):
                return t["out"][int(name[3:])]
            raise SpecError(f"DECODER has no port {name!r}")
        if k == "SPLITTER":
            t = splitter_ports(loc, self.fanout, self.width, self.spacing)
            if name not in t:
                raise SpecError(f"SPLITTER has no port {name!r}")
            return t[name]
        if k in ("ADDER", "SUBTRACTOR", "MULTIPLIER", "COMPARATOR"):
            table = {"ADDER": ADDER_PORTS, "SUBTRACTOR": SUB_PORTS,
                     "MULTIPLIER": MUL_PORTS, "COMPARATOR": COMP_PORTS}[k]
            if name not in table:
                raise SpecError(f"{k} has no port {name!r}")
            dx, dy = table[name]
            return (loc[0] + dx, loc[1] + dy)
        if k == "REG":
            if name not in REG_PORTS:
                raise SpecError(f"Register has no port {name!r}")
            dx, dy = REG_PORTS[name]
            return (loc[0] + dx, loc[1] + dy)
        if k in ("DFF", "TFF", "JKFF", "SRFF"):
            if name not in FF_PORTS:
                raise SpecError(f"{k} has no port {name!r}")
            dx, dy = FF_PORTS[name]
            return (loc[0] + dx, loc[1] + dy)
        if k == "RAM":
            if name not in RAM_PORTS:
                raise SpecError(f"RAM has no port {name!r}")
            dx, dy = RAM_PORTS[name]
            return (loc[0] + dx, loc[1] + dy)
        if k == "TUNNEL":
            return loc
        if k in ("CONST", "CLOCK", "PIN", "PROBE"):
            if name in ("loc", "out", "p", "in"):
                return loc
            raise SpecError(f"{k} has no port {name!r}")
        raise SpecError(f"{k} has no named port {name!r}")

    def port_names(self) -> List[str]:
        """Every port name this part exposes, for routing and auditing."""
        k = self.kind
        if k == "CATALOG":
            n = len(self._cat().ports(self.xml_name, self.cat_attrs))
            return [f"p{i}" for i in range(n)]
        if k == "GATE":
            n = gate_inputs(self.gate, self.inputs)
            return ["out"] + [f"in{i}" for i in range(n)]
        if k == "MUX":
            return ["out", "select"] + [f"d{i}" for i in range(1 << self.select)]
        if k == "DECODER":
            return ["select", "enable"] + [f"out{i}" for i in range(1 << self.select)]
        if k == "SPLITTER":
            return ["bus"] + [f"f{i}" for i in range(1, self.fanout + 1)]
        if k in ("ADDER", "SUBTRACTOR", "MULTIPLIER", "COMPARATOR"):
            return list({"ADDER": ADDER_PORTS, "SUBTRACTOR": SUB_PORTS,
                         "MULTIPLIER": MUL_PORTS, "COMPARATOR": COMP_PORTS}[k])
        if k == "REG":
            return list(REG_PORTS)
        if k in ("DFF", "TFF", "JKFF", "SRFF"):
            return list(FF_PORTS)
        if k == "RAM":
            return list(RAM_PORTS)
        if k == "TUNNEL":
            return ["p"]
        return ["p"]

    def port_width(self, name: str) -> int:
        """Declared width of a port. Getting this wrong silently truncates a bus."""
        k = self.kind
        if k == "CATALOG":
            key = str(name)
            if key.startswith("p") and key[1:].isdigit():
                key = key[1:]
            return self._cat().port_width(self.xml_name, self.cat_attrs, int(key))
        if k == "RAM":
            if name == "A":
                return self.addr_width
            if name in ("Din", "Dout"):
                return self.width
            return 1
        if k in ("ADDER", "SUBTRACTOR"):
            return 1 if name in ("Cin", "Cout", "Bin", "Bout") else self.width
        if k == "MULTIPLIER":
            return 1 if name in ("upper", "hi") else self.width
        if k == "MUX":
            return self.select if name == "select" else (1 if name in ("select",) else self.width)
        if k == "DECODER":
            return self.select if name == "select" else 1
        if k == "REG":
            return REG_PORT_WIDTH_RULE.get(name, self.width) if \
                REG_PORT_WIDTH_RULE.get(name) != "data" else self.width
        if k == "GATE":
            return 1 if name.startswith("in") else self.width
        if k in ("DFF", "TFF", "JKFF", "SRFF"):
            return 1 if name in ("CK", "PRESET", "RESET", "Q", "QB") and self.width == 1 else self.width
        if k in ("PIN", "TUNNEL", "CONST"):
            return self.width
        return 1

    def box(self, pad: int = 0) -> Tuple[int, int, int, int]:
        """Conservative drawing box from the extreme ports. Used by the router and the visual audit."""
        if self.kind == "TUNNEL":
            return tunnel_box(self.loc, self.label, self.facing)
        if self.kind == "CATALOG":
            dx, dy, w, h = self._cat().box(self.xml_name, self.cat_attrs)
            return (self.loc[0] + dx - pad, self.loc[1] + dy - pad, w + 2 * pad, h + 2 * pad)
        pts = []
        for n in self.port_names():
            try:
                pts.append(self.port(n))
            except (SpecError, GeometryUnknown):
                continue
        if not pts:
            pts = [self.loc]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # a component body is at least 20x20 and extends 20 px past its outermost port
        x0, x1 = min(xs) - 10 - pad, max(xs) + 10 + pad
        y0, y1 = min(ys) - 10 - pad, max(ys) + 10 + pad
        return (x0, y0, x1 - x0, y1 - y0)

# --------------------------------------------------------------------------------------------------
# Net / Circuit
# --------------------------------------------------------------------------------------------------

@dataclass
class Endpoint:
    """One end of a net: a part reference and a port name (None means the part's own loc)."""

    ref: str
    port: Optional[str] = None

@dataclass
class Net:
    name: str
    driver: Endpoint
    sinks: List[Endpoint] = field(default_factory=list)
    width: int = 1

@dataclass
class Circuit:
    name: str = "main"
    parts: List[Part] = field(default_factory=list)
    nets: Dict[str, Net] = field(default_factory=dict)
    # Each wire carries the NAME of the net it belongs to. Inferring that later from the endpoints is
    # wrong: the router emits multi-segment paths whose intermediate corners are not in any net's
    # endpoint set, so an ownership check mis-attributed them and reported collinear overlaps between
    # wires that were in fact the same net.
    wires: List[Tuple[Point, Point, str]] = field(default_factory=list)
    # explicit routes, when a spec forces a layout by hand
    waypoints: Dict[str, List[Point]] = field(default_factory=dict)

    def by_ref(self, ref: str) -> Part:
        for p in self.parts:
            if p.ref == ref:
                return p
        raise SpecError(f"unknown part {ref!r}")

    def coord(self, ep: Endpoint) -> Point:
        p = self.by_ref(ep.ref)
        return p.loc if ep.port is None else p.port(ep.port)

    def libraries_used(self) -> List[int]:
        return sorted({p.lib for p in self.parts})

# --------------------------------------------------------------------------------------------------
# stdout
# --------------------------------------------------------------------------------------------------

def ensure_utf8_stdout() -> None:
    """Make stdout UTF-8. MUST only be called from a __main__ guard.

    Calling this at import time wraps sys.stdout for every importer too, and a second wrapper around
    an already-closed stream raises `ValueError: I/O operation on closed file`. That happened four
    times in this project (rtllib, router, project_header, geom) before the rule was written down.
    """
    if sys.stdout is not None and getattr(sys.stdout, "encoding", "").lower() not in ("utf-8", "utf8"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                      line_buffering=True)
