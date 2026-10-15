---
name: logisim-circuit
description: Read, draw, verify and repair Logisim Evolution .circ schematics. An AI cannot read a .circ file directly - it is XML full of canvas coordinates, library ordinals and component names that mean nothing without measurement - and this skill is what closes that gap in both directions. It turns a .circ into something the model can reason about, and turns the model's own understanding of a circuit into a .circ that really runs in Logisim. Use it when a Logisim or Logisim Evolution file is an input or a deliverable, or when digital logic has to end up as a runnable schematic. Connections are Tunnels only; the skill emits no wires at all.
---

# Logisim Circuit Skill

**A tool that lets an AI read Logisim schematics, and draw them.**

---

## Why this skill exists

An AI cannot read a `.circ` file. Not because the XML is hard to parse — because the file does not say
what it means:

* components are addressed by **library ordinal** plus an XML name, and getting either wrong means the
  loader drops the part **silently**;
* every port is a bare canvas **coordinate**, so "which pin connects to which" is a geometry puzzle, not
  a field;
* attributes are mostly **absent**, and the absent ones default to values that change behaviour
  (`Tunnel.width` → 1 bit, `Comparator.mode` → SIGNED, `Adder.width` → 8 bits);
* a circuit can be wrong in ways that still load and still print a complete table.

Reading such a file requires knowing what was **measured off the real jar**. Writing one requires the
same, plus a way to prove the result.

This skill carries both directions:

| Direction | What it does | Entry points |
|---|---|---|
| **`.circ` → understanding** | reads a schematic into something the model can reason about: structure, widths, connectivity, what each part is | `circ2netlist.py`, `netcheck.py`, `audit_width.py`, `audit_visual.py`, `compdb.py`, `geom.py` |
| **understanding → `.circ`** | turns a spec the model writes into a schematic that runs, and proves it | `generator.py`, `verify_model.py`, `verify_clocked.py` |

**What it does not do:** decide the circuit. Choosing the architecture — a shift-add multiplier, a Moore
controller, a register file and a bus — is the model's own reasoning about digital logic. **Arrive with a
design.** The generator will happily emit a valid `.circ` for a wrong design, and the verifier will then
correctly tell you it is wrong.

---

## The one rule

**A circuit is not done until the real Logisim Evolution has simulated it and every input combination
matched an independent model, with a negative control that failed.**

Exit codes prove nothing. A file existing proves nothing. A validator that reads your own spec cannot
detect a serialiser bug. This project has shipped `.circ` files whose components were silently dropped
and whose tables still printed — with a zero exit code.

Corollary: the `expression` block of a spec is **your design statement**, written independently of the
gate list. Transcribing the gates into it verifies nothing.

---

## Connections are Tunnels. There are no wires.

**Measured** (`tests/samples_work/zerowire/test_pin_tunnel.circ`): a `Tunnel` placed exactly **on** a
port connects to it — on the input side and the output side, for `Pin`s and for gate ports alike. A
correct AND gate was built with **zero `<wire>` elements**. An earlier note in this project claimed the
opposite for input pins; that claim was wrong, and believing it is what pushed the design into a router,
lanes, trunk allocation and six collision audits — all of which existed only to draw wires.

So every net is expressed as Tunnels sharing one label:

```
input Pin ●[Tunnel a]        gate input  ●[Tunnel a]     (the Tunnel sits ON the port)
gate output ●[Tunnel y]      output Pin  ●[Tunnel y]
```

This removes whole classes of failure instead of working around them:

| With no wires there is… | …so |
|---|---|
| no router | nothing to allocate lanes or trunks |
| no spans | nothing to overlap, so no collinear merge |
| no traversal | nothing to pass through a foreign port |
| no routing-dependent layout | **a part's coordinate affects only legibility, never correctness** |

The one invariant left is that **two different labels must never land on one coordinate** — Tunnels at
one point are one node. `connect.check` enforces that, plus "no wires", "one driver per net" and "every
Tunnel sits on a port".

