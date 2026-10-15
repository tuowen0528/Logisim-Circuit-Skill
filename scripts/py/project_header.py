#!/usr/bin/env python3
"""Complete the project-level header of a generated `.circ`, so the file opens like a real one.

WHY THIS EXISTS

`ToolbarData()` (`ToolbarData.java:29-33`) initialises `contents` to an **empty list** - there are no
default tools - and `XmlReader` fills it **only** from the `<toolbar>` element (`XmlReader.java:536`).
A `.circ` without `<toolbar>` therefore opens in Logisim with a **completely empty toolbar**.

Measured structure, user's 9 read-only samples versus 16 generated files:

    element      samples   generated
    <toolbar>      9/9        0/16
    <options>      9/9        0/16
    <mappings>     9/9        0/16
    <lib> count     13         ~3

So the generators emit a circuit that is electrically correct and *looks* unfinished: no toolbar, an
incomplete component-library tree, and no circuit static attributes. This tool splices in the missing
project-level elements, using the block extracted from the user's own `Adder_sample.circ` so the shape
is theirs rather than invented.

WHAT IT CHANGES
  1. the `<lib>` list  -> all 13 libraries, with the `#Wiring` `<tool name="Pin">` child the samples carry
  2. `<options>` / `<mappings>` / `<toolbar>`  -> inserted after `<main .../>`
  3. `<circuit>` static attributes -> appearance / circuit / circuitnamedboxfixedsize / simulationFrequency
  4. `appearance="classic"` added to `Pin` (and Clock / Register / RAM), which samples carry on 37/37 Pins

WHAT IT DOES NOT CHANGE
  Components, wires, coordinates and net topology are untouched. `verify_model.py` must still pass
  with the SAME row count after this runs - that is the regression check, and it is asserted in tests.

Usage:
    py project_header.py <file.circ>              # rewrite in place
    py project_header.py <in.circ> <out.circ>
    py project_header.py --check <file.circ>      # report what is missing, change nothing
"""
from __future__ import annotations

import io
import pathlib
import re
import sys

# Only wrap when RUN, never when imported: a second wrapper around the same buffer closes the
# underlying stream when it is collected, and every later print then raises
# "I/O operation on closed file".
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)

# The 13 libraries, in the order the user's samples declare them. `#Wiring` carries a `<tool>` child
# configuring the default Pin appearance; the samples all have it, so it is reproduced.
LIBS = """  <lib desc="#Wiring" name="0">
    <tool name="Pin">
      <a name="appearance" val="classic"/>
    </tool>
  </lib>
  <lib desc="#Gates" name="1"/>
  <lib desc="#Plexers" name="2"/>
  <lib desc="#Arithmetic" name="3"/>
  <lib desc="#FPArithmetic" name="4"/>
  <lib desc="#Memory" name="5"/>
  <lib desc="#I/O" name="6"/>
  <lib desc="#TTL" name="7"/>
  <lib desc="#TCL" name="8"/>
  <lib desc="#Base" name="9"/>
  <lib desc="#BFH-Praktika" name="10"/>
  <lib desc="#Input/Output-Extra" name="11"/>
  <lib desc="#Soc" name="12"/>
"""

# Project options and mouse mappings, verbatim from the user's samples.
OPTIONS = """  <options>
    <a name="gateUndefined" val="ignore"/>
    <a name="simlimit" val="1000"/>
    <a name="simrand" val="0"/>
  </options>
  <mappings>
    <tool map="Button2" name="Poke Tool"/>
    <tool map="Button3" name="Menu Tool"/>
    <tool map="Ctrl Button1" name="Menu Tool"/>
  </mappings>
"""

# The toolbar. THIS is the element whose absence empties the toolbar.
TOOLBAR = """  <toolbar>
    <tool name="Poke Tool"/>
    <tool name="Edit Tool"/>
    <tool name="Wiring Tool"/>
    <tool name="Text Tool"/>
    <tool name="Image"/>
    <sep/>
    <tool name="Pin"/>
    <tool name="Pin">
      <a name="facing" val="west"/>
      <a name="type" val="output"/>
    </tool>
    <sep/>
    <tool name="NOT Gate"/>
    <tool name="AND Gate"/>
    <tool name="OR Gate"/>
    <tool name="XOR Gate"/>
    <tool name="NAND Gate"/>
    <tool name="NOR Gate"/>
    <sep/>
    <tool name="D Flip-Flop"/>
    <tool name="Register"/>
  </toolbar>
"""

