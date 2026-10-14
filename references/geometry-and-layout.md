# Geometry and electrical semantics — Logisim Evolution 5.1.0dev

Source-cited reference for **placing components and routing wires** so a generated `.circ`
simulates correctly. Citations are against
`logisim-evolution-main\src\main\java\com\cburch\logisim\...`.

**This is the highest-risk area of the whole generator.** Static checks cannot catch a wire that
lands on the wrong coordinate; the only reliable detector is `--tty table` showing `0`/`1` rather
than `U`/`E`, under a hard timeout (`docs\CAPABILITIES.md` §5).

Companion files: `01-file-format.md`, `docs\CAPABILITIES.md`, `00-probe-log.md`.

---

## 1. Coordinates and the grid

| Fact | Source |
|---|---|
| `Location.parse` accepts `(x,y)`, `x,y`, or `x y` (brackets optional, comma or space) | `Location.java:36-57` |
| `parse` **always** snaps: `create(x, y, true)` | `Location.java:57` |
| `create` rounds to a **multiple of 5**: `Math.round(x / 5) * 5` | `Location.java:21-24` |
| `WireIterator` advances by **10** per step | `WireIterator.java:30-35` |
| Writer emits `loc` via `Location.toString()` → `(x,y)` | `XmlWriter.java:373`, `:542-543` |

So there are **two grids**: coordinates are stored/snapped on a **5** grid, but wires are walked by
the iterator on a **10** grid. Writing coordinates on the 10 grid is the safe discipline.

### ⚠️ Off-10 coordinates can hang the simulator

A wire whose endpoints are not 10 apart in the varying axis cannot be iterated:

```java
final var offX = (destX - curX) % 10;
if (offX != 0) { destX = curX + deltaX * ((destX - curX) / 10); }   // WireIterator.java:37-40
```

