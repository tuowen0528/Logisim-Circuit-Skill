# Probe log — `.circ` authoring facts established empirically

> **Paths in this document refer to the development tree, not to this package.** The commands
> and file paths were recorded while the measurements were taken and are kept verbatim as
> evidence. This package ships no circuits, no test suite and no `examples/` directory — write
> your own spec and circuit files as shown in `README.md` §5.

Everything below was **observed**, either by the Lead or by a named teammate, against
`scripts\bin\logisim-evolution-all.jar` (Logisim Evolution `5.1.0dev`, sha256 `B872B0C2…68515`).
Nothing here is derived from convention. `[UNVERIFIED]` marks what is still open.

All CLI runs use explicit headlessness and a pinned locale:

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar --locale en --tty <format> <circ>
```

---

## 1. ✅ The verification primitive: `--tty table`

`--tty table` performs **exhaustive simulation** and prints a truth table — one row per input
combination, in ascending counting order. A 9-input adder therefore yields all **512** rows in a
single run, with no test-vector file and no GUI.

```
$ java ... --tty table tests\circ\smoke\minimal.circ
a y
0 0
1 1
exit 0
```

Multi-bit pins render as `NAME[width]`:

```
$ java ... --tty table tests\circ\probe\width2.circ
A[2] Y[2]
  00   00
  01   01
  10   10
  11   11
