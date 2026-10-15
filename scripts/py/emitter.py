#!/usr/bin/env python3
"""emitter — the single serialiser: IR -> .circ XML.

There used to be three of these, one per generator, each with its own header, its own library list and
its own attribute table. That is where the silent failures lived:

  * a component under a mismatched library ordinal, or with a wrong XML name, is DROPPED by the loader
    with no message - the file opens, the table prints, the part is not there;
  * an omitted `width` on a Tunnel silently makes it 1 bit, which truncates a bus;
  * an omitted `width` on an Adder silently makes it 8 bits;
  * an omitted `mode` on a Comparator silently makes it SIGNED;
  * an omitted `<toolbar>` opens the file with an empty component palette.

So every one of those is written EXPLICITLY here, for every part, and the library list is derived from
the parts actually present rather than pasted in. A default that is relied upon is an unmeasured
assumption; writing it costs bytes and removes a whole class of silent wrong answers.
"""
from __future__ import annotations

import pathlib

import ir
from ir import Circuit, Part, SpecError

# A `.circ` needs a project header, and a header without <toolbar> opens with an EMPTY palette
# (ToolbarData starts with no entries and only <toolbar> fills it). project_header owns that text and
# refuses to write a file that would still lack it.
import project_header as _ph

def _attrs(p: Part) -> str:
    """Attributes for one part. Explicit everywhere a default would be dangerous."""
    k = p.kind
    a = ""

    if k == "CATALOG":
        # A catalogue-backed part carries its settings verbatim. They are already in the spec because
        # `portdb` needs them to resolve the geometry, and portdb REFUSES a setting the jar was never
        # measured with - so any value reaching here is one the jar is known to accept.
        for name, val in p.cat_attrs.items():
            if isinstance(val, bool):
                val = 'true' if val else 'false'
            a += f'<a name="{name}" val="{val}"/>'
        return a

    if k == "GATE":
        # `width` always. `inputs` only for the multi-input gates: NOT and BUFFER have no such
        # attribute, and writing one they do not recognise is at best noise.
        a += f'<a name="width" val="{p.width}"/>'
        if p.gate not in ir.ONE_INPUT_GATES:
            n = ir.gate_inputs(p.gate, p.inputs)
            if n != 2:
                a += f'<a name="inputs" val="{n}"/>'
        if p.size != ir.DEFAULT_SIZE:
            a += f'<a name="size" val="{p.size}"/>'

    elif k == "MUX":
        a += f'<a name="select" val="{p.select}"/><a name="width" val="{p.width}"/>'

    elif k == "DECODER":
        # A Decoder has NO `width` attribute - only `select` and the tri-state/enable options.
        a += f'<a name="select" val="{p.select}"/>'

    elif k in ("ADDER", "SUBTRACTOR", "MULTIPLIER"):
        # Without this Logisim defaults to 8 bits, so a 4-bit register feeding it produced a width
        # mismatch and every output read `UUU U`. Measured: the A/B/Sum ports come back width=8.
        a += f'<a name="width" val="{p.width}"/>'

    elif k == "COMPARATOR":
        # `mode` is written EXPLICITLY: it defaults to SIGNED, and an unsigned compare that silently
        # went signed is wrong only for operands with the top bit set - the worst kind of wrong.
        a += f'<a name="width" val="{p.width}"/><a name="mode" val="{p.mode}"/>'

    elif k == "SPLITTER":
        a += f'<a name="fanout" val="{p.fanout}"/><a name="incoming" val="{p.width}"/>'
        a += f'<a name="spacing" val="{p.spacing}"/>'

    elif k == "REG":
        a += f'<a name="width" val="{p.width}"/>'
        if p.trigger:
            a += f'<a name="trigger" val="{p.trigger}"/>'

    elif k in ("DFF", "TFF", "JKFF", "SRFF"):
        if p.width > 1:
            a += f'<a name="width" val="{p.width}"/>'
        # Written rather than defaulted: the default IS rising, but an upstream change would silently
        # redefine the edge every sequential circuit here is built around.
        a += f'<a name="trigger" val="{p.trigger}"/>'

    elif k == "RAM":
        # addrWidth and dataWidth are SEPARATE attributes. Omitting either makes Logisim use its own
        # default, so the port widths silently disagree with the wiring.
        a += f'<a name="addrWidth" val="{p.addr_width}"/><a name="dataWidth" val="{p.width}"/>'

    elif k == "CONST":
        a += f'<a name="value" val="0x{p.value:x}"/>'
        if p.width > 1:
            a += f'<a name="width" val="{p.width}"/>'

    elif k == "TUNNEL":
        a += f'<a name="label" val="{p.label}"/>'
        if p.width > 1:
            a += f'<a name="width" val="{p.width}"/>'
        if p.facing:
            a += f'<a name="facing" val="{p.facing}"/>'

    elif k == "PIN":
        # Pin.type defaults to `input`, so an output pin MUST say so.
        a += f'<a name="label" val="{p.label}"/><a name="type" val="{p.pin_type}"/>'
        if p.width > 1:
            a += f'<a name="width" val="{p.width}"/>'
        if p.pin_type == "output":
            a += '<a name="facing" val="west"/>'

    elif k == "CLOCK":
        a += '<a name="facing" val="east"/>'

    elif k == "PROBE":
        if p.label:
            a += f'<a name="label" val="{p.label}"/>'

    else:
        raise SpecError(f"emitter has no attribute rule for kind {k!r}")

    return a

def to_xml(c: Circuit) -> str:
    """Serialise a Circuit. The library list is DERIVED from the parts, never pasted."""
    libs = c.libraries_used()
    unknown = [n for n, o in ir.LIB_ORDINALS.items() if o in libs]
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
             '<project source="5.1.0dev" version="1.0">']
    for name in ("Wiring", "Gates", "Plexers", "Arithmetic", "FPArithmetic", "Memory", "I/O",
                 "TTL", "TCL", "Base", "BFH-Praktika", "Input/Output-Extra", "Soc"):
        if ir.LIB_ORDINALS[name] in libs:
            lines.append(f'  <lib desc="#{name}" name="{ir.LIB_ORDINALS[name]}"/>')
    lines.append(f'  <main name="{c.name}"/>')
    lines.append(f'  <circuit name="{c.name}">')

    for p in c.parts:
        a = _attrs(p)
        lines.append(f'    <comp lib="{p.lib}" loc="({p.loc[0]},{p.loc[1]})" '
                     f'name="{p.xml_name}">{a}</comp>')

    # NO WIRES. Connections are Tunnels, placed by `connect.py`. This serialiser used to own a loop
    # that wrote `<wire .../>`, left over from the router era; it was inert because nothing populates
    # `Circuit.wires` any more, and an inert path that contradicts the architecture is what a later
    # change re-enables by accident. So a wire reaching here is an ERROR, not something to emit.
    if c.wires:
        raise SpecError(
            f"{len(c.wires)} wire(s) reached the serialiser. This skill emits Tunnels only - every "
            f"net must be expressed as Tunnels on its ports by `connect.py`. A wire here means a "
            f"code path was re-enabled that the architecture retired.")

    lines += ['  </circuit>', '</project>']
    return "\n".join(lines) + "\n"

def write(c: Circuit, path) -> pathlib.Path:
    """Serialise and write, applying the project header.

    `project_header.write_circ` REFUSES to write a circuit that would still lack the header, which is
    what stops an empty toolbar from reaching a file.
    """
    path = pathlib.Path(path)
    _ph.write_circ(path, to_xml(c))
    return path