Measured: a circuit whose wire ran `(100,80) → (240,85)` (Δy = 5) **never terminated** — the table
run had to be killed after 20 s, with no output. A component placed at y=85 with a wire at y=85 is
fine (that wire's Δy = 0), and a pin at an off-10 y still connects to a wire at that same y
(measured: `(105,85) → (245,85)` gave `a y / 0 0 / 1 1`).

> **Generator rule:** put every component `loc` and every wire endpoint on multiples of **10**.
> `(x,y)` coordinates with Δx or Δy equal to a non-multiple of 10 *in the varying axis* are forbidden.
> Always run probes under a hard timeout (`00-probe-log.md` §10).

---

## 2. Ports: where a wire must reach

An `Instance` exposes ports whose offset belongs to the component (`Instance.getPortLocation`,
`Instance.getPorts`). The absolute port coordinate is `loc + offset`.

### 2.1 `Pin` — port is always at `loc`

`Pin.computePorts` (`Pin.java:778-785`):

```java
String endType = attrs.isOutput() ? Port.INPUT : Port.OUTPUT;
Port port = new Port(0, 0, endType, StdAttr.WIDTH);
instance.setPorts(new Port[] {port});
```

Confirmed by dumping a loaded file: both an input pin at `(100,80)` and an output pin at `(240,80)`
report `end[0] … offset=(0,0)`.

Note the deliberate inversion on output pins: an **output** pin exposes an **INPUT** end
(`Port.INPUT`), because the pin is driven *by* the circuit it observes.

> **Generator rule:** place the pin at `loc` and end the wire exactly at `loc`.
> `facing` on a `Pin` is cosmetic for connectivity — it does **not** move the port.

### 2.2 Built-in components — offsets come from the component

Authoritative values for the components this project uses, read from the component objects
themselves (not derived by hand). `size` is the gate's `size` attribute; default is **50**.

| Component (`name`) | Default attrs | Output port | Input ports |
|---|---|---|---|
| `Pin` | `width=1` | `(0,0)` | — (single port at `loc`) |
| `Tunnel` | `width=1`, `facing=west` | `(0,0)` — type 3 = tri-state/bidirectional | — |
| `Constant` | `width=1`, `value=1` | `(0,0)` | — |
| `Power` / `Ground` | `width=1` | `(0,0)` | — |
| `NOT Gate` | `size=30` | `(0,0)` | `(-30,0)` |
| `Buffer` | *(no `size` attribute)* | `(0,0)` | `(-20,0)` |
| `AND Gate` / `OR Gate` | `size=50`, `inputs=2` | `(0,0)` | `(-50,-20)`, `(-50,+20)` |
| `NAND Gate` / `NOR Gate` | `size=50`, `inputs=2` | `(0,0)` | `(-60,∓20)` (output negation adds 10) |
| `XOR Gate` | `size=50`, `inputs=2` | `(0,0)` | `(-60,∓20)` (width 60) |
| `XNOR Gate` | `size=50`, `inputs=2` | `(0,0)` | `(-70,∓20)` |
| `XOR Gate` (3 inputs) | `size=50` | `(0,0)` | `(-60,-20)`, `(-60,0)`, `(-60,+20)` |

### ⚠️ `size` **does** drive port offsets — measured, not derived

This corrects `00-probe-log.md` §4. Varying `size` on a 2-input `AND Gate`:

```
loc=(300,100) size=30 -> bounds 30x30, inputs (270, 90)=(-30,-10)  and (270,110)=(-30,+10)
loc=(600,100) size=50 -> bounds 50x50, inputs (550, 80)=(-50,-20)  and (550,120)=(-50,+20)
loc=(900,100) size=70 -> bounds 70x70, inputs (830, 80)=(-70,-20)  and (830,120)=(-70,+20)
```

**The `00-probe-log.md` §4 numbers `(-20,∓20)` at `loc=(150,100)` are the `size=30` case, not the
default.** Reproduced exactly: `AND Gate` at `loc=(150,100)` with `size=30` gives
`bounds [x=120 y=85 w=30 h=30]`, `end[1] INPUT abs=(120,90) offset=(-30,-10)`,
`end[2] INPUT abs=(120,110) offset=(-30,+10)`; wired at those ports it produced a correct AND
truth table (`0 0 0 / 0 1 0 / 1 0 0 / 1 1 1`).

The `Buffer` observation that motivated "size does not matter" is confounded: **`Buffer` has no
`size` attribute at all**, so setting `size` on it is ignored and its input stays at `(-20,0)`.

The source formula does reproduce all of this — `getInputOffset` (`AbstractGate.java:213-271`)
computes `axisLength = size + bonusWidth (+10 if output negated)` and spaces inputs by `skipDist`
chosen from `inputs` and `size`. Read `size` from the attribute set before predicting offsets.

> **Generator rule:** never hand-derive port offsets. Either (a) fix `size` explicitly and use the
> measured table above, or (b) obtain them from the component object. Offsets change with `size`,
> `inputs`, and per-input negation.

---

## 3. Electrical connectivity — connected vs merely crossing

Connectivity is by **coincident point**, not by visual overlap. The relevant machinery:

- `CircuitWires` builds bundles from wires and components, then `connectComponents` /
  `connectWires` / `connectTunnels` / `connectPullResistors` (`CircuitWires.java:596-601`).
- Adding a wire records its endpoints as points: `points.add(comp)` (`CircuitWires.java:573-576`).
- A wire's interior points come from `WireIterator`, which yields every 10-px step **including both
  ends** (`WireIterator.java:47-59`).
- `Wire.overlaps` is **not** the connectivity rule — it is used only by `WireRepair`
  (`WireRepair.java:151`, `:182`).

Practical rules:

1. **Endpoint on endpoint** ⇒ connected.
2. **Endpoint on another wire's interior** ⇒ connected (T-junction). This is legal and sometimes
   intended — and it is the most common *accidental* merge.
3. **Interior crossing interior** ⇒ connected only if they share a point, i.e. both wires are
   orthogonal and the crossing is exactly on the 10 grid. Measured: a horizontal `y=80` run and a
   vertical `x=170` run sharing `(170,80)` both behaved consistently; do not rely on crossing wires
   staying independent.
4. **A wire endpoint landing exactly on a foreign component's port** merges that net with the
   component's net. Measured failure: a wire `(200,100) → (400,100)` ran straight through an XOR
   gate's output port at `(260,100)`, shorting input and output nets; the table then showed `E` on
   **both** outputs.

### Zero-length and degenerate wires

`Circuit.mutatorAdd` silently drops a wire whose ends are equal
(`Circuit.java:850-853`: `if (wire.getEnd0().equals(wire.getEnd1())) return;`), and
`CircuitWires.addWire` returns false for duplicates, so a duplicate wire is not added
(`CircuitWires.java:585-593`). Harmless, but do not rely on it for routing correctness.

### Duplicate components

`Circuit.isDoubleMapped` (`Circuit.java:820-836`) rejects a second component of the **same factory**
at the same location, with one exception: an input `Pin` and an output `Pin` may coexist (the check
compares `Pin.ATTR_TYPE`). Measured and used deliberately: an output `Pin` placed at a gate's output
port is the standard way to observe a gate output.

---

## 4. The routing discipline that works

Derived from the failure modes in `00-probe-log.md` §7 and extended with the port rule above.

| Rule | Why |
|---|---|
| **Route strictly orthogonally.** Never emit a diagonal wire. | A diagonal is rasterised into a staircase that can land on a foreign net's coordinate. Also the direct cause of a measured hang. |
| **One vertical channel `x` per net.** | Two nets sharing a channel segment merge. |
| **One horizontal lane `y` per net.** | Same. |
| **All coordinates on multiples of 10.** | `WireIterator` steps by 10; off-10 deltas can fail to terminate. |
| **Never place a wire endpoint on another net's port or interior.** | This is the merge mechanism. Include foreign **gate output ports** in the forbidden set — that is what broke the first full-adder attempt. |
| **Prefer routing from below/beside.** Give every riser a private x and let it rise to its target's y. A riser that only ever meets its own horizontal lane cannot merge. | Makes accidental T-junctions structurally impossible rather than merely unlikely. |
| **After every generation: `--tty stats`, then `--tty table` under a timeout.** | Catches silent component drops, `U`, `E`, and oscillation respectively. |

### ⚠️ Symptom dictionary for `--tty table`

| Output | Meaning | Action |
|---|---|---|
| `0` / `1` | connected and simulated | — |
| `U` | net not driven / wire does not reach the port | fix geometry |
| `E` | two drivers on one net (unintended merge) | find the foreign endpoint on the net |
| nothing, exit −1, CPU pinned | **oscillation** from a combinational loop | kill; make routing orthogonal and re-check for merges |

`U` and `E` are printed as ordinary data values — they are **not** parse failures, so a verification
runner must treat them as *mismatches* (`docs\CAPABILITIES.md` §5).

---

## 5. `Tunnel` — ✅ VERIFIED, and the recommended way to avoid routing entirely

`Tunnel` has a single bidirectional port at `(0,0)`, with `width` and `label` attributes. Tunnels
sharing a label are the same net regardless of geometry (`connectTunnels`,
`CircuitWires.java:600`) — verified empirically, not just from source.

**This removes routing risk structurally.** The only wires needed are short stubs between a port and
its tunnel. Measured on `tests\circ\probe\fa2.circ` (1-bit full adder built from five 2-input gates,
wired entirely through tunnels) — **correct on all 8 rows**:

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
exit 0
```

`sum` = parity of the three inputs, `cout` = majority. Compare the same function routed with
coordinate lanes and risers, which produced `E` on both outputs — an unintended merge — even though
every coordinate was on the 10 grid. **Tunnels are the safer default.**

Pattern used (per gate): an input stub runs from the input port `(loc.x-W, loc.y∓20)` to a tunnel at
`(loc.x-W-20, same y)`; the output stub runs from `(loc.x, loc.y)` to a tunnel at
`(loc.x+20, loc.y)`. Gate rows are spaced 200 apart so no two components can share a coordinate.
Consumers re-declare the same label (e.g. `<comp lib="0" loc="(640,600)" name="Tunnel"><a name="label" val="sum"/></comp>`).

`[UNVERIFIED]`: whether a tunnel's net width must match that of its peers, and whether tunnel labels
participate in VHDL label validation (`CircuitLabelValidator.java`).

## 6. Subcircuits

- `<circuit name="X">` defines a subcircuit; `<comp loc="(x,y)" name="X"/>` (with **no** `lib`
  attribute) instantiates it. The empty-`lib` path in `findLibrary` (`XmlReader.java:110`) resolves it
  against the file itself.
- The subcircuit instance carries `circuit=xor2` among its attributes.

### ⚠️ Port offsets are **rescaled**, not the pins' authored coordinates

`00-probe-log.md` §5 records subcircuit input ports as sitting at "the pin's own authored
coordinate". **Measured otherwise.** A subcircuit whose pins are authored at `x@(100,80)`,
`z@(100,120)` (inputs) and `w@(300,100)` (output), instantiated at `loc=(500,100)`:

```
xor2  loc=(500,100)  bounds=[x=469 y=89 w=32 h=32]
      attrs: circuit=xor2  facing=east  …
      end[0] INPUT  abs=(470,100)  offset=(-30,0)    <-- from input pin x@(100,80)
      end[1] INPUT  abs=(470,110)  offset=(-30,10)   <-- from input pin z@(100,120)
      end[2] OUTPUT abs=(500,100)  offset=(0,0)      <-- output pin w, at the instance loc
      port[0] type=1  abs=(470,100)
      port[1] type=1  abs=(470,110)
      port[2] type=2  abs=(500,100)
```

So the authored 40-px gap between `x` and `z` collapses to a **10-px** gap, and the ports do not sit
at `(100,80)`/`(100,120)` in the parent. The output pin's port **is** at the instance `loc`, which
does match the probe log. Note also `port[2]` carries **type=2 (OUTPUT)** on the instance while the
subcircuit's own authored pin was an output pin.

Only the **relative order** of the subcircuit's pins is preserved; the absolute offsets are
recomputed from the instance's `facing` and the subcircuit's appearance
(`SubcircuitFactory.computePorts` → `CircuitAppearance.getPortOffsets`).

> **Consequence for this project.** Do **not** hand-compute subcircuit port offsets. Either (a) build
> a flat tunnel-based netlist — verified working above — or (b) obtain instance port offsets from the
> loaded component (the subcircuit instance exposes them via `getEnds()`/`getPorts()`), exactly as
> for built-in components.

### `E` on a subcircuit output net

Wiring a parent `Pin` to a subcircuit output and simulating gave `E` on all rows even though the
ports are outside each other's bounds (instance `bounds=[x=469 y=89 w=32 h=32]`; the driving pin at
`(60,80)` reaches `(100,80)`). The cause is **not** established. `[UNVERIFIED]` — treat a subcircuit
output feeding a parent pin as needing verification before use, and prefer tunnels (§5).

---

## 7. Recommended authoring loop

1. Generate the `.circ` (UTF-8, no BOM; all coordinates on the 10 grid).
2. `--tty stats` → component histogram equals what you intended (catches silent drops).
3. `--tty table`, **under a hard timeout** → values are `0`/`1`; `U`/`E`/timeout localise the fault.
4. Only then treat the circuit as correct.

A useful extra tool: load the file through the real loader and print each component's `loc`, bounds
and every port's absolute location. That turns "where is this port?" from a guess into a
measurement, and it is how the offsets in §2.2 were obtained.

> **Caveat when writing such a tool:** it must override `Loader.showError`
> (`Loader.java:575-613`), which otherwise opens a modal dialog and throws
> `java.awt.HeadlessException` (or blocks) in a headless run. Also call `System.exit(0)` at the end —
> Logisim keeps non-daemon threads alive, so the JVM may not exit on its own.
