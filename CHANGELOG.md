# Changelog

## Unreleased - fixes from installation feedback

Four defects reported after installing the skill with another agent. All are fixed and verified.

**Tunnel `facing` was inverted for output pins.**
`rtllib.py` and `circmodel.py` applied one facing to every consumer Tunnel (`east`). That is right for a
gate input, but an output Pin's own body extends LEFT, so the glyph was drawn on top of the pin. The
rule is now a function, `tunnel_facing(neighbour_on_the_right)`, which asks where the neighbour is
instead of reusing a constant - the naming is inverted relative to what it draws, and that is what kept
it regressing. Measured: `west` draws the body RIGHT of the port, `east` draws it LEFT.
*Verified:* the change is cosmetic (Tunnels join by label), and `adder4` 512/512 and `alu8`
1,048,576/1,048,576 both still pass.

**No GUI / drawing-level validation existed.**
`scripts/py/audit_visual.py` is new. It measures every component's drawing box, including a Tunnel
glyph's width of `8*len(label)+10` px, and reports `OVERLAP`, `LABEL-CLASH` and `CROWDED`. Electrical
validators cannot see any of that, and a tunnel-first schematic is connected entirely by labels.
*It already found real defects:* `alu4.circ` and `alu8.circ` have overlapping Tunnel glyphs on
multiplexer data pins, which are 10 px apart against a 22 px glyph. That is structural - no facing or
label choice fixes it - and it is now recorded as a known readability limitation rather than hidden.

**`seqmodel.py` silently dropped J-K and S-R flip-flops.**
It emitted `JK Flip-Flop` / `SR Flip-Flop`; the shipped names are `J-K Flip-Flop` / `S-R Flip-Flop`, and
a wrong name is discarded by the loader with no error. Fixed in `seqmodel.py` and `audit_width.py`, and
the "known, not fixed" notes in the docs now read "fixed".

**The `Pin`/`Tunnel` co-location rule in `generate.md` was too broad.**
It forbade co-location outright. The measured behaviour is asymmetric: an **input** Pin with no wire is
never driven, but an **output** Pin co-located with its receiving Tunnel is the standard pattern - it is
how the 512/512 adder terminates its outputs. The rule now states both halves and explains why.

## 1.0.0 — first packaged release

First standalone release. The development tree is not included; this package contains only what a user
needs to install, run and verify the skill.

**Verified capabilities**

* 4-bit ripple-carry adder — 512/512, with a negative control.
* 4-bit subtractor — 512/512, plus three controls that all fail as required.
* 4-bit ALU (Adder + Multiplexer) — 1024/1024.
* **8-bit ALU, tunnel-wired — 1,048,576/1,048,576.**
* 2-bit magnitude comparator (16/16), 4-way multiplexer (64/64).
* 1-bit full adder as **pure wires** — 8/8.
* All six gate kinds, 8-bit, as **pure wires** — 65,536/65,536.
* 8-bit adder with `Cin` tied, as **pure wires** — 65,536/65,536.
* Clocked: 2-bit counter, 8-bit register hold, 4-bit accumulator, divide-by-2.
* Component knowledge base: 74/74 components, 322 attributes, 1,402 geometry sweeps, cross-verified.

**Known limitations, recorded rather than hidden**

* Automatic routing verified for single-bit combinational circuits only. The 8-bit ALU re-routed to
  pure wires passes 725,572/1,048,576 — **not shipped as a verified artifact**.
* Behaviour simulated for 10/74 components.
* Floating-point behaviour unmeasured.
* `seqmodel.py` flip-flop names — **fixed** (now `J-K Flip-Flop` / `S-R Flip-Flop`).
* `ROM` not modelled; RAM contents cannot be preloaded.

**Not included on purpose**

* The Logisim Evolution JAR (52 MB, third-party, GPL) — install it yourself, see `docs/installation.md`.
* The user's private `.circ` reference samples — no public licence.
* Research material, probe programs and intermediate build output.
