# Installation

## 1. Python

**3.11 or newer.** Nothing to install: every script uses the standard library only.

```bash
python --version
```

## 2. Java

**17 or newer**, needed to run Logisim Evolution headlessly.

```bash
java -version
```

## 3. Logisim Evolution — required for verification

The scripts shell out to the official CLI:

```
java -jar <logisim.jar> --locale en --tty table <circuit.circ>
java -jar <logisim.jar> --locale en --tty stats <circuit.circ>
```

`--tty table` prints one row per input combination and `--tty stats` prints the component census; both
are parsed by `scripts/py/tabletool.py`. **This is the only source of simulation truth in the project.**

Version used and verified: **5.1.0dev**, built from source. The `.circ` samples in circulation are
5.0.0 and load correctly.

### Option A — build from source (what was verified)

```bash
git clone https://github.com/logisim-evolution/logisim-evolution
cd logisim-evolution
./gradlew shadowJar        # produces build/libs/logisim-evolution-*-all.jar
```

### Option B — a prebuilt release

Download a `-all.jar` from the Logisim Evolution releases page. Use the same major version if you can;
the format rules in `references/logisim-format.md` were measured on 5.1.0dev.

### Option C — point at an existing install

Any `logisim-evolution-*-all.jar` works.

### Telling the scripts where it is

The scripts look for the JAR in this order:

1. `scripts/bin/logisim-evolution-all.jar` inside this package;
2. the `LOGISIM_JAR` environment variable.

So either copy your JAR to `scripts/bin/logisim-evolution-all.jar`, or:

```bash
# Linux / macOS
export LOGISIM_JAR=/path/to/logisim-evolution-all.jar

# Windows PowerShell
$env:LOGISIM_JAR = "C:\path\to\logisim-evolution-all.jar"
```

`scripts/bin/*.jar` is in `.gitignore`, so a JAR you place there will not be committed.

### Check it

```bash
java -jar scripts/bin/logisim-evolution-all.jar --locale en --tty stats \
  adder4.circ
```

A component census means the JAR works. **Always pass `--locale en`**: the table parser depends on
English column headings.

## 4. First verification

```bash
python scripts/py/verify_model.py adder4.circ \
  --spec specs/comb/adder4.json
```

Expect `512/512`. If you see `harness_error`, the JAR is not being found — see
`docs/troubleshooting.md`.

## 5. Licensing

No `LICENSE` file ships with this package, because the intended licence has not been chosen.

What to know before choosing one:

* **No third-party source is copied into this package.** The scripts are original; the knowledge base
  is *measurements* of a third-party binary, not its code.
* **Logisim Evolution is GPL.** This package does not bundle it — the user installs it separately — so
  distributing this package does not by itself place it under the GPL.
* `references/component-catalog.json` contains measured facts (names, attribute names, coordinates),
  which are generally not copyrightable, but the intended use should still be considered.
* Pick a licence and add `LICENSE` **before** publishing.
