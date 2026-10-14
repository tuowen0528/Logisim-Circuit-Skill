# Capability ledger — what the headless CLI provably does

> **Paths in this document refer to the development tree, not to this package.** The commands
> and file paths were recorded while the measurements were taken and are kept verbatim as
> evidence. This package ships no circuits, no test suite and no `examples/` directory — write
> your own spec and circuit files as shown in `README.md` §5.

Status of every entry: **VERIFIED** (literal command + literal output observed),
**UNVERIFIED** (not established — do not depend on it), or **REFUTED** (believed true, disproved;
kept so we do not repeat it).

- Jar: `scripts\bin\logisim-evolution-all.jar` — 5.1.0dev, sha256 `B872B0C2D4A46F02676C280C30FBD022B74248B9641A7D3D1DB29F9715868515`
- Harness: `scripts\dsh\cli-probe.py` (raw-byte stdout/stderr capture, hard timeout)
- Projections: `tests\circ\smoke\`, `tests\circ\probe\`
- Lead's probes: `references\00-probe-log.md`

> ### Two harness traps that invalidate evidence — both hit during this session
>
> 1. **Never pipe a java process into a truncating cmdlet.** `java … | Select-Object -First N`
>    closes the pipe, kills the JVM, and reports a **false** non-zero exit code. Observed
>    `exit=-1` on a run whose true code is `0`. Capture fully, *then* read `$LASTEXITCODE`.
>    Same class of trap: `Start-Process -ArgumentList` splits paths on spaces, and `cmd /c` strips
>    the quotes you add — both handed java `D:\Logisim` instead of the full path.
> 2. **PowerShell redirection transcodes and swallows.** `& java … 1>out 2>err` wrote stdout as
>    **UTF-16LE** (first bytes `255,254`), so an "empty table" looked like a 4-byte file of NULs;
>    and `2>&1` merged stderr into `ErrorRecord` objects, hiding the error text entirely. All
>    evidence below therefore comes from `scripts\dsh\cli-probe.py`, which uses
>    `subprocess.run(capture_output=True)` and writes raw bytes.

---

## 1. ✅ Headless operation — VERIFIED

`--tty` is the only supported headless mode; it is what sets `Main.headless`:

```java
var isTty = false;
if (cmd.hasOption(ARG_TTY_SHORT) || cmd.hasOption(ARG_TEST_FPGA_SHORT) || cmd.hasOption(ARG_TEST_FPGA_LONG)) {
  isTty = true;
  Main.headless = true;                                            // Startup.java:362-363
}
```

Measured `GraphicsEnvironment.isHeadless()` is **false by default** on this host, so
`-Djava.awt.headless=true` must be passed explicitly.

```powershell
java '-Djava.awt.headless=true' -jar scripts\bin\logisim-evolution-all.jar --locale en --tty table <file.circ>
```

| Command | Result |
|---|---|
| `--tty table` on a valid circuit | exit **0**, table on stdout, 0 B stderr |
| `--tty stats` on a valid circuit | exit **0**, component histogram on stdout |

`--locale en` is **mandatory**: the JVM default locale here is `zh_CN` with `stdout.encoding=MS936`,
so an unpinned run renders localized text in GBK.

---

## 2. ⭐ `--tty table` — the verification primitive (most detailed section)

`--tty table` performs **exhaustive simulation**: one row per input combination, in one process, with
no test-vector file and no GUI. A 9-input adder therefore yields all **512** rows in a single run.
This is the mechanism the 4-bit-adder acceptance test depends on.

Entry point: `TtyInterface.doTableAnalysis` (`TtyInterface.java:394-488`). It is reached only when
there is no output pin named `halt` (`TtyInterface.java:344-347`).

### 2.1 Driver source facts

| Property | Source |
|---|---|
| One fresh `CircuitState` per row; inputs driven from the row index | `TtyInterface.java:444-461` |
| Row count = `1 << (total input BITS)` — multi-bit pins expand to their bits | `TtyInterface.java:439-440` |
| Bit index within a row: `idx & (1 << (inputs - col - 1))` | `TruthTable.java:296-298` |
| Within a pin, bits are filled MSB-first | `TtyInterface.java:452-455` |
| Header text: `name` if width == 1, else `name[width]` | `TtyInterface.java:210-212` |
| Value text | `TtyInterface.java:195-208` |
| Column widths from the **first row's** values | `TtyInterface.java:171-179` |
| Separator is a single space (pretty mode) | `TtyInterface.java:166-169` |
| Oscillating circuit ⇒ every output becomes error | `TtyInterface.java:470-472` |

### 2.2 ⚠️ Column order is **geometric**, not XML declaration order

`TtyInterface` gets its pin map from `Analyze.getPinLabels` (`TtyInterface.java:328`), which returns a
`TreeMap` ordered by `Location.CompareVertical` (`Analyze.java:267`) — i.e.

```java
if (aloc.y != bloc.y) return aloc.y - bloc.y;   // smaller y first: TOP to BOTTOM
else if (aloc.x != bloc.x) return aloc.x - bloc.x;  // then LEFT to RIGHT
else return a.hashCode() - b.hashCode();        // then HASH CODE
```

Then `doTableAnalysis` emits **all input pins first, then all output pins**, each group in that
geometric order (`TtyInterface.java:418-437`).

**Measured** with a deliberately scrambled layout (components declared in the order `r, r2, p, q, s, t`
but positioned so the geometric order differs):

```
r r2 p[4] q s[2] t
0  0 0000 0   UU U
...
1  1 1111 1   UU U
```
129 lines = 1 header + **128 rows** = 2^7, the input bits being `p[4] + r2 + q + r` = 7 bits.

> **Generator rules.**
> (a) To control column order, control **y then x** placement — reordering `<comp>` elements in the
> XML does nothing.
> (b) Equal `y` *and* equal `x` falls back to `hashCode()`, which is not stable: never place two pins
> at the same point.

### 2.3 ⚠️ Value rendering depends on width

```java
private static String valueFormat(Value v, int format) {
  if ((format & FORMAT_TABLE_BIN) != 0) return v.toString();
  else if ((format & FORMAT_TABLE_HEX) != 0) return v.toHexString();
  else {
    if (v.getWidth() <= 6) return v.toBinaryString();
    else return "0x" + v.toHexString();
  }
}
```

So in plain `--tty table` mode: **width ≤ 6 ⇒ binary; width ≥ 7 ⇒ hex with `0x` prefix.**
Measured:

| Input | Output |
|---|---|
| 2-bit pin (`width=2`) | `a[2] y[2]` then rows `  00   00`, `  01   01`, `  10   10`, `  11   11` (right-aligned in the header-derived width) |
| 4-bit pin (`width=4`) | `p[4]`, rows `0000 … 1111` |
| 7-bit value | would render `0x00`… `0x7f` `[UNVERIFIED — not measured]` |

Width-1 pins print bare (`a`, not `a[1]`) — `TtyInterface.java:211`.

> **Generator rule:** a parser must know each column's width to interpret its radix.
> The header carries `name[width]` for width > 1; a bare name means width 1.

### 2.4 `U` and `E` are printed as ordinary values

Measured on undriven / contended outputs:

| Output | Meaning |
|---|---|
| `U` | net not driven (wire does not reach the port) — e.g. `onlyout` / `U`, or `a b y` → `0 0 U` |
| `E` | contention: two drivers on one net |

These are **data**, not parse failures. A verification runner must treat `U` and `E` as
**mismatches**, never as "unparseable row, skip".

### 2.5 Zero-input and empty cases — measured

| Circuit | stdout | exit | Note |
|---|---|---|---|
| 1 input pin + 1 output pin | `a y` / `0 0` / `1 1` (15 B) | 0 | baseline |
| **0 input pins, 1 output pin** | `onlyout` / `      U` (18 B) | 0 | `rowCount = 1 << 0 = 1`; "truth table" has one row |
| **0 pins at all** | two blank lines (4 B) | 0 | no header, no data |
| undefined component `NoSuchComponent` | two blank lines (4 B) | **0** | ⚠️ see §5 |
| undeclared library ordinal `lib="9"` | two blank lines (4 B) | **0** | ⚠️ see §5 |

(4 B is `13,10,13,10` — two CRLF blank lines, raw bytes read in-process.)

---

## 3. ❌ `--test-vector` is unusable headless — REFUTED

`docs\test_vector.md` documents `--test-vector` and claims "Exit with status code 0 on success,
non-zero on failure". **It cannot run headless at all.**

```
$ java '-Djava.awt.headless=true' -jar <jar> --locale en --test-vector main <vec> <circ>
exit 1
stdout: (empty)
stderr: Exception in thread "main" java.awt.HeadlessException
        at java.desktop/java.awt.GraphicsEnvironment.checkHeadless(GraphicsEnvironment.java:158)
        at java.desktop/java.awt.Window.<init>(Window.java:512)
        at java.desktop/java.awt.Frame.<init>(Frame.java:428)
        at javax.swing.SwingUtilities.getSharedOwnerFrame(SwingUtilities.java:1970)
        at javax.swing.JOptionPane.getRootFrame(JOptionPane.java:1687)
        at javax.swing.JOptionPane.showOptionDialog(JOptionPane.java:875)
        at com.cburch.logisim.gui.generic.OptionPane.showOptionDialog(OptionPane.java:196)
        at com.cburch.logisim.Main.main(Main.java:89)