# Static attributes every sample `<circuit>` carries.
CIRCUIT_ATTRS = """    <a name="appearance" val="logisim_evolution"/>
    <a name="circuit" val="{name}"/>
    <a name="circuitnamedboxfixedsize" val="true"/>
    <a name="simulationFrequency" val="16.0"/>
"""

# Component kinds that carry `appearance` in the samples. Counted there as Pin 37/37, Clock 2/2,
# RAM 1/1, Register 1/1. Gates, Adder, Multiplexer, Tunnel and Splitter do NOT carry it.
APPEARANCE_KINDS = ('Pin', 'Clock', 'Register', 'RAM')
APPEARANCE_VALUE = 'classic'

def check(text: str) -> dict:
    """Report the presence of each project-level element.

    Two bugs lived here and both produced FALSE "still missing" reports for a file that was in fact
    correct, which is the worst failure mode for a checker:

      * `circuit_attrs` matched only when `simulationFrequency` was the FIRST `<a>` under `<circuit>`,
        so a correctly completed file reported 0.
      * `pins_without_appearance == 0` means GOOD, but the caller treated any 0 as missing.
    """
    m = re.search(r'<circuit\b[^>]*>(.{0,600})', text, re.S)
    seg = m.group(1) if m else ''
    return {
        'toolbar': len(re.findall(r'<toolbar\b', text)),
        'options': len(re.findall(r'<options\b', text)),
        'mappings': len(re.findall(r'<mappings\b', text)),
        'libs': len(re.findall(r'<lib\b', text)),
        'circuit_attrs': len(re.findall(r'<a\s+name="simulationFrequency"', seg)),
        'pins_without_appearance': count_pins_without_appearance(text),
    }

def missing_elements(c: dict) -> list:
    """Which elements are absent. `pins_without_appearance` and `libs` are NOT zero-means-missing."""
    out = [k for k in ('toolbar', 'options', 'mappings', 'circuit_attrs') if c[k] == 0]
    if c['libs'] < 13:
        out.append(f'libs({c["libs"]}/13)')
    if c['pins_without_appearance'] > 0:
        out.append(f'appearance×{c["pins_without_appearance"]}')
    return out

def count_pins_without_appearance(text: str) -> int:
    n = 0
    for m in re.finditer(r'<comp\b([^>]*)>(.*?)</comp>|<comp\b([^>]*)/>', text, re.S):
        head = m.group(1) or m.group(3) or ''
        body = m.group(2) or ''
        nm = re.search(r'name="([^"]*)"', head)
        if not nm or nm.group(1) not in APPEARANCE_KINDS:
            continue
        if 'name="appearance"' not in body:
            n += 1
    return n

def complete(text: str, source: str | None = None) -> str:
    """Splice in every missing project-level element. Circuit content is left byte-identical.

    `source` sets the `<project source="...">` version string. `XmlReader.java:388-405` READS it into
    `sourceVersion` and branches on it (`compareTo(new LogisimVersion(2,7,2)) < 0`), so it is not
    purely informational - it selects version-specific parsing. The generators write our own
    `5.1.0dev`; the user's samples are `5.0.0`. Pass `--source 5.0.0` to match a 5.0.0 install, then
    re-verify, because the parsing path changes.
    """
    # 0. optionally rewrite the project version string
    if source:
        text, n_src = re.subn(r'(<project\s+source=")[^"]*(")', rf'\g<1>{source}\g<2>', text, count=1)
        if n_src == 0:
            raise SystemExit('no <project source="..."> attribute found')

    # 1. replace the whole <lib .../> run with the full 13-library block (with the tool child)
    text, n_lib = re.subn(r'(?:[ \t]*<lib\b[^>]*/>\s*\n)+', LIBS, text, count=1)
    if n_lib == 0:
        raise SystemExit('no <lib> block found - is this a generated .circ?')

    # 2. insert options / mappings / toolbar immediately after <main .../>
    m = re.search(r'([ \t]*<main\b[^>]*/>\s*\n)', text)
    if not m:
        raise SystemExit('no <main .../> element found')
    if '<toolbar' not in text:
        text = text[:m.end()] + OPTIONS + TOOLBAR + text[m.end():]

    # 3. static attributes as the first children of <circuit>
    def add_attrs(mm):
        name = mm.group(1)
        tail = text[mm.end():mm.end() + 600]
        if 'simulationFrequency' in tail:
            return mm.group(0)
        # consume the following blank line so the attributes sit directly under <circuit>
        return mm.group(0) + CIRCUIT_ATTRS.format(name=name)

    text = re.sub(r'<circuit\s+name="([^"]*)">\n(\s*\n)?', add_attrs, text, count=0)

    # 4. appearance on the kinds that carry it in the samples
    def add_appearance(mm):
        head = mm.group(1) or mm.group(3) or ''
        body = mm.group(2) or ''
        nm = re.search(r'name="([^"]*)"', head)
        if not nm or nm.group(1) not in APPEARANCE_KINDS:
            return mm.group(0)
        if 'name="appearance"' in body:
            return mm.group(0)
        # insert as the first child attribute, matching the samples' ordering
        new_body = re.sub(r'(>)(\s*)', rf'\1\2<a name="appearance" val="{APPEARANCE_VALUE}"/>'
                                           rf'\2', body, count=1)
        return f'<comp{head}>{new_body}</comp>'

    text = re.sub(r'<comp\b([^>]*)>(.*?)</comp>', add_appearance, text, flags=re.S)
    return text

