# Changelog

## 0.4.0 — the catalogue is proven, not just sourced

Every one of the **74 components** across the six libraries now carries, in
`references/component-catalog.json`:

| field | meaning |
| --- | --- |
| `purpose` | what it computes |
| `usage` | how to configure it |
| `traps` | the silent-failure modes |
| `behaviour_at` | the `file:line` its behaviour was read from |
| `behaviour_source` | a public upstream link (reachability NOT verified from the build host) |
| `propagate_excerpt` | the actual code, shipped in the package so no network is needed |
| `sim_verified` | `null`, or the case names and row count that prove it in the real simulator |

**40 of 74 components are verified by exhaustive simulation — 397,651 rows compared** against
independent golden models. The other 34 record `sim_verified: null`, which is an honest "not yet proven"
rather than an implied pass.

### The knowledge base corrected itself

Every one of these was written down, cited to a source line, and **wrong**. The simulator caught them:

| component | recorded | actually |
| --- | --- | --- |
| `Comparator.mode` | "unsigned default" | `twosComplement` — 1111 is -1, less than 0001 |
| `Bit Extender.type` | "zero-extension default" | `sign` — a 4-bit 15 widens to 0xFFFF, not 0x000F |
| `Decoder.enable` | "defaults false" | `true`; a floating port tied to 0 DISABLES it |
| `XOR Gate.xor` | "parity" | `'1'` = **exactly one** input high; `odd` gives parity |
| `XNOR Gate.xor` | "even parity" | the complement of exactly-one |
| `Priority Encoder` | port 5 an enable | active HIGH, and with no input asserted the output is **undefined**, not 0 |
| `Controlled Buffer/Inverter` | "disabled output is 0" | `UUUU` |
| `Negator`, `Even Parity` | a `negate` attribute | no such attribute; they are `negate0`/`negate1` |
| `FPSquareRoot` | `sqrt(-0.0) = +0.0`, `sqrt(neg) = +NaN` | `-0.0`, and a **negative** NaN on x86 |
| `FPDivider` | "ERR when the result is NaN" | ERR when **either** output is NaN — `1/0` has a finite quotient but a NaN remainder |
| `FPMinMax` | plain `min`/`max` | IEEE minNum/maxNum: `min(+0,-0)` is `-0` |

### Anything can be built now

The generator hand-coded 17 part kinds and could not reach the other 57. A **`CATALOG` part** resolves
geometry from the measured catalogue instead (`portdb.py`), so all 74 are reachable without a second
hand-written port table — the kind of duplication that produced two contradicting NOT-gate offsets here.

A lookup that needs **two** attributes away from their defaults is **refused**, not composed: every sweep
in the catalogue changes exactly one attribute, so composing them silently returns whichever ran last (a
Shifter asked for `width=4, shift=ll` comes back with 8-bit ports).

### Verification tooling

* `verify_model.py` gained `skip_when`. NaN payloads are not reproducible across Java and Python, so
  those rows are counted as **skipped**, never as passed. `passed + failed + skipped == rows` always.
* `fp.py` implements MiniFloat143 from the source, including round-to-nearest-even. `fpwidth` accepts
  **8, 16, 32 and 64 only** — 1, 2, 3 and 4 are geometrically valid and functionally return the error
  value.
* `verify_vectors.py` wraps the CLI's native `--test-vector` runner. It requires `--tty` as well, or the
  splash screen aborts in headless mode and the real exception is shown in a GUI dialog instead of stderr.
* `compdb purposes` and `compdb alltraps` answer "what components are there and what does each do" and
  "which of them fail silently", which is the question an agent actually starts with.

### Fixed

* The release shipped **without `portdb.py`** and with a stale `ir.py`, so the package could not express
  any catalogue-backed component and raised `ModuleNotFoundError` on import. The sync now copies the whole
  tool directory rather than a hand-kept list of seven files.
* `verify_model.py` evaluated golden expressions with `__builtins__` emptied, which removed `min`, `max`,
  `abs` and `int` — so `min(A,B)` failed as a harness error and looked like a broken circuit.
* Two files described the same knowledge base (`components.json` and `component-catalog.json`); the
  catalogue is now a single file.
* `references/component-catalog.json` described 35 of 74 components and was superseded by the
  catalogue; keeping both meant two answers to one question, and the stale one was the shorter one.

---

## 0.3.0 — tunnels, not wires

* **Zero `<wire>` elements.** A `Tunnel` placed exactly on a port connects to it; every net is a label.
* Measured tunnel facing: `west` draws to the RIGHT of its location and `east` to the LEFT — the reverse of
  the names — with the box at `loc+(0,-11)` and width `8*len(label)+10`.
* Gate input offsets measured per gate: `AND`/`OR` at `-(size+0)`, `NAND`/`NOR`/`XOR` at `-(size+10)`,
  `XNOR` at `-(size+20)`, and `NOT`/`BUFFER` FIXED at −30 and −20 regardless of size.
* Verified: `adder4` 512/512 and `alu8` 1,048,576/1,048,576, both with zero wires.

---

## Positioning: this is a tool, not a designer

The scope is stated explicitly in `SKILL.md`, `README.md` and the workflows:

> **This skill does not design circuits.** Choosing the architecture - that a multiplier should be a
> shift-add array, that a controller is a Moore machine with six states, that a CPU needs a register file
> and a bus - is the model's own reasoning about digital logic. The skill turns a design that has already
> been settled into a valid .circ file, and tells you the truth about the result.

The division of labour is written out as a table, and the generate workflow opens with the decisions the
model must have made before the workflow can be used at all (what the circuit computes, the interface,
the architecture, the encodings, the clocking). The frontmatter description does not list design as a
capability.

This matters practically: the generators accept a spec for a wrong design and emit a structurally valid
circuit for it. The skill can tell you the file is well formed and simulates to what you asked for. It
cannot tell you that what you asked for was the right thing to build.

---

## 0.2.0 — measured geometry

* 1,402 single-attribute geometry sweeps measured out of the jar, covering every component.
* Canonical library ordinals recorded; a mismatch or a wrong XML name makes the loader **drop the
  component silently** — `J-K Flip-Flop` and `S-R Flip-Flop` need their hyphens.
* Dangerous defaults written explicitly into every file: `Tunnel.width`, `Constant.value`, `Adder.width`,
  `Comparator.mode`, `Pin.type`.

---

## 0.1.0 — first release

* `.circ` is XML; the file format, and headless verification through the official CLI.