```

Cause, from source:

- `Startup.java:362-363` — `Main.headless = true` is set **only** in the `--tty` / `--test-fpga`
  branch. `--test-vector` never sets it, so `Main.hasGui()` (`Main.java:94-101`) stays true.
- `Main.java:85-90` — when `startup.run()` throws, the catch block renders the stack trace and calls
  `OptionPane.showMessageDialog`, which needs a window → `HeadlessException`.
- The **real** underlying exception is therefore swallowed into a dialog that cannot open.

**Do not build anything on `--test-vector`.** Our verification path is `--tty table` (§2).

### 3b. ❌ `--new-file-format` is **also** unusable headless — REFUTED

Discovered by direct test; the round-trip procedure required by the plan (A1.4) cannot use it.

```
$ java '-Djava.awt.headless=true' -jar <jar> --locale en --new-file-format <in> <out>
exit 1
stderr: Exception in thread "main" java.awt.HeadlessException
        at java.desktop/java.awt.GraphicsEnvironment.checkHeadless(GraphicsEnvironment.java:158)
        ...
output file created: NO
```

Same root cause: `handleArgTestCircGen` (`Startup.java:800-809`) sets `exitAfterStartup = true` but
never sets `isTty`, so it takes the GUI path. **Any non-`--tty` option is effectively headless-hostile.**

> **Consequence for the plan's A1.4.** A load→re-save round trip via the CLI is **not available**.
> Substitute (weaker but real): load the file through the real loader or via `--tty stats`/`--tty table`
> and compare observable behaviour, rather than comparing re-serialised bytes.

---

## 4. ✅ `--tty` format flags — measured

`--tty` accepts a comma-separated list (`Startup.java:566-586`).

| Flag | Standalone | With `table` | Notes |
|---|---|---|---|
| `table` | ✅ exit 0 | — | exhaustive simulation, §2 |
| `stats` | ✅ exit 0 | ✅ (appends after the table) | component histogram |
| `speed` | `[UNVERIFIED]` | ✅ (129 lines, same as table) | timing to stderr |
| `binary` | ❌ **HANGS** | `[UNVERIFIED]` | timeout after 20 s, no output |
| `hex` | `[UNVERIFIED]` | `[UNVERIFIED]` | |
| `csv` | ❌ **HANGS** | `[UNVERIFIED]` | timeout after 20 s, no output |
| `tabs` | `[UNVERIFIED]` | `[UNVERIFIED]` | |
| `halt` | `[UNVERIFIED]` | `[UNVERIFIED]` | |
| `tty` | `[UNVERIFIED]` | `[UNVERIFIED]` | requires a `TTY` component, else `ttyNoTtyError` (`TtyInterface.java:502-505`) |

**Why `binary`/`csv`/`tabs` hang:** they set only the `FORMAT_TABLE_*` sub-flag and not the base
`FORMAT_TABLE` bit (`Startup.java:571-579`). `doTableAnalysis` is gated on `(format & FORMAT_TABLE) != 0`
(`TtyInterface.java:344`), so they fall through to `runSimulation`, whose main loop exits only on a
`halt` pin or oscillation (`TtyInterface.java:520-558`) — i.e. never. `binary`'s bit value is 128, so
`128 & 1 == 0`.

> **Always run `--tty` under a hard timeout.** This is a silent hang with no diagnostic.

---

## 5. ⚠️ False-pass modes and required guards

Every way a run can look successful without the circuit being correct, with the mitigation our
tooling must implement. This is the most important list in the repository.

| # | Mode | Evidence | Required guard |
|---|---|---|---|
| F1 | **Exit code carries no correctness information.** A wrong circuit exits 0. | Undefined component → exit 0; undeclared lib → exit 0; wrong gate output → exit 0. Only malformed XML / missing file give −1. | Never gate on exit code. Parse the table. |
| F2 | **A misspelled component is silently dropped**; the file loads and the table still prints, just without that part. | `00-probe-log.md` §3: `--tty stats` shows `1 1 Pin` instead of `3 3 Pin / 1 1 AND Gate`. Diagnostic exists but goes to a modal dialog (§6). | Run `--tty stats` every time and assert the component histogram equals the intended one. |
| F3 | **Empty table looks like a clean run.** No pins ⇒ 4 bytes, two blank lines, exit 0. | 0-pin circuit and undefined-component circuit both produced exactly `\n\n`. | Require ≥1 header line and the expected number of data rows; require header field count == expected pin count. |
| F4 | **Zero-row "success".** `rowCount = 1 << inputBits`; a circuit that should have N inputs but has fewer silently tests far fewer combinations. | 0-input circuit emits exactly 1 row, exit 0. | Assert `rows == 2 ** total_input_bits`, where `total_input_bits` is computed from the *specification*, not from the file. |
| F5 | **`U`/`E` are data, not errors.** A circuit that fails to connect anything prints `U` for every row and exits 0. | `a b y` → `0 0 U / 0 1 U / …` | Treat any `U` or `E` in a value column as a mismatch. |
| F6 | **Oscillation hangs instead of failing.** An unintended net merge can close a combinational loop; the simulator never converges. | `getEnd0 → (100,80)`, `(240,85)` wire never terminated (killed at 20 s); `00-probe-log.md` §7 recorded 146 s CPU. | Hard timeout on every run; a timeout is a **failure**, never a skip. |
| F7 | **Column order is geometric, so a parser keyed to declaration order silently mis-maps columns.** | Measured `r r2 p[4] q s[2] t` for a file declaring `r, r2, p, q, s, t` in that order but placed differently. | Resolve column order from geometry (y then x), or assert the header matches the expected order exactly. |
| F8 | **Value radix depends on width**, so one parser can misread wide columns. | width ≤ 6 binary, ≥ 7 `0x…` (`TtyInterface.java:195-208`); measured for 1/2/4-bit. | Parse per column using the width from the header `name[width]`. |
| F9 | **`hashCode()` tie-break** for two pins at the same point makes column order unstable. | `Location.java:186`. | Never place two pins at the same coordinate; assert the header. |
| F10 | **Tooling traps produce false results** (truncating pipe ⇒ fake exit −1; PowerShell redirection ⇒ UTF-16 + swallowed stderr). | §"Two harness traps" above. | Capture raw bytes in-process; never read pass/fail through a truncating pipeline. |
| F11 | **`--tty binary`/`csv`/`tabs` hang** and look like a slow run. | §4. | Use exactly `--tty table` (optionally `table,stats`); hard timeout. |

---

## 6. Loader diagnostics are invisible headless — new trap

`Loader.showError` (`Loader.java:575-613`) always opens a **modal Swing dialog**
(`OptionPane.showOptionDialog`). `Main.headless = true` only suppresses `Main`'s own look-and-feel
setup; `Loader` still calls `OptionPane`.

Consequence: under a plain headless loader run, **any file diagnostic either throws
`java.awt.HeadlessException` or blocks** — even though the `--tty` path appears to load the file
"successfully". Directly observed: dumping a file whose component was named `AND` (should be
`AND Gate`) threw `HeadlessException` at `Loader.java:605`.

Overriding `showError` to print instead of dialoguing reveals the real diagnostic:

```
[loader-diagnostic] component "AND" missing from library "1" [main.AND((300,100))]
```

Text keys are in `resources\logisim\strings\file\file.properties`:
`compAbsentError = component "%s" missing from library "%s"`,
`libMissingError = library "%s" not found"`, `attrValueInvalidError`, `wireStartInvalidError`, …

