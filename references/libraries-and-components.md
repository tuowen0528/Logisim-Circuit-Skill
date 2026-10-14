# 02 — Libraries, components, attributes and ports

Evidence base for generating `.circ` files that **load and simulate correctly on the first attempt**.

- Reference source: `logisim-evolution-main` @ `5.1.0-dev`
- CLI used for all empirical checks: `scripts\bin\logisim-evolution-all.jar` (5.1.0dev)
- Every claim below is either **cited** (`File.java:LINE`) or marked `[UNVERIFIED]`.
- "Verified" means: a `.circ` containing it was loaded by the real CLI and simulated correctly. Probes live in `tests\circ\probe\`.

> **Golden rule for the generator:** a `.circ` references components as
> `<comp lib="<ordinal>" name="<exact factory name>">` with `<a name="<exact attr>" val="..."/>`
> children. Both the name and the attribute names are case- and space-sensitive. A wrong
> component name is **silently dropped** (the file still loads, the component is just gone).

---

## 1. Libraries and the file-local ordinal

### 1.1 How the ordinal is assigned

`<lib name="0"/>` uses a **file-local ordinal**, not a fixed id:

- Writer: `XmlWriter.fromLibrary` assigns `Integer.toString(libs.size())` **in write order**, storing it in a map (`XmlWriter.java:378-387`). So the first library written gets `"0"`, the next `"1"`, etc.
- Reader: `XmlReader.findLibrary(libName)` looks the string up in a `HashMap<String, Library>` populated from the file's own `<lib>` elements (`XmlReader.java:91,109-112`).
- `<comp lib="...">` is resolved through that same map (`XmlCircuitReader.java:78-93`).

**Consequence:** the ordinal is only *self-consistent within one file*. You may declare libraries in any order and any numbering, as long as every `<comp lib=>` refers to a `<lib name=>` declared in that same file. Reordering `<lib>` elements **without** updating every `<comp lib=>` silently points components at the wrong library (or drops them).

The `desc` string is a **display descriptor** of the form `#DisplayName`. It works in the fixtures as `#Wiring` / `#Gates`, but only `name` is load-bearing. `[UNVERIFIED]` whether a missing/incorrect `desc` affects loading — always emit the correct `desc` anyway.

### 1.2 Library inventory (verified by dumping each library's tool list)

Measured by loading a project and enumerating `Library.getTools()` → `AddTool.getFactory().getName()` (see `tests\circ\probe\PortDump.java`, "LIBRARIES" section).

| `desc` | Library id | Exact XML names of tools (in `getTools()` order) |
|---|---|---|
| `#Wiring` | `Wiring` | `Splitter`, `Pin`, `Probe`, `Tunnel`, `Pull Resistor`, `Clock`, `POR`, `Constant`, `Power`, `Ground`, `NoConnect`, `Transistor`, `Transmission Gate`, `Bit Extender` |
| `#Gates` | `Gates` | `NOT Gate`, `Buffer`, `AND Gate`, `OR Gate`, `NAND Gate`, `NOR Gate`, `XOR Gate`, `XNOR Gate`, `Odd Parity`, `Even Parity`, `Controlled Buffer`, `Controlled Inverter`, `PLA` |

Other built-in libraries exist (arithmetic, memory, plexers, io, ttl, base, …); their tool lists are **not yet enumerated here** (see §7). Do not guess their XML names.

> `NoConnect` is the "Do not connect" tool — its XML name is `NoConnect`, **not** "Do not connect" (`WiringLibrary`, verified in the dump above).

---

## 2. Minimum viable `.circ` skeleton

Verified working (the shape below loads and simulates):

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<project source="5.1.0dev" version="1.0">
  <lib desc="#Wiring" name="0"/>
  <lib desc="#Gates" name="1"/>
  <main name="main"/>
  <circuit name="main">
    <!-- comps and wires -->
  </circuit>
