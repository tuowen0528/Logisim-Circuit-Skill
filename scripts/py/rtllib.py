#!/usr/bin/env python3
"""RTL-level spec-driven generator: multiplexers, decoders, adders, subtractors, multipliers,
comparators, splitters + gates.

`circmodel.py` covers only the eight gate kinds, which is enough for a full adder but not for a
realistic block. This module adds the RTL components needed for ALU-scale designs, keeping the same
tunnel-based net model so the merge/oscillation failure mode cannot occur.

DISCIPLINE: every port offset below was MEASURED with `SamplePorts` against the real loader, not
derived from source arithmetic. Offsets are relative to the component's `loc`.

MEASURED PORT TABLES (Logisim Evolution 5.1.0dev)

  Multiplexer, select=k, width=w       bounds w=40 h=20*(2^k) ... inputs on the left
      data inputs    (loc.x-40, loc.y-20+10*i)   i = 0..2^k-1, width w
      select         (loc.x-20, loc.y+20)        width k
      output         loc                         width w
      (measured for k=2,w=4: ends at (260,280)(260,290)(260,300)(260,310)(280,320)(300,300))

  Decoder, select=k
      outputs i      (loc.x+20, loc.y-40+10*i)   i = 0..2^k-1, width 1
      select         loc                         width k
      enable         (loc.x-10, loc.y)           width 1
      (measured for k=2: (720,260..290) then (700,300) then (690,300))

  Adder, width=w
      A              (loc.x-40, loc.y-10)        width w
      B              (loc.x-40, loc.y+10)        width w
      Cin            (loc.x-20, loc.y-20)        width 1
      Sum            loc                         width w
      Cout           (loc.x-20, loc.y+20)        width 1

  Subtractor, width=w
      A              (loc.x-40, loc.y-10)        width w
      B              (loc.x-40, loc.y+10)        width w
      Bin            (loc.x-20, loc.y-20)        width 1
      out            loc                         width w
      Bout           (loc.x-20, loc.y+20)        width 1

  Multiplier, width=w
      A              (loc.x-40, loc.y-10)        width w
      B              (loc.x-40, loc.y+10)        width w
      out            loc                         width w   <- LOW half of the product
      upper          (loc.x-20, loc.y-20)        width w   <- high-word input; tie to 0 for w x w
      hi             (loc.x-20, loc.y+20)        width w   <- HIGH half of the product
      (MEASURED, and confirmed by an exhaustive 65536-row simulation of an 8x8 multiply that
       matched a*b on EVERY row)

  Comparator, width=w
      A              (loc.x-40, loc.y-10)        width w
      B              (loc.x-40, loc.y+10)        width w
      >              (loc.x, loc.y-10)           width 1
      =              (loc.x, loc.y)              width 1
      <              (loc.x, loc.y+10)           width 1

  Splitter, fanout=n, incoming=w, spacing=s
      bus            loc                         width w, INPUT_OUTPUT
      fanout i (1-based)  (loc.x+20, loc.y-10 - s*10*(n-i))  width = bit count
      (measured for n=2,s=2,w=4: bus (1920,300) w4; fanout1 (1920,270) w1; fanout2 (1920,290) w3)

  Gate kinds and Pin/Tunnel/Constant: see circmodel.py.

Usage:
    py rtllib.py build <spec.json> <out.circ> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import circmodel as cm
import project_header as _ph  # noqa: E402  (GATE_NAMES, input_ports, output_port)

# circmodel already made stdout UTF-8 safe; wrapping again here would close the shared buffer.
cm.ensure_utf8_stdout()

WIRING_LIB, GATES_LIB, PLEXERS_LIB, ARITH_LIB = 0, 1, 2, 3


def mux_ports(loc, k, w):
    """MEASURED port geometry for select = 1..8.

    Neither the x offset nor the data-input spacing is uniform across configurations, so this is a
    table plus a rule that was CHECKED against all eight measured cases (see
    `scripts/analysis/extend_mux_ports.py`, which refuses to patch if any case disagrees):

        select  data_x  first_dy  step   select_y   box height
          1      x-30     -10      20       +20         40
          2      x-40     -20      10       +20         60
          3      x-40     -40      10       +40        100
          4      x-40     -80      10       +80        180
          5      x-40    -160      10      +160        340
          6      x-40    -320      10      +320        660
          7      x-40    -640      10      +640       1300
          8      x-40   -1280      10     +1280       2580

    i.e. for k >= 2 the data block begins at -10*2^(k-1) and steps 10, and the select pin is at
    +10*2^(k-1) (the negatives of each other). select=1 is the odd case and is a literal.
    Select is at (x-20, y+select_y) and the output at loc, for every configuration measured.
    """
    n = 1 << k
    if k == 1:
        dx, base, step, sel_y = -30, -10, 20, 20
    elif 2 <= k <= 8:
        base = -10 * (1 << (k - 1))
        dx, step, sel_y = -40, 10, -base
    else:
        raise SpecError(
            f"Multiplexer select={k} has not been measured; the measured range is 1..8")
    # box height is 20*(1 + 2^(k-1)): measured 40/60/100/180/340/660/1300/2580 for k = 1..8
    h = 20 * (1 + (1 << (k - 1)))
    data = [(loc[0] + dx, loc[1] + base + step * i) for i in range(n)]
    return {
        "data": data,
        "select": (loc[0] - 20, loc[1] + sel_y),
        "out": loc,
        "n": n,
        "box_wh": (40, h),
        "widths": {"select": k, "data": w, "out": w},
    }


def decoder_ports(loc, k):
    """Measured for select = 1 and 3: outputs 20 px apart, the LAST one at loc.y-10."""
    n = 1 << k
    return {
        "out": [(loc[0] + 20, loc[1] - 10 - 20 * (n - 1 - i)) for i in range(n)],
        "select": loc,
        "enable": (loc[0] - 10, loc[1]),
        "n": n,
        "widths": {"select": k, "out": 1, "enable": 1},
    }


ADDER_PORTS = {
    "A": (-40, -10), "B": (-40, 10), "Cin": (-20, -20),
    "Sum": (0, 0), "Cout": (-20, 20),
}
SUB_PORTS = {
    "A": (-40, -10), "B": (-40, 10), "Bin": (-20, -20),
    "out": (0, 0), "Bout": (-20, 20),
}
MUL_PORTS = {
    "A": (-40, -10), "B": (-40, 10), "upper": (-20, -20),
    "out": (0, 0), "hi": (-20, 20),
}
COMP_PORTS = {"A": (-40, -10), "B": (-40, 10), ">": (0, -10), "=": (0, 0), "<": (0, 10)}

# Tunnel facing semantics - MEASURED, and the OPPOSITE of what the names suggest.
#
#   facing="west"  -> the glyph is drawn to the RIGHT (east) of loc : box (loc.x,    loc.y-11, ~24, 22)
#   facing="east"  -> the glyph is drawn to the LEFT  (west) of loc : box (loc.x-33, loc.y-11, ~33, 22)
#   facing unset   -> same as "west"
#
# i.e. `facing` names the side the WIRE attaches to, and the body is drawn on the opposite side.
#
# A sink Tunnel sits exactly on a component's input port, so that component is on its RIGHT: the
# body must extend LEFT, which is facing="east". It used to be "west", which drew the glyph straight
# into the component body - the overlap reported against the generated 4-bit adder.
# A driver Tunnel has its source on the LEFT, so the body extends RIGHT: facing="west".
TUNNEL_SINK_FACING = "east"
TUNNEL_DRIVER_FACING = "west"


def splitter_ports(loc, fanout, incoming, spacing, bits=None):
    """fanout i is 1-based and sits at (loc.x+20, loc.y-10 - spacing*10*(fanout-i))."""
    ports = {"bus": loc}
    widths = {"bus": incoming}
    for i in range(1, fanout + 1):
        ports[f"f{i}"] = (loc[0] + 20, loc[1] - 10 - spacing * 10 * (fanout - i))
    return {"ports": ports, "widths": widths}


class SpecError(Exception):
    pass


@dataclass
class Part:
    kind: str                 # MUX | DECODER | ADDER | SUBTRACTOR | MULTIPLIER | COMPARATOR |
                              # SPLITTER | GATE | CONST | PIN | TUNNEL | PROBE
    ref: str
    loc: Tuple[int, int] = (0, 0)
    attrs: Dict[str, object] = field(default_factory=dict)
    width: int = 1
    value: int = 0
    label: str = ""
    gate: str = ""            # for GATE: AND/OR/XOR/...

    @property
    def lib(self) -> int:
        return {"MUX": PLEXERS_LIB, "DECODER": PLEXERS_LIB, "ADDER": ARITH_LIB,
                "COMPARATOR": ARITH_LIB, "SUBTRACTOR": ARITH_LIB, "MULTIPLIER": ARITH_LIB,
                "SPLITTER": WIRING_LIB, "GATE": GATES_LIB,
                "CONST": WIRING_LIB, "PIN": WIRING_LIB, "TUNNEL": WIRING_LIB,
                "PROBE": WIRING_LIB}[self.kind]

    @property
    def xml_name(self) -> str:
        if self.kind == "GATE":
            if self.gate not in cm.GATE_NAMES:
                raise SpecError(f"unknown gate kind {self.gate!r}; known {sorted(cm.GATE_NAMES)}")
            return cm.GATE_NAMES[self.gate]
        return {"MUX": "Multiplexer", "DECODER": "Decoder", "ADDER": "Adder",
                "COMPARATOR": "Comparator", "SUBTRACTOR": "Subtractor",
                "MULTIPLIER": "Multiplier", "SPLITTER": "Splitter", "CONST": "Constant",
                "PIN": "Pin", "TUNNEL": "Tunnel", "PROBE": "Probe"}[self.kind]

    # -- ports -------------------------------------------------------------------------------
    def port(self, name: str) -> Tuple[int, int]:
        k = self.attrs
        if self.kind == "MUX":
            p = mux_ports(self.loc, int(k["select"]), int(k["width"]))
            if name == "out":
                return p["out"]
            if name == "select":
                return p["select"]
            if name.startswith("d"):
                return p["data"][int(name[1:])]
        elif self.kind == "DECODER":
            p = decoder_ports(self.loc, int(k["select"]))
            if name == "select":
                return p["select"]
            if name == "enable":
                return p["enable"]
            if name.startswith("y"):
                return p["out"][int(name[1:])]
        elif self.kind == "ADDER":
            if name in ADDER_PORTS:
                dx, dy = ADDER_PORTS[name]
                return self.loc[0] + dx, self.loc[1] + dy
        elif self.kind == "SUBTRACTOR":
            if name in SUB_PORTS:
                dx, dy = SUB_PORTS[name]
                return self.loc[0] + dx, self.loc[1] + dy
        elif self.kind == "MULTIPLIER":
            if name in MUL_PORTS:
                dx, dy = MUL_PORTS[name]
                return self.loc[0] + dx, self.loc[1] + dy
        elif self.kind == "COMPARATOR":
            if name in COMP_PORTS:
                dx, dy = COMP_PORTS[name]
                return self.loc[0] + dx, self.loc[1] + dy
        elif self.kind == "SPLITTER":
            p = splitter_ports(self.loc, int(k["fanout"]), int(k["incoming"]),
                               int(k.get("spacing", 1)))
            if name in p["ports"]:
                return p["ports"][name]
        elif self.kind == "GATE":
            if name == "out":
                return cm.output_port(self.xml_name, self.loc)
            if name.startswith("in"):
                ins = cm.input_ports(self.xml_name, self.loc, gate_inputs(self.gate, k),
                                     int(k.get("size", cm.DEFAULT_SIZE)))
                return ins[int(name[2:])]
        elif self.kind in ("CONST", "PIN", "TUNNEL", "PROBE"):
            return self.loc
        raise SpecError(f"{self.kind} {self.ref!r} has no port {name!r}")

    def port_width(self, name: str) -> int:
        k = self.attrs
        if self.kind == "MUX":
            if name == "select":
                return int(k["select"])
            return int(k["width"])
        if self.kind == "DECODER":
            return int(k["select"]) if name == "select" else 1
        if self.kind == "ADDER":
            return 1 if name in ("Cin", "Cout") else int(k.get("width", 1))
        if self.kind == "SUBTRACTOR":
            return 1 if name in ("Bin", "Bout") else int(k.get("width", 1))
        if self.kind == "MULTIPLIER":
            return int(k.get("width", 1))
        if self.kind == "COMPARATOR":
            return 1 if name in (">", "=", "<") else int(k.get("width", 1))
        if self.kind == "SPLITTER":
            if name == "bus":
                return int(k["incoming"])
            return int(self.attrs.get("_fw", {}).get(name, 1))
        if self.kind == "GATE":
            return int(k.get("width", 1))
        return int(self.width)


# ----------------------------------------------------------------------------------------------
# Builder
# ----------------------------------------------------------------------------------------------

OWNED_PORTS = {
    "MUX": {"out"}, "DECODER": {"y"}, "ADDER": {"Sum", "Cout"},
    "SUBTRACTOR": {"out", "Bout"}, "MULTIPLIER": {"out", "hi"},
    "COMPARATOR": {">", "=", "<"},
    "SPLITTER": {"f"}, "GATE": {"out"}, "CONST": {"-"}, "PIN": {"-"}, "PROBE": set(),
}

# How many inputs a gate has. MEASURED from the Gates library: `AND`/`OR`/`NAND`/`NOR`/`XOR`/`XNOR`
# carry an `inputs` attribute (2..64), while `NOT Gate` and `Buffer` have NO `inputs` attribute at
# all and are permanently 1-input (components.json: NOT has 7 attributes, none of them `inputs`).
# Asking a NOT gate to justify an `in1` port made validation reject a perfectly good spec, so the
# count is derived per gate kind rather than defaulting to 2.
_ONE_INPUT_GATES = {"NOT", "BUFFER"}


def gate_inputs(gate: str, attrs: dict) -> int:
    if "inputs" in attrs:
        return int(attrs["inputs"])
    return 1 if gate.upper() in _ONE_INPUT_GATES else 2


# Components whose ports are also checked pairwise for self-coincidence.
_RTL_KINDS = ("GATE", "MUX", "DECODER", "ADDER", "SUBTRACTOR", "MULTIPLIER", "COMPARATOR",
              "SPLITTER")


def _port_names(p: Part) -> List[str]:
    if p.kind == "GATE":
        return ["out"] + [f"in{i}" for i in range(gate_inputs(p.gate, p.attrs))]
    if p.kind == "MUX":
        return ["out", "select"] + [f"d{i}" for i in range(1 << int(p.attrs["select"]))]
    if p.kind == "DECODER":
        return ["select", "enable"] + [f"y{i}" for i in range(1 << int(p.attrs["select"]))]
    if p.kind == "ADDER":
        return list(ADDER_PORTS)
    if p.kind == "SUBTRACTOR":
        return list(SUB_PORTS)
    if p.kind == "MULTIPLIER":
        return list(MUL_PORTS)
    if p.kind == "COMPARATOR":
        return list(COMP_PORTS)
    if p.kind == "SPLITTER":
        return ["bus"] + [f"f{i}" for i in range(1, int(p.attrs["fanout"]) + 1)]
    return []


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


class RtlBuilder:
    """Build a circuit from a spec with gates + RTL components, nets as named tunnels.

    Spec:
      {
        "name": "main",
        "inputs":  [["A", 4], ["B", 4], ["Op", 2]],
        "outputs": [["Y", 4]],
        "parts": [
          {"ref":"add","kind":"ADDER","loc":[400,300],"width":4},
          {"ref":"m","kind":"MUX","loc":[900,300],"select":2,"width":4},
          {"ref":"g0","kind":"GATE","gate":"AND","loc":[1300,300],"inputs":2,"width":4}
        ],
        "nets": {
          "A":   {"drive":"in:A",   "use":[["add","A"],["g0","in0"]]},
          "S":   {"drive":"add:Sum","use":[["m","d0"]]},
          "Y":   {"drive":"m:out",  "use":["out:Y"]}
        }
      }

    Net endpoints are written "<ref>:<port>", or "in:<pin>" / "out:<pin>" for pins.
    Nets are realised as TUNNELS: one driver tunnel just off the driving port, and one consumer
    tunnel placed exactly ON each sink port, so no coordinate routing is needed.
    """

    def __init__(self, spec: dict):
        self.spec = spec
        self.parts: List[Part] = []
        self.by_ref: Dict[str, Part] = {}
        self.nets: Dict[str, dict] = {}
        self._build()

    def _build(self):
        spec = self.spec
        # pins are modelled as Parts so ports resolve uniformly
        for label, width in cm._io_pairs(spec.get("inputs", [])):
            p = Part("PIN", f"__in_{label}", loc=(120, 200 + 40 * len(self.parts)),
                     width=width, label=label, attrs={"pin_type": "input"})
            self.parts.append(p)
            self.by_ref[p.ref] = p
        for label, width in cm._io_pairs(spec.get("outputs", [])):
            p = Part("PIN", f"__out_{label}", loc=(3000, 200 + 40 * len(self.parts)),
                     width=width, label=label, attrs={"pin_type": "output"})
            self.parts.append(p)
            self.by_ref[p.ref] = p

        for d in spec.get("parts", []):
            kind = d["kind"].upper()
            p = Part(kind=kind, ref=d["ref"], loc=tuple(d.get("loc", (0, 0))),
                     attrs={k: v for k, v in d.items() if k not in ("ref", "kind", "loc")},
                     width=int(d.get("width", 1)), value=int(d.get("value", 0)),
                     label=d.get("label", ""), gate=d.get("gate", "").upper())
            if p.ref in self.by_ref:
                raise SpecError(f"duplicate part ref {p.ref!r}")
            # splitter fanout widths must sum to `incoming`; record per-port widths
            if kind == "SPLITTER":
                self._splitter_widths(p)
            self.parts.append(p)
            self.by_ref[p.ref] = p

        for net, d in spec.get("nets", {}).items():
            self.nets[net] = {
                "drive": self._endpoint(d["drive"]),
                "use": [self._endpoint(u) for u in d.get("use", [])],
            }

        self._place_output_pins()

        # Tie off unconnected inputs explicitly. An unconnected input is UNDEFINED, not zero:
        # leaving `Adder.Cin` floating made the whole ALU emit `UUUU` with no diagnostic at all.
        # Spec: "tie": {"add:Cin": 0, "m:enable": 1}
        self._apply_ties(spec.get("tie", {}))

    def _place_output_pins(self):
        """Park each output pin at the SAME y as the port that drives it, on the far right.

        A Pin has a single port at its own `loc`, and a tunnel connects only where coordinates
        coincide. Two rules therefore both matter:

        * the pin's y MUST equal its driver port's y, or the pin is never driven and the output
          reads `UUUU` with no diagnostic (the bug this fixes: the pin sat at y=280 while its
          driver was at y=300);
        * the pin must NOT sit exactly on the driver tunnel's coordinate, because the serialiser
          also emits a tunnel at the pin, and two tunnels at one point is at best redundant and at
          worst a merge, so the pin goes to a distinct x.

        This mirrors the placement `circmodel.py` already uses (proven by the 512/512 adder).
        """
        right = 0
        for p in self.parts:
            if p.kind in _RTL_KINDS or p.kind == "PIN":
                right = max(right, p.loc[0])
        right += 6 * cm.COLUMN_PITCH

        claimed: set = set()
        for p in self.parts:
            if p.kind != "PIN" or p.attrs.get("pin_type") != "output":
                continue
            driver_net = next((n for n, d in self.nets.items()
                               if any(u[0] == p.ref for u in d["use"])), None)
            if driver_net is None:
                p.loc = (right, 500 + 40 * len(claimed))
                claimed.add(p.loc[1])
                continue
            dref, dport = self.nets[driver_net]["drive"]
            y = self.coord(dref, dport)[1]
            while y in claimed:                     # keep every pin on its own row
                y += 20
            claimed.add(y)
            p.loc = (right, y)

    def _apply_ties(self, ties: dict):
        for i, (target, value) in enumerate(sorted(ties.items())):
            ref, port = self._endpoint(target)
            part = self.by_ref[ref]
            width = part.port_width(port)
            label = f"__tie{i}"
            src = Part("CONST", label, loc=(20, 3000 + 40 * i), width=width, value=int(value))
            self.parts.append(src)
            self.by_ref[label] = src
            self.nets[label] = {"drive": (label, "-"), "use": [(ref, port)]}

    def _splitter_widths(self, p: Part):
        """Derive each fanout width from the `bitN` attributes, then store on the part."""
        incoming = int(p.attrs["incoming"])
        fanout = int(p.attrs["fanout"])
        groups: Dict[int, List[int]] = {i: [] for i in range(1, fanout + 1)}
        for n in range(incoming):
            v = p.attrs.get(f"bit{n}", 0)
            if isinstance(v, str) and v == "none":
                continue
            groups[int(v)].append(n)
        widths = {f"f{i}": len(groups[i]) for i in groups}
        if sum(widths.values()) != incoming:
            raise SpecError(
                f"splitter {p.ref!r}: fanout widths {widths} sum to {sum(widths.values())}, "
                f"expected incoming={incoming}"
            )
        p.attrs["_fw"] = widths

    def _endpoint(self, text):
        """Normalise an endpoint to (ref, port).

        Accepts both notations so RTL specs can reuse the combinational spec's habit:
          * "in:A" / "out:Y" / "ref:port"   (string form)
          * ["ref", "port"] / ["ref", null]  (sequence form, `null` meaning the part's own loc)
        """
        if isinstance(text, (list, tuple)):
            if len(text) != 2:
                raise SpecError(f"net endpoint {text!r} must be [ref, port]")
            head, port = str(text[0]), text[1]
        elif isinstance(text, str):
            if ":" not in text:
                raise SpecError(
                    f"net endpoint {text!r} must be 'ref:port', 'in:NAME', 'out:NAME', "
                    f"or [ref, port]")
            head, port = text.split(":", 1)
        else:
            raise SpecError(f"net endpoint {text!r} has unsupported type {type(text).__name__}")

        if head == "in":
            ref = f"__in_{port}"
        elif head == "out":
            ref = f"__out_{port}"
        else:
            ref = head
        if ref not in self.by_ref:
            raise SpecError(f"net endpoint {text!r} references unknown part {ref!r}")
        # A Pin has a single port at its own loc; normalise it to "-" so that a spec writing
        # "out:Y" and one writing ["__out_Y", null] produce the SAME key. Mismatched keys here
        # made the validator report "output pin is not driven" for a correctly driven circuit.
        if self.by_ref[ref].kind == "PIN":
            port = "-"
        elif port is None:
            port = "-"
        return (ref, port)

    # -- coordinates -------------------------------------------------------------------------
    def coord(self, ref: str, port: str) -> Tuple[int, int]:
        p = self.by_ref[ref]
        if p.kind == "PIN":
            return p.loc
        return p.port(port)

    def driver_coord(self, ref: str, port: str) -> Tuple[int, int]:
        c = self.coord(ref, port)
        return (c[0] + cm.TUNNEL_STUB, c[1])

    # -- validation --------------------------------------------------------------------------
    def _all_coords(self) -> List[Tuple[Tuple[int, int], str, str]]:
        """Every coordinate the serialiser will emit, tagged with the net that owns it."""
        out: List[Tuple[Tuple[int, int], str, str]] = []
        for net, d in self.nets.items():
            dref, dport = d["drive"]
            out.append((self.coord(dref, dport), net, "driver port"))
            out.append((self.driver_coord(dref, dport), net, "driver tunnel"))
            for (uref, uport) in d["use"]:
                out.append((self.coord(uref, uport), net, "consumer tunnel"))
        return out

    def _geometry_problems(self) -> List[str]:
        """Reject two different nets landing on one coordinate.

        Tunnels connect BY LABEL, so if two nets occupy the same point they merge - which shows up
        as contention (`E`) or as a circuit that simply never works, with no diagnostic.
        """
        problems: List[str] = []
        owners: Dict[Tuple[int, int], List[Tuple[str, str]]] = {}
        for coord, net, what in self._all_coords():
            owners.setdefault(coord, []).append((net, what))
        for coord, claims in owners.items():
            nets = sorted({c[0] for c in claims})
            if len(nets) > 1:
                kinds = ", ".join(f"{n}({k})" for n, k in claims)
                problems.append(f"coordinate {coord} is occupied by {len(nets)} nets: {kinds} "
                                f"- tunnels connect by label, so these would MERGE")
        # a part's two ports must not coincide (would short across the part)
        for p in self.parts:
            if p.kind not in _RTL_KINDS:
                continue
            seen: Dict[Tuple[int, int], str] = {}
            for n in _port_names(p):
                try:
                    c = p.port(n)
                except SpecError:
                    continue
                if c in seen:
                    problems.append(f"{p.ref}: ports {seen[c]!r} and {n!r} coincide at {c}")
                seen[c] = n
        return problems

    def validate(self) -> List[str]:
        problems: List[str] = []
        for net, d in self.nets.items():
            drv = d["drive"]
            p = self.by_ref[drv[0]]
            if p.kind == "PIN" and p.attrs.get("pin_type") != "input":
                problems.append(f"net {net!r} is driven by output pin {p.label!r}")
            if p.kind == "PROBE":
                problems.append(f"net {net!r} is driven by a Probe (passive)")
        # every RTL input endpoint must be driven exactly once
        sinks: Dict[Tuple[str, str], List[str]] = {}
        for net, d in self.nets.items():
            for (ref, port) in d["use"]:
                sinks.setdefault((ref, port), []).append(net)
        for (ref, port), nets in sinks.items():
            if len(nets) > 1:
                problems.append(f"{ref}:{port} is driven by {len(nets)} nets {nets} - contention")

        # required ports must be connected
        for p in self.parts:
            req: List[str] = []
            if p.kind == "MUX":
                req = [f"d{i}" for i in range(1 << int(p.attrs["select"]))] + ["select"]
            elif p.kind in ("ADDER", "SUBTRACTOR", "COMPARATOR"):
                req = ["A", "B"]
            elif p.kind == "MULTIPLIER":
                req = ["A", "B", "upper"]
            elif p.kind == "GATE":
                req = [f"in{i}" for i in range(gate_inputs(p.gate, p.attrs))]
            for r in req:
                if (p.ref, r) not in sinks:
                    problems.append(f"{p.ref}: {r} is unconnected")

        for label, _ in cm._io_pairs(self.spec.get("outputs", [])):
            # "out:Y" normalises to ("__out_Y", "-")
            if (f"__out_{label}", "-") not in sinks:
                problems.append(f"output pin {label!r} is not driven")

        problems.extend(self._geometry_problems())
        return problems

    # -- serialisation -----------------------------------------------------------------------
    def to_xml(self) -> str:
        L = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
             '<project source="5.1.0dev" version="1.0">',
             '  <lib desc="#Wiring" name="0"/>',
             '  <lib desc="#Gates" name="1"/>',
             '  <lib desc="#Plexers" name="2"/>',
             '  <lib desc="#Arithmetic" name="3"/>',
             f'  <main name="{self.spec.get("name", "main")}"/>',
             f'  <circuit name="{self.spec.get("name", "main")}">']

        for p in self.parts:
            if p.kind == "PIN":
                attrs = f'<a name="label" val="{p.label}"/>'
                attrs += f'<a name="type" val="{p.attrs.get("pin_type", "input")}"/>'
                if p.width > 1:
                    attrs += f'<a name="width" val="{p.width}"/>'
                L.append(f'    <comp lib="{p.lib}" loc="({p.loc[0]},{p.loc[1]})" '
                         f'name="Pin">{attrs}</comp>')
                continue
            a = ""
            if p.kind == "MUX":
                a = f'<a name="select" val="{p.attrs["select"]}"/>'
                if int(p.attrs.get("width", 1)) > 1:
                    a += f'<a name="width" val="{p.attrs["width"]}"/>'
            elif p.kind == "DECODER":
                a = f'<a name="select" val="{p.attrs["select"]}"/>'
            elif p.kind in ("ADDER", "SUBTRACTOR"):
                a = f'<a name="width" val="{p.attrs.get("width", 1)}"/>'
            elif p.kind == "MULTIPLIER":
                # `mode` defaults to unsigned, but it is stated explicitly so the intent lives in
                # the file rather than in a default.
                a = f'<a name="width" val="{p.attrs.get("width", 1)}"/>'
                a += f'<a name="mode" val="{p.attrs.get("mode", "unsigned")}"/>'
            elif p.kind == "COMPARATOR":
                # `mode` is MANDATORY. `Comparator.java:63-64` declares
                #   new Attribute[] {StdAttr.WIDTH, MODE_ATTR},
                #   new Object[] {BitWidth.create(8), SIGNED_OPTION}
                # so the DEFAULT IS SIGNED two's complement, and `propagate` swaps the two sign bits
                # whenever the mode is not `unsigned` (Comparator.java:133-140).
                #
                # Omitting it made a 2-bit compare read 10 as -2, so `A=2, B=0` reported LT instead
                # of GT. That is the fourth member of the silent-default family this project keeps
                # finding: Tunnel width (1 bit), Constant value (TRUE), Adder width (8 bits), and
                # now Comparator mode (signed).
                #
                # The default here is `unsigned`, because a spec that says "magnitude comparator"
                # means unsigned unless otherwise - and a silent signed default is exactly the trap.
                mode = p.attrs.get("mode", "unsigned")
                a = f'<a name="width" val="{p.attrs.get("width", 1)}"/>'
                a += f'<a name="mode" val="{mode}"/>'
            elif p.kind == "SPLITTER":
                a = f'<a name="fanout" val="{p.attrs["fanout"]}"/>'
                a += f'<a name="incoming" val="{p.attrs["incoming"]}"/>'
                a += f'<a name="spacing" val="{p.attrs.get("spacing", 1)}"/>'
                for n in range(int(p.attrs["incoming"])):
                    if f"bit{n}" in p.attrs:
                        a += f'<a name="bit{n}" val="{p.attrs[f"bit{n}"]}"/>'
            elif p.kind == "GATE":
                if int(p.attrs.get("inputs", 2)) != 2:
                    a += f'<a name="inputs" val="{p.attrs["inputs"]}"/>'
                if int(p.attrs.get("size", cm.DEFAULT_SIZE)) != cm.DEFAULT_SIZE:
                    a += f'<a name="size" val="{p.attrs["size"]}"/>'
                if int(p.attrs.get("width", 1)) > 1:
                    a += f'<a name="width" val="{p.attrs["width"]}"/>'
            elif p.kind == "CONST":
                # Constant's default is TRUE (`Constant.java`: `private Value value = Value.TRUE`),
                # so an element emitted WITHOUT a value attribute drives 1, not 0. That silently
                # turned a "tie Cin low" into "tie Cin high" and made an adder off by one.
                a = f'<a name="value" val="0x{p.value:x}"/>'
                if p.width > 1:
                    a += f'<a name="width" val="{p.width}"/>'
            L.append(f'    <comp lib="{p.lib}" loc="({p.loc[0]},{p.loc[1]})" '
                     f'name="{p.xml_name}">{a}</comp>')

        # nets as tunnels. The WIDTH must be written on every tunnel: a width-less Tunnel is
        # 1 bit, which silently truncates a multi-bit net (measured - a 2-bit AND wired through
        # width-less tunnels read `UU` on every row while the directly-wired version was correct).
        for net, d in sorted(self.nets.items()):
            dref, dport = d["drive"]
            src = self.coord(dref, dport)
            dt = self.driver_coord(dref, dport)
            nw = self.by_ref[dref].port_width(dport)
            L.append(f'    <wire from="({src[0]},{src[1]})" to="({dt[0]},{dt[1]})"/>')
            L.append(self._tunnel_xml(net, dt, nw, facing=TUNNEL_DRIVER_FACING))
            for (uref, uport) in d["use"]:
                sink = self.coord(uref, uport)
                # Where is the neighbour? A gate input port sits on a gate to the RIGHT of the
                # tunnel; an output Pin sits on a pin whose own body extends LEFT. `tunnel_facing`
                # turns that into the correct direction so the glyph never lands on the component.
                up = self.by_ref[uref]
                neighbour_right = not (up.kind == "PIN" and up.attrs.get("pin_type") == "output")
                L.append(self._tunnel_xml(net, sink, nw,
                                          facing=tunnel_facing(neighbour_right)))
        L += ['  </circuit>', '</project>']
        return "\n".join(L) + "\n"

    @staticmethod
    def _tunnel_xml(label: str, loc, width: int, facing: Optional[str] = None) -> str:
        w = f'<a name="width" val="{width}"/>' if width > 1 else ""
        f = f'<a name="facing" val="{facing}"/>' if facing else ""
        return (f'    <comp lib="0" loc="({loc[0]},{loc[1]})" name="Tunnel">'
                f'<a name="label" val="{label}"/>{w}{f}</comp>')

    def write(self, path) -> pathlib.Path:
        path = pathlib.Path(path)
        _ph.write_circ(path, self.to_xml())
        return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Build an RTL+gate circuit from a spec.")
    sub = ap.add_subparsers(dest="cmd")

    pb = sub.add_parser("build", help="write a .circ from an RTL spec")
    pb.add_argument("spec")
    pb.add_argument("out")
    pb.add_argument("--dry-run", action="store_true", help="validate only, write nothing")

    # Also accept a bare `spec out` form so the tool stays usable without a subcommand.
    ap.add_argument("spec_pos", nargs="?", help=argparse.SUPPRESS)
    ap.add_argument("out_pos", nargs="?", help=argparse.SUPPRESS)
    a = ap.parse_args()

    spec_path = getattr(a, "spec", None) or a.spec_pos
    out_path = getattr(a, "out", None) or a.out_pos
    if not spec_path or not out_path:
        ap.print_usage()
        print("error: need a spec and an out path (e.g. `rtllib.py build spec.json out.circ`)")
        return 2

    spec = json.loads(pathlib.Path(spec_path).read_text(encoding="utf-8"))
    try:
        b = RtlBuilder(spec)
    except SpecError as e:
        print(json.dumps({"status": "spec_error", "error": str(e)}, indent=2, ensure_ascii=False))
        return 3
    problems = b.validate()
    if problems:
        print("REFUSING to write: the spec has problems that would be SILENT or wrong:")
        for p in problems:
            print("  - " + p)
        return 3
    if getattr(a, "dry_run", False):
        print(f"OK: {len(b.parts)} parts, {len(b.nets)} nets (dry run)")
        return 0
    print("wrote", b.write(out_path), f"({len(b.parts)} parts, {len(b.nets)} nets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