`facing` is cosmetic — Tunnels join by label — but a wrong value draws the glyph over its component. Ask
**where the neighbour is** and point the glyph away from it: measured, `west` draws to the RIGHT of the
port and `east` to the LEFT, which is the reverse of what the names suggest.

---

## One file per job

Modularity is only safe when each module owns exactly one job. This project learned that the expensive
way: three generators each had their own `Part` class, their own spec vocabulary and their own copy of
the measured port tables — and **two of those copies disagreed**. `NOT`'s input offset was computed as
`-50` in one and used as `-30` in the other; the jar does `-30`, so one generator built every NOT gate
with a floating input, and a floating input turns a gate's output into a constant.

| File | The one job |
|---|---|
| `ir.py` | the model, and **the only source of measured geometry** |
| `emitter.py` | IR → `.circ` XML |
| `connect.py` | nets → Tunnels (no wires) |
| `layout.py` | coordinates |
| `generator.py` | spec → IR → connect → emit. **The only generator.** |
| `verify_model.py` | exhaustive verification (every input combination) |
| `verify_clocked.py` | clocked verification (state sequence) |
| `compdb.py`, `geom.py` | query the component catalogue / a component's measured geometry |
| `circ2netlist.py`, `netcheck.py`, `audit_width.py`, `audit_visual.py` | read a `.circ` back: netlist, connectivity, width truncation, drawing |
| `repair.py`, `tabletool.py`, `mkbench.py` | bounded repair, table capture, testbench |
| `project_header.py` | the project header — which is what makes the toolbar non-empty |

`the retired tool set` holds the wire-era tooling (`connect.py`, `connect.py`) and the ad-hoc builders
that existed to prove the router. **Nothing in the skill imports them.**

---

## Workflow

1. **Design first.** Settle the architecture and write down what the circuit computes. That statement is
   the golden model you will be checked against.
2. **Check the components exist and measure them.** `python scripts/py/compdb.py show "<component>"`. If a
   configuration has not been measured the tool **refuses** rather than guessing. Do not guess.
3. **Write a spec, not XML.** Shapes are in `README.md` §5; `references/workflows/generate.md` documents
   every part kind.
4. **Generate.** `python scripts/py/generator.py build <spec.json> <out.circ>` — Tunnels only.
5. **Verify.** `verify_model.py <circuit> --spec <spec>` for combinational work;
   `verify_clocked.py verify <spec> <circuit>` for clocked work.
6. **Audit the drawing.** `audit_visual.py <circuit>` — electrical checks cannot see the schematic, and a
   Tunnel glyph is `8*len(label)+10` px wide.
7. **Read it back.** `circ2netlist.py` and `netcheck.py` turn the generated file into a netlist you can
   compare against the one you intended. If they disagree, the generator is wrong — not your reading.

---

## Measured facts that prevent the classic failures

* **Library ordinals are canonical and dense from zero.** `0 #Wiring, 1 #Gates, 2 #Plexers,
  3 #Arithmetic, 4 #FPArithmetic, 5 #Memory, 6 #I/O, 7 #TTL, 8 #TCL, 9 #Base, 10 #BFH-Praktika,
  11 #Input/Output-Extra, 12 #Soc`. A component under a mismatched ordinal is silently dropped.
* **A wrong component XML name is silently dropped too.** Mind the hyphens: `J-K Flip-Flop`,
  `S-R Flip-Flop`.
* **A `.circ` without `<toolbar>` opens with an empty palette** and nothing can be placed.
* **Gate input offsets differ per kind** — AND/OR `-(size+0)`, NAND/NOR/XOR `-(size+10)`, XNOR
  `-(size+20)` — but **NOT and BUFFER are fixed** at `-30` and `-20`, independent of `size`.
* **Silent defaults**: `Tunnel.width`→1, `Constant.value`→TRUE, `Adder.width`→8, `Comparator.mode`→SIGNED,
  `Pin.type`→input. `emitter.py` writes every one of them explicitly.
