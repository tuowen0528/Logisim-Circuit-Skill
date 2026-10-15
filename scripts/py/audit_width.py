#!/usr/bin/env python3
"""Structural auditor for `.circ` files: find nets whose WIDTH is wrong.

WHY THIS EXISTS

Round 7 of this project found that a `Tunnel` **defaults to 1 bit**. A multi-bit net built from
width-less tunnels is silently truncated: the circuit loads, prints a full truth table, and every
value on that net is wrong, with **no diagnostic anywhere**. Nothing in the toolchain detected it,
because detection needs *structural* analysis of the netlist, not simulation.

This tool reconstructs the netlist from the file and reports:

  W1  TRUNCATION      a tunnel on an N-bit net declares a width < N  (the round-7 bug)
  W2  WIDTH_CONFLICT  two driving endpoints on one net disagree about width
  W3  PORT_MISMATCH   a consumer's port width differs from the net's width
  W4  FLOATING        a net with no driver
  W5  MULTIPLE_DRIVERS one net driven by more than one source (contention)

Net membership is reconstructed the way Logisim actually connects things:
  * a `Tunnel` joins every other Tunnel sharing its LABEL (that is the whole point of a tunnel)
  * a `wire` joins its two endpoints
  * a component port joins any wire endpoint / tunnel / port at the SAME coordinate

Nothing here executes the design or infers function; it is pure structural analysis, so it is fast
and safe to run on any file.

Usage:
    py audit_width.py <file.circ> [<file2.circ> ...] [--json]
Exit: 0 = clean, 1 = problems found, 2 = could not analyse (NOT a pass).
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

# Make stdout UTF-8 safe exactly once per process. Wrapping repeatedly closes the shared buffer
# (a previous wrapper gets collected and its finalizer closes it), which shows up as
# "I/O operation on closed file" in unrelated modules. ir owns the one-shot helper; fall
# back to a local guard so this tool also works standalone.
try:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import ir as _cm
    _cm.ensure_utf8_stdout()
except Exception:                                    # pragma: no cover - defensive
    if not getattr(sys.stdout, "_dsh_utf8", False):
        # Only wrap when RUN, never when imported: a second wrapper around the
        # same buffer closes the underlying stream when collected, and every
        # later print then raises "I/O operation on closed file".
        if __name__ == '__main__':
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                          errors="replace", line_buffering=True)
        sys.stdout._dsh_utf8 = True

# ----------------------------------------------------------------------------------------------
# Component port tables (measured; see  sections 4, 14, 16)
# ----------------------------------------------------------------------------------------------

GATE_NAMES = {"AND Gate", "OR Gate", "NAND Gate", "NOR Gate",
              "XOR Gate", "XNOR Gate", "NOT Gate", "Buffer", "Odd Parity", "Even Parity"}
# Gate input offsets live in cm.gate_input_ports (one measured table).

def gate_input_offsets(name: str, n: int, size: int):
    """Gate input offsets, delegated to ir's MEASURED table.

    This used to be a SECOND COPY of the rule `-(size + 10) if name in WIDE_GATES else -size`, which
    was wrong for NAND and NOR (measured -60, not -50). The auditor therefore looked for their inputs
    10 px from where they are and reported nets as undriven on a circuit that passes 1,048,576/1,048,
    576 real simulated combinations. One measured table, one owner.

    ir raises for a gate it has no measurement for, and that exception is allowed to propagate:
    a gate whose geometry is unknown must not be audited against a guess.
    """
    import ir as cm
    base = cm.gate_input_ports(name, (0, 0), n, size)
    return [(x, y) for (x, y) in base]

class Endpoint:
    __slots__ = ("kind", "ref", "port", "coord", "width")

    def __init__(self, kind, ref, port, coord, width):
        self.kind = kind        # "pin" | "gate" | "rtl" | "tunnel" | "wire"
        self.ref = ref
        self.port = port
        self.coord = coord
        self.width = width

def parse_loc(text: Optional[str]):
    if not text:
        return None
    try:
        x, y = text.strip().strip("()").split(",")
        return int(x), int(y)
    except (ValueError, AttributeError):
        return None

def analyse(path: pathlib.Path) -> dict:
    root = ET.fromstring(path.read_bytes())
    out = {"file": str(path), "circuits": [], "problems": []}

    for circ in root.findall("circuit"):
        cname = circ.get("name")
        endpoints: List[Endpoint] = []
        tunnels: List[dict] = []
        wires: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        opaque: List[dict] = []

        for comp in circ.findall("comp"):
            name = comp.get("name")
            loc = parse_loc(comp.get("loc"))
            attrs = {a.get("name"): a.get("val") for a in comp.findall("a")}
            if name == "Tunnel":
                tunnels.append({"label": attrs.get("label", ""), "loc": loc,
                                "width": int(attrs["width"]) if "width" in attrs else 1})
                continue
            if name == "Pin":
                w = int(attrs.get("width", 1))
                # Many real circuits leave pins UNLABELLED, so fall back to the coordinate rather
                # than printing "?" for every one of them.
                lbl = attrs.get("label") or f"Pin@{loc}"
                endpoints.append(Endpoint("pin", lbl, attrs.get("type", "input"), loc, w))
                continue
            if name in GATE_NAMES:
                w = int(attrs.get("width", 1))
                n = int(attrs.get("inputs", 1 if name in ("NOT Gate", "Buffer") else 2))
                size = int(attrs.get("size", 50))
                endpoints.append(Endpoint("gate", f"{name}@{loc}", "out", loc, w))
                # Port NAMING must match the generator, and match across BOTH generators:
                #   ir / rtllib emit gates and refer to inputs as `in<i>`;
                #   seqmodel emits a NOT gate and refers to it as `in` / `out`.
                # Naming them only `in<i>` here made the auditor miss the inverter entirely and
                # report a driven net as having NO driver. Accept both spellings by emitting the
                # alias `in` for single-input gates.
                for i, (dx, dy) in enumerate(gate_input_offsets(name, n, size)):
                    port = "in" if n == 1 else f"in{i}"
                    endpoints.append(Endpoint("gate", f"{name}@{loc}", port,
                                              (loc[0] + dx, loc[1] + dy), w))
                continue
            if name == "Constant":
                w = int(attrs.get("width", 1))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "out", loc, w))
                continue
            if name == "Multiplexer":
                w = int(attrs.get("width", 1))
                k = int(attrs.get("select", 1))
                n = 1 << k
                # MEASURED per configuration: neither x offset nor spacing is uniform
                # (select=1 -> x-30/step 20; select=2,3 -> x-40/step 10), so it is a table.
                mux_table = {1: (-30, -10, 20), 2: (-40, -20, 10), 3: (-40, -40, 10)}
                if k in mux_table:
                    dx, base, step = mux_table[k]
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", "out", loc, w))
                    for i in range(n):
                        endpoints.append(Endpoint("rtl", f"{name}@{loc}", f"d{i}",
                                                  (loc[0] + dx, loc[1] + base + step * i), w))
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", "select",
                                              (loc[0] - 20, loc[1] + 20), k))
                else:
                    opaque.append({"ref": f"{name}@{loc} (select={k} unmeasured)",
                                   "loc": loc, "width": None})
                continue
            if name == "Decoder":
                k = int(attrs.get("select", 1))
                n = 1 << k
                # MEASURED for select=1,3: outputs 20 px apart, the LAST one at loc.y-10.
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "select", loc, k))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "enable",
                                          (loc[0] - 10, loc[1]), 1))
                for i in range(n):
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", f"y{i}",
                                              (loc[0] + 20, loc[1] - 10 - 20 * (n - 1 - i)), 1))
                continue
            if name == "Adder":
                w = int(attrs.get("width", 1))
                for port, (dx, dy), pw in (("A", (-40, -10), w), ("B", (-40, 10), w),
                                           ("Cin", (-20, -20), 1), ("Sum", (0, 0), w),
                                           ("Cout", (-20, 20), 1)):
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", port,
                                              (loc[0] + dx, loc[1] + dy), pw))
                continue
            if name == "Comparator":
                w = int(attrs.get("width", 1))
                for port, (dx, dy), pw in (("A", (-40, -10), w), ("B", (-40, 10), w),
                                           (">", (0, -10), 1), ("=", (0, 0), 1), ("<", (0, 10), 1)):
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", port,
                                              (loc[0] + dx, loc[1] + dy), pw))
                continue
            if name == "Splitter":
                inc = int(attrs.get("incoming", 1))
                fan = int(attrs.get("fanout", 1))
                spacing = int(attrs.get("spacing", 1))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "bus", loc, inc))
                for i in range(1, fan + 1):
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", f"f{i}",
                                              (loc[0] + 20,
                                               loc[1] - 10 - spacing * 10 * (fan - i)), None))
                continue
            if name == "Register":
                # MEASURED (SamplePorts) and cross-checked against Register.java:
                #   end[0] Q (60,30)  end[1] D (0,30)  end[2] CK (0,70)  end[3] CLR (30,90)  end[4] EN (0,50)
                # Modelling this is what lets the auditor see a Register DRIVING a net. Leaving it
                # opaque produced a false W4 on a correctly wired accumulator: the Q -> Adder.A net
                # looked undriven because the Register was invisible.
                w = int(attrs.get("width", 1))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "Q", (loc[0] + 60, loc[1] + 30), w))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "D", (loc[0], loc[1] + 30), w))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "CK", (loc[0], loc[1] + 70), 1))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "CLR",
                                          (loc[0] + 30, loc[1] + 90), 1))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "EN", (loc[0], loc[1] + 50), 1))
                continue
            if name in ("D Flip-Flop", "T Flip-Flop", "J-K Flip-Flop", "S-R Flip-Flop"):
                w = int(attrs.get("width", 1))
                endpoints.append(Endpoint("rtl", f"{name}@{loc}", "Q", (loc[0] + 50, loc[1] + 10), w))
                for port, (dx, dy), pw in (("D", (-10, 10), w), ("CK", (-10, 50), 1),
                                           ("QB", (50, 50), w), ("PRESET", (20, 0), 1),
                                           ("RESET", (20, 60), 1)):
                    endpoints.append(Endpoint("rtl", f"{name}@{loc}", port,
                                              (loc[0] + dx, loc[1] + dy), pw))
                continue
                continue
            # A component this tool does NOT model: record its position as an OPAQUE port rather
            # than inventing a 1-bit endpoint. Modelling it as 1 bit produced false
            # "width conflict" and "no driver" reports on real circuits (Register/RAM/flip-flops).
            # An opaque port suppresses W4 (we cannot claim the net is undriven) but is reported
            # in the net summary so the coverage gap is visible rather than hidden.
            if loc:
                opaque.append({"ref": f"{name}@{loc}", "loc": loc,
                               "width": int(attrs["width"]) if "width" in attrs else None})

        for w in circ.findall("wire"):
            a, b = parse_loc(w.get("from")), parse_loc(w.get("to"))
            if a and b:
                wires.append((a, b))

        problems, nets = solve(cname, endpoints, tunnels, wires, opaque)
        out["circuits"].append({"name": cname, "nets": nets})
        for p in problems:
            p["circuit"] = cname
            out["problems"].append(p)
    return out

# ----------------------------------------------------------------------------------------------
# Net reconstruction by union-find over coordinates and tunnel labels
# ----------------------------------------------------------------------------------------------

class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

def solve(cname, endpoints, tunnels, wires, opaque=()):
    dsu = DSU()
    # coordinate key for a wire endpoint
    def ck(c):
        return ("coord", c)

    for a, b in wires:
        dsu.union(ck(a), ck(b))
    # a tunnel joins all tunnels sharing a label
    labels: Dict[str, Tuple[int, int]] = {}
    for t in tunnels:
        key = ("label", t["label"])
        dsu.union(ck(t["loc"]), key)
    # every port joins its own coordinate
    for e in endpoints:
        if e.coord:
            dsu.union(("port", id(e)), ck(e.coord))
    # unmodelled components join their coordinate too, so we can tell "attached to something we
    # cannot see" apart from "genuinely nothing here"
    opaque_by_coord: Dict[Tuple[int, int], List[dict]] = defaultdict(list)
    for o in opaque:
        dsu.union(ck(o["loc"]), ck(o["loc"]))
        opaque_by_coord[o["loc"]].append(o)
        dsu.union(("opaque", o["ref"]), ck(o["loc"]))

    # group ports / tunnels / wire-coords per net
    groups: Dict[object, dict] = defaultdict(lambda: {"ports": [], "tunnels": [], "coords": set()})
    for e in endpoints:
        if e.coord:
            groups[dsu.find(("port", id(e)))]["ports"].append(e)
    for t in tunnels:
        groups[dsu.find(ck(t["loc"]))]["tunnels"].append(t)
    for a, b in wires:
        for c in (a, b):
            groups[dsu.find(ck(c))]["coords"].add(c)

    problems: List[dict] = []
    nets = []
    # map every opaque coordinate to its DSU root once, instead of re-deriving it per net
    opaque_root: Dict[object, List[dict]] = defaultdict(list)
    for coord, items in opaque_by_coord.items():
        opaque_root[dsu.find(ck(coord))].extend(items)

    for gi, (root, g) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0]))):
        drivers = [e for e in g["ports"] if is_driver(e)]
        consumers = [e for e in g["ports"] if not is_driver(e)]
        decl = sorted({t["width"] for t in g["tunnels"]})
        port_widths = sorted({e.width for e in g["ports"] if e.width is not None})
        name = g["tunnels"][0]["label"] if g["tunnels"] else f"net{gi}"

        # the effective width: what the drivers deliver, else what the ports declare
        drv_w = sorted({e.width for e in drivers if e.width is not None})
        eff = None
        if drv_w:
            eff = max(drv_w)
            if len(drv_w) > 1:
                problems.append({
                    "code": "W2", "net": name,
                    "message": f"drivers disagree about width: {drv_w}",
                    "detail": [f"{e.ref}:{e.port} w={e.width}" for e in drivers],
                })
        elif port_widths:
            eff = max(port_widths)

        # W1: a tunnel narrower than the net SILENTLY truncates it
        if eff is not None and decl:
            for t in g["tunnels"]:
                if t["width"] < eff:
                    problems.append({
                        "code": "W1", "net": name, "at": t["loc"],
                        "message": (f"tunnel width {t['width']} < net width {eff} "
                                    f"- the net is SILENTLY TRUNCATED to {t['width']} bit(s)"),
                        "why": ("a Tunnel defaults to 1 bit; every tunnel on a wider net must "
                                "declare width"),
                    })
                    break

        # W3: a consumer port whose width differs from the net
        if eff is not None:
            for e in consumers:
                if e.width is not None and e.width != eff:
                    problems.append({
                        "code": "W3", "net": name,
                        "message": (f"{e.ref}:{e.port} expects width {e.width} but the net is "
                                    f"{eff}"),
                    })

        opaque_here = [o["ref"] for o in opaque_root.get(root, [])]

        if not drivers:
            # Only report when the net actually EXPECTS a value. An output Pin is an OBSERVATION
            # point, not an expectation: a deliberately unwired `halt`/probe pin is a legitimate
            # testbench idiom (the sequential testbenches rely on exactly that), so it must not be
            # reported. A bare wire fragment with no ports is likewise harmless.
            #
            # ASYNC CONTROL PINS are excluded for the same reason: a flip-flop's PRESET/RESET are
            # LEVEL controls that a design may legitimately leave floating, and they are active
            # only when driven. Reporting them produced false W4s on every sequential circuit.
            #
            # NOTE: a completely unwired component pin (no wire touches it) IS still reported when
            # it is a value input, because that is a real defect a reader should see - `AND.circ`
            # has three gates with no wiring at all, and that finding must survive.
            CONTROL_ONLY = {"PRESET", "RESET", "CK", "CLK", "EN", "CLR"}
            expecting = [e for e in consumers
                         if not (e.kind == "pin" and e.port == "output")
                         and e.port not in CONTROL_ONLY]
            if opaque_here:
                pass            # attached to a component we do not model -> coverage gap, not W4
            elif expecting:
                problems.append({
                    "code": "W4", "net": name,
                    "message": (f"net has NO driver but feeds "
                                f"{[f'{e.ref}:{e.port}' for e in expecting]} "
                                f"- those inputs read U/undefined"),
                })
        elif len(drivers) > 1:
            problems.append({"code": "W5", "net": name,
                             "message": f"net has {len(drivers)} drivers (contention -> E)",
                             "detail": [f"{e.ref}:{e.port}" for e in drivers]})

        nets.append({"name": name, "width": eff, "tunnel_widths": decl,
                     "drivers": [f"{e.ref}:{e.port}" for e in drivers],
                     "consumers": [f"{e.ref}:{e.port}" for e in consumers],
                     "opaque": opaque_here,
                     "n_wires": len(g["coords"])})
    return problems, nets

def is_driver(e: Endpoint) -> bool:
    """Does this port SOURCE a value onto the net?

    Getting this wrong produces false positives, so the rules are explicit and name-based rather
    than guessed:

      * `Pin` with type="input" is a source (it feeds the circuit); type="output" is a sink.
      * a gate's `out` is a source; its `in*` are sinks.
      * RTL components: only the ports this tool knows to be outputs count as sources.
      * `Splitter.bus` is INPUT_OUTPUT but semantically the assembler, so it is a SOURCE; the
        `f*` fanout ports are the pieces and are sinks here.

    An earlier version treated any non-excluded RTL port as a source, which made `Adder.A`,
    `Splitter.f*` and `Register.Q` all look like drivers and produced spurious contention on
    real circuits.
    """
    if e.kind == "pin":
        return e.port == "input"
    if e.kind == "gate":
        return e.port == "out"
    if e.kind == "rtl":
        # sources: explicit output ports of the components we model
        if e.port in ("out", "Sum", "Cout"):
            return True
        if e.port == "Q" or e.port == "QB":   # Register / flip-flop outputs
            return True
        if e.port in (">", "=", "<"):        # Comparator outputs
            return True
        if e.port.startswith("y") and e.port[1:].isdigit():   # Decoder outputs
            return True
        if e.port == "bus":                  # Splitter assembles the fanout bits
            return True
        return False
    return False

def main() -> int:
    ap = argparse.ArgumentParser(description="Audit .circ nets for width problems (structural).")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    results = []
    bad = 0
    for f in a.files:
        p = pathlib.Path(f)
        try:
            r = analyse(p)
        except ET.ParseError as e:
            results.append({"file": str(p), "error": f"XML parse error: {e}"})
            bad += 1
            continue
        results.append(r)
        if r["problems"]:
            bad += 1

    if a.json:
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
        return 1 if bad else 0

    for r in results:
        print("=" * 78)
        print(r["file"])
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        for c in r["circuits"]:
            multi = [n for n in c["nets"] if (n["width"] or 1) > 1]
            print(f"  circuit {c['name']!r}: {len(c['nets'])} nets "
                  f"({len(multi)} wider than 1 bit)")
            for n in multi:
                print(f"    net {n['name']!r}: width={n['width']} "
                      f"tunnel_widths={n['tunnel_widths']} "
                      f"drivers={n['drivers']} consumers={len(n['consumers'])}")
        if r["problems"]:
            print(f"  !! {len(r['problems'])} PROBLEM(S):")
            for pr in r["problems"]:
                print(f"    [{pr['code']}] net {pr.get('net')!r}: {pr['message']}")
                if pr.get("why"):
                    print(f"          {pr['why']}")
        else:
            print("  OK: no width problems found")
    print()
    print(f"files: {len(results)}   with problems: {bad}")
    return 1 if bad else 0

if __name__ == "__main__":
    raise SystemExit(main())
