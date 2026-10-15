# Workflow — analyse an existing `.circ`

Goal: explain **what a circuit actually does**, backed by measurement, without inferring function
from component names or layout.

## 1. Structural audit first (no simulation, no JVM)

```powershell
py scripts\py\audit_width.py path\to\circuit.circ
```

Cheap and catches whole classes of silent breakage before you spend a simulation run:

| Code | Meaning |
|---|---|
| **W1** | a tunnel on an N-bit net declares width < N — **the net is silently truncated** |
| W2 | two drivers on one net disagree about width |
| W3 | a consumer port's width differs from the net's |
| W4 | a net with no driver that nonetheless feeds inputs (`U`/undefined) |
| W5 | more than one driver on a net (contention → `E`) |

⚠️ Components this tool does not model are reported as `opaque` (a **coverage gap**), never as W4.
A clean report means "no problem among the components this tool models", **not** "the file is
correct".

## 2. Read the structure

```powershell
py scripts\py\generator.py describe path\to\circuit.circ
```

Reports what is literally in the file — library declarations, components with their attributes,
wires — and does **not** infer function from names or layout.

Also useful:

```powershell
java -Djava.awt.headless=true -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty stats --test-circuit path\to\circuit.circ
```

`--tty stats` doubles as a **silent-drop detector**: a misspelled component name is silently
discarded on load, and the count is how you notice.

## 3. Establish connectivity, not appearance

Net membership is decided by the electrical model, not by drawing proximity:

* a `Tunnel` joins every other `Tunnel` sharing its **label**;
* a `wire` joins its two endpoints;
* a component port joins whatever sits at the **same coordinate** — and this includes ports that
  are **unconnected**, which is how a wire running along a port's row silently merges with it.

`Splitter_sample.circ` proves coordinate-shared connection with **zero** `<wire>` elements.

## 4. Simulate — and read the output correctly

```powershell
# combinational: exhaustively enumerates INPUT PINS
java -Djava.awt.headless=true -jar scripts\bin\logisim-evolution-all.jar `
     --locale en --tty table path\to\circuit.circ

# sequential: needs an output Pin labelled `halt`, which selects the clocked loop
```

Reading rules that are easy to get wrong:

* **Rows are printed only when an output CHANGES.** A length-1 stream is a real result ("it never
  changed"), not a truncated run.
* Radix switches to `0x`-hex above a width threshold; below it you get binary.
* Column order is geometric (**y, then x**), inputs before outputs — look pins up **by name**.
* A signal wider than 6 bits occupies **two** columns; the tokeniser splits on whitespace.
* `--tty table` writes **UTF-16LE with a BOM** when redirected to a file.

## 5. Confirm behaviour against an independent model

Never accept a plausible-looking table as proof. State the expected relationship **independently**
(an expression or a small model that does not share code with the circuit), then compare:

```powershell
py scripts\py\verify_model.py path\to\circuit.circ --spec examples\specs\comb\<name>.json
```

`verify_model.py` exit codes: `0` pass, `1` mismatch, `2` harness error. **A non-zero exit for a
harness reason is not a failure of the circuit** — distinguish them.

## 6. Report in four parts

Every claim gets one of these labels, and nothing is promoted between them without evidence:

1. **Confirmed facts** — measured, with the command that produced them.
2. **Functional inference** — what the connectivity implies, stated as inference.
3. **Verification evidence** — the simulation result, including a negative control.
4. **Open questions** — what is still unknown, explicitly.

## Non-negotiables

* Exit codes carry **no** correctness information: a wrong circuit still exits `0`.
* Always run a **negative control** that fails **numerically**, or you have not shown the test can fail.
* Hard timeout on every JVM call; a clocked loop never self-terminates.
* Check `examples\user_samples\` hashes before and after — analysis must be **read-only**.
