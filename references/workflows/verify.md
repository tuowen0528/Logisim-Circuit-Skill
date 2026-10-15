# Workflow: verify a Logisim circuit by real simulation

**Goal:** decide, with evidence, whether a `.circ` implements a required function — and be unable to
fool yourself.

## Before you start

- Resolve the paths below against the project root.
- Read  §1 (the table format) and §9 (the symptom dictionary).
- Know the required function as an **independent model** (integer arithmetic, a spec, a truth table).

## Step 1 — Confirm the components actually loaded

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty stats --test-circuit <circuit.circ>
```

Compare the inventory against what the design intends. A misspelled component name is **silently
dropped**, so a wrong circuit can look perfectly healthy in the table.

- `--tty stats --test-circuit <circ>` is the form to use. (An earlier version of this workflow claimed
  plain `--tty stats` hangs on stdin; that was **not reproducible** — both forms returned exit 0 in a
  controlled test. Do not treat `--tty stats` as risky.)

## Step 2 — Simulate exhaustively

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty table <circuit.circ>
```

One run enumerates **every** input combination: `2**(sum of input widths)` rows.

**Run it under a hard timeout.** If it produces no output and the CPU is pinned, the circuit is
**oscillating** — a combinational loop from an unintended net merge. That is a real result, not a slow
machine:

```powershell
$j = Start-Job -ScriptBlock { param($jar,$f)
  & java '-Djava.awt.headless=true' -jar $jar --locale en --tty table $f 2>&1
} -ArgumentList $jar,$circ
$null = Wait-Job $j -Timeout 60
if ($j.State -eq 'Running') { Stop-Job $j; Remove-Job $j -Force; 'TIMEOUT — suspect oscillation' }
else { Receive-Job $j; Remove-Job $j -Force }
```

## Step 3 — Or let the tool do steps 1–2 plus the comparison

```powershell
py scripts\py\tabletool.py simulate <circuit.circ> [--expect-rows N]
py scripts\py\tabletool.py verify   <circuit.circ> --spec <spec> [--expect-rows N]
```

`tabletool.py` handles the UTF-16 redirected-stdout quirk and the width-dependent radix for you, and
classifies the outcome as `passed` / `failed` / `harness_error`.

## Step 4 — Judge the result correctly

| Observed | Verdict |
|---|---|
| `status = passed`, `passed == expected_vectors`, `failed == 0` | **PASS** |
| `status = failed` with listed mismatches | **FAIL** — the circuit is wrong |
| `status = harness_error` | **NOT a pass.** The run did not produce usable evidence |
| cannot parse a header or any rows | **NOT a pass** |
| row count ≠ `2**input_widths` | **NOT a pass** |

**Never** use the process exit code to decide correctness: a wrong circuit still exits 0. Non-zero
means only that the run failed to start.

**Watch the radix.** Width 1–4 prints binary (`1111`); width ≥ 5 prints hex (`0x0000`). A checker that
assumes hex will silently mis-verify a 4-bit design.

**Look pins up by name.** Column order is geometric (y then x, inputs before outputs) — never by
declaration order.

## Step 5 — Prove your harness can fail (mandatory)

Build a negative control that differs from the good circuit in a way that produces **numeric**
mismatches, and confirm it reports `failed` with a non-zero count.

> A negative control that breaks *geometry* is a bad control: if a substituted component's ports move
> (e.g. `OR Gate` inputs at `loc.x−50` vs `XOR Gate` at `loc.x−60`), the input floats, the output
> becomes `E`, and the run is classified `harness_error` — exercising the **parse** path instead of the
> **comparison** path. Prefer a label-level change (one wire/tunnel reconnected) that leaves every
> component and port intact.

A `512/512` pass with no negative control is **not** evidence.

## Failure modes to name explicitly when reporting

- `U` in a column — nothing drives that net.
- `E` on **every** row of a column — no connection at all.
- `E` on **only some** rows — contention; two nets are merged.
- constant `0`/`1` that looks plausible but is wrong — silent mis-wiring, no diagnostic raised.

## Reporting template

```
circuit        : <path> (sha256)
components     : <literal --tty stats inventory>
command        : <literal command>
rows           : <n> of expected <2**inputs>
result         : passed <n> / failed <n>   (harness_error: none|<reason>)
negative ctrl  : <control circuit> -> failed <n>   [REQUIRED]
not proven     : <explicit list of what this run does NOT establish>
```