> **Guard:** do not assume "the CLI loaded it, so the file was valid". Validity must be established by
> `--tty stats` (§5 F2) or by a loader with `showError` overridden.

---

## 7. Component catalogue — authoritative names (for first-attempt-valid generation)

Enumerated by instantiating every registered `AddTool` in the running jar: **14 libraries,
188 component names.** The XML `name` is the **factory name** — it frequently differs from the
displayed label and **often contains a space**.

**Gates: the XML name carries the word "Gate".** `AND Gate`, `OR Gate`, `NAND Gate`, `NOR Gate`,
`XOR Gate`, `XNOR Gate`, `NOT Gate`, `Odd Parity`, `Even Parity`, `Controlled Buffer`,
`Controlled Inverter`, `PLA` — but **`Buffer`** has no suffix.

Key components for this project, with verified default attributes and port offsets
(2-input gates at default `size=50`; see `03-geometry-layout.md` §2.2):

| Library | `name` | Defaults | Ports |
|---|---|---|---|
| `Wiring` | `Pin` | `width=1 type=input behavior=simple radix=2 facing=east` | `(0,0)` |
| `Wiring` | `Tunnel` | `width=1 facing=west label=` | `(0,0)` |
| `Wiring` | `Constant` | `width=1 value=1 facing=east` | `(0,0)` |
| `Wiring` | `Clock` | `facing=east highDuration=1 lowDuration=1 phaseOffset=0` | `(0,0)` |
| `Wiring` | `Power` / `Ground` | `width=1` | `(0,0)` |
| `Wiring` | `Bit Extender` | `in_width=8 out_width=16 type=sign` | out `(0,0)`, in `(-40,0)` |
| `Gates` | `NOT Gate` | `width=1 size=30 out=01` | out `(0,0)`, in `(-30,0)` |
| `Gates` | `Buffer` | `width=1 out=01` (**no `size`**) | out `(0,0)`, in `(-20,0)` |
| `Gates` | `AND Gate` / `OR Gate` | `width=1 size=50 inputs=2 out=01 negate0/1=false` | out `(0,0)`, in `(-50,∓20)` |
| `Gates` | `NAND Gate` / `NOR Gate` | as above | out `(0,0)`, in `(-60,∓20)` |
| `Gates` | `XOR Gate` | `width=1 size=50 inputs=2 out=01 xor=1` | out `(0,0)`, in `(-60,∓20)` |
| `Gates` | `XNOR Gate` | as above | out `(0,0)`, in `(-70,∓20)` |
| `Arithmetic` | `Adder` | `[UNVERIFIED] attributes` | built-in — **must not** replace the 4-bit design |
| `Plexers` | `Multiplexer`, `Decoder`, `Priority Encoder`, `BitSelector`, `Demultiplexer` | `[UNVERIFIED]` | `[UNVERIFIED]` |
| `Memory` | `D Flip-Flop`, `Register`, `Counter`, `RAM`, `ROM`, `Shift Register` | `[UNVERIFIED]` | `[UNVERIFIED]` |
| `I/O` | `Button`, `LED`, `7-Segment Display`, `DipSwitch`, `Keyboard`, `TTY` | `[UNVERIFIED]` | `[UNVERIFIED]` |

