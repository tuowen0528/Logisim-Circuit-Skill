# `.circ` file format — Logisim Evolution 5.1.0dev

Source-cited reference for **generating** `.circ` files that load on the first attempt.
Every claim carries a `file:line` citation against
`logisim-evolution-main\src\main\java\com\cburch\logisim\...`.
Anything I could not determine is marked `[UNVERIFIED]` and **must not** be relied on by a generator.

Companion files: `(the measured geometry is in component-catalog.json; query it with compdb)` (coordinates/connectivity), `docs/internals/CAPABILITIES.md`
(what the CLI can actually do),  (Lead's empirical probes).

---

## 1. Document skeleton

`XmlWriter.write` builds a DOM and serialises it (`XmlWriter.java:180`). The shape it emits:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<project version="1.0" source="5.1.0dev">
  <lib desc="#Wiring" name="0"/>
  <lib desc="#Gates" name="1"/>
  <main name="main"/>
  <circuit name="main">
    <comp lib="0" loc="(100,80)" name="Pin">
      <a name="label" val="a"/>
      <a name="type" val="input"/>
    </comp>
    <wire from="(100,80)" to="(240,80)"/>
  </circuit>
</project>
```

### Element inventory

| Element | Created at | Purpose |
|---|---|---|
| `project` | `XmlWriter.java:447` | document root |
| `lib` | `XmlWriter.java:379` | declares a library and its file-local ordinal |
| `main` | `XmlWriter.java:466` | names the main circuit |
| `circuit` | `XmlWriter.java:277` | one circuit / subcircuit |
| `comp` | `XmlWriter.java:370` | one component instance |
| `wire` | `XmlWriter.java:541` | one wire segment |
| `a` | `XmlWriter.java:240` | one attribute of the enclosing `comp`/`tool` |
| `tool` | `XmlWriter.java:435`, `:520` | library toolbar entry (only if a custom toolbar is defined) |
| `vhdl` | reader case `XmlReader.java:461` | VHDL entity circuit |
| `message` | reader case `XmlReader.java:546` | free-text message appended to the file |
| `options`, `mappings`, `toolbar` | reader cases `XmlReader.java:521`, `:533`, `:536` | project options / mouse mappings / toolbar |

**`project` attributes**

| Attribute | Writer | Required by reader? |
|---|---|---|
| `source` | `XmlWriter.java:458` — set to `BuildInfo.version` | No. Missing ⇒ reader falls back to the running build's version (`XmlReader.java:391-392`). Value is parsed by `LogisimVersion.fromString` (`XmlReader.java:1023`); a `-HC` suffix marks a HolyCross file (`XmlReader.java:395`). |
| `version` | `XmlWriter.java:457` — literal `"1.0"` | Not read for parsing. |

> **Generator rule:** emit `source="4.1.0"` or the current build string. Do **not** omit it if you want
> reproducible version-dependent behaviour; age-based compatibility paths trigger below
> `2.7.2` (`XmlReader.java:405`).

**`lib` attributes** — `desc` and `name` are both **mandatory**:

- `toLibrary` refuses a `<lib>` without `name` (`XmlReader.java:355-358`) or without `desc`
  (`XmlReader.java:359-362`) and shows `libNameMissingError` / `libDescMissingError`.
- `desc` is the library descriptor that selects the implementation; `name` is the ordinal used by
  `<comp lib=...>`. See §2.

**`circuit` attributes** — only `name` (`XmlWriter.java:278`). A `circuit` may also carry
`appearance` (`XmlWriter.java:310`, read at `XmlReader.java:472`).

**`comp` attributes** — `name` (mandatory), `lib` (ordinal), `loc` (mandatory):

- `name` — `XmlWriter.java:372`; missing/empty ⇒ `compNameMissingError` (`XmlCircuitReader.java:74-76`).
- `lib` — `XmlWriter.java:371`, written only when the factory's library name is known.
- `loc` — `XmlWriter.java:373`; missing/empty ⇒ `compLocMissingError` (`XmlCircuitReader.java:111-113`);
  unparsable ⇒ `compLocInvalidError` (`XmlCircuitReader.java:116-117`).

**`wire` attributes** — `from` and `to`, both mandatory
(`XmlWriter.java:542-543`; reader `XmlCircuitReader.java:138-159`, errors
`wireStartMissingError` / `wireStartInvalidError` / `wireEndMissingError` / `wireEndInvalidError`).

**`a` attributes** — `name` mandatory, `val` optional:

- `XmlWriter.java:241`, `:246`, `:251`.
- `name` missing ⇒ message `attrNameMissingError` (`XmlReader.java:127-129`).
- `val` present ⇒ used; **absent ⇒ the element's text content is used instead**
  (`XmlReader.java:133-145`). Both forms are therefore legal; the writer always uses `val`.

### Writer prolog / encoding

`XmlWriter` serialises a DOM (no hand-rolled escaping), so `&`, `<`, `>` and `"` in attribute values
are escaped by the XML serialiser. There is no custom escaping code to replicate
(`grep escape|replace(` in `XmlWriter.java` matches only unrelated lines `:238`, `:366`).

The emitted declaration matches the fixtures
(`logisim-evolution-main\src\test\resources\fpga\splitter-same-source.circ:1`):

```xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
```

> **Generator rule:** write **UTF-8 without BOM**. Attribute values are stored as text; the reader
> de-relativises only the special attribute named `filePath` (`XmlReader.java:135-142`).

---

## 2. Library ordinals: `<lib name>` ↔ `<comp lib>`

**The ordinal is a literal string used as a map key — not a positional index.**

- `toLibrary` puts `libs.put(name, ret)` keyed by the **literal** `name` attribute
  (`XmlReader.java:363-367`).
- `findLibrary` looks the `lib` attribute up by that same literal key
  (`XmlReader.java:109-113`):

```java
Library findLibrary(String libName) throws XmlReaderException {
  if (StringUtil.isNullOrEmpty(libName)) return file;
  final var ret = libs.get(libName);
  if (ret == null) throw new XmlReaderException(S.get("libMissingError", libName));
  return ret;
}
```

Consequences a generator must respect:

1. `<lib name="0"/>` + `<comp lib="0" .../>` works because both spell `0`.
2. `<lib name="7"/>` + `<comp lib="7" .../>` is **equally valid** — ordinals need not be contiguous.
3. An **empty or absent** `lib` on a `comp` resolves to the *file itself*
   (`XmlReader.java:110`), not to library 0. This is the mechanism behind `compUnknownError` /
   `"no-lib"` (`XmlCircuitReader.java:80-83`).
4. A `comp` whose `lib` matches no declared `<lib>` ⇒ `libMissingError`
   (text: `library "%s" not found`, `file.properties`), the component is not created.

`desc` selects the library. The descriptor strings are file-local display keys such as `#Wiring`,
`#Gates`, `#Base`, `#Arithmetic` (see `XmlReader.java:1238-1250`, `:1330` and the fixtures).

### Authoritative library and component names

Enumerated from the running jar by instantiating every registered `AddTool` (14 libraries,
188 component names). Full table: `docs/internals/CAPABILITIES.md` §7.

| Library `desc` | Library id | Examples of component `name` |
|---|---|---|
| `#Wiring` | `Wiring` | `Pin`, `Tunnel`, `Splitter`², `Clock`, `Constant`, `Power`, `Ground`, `Probe`, `Bit Extender`, `Transistor`, `Transmission Gate`, `Pull Resistor`, `NoConnect` |
| `#Gates` | `Gates` | `NOT Gate`, `Buffer`, `AND Gate`, `OR Gate`, `NAND Gate`, `NOR Gate`, `XOR Gate`, `XNOR Gate`, `Odd Parity`, `Even Parity`, `Controlled Buffer`, `Controlled Inverter`, `PLA` |
| `#Plexers`¹ | `Plexers` | `Multiplexer`, `Demultiplexer`, `Decoder`, `Priority Encoder`, `BitSelector` |
| `#Arithmetic`¹ | `Arithmetic` | `Adder`, `Subtractor`, `Multiplier`, `Divider`, `Comparator`, `Shifter`, `BitAdder`, `BitFinder`, `MinMax`, `Negator`, `SquareRoot`, `Exponentiator`, `Absolute`, `Binary_to_BCD_converter`, `BCD_to_7_Segment_decoder` |
| `#Memory`¹ | `Memory` | `D Flip-Flop`, `Register`, `Counter`, `RAM`, `ROM`, `Shift Register`, `Random`, `PLA`… |
| `#I/O`¹ | `I/O` | `Button`, `LED`, `7-Segment Display`, `Hex Digit Display`, `DipSwitch`, `Keyboard`, `TTY`, `Buzzer`, `Joystick`, `MatrixKeypad` |
| `#Base` | `Base` | (text/pin base tools) |

¹ Descriptor string is `[UNVERIFIED]` for these rows — only the **id** was enumerated. The `desc` for
`#Wiring`/`#Gates`/`#Base` is cited at `XmlReader.java:1238-1250`; read each library's own
`_ID`/descriptor before emitting others. **Do not guess a `desc`.**

² `Splitter` is registered by the Wiring library but written with a separate element shape; verify
before use.

> ### ⚠️ The single most dangerous trap: a wrong `name` is not fatal
>
> `getComponent` reports the problem (`component "%s" missing from library "%s"`,
> `compAbsentError` at `XmlCircuitReader.java:86-92` / `XmlReader.java:571`), but the file **still
> loads** and the remaining circuit **still simulates**. A generator that misspells
> `"AND Gate"` as `"AND"` gets a file that opens, prints a truth table, and is silently missing the
> gate. Measured in  §3:
>
> ```
> $ java ... --tty stats and1.circ     # name="AND" (wrong)
> 1  1  Pin        Wiring             # the gate is simply absent
> 1  1  TOTAL (without project's sub circuits)
> ```
>
> Mitigation is mandatory in our loop: **`--tty stats` after every generation**, and compare the
> component histogram to what was intended. See `docs/internals/CAPABILITIES.md` §5 (false-pass modes).

---

## 3. Circuits, subcircuits and `<main>`

- `<main name="X"/>` selects the main circuit (`XmlReader.java:539-540`); the writer emits it from
  `file.getMainCircuit().getName()` (`XmlWriter.java:466-467`).
- Every `<circuit name="X">` becomes a reusable subcircuit. A `<comp>` with no `lib` attribute (or an
  empty one) and `name="X"` instantiates subcircuit `X` — the empty-`lib` path in `findLibrary`
  (`XmlReader.java:110`).
- The **order of `<circuit>` elements is significant**: the reader is called out as order-sensitive
  for node ordering, and `XmlWriter.java:99`, `:168` explicitly preserve child order for
  `project`, `lib` and `toolbar`. Emit `<lib>` elements before the `<circuit>`s that reference them,
  and `<main>` before `<circuit>`.

⚠️ Subcircuit port geometry is **rescaled**, not the pins' authored coordinates. Measured for a
subcircuit with input pins at `(100,80)`/`(100,120)` and output at `(300,100)`, instantiated at
`loc=(500,100)`: ports report `(-30,0)` and `(-30,10)` relative to the instance, with the output at
`(0,0)` — the authored 40-px pin gap collapses to 10 px, and only the pins' **relative order**
survives. This contradicts the "pin's own authored coordinate" reading in  §5.
See `(the measured geometry is in component-catalog.json; query it with compdb)` §6.

> **Generator rule:** never compute subcircuit port offsets by arithmetic on the subcircuit's pin
> coordinates. Either build a flat tunnel-based netlist (`(the measured geometry is in component-catalog.json; query it with compdb)` §5 — verified
> working on a 1-bit full adder), or read the offsets off the loaded instance.

---

## 4. Attribute parsing: what the reader tolerates vs rejects

`initAttributeSet` (`XmlReader.java:116-195`) is the whole contract. The loop is driven by the
**component's own attribute list**, not by the file:

```java
for (var i = 0; true; i++) {           // refetched each iteration: splitter changes it
  final var attrList = attrs.getAttributes();
  if (i >= attrList.size()) break;
  final var attrVal = attrsDefined.get(attr.getName());
  if (attrVal == null) { ...default... }
  else {
    try { attrs.setValue(attr, attr.parse(attrVal)); }
    catch (NumberFormatException e) { messages.add(attrValueInvalidError); }
  }
}
```

| Situation | Behaviour |
|---|---|
| Attribute `name` unknown to the component | **Tolerated.** The loop iterates the component's attributes, so a file attribute that matches nothing is simply never read. |
| Attribute `name` missing on `<a>` | Message `attrNameMissingError` (`XmlReader.java:127-129`). |
| Attribute omitted, component has a default | Default applied — via `defaults.getDefaultAttributeValue(attr, ver)` (`XmlReader.java:171-180`), and only when `setDefaults` (`:153`). Special cases: `ProbeAttributes.PROBEAPPEARANCE` → `APPEAR_CLASSIC`, `StdAttr.APPEARANCE` → `APPEAR_EVOLUTION` for evolution files (`:165-175`). |
| Attribute present but value unparsable | Message `attrValueInvalidError`, value **not** set (`XmlReader.java:186-189`). |
| `val` missing but text present | Text content used as the value (`XmlReader.java:143-145`). |
| Unknown **element** under a known parent | `XmlIterator.forChildElements(parent, "a")` only visits `a`; other elements are skipped. |
| Unrecognised top-level element | `XmlReader.java:550` throws `IllegalArgumentException("Invalid node in logisim file: " + name)`. |
| `from`/`to` unparsable on a wire | `wireStartInvalidError` / `wireEndInvalidError` (`XmlCircuitReader.java:146-148`, `:157-159`). |

**Ordering caveat:** because `attrs.getAttributes()` is re-fetched each iteration to support
Splitters that mutate their own attribute list (`XmlReader.java:154-156`), the attribute order in the
file does **not** matter.

> **Message visibility trap.** Those messages are surfaced through `Loader.showError`
> (`Loader.java:575-613`), which calls `OptionPane.showOptionDialog` — a **modal Swing dialog**. Under
> a plain headless loader run this throws `java.awt.HeadlessException`; on the `--tty` path it is
> effectively invisible. Verified by dumping a file with `name="AND"`:
>
> ```
> [loader-diagnostic] component "AND" missing from library "1" [main.AND((300,100))]
> ```
>
> So the diagnostic *exists* but you will not see it on stdout. Assume a bad name fails **quietly**.

---

## 5. Declared-vs-actual: what a generator may rely on

| Rule | Status |
|---|---|
| `<lib name>` binds to `<comp lib>` **literally**, not positionally | VERIFIED — `XmlReader.java:363-367`, `:109-113` |
| `lib` and `desc` are both mandatory on `<lib>` | VERIFIED — `XmlReader.java:355-362` |
| `name`, `lib`(may be empty for subcircuits), `loc` required on `<comp>` | VERIFIED — `XmlCircuitReader.java:74-117` |
| `from`, `to` required on `<wire>` | VERIFIED — `XmlCircuitReader.java:138-159` |
| Attribute names are the component's `Attribute.getName()` values | VERIFIED — `XmlReader.java:162-163`, `XmlWriter.java:241` |
| Unknown attributes are ignored (not an error) | VERIFIED — loop is driven by the component's list |
| Unknown top-level element is a hard `IllegalArgumentException` | VERIFIED — `XmlReader.java:550` |
| Writer escapes special characters | VERIFIED by construction (DOM serialiser); no custom escape code |
| Whether *every* attribute name/type is what a generator expects | `[UNVERIFIED]` per component — enumerate from each factory's `AttributeSet`; `docs/internals/CAPABILITIES.md` §7 lists defaults for all components |
| Exact `desc` strings for `#Plexers`, `#Arithmetic`, `#Memory`, `#I/O` | `[UNVERIFIED]` — read each library's descriptor before emitting |
| `size`-dependence and `facing`/negation port rotation | geometry — see `(the measured geometry is in component-catalog.json; query it with compdb)` §2.2 |

## The `facing` rule for Tunnels — measured, and stated as a principle

`facing` does **not** change the electrical behaviour of a Tunnel: Tunnels join by **label**, so a
Tunnel connects the same way whichever direction it is set to. `facing` only decides **which side the
glyph body is drawn on**, and getting it wrong is purely a readability defect — the glyph lands on top
of the component it is attached to.

Measured on the shipped jar for every case (`geom.py show Tunnel facing=west label=Sum`):

| `facing` | glyph box | direction the body is drawn |
|---|---|---|
| `west` (and unset) | `loc + (0, -11)`, width `8*len(label)+10` | **RIGHT** of the port |
| `east` | `loc + (-w, -11)` | **LEFT** of the port |

The names read **backwards** relative to what they draw, which is why choosing a constant per role
keeps producing the wrong picture. Do not memorise the table — ask one question:

> **Where is the neighbour? Point the glyph AWAY from it.**

| the Tunnel sits on… | neighbour is on the | use |
|---|---|---|
| a gate **input** port | the **right** | `east` |
| an **output Pin** (`facing="west"`, so the pin body extends LEFT) | the **left** | `west` |
| a driver stub just right of a gate output or input Pin | the **left** | `west` |

`generator.py` and `generator.py` implement this as `tunnel_facing(neighbour_on_the_right)`, so the
question is asked at each call site rather than a constant being reused. Before that fix the output-pin
Tunnel was emitted with `east`, which drew its glyph straight over the pin body.

**Consequence:** because `facing` is cosmetic, changing it can never break a verified circuit. It also
means a wrong `facing` will pass every electrical check and every exhaustive simulation — only
`audit_visual.py` sees it.