</project>
```

- `XmlWriter.fromComponent` writes exactly three attributes on `<comp>`: `lib`, `name`, `loc` (`XmlWriter.java:370-373`). Everything else is an `<a>` child.
- A component with **no** `lib` attribute resolves against the project itself (subcircuits), not against a library (`XmlReader.findLibrary`: empty name ⇒ `file`, `XmlReader.java:110`).
- Wires are `<wire from="(x,y)" to="(x,y)"/>`; a zero-length wire is skipped on read (`XmlCircuitReader.java:161-164`).
- Optional wire attribute `buswidthpos` (`XmlCircuitReader.java:165-171`).

### Writing files: encoding matters

Write `.circ` and test-vector files as **UTF-8 without BOM**, explicitly:
`[System.IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding($false)))`.
PowerShell's `Set-Content -Encoding UTF8` inserts a BOM, and Python's default here is cp936
(see `docs\environment-audit.md` §4).

---

## 3. ⭐ Critical path: `Pin`, `AND Gate`, `OR Gate`, `XOR Gate`, `wire`

This section is what the 4-bit ripple-carry adder needs. All of it is verified by simulation.

### 3.1 `Pin`

- XML name: **`Pin`** (`Pin.java:77` `_ID = "Pin"`; constructor `super(_ID, …)` `Pin.java:742`). Library: `#Wiring`.
- **Exactly one port, always at the pin's own `loc`**: `new Port(0, 0, endType, StdAttr.WIDTH)` (`Pin.java:779`). No offset, so `end == loc`.
- Port **direction inverts** for output pins: an *input* pin creates an `OUTPUT` port; an *output* pin creates an `INPUT` port (`Pin.java:778`). This is from the circuit's point of view and is correct — verified.

| attribute | required | type / legal values | default | notes |
|---|---|---|---|---|
| `label` | **practically required** | string | `""` | The label is the column name in `--tty table` output **and** the name test vectors reference. Unlabelled pins are unusable for verification. |
| `type` | optional | `input` \| `output` | `input` | `PinAttributes.java:35` `type = Pin.INPUT`. Determines port direction + which attribute list applies. |
| `facing` | optional | `east`\|`west`\|`north`\|`south` | `east` | Rotation only. Only affects drawing/label placement, **not** the port location (port is at `(0,0)` regardless). |
| `width` | optional | bit width (e.g. `1`, `4`, `8`) | `1` | `PinAttributes.java:34`. Sets port width. |
| `behavior` | optional | `simple` \| `pull up` \| `pull down` \| `tristate` `[UNVERIFIED values]` | `simple` | Only shown for input pins (`PinAttributes.java:24-32`). |
| `labelloc` | optional | `center`\|`north`\|`south`\|`east`\|`west` | derived from `facing` | `Pin.java:752-757`. |
| `labelvisible`, `labelfont`, `labelcolor` | optional | see `StdAttr.java:45-56` | — | Cosmetic. |
| `initial` | optional | long | `0` | Input pins only (`PinAttributes.java:26`). |

Verified input/output pin XML (from working probes):

```xml
<comp lib="0" loc="(60,80)" name="Pin">
  <a name="label" val="a"/>
  <a name="type" val="input"/>
</comp>

<comp lib="0" loc="(1000,200)" name="Pin">
  <a name="label" val="s"/>
  <a name="type" val="output"/>
  <a name="facing" val="west"/>
</comp>
```

Notes:
- `facing="west"` on an output pin is **not required** for the pin to work; `minimal.circ` uses it and so do the working probes, but the port is at `loc` either way. Keeping it is harmless and matches GUI convention for right-hand-side outputs.
- **`type` may be omitted** and defaults to `input`. Omitting it on what you intend as an output pin silently yields an *input* pin — always state `type="output"` explicitly.

### 3.2 Gate names and geometry

**XML names contain a space and the word "Gate"** (except `Buffer`):

| component | XML `name` | factory source |
|---|---|---|
| AND | `AND Gate` | `AndGate.java` `super("AND Gate", …)` |
| OR | `OR Gate` | `OrGate.java` `super("OR Gate", …)` |
| XOR | `XOR Gate` | `XorGate.java:53` `super("XOR Gate", …)` |
| NAND | `NAND Gate` | `NandGate.java` |
| NOR | `NOR Gate` | `NorGate.java` |
| XNOR | `XNOR Gate` | `XnorGate.java` |
| NOT | `NOT Gate` | `NotGate.java` `_ID = "NOT Gate"` |
| buffer | `Buffer` | `Buffer.java` `_ID = "Buffer"` (no "Gate" suffix!) |

> A wrong name is **silently discarded** — the project still loads and `--tty table` still prints, the gate is just absent. Use `--tty stats` to detect it (the component count changes).

**Port geometry for a 2-input gate (`facing = east`, the default):**

| | output | input0 | input1 |
|---|---|---|---|
| `XOR Gate` | `(loc.x, loc.y)` | `(loc.x−60, loc.y−20)` | `(loc.x−60, loc.y+20)` |
| `AND Gate`, `OR Gate`, `NAND`, `NOR`, `XNOR`* | `(loc.x, loc.y)` | `(loc.x−50, loc.y−20)` | `(loc.x−50, loc.y+20)` |

