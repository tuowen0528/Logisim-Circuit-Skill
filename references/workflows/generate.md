# Workflow: generate a Logisim circuit from a requirement

**Goal:** turn a natural-language requirement into a `.circ` that loads, simulates, and is proven
correct — without hand-placing coordinates.

## Step 0 — Restate the requirement as something checkable

Produce an explicit, testable spec **before** touching any file:

- **Interface:** every input and output pin, with exact names and widths. Example: `A[4]`, `B[4]`,
  `Cin` → `S[4]`, `Cout`.
- **Function:** an independent model. Prefer plain arithmetic or a truth table over a description.
- **Structure constraints:** e.g. "must be four discrete 1-bit full adders in ripple-carry", "must not
  use the built-in `Adder`".
- **Input space size:** `2**(sum of input widths)` — this is the row count your verification must
  produce.

If the requirement is ambiguous, resolve it now. Every later step depends on this spec.

## Step 1 — Decide the architecture, respecting any structural constraint

- Choose a topology that satisfies the constraint *literally*. If the requirement says "four discrete
  1-bit adders", build exactly that — do not substitute a single wide component and call it equivalent.
- Prefer **hierarchical decomposition** for anything larger than a few gates: define the repeated unit
  once and instantiate it.

## Step 2 — Lay out the circuit (the part that decides success or failure)

**Use the tunnel-first pattern.** Make every inter-gate net a `Tunnel`, which connects by **label**.
This gives zero crossings and zero routing channels and removes the merge failure mode entirely.

Rules, in order of importance:

1. **Do not co-locate a `Pin` with a `Tunnel`.** They do not merge. Join them with an **explicit wire**,
   or the input is never driven and every output silently reads a plausible constant `0`.
2. **Place each consumer `Tunnel` exactly on the gate input port**, so it needs no wire.
3. **Give each driver `Tunnel` a short stub** from its gate output port.
4. **Never use a diagonal wire.** Diagonals rasterise onto foreign coordinates and merge nets.
5. **Never let a wire endpoint land on another net's coordinate** — that creates a T-junction merge.
6. **Namespace internal nets** distinctly from terminal pins. Reusing a label (e.g. `S0` for both an
   internal sum net and an output pin) puts multiple drivers on one net and yields `E` everywhere.
7. **Leave 10 px clear** between a gate's own ports; internal nets are only 10 px apart by default.

### Exact geometry (facing east, `inputs=2`, default `size` 50)

| Component | Output port | Input ports |
|---|---|---|
| `XOR Gate`, `XNOR Gate` | `loc` | `(loc.x−60, loc.y−20)`, `(loc.x−60, loc.y+20)` |
| `AND`/`OR`/`NAND`/`NOR Gate` | `loc` | `(loc.x−50, loc.y−20)`, `(loc.x−50, loc.y+20)` |
| `Pin` | — | its own `loc` |

A wire connects if its **endpoint** is at the port **or** if it **passes through** it. Either is fine —
but a wire that stops *short* of the port leaves the net undriven.

### Component XML

```xml
<comp lib="1" loc="(400,100)" name="XOR Gate"/>
<comp lib="0" loc="(40,80)"  name="Pin"><a name="label" val="a"/><a name="type" val="input"/></comp>
<comp lib="0" loc="(1460,200)" name="Pin"><a name="label" val="S0"/><a name="type" val="output"/><a name="facing" val="west"/></comp>
<comp lib="0" loc="(100,80)" name="Tunnel"><a name="label" val="a"/></comp>
<comp lib="0" loc="(340,80)" name="Tunnel"><a name="label" val="a"/><a name="facing" val="west"/></comp>
```

`<lib name="N">` ordinals are **file-local**, assigned by declaration order. Declare every library a
component references.

## Step 3 — Emit the file from a data structure

Write a small generator rather than the XML by hand — hand-placed coordinates are where every failure
in this project originated. Then:

- Write **UTF-8 without BOM** (this host's Python defaults to cp936 — pass `encoding="utf-8"`).
- Emit `\n` line endings for determinism.

A worked generator is at `gen_adder4.py`.

> **Prefer the general path in [`build.md`](build.md).** A declarative spec plus `circmodel.py` is
> now the supported way to generate circuits, and it verifies against an independent model. The
> earlier hand-written generator `scripts/dsh/gen_adder.py` was **removed**: it wired the first XOR
> gate's port 0 to `Cin` instead of `A0`, so `A0..A3` had no effect on the output while the file
> still loaded and printed all 512 rows (`docs/evidence-matrix.md` W-13/W-16).

## Step 4 — Validate before simulating

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty stats --test-circuit <circuit.circ>
```

Confirm the component inventory matches your intent. This is the **only** cheap detector for silently
dropped components (wrong name, or an out-of-range attribute).

## Step 5 — Verify (do not skip)

Follow [`verify.md`](verify.md). You must end with:

- the exhaustive table from `--tty table`, compared against your **independent** model,
- `passed == 2**inputs` and `failed == 0`,
- **and** a negative control that your harness reports as failing.

## Step 6 — Record what you proved

State the circuit path and sha256, the component inventory, the exact commands, the pass counts, the
negative-control result, and explicitly what remains **unproven**.

## Pitfalls that produce a silently wrong circuit

| Mistake | Consequence |
|---|---|
| Misspelled component name | Component **silently dropped**; file still loads |
| Attribute value out of range | Component **silently dropped** (e.g. `Splitter` `spacing` outside 1–9) |
| `Pin` + `Tunnel` co-located | Input never driven; outputs read plausible constant `0` |
| Diagonal wire | Nets merge → `E`, or a combinational loop → **hang** |
| Wire endpoint on a foreign net | T-junction merge → `E` / hang |
| Reused net label | Multiple drivers on one net → `E` everywhere |
| Wire ending just short of a port | `U` (or `E` if it also touches another net) |
