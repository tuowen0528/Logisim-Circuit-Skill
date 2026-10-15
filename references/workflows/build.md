# Workflow: build a circuit from a specification (the general path)

## What this tool is

A **generation and troubleshooting tool** for `.circ` files: it turns a design you have already worked
out into valid XML, and proves whether the result behaves. It does not choose the architecture, and it
has no built-in templates for adders, ALUs or CPUs — every circuit is expressed through the spec you
write.

Use this when the requirement is **not** one of the pre-named specs. It builds any combinational
circuit from a declarative spec, emits valid `.circ`, and verifies it against an independent model.

## Step 0 — Write the requirement as a checkable spec

Two things matter, and they must be **independent of each other**:

1. **the netlist** — which gates exist and how nets connect them (the implementation);
2. **the golden model** — what the circuit must compute (the requirement).

Never derive the model from the netlist. That is the whole point: a model copied from the
implementation would agree with the implementation's bugs.

## Step 1 — Write the spec JSON

```json
{
  "name": "main",
  "inputs":  ["A", "B", "Sel"],
  "outputs": ["Y"],
  "gates": [ ["nsel", "NOT", 1, 1],
             ["t1", "AND", 2, 1],
             ["t2", "AND", 2, 1],
             ["t3", "OR",  2, 1] ],
  "nets": {
    "A":    { "drive": "in:A",   "use": [["t1", 0]] },
    "B":    { "drive": "in:B",   "use": [["t2", 0]] },
    "Sel":  { "drive": "in:Sel", "use": [["nsel", 0], ["t2", 1]] },
    "nSel": { "drive": "g:nsel", "use": [["t1", 1]] },
    "p":    { "drive": "g:t1",   "use": [["t3", 0]] },
    "q":    { "drive": "g:t2",   "use": [["t3", 1]] },
    "Y":    { "drive": "g:t3",   "use": ["out:Y"] }
  },
  "expression": { "Y": "B if Sel else A" }
}
```

- `gates`: `[ref, kind, n_inputs, width]`. `kind` is one of
  `AND OR NAND NOR XOR XNOR NOT BUFFER` — the emitter maps these to the real XML names
  (`AND Gate`, …; `Buffer` has no suffix).
- `nets`: each net has exactly one `drive` and any number of `use` entries.
  `drive` is `"in:<pin>"` or `"g:<gate ref>"`; a use is `["<gate ref>", <port index>]` or `"out:<pin>"`.
- `expression` **or** `model` supplies the golden model. `model` points to a `.py` file exposing
  `model(inputs, widths) -> dict`; use it when a one-line expression is not enough.
- I/O entries may be `"NAME"` (width 1) or `["NAME", width]`.

## Step 2 — Build

```powershell
py scripts\py\generator.py build examples\specs\comb\mux2.json examples\generated\mux2.circ
```

The builder refuses to write a circuit whose placement would be **silently wrong** (see step 3), so a
non-zero exit here is a real spec/placement problem, not a warning.

## Step 3 — Understand the three silent failure modes it guards

These all produce a file that **loads fine and prints a table**, while the circuit is wrong:

| Failure | Cause | Guard |
|---|---|---|
| A component vanishes | Wrong XML name, or an attribute value out of range | `--tty stats` count check |
| `E` everywhere on some nets | Two nets merged by a coordinate collision | `validate()` — no two nets may occupy one coordinate |
| Plausible constant output | A pin's net is never driven | `validate()` + the model check |

The collision guard exists because tunnels connect **by label**: if two different nets end up sharing
a coordinate, they merge. A real bug of exactly this kind (two input pins landing on one row) merged
`a` and `b` in a full adder. Both pins now get unique rows, and the validator refuses output that
would collide.

## Step 4 — Confirm the component inventory

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty stats --test-circuit examples\generated\mux2.circ
```

Compare the counts against the spec's gate list. A missing gate means it was silently dropped.

## Step 5 — Verify against the independent model

```powershell
py scripts\py\verify_model.py examples\generated\mux2.circ --spec examples\specs\comb\mux2.json
```

Exit `0` = every one of the `2**(input bits)` rows matched; `1` = mismatches; `2` = **harness error,
which is not a pass**.

## Step 6 — Always run a negative control

Build a deliberately wrong variant and confirm the verifier reports `failed`:

```powershell
py scripts\py\generator.py build examples\specs\comb\mux2_broken.json examples\generated\mux2_broken.circ
py scripts\py\verify_model.py examples\generated\mux2_broken.circ --spec examples\specs\comb\mux2_broken.json
# expect exit 1 with a non-zero failed count
```

A pass with no negative control is not evidence.

## Step 7 — Read a circuit back (analysis)

The same module reads any `.circ` into the same data model, without inferring function:

```powershell
py scripts\py\generator.py describe examples\user_samples\Half_Adder.circ
```

## Worked examples in this repo

| Spec | Circuit | Rows | Result |
|---|---|---|---|
| `examples\specs\comb\mux2.json` | 2:1 mux (NOT/AND/AND/OR) | 8 | passed |
| `examples\specs\comb\fulladder.json` | 1-bit full adder | 8 | passed |
| `examples\specs\comb\majority3.json` | majority of three | 8 | passed |
| `examples\specs\comb\adder2.json` | 2-bit ripple-carry (carry chain) | 32 | passed |
| `examples\specs\comb\mux2_broken.json` | negative control | 8 | **failed (expected)** |

Run `py -m unittest discover -s tests\py` offline; set `CIRCMODEL_INTEGRATION=1` to include the
simulation tests.

## Limits — do not overclaim

- **Combinational only, for this path.** The spec language has no clocked components, and
  `verify_model.py` compares a truth table, so it cannot check state sequences. Clocked simulation
  *is* achievable — add a pin labelled `halt` (see `SKILL.md` and
   §14) — but a spec-driven **sequential** verifier does not exist yet.
- **Exhaustive, so it is bounded by `2**(input bits)`.** Beyond roughly 2^17 rows this is not
  practical; a wide circuit needs a different mechanism that does not exist yet.
- **Gate kinds are limited** to the eight above. `Splitter`, `Multiplexer`, `Adder`, memory and I/O
  components are not yet expressible in the spec language.