Full 14-library / 188-name list: `scripts\dsh\out\components.txt` (regenerate with any
`AddTool`-enumerating tool).

---

## 8. ✅ Tunnel-based connectivity — the reliable construction primitive

`Tunnel` connects by **label**, not coordinate (`connectTunnels`, `CircuitWires.java:600`).
Verified by building a 1-bit full adder from five 2-input gates whose every inter-gate net is a
tunnel: **all 8 rows correct, exit 0** (`tests\circ\probe\fa2.circ`).

```
a b cin sum cout
0 0   0   0    0
0 0   1   1    0
0 1   0   1    0
0 1   1   0    1
1 0   0   1    0
1 0   1   0    1
1 1   0   0    1
1 1   1   1    1
```

This is the contrast that matters: the **same function routed with coordinate lanes and risers**
(every coordinate on the 10 grid, one lane and one riser per net) produced `E` on both outputs —
an unintended net merge. Tunnels remove the failure mode rather than making it less likely.

> **Recommended default for all generated combinational logic: route through tunnels.** Keep wires
> to short, single-purpose stubs between a port and its tunnel. See
> `03-geometry-layout.md` §5 for the exact stub pattern.

### ⚠️ Subcircuit port offsets are rescaled — do not hand-compute them

Measured: a subcircuit with input pins authored at `(100,80)`/`(100,120)` and output at `(300,100)`,
instantiated at `loc=(500,100)`, reports