* **A stale `.<name>.circ.autosave` makes every later load of that file fail.** Delete it before
  concluding a circuit is corrupt.
* **Two grids, and only one of them is safe.** Coordinates are parsed and snapped to a **5** grid
  (`Location.create` rounds `Math.round(x/5)*5`), but `WireIterator` walks wires in steps of **10**.
  Write every coordinate on the 10 grid. An off-10 coordinate can **hang the simulator**, not merely
  misplace a part, so a hang is a coordinate bug before it is anything else.

---

## Limits — do not overstate these

* **Behaviour is verified by real simulation for 40 of 74 components — 397,651 rows compared.** The other
  34 record `sim_verified: null`, which is an honest "not yet proven", not an implied pass. `compdb show`
  prints `VERIFIED` or `VERIFIED: NO` for whichever component you ask about, so the distinction is
  always one command away. Geometry is measured for **all** 74.
* **The 34 unproven ones**: every sequential component (Counter, all five flip-flops, Register, Shift
  Register, RAM, DualRAM, Random, ROM), ten of the FP operators, the Wiring family, and `PLA`, `BitFinder`
  and `Divider`. `Divider` cannot be verified exhaustively at all: division by zero yields the error
  value, which equals no number in a comparison.
* **NaN payloads are not modelled.** Java preserves a NaN's bits through a float's significand and Python
  cannot reproduce that, so those rows are counted as `skipped` — never as passed. A case that skips rows
  says so.
* **`FPSquareRoot` of a negative returns a NEGATIVE NaN on x86** (`Math.sqrt` compiles to `SQRTSD`, whose
  default QNaN has the sign bit set). On arm64 the same circuit gives the positive NaN. That is the
  platform, not Logisim.
* **`fpwidth` accepts 8, 16, 32 and 64 only.** 1, 2, 3 and 4 are geometrically valid and functionally
  return the error value. Width 8 is MiniFloat143 — 1 sign, 4 exponent, 3 significand, bias 7 — and is
  not IEEE.
* Multiplexer data pins are 10 px apart while a Tunnel glyph is 22 px tall, so tunnelling adjacent data
  pins produces overlapping glyphs. Readability only — `audit_visual.py` reports it.
* Repair is a **bounded search over four edit kinds**, not synthesis.
* **This skill does not design circuits.** The generators accept a spec for a wrong design and emit a
  structurally valid circuit for it. `docs/agent-compatibility.md` records which agents are actually
  verified (only DeepSeek Harness) versus documented-but-untested.

Per-circuit numbers: `README.md` §2 and `docs/internals/CAPABILITIES.md`.
Installation: `docs/installation.md`. Changes: `CHANGELOG.md`.

---

## Learning the components

The catalogue records, for **every one of the 74 components**: what it does, how to use it, the traps
that make it fail silently, and the `file:line` in the upstream source its behaviour was read from.

```bash
python scripts/py/compdb.py purposes            # all 74, one line each
python scripts/py/compdb.py purposes --lib Memory --traps
python scripts/py/compdb.py show Multiplier     # purpose, usage, traps, attributes, ports, sweeps
python scripts/py/compdb.py alltraps            # every silent-failure mode in one list
python scripts/py/compdb.py check Comparator mode=signed   # legality of a configuration
```

Read `purposes` **before** choosing components, and `show` before configuring one. Two examples of what
that buys:

* `Multiplier` - the output is the **LOW half** of A*B by default and the high half is discarded, so a
  4-bit 15*15 reads 1. Verified by simulation, 256/256.
* `Comparator` - `mode` **defaults to SIGNED**, so an unsigned compare built on the default is wrong
  exactly when an operand's top bit is set.

Every recorded trap is there because it produced a circuit that loaded, simulated and computed the
wrong answer. That is the failure mode this catalogue exists to prevent.
