---
name: logisim-circuit
description: Analyze, design, generate, verify and repair Logisim Evolution circuits (.circ XML) by real simulation. Use when a task involves a Logisim or Logisim Evolution .circ file, digital-logic schematics, or generating and validating combinational or sequential circuits (adders, multiplexers, ALUs, registers, counters) against an independent model and exhaustive input combinations. Covers the .circ format, measured component geometry, headless verification with the official CLI, and how to avoid silent wrong-answer modes.
---

# Logisim Circuit Skill

Analyse, generate, verify and repair Logisim Evolution `.circ` files.

## When to use this

Use it when the task involves a Logisim / Logisim Evolution circuit, or digital-logic design that must
end as a `.circ` file. In particular:

* reading or explaining an existing `.circ`;
* generating a combinational, RTL or sequential circuit;
* proving a circuit correct across **all** inputs, not just a sample;
* finding why a `.circ` silently does nothing, or produces wrong values;
* measuring a component's real ports, bounds or attributes.

## The one rule

**A circuit is not done until the real Logisim Evolution has simulated it and every input combination
matched an independent model, with a negative control that failed.**

Exit codes prove nothing. A file existing proves nothing. A validator that reads your own spec cannot
detect a serialiser bug. This project has produced correct-looking `.circ` files whose components were
silently dropped and whose tables printed anyway — with a zero exit code.

## Workflow

1. **Load the knowledge base** before writing any XML.
   `python scripts/py/compdb.py show "<component>"` — measured attributes, legal values and ports.
   If a combination has not been measured, the tool **refuses** rather than guessing. Do not guess.
2. **Write a spec**, not XML. The JSON shape is shown in `README.md` §5, and
   `references/workflows/generate.md` documents every part kind.
3. **Build**: `circmodel.py` (combinational), `rtllib.py` (RTL), `seqmodel.py` (sequential).
4. **Verify**: `python scripts/py/verify_model.py <circuit> --spec <spec>`.
   Exhaustive by default; raise `--max-rows` for wide circuits.
5. **Audit structure**: `audit_width.py` for silent width truncation, `netcheck.py` for connectivity.
6. **Repair** if a control fails: `repair.py`.

Full procedures: `references/workflows/{analyze,generate,verify,repair,sequential,build}.md`.

## Where things are

| Need | File |
|---|---|
| Component metadata, attributes, legal values | `references/component-catalog.json` |
| Query it | `scripts/py/compdb.py` |
| `.circ` format rules | `references/logisim-format.md` |
| Measured geometry | `references/geometry-and-layout.md`, `scripts/py/geom.py` |
| Every measured fact + the experiment behind it | `references/evidence-log.md` |
| Naming traps and library rules | `references/component-index.json` |

## Measured facts that prevent the classic failures

* **Library ordinals are canonical.** `0 #Wiring, 1 #Gates, 2 #Plexers, 3 #Arithmetic, 4 #FPArithmetic,
  5 #Memory, 6 #I/O, 7 #TTL, 8 #TCL, 9 #Base, 10 #BFH-Praktika, 11 #Input/Output-Extra, 12 #Soc`.
  A component under a mismatched ordinal is **silently dropped**.
* **Wrong component XML name → silently dropped.** Use `NoConnect`, `BitSelector`, `SquareRoot`,
  `MinMax`, `BitAdder`, `BitFinder`, `Random`, `DualRAM`, `POR`, `J-K Flip-Flop`, `S-R Flip-Flop`.
* **A `.circ` without `<toolbar>` opens with an empty toolbar.** `ToolbarData` has no defaults.
* **Gate input offsets are per gate kind**: AND/OR `-(size+0)`, NAND/NOR/XOR `-(size+10)`,
  XNOR `-(size+20)`, NOT `-size`. Getting this wrong makes an input float and the output a constant.
* **Silent-default attributes**: `Tunnel.width`→1, `Constant.value`→TRUE, `Adder.width`→8,
  `Comparator.mode`→SIGNED.
* **Wires connect only where endpoints coincide.** A mid-span crossing does **not** connect; an
  endpoint landing on another wire's span **does** (T junction); collinear overlapping wires merge.
* **A stale `.<name>.circ.autosave` makes every later load of that file fail.** Delete it.

## Limits — do not overstate these

* **Automatic wire routing is verified for single-bit combinational circuits only.** Multi-bit
  multi-column circuits must use tunnel wiring, which is the verified path.
* Behaviour is simulated for **10 of 74** components (the gate family); geometry is measured for all 74.
* Floating-point behaviour is unmeasured.
* `seqmodel.py`'s `JK Flip-Flop`/`SR Flip-Flop` names are wrong and those components are dropped.
* `ROM` is not modelled; RAM cannot be preloaded from a `.circ`.

Details and per-circuit numbers: `README.md` §2 and `docs/internals/CAPABILITIES.md`.