```

Facts established by `verify-harness` and `fmt-geom`:

- A **1-bit** pin renders **bare** (`Cin`); width ≥ 2 renders `NAME[w]`.
- **Radix is width-dependent:** width **1–6 → binary** (exactly `w` chars); width **≥ 7 → hex**
  (`0x` + lowercase, padded to `ceil(w/4)` digits). So `S[4]` prints `1111`, **not** `0xF`.
  Source: `TtyInterface.java:205` (`width <= 6` → binary, else `"0x"` + hex).
  Measured across widths 4,5,6,7,8 — twice, by two team members:
  `width 5 → 00000`, `width 6 → 000000`, `width 7 → 0x00`.
  > **Correction.** This log previously said the threshold was 5. That was wrong and it caused a
  > real defect: `scripts\py\tabletool.py` branched to hex at `width >= 5`, so it reported
  > `harness_error` on every 5- and 6-bit circuit (`r5.circ` reproduced it, `r4`/`r8` passed).
  > Fixed to `width >= 7`; a regression test now pins the boundary.
- **Column order is GEOMETRIC, not XML order.** `Analyze.getPinLabels` returns a `TreeMap` sorted by
  `Location.CompareVertical` → **y then x** (`Analyze.java:267`, `Location.java:176-188`), and then
  `doTableAnalysis` emits **all inputs first, then all outputs** (`TtyInterface.java:418-437`).
  Ties fall back to `hashCode()`, which is unstable.
  **Generator rule: control column order by *placement*, and have checkers look pins up by NAME.**
- Row count is exactly `2**(sum of input widths)`.
- Fields are whitespace-padded — parse on `split()`, never fixed columns.
- `U` (undefined) and `E` (contention) are **printed as data values**, not parse failures.
- **Exit code carries no correctness information**: a wrong circuit still exits 0.
  Non-zero appears only for gross I/O failures. Exit code separates *ran* from *didn't run* only.

### ⚠️ Redirected stdout is UTF-16

`--tty table` writes **UTF-16 little-endian with a BOM** when stdout is redirected (`1> file`).
Decode with an explicit `utf-16-le` after checking for the `FF FE` prefix — decoding with Python's
generic `"utf-16"` codec mis-handled the BOM in testing.

## 2. ❌ Headless-hostile options (REFUTED as usable)

`Startup.parseArgs` sets `Main.headless = true` **only** in the `--tty` / `--test-fpga` branch
(`Startup.java:362-363`). Every other option leaves `Main.hasGui()` true, so `Main.main`'s catch block
renders the error into a Swing dialog (`Main.java:83-91`) and dies with `java.awt.HeadlessException`,
**swallowing the real exception** into a window that cannot open.

| Option | Result |
|---|---|
| `--test-vector` | `HeadlessException`, exit 1, **empty stdout** |
| `--new-file-format <in> <out>` | `HeadlessException`, exit 1, **no output file created** — so the planned load→re-save round-trip must be replaced by **behavioural** comparison (`--tty stats` / `--tty table`) |
| `--test-circuit` (alone) | `HeadlessException` |
| `--tty stats` / `--tty table` | ✅ work |

Adding `--tty` to `--test-circuit` makes it work (`--tty stats --test-circuit <circ>`).

**Do not build anything on `--test-vector`.** `--tty table` fully replaces it.

---

## 3. ⚠️ Component names silently drop when wrong

**Gate XML names contain a space and the word "Gate":** `AND Gate`, `OR Gate`, `XOR Gate`,
`NAND Gate`, `NOR Gate`, `XNOR Gate`, `NOT Gate` — but `Buffer` has no suffix.

```
$ java ... --tty stats and1.circ        # comp name="AND" (WRONG)
1  1  Pin        Wiring                 # <-- the gate is GONE, no error
1  1  TOTAL (without project's sub circuits)

$ java ... --tty stats and_ok.circ      # comp name="AND Gate" (correct)
3  3  Pin        Wiring
1  1  AND Gate   Gates
4  4  TOTAL (without project's sub circuits)
```

`fmt-geom` found the mechanism: `Loader.showError` (`Loader.java:575-613`) *does* build a diagnostic
(`component "AND" missing from library "1"`) but delivers it through a modal `OptionPane`, which
throws or blocks when headless. **Any loader diagnostic can be invisible in a `--tty` run.**

The same silent-drop class hits **out-of-range attribute values**: `Splitter.spacing` is restricted to
**1..9** (`SplitterAttributes.java:178`); `spacing="40"` made the splitter vanish with no error.

Library ids: `GatesLibrary._ID = "Gates"` (`GatesLibrary.java:28`),
`WiringLibrary._ID = "Wiring"` (`WiringLibrary.java:31`); factory names come from each gate's
`super("...")` call (e.g. `AndGate.java:59`). Multi-library fixture:
`logisim-evolution-main\src\test\resources\fpga\splitter-unconnected-bits.circ`.

**`--tty stats` is the cheap detector for this entire failure class.**

---

## 4. ✅ Gate geometry — `size` DOES drive it

`AbstractGate.computePorts` (`AbstractGate.java:119-127`) declares
`ports[0] = new Port(0, 0, Port.OUTPUT, StdAttr.WIDTH)` → **output at `loc`**, inputs at
`getInputOffset(attrs, i)` (`AbstractGate.java:213-271`).

**Measured input offsets for a 2-input `AND Gate`:**

| `size` | bounds | input offsets from `loc` |
|---|---|---|
| `30` | 30×30 | `(−30, −10)`, `(−30, +10)` |
| **omitted (default)** | 50×50 | **`(−50, −20)`, `(−50, +20)`** |
| `50` | 50×50 | `(−50, −20)`, `(−50, +20)` |
| `70` | 70×70 | `(−70, −20)`, `(−70, +20)` |

**Default is 50**, not 30: `GateAttributes.java:60` sets `size = SIZE_MEDIUM`, value `50`
(`GateAttributes.java:31`).

`XOR Gate` is **10 px wider** (`XorGate.java:54` calls `setAdditionalWidth(10)`), because
`axisLength = size + bonusWidth` (`AbstractGate.java:217`):

| gate | output | input 0 | input 1 |
|---|---|---|---|
| `XOR Gate` | `loc` | `(loc.x−60, loc.y−20)` | `(loc.x−60, loc.y+20)` |
| `AND`/`OR`/`NAND`/`NOR` | `loc` | `(loc.x−50, loc.y−20)` | `(loc.x−50, loc.y+20)` |

Confirmed twice: by simulating all six gates against exhaustive truth tables, and independently by
`comp-catalog` reading `comp.getEnds()` through Logisim's own loader
(`tests\circ\probe\PortDump.java`).

```
AND Gate   y = 0,0,0,1      OR Gate   y = 0,1,1,1      XOR Gate  y = 0,1,1,0
NAND Gate  y = 1,1,1,0      NOR Gate  y = 1,0,0,0      XNOR Gate y = 1,0,0,1
```

**Connection rule.** A wire connects to a port if the wire's **endpoint** is at/near it **or** if it
**passes through** it as an interior point. `comp-catalog` measured the threshold on an `XOR Gate` at
`loc=(400,100)` (true port at x=340): wire ends at 300/320/330/338 → **all-`E`**; **340 → correct**;
380 → correct by pass-through.

> ⚠️ An earlier version of this log reported `(loc.x−20, ∓20)` and concluded `size` did not matter.
> **Both were wrong.** The numbers were the `size=30` case, and the counter-example used `Buffer`,
> which **has no `size` attribute at all**, so it could not be evidence. The apparent "x ≥ 130
> threshold" was the port **containment radius** reaching a port actually at x=100.
>
> **Do not measure ports by bisecting wire endpoints.** Use a geometric dump, or confirm
> behaviourally with `--tty table`.

`[UNVERIFIED]` `inputs >= 3` spacing (reported as `(−60,−20)/(−60,0)/(−60,+20)`); rotation under
non-`east` `facing`; per-input negation offsets.

---

## 5. ⚠️ Subcircuits — port offsets are instance-relative (two earlier claims retracted)

`<circuit name="X">` defines a subcircuit; `<comp loc="(x,y)" name="X"/>` instantiates it;
`--tty stats` lists it as `subcirc`.

**Two successive claims in this log were wrong and are both retracted:**

1. *"An input pin's parent-side port is at the pin's own authored coordinate."* Confounded — the
   parent pins in that probe happened to sit on exactly those coordinates.
2. *"Subcircuit offsets are rescaled to the pins' bounding box."*

**Measured correctly** (`build-eng`, two instances at different `loc`s): offsets are **relative to each
instance's own `loc`** — inputs `(−30, 0)` and `(−30, +10)`, output `(0, 0)`, with authored 40-px pin
gaps collapsing into a 32×32 appearance box. They must be **measured per subcircuit**.

Feeding a parent `Pin` from a subcircuit output produced `E` on every row in one test, so that
connection style is **not** established.

**Conclusion: do not build generated logic from subcircuits.** Tunnels (§6) make them unnecessary —
the shipped 4-bit adder uses none.

---

## 6. ✅ Tunnels are the mandated primitive — they remove the failure mode

A `Tunnel` connects **by label** (`CircuitWires.java:600`). A generated 4-bit ripple-carry adder built
entirely from tunnels **passes 512/512** (§7). Every inter-gate net is a tunnel, so the circuit has
**zero crossings, zero routing channels and zero lanes** — the only wires are short isolated stubs
from a gate output port to its adjacent driver tunnel.

`build-eng` demonstrated the contrast directly: the *same* full-adder function routed with coordinate
lanes and risers produced `E` on both outputs, while the tunnel version was correct on all 8 rows.

**Mandate: generate combinational logic tunnel-first.** Coordinate channel routing is a fallback.

> Two tunnel styles work: a consumer tunnel placed **directly on** a gate input port, or joined to it
> by a short wire. Both are used in this project's probes. What must *not* be done is co-locating a
> **Pin** with a Tunnel and assuming they merge (§8).

---

## 7. ✅ ACCEPTANCE ACHIEVED — 4-bit ripple-carry adder, 512/512

`examples\adder4\adder4.circ`
`sha256 = 67B15194E58B5C05FA4B712BC660415504DA71B743D3446A18B062BFF4D46040`

**Independently re-run by the Lead** (not merely reported):

```
$ py scripts\py\tabletool.py verify examples\adder4\adder4.circ --spec adder --expect-rows 512
  status = passed   passed = 512   failed = 0   expected_vectors = 512   mismatches = 0   exit 0

$ py scripts\py\tabletool.py verify examples\adder4\adder4_broken.circ --spec adder --expect-rows 512
  status = failed   passed = 384   failed = 128   exit 1        <-- negative control
```

**Structure proof** (`--tty stats --test-circuit`, exit 0):

```
  3  Splitter   Wiring
  5  Pin        Wiring
 74  Tunnel     Wiring
  8  AND Gate   Gates
  4  OR Gate    Gates
  8  XOR Gate   Gates
102  TOTAL
```

8 AND + 8 XOR + 4 OR = **20 gates = 4 × 5 = four discrete 1-bit full adders**. The project declares
**only** `#Wiring` and `#Gates` — there is no `#Arithmetic` library, so a built-in `Adder` is
structurally impossible.

Interface: inputs `A[4]`, `B[4]`, `Cin`; outputs `S[4]`, `Cout` → 9 input bits → 512 combinations.

### A negative control must fail numerically

The good and broken files differ in **exactly two lines** — one carry tunnel label `c1` → `cin` at two
coordinates — leaving every gate, port and wire intact, and producing **128 clean numeric
mismatches**.

An earlier attempt (swapping one slice's `XOR Gate` for an `OR Gate`) was a **bad** control: because
`OR Gate` inputs sit at `loc.x−50` while `XOR Gate` inputs sit at `loc.x−60`, the substituted gate's
inputs floated, its output became `E`, and the harness classified the run as `harness_error` — it
exercised the **parse** path, not the **comparison** path.

---

## 8. ⚠️ Silent-zero failure: a Pin and a Tunnel at one coordinate do not merge

While writing the 4-bit generator the Lead placed each input `Pin` and its driver `Tunnel` at the
**same** coordinate with no wire, assuming two components on one point join the net. They do not.
The result raised **no error at all**: the circuit loaded, printed all 512 rows, and returned
**all-zero outputs**. A 1-bit version of the same generator produced `S0 = B0 XOR Cin`, with `A0`
silently ignored — the input nets were simply never driven.

**Rule: join a Pin to a Tunnel with an explicit wire.** And treat "plausible but constant" output as a
first-class suspicion: a bit-exact but wrong circuit raises no diagnostic whatsoever.

---

## 9. Symptom dictionary

| Output shows | Meaning |
|---|---|
| `U` | Not connected — the wire does not touch the port |
| `E` on **every** row of a column | **No connection** |
| `E` on **only some** rows | **Contention** — two nets merged |
| constant `0`/`1`, bit-exact but wrong | Silent mis-wiring (§8) — no diagnostic raised |
| **one input has NO effect on any row** | **Silent mis-wiring.** A correctly connected input must change the output for at least one combination |
| *no output, exit −1, CPU 100 %* | **Oscillation** from a combinational loop |

### The single-input scan — the cheapest diagnostic for a silent mis-wire

Drive **one** input bit at a time and watch the outputs. This instantly reveals which inputs are
actually connected. It found a defect that a 512-row "all rows printed, exit 0" run had hidden:

```
A0=1 -> S=0000  Cout=0        # A has NO effect at all: all four bits dead
A1=1 -> S=0000  Cout=0
A2=1 -> S=0000  Cout=0
A3=1 -> S=0000  Cout=0
B0=1 -> S=0001  Cout=0        # B works
B1=1 -> S=0010  Cout=0
B2=1 -> S=0100  Cout=0
B3=1 -> S=1000  Cout=0
```

Cause: the generator wired the first XOR gate's **port 0** to `Cin` instead of `A0`, so `A0` was
never consumed and `Cin` was consumed twice. **The circuit loaded, printed all 512 rows, and exited
0.** Nothing but a golden-model comparison or this scan would have caught it.

**Two rules follow:**
1. Every gate input must be driven, and no gate input may be driven twice. Validate this in the
   generator (`circmodel.validate`) rather than discovering it from a truth table.
2. If an input appears to do nothing, suspect a mis-wire before suspecting the simulator.

---

## 10. ⚠️ Tooling traps that fabricated results

Each of these cost real time and produced **false conclusions**:

1. **Truncating pipes.** `java ... | Select-Object -First N` closes the pipe, kills the JVM and
   reports a **fake non-zero exit code** (observed `exit=-1` where the truth is `0`). Reusing
   `$LASTEXITCODE` after an array assignment also reports a stale value.
   **Capture stdout fully into a variable or file, then read `$LASTEXITCODE` immediately.**
2. **`Start-Process -ArgumentList` splits paths on spaces.** `<your checkout directory>\...` was handed
   to java as `D:\Logisim`, producing a confusing "cannot find file" failure.
3. **No timeout ⇒ an oscillating circuit burns CPU.** One runaway reached **146 s CPU** before being
   caught. Wrap every CLI probe in a hard timeout (background job + `Wait-Job -Timeout`).

---

## 11. Routing discipline (fallback when tunnels are not used)

The hand-routed `tests\circ\probe\fulladder2.circ` is also correct on all 8 rows, and reaching that
required discovering that careless wiring does not merely give wrong answers — it can **hang the
simulator**:

| Symptom | Cause |
|---|---|
| wrong gate name | component **silently dropped** (§3) |
| `U` | wire does not reach the input port |
| `E` on both nets, exactly when inputs differ | two nets unintentionally **merged** |
| **hang**, exit −1, CPU pinned | an unintended merge closed a **combinational loop → oscillation** |

A **diagonal wire** was the worst offender: `(60,40) → (160,80)` gets rasterised into a staircase that
can land on a foreign net's coordinates and merge the two nets. (`comp-catalog` separately observed a
diagonal wire driving `WireRepair` into `OutOfMemoryError`.)

Discipline that works: one vertical channel `x` per net, one horizontal lane `y` per net, strictly
perpendicular segments, and **never let a wire endpoint land on another net's coordinate** — an
endpoint on another net's interior creates a T-junction and merges them.

---

## 12. Recommended loop for authoring any new component pattern

1. Write the candidate `.circ`.
2. `--tty stats --test-circuit` → component counts are what you intended (**catches silent drops**).
3. `--tty table` **under a hard timeout** → values are `0`/`1`, not `U`/`E` (**catches geometry
   faults**), and a timeout reveals oscillation.
4. Compare against an **independent golden model**; never trust the circuit to check itself.
5. Confirm the harness **can fail** by running a negative control that differs in a way producing
   **numeric** mismatches.
6. Only then trust it.

---

## 13. Open questions

1. `[UNVERIFIED]` Gate port offsets for `inputs >= 3`; rotation under non-`east` `facing`; per-input
   negation offsets.
2. `[UNVERIFIED]` Whether every gate follows `"<NAME> Gate"` — enumerate each `super(...)` line.
3. `[UNVERIFIED]` 7-bit-and-wider value rendering (predicted `0x…`, unmeasured at that width).
4. `[UNVERIFIED]` `desc` strings for `#Plexers` / `#Arithmetic` / `#Memory` / `#I/O`; attribute names
   and ports for `Adder` / `Multiplexer` / `Decoder` / memory / I/O components.
5. `[UNVERIFIED]` Tunnel width-matching and label validation rules.
6. `[UNVERIFIED]` The `speed` / `hex` / `tabs` / `halt` / `tty` output formats (and `binary`/`csv`/
   `tabs` reportedly **hang** because they omit the base `FORMAT_TABLE` bit — `Startup.java:571-579`
   gates `doTableAnalysis` at `TtyInterface.java:344`).
7. `[UNVERIFIED]` Input/output split when a circuit emits outputs before inputs (the harness infers
   the split positionally).

## 14. ✅ SEQUENTIAL SIMULATION IS POSSIBLE — the `halt` pin selects the clocked loop

**This is the single most important capability finding after §1.** `--tty table` is *not* only a
combinational enumerator. `TtyInterface` takes **two different paths**, chosen by whether a `Pin`
whose label is exactly `halt` exists (`TtyInterface.java:339`):

| Condition | Path taken | Clocks tick? |
|---|---|---|
| **no** `halt` pin | `doTableAnalysis(...)` at `TtyInterface.java:344-346` — exhaustive **combinational** enumeration | **NEVER** |
| a `halt` pin exists | the clock-driven loop at `TtyInterface.java:520-558` | **YES** — `toggleClocks(); propagate();` per iteration, one printed row per cycle |

**Reproduce it:** `py scripts\analysis\seq_experiment.py` — three experiments, all currently PASS,
raw output in `tests\samples_work\seq_evidence\`.

⚠️ **A `halt` pin that is constantly TRUE does not stop the run** — it only *selects* the clocked
loop. Termination needs real comparison logic driving `halt`. Until that exists, runs are bounded by
a **wall-clock timeout**; the loop is fast (~7×10⁵ value changes in 3 s).

### ⚠️ Rows count CHANGES, not ticks — and this misled me twice

`--tty table` prints a row **only when an output changes**:

| Circuit | Value-stream length |
|---|---|
| `Clock` observed directly (alternates every tick) | **741,593** |
| toggle flip-flop (alternates every tick) | **604,860** |
| `Register` clocked with a constant on `D` | **1** |

The register changes exactly **once** (`0000` → `1010`), so its stream is length 1 and the **header
row is the pre-first-edge state**. I twice misread that as a truncated run or a failed latch. **A
length-1 stream means "this output changed once (or never)" — a real result, not a tool failure.**

### ⚠️ Two measurement-integrity traps

1. **Reading the output file too early truncates the evidence.** After `taskkill /T` the JVM still
   holds buffered stdout; the same experiment reported 1 row and 2 rows on different runs. Wait for
   the writer to exit, then poll until the file size stops growing.
2. **Sharing one output filename across experiments destroys evidence.** Give each run its own file.

### Measured ports for clocked components (`SamplePorts`)

| Component | Ports relative to `loc` |
|---|---|
| `D`/`T`/`JK`/`SR` flip-flop | data `(−10,+10)` · CK `(−10,+50)` · Q `(+50,+10)` · Q̄ `(+50,+50)` · preset `(+20,0)` · reset `(+20,+60)` |
| `Register` (w=N) | Q `(+60,+30)` · D `(0,+30)` · clr `(0,+50)` · clk `(0,+70)` · en `(+30,+90)` |
| `Counter` (w=N) | OUT `(+190,+110)` · IN `(0,+110)` · CK `(0,+50)` · CLR `(0,+30)` · LD `(0,+20)` · UD `(0,+40)` · EN `(0,+70)` · CARRY `(+190,+50)` |

⚠️ **Port indices differ across components** (flip-flop CK is `end[5]`, `Counter` CK is `end[2]`) —
resolve by NAME, never by index.

### Complete `--tty` subformat list (`Startup.java:499-507`)

`table`, `speed`, `tty`, `halt`, `stats`, `binary`, `hex`, `csv`, `tabs`. `binary`/`csv`/`tabs`
**hang** because they set only a `FORMAT_TABLE_*` sub-flag and omit the base `FORMAT_TABLE` bit.

## 15. ⚠️⚠️ TWO SILENT BUGS THAT ONLY MULTI-BIT NETS EXPOSE

Both produced circuits that loaded, printed a full table, and were **wrong** — with no diagnostic.
Both survived undetected because **no test had ever exercised a multi-bit net**: the 4-bit adder
uses width-1 gates, one slice per bit, so all of its tunnels really are 1 bit.

### 15.1 A `Tunnel` defaults to **1 bit** — multi-bit nets need an explicit `width`

Isolated with one 2-bit AND, varying only the wiring style:

| Variant | Output |
|---|---|
| pins + gate + output pin wired **directly** (no tunnels) | ✅ correct |
| same circuit through **tunnels** with no `width` | ❌ `UU` on every row |
| same circuit through tunnels with `width="2"` on **every** tunnel | ✅ correct |

```
$ java ... --tty table mbit.circ        # tunnels WITHOUT width, 2-bit AND
A[2] B[2] Y[2]
  00   00   UU            <-- silently undefined
```

**Rule: every `Tunnel` on a net of width > 1 must carry `<a name="width" val="N"/>`.** A width-less
tunnel truncates the whole net to 1 bit. Verified to fix widths 2, 4 and 8 (8-bit AND: 65,536/65,536
rows). Both generators now emit the net's width on every tunnel, and a test asserts it.

> **This is the single most consequential format fact found so far**, because the failure is silent
> and it invalidates any multi-bit design built from tunnels without widths.

### 15.2 `Constant` defaults to **TRUE** — a missing `value` drives 1, not 0

`Constant.java` declares `private Value value = Value.TRUE`. So a Constant emitted with no `value`
attribute drives **1**. Measured consequence on an `Adder`:

| `Adder.Cin` | Observed `S` for A=0,B=0 |
|---|---|
| left **unconnected** | **0** — the Adder treats an undriven Cin as 0 |
| tied via a Constant **without** `value` | **1** — i.e. "tie low" was actually "tie high" |
| tied via a Constant **with** `value="0x0"` | **0** ✅ |
| tied via a Constant **with** `value="0x1"` | **1** ✅ |

**Rule: always write `value` explicitly on a Constant.** An "off by one" on your first
Adder-based design is the signature of this bug.

### 15.3 Lesson: the multi-bit path was never really tested

Both bugs were reachable only through a **multi-bit net**, and the whole project had tested
single-bit gates stacked into words. The fix is structural, not just code: an ALU built from an
`Adder`, a `Multiplexer` and 4-bit gates now verifies **1024/1024**, which exercises multi-bit nets
end to end.

## 16. ✅ RTL COMPONENTS — multiplexer, decoder, adder, comparator, splitter

All five exist in 5.1.0dev and their ports are **measured** (`scripts\py\rtllib.py` carries the
tables). Multi-bit nets work once rule 15.1 is honoured.

| Component | Ports relative to `loc` |
|---|---|
| `Multiplexer` (select=k, width=w) | see the measured table below — **not uniform** |
| `Decoder` (select=k) | out `(x+20, y−10−20(2^k−1−i))` · select `loc` · enable `(x−10, y)` |
| `Adder` (width=w) | A `(x−40,y−10)` · B `(x−40,y+10)` · Cin `(x−20,y−20)` · Sum `loc` · Cout `(x−20,y+20)` |
| `Comparator` (width=w) | A `(x−40,y−10)` · B `(x−40,y+10)` · `>` `(x,y−10)` · `=` `(x,y)` · `<` `(x,y+10)` |
| `Splitter` (fanout=n) | bus `loc` · fanout i `(x+20, y−10−spacing*10*(n−i))` |

`Probe` is a passive **INPUT**, so it observes an internal net without driving it — useful for
testbenches.

### ⚠️ `Multiplexer` port geometry is NOT uniform across `select`

A formula fitted to `select=2` was wrong for the others, and two attempts at a general formula each
broke a different case. The **measured table** is:

| select | n | data x offset | first data dy | step | select dy | output |
|---|---|---|---|---|---|---|
| 1 | 2 | `x−30` | −10 | **20** | +20 | `loc` |
| 2 | 4 | `x−40` | −20 | **10** | +20 | `loc` |
| 3 | 8 | `x−40` | −40 | **10** | +20 | `loc` |

**The data-input spacing differs (20 px vs 10 px) and so does the x offset.** `Decoder` outputs by
contrast ARE uniform: 20 px apart with the **last one at `loc.y−10`**, for `select` 1, 2 and 3.

**Lesson: measure each configuration of a parameterised component. One measured configuration is
not a formula.**

## 17. ✅ STRUCTURAL WIDTH AUDIT — catches §15.1 without simulating

Building a 2-bit ripple counter cost four separate geometry failures, each silent in a different
way. They are recorded because **every one of them produced a circuit that loaded and printed a
table** rather than an error.

| # | Mistake | Symptom |
|---|---|---|
| 1 | a wire descends at `x = loc.x−10` to reach a flip-flop's **D**, but **CK sits at the same x, 40 px lower** | constant merged onto the clock net — circuit produced **ZERO rows** |
| 2 | a wire runs **east along `y` = a port's row** while passing it | the two nets merge at that port; a counter read `E`/`1` forever |
| 3 | `q0` routed **straight east from `ff0.Q`** at `y=310` | the run passes **through `ff1.Q` at (750,310)**, merging the two outputs |
| 4 | two nets with **partially overlapping collinear** wires | Logisim merges collinear runs; comparing whole segments for equality misses it |

**Rule: a flip-flop's D and CK share an x and are only 40 px apart vertically.** Give the data route
a horizontal approach and let the clock own that x, or route one of them clear of the other.

`seqmodel.validate()` now rejects all four: wire-through-port, **run-along-port-row**, exact-segment
sharing, and **range-based collinear overlap**. The row-run rule deliberately fires only for a
**foreign** port — a port that belongs to the same net is legitimately on that net's wire.

### ⚠️ A cautionary note on branch routing

`waypoints` describe **one linear path** from the driver to the sink. They cannot branch. Asking for
"reach `ff1.CK` **and** continue to the output pin" therefore silently produced a route that went
`(640,370) → (1200,370)` and **never touched `ff1.CK` at all** — the counter could not count because
its second stage had no clock. A net that must both feed an input and reach a pin needs the input as
its **terminal endpoint**; tapping further along needs a separate net.

### ✅ Multi-column state verification (`watch_columns`)

A single-column watch cannot validate a multi-signal machine: watching only `q0` still "passes" when
the ripple link is broken. `seqmodel` therefore accepts `watch_columns`, and each observed state is
the **tuple** of those columns, compared against a period of state tuples. Verified working: a
two-signal circuit reporting `status: passed`, 10 periods exercised, `stream_head` of
`["0","0"], ["1","0"], …`.



Because the §15.1 truncation is silent, detecting it needs **structural** analysis, not simulation.
`scripts\py\audit_width.py` reconstructs the netlist and reports:

| Code | Meaning |
|---|---|
| **W1** | a tunnel on an N-bit net declares width < N — **the net is silently truncated** |
| W2 | two drivers on one net disagree about width |
| W3 | a consumer port's width differs from the net's |
| W4 | a net with no driver that nonetheless feeds inputs (`U`/undefined) |
| W5 | more than one driver on a net (contention → `E`) |

Net membership is reconstructed the way Logisim connects: tunnels join by **label**, wires join
their endpoints, and any port joins whatever sits at its coordinate.

```
$ py scripts\py\audit_width.py examples\generated\alu4.circ
  circuit 'main': 10 nets (8 wider than 1 bit)
    net 'A': width=4 tunnel_widths=[4] drivers=['A:input'] consumers=4
  OK: no width problems found
```

**Proven to fail on the real defect**: strip the `width` attribute from all 22 tunnels of the ALU
and the auditor immediately reports **8 truncations** (`Y`, `n_sum`, `n_or`, `n_xor`, `n_and`, `A`,
`B`, `Op`) — with no simulation. Put the widths back and it is clean again.

### ⚠️ Two false-positive traps found by validating it against the user's own circuits

Both produced alarming but **wrong** reports on real files:

1. **Port direction must be name-based.** Treating every non-excluded RTL port as a driver made
   `Adder.A`, `Splitter.f*` and `Register.Q` all look like sources → spurious contention.
   `Splitter.bus` is `INPUT_OUTPUT` but semantically the **assembler**, so it is a source; `f*` are
   sinks. And a `Pin` with `type="input"` is a **source**, not a sink.
2. **Do not invent ports for unmodelled components.** Giving every unknown component a 1-bit stub
   at its `loc` produced false "width conflict"/"no driver" reports for `Register`, `RAM` and
   flip-flops. They are now tracked as **opaque**: a net attached to one is reported as a *coverage
   gap*, never as W4 — so the limitation is visible rather than hidden.

After both fixes the audit of the 9 user samples reports only **real** findings, e.g. `AND.circ`
really does contain three gates with no wiring, and `Adder_sample.circ` really does have an 8-bit
source fighting the Adder's default 1-bit input. Four samples audit completely clean.



## 18. ⚠️ ROUTING A SEQUENTIAL CIRCUIT — four distinct ways to short a net

Building a 2-bit ripple counter cost four separate geometry failures, each silent in a different
way. They are recorded because **every one of them produced a circuit that loaded and printed a
table** rather than an error.

| # | Mistake | Symptom |
|---|---|---|
| 1 | a wire descends at `x = loc.x-10` to reach a flip-flop's **D**, but **CK sits at the same x, 40 px lower** | constant merged onto the clock net - circuit produced **ZERO rows** |
| 2 | a wire runs **east along `y` = a port's row** while passing it | the two nets merge at that port; a counter read `E`/`1` forever |
| 3 | `q0` routed **straight east from `ff0.Q`** at `y=310` | the run passes **through `ff1.Q` at (750,310)**, merging the two outputs |
| 4 | two nets with **partially overlapping collinear** wires | Logisim merges collinear runs; comparing whole segments for equality misses it |

**Rule: a flip-flop's D and CK share an x and are only 40 px apart vertically.** Give the data route
a horizontal approach and let the clock own that x, or route one of them clear of the other.

`seqmodel.validate()` now rejects all four: wire-through-port, **run-along-port-row**, exact-segment
sharing, and **range-based collinear overlap**. The row-run rule deliberately fires only for a
**foreign** port - a port that belongs to the same net is legitimately on that net's wire.

### ⚠️ A cautionary note on branch routing

`waypoints` describe **one linear path** from the driver to the sink. They cannot branch. Asking for
"reach `ff1.CK` **and** continue to the output pin" therefore silently produced a route that went
`(640,370) -> (1200,370)` and **never touched `ff1.CK` at all** - the counter could not count because
its second stage had no clock. A net that must both feed an input and reach a pin needs that input as
its **terminal endpoint**; tapping further along needs a separate net.

### ✅ RESOLVED: the 2-bit ripple counter - `00 -> 11 -> 01 -> 10`

`examples\specs\seq\seq_ripple2.json` **verifies**: 24 cycles, 6 periods, 0 mismatches. The earlier
failure was **mistake 2 above in its most deceptive form**:

> `q0` was routed east along `y=350` from x=640 to x=1000. That row is not only CK's row - it is
> also **`ff1.Qbar`'s row, at (750,350)**, and `Qbar` is the *inverse* of `ff1.Q`.

So `Qbar` fought `ff0.Q` on the same net and the counter read `E, 1, E, 1, ...` with `q1` frozen.
The fix is to enter `CK` at (690,350) and then immediately step **down to y=400** before running
east, clear of `Qbar` and of `q1`'s vertical at x=900.

**Why the validator stayed silent** - and this is the real lesson:

> it only checked ports that some net already **claimed**. `Qbar` was **unconnected**, so it
> belonged to no net and was invisible. But an unconnected port is still electrically present.

`_known_ports()` now enumerates **every** port of every component (a flip-flop contributes
`D, CK, Q, QB, PRESET, RESET`), and any wire touching a foreign port - claimed or not - is rejected.
With that fix the validator names the bug exactly:
`net 'q0' wire (640,350)->(1400,350) touches port (750,350) of ff1:QB`.

### WARNING: two comparator defects this counter exposed

Both made a **correct** circuit report `failed`, so neither could be found without a genuinely
independent case to compare against:

1. **List-vs-tuple.** `state_of()` produced **lists** while `period` was built as **tuples**, so
   `[0,0] == (0,0)` was always False and the rotation search never matched. Both sides are now
   normalised to tuples.
2. **Reversed rotation orientation.** `rot` is matched as
   `stream[:n] == period[rot:] + period[:rot]`, so the stream *begins* at `period[rot]` and
   `stream[i]` must equal `period[(i + rot) % n]`. The code used `(i - rot)`. It survived because
   (a) the 1-bit period `[1,0]` is shift-symmetric, and (b) a rotation is **self-inverse** whenever
   `2*rot = 0 (mod n)` - which is exactly the 2-bit counter's `rot=3, n=4`: `6 mod 4 = 2`.

### WARNING: the printed sequence is NOT the textbook counting order

Measured, not assumed: the settled states are **`00 -> 11 -> 01 -> 10`**, *not*
`00 -> 01 -> 10 -> 11`. Because a row is printed only when a watched signal **changes**, the
intermediate states within a clock edge are transients that never get their own row. I first
asserted the textbook order and it was wrong - the expected period must come from measurement,
never from the design's intent.

### ✅ Multi-column state verification (`watch_columns`)

A single-column watch cannot validate a multi-signal machine: watching only `q0` still "passes" when
the ripple link is broken. `seqmodel` accepts `watch_columns`, and each observed state is the
**tuple** of those columns, compared against a period of state tuples. Verified: `status: passed`,
6 periods exercised, live stream `[0,0] [1,1] [0,1] [1,0]`.

## 19. ⚠️ USING THE `Register` FOR THE FIRST TIME EXPOSED THREE DEFECTS

`Register` had been modelled since P7.6 but **no spec ever used it**, so its port table was an
untested claim. Building the first real register circuit found three defects that all agreed with
each other and therefore cancelled out.

### 19.1 The port table had `CLR` and `EN` SWAPPED

Measured with `SamplePorts` and confirmed in `Register.java` (declaration order is
`OUT, IN, CK, CLR, EN`):

| index | port | offset from `loc` |
|---|---|---|
| `end[0]` | `Q` | `(+60,+30)` |
| `end[1]` | `D` | `(0,+30)` |
| `end[2]` | `CLK` | `(0,+70)` |
| `end[3]` | `CLR` | `(+30,+90)` |
| `end[4]` | `EN` | `(0,+50)` |

The table had `CLR=(0,50)` and `EN=(30,90)` - **swapped** - and **the unit test asserted those same
wrong values**, so the test agreed with the bug instead of catching it. A test written from the same
misunderstanding as the code proves nothing.

### 19.2 A `Pin` did not inherit its net's width

A spec's blanket `width` applies to declared parts, so Pins defaulted to **1 bit**. Wiring an 8-bit
`Register.Q` into a 1-bit output Pin left every value **undefined** - the harness reported a
single-row stream of `U` for a correctly wired register. Pins are now sized from the net they sit on.

### 19.3 The clock check only knew about `CK`

`validate()` required every *flip-flop* to have `CK` driven. A `Register` names its clock `CLK`, so
registers were **never validated at all** - a floating register clock would build happily and never
latch. The check now covers both.

### 19.4 `Register` semantics (from source, then confirmed by simulation)

```java
if (CLR == TRUE) value = 0;
else if (triggered && EN != FALSE) value = IN;
```

* A fresh `Register` is **UNDEFINED**. With `CLR` tied low the output stayed `U` **forever** -
  observed as a 1-row stream of `U`. `CLR` is how a defined power-on state is established.
* `CLR` **overrides** the load, so it must be de-asserted for a load to be observable.

**Verified load-and-hold** (`examples\specs\seq\seq_reg8_hold.json`): `CLR` driven by `NOT(CLK)`, `EN`
high, `D=0xa`. Stream is exactly **`0000` -> `1010`** and then nothing, because the value is held.
`status: passed`.

### 19.5 ⚠️ A signal wider than 6 bits occupies TWO table columns

An 8-bit pin renders as `0000 0000`, and the harness tokenises on whitespace, so it splits into two
columns and the watcher reads half a value. **Watched signals must be at most 6 bits wide** (one
rendered token). The register test uses 4 bits for this reason.

### 19.6 ⚠️ "Converges and holds" is not a harness error

Rows print only on **change**, so a circuit that latches once and then holds produces exactly as
many rows as it has real changes. The comparator demanded **two** full periods and so rejected a
correct, fully observed load-and-hold as a `harness_error`. The rule is now: if the stream stopped
growing before the requested cycle count, **one** complete period is sufficient evidence; two are
required only while the run is still producing rows.

### 19.7 `seqmodel` gained a `NOT` gate

Needed because a `Register`'s `CLR`/`EN` are **level**-sensitive: driving `CLR` from `NOT(CLK)` is
how a load/hold cycle is expressed with no signal-generator primitive. Ports are measured from
section 4 - output at `loc`, input 30 px to the left.

### 19.8 The `Register` port cluster forces one x lane per signal

`D`, `CLK` and `EN` are all at `x=600` and `CLR` at `x=630`, stacked at `y=330/350/370/390`. **Any
vertical wire in that column crosses foreign ports**, and `CK`'s row (`y=350`) is also `EN`'s row.
`validate()` rejected **six** distinct shorts across four attempts at this layout. Working rule: give
every signal its own x lane for the long run, and touch the port column only for the final approach.


### 19.9 ⚠️⚠️ `seqmodel` SILENTLY DROPPED MIDDLE NET ENDPOINTS

The most serious defect of this round. The router built its path as

```python
full = [pts[0]] + route + [pts[-1]]      # WRONG
```

so for a net with three or more endpoints, **only the first and last were ever wired**. The
component in the middle was left **floating on the board** while the spec claimed it was connected -
and `validate()` reported no problem, because it inspected the spec, not the emitted wire.

It was found only because a NOT gate's input was a middle endpoint. The consequence was that the
register's `CLR` floated, and the load/hold test **passed for the wrong reason**: with `CLR`
undriven the load happened on *every* edge, so a constant `D` was simply latched - which looks
identical to a correct load/hold cycle.

**Fixes**
* the router now walks **every** endpoint: `[pts[0]] + route + [pts[-1]]` became
  `[pts[0]] + route + [pts[-1]]` *with* all intermediate endpoints spliced in, and when no
  waypoints are given it joins the endpoints directly;
* `validate()` now checks that **every declared endpoint actually lies on the emitted wire** and
  names any that does not.

The new check immediately caught a real case in `seq_ripple2`, whose `q1` net listed `ff1:Q` while
its waypoints routed from `(900,500)` - the output was never wired to its own pin.

**Lesson: a validator that reads the SPEC cannot detect a bug in the SERIALISER.** The check has to
compare the declared intent against what was actually emitted.


## 20. ⚠️ INPUT PINS CANNOT DRIVE A CLOCKED CIRCUIT

Attempting to build a register testbench out of input pins produced a definitive negative result,
and it is worth recording because the intuition is wrong.

`--tty table` has **two modes**, and input pins work in only one of them:

| mode | selected by | input pins |
|---|---|---|
| **combinational** `doTableAnalysis` (`TtyInterface.java:394`) | no `halt` pin | **enumerated and driven** via `Pin.FACTORY.driveInputPin` - this is how the 512-row adder acceptance case works |
| **clocked** `runSimulation` (`TtyInterface.java:490`) | a `halt` pin exists | **never driven** - an input pin simply sits at `U` for the whole run |

Worse, the combinational path sets **VALUES, not EDGES**:

```
$ java ... --tty table reg_pins.circ          # din[4], en, clk are input pins
din[4] en clk q[4]
  0000  0   0 0000
  0000  0   1 0000
  0001  1   0 0000
  ...                                         65 rows, q = 0000 on EVERY row
```

An edge-triggered part never sees a clock transition, so it never latches. **A pin-driven
edge-triggered design silently tests nothing** - it prints a full, plausible truth table in which
the outputs are all constant.

`seqmodel.validate()` therefore **refuses input pins in a sequential spec**, with the measured
evidence in the message, rather than allowing a circuit that cannot be verified.

**Consequence for this project**: there is still no way to run "assert CLR, then release it, then
observe the load". A load/hold cycle remains unverifiable, and `Register`'s LOAD path is unverified.
Recorded as an open gap.


## 21. ⚠️⚠️ THE SKILL WAS NOT ACTUALLY LOADABLE

The most consequential packaging defect of the project, and one that **no circuit test could ever
have caught**: every tool worked, every spec passed, and the Skill was **invisible to the Harness**.

`.dsh\skills\logisim-circuit\SKILL.md` began with a Markdown heading:

```markdown
# Logisim Circuit Skill — Harness entry point
```

There was **no frontmatter block at all**. The DSH loader requires the file to *start* with a line
that is exactly `---`, and to contain a `name` and a `description`; without them the skill is
**silently ignored** - no error, no warning, it simply never appears in the catalog.

`scripts\dsh\validate-skill.js` had been written specifically to encode these rules and said so
plainly:

```
RESULT: INVALID — no parseable frontmatter block (must start with --- and close with ---)
```

The file had **never been run against the entry point**. Writing a validator is not the same as
running it.

**Fix**: real frontmatter added (`name: logisim-circuit` plus a description). Confirmed by the
`logisim-circuit` skill appearing in the available-skill catalog, which it had never done before,
and by `validate-skill.js` returning `VALID`.

### Related traps, all silent

| Trap | Effect |
|---|---|
| no `---` block | no metadata found → skill ignored |
| UTF-8 BOM | first line becomes `\uFEFF---` → no metadata → skill ignored |
| `name` not lower-kebab-case | rejected by the loader regex |
| missing `description` | required string → skill ignored |
| writing the entry point as *documentation about* the skill | prose mentioning `name:` is **not** metadata |

### Guard added

`tests\py\test_skill_packaging.py` now asserts: the entry point exists, has no BOM, **starts** with
`---`, carries a non-empty `name` and `description`, has a loader-legal name, passes
`validate-skill.js`, and that `scripts\analysis\audit_skill.py` reports no problems. Packaging is now
covered by the same suite as the logic, so this cannot silently regress.


## 22. ✅ THE `Register` LOAD PATH IS VERIFIED — and two validator bugs it exposed

Round 11 concluded that a load/hold cycle was inexpressible, because `CLR` had to be asserted and
then released and nothing could sequence it. That conclusion was **half right and half my own
measurement error**.

### 22.1 The round-11 design was sampling the wrong phase

That attempt drove `CLR` from `NOT(CLK)`. Since `--tty table` samples outputs while the clock is
**low**, `NOT(CLK)` is **high** at every observation - so the register was being *cleared* at each
sample and correctly read a constant 0. The `CLR` path really was proven; the *load* was never
reachable in that arrangement, and I misread the constant 0 as "cannot do this at all".

### 22.2 Tying `CLR` LOW makes the load observable

`Register.java`: `if (CLR == TRUE) value = 0; else if (triggered && EN != FALSE) value = IN;`.
`CLR` **overrides** the load, so releasing it is what permits a load. With `CLR = 0`, `EN = 1`,
`D = 0xa` and a free-running clock:

```
$ java ... --tty table seq_reg4_load.circ
0000
1010
```

**Exactly two rows, and still exactly two after a 10-second run (~10^6 clock cycles).** The register
loaded once on the first clock edge and then **held** forever. A free-running oscillator keeps
emitting rows, so a 2-row stream across a million cycles is positive evidence of latching rather
than of a stalled run.

`examples\specs\seq\seq_reg4_load.json` now verifies this: **`status: passed`**.

### 22.3 ⚠️ Two validator bugs that checked geometry the serialiser never produced

Both produced **false** "component would be left FLOATING" reports on valid specs:

1. **`_wire_net()` emitted the RAW endpoint pair.** A non-axis-aligned pair like
   `(200,700) -> (250,400)` appeared as one diagonal "segment", while the file actually contained
   the two orthogonal segments. `_on_segment` only understands axis-aligned segments, so valid wires
   were rejected. Fixed by routing through `_orthogonal` exactly as the serialiser does.
2. **The endpoint check tested the raw point pairs** rather than the orthogonalised ones, so it
   compared against diagonal pairs again. Fixed to test the emitted segments.

**A validator must inspect the emitted artifact, not the intent.** This is the third time a defect
of that shape has appeared (round 11's middle-endpoint drop, round 12's unrun validator), and it is
now recorded as a standing rule.

### 22.4 ⚠️ The power-on row is not part of the cycle

The clocked loop prints a row **before** the first edge: the power-on state. When that state is a
value the period contains, including it additionally verifies it - the counter's first row really is
a member of its period. When it is **not** (a register powering up at 0 and then holding `0xa`
forever, period `['1010']`), row 0 can never match a rotation, and searching from row 0 **rejected a
correct circuit**.

The rotation search now tries row 0 and then row 1, and when row 1 is used the power-on row is
**excluded from the comparison** (not merely from the search - checking it against `expect(0)`
reported the power-on row itself as a mismatch). The result reports `power_on_row_excluded`.


## 23. ✅ BOUNDED, PROVEN REPAIR (P8) — and why the negative control is the whole point

The objective lists **repair**, and until now `workflows/repair.md` said plainly that no automatic
repairer existed. What is now implemented is deliberately narrow, because the honest version of this
capability is narrow:

> `scripts\py\repair.py` searches a **small, caller-declared** space of candidate edits and reports
> one **only after the real simulation passes**. It is **not** synthesis.

### What it does

1. **Refuses to proceed** if the circuit is not actually broken, or if the reference result is a
   harness error rather than a definite failure. ("Fixing" a working circuit is not a feature.)
2. Enumerates candidates from four declared kinds: `swap_mux_inputs`, `dual_gate`, `flip_constant`,
   `change_width`.
3. Rebuilds and **re-simulates** each candidate.
4. Reports a candidate only if the golden model now passes.

### Verified result

An ALU whose opcode multiplexer had `d2`/`d3` swapped - so `Op=2` computed XOR and `Op=3` computed
OR, giving **674/1024** - was pinned exactly and proven:

```
status      : repaired
REPAIR      : swap mux m inputs d2<->d3
proven by   : passed (passed=1024, failed=0)
control     : fails (exit 1) - the oracle discriminates, so a repair is meaningful
```

### ⚠️ The negative control is the point, not a formality

A "repair" is worthless if the oracle is too weak to say *no*. `--control` names a circuit that must
**not** also pass, and the three outcomes are distinguished rather than lumped together:

| control result | meaning |
|---|---|
| `exit 1` — definite failure | the oracle discriminates, so a repair is **meaningful** |
| `exit 2` — cannot be evaluated (e.g. pins do not match the spec) | **weak evidence**; not a pass, but strengthen it |
| `exit 0` — it passes | the oracle is **not discriminating**; distrust any repair |

### ⚠️ Three defects found while building it, all in the harness rather than the search

1. **Wrong builder for the spec format.** `--kind comb` sent an RTL spec to `circmodel`, so every
   candidate was refused at build time and the tool reported "no fix found" while having tested
   nothing. The `kind` now selects `rtllib` for RTL specs.
2. **The verifier was chosen from `--kind`, not from the spec.** A perfectly good control looked
   like it passed, because a sequential spec was fed to the combinational verifier. The **artifact**
   now decides.
3. **"Not a pass" was conflated with "a definite failure".** The control check accepted only
   `exit 1`, so an unevaluable control was reported as "DID NOT FAIL". Those are different facts and
   are now reported separately.

### ⚠️ Scope is reported, never blurred

* **Spec format**: only the `parts` format (rtllib / seqmodel). A `gates`-format spec returns
  `unsupported_spec_format` **exit 2** rather than "no fix found" — because "I cannot search this"
  and "I searched and found nothing" are different claims, and conflating them overstates the tool.
* **Edit kinds**: four. No synthesis, no netlist restructuring, no multi-edit repair.

**Lesson: a bounded tool must report the boundary of its search separately from the outcome of its
search.**


## 24. ✅ FIRST CLOCKED DATAPATH — the 4-bit accumulator, and the silent width default it exposed

`seqmodel` had **no arithmetic component at all**, so a clocked datapath was inexpressible: there was
no way to write `Q + 1 -> D`. Adding the `Adder` (port table identical to `rtllib`'s, measured in
section 16) made the canonical CPU building block expressible:

```
Register.Q -> Adder.A
constant 1 -> Adder.B          Adder.Sum -> Register.D      (the feedback loop)
CLK -> Register.CLK            EN tied high, CLR tied low
```

Verified: **`status: passed`**, 64 cycles, 4 full periods, 0 mismatches. The observed stream is
`0000 0001 0010 ... 1111 0000 ...` - a modulo-16 accumulator, which is a counter whose carry is
deliberately dropped (Cout left unconnected).

### ⚠️ The `Adder` silently defaulted to EIGHT bits

The first build produced a single row of `UUUU`, with **no diagnostic anywhere**. The cause:

> the serialiser emitted `<comp lib="3" loc="(900,300)" name="Adder"></comp>` with **no `width`
> attribute**, and Logisim's default is **8 bits**.

So a 4-bit register drove an 8-bit adder, the widths disagreed, and every output went undefined.
`SamplePorts` confirmed it: `A`, `B` and `Sum` all came back `width=8`.
`seqmodel` now writes the width for `ADDER` alongside the flip-flops and `REG`.

**This is the third instance of the same family** - a component attribute that Logisim defaults
silently, so omitting it produces a circuit that loads, prints a table, and is wrong:
`Tunnel` width (1 bit), `Constant` value (`TRUE`), and now `Adder` width (8 bits).
**Rule: every width-bearing component must state its width explicitly**, and the table of measured
port widths is the check.

### ⚠️ Layout: `D` and `Q` share a row

A `Register`'s `D` is at `(x, y+30)` and its `Q` at `(x+60, y+30)` - **the same y**. A feedback wire
returning to `D` along that row therefore sweeps straight through `Q` and merges the two nets, which
`validate()` refused. The feedback descends at `x = 620`, i.e. **between** `D`'s column (600) and
`Q`'s column (660), and approaches `D` from the right, stopping short of `Q`.

Two further placement rules this circuit needed, both already recorded but exercised together here:

* each constant also needs its **own ROW**, not just its own column - the increment constant's long
  horizontal run otherwise passed straight through another constant's port;
* an output `Pin` can simply sit **on** a net's wire, so one net drives both the datapath and the
  observation point. A `PROBE` cannot do this job: it is passive and drives nothing.


## 25. ✅ RAM PORTS MEASURED — and why RAM contents CANNOT be preloaded from a `.circ`

`RAM` is now modelled in `seqmodel`. Every port below was read with `SamplePorts` (Logisim's own
loader), **not** inferred from source, and the port **names and index order** come from
`RamAppearance.configurePorts`, which fills its array in this order:
`addr, data-out, data-in, OE, WE, clk, LE, BE, CLR`.

Measured for `addrWidth=2`, `dataWidth=4` at `loc=(700,400)`:

| port | absolute | width | direction |
|---|---|---|---|
| `A` (address) | `(700, 410)` | 2 | IN |
| `WE` (write enable) | `(700, 450)` | 1 | IN |
| `OE` (output enable) | `(700, 460)` | 1 | IN |
| `CK` (clock) | `(700, 470)` | 1 | IN |
| `Din` | `(700, 490)` | 4 | IN |
| `Dout` | `(940, 490)` | 4 | OUT |

Offsets: `A (0,10)`, `WE (0,50)`, `OE (0,60)`, `CK (0,70)`, `Din (0,90)`, `Dout (+240,+90)`.
**`WE` sits ABOVE `OE`** - an easy pair to transpose, exactly as `CLR`/`EN` were on the `Register`.

⚠️ **All five inputs share `x = 700`**, stacked only 10-20 px apart, and `Dout` is on the **opposite**
edge. This makes RAM the worst layout case in the whole catalogue: a vertical run down `x=700`
sweeps through **four foreign ports**, and because *a wire touching any port merges with it*, that
one mistake merges five nets at once. The rule that resolves it:

> **the LAST waypoint of each net must already be on that port's own row**, so the final approach is
> a short horizontal run and never a vertical one.

### ⚠️⚠️ RAM BEHAVIOUR IS NOT VERIFIED — and my first reading of it was WRONG

The first working testbench produced `UUUU -> 1010`, and I read that as *"the RAM stored `D=10` at
address 0 and read it back"*. **That reading was wrong**, and the negative control caught it.

`1010` **is** `Din`. So the observation is equally consistent with a working memory and with a
circuit that simply passes `Din` through to `Dout`. The decisive experiment is to vary the data:

| variant | last observed `Dout` |
|---|---|
| `Din = 10` (`1010`) | `1010` |
| `Din = 5` (`0101`) | `0101` |
| `Din = 0` (`0000`) | `0000` |

**`Dout` tracks `Din` exactly.** In this circuit the RAM therefore behaves as a **pass-through**,
and `UUUU -> 1010` was **not** evidence of a completed write. Varying the address 0..3 also changed
nothing - every address read `1010`. Confirming that `OE = 0` does force `UUUU`, so the
output-enable control is real and correctly wired.

This is precisely the failure mode the project's verification discipline exists to catch: a
plausible, fully-formed table that looks like success and is not.
**A value that happens to equal the input is not evidence of storage — the test must VARY the input,
or it proves nothing.**

Root cause is not yet established. The plausible candidate is that the RAM's `CK` never produces the
edge a write needs; distinguishing "the write never completes" from "the read follows the data pins"
requires scheduling `WE`/`OE`/address **in time**, which the toolkit still cannot do.
**Recorded as an open gap**: RAM port GEOMETRY is measured and correct; RAM BEHAVIOUR is unverified.

**Confirmed on a SECOND, independent layout.** A much simpler 8-part testbench (below) was built
with the same topology, and it reproduces the effect exactly:

| variant | last observed `Dout` |
|---|---|
| `Din = 10` | `1010` |
| `Din = 9` | `1001` |
| `Din = 5` | `0101` |
| `Din = 0` | `0000` |
| `addr = 0 / 1 / 3` | `1010` / `1010` / `1010` |

`Dout` tracks `Din` on **both** layouts, and no address changes the read. So this is a property of
the circuit construction, not of one particular routing.

⚠️ **The same wrong inference was made twice.** The second layout was built by a helper that
reported *"the RAM WROTE din=10 into address 0 and read it back"*, reasoning from `q = 1010` — which
is exactly `Din`. That is the **identical** error I had already made and corrected one step earlier
on the other layout. The lesson generalises: **an observation consistent with a working system and
with a degenerate one is not evidence for either**, and the check that separates them (vary the
input) must actually be *run*, not merely known.

### ✅ Routing lesson from the simpler layout: corridors are the PROBLEM, not the solution

The layout that finally built on the first attempt is dramatically simpler than the corridor scheme
I had been iterating on:

```json
"parts": [
  clk  CLOCK (100,450)   inv NOT (300,450)      addr CONST (200,410) w2 v0
  din  CONST (300,490) w4 v10                    oe   CONST (500,460) w1 v1
  ram  RAM   (700,400) addr_width2 width4        q    PIN   (1200,490) output
  halt PIN   (1200,60)  output
],
"nets": {
  "addr": [["addr",null],["ram","A"]],   "din":  [["din",null],["ram","Din"]],
  "oe":   [["oe",null],  ["ram","OE"]],  "we":   [["inv","out"],["ram","WE"]],
  "ck":   [["clk",null], ["inv","in"],["ram","CK"]],
  "dout": [["ram","Dout"],["q",null]]
},
"waypoints": { "ck": [[270,450],[270,470]] }
```

**Only ONE net needs waypoints at all.** The rule that makes it work:

> put each source **on its destination port's own row**, so the connection is a single straight
> horizontal wire. Then no net ever crosses another's row, and a 2-endpoint net needs **no**
> waypoints — so it cannot acquire a bad final approach.

Every routing failure in the previous hours came from corridors: a corridor is a long vertical run
that must cross the other four ports' rows, and each crossing is a chance to merge nets. Removing the
corridors removed the entire failure class. **Fewer waypoints means fewer failure modes.**

### ✅ `halt` must be declared but NOT appear in any net

A net whose only endpoint is an output `Pin` has **0 drivers**, so `validate()` reports
`net 'halt' has 0 drivers, expected exactly 1`. The correct pattern — used by the passing specs — is
to **declare the Pin and omit it from `nets` entirely**. No wire and no stub are needed; a stub net
would additionally emit zero wires.

### ✅ `WE = NOT(clk)` does write (so the propagation-order worry was unfounded)

`Ram.java` writes only when `triggered && WE == TRUE` within the same propagate call that first sees
the rising `CK` edge, which statically suggests an inverter settling *before* the RAM would kill the
write. Measured: it writes anyway — `WE` still holds its pre-edge value when the RAM samples. An
earlier variant that used a **second** clock instance for `inv.in` gave the identical `UUUU → 1010`,
so the second clock buys nothing.

⚠️ But note this is only evidence that the **edge is seen**, not that the **write succeeded** — the
`Dout`-follows-`Din` result above shows the stored value is not distinguishable from a pass-through.

### ✅ A fresh RAM's contents are UNKNOWN, not zero

The first observed `Dout` is `UUUU`, not `0000`, and becomes a defined value afterwards. Any future
golden sequence must start from `U`, not from `0`.

### ⚠️ RAM contents are NOT in the `.circ`

`MemContents` is kept **outside** the saved file (Logisim stores memory in a separate hex file).
Consequently:

* **memory cannot be preloaded declaratively** in a generated `.circ`;
* every RAM verification must first **write** through the write port, which needs `WE`/`OE`
  scheduled **in time** - and this toolkit still has no signal generator that can assert a signal,
  release it, and assert it again.

So RAM reads cannot be verified from a preloaded image, and RAM writes cannot yet be given a
controlled `WE`/`OE` sequence. Recorded as a **named gap**, not papered over: the ports are
measurably correct, but RAM **behaviour** is not verified.


## 26. ✅ NATURAL LANGUAGE → CIRCUIT, END TO END (and the spec-format trap it exposed)

The objective asks for a Skill that lets an AI design circuits from **natural language**, and that
path had never been exercised. It now has been. The only input was this paragraph:

> Build a 4-to-1 multiplexer. It has four 1-bit data inputs d0, d1, d2 and d3, and a 2-bit selector
> S. The single output Y must equal whichever data input S selects: Y = d0 when S = 0, d1 when S = 1,
> d2 when S = 2, d3 when S = 3.

| stage | outcome |
|---|---|
| design | "pick one of four data inputs by a 2-bit selector" = `Multiplexer select=2`, one component |
| generate | `examples\generated\mux4.circ` (7 parts, 6 nets) |
| golden model | a table lookup written from the **requirement**, independent of the component used |
| verify | ✅ **64/64** (4 data bits + 2 selector bits = 64 combinations, exhaustive) |
| negative control | ✅ exchanging `d1`/`d2` → **48 passed / 16 failed**, exactly the S=1 and S=2 rows where `d1 != d2` |

The control is what makes the 64/64 meaningful: on its own, a correct-looking table cannot exclude
"the selection is systematically misaligned but happens to agree".

### ⚠️ The first design attempt was WRONG: a `Splitter` MERGES nothing

The first draft tried to join two 1-bit selector pins into one 2-bit bus through a `Splitter`.
Measured ports show what a splitter actually is:

```
Splitter incoming=2 fanout=1 spacing=1 @(450,700):
   bus -> (450, 700)      f1 -> (470, 680)      f2 -> (470, 690)
```

It **splits**: one N-bit bus at `loc`, N 1-bit lines out at `(x+20, y-10-spacing*10*(fanout-i))`.
It is not a merge. The correct construction is **a single 2-bit input `Pin`**, which `--tty table`
enumerates as two adjacent columns.

### ⚠️⚠️ `rtllib` and `seqmodel` take DIFFERENT spec formats, and neither says so

| | `rtllib` (RTL specs) | `seqmodel` (sequential specs) |
|---|---|---|
| pins | **auto-created** from `inputs`/`outputs`; do NOT declare PIN parts | must be declared explicitly as PIN parts |
| `inputs` | `[["A", 4], ["Op", 2]]` - list of `[name, width]` | n/a |
| `nets` | `{name: {"drive": "ref:port", "use": [["ref","port"], ...]}}` | `{name: [["ref","port"], ...]}` |
| output pin use | the **string** `"out:Y"` inside `use` | the pair `["Y", null]` |
| constants | a `tie` map, e.g. `{"add:Cin": 0}` | `CONST` parts |

Feeding one format to the other surfaces as **unrelated-looking Python errors** -
`TypeError: list indices must be integers or slices, not str`, and `unknown part 'Y'` - not as
"this is the wrong spec format for this tool". Three attempts went on this. The formats were
reconciled only by reading the working `alu4.json` and copying its conventions exactly; that is the
reliable method, and it is why a new spec should start from the closest existing working example
rather than from a tool's `--help`.


## 27. ✅ RAM `WE` GENUINELY GATES STORAGE — and the constant-data tests could not have shown it

Round 16 concluded the RAM behaved as a **pass-through**, because `Dout` tracked `Din` exactly. That
conclusion was correct about the *observation* but wrong about the *circuit*, and this round found
the reason: **every experiment fed the RAM a CONSTANT data value**.

With the data constant, "the memory stored it" and "the output is a live reflection of the input" are
the **same observation**. No amount of re-running can separate them. The test needed
**time-varying data**, which the already-verified 4-bit accumulator can supply.

### The isolation experiments

Each variant changed ONE thing from the baseline (`clk -> CK`, `NOT(clk) -> WE`, `OE = 1`):

| variant | last `Dout` | what it shows |
|---|---|---|
| baseline (`WE = NOT(clk)`, `Din = 10`) | `1010` | — |
| **`WE` held at 0** | `0000` | writes are blocked |
| **`WE` held at 1** | `1010` | writes are permitted |
| `WE` left floating | `0000` | a floating `WE` does **not** enable writes |
| `OE` left floating | `1010` | `OE` floating still reads |

So **`WE` is correctly identified, and it really does gate the data** — the pass-through was never a
pin mix-up.

### The decisive experiment: accumulator-driven data

The accumulator from section 24 (`Register.Q -> Adder.A`, `Sum -> Register.D`, ramping
`0,1,2,...,15,0,...`) was wired to the RAM's `Din` instead of an output pin, with both running from
the same clock. Same circuit, one control bit different:

| `WE` | observed `Dout` stream |
|---|---|
| **1** | `UUUU`, `0000`, `1111` — the value **follows the changing data** |
| **0** | `UUUU`, `0000` — the value is **HELD** |

**That difference is the signature of real storage gated by a write-enable.** A transparent
pass-through cannot produce it: with `WE = 0` a pass-through would still echo `Din`, and it does not.
Stable over a 12-second run (~10^6 ticks).

So the round-16 conclusion is **corrected**: the RAM is **not** a pass-through. `WE` gates writes,
`OE` gates the read, and the stored value persists.

### The general lesson, which is the valuable part

> **A test in which the expected answer equals the input cannot distinguish storage from a wire.**

The earlier "pass-through" verdict and this round's "it stores" verdict came from the *same* built-in
component; only the **stimulus** differed. Three separate rounds were spent because the experiment
was mis-designed, not because the circuit was wrong. When testing memory - or any state - the
stimulus must **change over time**; a constant input makes the whole class of storage defects
invisible.

### Still open

* The write is confirmed to happen, but a **precise write/read schedule** (assert `WE`, release it,
  change the address, read back a *specific* word) still needs a real signal generator. `--tty table`
  cannot schedule a control pin, and RAM contents cannot be preloaded from a `.circ`.
* Consequently the **address-dependence** of a completed write is still unverified: rounds 16-17
  showed all addresses read the same value, and nothing here contradicts that.


## 28. ✅ A SIGNAL GENERATOR AT LAST — the divide-by-2 flip-flop (and the multi-drop routing rule)

The shared blocker for RAM scheduling, register load-then-clear and instruction sequencing was the
absence of a **signal generator**: something that produces a control level that changes on a known
schedule. It turns out to be buildable from components that already work.

### The primitive

A `D Flip-Flop` with **`D = NOT(Q)`** toggles on every clock edge, so its `Q` is a square wave at
**half the clock rate**:

```
clk -> dff.CK        dff.QB -> dff.D        (QB is NOT(Q), so D = NOT(Q))
observe dff.Q
```

**Measured: 731,442 rows toggling `0,1,0,1,...`** — a genuine free-running oscillator.
`examples\specs\seq\seq_div2.json` verifies it: **`status: passed`, 64 cycles, 32 periods, 0 mismatches.**

### Why a divided clock is the thing that was missing

`--tty table` prints a row only when an output **changes**, and samples once per clock. A control
signal derived **combinationally** from the clock — `NOT(clk)` — has the *same value at every sample*,
so it can never hold a state for an observable period. That is exactly why the round-11 register test
read a constant `0000`: `NOT(CLK)` was asserted at every sampling instant.

A **divided** clock is HIGH for a whole period and LOW for the next, so whatever it gates does one
thing on one edge and the opposite on the next. That is what makes a held state observable, and it is
the difference between "the harness samples at one phase" and "the circuit has its own timing".

### ⚠️ A single-line `waypoints` route can only reach the LAST endpoint

This defect invalidated two circuits this round before it was seen, and it is the same family as the
middle-endpoint drop fixed in round 11.

`validate()`/the serialiser build a net's geometry as:

```
[first endpoint] + [waypoints in order] + [last endpoint]
```

**Middle endpoints are not inserted.** So a net declaring three endpoints produces a *linear* path
that, at best, passes through one of the two non-final ones. Concretely:

```
"clk": [["clk",null], ["dff","CK"], ["reg","CLK"]],
"waypoints": {"clk": [[350,800],[1500,800],[1500,370],[1400,370]]}
```

emits a path ending at `reg.CLK` — and **`dff.CK` is never reached**. The divider therefore never
received a clock, `QB` stayed undefined, `CLR` stayed undefined, and the register simply loaded once
and held `1010`. The circuit looked plausible and the spec claimed a connection that did not exist.

> **Rule: a net may have only TWO real endpoints unless the waypoints explicitly route THROUGH every
> middle endpoint's own coordinate.** For a multi-drop signal such as a clock, either give each drop
> its own two-endpoint net, or build the waypoint list through each drop in turn.

Reordering the endpoints made the build fail loudly (`endpoint dff:CK is NOT on the routed path`),
which is the validator earning its keep; but the ordering used by the failing circuits had the
middle endpoint *silently* skipped.


### ⚠️ OPEN: the divider oscillates alone but NOT in the register testbench

Honest status, because the difference matters and is not yet explained.

| circuit | divider behaviour |
|---|---|
| `seq_div2.json` — bare divider, `Q` to an output pin | **731,442 rows**, toggling `0,1,0,1,…` — verified `passed` |
| `seq_divclk_reg.json` — same divider plus a Register whose `CLR` is driven from it | the register reads a **constant `0000`**; the divider does not appear to toggle |

Everything mechanical checks out in the second circuit: `validate()` reports **0 problems**, the width
auditor reports **OK**, and the computed paths genuinely reach every port -

```
dff.CK = (490,700)  reg.CLK = (1400,370)  inv.in = (770,700)
clk path = [(150,700),(350,800),(1500,800),(1500,370),(1400,370),(450,370),(450,700),(490,700)]
fb  path = [(550,700),(770,700),(770,850),(400,850),(400,660),(490,660)]
```

so `clk` does arrive at `dff.CK` and `QB` does drive both the inverter and `D`. The observed
`0000` implies `CLR` is held high, which implies the divider's `Q` is held high, which is what a
flip-flop with `D` stuck at 1 would do — but `D` is `QB`, and `QB` should be `NOT(Q)`.

Two candidate explanations remain, and they have NOT been separated:

1. **The shared `fb` net is a fan-out of one output to two destinations**, and the inverter is in the
   middle of that path rather than on a spur. Propagation order through a single net that both feeds
   back and feeds a gate may not settle the way the bare divider does.
2. Something about a `Register`'s `CLR` being driven by a divided level (rather than by a level that
   tracks the clock) changes when the register samples.

**Not claimed as working.** The bare divider IS verified; the register-with-divided-clock variant is
**not**, and it is left recorded rather than dressed up.

### ✅ The width auditor earned its place again

Running the auditor over every generated circuit caught a **real** defect that all the functional
checks had missed: `seq_divclk_reg.circ` reported

```
[W4] net 'net1': net has NO driver but feeds ['D Flip-Flop@(500, 650):D']
```

The divider's feedback (`QB -> D`) was **missing**, so the flip-flop never toggled and the whole
"divided clock" premise of that circuit was false. This is the same silent-failure family the auditor
was built for, and it is the second time it has found a mistake in a circuit I believed was correct.


### ✅ RESOLVED: the divider was never the problem — the observation was

Round 19 left it OPEN whether a shared clock net stopped the divider. **Decomposition settled it: the
divider is robust.** Starting from the verified `seq_div2` and adding one thing at a time:

| step | circuit | rows | toggling? |
|---|---|---|---|
| 0 | the verified divider, unchanged | 635,147 | **yes** |
| 1 | + a `Register` clocked by its **own** clock, `CLR` tied low | 568,854 | **yes** |
| 2 | + the inverter tap on the feedback net (`QB` fanning out to the inverter and `D`) | 626,995 | **yes** |
| 3 | + the inverter output driving `Register.CLR` | 540,807 | **yes** |

and in the full two-clock testbench, observing the **divider** gives **580,803 rows** of `0,1,0,1,…`.

So: extra parts do not disturb it, the `QB` fan-out does not disturb it, and driving a register's
`CLR` does not disturb it. **The round-19 hypothesis was wrong.**

### ⚠️ The actual reason the register reads `0000`: sampling phase

`CLR` is driven by an **inverted clock**. `--tty table` samples once per clock, and at the sampling
instant `NOT(clk)` is **high** — so the register is **cleared at every observation** and necessarily
reads a constant `0000`. This is **exactly the round-11 trap**, reproduced: a control signal derived
combinationally from the clock has the same value at every sample.

The register is very likely loading correctly between samples. **The measurement cannot see it**, and
that is a property of the instrument, not of the circuit.

### The structural constraint this exposes

A **single** clock cannot both drive a register and sequence that register's control inputs
observably:

* if a control is `NOT(clk)`, it is constant at every sample (this case);
* if a control is `clk`, it is likewise constant at every sample;
* if a control comes from a **divided** clock, it is constant for a whole period — but the register is
  then clocked on every edge, so it acts on **both** the high and low phases of its own control, and
  the two effects cancel into a constant output.

Making a load observable needs **both** a divided control **and** a divided register clock (i.e. two
different rates), or a data source that changes between edges. That is a genuine architectural limit
of driving everything from one `--tty table` clock, and it is why the register LOAD path is still only
verified in the special case of `seq_reg4_load.json` (constant data, `CLR` tied low, load once and
hold).

**Recorded as an open gap**, with the mechanism now understood rather than guessed.


## 29. ⚠️ THE `Comparator` DOES NOT COMPARE THE WAY ITS PORTS SAY — open defect

Found while building the project's first three-output circuit. **Not resolved**, and recorded rather
than worked around, because "the tool passes" must never be achieved by weakening the spec.

### The observation, with constants only (no table-order ambiguity)

A `Comparator` (width 2, `end[0]` = `A` at `(x-40,y-10)`, `end[1]` = `B` at `(x-40,y+10)`,
`end[2..4]` = `>`,`=`,`<` at `(x,y-10/y/y+10)`) with **A and B tied to constants**:

| A | B | measured | expected |
|---|---|---|---|
| 1 | 0 | **GT** | GT ✅ |
| 0 | 1 | **LT** | LT ✅ |
| 2 | 0 | **LT** | GT ❌ |
| 0 | 2 | **GT** | LT ❌ |
| 3 | 0 | **LT** | GT ❌ |
| 0 | 3 | **GT** | LT ❌ |

**Every case with a single bit set is correct; every case needing bit 1 is reversed.** The two values
that stay correct across all tests are exactly those with palindromic bit patterns, which is the
signature of a **bit-order** fault rather than a wrong-output fault.

### The 16-row table, enumerated

`A[2] B[2] GT EQ LT` matches the requirement on **8 of 16** rows; the 8 failures are precisely the
rows where bit 1 differs between A and B.

### What has been RULED OUT

* **Not the port geometry.** `SamplePorts` on the generated file returns exactly
  `end[0]=(560,390) w2`, `end[1]=(560,410) w2`, `end[2]=(600,390) w1`, `end[3]=(600,400) w1`,
  `end[4]=(600,410) w1` — matching `Comparator.java:70-74` (`IN0(-40,-10)`, `IN1(-40,10)`,
  `GT(0,-10)`, `EQ(0,0)`, `LT(0,10)`) and the tooltip labels `comparatorInputATip` /
  `comparatorInputBTip` / `comparatorGreaterTip` / `comparatorLessTip`.
* **Not the table's display order.** A 2-bit + 2-bit `Adder` reconciles as `A + B == S + 4*Cout` on
  **16/16** rows reading the first character as the HIGH bit, and only **9/16** the other way.
* **Not the decoder in `verify_model.py`.** Reversing the multi-bit input decode to make the
  comparator pass BREAKS working specs: `mux4` falls 64→48 (16 fails) and `alu4` falls 1024→369
  (655 fails). Reverting restores both to 100%, which is the cleanest evidence available that the
  existing decode is right and the Comparator is the outlier.
* **Not the wiring.** The emitted `.circ` connects the 2-bit constant `0x2` to the tunnel landing on
  `(560,390)` = `end[0]` = `A`, and `0x1` to `(560,410)` = `end[1]` = `B`.

### Why this matters beyond one component

The circuit is the first here with **three outputs**, and the first whose correctness depends on a
**multi-bit input value** rather than on which single bit is set. Every earlier multi-bit design
(`mux4`, `alu4`) is insensitive to a reversed bit order or to a reversed selector: `mux4`'s four data
inputs are all 1 bit, so a reversed 2-bit selector merely **permutes** which selector value picks
which input, and all 64 rows still pass. A comparator is not symmetric in that way, which is why it
caught what five passing specs could not.

**Lesson: a circuit whose function is SYMMETRIC under a plausible wiring error cannot detect that
error.** Test coverage that adds only symmetric designs adds no power against this class.

### A real bug this DID find: the pass/fail accounting in `verify_model.py`

The first run reported `passed: 8, failed: 16` out of **16 rows** — arithmetically impossible.
`passed` counted **rows** while `failed` counted **output mismatches**, so with a single output the
two coincided and every earlier spec looked right. Now both count rows, with the raw output-mismatch
count reported separately as `output_mismatches`, and `passed + failed == rows` is asserted by a test.


### 29.4 Round 22: the contradiction is REAL and reproducible — and it is NOT in the specs

Further probing narrowed the Comparator defect to a **disagreement about how a multi-bit rendered
field maps to physical input bits**, and produced two facts that cannot both be read the same way.

**Fact A — the 2-bit data path is faithful.** A 2-bit input pin wired straight to a 2-bit output pin,
and the same through an `Adder (+0)`, both give the full identity:

```
A[2] Y[2]        A[2] Y[2]  (through Adder +0)
00   00          00   00
01   01          01   01
10   10          10   10
11   11          11   11
```

So no bit is dropped anywhere: the pin carries, and a component receives, whatever the table shows.
**This rules out "bit 1 never arrives".**

**Fact B — the enumeration order is a known odometer.** `TruthTable.isInputSet` is

```java
return (idx & (1 << (inputs - col - 1))) != 0;
```

so column `col` is set by bit `inputs-col-1` of the row index: the LAST column is toggled by the
lowest index bit. With two inputs, row 1 sets column 1 and row 2 sets column 0.

**Fact C — the display is MSB-first.** `TtyInterface.valueFormat` renders via `v.toBinaryString()`.

**Facts B and C together** say: with two input pins, row 1 renders column 1 (the high bit) as its
`1`, i.e. `01`; row 2 renders column 0 (the low bit), i.e. `10`.

**Fact D — the Comparator disagrees.** Driven by a 2-bit input pin against `B = 0`:

| printed A | circuit says |
|---|---|
| `00` | EQ (A = 0) |
| `01` | **GT** (A > 0) |
| `10` | **LT** (A < 0) |
| `11` | **LT** (A < 0) |

`GT` on the row printed `01` is consistent with B+C. `LT` on `10` and `11` is **not** consistent with
any unsigned reading of those patterns: an unsigned 2-bit value is never less than 0.

**Fact E — the two independent probes agree with each other.** The comparator with tied constants gave
`A=1,B=0 -> GT` and `A=2,B=0 -> LT`, matching Fact D exactly.

### The honest position

* The **specs that pass stay passing** with the current MSB-first decode, and it was verified that
  switching the decode to LSB-first BREAKS them (`mux4` 64→48, `alu4` 1024→369 with 655 output
  mismatches). So the decode is not naively wrong.
* Yet the Comparator measurement in Fact D is reproducible and cannot be explained by any single
  consistent reading of Facts B, C and E.
* I could **not** resolve it in this round. Two later probe designs failed for unrelated reasons (a
  `Write` gate producing `EE` on a 2-bit net when a 1-bit pin fed a 2-bit `Adder` port), so the
  isolating experiment is still outstanding.

**Therefore: the Comparator remains an OPEN DEFECT and the multi-bit input interpretation remains
UNRESOLVED.** No spec was adjusted to make the comparator pass; a passing number must never be bought
by weakening the model. Both are recorded here so the next attempt starts from Facts A-E instead of
from scratch.


### 29.5 Round 23: the JVM-API route HANGS — a tooling limit, recorded so it is not retried blindly

To settle the Comparator question without depending on any printed table, I wrote a Java tool that
drives a circuit through **Logisim's own simulator API**: build a `Value` from an integer, hand it to
`Pin.FACTORY.driveInputPin`, propagate, and read each output port's `Value` back as an integer. That
would have been decisive, because no rendering or tokenising is involved.

**It hangs, reproducibly, in two separate implementations.**

| attempt | outcome |
|---|---|
| `Start-Job` + `Receive-Job` capture, hard `Wait-Job -Timeout 40` | TIMEOUT, no output at all |
| `Start-Process -RedirectStandardOutput`, `WaitForExit(45000)` | exited immediately with `ClassNotFoundException: circ` — `-ArgumentList` split the classpath on `;` |
| `cmd /c <batch>` with the classpath quoted inside the batch | hung past 240 s |

Notes that matter for anyone retrying this:

* `new CircuitState(proj, circ)` no longer exists in 5.1.0dev. The working constructor is
  `CircuitState.createRootState(proj, circuit, Thread.currentThread())` — the same call
  `TtyInterface.java:349` uses. **This compiled cleanly**, so the code itself was at least
  structurally right; the hang is at runtime, most likely in `Propagator.propagate()` being driven
  outside the CLI's own simulation loop.
* `Start-Process -ArgumentList` splitting a `;` classpath is the **same trap already recorded in
  section 10.2** (it splits on spaces there; here it splits the classpath). It is worth stating again
  because it produced a *misleading* error (`ClassNotFoundException: circ`) that looks like a code
  fault rather than an argument-passing fault.
* The tool was **removed** rather than left in `scripts/analysis/`, because a hanging tool with no
  timeout is worse than no tool: the project's own rule is a hard timeout on every JVM call.

**Consequence:** the isolating experiment for the Comparator is *still* outstanding, and the
JVM-API route is now known to be blocked by something in `propagate()` that a future round must
diagnose (most likely: propagating a circuit whose input pins have not all been driven, or driving
the propagator without the CLI's event loop).


### 29.6 Round 25: the `Comparator` fault is reproduced cleanly, and the ambiguity is now exact

Round 21's result was obtained through a parser, so it needed re-confirming without one. The cleanest
possible circuit — two `Constant` parts, one `Comparator`, three observed outputs, **no input pins at
all** — reproduces it:

| A | B | table | verdict | expected |
|---|---|---|---|---|
| 1 | 0 | `1 0 0` | GT | GT ✅ |
| 2 | 0 | `0 0 1` | **LT** | GT ❌ |
| 3 | 0 | `0 0 1` | **LT** | GT ❌ |
| 0 | 1 | `0 0 1` | LT | LT ✅ |
| 0 | 2 | `1 0 0` | **GT** | LT ❌ |
| 2 | 1 | `0 0 1` | **LT** | GT ❌ |
| 1 | 2 | `1 0 0` | **GT** | LT ❌ |
| 2 | 2 | `0 1 0` | EQ | EQ ✅ |

**3/8 correct. Every failure involves bit 1; every single-bit case is right.**

### All three data paths are individually verified canonical

| path | test | result |
|---|---|---|
| `Constant` → output pin | constant 1/2/3 read back | `01` / `10` / `11` — MSB-first ✅ |
| input pin → output pin | 2-bit identity | `00/01/10/11` ✅ |
| input pin → `Adder` | `A + B(=2) + Cin(=1)`, 4 rows | reconciles on **4/4** rows reading the first character as the HIGH bit, 2/4 the other way ✅ |

So the constant path, the pin path and the display all agree with each other — **only the Comparator
disagrees with all three.**

### The ambiguity is now exact, and it is why the harness alone cannot settle this

The 8-row table above is fitted **exactly** by two different statements, which are mirror images:

1. the Comparator mis-handles bit 1 (`GT ⟺ ¬(A>B) XOR (A≠B)`), or
2. the display is LSB-first, in which case the Comparator is numerically perfect.

Both fit every row. And the adder test cannot break the tie either: with MSB-first it fits 4/4, and
with LSB-first the model and the decode reverse *together*, so it stays self-consistent. **A
self-consistent instrument cannot detect its own consistent reversal.**

### The isolating experiment that would settle it

Only an **external** reference to bit significance can break the tie — something whose interpretation
of a multi-bit integer is fixed outside this project. The two candidates:

* the **JVM-API route** (drive a `Value` built from an integer, read back an integer). Attempted in
  round 23 and **blocked**: `Propagator.propagate()` hangs outside the CLI's own simulation loop. That
  is now the gating problem, and it is a *tooling* problem, not a circuit problem.
* a **component whose behaviour is defined on the numeric value** and whose result is 1 bit, so no
  multi-bit rendering is involved. The `Comparator` was that attempt and it is the thing under test.

**Position: the Comparator stays an OPEN DEFECT and the absolute bit order stays UNRESOLVED.** No
spec was bent to make it pass. Every passing spec uses the current decode and is unaffected, because
none of them distinguishes the two readings (`mux4`'s data inputs are all 1 bit; `alu4`'s model and
its decode share a convention).


## 30. ✅ RESOLVED: the `Comparator` "defect" was MY missing attribute — `mode` defaults to SIGNED

Round 21-25 recorded the `Comparator` as an open defect. It was not a Logisim defect at all. The
component was right; **my generator was wrong.**

### The root cause

`Comparator.java:63-64`:

```java
new Attribute[] {StdAttr.WIDTH, MODE_ATTR},
new Object[]    {BitWidth.create(8), SIGNED_OPTION}
```

and `propagate` (lines 130-140):

```java
if (pos == ax.length - 1 && ab != bb) {
  Object mode = state.getAttributeValue(MODE_ATTR);
  if (mode != UNSIGNED_OPTION) { Value t = ab; ab = bb; bb = t; }   // swap the SIGN BITS
}
```

So the default mode is **`twosComplement`**: a signed comparison. `rtllib` emitted only
`<a name="width" .../>` and never `mode`, so every comparator this project built was compared as
**signed**. At width 2 that makes `10` equal to **-2** and `11` equal to **-1**, which is why
`A=2, B=0` reported `LT` and why only bit 0 ever behaved as expected.

**Signed comparison predicts all 10 measured rows exactly** — that is what finally identified it,
after four rounds of looking for a fault in the wrong places.

### The fix

`rtllib` now **always** emits `mode`, defaulting to `unsigned` (a spec that says "magnitude
comparator" means unsigned unless it says otherwise, and a silent signed default is precisely the
trap). `mode` may be set per part.

```
<comp lib="3" loc="(600,400)" name="Comparator"><a name="width" val="2"/><a name="mode" val="unsigned"/></comp>
```

`examples\specs\rtl\cmp2.json` — a 2-bit magnitude comparator specified only in words — now passes
**16/16** exhaustively, with a negative control that exchanges A and B and **fails 12 of 16**.

### ⚠️⚠️ WHY THIS TOOK FIVE ROUNDS: I misdiagnosed a non-exiting JVM as a hang

Round 23 concluded the JVM-API route "hangs" and abandoned it. **That was wrong**, and it cost the
investigation its decisive instrument for two rounds. `ApiTrace` — which prints a flushed checkpoint
after every step — showed:

```
STEP 0 start, args=1
STEP 1 file exists=true
...
STEP 12 calling propagate()
STEP 13 propagate() RETURNED
STEP 14 done
```

**Every checkpoint printed, including the last.** The work completed. The process simply never
**exited**, because Logisim leaves non-daemon threads running, so my hard timeout killed it and the
caller saw "timeout" with no output captured. One line — `System.exit(0)` — was all that was missing.

Once fixed, `ApiDrive` settled the question in a single run: with input pins driven from **integers**
and outputs read back as **integers**, no table, no rendering, no tokenising, the results matched the
CLI table exactly, proving the fault was in the circuit rather than in how I read the output.

**Lessons, both general:**

1. **"It hangs" is a hypothesis, not an observation.** A program that finishes its work but never
   exits is indistinguishable from a hang *only if you never see its output*. Print a flushed
   checkpoint after every step before concluding anything about where it stops.
2. **Do not abandon the strongest instrument on the strength of one failure.** The API route was the
   only way to bypass the rendering layer that the whole ambiguity lived in, and two rounds were
   spent on weaker methods after discarding it.

### This is the fourth silent-default defect in the same family

| component | omitted attribute | silent default |
|---|---|---|
| `Tunnel` | `width` | **1 bit** |
| `Constant` | `value` | **TRUE** |
| `Adder` | `width` | **8 bits** |
| **`Comparator`** | **`mode`** | **signed (two's complement)** |

Every one of them produced a circuit that loads, prints a full table, and is wrong. The standing rule
is now explicit: **for every component with a mode/width/value attribute, emit it — or verify the
default by measurement before relying on it.**


## 31. Systematic attribute-default audit — the Comparator trap, generalised

The Comparator defect (§30) was found by accident, after five rounds. It should have been found by
construction, because it belongs to a **class**: a component attribute whose default silently changes
behaviour, omitted by the generator, producing a circuit that loads, prints a complete table, and is
wrong.

`scripts\analysis\audit_attr_defaults.py` lists every attribute this project **depends on**, the
default the component itself declares, the source line that says so, and whether the builders write
it. Anything marked `RELY` is a place where an upstream default change would silently alter every
circuit generated.

### Result

| component | attribute | default | written? |
|---|---|---|---|
| `Adder` | `width` | **8** | ✅ written |
| `Comparator` | `mode` | **SIGNED** | ✅ written (this was the real defect) |
| `Constant` | `value` | **TRUE** | ✅ written |
| `Tunnel` | `width` | **1 bit** | ✅ written |
| `Multiplexer` | `select` | — | ✅ written |
| `Splitter` | `spacing` | — | ✅ written |
| `RAM` | `addrWidth`/`dataWidth` | — | ✅ written |
| `Pin` | `behavior` | `SIMPLE` | ⚪ relied on (safe) |
| `Register` | `trigger` | `rising` | ⚠️ **was relied on → now written** |
| `D`/`T`/`JK`/`SR` Flip-Flop | `trigger` | `rising` | ⚠️ **was relied on → now written** |

### Checked and safe, with the reason

* `Pin.behavior = SIMPLE` (`Pin.java:729-731`) — input pins are driven by the enumerated table, and
  `driveInputPin` writes `intendedValue` directly, so no pull-up/down is involved.
* gate `ATTR_OUTPUT = "01"` (`GateAttributes.java:49`) — the ordinary totem-pole output. `0Z` and
  `Z1` are tri-state / open-collector forms and are not used anywhere here.

### The one real action taken

`trigger` was **relied on** across every clocked part. All six verified sequential specs pass, which
**proves** the default is `rising` today — but that is a measurement of the current build, not a
guarantee, and an upstream default change would silently redefine the clock edge of every sequential
circuit this project generates.

`seqmodel` now emits `trigger` explicitly for `Register` and all four flip-flop kinds, with a
per-part override (`"trigger": "falling"`). `Part` gained a `trigger: str = "rising"` field. All six
verified sequential specs were **rebuilt and re-verified** afterwards and still pass:

```
seq_tff 32 periods · seq_ripple2 16 · seq_reg8_hold 1 · seq_reg4_load 2 · seq_accum4 4 · seq_div2 32
```

### Two mistakes made while doing this, both caught immediately

1. **First attempt used `p.attrs.get("trigger", ...)`.** `Part` is a dataclass with no `attrs` field,
   so `to_xml` raised `AttributeError` for **every** build. Caught because the rebuild loop checked
   for `Traceback` rather than trusting the exit code, and because the emitted `.circ` was inspected
   rather than assumed.
2. **The first `edit` call had its arguments inverted**, so the replacement re-inserted the bad line
   instead of removing it. Caught by reading the region back before rebuilding.

**Lesson, again the same one: verify the emitted artifact, not the tool's report.** The build loop
would have shown six green "verified" lines if it had been driven by exit codes alone.


## 32. Subcircuits: encoding CONFIRMED, port placement MEASURED (unusual), output side STILL OPEN

Every circuit this project generates is a single flat `<circuit>`. A CPU needs modules — a 1-bit full
adder instantiated four times, an ALU once per bit — so hierarchy is the hard prerequisite for
anything "complex" in the objective's sense. This round established the format by hand-written XML
rather than through a generator, so a generator bug could not be mistaken for a format limit.

### CONFIRMED: the subcircuit instance encoding

```xml
<comp loc="(500,200)" name="PASS"/>
```

**No `lib` attribute.** `XmlWriter.fromComponent` omits `lib` when the component's library *is* the
file itself, and `SubcircuitFactory.getName()` returns `source.getName()` — the circuit name. So the
target circuit is named by the `name` attribute and nothing else.

`stats` proves the instance is real and not merely tolerated:

```
1	1	PASS	h3_76885            <- the subcircuit itself
4	4	Pin 	Wiring
4	4	TOTAL (without project's sub circuits)
5	5	TOTAL (with sub circuits)   <- the instance resolves
```

### RULED OUT: the `desc=` form is a LOAD FAILURE, not an alternative

`<comp desc="PASS" loc="(500,200)"/>` fails to load at all
(`XmlReader.readLibrary` → `Loader.showError`). Use `name=`.

### MEASURED: the subcircuit's ports sit at the PIN'S OWN COORDINATES, ignoring the instance

Decisive test — subcircuit `PASS` has its input pin at `(200,200)` and output at `(600,200)`; the
instance is placed at `(1000,1000)`; input pin `A` is wired ONLY to `(200,200)`:

```
A P0 P2 P1 P3
0  0  U  U  U
1  1  U  U  U
```

`P0` is at `(200,200)` and **follows A**. `P1` is at the instance's location plus the pin coordinate,
`(1200,1200)`, and reads **U**.

**So a subcircuit's port locations are the subcircuit's own pin coordinates, in the parent sheet's
absolute space, with the instance position NOT added.** This is the opposite of every other component
in the library, where ports are offsets from `loc`, and it is why the first attempt at hierarchy read
`U` everywhere: the instance was placed at `(500,200)` while its ports were at `(200,200)` and
`(600,200)`, so nothing was connected to anything.

A consequence worth stating: **two instances of the same subcircuit would have colliding ports**, so a
usable generator must translate the subcircuit's pins per instance, or place every instance at the
origin. That is a design constraint on the generator, not a Logisim limitation.

### STILL OPEN: the subcircuit's OUTPUT port did not drive anything

With the input side confirmed working, the output port at `(600,200)` read `U` in every variant tried
(output pin placed on the port, adjacent to it, and with the instance at the origin). Whether the
subcircuit's internal wire is being dropped, whether an output `Pin` inside a subcircuit needs
something else, or whether the output port needs the parent side to drive it, is **not determined**.
Recorded as an open question for the next attempt rather than guessed at.

### Two tooling hazards found while probing

1. **`OutOfMemoryError` inside `WireRepair.doOverlaps`** (`WireRepair.java:170`), reached from
   `XmlReader.toLogisimFile`. Two wires sharing both an endpoint AND a direction make the overlap
   repair blow the heap instead of reporting a malformed file. A control circuit written with
   `wire from=(200,200) to=(450,180)` and `wire from=(200,200) to=(450,220)` did this — the classic
   "two wires from one point" fan-out that looks obviously harmless. **Orthogonal, non-overlapping
   stubs only**, which is the rule `seqmodel`'s validator already enforces.
2. **`HeadlessException` inside `Loader.showError` MASKS every load failure.** All three failures this
   round surfaced as a bare `HeadlessException` with no indication of the real problem, because the
   loader reports errors by opening a Swing dialog and a headless JVM throws first. `stats` was no
   better. **A malformed `.circ` therefore looks identical to a hang or an empty result.** Diagnostic
   tool `scripts\analysis\LoadDiag.java` was written to print the real exception; it reports
   `OPEN THREW: java.awt.HeadlessException` and the stack still points at the true origin
   (`XmlReader.readLibrary`), which is what identified `desc=` as the culprit.


## 33. ⚠️⚠️ `facing` names the side the WIRE attaches to — the glyph is drawn OPPOSITE

Found from user feedback on the delivered 4-bit adder: *"all the Tunnels on gate inputs face West,
which makes them overlap other components; on the output side the components are too close and also
overlap; the output-side input tunnels are West too and overlap."* All three were real, and all three
came from one misunderstanding of `facing`.

### MEASURED geometry (`SamplePorts`, all at loc = (400,200))

| attribute | reported box | glyph drawn |
|---|---|---|
| `facing` unset | `(400,189,32,22)` → x ∈ [400,432] | **to the RIGHT** |
| `facing="west"` | `(400,189,32,22)` → x ∈ [400,432] | **to the RIGHT** |
| `facing="east"` | `(368,189,33,22)` → x ∈ [368,401] | **to the LEFT** |
| `facing="north"` | `(385,200,30,24)` → y ∈ [200,224] | downward |
| `facing="south"` | `(385,176,30,25)` → y ∈ [176,201] | upward |

**`facing="west"` draws the glyph to the RIGHT. `facing="east"` draws it to the LEFT.** The name
describes the side the **wire** attaches to; the body goes on the opposite side. Unset behaves as
`west`.

Label length drives the width: `"A"` → 18 px, `"A0"` → 24 px, `"nt_a0_1_carry"` (13 chars) → **112 px**.

### The bug this caused

`circmodel.py` put `facing="west"` on **every sink Tunnel** — the ones sitting exactly on a gate's
input port, with the gate on their RIGHT. Since `west` draws rightwards, the glyph ran straight into
the gate body:

```
XOR Gate@(300,200)  box (240,175,60,50)      x ∈ [240,300]
Tunnel(A0)@(240,180) box (240,169,24,22)     x ∈ [240,264]     ← inside the gate
```

Fix: a sink has its component on the RIGHT, so the body must extend LEFT → **`facing="east"`**.
A driver has its source on the LEFT, so the body extends RIGHT → **`facing="west"`** (now explicit
rather than relying on the default).

### The pin-pitch half of the report

Output pins were 20 px apart and the audit did not flag them, because a `Pin` box is exactly 20 px
tall so the boxes only *touch*: `S0@200` box y ∈ [190,210], `S1@220` box y ∈ [210,230]. But a
`Tunnel` box is **22 px** tall, and each output pin has a co-located Tunnel, so those overlapped by
2 px AND the labels sat on top of each other. 20 px was never enough; the generator's nudge step was
hard-coded `y += 20`.

Fix: `PIN_PITCH = 40`, which clears a 22 px box with room for a label and matches the spacing the
user's own samples use. Output pins moved `y = 200,220,240,260,280` → `200,240,280,320,360`.

### Result, measured with the real-geometry auditor

| | before | after |
|---|---|---|
| total findings | 70 | **25** |
| `L1` box overlap | **40** | **0** |
| `L2` box contains | **5** | **0** |
| `L6` same loc | 5 | 5 (Pin+Tunnel — standard idiom, and now non-overlapping) |
| `L3` wire-in-tunnel-box | 20 | 20 (auditor false positive, see §32) |

Electrically unchanged: the regenerated 4-bit adder still passes **512/512**.

### ⚠️ A METHODOLOGICAL MISTAKE THIS EXPOSED IN MY OWN AUDIT REPORT

In §32 I classified those 50 Tunnel-on-component overlaps as **"design pattern, not a defect"** and
excluded them from the defect count. That was wrong for the question the user was asking. The
classification was right that the pattern is *electrically deliberate* — a Tunnel on a port is this
project's driving mechanism — but the user's complaint is about **the drawing**, and a glyph sitting
on top of a gate is visible breakage no matter how intentional the connection is.

**Dismissing a finding as "intentional" answers "is this a bug?" when the user asked "does this look
broken?"** An auditor must report both: an intentional pattern can still be a layout defect. The
classifier now keeps Tunnel overlaps in the reported count with the reason they occur, instead of
zeroing them.


## 34. Two more layout rules the 4-bit subtractor forced into being

The subtractor is the second exhaustively verified datapath (512/512) and the first spec that is
**generated by a script** (`scripts\py\`) rather than typed, so the ripple chain is
correct by construction. Building it exposed two generator defects that the SamplePorts layout
auditor had **missed**, both of which only the new geometry checks caught.

### 34.1 INPUT pins could sit 20 px apart — `y in taken` is not a minimum separation

`place()` assigned each input pin a `y` taken from the first gate input it feeds, then ensured
uniqueness with:

```python
while y in taken:      # exact equality only
    y += PIN_PITCH
```

In a subtractor `A0` feeds a **2-input XOR** (input port at `loc.y - 20`) while `B0` feeds a
**1-input NOT** (port at `loc.y`), so the two pins landed at **180 and 200**. A pin's driver Tunnel
sits at `(pin.x + PIN_STUB, pin.y)` and a Tunnel box is **22 px tall**, so those two glyphs overlapped.

The real auditor did not flag it because the overlap was only **2 px**, under its 3 px rounding
tolerance. Measured before/after:

```
before   input y = [180, 200, 320, 420, 440, ...]   gaps include 20
after    input y = [180, 240, 320, 420, 480, ...]   gaps 60, 80, 100, ...
```

Fix: both pin columns now enforce `abs(y - t) < PIN_PITCH` against every already-placed pin, on the
input side AND the output side. **Uniqueness was never the requirement; separation was.**

### 34.2 A long NET NAME makes a wide Tunnel box on BOTH ends of a net

A Tunnel's box is `8*len(label) + 10` px wide. `validate()` now computes the box for the driver
(which extends RIGHT) and for every sink (which extends LEFT) and rejects an overlap over 3 px.

A 12-character net name (`final_carry`) is ~96 px per side; with the driver at `source + 32` and the
sink one column over, the two boxes overlapped by **13 px**. Renaming it to `cout` fixed it, and all
internal net names in the subtractor are now <= 4 characters.

**This is a permanent constraint on spec authoring**: internal net names must stay short. The check
reports the exact box coordinates so the offending name is identifiable.

### 34.3 The subtractor itself, and its three controls

`D = A - B - Bin (mod 16)`, `Bout = 1` when the true difference is negative. Built as a ripple-carry
adder over `A_i` and `NOT(B_i)` with stage-0 carry-in `NOT(Bin)`, since
`A + (15-B) + (1-Bin) == A - B - Bin + 16`.

| circuit | status | rows | passed | failed | output mismatches |
|---|---|---|---|---|---|
| `sub4` | **passed** | 512 | **512** | 0 | 0 |
| control `no-bin-invert` | failed | 512 | 0 | 512 | 992 |
| control `swap-b01` | failed | 512 | **256** | 256 | 496 |
| control `no-bout-invert` | failed | 512 | 0 | 512 | **512** |

The controls are chosen to fail at **different granularities**, which is what makes the pass
meaningful:

* `swap-b01` crosses B0 and B1, so it is correct exactly when `B0 == B1` - **precisely 256 of 512
  rows fail**, which demonstrates the model discriminates *per row* rather than merely noticing
  something gross;
* `no-bout-invert` drops the final NOT, so only `Bout` is wrong - **exactly 512 output mismatches
  over 512 rows = one wrong output per row**, isolating a *single output*;
* `no-bin-invert` is a gross control, wrong on every row.

### 34.4 A help flag that destroyed data

` --help` fell through to the "no positional argument" branch and **overwrote the
committed spec** `specs/comb/sub4.json`. The self-audit runs `--help` on every script in
`scripts/py`, so this would have silently replaced the spec on every audit run. `--help` is now
handled explicitly. **A help flag must never have a side effect.**

## 35. Input pins now follow the DECLARED order — and why that is safe

User feedback: the input pins of the delivered subtractor were in no sensible order. Measured header:

```
before:  A0 B0 B1 A1 B2 B3 A2 Bin A3      <- scrambled
after:   A0 A1 A2 A3 B0 B1 B2 B3 Bin      <- the order the spec declares
```

### Root cause

`place()` set each input pin's `y` to the y of the **first gate input it feeds**, so the driver Tunnel
and the sink Tunnel lay on one horizontal line. That is tidy per net, but the pins inherit the gate
rows, and gates are laid out by dependency depth. In a subtractor `A0` feeds a 2-input XOR at
`col 0 / row 0` while `B0` feeds a NOT gate, so the pins came out in depth order rather than in any
order a reader recognises.

### Why reordering cannot break the circuit

**Tunnels connect by LABEL, not by position.** A driver Tunnel at `(pin.x + PIN_STUB, pin.y)` and a
sink Tunnel sitting on a gate input port hundreds of pixels away are the same net purely because they
share a label. There is no routing wire between them.

So a pin's `y` is **cosmetic**, and it can be chosen for readability. `place()` now assigns

```
pin.loc = (120, y0 + i * PIN_PITCH)
```

for `i` in the declared order, which also makes the column evenly spaced by construction.

**This is a claim that must be PROVED, not assumed**, so the circuit was re-verified afterwards:
`sub4` still passes **512/512**, and the whole combinational suite was regenerated and re-verified
(`adder2` 32/32, `adder4` 512/512, `fulladder` 8/8, `majority3` 8/8, `mux2` 8/8, `sub4` 512/512).

### `rtllib` did not need the change

It already places pins explicitly in spec order, so its headers were already readable:
`A[4] B[4] Op[2] Y[4]`, `d0 d1 d2 d3 S[2] Y`, `A[2] B[2] GT EQ LT`. Only `circmodel` derived pin
positions from the destination gate.

### The trade-off, stated

Pins no longer sit on their destination gate's row, so the driver and sink Tunnels are at different
`y`. Nothing electrical depends on that, but a reader who expected the horizontal-alignment style will
see it change. The declared order was chosen because two of the project's own specs (adders,
subtractors) have many same-prefixed pins where alignment produced an unreadable column.

## 36. 六库组件属性与几何的实测知识库（第一阶段→第二阶段）

### 36.1 测量方法（全部来自真实 jar，无手写）

| 工具 | 作用 | 产出 |
|---|---|---|
| `tests\circ\probe\AttrDump.java` | 遍历六库每个 `AddTool`，dump 全部属性（XML 键名/显示名/实现类/类型/默认值/合法值/边界）+ 默认端口与包围盒 | `tests\samples_work\attrdump.txt` |
| `tests\circ\probe\GeomSweep.java` | 对每个属性取**每个合法值**、并对 `facing` 取**四个朝向**，dump 端口几何 | `tests\samples_work\geomsweep.txt` |
| `scripts\analysis\build_component_knowledge.py` | 解析两份 dump 为机器可检索知识库 | `references\components.json` |

**规模**：74 个组件、**322 条属性**、**1,402 次几何扫掠**、六库计数与产物 `getTools()` 完全一致、**0 缺口**、0 个组件无端口。

合法值用**反射**从 jar 交给的对象上读出（`OptionAttribute.vals`、`IntegerRangeAttribute.start/end`、`BitWidth` 的 `min/max/choices`），因为 Logisim 没有公开访问器。反射**打印字段名**，所以未识别的形状是**可见的缺失**而非静默遗漏。

### 36.2 重大发现：`#FPArithmetic` 全库（此前完全未知）

18 个组件**共享** `fpwidth`（显示 "Float size"），合法值 **[8, 16, 32, 64]**，**默认 32**。差异仅在附加属性：

| 组件 | 附加属性 | 合法值 |
|---|---|---|
| `FPConstant` | `value` | — |
| `FPExponentiator` | `mode` | `arb`(Arbitrary base) / `exp` / `expm1` |
| `FPLogarithm` | `mode` | `logxy` / `log` / `log10` / `log1p` |
| `FPMultiplier` | `multiplyMode` | `multiply` / **`fusedMultiplyAdd`** |
| `FPRound` | `mode` | `ceil` / `floor` / `round` / `rint` / `truncate` |
| `FPTrigonometry` | `type` | `trig` / `arc` / `hyp` |
| `FPToFP` | `fpwidthin` / `fpwidthout` | (两个独立位宽) |
| `FPToInt` | `width` 1..64, `mode` | `twosComplement` / `unsigned` |
| `IntToFP` | `width` 1..64, `mode` | `twosComplement` / `unsigned` |

`FPAdder` 端口实测：`A(-40,-10) B(-40,+10) 输出(0,0)` 宽 32，**外加一个 1 位错误/状态输出 `(-20,+20)`**——这是本项目此前完全不知道的第五个端口。

⚠️ **仍未测量**：8/16/32/64 位分别对应哪种浮点格式、特殊值（NaN/Inf/零/非规格化）行为。**不得假设遵循 IEEE-754 全部规则。**

### 36.3 重大发现：全部 Arithmetic、Memory、FP 组件**没有 `facing`**

实测：`Arithmetic` 13/13、`Memory` 11/11、`FPArithmetic` 18/18 **均无 `facing` 属性**——**这些组件不可旋转**，端口几何是固定的。
只有 `Wiring` 的 12/14 与 `Gates` 的 13/13 有 `facing`。

### 36.4 门组件比已知的丰富得多

`AND Gate` 实测 **9 个属性**，其中三个此前完全不知道：

| 属性 | 合法值 | 含义 |
|---|---|---|
| `out` | `01`(0/1) / `0Z`(0/floating) / `Z1`(floating/1) | **输出三态行为** |
| `negate0` / `negate1` | 布尔 | **逐输入取反气泡**（`NegateAttribute`） |
| `inputs` | 2..64 | 输入脚数量 |
| `size` | `30`(Narrow) / `50`(Medium) / `70`(Wide) | 门形尺寸 → 改变端口偏移 |
| `width` | 1..64 | 数据位宽 |

`XOR`/`XNOR` 有 10 个属性。

### 36.5 `Splitter` 的属性集是**动态**的

`fanout`(1..64)、`incoming`(1..64)、`spacing`(1..9)、`appear`(left/right/center/legacy)，
以及**随 `fanout` 增删的 `bit0`…`bitN`**（`BitOutAttribute`）。

第一次扫掠时改 `fanout` 抛 `ConcurrentModificationException` —— 因为**属性列表在遍历中被修改**。这证明属性之间存在**结构性依赖**，不只是取值依赖。

### 36.6 `RAM` 的完整属性（13 个）

`addrWidth` 2..24(默认 8)、`dataWidth` 1..64(默认 8)、
`enables`(byte/line)、`type`(volatile/nonvolatile)、`clearpin`(bool)、`trigger`(4 值)、
`asyncread`(bool)、`readbehav`(**war**/raw)、
`databus`(**bidir**/bibus/bibusControlled)、`label`/`labelfont`/`labelvisible`/`appearance`。

默认 `bounds` = **240×250**（默认尺寸很大）。端口：`(0,10)w8 (240,90)w8O (0,90)w8 (0,60)w1 (0,50)w1 (0,70)w1`。

### 36.7 ⚠️⚠️ 连接规则：Pin 与 Tunnel 之间**必须有 wire**

用自动测试台（`scripts\py\mkbench.py`）时踩到的三个坑，全部实测确认：

| 接法 | 结果 | 结论 |
|---|---|---|
| 输入 Pin 与 Tunnel 相距 40px、**无 wire** | 表全为 **`U`**（未驱动） | 必须加 `<wire from="Pin" to="Tunnel"/>` |
| 输出 `Pin` **直接放在**元件输出端口上 | 表全为 **`E`**（争用） | 端口上不能直接放 Pin |
| 输出 `Tunnel` **直接放在**元件输出端口上 | 表全为 **`U`** | 必须先从端口引出 wire |
| 输出 Pin + Tunnel **共位**、驱动端经 wire 接 Tunnel | ✅ **正确** | 与已验证的 `sub4.circ` 一致 |

**实测的正确模式**（取自项目自己 512/512 的 `sub4.circ`）：

```
输入： <Pin type=input>  --wire-->  <Tunnel facing=west>   …按标签…   <Tunnel facing=east> 在元件输入端口上
输出： 元件输出端口 --wire--> <Tunnel facing=west>        …按标签…   <Pin type=output facing=west> + <Tunnel facing=east> 共位
```

**输入侧与输出侧不对称**：输入 Pin 必须用 wire 接 Tunnel；输出 Pin 可与 Tunnel 共位。

### 36.8 自动测试台已建立并可穷举验证任意组合组件

`scripts\py\mkbench.py`：从 `references\components.json` 的**实测端口**自动生成穷举测试电路，
不需要人工布线（Tunnel 按标签连接），支持 `--attr` 设置属性、`--max-in-bits` 拒绝过大的枚举，
并拒绝不存在的属性名而**不静默丢弃**。

**已穷举验证通过（真实仿真）**：

| 组件 | 行数 | 结果 |
|---|---|---|
| `AND Gate` | 4 | `0 0 0 1` ✅ |
| `OR Gate` | 4 | `0 1 1 1` ✅ |
| `XOR Gate` | 4 | `0 1 1 0` ✅ |
| `NAND Gate` | 4 | `1 1 1 0` ✅ |
| `NOR Gate` | 4 | `1 0 0 0` ✅ |
| `XNOR Gate` | 4 | `1 0 0 1` ✅ |
| `Buffer` | 2 | `0 1` ✅ |
| `NOT Gate` | 2 | `1 0` ✅ |
| `Odd Parity` | 4 | `0 1 1 0` ✅ **与 XOR 2 输入完全一致** |
| `Even Parity` | 4 | `1 0 0 1` ✅ **与 XNOR 2 输入完全一致** |

⚠️ 这些是**默认属性**下的结果。属性变体（`inputs`>2、`width`>1、`size`、`negateN`、`out`）的端口
需先设属性再重新测量端口，`mkbench.py` 目前用默认端口，**属性变体下的端口表尚未接入**——这是已知缺口。

## 37. ⚠️⚠️ 门输入脚偏移不是一条规则 —— 8 位 ALU 击穿了它

### 37.1 缺陷

`circmodel.input_ports` 用**一条规则**覆盖整个门族：`-(size+10) if name in WIDE_GATES else -size`，
其中 `WIDE_GATES = {XOR, XNOR}`。这条规则只对 AND/OR 和 XOR 拟合过，然后被推广到全族。实测（`components.json`）：

| 门 | size 30 | size 50 | size 70 | 规律 |
|---|---|---|---|---|
| AND / OR | −30 | **−50** | −70 | `−(size + 0)` |
| **NAND / NOR** | −40 | **−60** | −80 | `−(size + 10)` |
| XOR | −40 | −60 | −80 | `−(size + 10)` |
| **XNOR** | −50 | **−70** | −90 | `−(size + 20)` |
| NOT | —(size 20) −20 | −30 | — | `−(size + 0)` |
| Buffer | — | −20 | — | 固定，无 `size` 属性 |

**dx 错 10px → 汇入 Tunnel 落在真实端口之外 → 该门输入悬空 → 输出变成常数。**

### 37.2 症状：恒定输出，不随输入变化

8 位 ALU 的 Op=4（NAND）与 Op=5（NOR）**无论 A、B 取何值都返回 `0xEE`**；同一电路里 AND/OR/XOR/NOT/ADD/SUB/MUL **全部正确**。

```
  Op=4 NAND   A=  0 B=  0  Y=0xEE expected=0xFF  BAD
  Op=4 NAND   A=255 B=255  Y=0xEE expected=0x00  BAD
  Op=5 NOR    A=  1 B=  0  Y=0xEE expected=0xFE  BAD
  Op=0 AND    A=255 B=255  Y=0xFF expected=0xFF  OK
```

「与输入无关的常数输出」是**输入悬空**的特征。修好偏移后 ALU 通过 **1,048,576 / 1,048,576**。

### 37.3 同一缺陷的**第二份拷贝**

`audit_width.py` 里有一份独立的同一规则：

```python
WIDE_GATES = {"XOR Gate", "XNOR Gate"}
dx = -(size + 10) if name in WIDE_GATES else -size
```

因此审计器在**真实仿真 1,048,576 行全部通过**的电路上报：

```
[W4] net 'net11': net has NO driver but feeds ['NAND Gate@(520, 560):in0']
```

悬空输入不可能通过 1,048,576 行——**是审计器错了，不是电路错了**。

**重复的逻辑 = 重复的缺陷。** 已改为 `audit_width.py` 直接调用 `circmodel.input_ports`（唯一的实测表所有者）。
未测量 size 扫掠的门（Parity / PLA / Controlled Buffer·Inverter）现在**报错而非猜测**——它们也不在 `GATE_NAMES` 中，不可达。

### 37.4 顺带扩展：Multiplexer select 1..8

`mux_ports` 原本只支持 select ≤ 3，构建 select=4 时**拒绝**（正确行为）。而知识库里**已有 1..8 的全部实测**。公式经**逐一核验**后才写入：

| select | data_x | first_dy | step | select_y | box 高 |
|---|---|---|---|---|---|
| 1 | x−30 | −10 | 20 | +20 | 40 |
| 2 | x−40 | −20 | 10 | +20 | 60 |
| 3 | x−40 | −40 | 10 | +40 | 100 |
| 4 | x−40 | −80 | 10 | +80 | 180 |
| 5..8 | x−40 | −10·2^(k−1) | 10 | +10·2^(k−1) | 20·(1+2^(k−1)) |

`scripts\analysis\extend_mux_ports.py` **在任何一项与实测不符时拒绝打补丁**。

### 37.5 事故：`rtllib.py` 曾被完全破坏并已重建

**原因**：我用 PowerShell 字符串替换改多行源码，替换文本里的 `` `n `` 在单引号串中未被解释，字面量写入文件；随后的「修复」又把换行全部删除，文件变成一条 `#!` 注释（空模块）。

**教训**：**永远不要用 shell 字符串手术改多行源码**——用 `edit`/`write` 文件工具。

**恢复**：字符全部幸存，但换行位置不可恢复。按 `tests\py\test_rtllib.py`（30+ 断言）、7 个 RTL spec、
幸存的模块 docstring（含全部实测端口表）重建，**以 29 个测试全通过为验收**。同时补上了 Multiplier 与
Subtractor 支持（端口来自实测，乘法器语义由 65,536 行穷举确认）。

### 37.6 8 位 ALU 交付

`output\alu8.circ`：A[8]、B[8]、Op[4] → Y[8]、Yhi[8]；95 个元件（6 门 + Adder + Subtractor +
Multiplier + 2×select-4 Multiplexer + 4 Constant）。
Op：0 AND / 1 OR / 2 XOR / 3 NOT(A) / 4 NAND / 5 NOR / 6 ADD / 7 SUB / 8 MUL / 9–15 保留（0）。

**穷举验证 1,048,576 / 1,048,576 行，0 失配。** 阴性对照：修偏移之前同一 spec 失败 **130,334 行**——
这个数字就是「门偏移错了」的真实对照证据。

## 38. 工具栏「又没了」—— 根因是头部补全是**手工步骤**，已从架构上根除

### 38.1 症状与根因区分

**症状**：打开生成的电路，工具栏是空的，无法放置元件。

**底层事实**（此前已实测）：`ToolbarData()` 的 `contents = new ArrayList<>()` **没有默认项**，
`XmlReader.java:536-537` **只从 `<toolbar>` 元素填充**。**没有 `<toolbar>` 的文件打开就是空工具栏。**

**但"又没了"的真正根因不是这个事实，而是流程**：

> `project_header.py` 是一个**独立、手工、可选**的步骤。各个生成器各自 `path.write_text(...)`
> 写文件，**没有任何一个调用它**。

所以每次重新生成电路，头部就消失一次。实测缺失范围：

| 位置 | 状况 |
|---|---|
| `generated/` **全部 24 个** | ❌ 缺 `<toolbar>` |
| 新增的 `fulladder_wired.circ` | ❌ 缺 |
| `output/` 中我**手工**跑过补全脚本的 3 个 | ✅ 有 |

**"修一次文件"这条路已经被证明会失败** —— 因为下一次生成又会覆盖掉。

### 38.2 根除：三层

**第一层 — 写入即补全，不补全就拒绝**

`project_header.py` 新增 `write_circ(path, text, source='5.0.0')`：补全头部 → 校验 →
**若头部仍不完整则抛 `RuntimeError`，拒绝写文件**。

全部 5 个写入点改为走它：

| 文件 | 原写入点 |
|---|---|
| `circmodel.py` | `path.write_text(to_xml(circuit), ...)` |
| `rtllib.py` | `path.write_text(self.to_xml(), ...)` |
| `seqmodel.py` | `path.write_text(to_xml(c), ...)` |
| `` | `out.write_text(to_xml(routes, out_xy), ...)` |
| **`repair.py`** | `out_circ.write_text(winner["circuit"], ...)` ← 最后一个漏网的 |

**第二层 — 仓库级守卫，回退立刻变红**

新增 `tests\py\test_project_header.py`：

1. 扫描 `output/` 与 `generated/` 下**每一个 `.circ`**，
   缺 `<toolbar>`/`<options>`/`<mappings>` 或 `<lib>` 少于 13 个即**失败**；
2. 扫描 `scripts\py\*.py`，**任何绕过 `write_circ` 直接写 `.circ` 的代码即失败**。

同一扫描也加进 `audit_skill.py`，所以自审会报 `PROJECT HEADER on every generated .circ`。

**第三层 — 重建全部产物，不留过期文件**

重建后发现 `mux2_broken.circ` 仍是旧的：因为它是**故意的缺陷对照**，生成器（正确地）
拒绝写它，于是测试一直在验证一个**过期文件**。加了 `--force`：

```
circmodel.py build <spec> <out> --force
  !! --force: writing a KNOWN-DEFECTIVE control with 3 problem(s)
  wrote ... (4 gates, 7 nets)
```

**阴性对照必须可复现**，而且**必须带头部**，否则它不像一个真实文件。

### 38.3 顺带修掉的两个"重复包装 stdout"缺陷

`project_header.py` 在被 `import` 时也包装 `sys.stdout`，而调用方已经包装过 ——
同一个 buffer 被包两次，第二个包装器回收时**关闭底层流**，之后每次 `print` 都抛
`I/O operation on closed file`。

已改为**只在 `__main__` 时包装**。这是本项目第三次踩到同一类问题（`rtllib`/`router`/`geom`），
规则：**任何模块都不应在被 import 时改 `sys.stdout`。**

### 38.4 结果

| 项 | 修复前 | 修复后 |
|---|---|---|
| 缺头部的 `.circ` | **12 / 30** | **0 / 30** ✅ |
| 绕过 `write_circ` 的写入点 | 5 | **0** |
| 守卫测试 | 无 | **3 个** |
| 自审是否检查头部 | 否 | **是** |
| 测试套件 | 263 | **266 全通过** |

**关键电路复核**：`adder4` 512/512、`sub4` 四例（正例通过、三个对照全失败）、
`fulladder_wired` toolbar=True。**用户 9 个样本未改动。**
