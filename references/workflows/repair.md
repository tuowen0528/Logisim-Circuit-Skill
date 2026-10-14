# Workflow — diagnose and repair a failing circuit

> **Status: there IS now a bounded, proven repair tool — but it is NOT general synthesis.**
> `scripts\py\repair.py` searches a small, caller-declared space of candidate edits and reports one
> **only after the real simulation passes**. It cannot design a circuit, and anything outside its
> declared scope is reported as unsupported rather than guessed at.

## Step 0 — is it actually broken?

Before changing anything, make the failure **concrete**:

```powershell
py scripts\py\verify_model.py path\to\circuit.circ --spec <spec.json>
```

Read the exit code:

| exit | meaning | what to do |
|---|---|---|
| `0` | pass | nothing to repair; if you expected failure, the *test* is wrong |
| `1` | definite mismatch | a real failure — continue below |
| `2` | harness problem | **not** a circuit failure; fix the harness first |

⚠️ Never treat `2` as `1`. Conflating them lets a broken circuit masquerade as a tooling problem,
and vice versa.

## Step 1 — try the bounded repair tool

```powershell
py scripts\py\repair.py --kind rtl `
    --broken examples\generated\alu4_broken.circ `
    --spec   examples\specs\rtl\alu4_broken.json `
    --control examples\generated\mux2_broken.circ `
    --control-spec examples\specs\comb\mux2.json
```

What it does, in order:

1. **Refuses to proceed** if the circuit is not actually broken, or if the reference result is a
   harness error rather than a definite failure.
2. Enumerates candidate edits from the declared kinds (`--candidates`): `swap_mux_inputs`,
   `dual_gate`, `flip_constant`, `change_width`.
3. Rebuilds and re-simulates each candidate.
4. Reports a candidate **only** if the golden model now passes.

Verified example — an ALU whose opcode multiplexer had `d2`/`d3` swapped (so `Op=2` computed XOR and
`Op=3` computed OR, giving 674/1024):

```
status      : repaired
REPAIR      : swap mux m inputs d2<->d3
proven by   : passed (passed=1024, failed=0)
control     : fails (exit 1) - the oracle discriminates, so a repair is meaningful
```

### Why the control matters

A "repair" is worthless if the oracle is too weak to say *no*. `--control` names a circuit that must
**not** also pass:

| control result | meaning |
|---|---|
| `exit 1` (definite failure) | the oracle discriminates — a repair is meaningful |
| `exit 2` (cannot be evaluated; e.g. pins do not match the spec) | **weak evidence** — not a pass, but strengthen it |
| `exit 0` (passes) | the oracle is not discriminating — **distrust any repair** |

`--control-spec` lets the control be judged against its own spec so it can fail *definitively*.

### Scope, stated honestly

| | |
|---|---|
| Spec format | **only the `parts` format** (rtllib / seqmodel). A `gates`-format spec reports `unsupported_spec_format`, **not** "no fix found". |
| Edit kinds | the four above; no synthesis, no netlist restructuring |
| Sequential | supported via `--kind seq`, but clocked runs are slower and need `--seconds` |

## Step 2 — structural audit before simulation

```powershell
py scripts\py\audit_width.py path\to\circuit.circ
```

Most "impossible" failures are one of these, and all of them are **silent**:

| Symptom you see | Likely cause | Fix |
|---|---|---|
| all values `U` / `UUUU` | a **Pin does not inherit its net width** (defaults to 1 bit) | set the Pin's width to the net's |
| all values `U`, circuit otherwise sane | a multi-bit net built from **width-less tunnels** (W1: a Tunnel defaults to **1 bit**) | write `width="N"` on **every** tunnel of the net |
| net reads `E` forever | two drivers — often a wire running **along a port's row** or **through an unconnected port** | reroute; give each signal its own x lane |
| `E` only on *some* rows | contention that a conditional path exposes | inspect the net membership, not the drawing |
| output never moves | an undriven clock/enable, or a **clocked part whose clock is `CLK` not `CK`** | verify the clock input is actually driven |
| circuit loaded but a component is missing | a **misspelled component name is silently dropped** | `--tty stats` and compare counts |
| "tie low" behaves as tie high | `Constant` **defaults to TRUE**; a missing `value` drives **1** | always write `value` explicitly |

## Step 3 — escalate to simulation

Combinational circuits enumerate input pins directly. Sequential circuits need an output Pin
labelled **`halt`**, which selects the clocked loop.

⚠️ **Input pins cannot drive a clocked circuit.** The clocked loop never drives them, and the
combinational enumeration sets **values, not edges** — so an edge-triggered part never latches and
you get a full, plausible table in which every output is constant. Measured: a `Register` stayed
`0000` on all 64 rows.

## Step 4 — prove the repair

A repair is not done when the symptom disappears. It is done when **all** of these hold:

1. the failing spec now **passes**, and
2. a **negative control still fails** — otherwise you may have broken the test rather than fixed the
   circuit, and
3. the structural audit is **clean**, and
4. nothing else regressed.

```powershell
py scripts\py\audit_width.py <repaired.circ>            # must be clean
py scripts\py\verify_model.py <repaired.circ> --spec <spec>   # must exit 0
py -m unittest discover -s tests\py                     # must stay green
```

## Step 5 — prefer regeneration over patching

If the circuit came from a spec, **fix the spec and regenerate**. Hand-editing generated XML leaves
the spec and the artifact disagreeing, and the next regeneration silently discards the fix.

## Anti-patterns

* Editing the expected values until the test passes. That is not a repair; it is deleting the test.
* Treating "the table printed" as success — a wrong circuit prints a full table.
* Trusting an exit code — a wrong circuit still exits `0`.
* Repairing without a negative control, so you cannot tell a fix from a coincidence.
* Reading `unsupported_spec_format` as "no fix exists" — it means the tool cannot search this spec.
* Assuming a bounded tool can synthesise. It searches **four edit kinds**; anything else is out of
  scope and must be done by hand and then re-proven.