```
xor2 loc=(500,100) bounds=[x=469 y=89 w=32 h=32]
     end[0] INPUT  abs=(470,100) offset=(-30,0)
     end[1] INPUT  abs=(470,110) offset=(-30,10)
     end[2] OUTPUT abs=(500,100) offset=(0,0)
```

The authored 40-px pin gap becomes a 10-px gap; only the pins' **relative order** survives.
`00-probe-log.md` §5's claim that inputs sit at "the pin's own authored coordinate" did **not**
reproduce. Also `[UNVERIFIED]`: feeding a parent `Pin` from a subcircuit output gave `E` on every
row here, cause not established.

> **Guard:** obtain instance port offsets from the loaded component, never by arithmetic on the
> subcircuit's authored pin coordinates.

---

## 9. Tooling notes for future work

Writing a tool that loads a `.circ` through the real jar is the cheapest way to turn geometry
questions into measurements. Two gotchas, both hit here:

- **Override `Loader.showError`** (`Loader.java:575`) or any file diagnostic throws
  `HeadlessException`/blocks (§6).
- **Call `System.exit(0)` at the end.** Logisim keeps non-daemon threads alive, so the JVM hangs
  after printing.
- Compile with `javac -cp <jar>`; run with
  `java -Djava.awt.headless=true -cp "<jar>;<dir>" <Tool>` and **quote the classpath** (the project
  path contains a space).

---

## 10. Open / unverified

| # | Item | Why it matters |
|---|---|---|
| U1 | 7-bit-and-wider value rendering in `--tty table` | Parser radix for wide columns (predicted `0x…` from `TtyInterface.java:205-206`, not measured) |
| U2 | `Adder`, `Multiplexer`, `Decoder`, memory and I/O attribute names/defaults/ports | Needed for P5/P10 generation |
| U3 | Exclusive `desc` strings for `#Plexers`, `#Arithmetic`, `#Memory`, `#I/O` | Required to declare `<lib>` correctly |
| U4 | Whether `Tunnel` nets require matching widths, and tunnel label validation | Would let us eliminate most routing risk |
| U5 | `Splitter` geometry and bus semantics | Needed for buses/4-bit datapaths |
| U6 | Subcircuit input-port rule (pin's own coordinate claim in `00-probe-log.md` §5) | The 4-bit adder will likely use subcircuits |
| U7 | Behaviour of `-Dstdout.encoding` / reading stdout as GBK vs UTF-8 | Only matters if `--locale` is ever omitted |