\* only `AND`/`OR`/`XOR` were measured port-by-port; the others share the same class and `size` defaults, so they follow the same rule — **verify before relying on the exact x for NAND/NOR/XNOR**.

Measured directly via `PortDump` (loads the file with Logisim's own loader and prints `comp.getEnds()`):

```
XOR Gate loc=(400,100) → end[0]=(400,100) OUTPUT, end[1]=(340,80) INPUT, end[2]=(340,120) INPUT
AND Gate loc=(400,500) → end[0]=(400,500) OUTPUT, end[1]=(350,480) INPUT, end[2]=(350,520) INPUT
OR  Gate loc=(780,500) → end[0]=(780,500) OUTPUT, end[1]=(730,480) INPUT, end[2]=(730,520) INPUT
```

**Why XOR is 10px wider:** `XorGate` calls `setAdditionalWidth(10)` (`XorGate.java:54`), and the input offset uses `axisLength = size + bonusWidth + (negateOutput ? 10 : 0)` (`AbstractGate.java:217`). With the default `size = 50` (`GateAttributes.java:60`) that is `60` for XOR and `50` for the others. `getInputOffset` returns `(-axisLength, dy)` for `facing == EAST` (`AbstractGate.java:268-269`), and `Port.toEnd` translates by exactly that offset (`Port.java:122-124`). This off-by-10 is the single easiest way to mis-wire an adder, because the full adder uses both XOR and AND.

Gate attributes (defaults from `GateAttributes.java:58-66`):

| attribute | required | legal values | default |
|---|---|---|---|
| `width` | optional | bit width | `1` |
| `facing` | optional | `east`/`west`/`north`/`south` | `east` |
| `size` | optional | `30` (narrow) / `50` (normal) / `70` (wide) | `50` |
| `inputs` | optional | integer 2…64 | `2` |
| `out` | optional | `01` / `0Z` / `Z1` | `01` |
| `negate0`, `negate1`, … (`negateN`) | optional | `true`/`false` | `false` |
| `xor` | XOR/XNOR only | `1` (exactly-one) / `odd` | `1` |
| `label`, `labelfont` | optional | — | — |

Minimal verified 2-input gate XML (no attributes needed — but see the `inputs` warning below):

```xml
<comp lib="1" loc="(400,100)" name="AND Gate"/>
<comp lib="1" loc="(400,100)" name="XOR Gate"/>
<comp lib="1" loc="(400,100)" name="OR Gate"/>
```

> ⚠️ **`inputs` is not written when it equals the default `2`.** If you intend a 3-input gate you *must* emit `<a name="inputs" val="3"/>`, which also moves the ports.
> ⚠️ Negated inputs are `negateN` booleans **and** they shift the port outward by 10 (`AbstractGate.java:257-260`) — combining negation with fixed coordinates is a trap.

### 3.3 Wiring rules — the part that actually causes failures

Connection semantics, established empirically:

1. A wire **ends exactly on** a port ⇒ connected.
2. A wire **passes through** a port (ends beyond it on the same axis) ⇒ **also connected**.
3. Two components whose ports are at the **same coordinate** ⇒ connected (this is how unintended merges happen).
4. Wires are strictly **axis-aligned** (horizontal or vertical). A **diagonal** wire is not merely wrong — it makes `Wire`'s point iterator non-terminating, so `XmlReader` → `WireRepair.doOverlaps` spins until `OutOfMemoryError` (`WireRepair.java:170`; `Wire.java:311` `isXEqual` basis). **Observed:** a diagonal `(40,90)→(240,100)` produced `OutOfMemoryError: Java heap space` at load.

**Measured port-x threshold** — XOR gate at `loc=(400,100)`, so input ports are at `x=340`.
Wires run to `(x,80)` and `(x,120)`:

| wire end x | result |
|---|---|
| 300, 320, 330, 338 | `E` on every row — **not connected** |
| **340** | correct XOR — ends exactly on the port |
| 380 | correct XOR — **passes through** the port at 340 |

This proves the port is at `x=340` (the `loc.x−60` rule) and also shows that a wire overshooting
the port still connects. Evidence: `tests\circ\probe\px_300/320/330/338/340.circ` and `portx_short_380.circ`.

> **Why this matters for generators:** if you compute port x from a wrong rule (e.g. `loc.x−20`)
> and route your wire *past* the true port, the circuit may still work by accident. That is exactly
> what happened in the Lead's `fulladder2.circ` (see §7). Do not rely on it — an accidental
> pass-through is indistinguishable from a correct connection until the geometry changes.

**Two failure modes that are easy to misdiagnose** (both hit during this investigation):

| mistake | symptom | cause |
|---|---|---|
| routing two different inputs to the *same* point | `E` on every row, but `0` on rows where both inputs are equal | two drivers contend on one net |
| a wire ending *short* of the port | `E` on every row | no connection at all |

Both print `E`; only the row pattern distinguishes them. An all-`E` column is the signature of
**no connection**, while `E` on only *some* rows indicates **contention**.

**Safe authoring discipline (verified to work):**
- Give every net its own **vertical channel x** and its own **horizontal lane y**.
- Only perpendicular segments; **never** a diagonal.
- Route each wire so it ends exactly on the target port (safest), and keep unrelated nets off
  each other's channel/lane coordinates.
- Add an explicit corner segment when branching, rather than relying on an endpoint landing mid-wire.

**Verified reference probe** — `tests\circ\probe\px_340.circ` (2-input XOR at `loc=(400,100)`,
inputs from *separate* pins, wires ending on the ports) produces the correct table:

```
a b y
0 0 0
0 1 1
1 0 1
1 1 0
```

### 3.4 A 3-gate verified cross-check

`tests\circ\probe\gates3.circ` (one XOR, one AND, one OR, six input pins, three output pins) was
simulated with `--tty table`, giving 64 rows; all 64 matched an independent Python model of
`yx = ax^bx`, `ya = aa&ba`, `yo = ao|bo` — **0 mismatches**. This exercises the whole
`Pin` + gate + `wire` surface the adder needs.

---

## 4. How to verify a generated circuit (this is the acceptance mechanism)

**`--test-vector` is NOT usable headless.** It never sets `Main.headless` (`--tty`/`--test-fpga` are
the only setters, `Startup.java:362-363`), so `Main.hasGui()` is true and the run dies in
`OptionPane.showMessageDialog` with `HeadlessException` (`Main.java:83-91`). Same for `--test-circuit`
on its own. **Confirmed by experiment** for both.

**Use `--tty table`** — it needs no test-vector file, runs the exhaustive simulation itself, and
prints the full truth table:

```powershell
$j = Start-Job -ScriptBlock {
  param($jar,$f)
  & java '-Djava.awt.headless=true' -jar $jar --locale en --tty table $f 2>&1
} -ArgumentList $jar,$f
$null = Wait-Job $j -Timeout 60
if ($j.State -eq 'Running') { Stop-Job $j; Remove-Job $j -Force; 'TIMEOUT/OSCILLATION' }
else { Receive-Job $j; Remove-Job $j -Force }
```

- One row per input combination, ascending; header is the pin labels in circuit order.
- Multi-bit pins render as `NAME[width]` with right-aligned binary.
- A 9-input adder therefore yields all **512** rows in one run — no test-vector file needed.
- For defect detection, diff the emitted rows against an independently computed model.
- `--tty stats` lists component counts (good for catching **silently dropped** components).

### Tooling traps that produce false readings

| trap | effect | avoid |
|---|---|---|
| `java … \| Select-Object -First N` | pipe closes, JVM killed, **fake** non-zero exit | capture fully, then inspect |
| `Start-Process -ArgumentList` | splits `<your checkout directory>\…` on the space → java sees `D:\Logisim` | quote/array form; prefer `Start-Job` |
| redirecting `--tty table` output with `1>` | file is written **UTF-16** on this host | decode with BOM detection, or capture in-process |
| no timeout on a probe | an oscillating circuit burns CPU indefinitely | always `Start-Job` + `Wait-Job -Timeout` |
| `Set-Content -Encoding UTF8` | inserts a **BOM** | `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))` |

---

## 5. Attributes shared across components (`StdAttr`)

The XML attribute name is the **first argument** of each `Attributes.for*` factory call
(`instance\StdAttr.java`):

| XML name | Java constant | type / legal values | line |
|---|---|---|---|
| `facing` | `FACING` | `east`, `west`, `north`, `south` | 24 |
| `width` | `WIDTH` | bit width | 26 |
| `trigger` | `TRIGGER` / `EDGE_TRIGGER` | `rising`, `falling`, `high`, `low` (edge variant: only `rising`/`falling`) | 31, 37 |
| `label` | `LABEL` | string | 39 |
| `labelfont` | `LABEL_FONT` | font | 40 |
| `labelcolor` | `LABEL_COLOR` | colour | 42 |
| `labelloc` | `LABEL_LOC` | `center`, `north`, `south`, `east`, `west` | 53 |
| `labelvisible` | `LABEL_VISIBILITY` | boolean | 58 |
| `appearance` | `APPEARANCE` | `classic`, `evolution`, `logisim_evolution` | 62 |
| `selloc` | `SELECT_LOC` | `bl`, `tr` | 75 |
| `fpwidth` | `FP_WIDTH` | 8/16/32/64 | 65 |

---

## 6. Gotchas (ranked by how likely they are to break a generated file)

1. **Gate XML names have a space** — `AND Gate`, not `AND`. Wrong ⇒ **silently dropped**.
2. **XOR/X(N)OR inputs sit 10px further left** than AND/OR (`loc.x−60` vs `loc.x−50`). Mixing them up shifts nets.
3. **Never emit a diagonal wire.** It hangs the loader with `OutOfMemoryError`.
4. **`type` defaults to `input`** on `Pin` — always write `type="output"` for outputs.
5. **`inputs` is omitted at its default of 2**; a 3-input gate needs it explicit (and ports move).
6. **`negateN` shifts the port by 10px** — don't combine with hardcoded coordinates.
7. **`out` defaults to `01`** (normal); `0Z`/`Z1` create three-state drivers.
8. **`NoConnect`** is the XML name of the "Do not connect" tool.
9. **`Buffer` has no "Gate" suffix** while `NOT Gate` does.
10. **Library ordinals are file-local** — reordering `<lib>` without rewriting every `<comp lib=>` corrupts the file.
11. **`--test-vector`/`--test-circuit` are not headless**; use `--tty table`.
12. **`--tty table` output is UTF-16 when redirected** here.
13. **Two ports at the same coordinate connect** — this is how an unintended merge (`E`) happens.
14. **`facing` does not move a `Pin`'s port** (always at `loc`), but it *does* move a **gate's** ports.

---

## 7. Not yet catalogued (explicit unknowns)

`[UNVERIFIED]` — do **not** use these in generated files until added here with citations:

- Tool inventories and XML names for the arithmetic, memory, plexers, io, ttl, base libraries
  (incl. `Multiplexer`, `Decoder`, `PriorityEncoder`, `Adder`, `Comparator`, `Bit Extender`,
  `Shifter`, `D Flip-Flop`, `Register`, `Counter`, `RAM`, `ROM`, `Button`, `LED`, `7-Segment`).
- Attributes and port layouts for `Splitter`, `Tunnel`, `Probe`, `Constant`, `Clock`, `Power`,
  `Ground`, `Transistor`, `Transmission Gate`, `PLA`, `Controlled Buffer`.
- Multi-input (>2) gate port spacing; non-`east` `facing` port offsets; the exact `behavior` value
  strings for `Pin`; `size`'s effect on non-default values.
- Whether a missing/incorrect `desc` on `<lib>` affects loading.
- Subcircuit representation (`<circuit>` referenced as a component) and appearance blocks.

### Evidence artefacts

| file | what it proves |
|---|---|
| `tests\circ\probe\PortDump.java` | loads a `.circ` with Logisim's loader and prints real `comp.getEnds()` port coordinates; also dumps each library's tool list |
| `tests\circ\probe\px_340.circ` | XOR with wires ending exactly on the ports → correct truth table |
| `tests\circ\probe\px_300.circ` (also 320/330/338) | same layout, wires ending short → all-`E`, no connection |
| `tests\circ\probe\portx_short_380.circ` | wires ending *past* the port → still connects (pass-through) |
| `tests\circ\probe\gates3.circ` | XOR+AND+OR, 64 rows, 0 mismatches vs independent model |
| `tests\circ\probe\and_ok.circ` | 2-input AND, verified truth table |
| `tests\circ\probe\fulladder2.circ` | Lead's 1-bit full adder (8 rows correct); its comment claims `loc.x−20`, which is wrong — it works only because its wires **pass through** the true ports (see §3.3) |

### Failure symptom dictionary

| symptom | meaning |
|---|---|
| `U` | input pin driven but the wire never reaches the driver |
| `E` on **every** row of a column | **no connection** (wire ends short, or a gate input is undriven) |
| `E` on **some** rows only | **contention** — two drivers merged onto one net |
| empty output, exit −1, CPU pegged | combinational **loop / oscillation**; never converges. Always probe under a hard timeout. |
