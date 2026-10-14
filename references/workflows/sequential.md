# Workflow: build and verify a SEQUENTIAL circuit (clocked)

Use this when the requirement involves state — flip-flops, registers, counters, memories. The key
difference from [`build.md`](build.md) is that correctness is a **sequence of states over clock
cycles**, not a combinational truth table.

## The mechanism you are relying on

`--tty table` takes two different paths (`TtyInterface.java:339`, `:344`, `:520-558`):

| Condition | Path | Clocks tick? |
|---|---|---|
| **no** pin labelled `halt` | exhaustive **combinational** enumeration | **NEVER** |
| a pin labelled `halt` exists | the **clock-driven loop** | **YES** |

So a sequential circuit is simulated by **giving it an output pin labelled exactly `halt`**. Every
printed row is one clock tick.

> ⚠️ **Rows are emitted only when an output CHANGES.** A signal that never moves produces **exactly
> one row**. This is a measured fact and it changes how you read results:
> the T-tied-high toggle emitted **583,094** rows; the same circuit with T tied low emitted **1**.
> A one-row result means "this output never changed" — a definite behaviour, **not** a tool failure.

## Step 0 — Write the requirement as a state sequence

State exactly what should happen per cycle. Example, a toggle:

> T is tied high, so on every rising clock edge Q flips: `1, 0, 1, 0, …`

Express it as the repeating period, e.g. `"sequence": ["1", "0"]`.

Write this from the **requirement**. Deriving it from your netlist would only prove the netlist
agrees with itself.

## Step 1 — Write the sequential spec

Sequential specs live in `examples\specs\seq\` (combinational ones in `examples\specs\comb\`) and use a
different schema — explicit geometry, because clocked wiring is not auto-placeable yet.

```json
{
  "name": "main",
  "parts": [
    { "ref": "clk",  "kind": "CLOCK", "loc": [120, 350] },
    { "ref": "t",    "kind": "CONST", "loc": [600, 520], "value": 1 },
    { "ref": "ff",   "kind": "TFF",   "loc": [400, 300] },
    { "ref": "q",    "kind": "PIN",   "loc": [900, 400], "label": "q", "pin_type": "output" },
    { "ref": "halt", "kind": "PIN",   "loc": [900, 60],  "label": "halt", "pin_type": "output" }
  ],
  "nets": {
    "clk": [["clk", null], ["ff", "CK"]],
    "t":   [["t", null],   ["ff", "D"]],
    "q":   [["ff", "Q"],   ["q", null]]
  },
  "waypoints": { "t": [[600, 150], [390, 150]], "q": [[900, 310]] },
  "watch_column": 0,
  "sequence": ["1", "0"]
}
```

Kinds: `CLOCK CONST TFF DFF REG PROBE PIN TUNNEL`. A net endpoint is `[ref, portName]`, or
`[ref, null]` for the part's own `loc`. `PROBE` is passive, so it observes a net without driving it.

**`waypoints` are not decoration — see step 2.**

## Step 2 — Respect the geometry, or you get a SILENT failure

Measured flip-flop ports: **data `(−10,+10)`, clock `(−10,+50)`** — the *same x*, 40 px apart.

Two consequences:

1. **A vertical run at `x = loc.x−10` passes through the clock port.** Routing a constant up to the
   data pin that way merges the constant onto the clock net.
2. **A horizontal approach to the data pin also crosses the clock port** when the source lies to the
   left, because the data pin's x equals the clock pin's x.

Either mistake produced a circuit that **printed zero rows with no diagnostic at all**. Use
`waypoints` to force a route that crosses no other port (the example above rises at `x=600`, clear of
the flip-flop, turns at `y=150`, then descends).

**`seqmodel.validate()` rejects the broken geometry** — always let it run rather than hoping:

```
net 't' wire ((390, 520), (390, 310)) passes through port (390, 350) owned by ['clk'] - that merges the nets
```

## Step 3 — Build

```powershell
py scripts\py\seqmodel.py build examples\specs\seq\seq_tff.json examples\generated\seq_tff.circ
```

Exit `3` means the spec was refused. **Do not work around it** — fix the spec.

## Step 4 — Verify the state sequence

```powershell
py scripts\py\seqmodel.py verify examples\specs\seq\seq_tff.json examples\generated\seq_tff.circ `
   --seconds 3 --cycles 12
```

| Exit | Meaning |
|---|---|
| 0 | `passed` — the value stream follows the expected period |
| 1 | `failed` — a definite mismatch (including "the output never changed") |
| 2 | `harness_error` — **not a pass**: no usable evidence |
| 3 | `spec_error` — the spec was rejected before building |

```
"status": "passed", "cycles_checked": 12, "periods_exercised": 6,
"stream_head": ["0","1","0","1", ...]
```

## Step 5 — Always run a negative control

Build a variant that should **not** work and confirm it reports `failed`:

```powershell
py scripts\py\seqmodel.py build  examples\specs\seq\seq_tff_broken.json examples\generated\seq_tff_broken.circ
py scripts\py\seqmodel.py verify examples\specs\seq\seq_tff_broken.json examples\generated\seq_tff_broken.circ
# expect exit 1: "only 1 row means the output NEVER CHANGED during the whole run"
```

## Limits — do not overclaim

- **The run does not self-terminate.** A `halt` pin that is constantly TRUE only *selects* the clocked
  loop; it does not stop it. Termination needs comparison logic driving `halt` inside the circuit,
  **which is not implemented yet** — so runs are bounded by `--seconds`, and verification is
  inherently sampling-based rather than exhaustive.
- **`--seconds` is a wall-clock bound**, so a slower machine captures fewer cycles. The verifier
  reports how many it actually checked.
- **Sequential wiring is hand-placed**, not auto-routed; `waypoints` are often required.
- **`Counter` is not supported** — its enable/load/clear semantics were not established (it produced
  all-`U`), so build sequencers from flip-flops whose ports *are* measured.
- Component kinds are limited to the list above; a **`Register`** is available but a verified
  register-based spec does not exist yet.
