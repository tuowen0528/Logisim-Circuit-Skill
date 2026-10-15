#!/usr/bin/env python3
"""generator — THE generator. One entry point, one model, every part kind.

    py generator.py build <spec.json> <out.circ> [--dry-run]

WHY THERE IS ONLY ONE

There were three (`circmodel`, `rtllib`, `seqmodel`), each grown when the previous could not express
something: single-bit gates for a 4-bit adder, then an RTL layer because gates cannot describe a wide
datapath, then a sequential layer because neither could describe a clock. Each step was justified; each
step's SOLUTION was a new file rather than a wider model. The result was three spec vocabularies for
the same concepts, one measured table written twice with CONTRADICTING values (NOT's input offset was
-50 in one and -30 in the other, and -30 is what the jar actually does), and a capability matrix in
which no single generator could build a CPU - combinational in one, sequential in another, subcircuits
in neither.

One model fixes the cause rather than the symptom. A part is a part; combinational and clocked are
properties of it, not different languages. The legacy spec formats are still ACCEPTED - three thin
normalisers below read them into the one IR - so every previously verified spec still builds.

WHAT THE NORMALISERS ACCEPT

    pins        "A0"                 or  ["A", 4]            (name, or name+width)
    gates       [ref, KIND, n, width] as a 4-tuple          (the combinational format)
    parts       {"ref":..., "kind":..., "loc":[...]}        (the RTL / sequential format)
    net drive   "in:A" | "g:ref" | "out:A"                  (string form)
    net use     ["ref", port] | ["ref", 3] | "out:A"        (port name or input index)
    net (seq)   [["ref", "PORT"], ["ref", null]]            (list-of-endpoints form)
    tie         {"ref:PORT": value}                         (constant tied to a port)
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys

import ir
from ir import Circuit, Endpoint, Net, Part, SpecError

if __name__ == "__main__":
    ir.ensure_utf8_stdout()

import emitter
import connect

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="write a .circ")
    b.add_argument("spec")
    b.add_argument("out")
    b.add_argument("--dry-run", action="store_true", help="validate and connect, write nothing")
    a = ap.parse_args()
    if a.cmd != "build":
        ap.print_help()
        return 2

    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    try:
        c = normalize(spec)
    except SpecError as e:
        print(json.dumps({"status": "spec_error", "error": str(e)}, indent=2))
        return 1

    # Every net becomes Tunnels on its ports. There is no router and no wire: a Tunnel placed exactly
    # on a port connects to it, measured on both the input and the output side, for Pins and for gate
    # ports alike. That is why a part's coordinate now affects only legibility, never correctness.
    problems = connect.connect(c)
    problems += connect.check(c)

    if problems:
        print(json.dumps({"status": "spec_error", "error": "; ".join(problems[:8])}, indent=2))
        return 1

    if a.dry_run:
        n_t = sum(1 for p in c.parts if p.kind == "TUNNEL")
        print(f"  ok: {len(c.parts)} parts ({n_t} Tunnel), {len(c.nets)} nets, 0 wires")
        return 0

    emitter.write(c, a.out)
    n_t = sum(1 for p in c.parts if p.kind == "TUNNEL")
    print(f"  wrote {a.out} ({len(c.parts)} parts, {n_t} Tunnel, {len(c.nets)} nets, 0 wires)")
    return 0

# --------------------------------------------------------------------------------------------------
# Normalisers: legacy spec -> IR
# --------------------------------------------------------------------------------------------------

def _pins(spec: dict) -> tuple:
    """Input and output pins from `inputs`/`outputs`. Accepts a name string or a [name, width] pair."""
    ins, outs = [], []
    for entry in spec.get("inputs", []):
        if isinstance(entry, str):
            ins.append((entry, 1))
        else:
            ins.append((entry[0], int(entry[1])))
    for entry in spec.get("outputs", []):
        if isinstance(entry, str):
            outs.append((entry, 1))
        else:
            outs.append((entry[0], int(entry[1])))
    return ins, outs

def _gate_parts(spec: dict) -> list:
    """The combinational format: gates as [ref, KIND, n_inputs, width]."""
    parts = []
    for g in spec.get("gates", []):
        if isinstance(g, dict):
            parts.append(_part_from_dict(g))
            continue
        ref, kind, n, w = g[0], g[1], int(g[2]), int(g[3])
        parts.append(Part(kind="GATE", ref=ref, gate=kind, inputs=n, width=w))
    return parts

def _part_from_dict(d: dict) -> Part:
    """The object format. Unknown keys are refused rather than ignored.

    Legacy kind names are translated into the one model. The sequential format had `NOT` as a kind of
    its own, and its own `ADDER`; here both are parts of the unified set, so `NOT` becomes
    GATE+gate=NOT. Keeping a separate NOT kind is exactly the kind of split that made a CPU
    inexpressible - the same component would exist twice, once per generator.
    """
    known = {f for f in Part.__dataclass_fields__}
    kw = {k: v for k, v in d.items() if k in known and k not in ("kind", "ref")}
    attrs = d.get("attrs") or {}
    for k, v in attrs.items():
        if k in known:
            kw.setdefault(k, v)
    if "loc" in kw:
        kw["loc"] = tuple(kw["loc"])
    kind = d["kind"]
    if kind == "NOT":
        kind, kw["gate"], kw["inputs"] = "GATE", "NOT", 1
    elif kind == "BUFFER":
        kind, kw["gate"], kw["inputs"] = "GATE", "BUFFER", 1
    if kind == "TUNNEL" and not kw.get("label"):
        raise SpecError(f"part {d.get('ref')!r}: a TUNNEL needs a label")
    return Part(kind=kind, ref=d["ref"], **kw)

def _endpoint(tok, spec: dict) -> Endpoint:
    """One endpoint token in any of the accepted spellings.

    Four spellings exist across the legacy formats, and all four are still read:

        "in:A"        an input pin named A
        "out:S"       an output pin named S
        "g:ref"       the output of part `ref`
        "add:Sum"     part `ref`'s named port   <- bare ref:PORT, which the RTL format uses
        ["ref", 3]    part `ref`'s input index 3
        ["ref", null] the part's own loc
    """
    if isinstance(tok, str):
        if ":" in tok:
            head, tail = tok.split(":", 1)
            if head == "in":
                return Endpoint(f"__in_{tail}", None)
            if head == "out":
                return Endpoint(f"__out_{tail}", None)
            if head == "g":
                return Endpoint(tail, "out")
            # NOT a prefix: `ref:PORT`. A bare ref may itself contain no colon, so this is safe.
            # `-` names the component's own loc, which is how a Constant or Clock is addressed -
            # each has a single port at its `loc`.
            return Endpoint(head, None if tail == "-" else tail)
        return Endpoint(tok, None)
    if isinstance(tok, (list, tuple)):
        ref, port = tok[0], (tok[1] if len(tok) > 1 else None)
        if isinstance(port, int):
            port = f"in{port}"
        return Endpoint(ref, port)
    raise SpecError(f"cannot read endpoint {tok!r}")

def normalize(spec: dict) -> Circuit:
    """Read any accepted spec shape into the one Circuit model."""
    c = Circuit(name=spec.get("name", "main"))
    by_ref = {}

    ins, outs = _pins(spec)
    for name, w in ins:
        p = Part(kind="PIN", ref=f"__in_{name}", width=w, label=name, pin_type="input")
        c.parts.append(p)
        by_ref[p.ref] = p
    for name, w in outs:
        p = Part(kind="PIN", ref=f"__out_{name}", width=w, label=name, pin_type="output")
        c.parts.append(p)
        by_ref[p.ref] = p

    for p in _gate_parts(spec) + [_part_from_dict(d) for d in spec.get("parts", [])]:
        if p.ref in by_ref:
            raise SpecError(f"duplicate part ref {p.ref!r}")
        c.parts.append(p)
        by_ref[p.ref] = p

    # ---- nets ---------------------------------------------------------------------------------
    raw = spec.get("nets", {})
    for name, body in raw.items():
        if isinstance(body, dict):
            driver = _endpoint(body["drive"], spec)
            sinks = [_endpoint(u, spec) for u in body.get("use", [])]
        else:
            eps = [_endpoint(e, spec) for e in body]
            if not eps:
                raise SpecError(f"net {name!r} has no endpoints")
            # A net's FIRST endpoint is the driver when it is a source; a bare list therefore needs
            # the driver identified by capability, not by position.
            driver, sinks = None, []
            for ep in eps:
                part = by_ref.get(ep.ref)
                if part is None:
                    raise SpecError(f"net {name!r} references unknown part {ep.ref!r}")
                if driver is None and part.drives(ep.port if ep.port else None):
                    driver, _ = ep, None
                else:
                    sinks.append(ep)
            if driver is None:
                raise SpecError(f"net {name!r} has no driving endpoint")
        for ep in [driver] + sinks:
            if ep.ref not in by_ref:
                raise SpecError(f"net {name!r} references unknown part {ep.ref!r}")
        c.nets[name] = Net(name=name, driver=driver, sinks=sinks)

    # ---- tie: a constant wired to a named port ------------------------------------------------
    for target, value in (spec.get("tie") or {}).items():
        ref, port = target.split(":", 1)
        # `-` names the component's OWN location, which is how a Constant or a Clock is addressed:
        # each has a single port at its `loc`, so `c_zero:-` is that port. Without this the RTL format's
        # tie entries failed with "CONST has no port '-'".
        if port == "-":
            port = None
        tref = f"__tie_{len(c.parts)}"
        part = by_ref.get(ref)
        if part is None:
            raise SpecError(f"tie target {target!r} references unknown part {ref!r}")
        w = part.width if port is None else part.port_width(port)
        tp = Part(kind="CONST", ref=tref, value=int(value), width=w)
        c.parts.append(tp)
        by_ref[tref] = tp
        c.nets[tref] = Net(name=tref, driver=Endpoint(tref, None),
                           sinks=[Endpoint(ref, port)])

    # ---- widths --------------------------------------------------------------------------------
    for n in c.nets.values():
        dpart = by_ref[n.driver.ref]
        n.width = dpart.port_width(n.driver.port) if n.driver.port else dpart.width

    # ---- layout: parts with no loc get placed deterministically --------------------------------
    _place(c, spec)

    # ---- waypoints (escape hatch) --------------------------------------------------------------
    c.waypoints = {k: [tuple(p) for p in v] for k, v in (spec.get("waypoints") or {}).items()}
    return c

def _place(c: Circuit, spec: dict) -> None:
    """Coordinates for every part.

    A spec that supplies `loc` for all of its OWN parts (the `parts` list) keeps them - that is the
    escape hatch for a layout that must be forced by hand. The input and output pins are SYNTHESISED
    from `inputs`/`outputs`, so they never carry a loc; they are always placed in the margins on
    unique rows, where they cannot collide with anything.

    Requiring a loc for every part including the synthesised pins rejected eight RTL specs that were
    perfectly well laid out. The rule that matters is narrower: the parts the SPEC placed must not be
    half-specified, because a half-supplied layout is how two components end up sharing a row.
    """
    from_spec = {d["ref"] for d in spec.get("parts", []) if "loc" in d}
    own = [p for p in c.parts if p.ref in from_spec]
    own_missing = [p.ref for p in c.parts
                   if p.ref in {d["ref"] for d in spec.get("parts", [])} and p.loc == (0, 0)]

    if own and own_missing:
        raise SpecError(
            f"spec gives `loc` for {len(own)} of its parts; {own_missing[:4]} have none, and a "
            f"half-supplied layout can put two components on one row - which merges their nets "
            f"silently. Supply loc for all of the spec's parts, or none.")

    if own:
        # the spec placed its own parts; only the synthesised pins need coordinates
        y = 200
        for p in sorted((q for q in c.parts if q.loc == (0, 0)), key=lambda q: q.ref):
            if p.kind == "PIN" and p.pin_type == "input":
                p.loc = (120, y)
            elif p.kind == "PIN":
                p.loc = (max((q.loc[0] for q in c.parts), default=1200) + 600, y)
            else:
                p.loc = (120, y)
            y += 60
        return

    import layout
    layout.layout(c)

# --------------------------------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(main())