# ------------------------------------------------------------------------------------------------
# The single way any generator should write a .circ
# ------------------------------------------------------------------------------------------------

def write_circ(path, text: str, source: str | None = '5.0.0') -> pathlib.Path:
    """Write a circuit with the project header applied, and REFUSE to write one without it.

    `ToolbarData()` starts with an empty `contents` list and `XmlReader` only fills it from
    `<toolbar>`, so a file that lacks the element opens with an EMPTY toolbar. Applying the header
    here - rather than in a separate manual step - is what stops "the toolbar is gone again" from
    coming back every time a circuit is regenerated.

    Raises RuntimeError if the completed text still fails the header check, so a broken writer
    surfaces immediately instead of producing a file that only LOOKS fine.
    """
    completed = complete(text, source=source) if source else complete(text)
    c = check(completed)
    # FATAL: the elements that make the toolbar and library panel work. A .circ without <toolbar>
    # opens with an EMPTY toolbar and the user cannot place anything.
    fatal = [m for m in missing_elements(c) if not m.startswith('appearance')]
    if fatal:
        raise RuntimeError(
            f'refusing to write {path}: the project header is incomplete (missing {fatal}). '
            f'A .circ without <toolbar> opens with an empty toolbar.')
    # NOT fatal: ppearance on Pins. Every real sample carries it, so its absence is a cosmetic
    # difference from the reference files - reported, never a reason to refuse the file.
    n_noapp = count_pins_without_appearance(completed)
    if n_noapp:
        sys.stderr.write(f'  note: {path} has {n_noapp} Pin(s) without appearance=classic\n')
    path = pathlib.Path(path)
    path.write_text(completed, encoding='utf-8', newline='\n')
    return path

def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        return 0
    do_check = '--check' in sys.argv
    source = None
    if '--source' in sys.argv:
        i = sys.argv.index('--source')
        if i + 1 < len(sys.argv):
            source = sys.argv[i + 1]
            args = [a for a in args if a != source]
    if not args:
        print(__doc__)
        return 2

    src = pathlib.Path(args[0])
    text = src.read_text(encoding='utf-8')

    before = check(text)
    print(f'  {src.name}')
    print(f'    改前: toolbar={before["toolbar"]} options={before["options"]} '
          f'mappings={before["mappings"]} libs={before["libs"]} '
          f'circuit静态属性={before["circuit_attrs"]} '
          f'缺appearance的Pin等={before["pins_without_appearance"]}')

    if do_check:
        missing = missing_elements(before)
        if missing:
            print(f'    缺失: {", ".join(missing)}  -> 需运行补齐')
            return 1
        print('    已完整，无需修补')
        return 0

    out = complete(text, source=source)
    dst = pathlib.Path(args[1]) if len(args) > 1 else src
    dst.write_text(out, encoding='utf-8', newline='\n')

    after = check(out)
    print(f'    改后: toolbar={after["toolbar"]} options={after["options"]} '
          f'mappings={after["mappings"]} libs={after["libs"]} '
          f'circuit静态属性={after["circuit_attrs"]} '
          f'缺appearance的Pin等={after["pins_without_appearance"]}')
    n_comp_before = len(re.findall(r'<comp\b', text))
    n_comp_after = len(re.findall(r'<comp\b', out))
    n_wire_before = len(re.findall(r'<wire\b', text))
    n_wire_after = len(re.findall(r'<wire\b', out))
    print(f'    元件数 {n_comp_before} -> {n_comp_after}，导线数 {n_wire_before} -> {n_wire_after}'
          f'  ({"未变" if (n_comp_before, n_wire_before) == (n_comp_after, n_wire_after) else "!!! 变了"})')
    print(f'    写入 {dst}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
