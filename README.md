# Logisim Circuit Skill

A portable skill that lets an AI agent **analyse, generate, verify and repair Logisim Evolution
circuits** — and, critically, **prove that a generated circuit actually works** by simulating it.

The distinguishing property of this project is its verification discipline: a circuit is not accepted
because a file was produced, but because the file was **opened by the real Logisim Evolution and
simulated exhaustively** against an independent model, with a negative control that must fail.

---

## 1. What it does

| Capability | How |
|---|---|
| **Understand the `.circ` format** | Measured from the shipped jar, not guessed. `references/logisim-format.md` |
| **Know every component** | 74 components, 322 attributes, 1,402 geometry sweeps measured out of a real build. `references/component-catalog.json` |
| **Generate combinational circuits** | From a JSON spec: `scripts/py/circmodel.py build <spec> <out.circ>` |
| **Generate RTL circuits** | `scripts/py/rtllib.py build <spec> <out.circ>` |
| **Generate sequential circuits** | `scripts/py/seqmodel.py build <spec> <out.circ>` |
| **Verify by real simulation** | `scripts/py/verify_model.py <circuit> --spec <spec>` runs the official CLI and compares **every** input combination |
| **Audit structure** | `scripts/py/audit_width.py` (silent width truncation), `scripts/py/netcheck.py` (connectivity) |
| **Repair** | `scripts/py/repair.py` — a bounded search over four edit kinds |
| **Query the knowledge base** | `scripts/py/compdb.py libs\|list\|show\|ports\|check\|traps\|where\|bench` |

---

## 2. Verified vs not verified

**Read this before trusting anything.** Every number below comes from a real run of the bundled CLI.

### Verified by exhaustive simulation

| Circuit | Rows checked |
|---|---|
| 4-bit ripple-carry adder | **512 / 512** |
| 4-bit subtractor | **512 / 512** (+ 3 negative controls, all fail as required) |
| 4-bit ALU (Adder + Multiplexer) | **1024 / 1024** |
| 8-bit ALU, tunnel-wired | **1,048,576 / 1,048,576** |
| 2-bit magnitude comparator | 16 / 16 (control fails 12) |
| 4-way multiplexer | 64 / 64 |
| 1-bit full adder, **pure wires** | 8 / 8 |
| All six gate kinds, 8-bit, **pure wires** | **65,536 / 65,536** |
| 8-bit adder with `Cin` tied, **pure wires** | **65,536 / 65,536** |
| 2-bit counter, 8-bit register hold, 4-bit accumulator, divide-by-2 | clocked simulation |

Component knowledge base: **74 / 74 components, 322 attributes, 1,402 geometry sweeps**, cross-checked
against both the shipped jar and the source registration — 100%.

### NOT verified — stated plainly

* **Automatic wire routing is proven only for single-bit combinational circuits.** The router
  (`scripts/py/router.py`) produces real Manhattan wires and passes six connectivity audits, but a
  multi-bit, multi-column circuit does **not** yet verify: the 8-bit ALU re-routed to pure wires
  passes **725,572 / 1,048,576**, not all of them. The spec-driven re-route passes 466,165.
  **Use tunnel wiring (`circmodel.py`, `rtllib.py`) for anything multi-bit — it is the verified path.**
* **Component behaviour is simulated for 10 of 74 components** (the gate family). Geometry is measured
  for all 74; behaviour for Plexers, Memory and FPArithmetic is not.
* **Floating-point behaviour is unmeasured** (8/16/32/64-bit, NaN/Inf/denormal).
* `seqmodel.py` emits `JK Flip-Flop` / `SR Flip-Flop`, but the real XML names are `J-K Flip-Flop` /
  `S-R Flip-Flop`; those two components are **silently dropped** by the loader. Known, not fixed.
* `ROM` is not modelled; RAM contents cannot be preloaded from a `.circ`.
* Repair is a bounded search over four edit kinds, **not** synthesis, and covers only the `parts` spec
  format.

---

## 3. Requirements

| Requirement | Version | Needed for |
|---|---|---|
| **Python** | 3.11 or newer | all scripts — **standard library only, no `pip install`** |
| **Java (JRE)** | 17 or newer | running Logisim Evolution headlessly |
| **Logisim Evolution** | **5.1.0dev** (built from source) | the simulation used to verify |

> **The Logisim JAR is deliberately not bundled.** It is a 52 MB third-party GPL binary; shipping it
> in a Git repository is both unwieldy and carries distribution obligations. See
> `docs/installation.md` for how to obtain it and point the scripts at it.
>
> Without it, **generation and structural auditing still work**, but **`verify_model.py` cannot run** —
> and real simulation is the whole point of this skill. Install it.

Development used Python 3.14 and Java 21 on Windows 11. Other versions are untested but expected to
work for the pure-Python scripts.

---

## 4. Install

```bash
git clone <your-fork-url> Logisim-Circuit-Skill
cd Logisim-Circuit-Skill
```

Then install the Logisim JAR — see **`docs/installation.md`**, which covers three ways (prebuilt
release, building from source, or pointing at an existing install) and how the scripts find it.

### Install into an agent

Agents differ. **There is no single "skills directory" across products.**

* Agents that discover standard `SKILL.md` skills: copy this directory into that agent's skills
  location. See `docs/agent-compatibility.md` for the paths we could verify.
* Agents **without** skill discovery: there is nothing to install — hand `SKILL.md` to the model as
  context and run the scripts yourself.
* Agents that **cannot execute local processes**: use the knowledge base for analysis only; every
  generation and verification step must be run by you in a terminal.

`docs/agent-compatibility.md` marks exactly which agents were **actually tested** and which are
documentation-only. Only tested ones are called compatible.

---

## 5. First run

The package ships **no example circuits** — everything below is built from a spec you write yourself.

### 1. Does the knowledge base load?

```bash
python scripts/py/compdb.py libs
```

You should see the six target libraries. If not, see `docs/troubleshooting.md`.

### 2. Write a spec

Save this as `fulladder.json` — a 1-bit full adder, verified at 8/8:

```json
{
  "name": "main",
  "inputs": [
    "a",
    "b",
    "c"
  ],
  "outputs": [
    "S",
    "Cout"
  ],
  "gates": [
    [
      "n1",
      "XOR",
      2,
      1
    ],
    [
      "n2",
      "AND",
      2,
      1
    ],
    [
      "n3",
      "AND",
      2,
      1
    ],
    [
      "n4",
      "XOR",
      2,
      1
    ],
    [
      "n6",
      "OR",
      2,
      1
    ]
  ],
  "nets": {
    "a": {
      "drive": "in:a",
      "use": [
        [
          "n1",
          0
        ],
        [
          "n2",
          0
        ]
      ]
    },
    "b": {
      "drive": "in:b",
      "use": [
        [
          "n1",
          1
        ],
        [
          "n2",
          1
        ]
      ]
    },
    "c": {
      "drive": "in:c",
      "use": [
        [
          "n3",
          1
        ],
        [
          "n4",
          1
        ]
      ]
    },
    "x": {
      "drive": "g:n1",
      "use": [
        [
          "n4",
          0
        ],
        [
          "n3",
          0
        ]
      ]
    },
    "p": {
      "drive": "g:n2",
      "use": [
        [
          "n6",
          0
        ]
      ]
    },
    "q": {
      "drive": "g:n3",
      "use": [
        [
          "n6",
          1
        ]
      ]
    },
    "S": {
      "drive": "g:n4",
      "use": [
        "out:S"
      ]
    },
    "Cout": {
      "drive": "g:n6",
      "use": [
        "out:Cout"
      ]
    }
  },
  "expression": {
    "S": "(a ^ b ^ c) & 1",
    "Cout": "1 if (a + b + c) >= 2 else 0"
  }
}
```

The format in one sentence: `gates` entries are `[ref, KIND, n_inputs, bit_width]`; a net's `drive` is
`"in:<pin>"` for an input pin or `"g:<gate_ref>"` for a gate output; and each `use` entry is
`"out:<pin>"` for an output pin or a `[gate_ref, port_index]` pair. The `expression` block is the
**golden model** — an independent statement of what the circuit must compute, evaluated by the
verifier and compared against the real simulation.

### 3. Build it

```bash
python scripts/py/circmodel.py build fulladder.json fulladder.circ
```

### 4. Verify it against EVERY input combination

```bash
python scripts/py/verify_model.py fulladder.circ --spec fulladder.json
```

Expected:

```
status=passed  rows=8  passed=8  failed=0
```

`rows` is every combination of the input pins — 8 here, 512 for a 4-bit adder, 1,048,576 for the 8-bit
ALU. The comparison is not a sample.

### 5. Make it bigger

`references/workflows/generate.md` documents every part kind. Wider circuits are the same shape with
more gates and multi-bit pins; `rtllib.py` adds `ADDER`, `SUBTRACTOR`, `MULTIPLIER`, `MUX`, `DECODER`
and `COMPARATOR` parts for RTL-style specs.

If step 4 does not print `8/8`, the installation is wrong — `docs/troubleshooting.md` lists the
failure modes, and the first thing to check is that the Logisim JAR is found.

---

## 6. Tests

```bash
python -m unittest discover -s scripts/py
```

Some tests spawn the Logisim CLI; set the integration switches to include them:

```bash
# Linux/macOS
CIRCMODEL_INTEGRATION=1 RTLLIB_INTEGRATION=1 SEQMODEL_INTEGRATION=1 REPAIR_INTEGRATION=1 RAM_INTEGRATION=1 python -m unittest discover -s scripts/py

# Windows PowerShell
$env:CIRCMODEL_INTEGRATION=1; python -m unittest discover -s scripts/py
```

Tests that need the JAR are **skipped**, not failed, when it is absent.

---

## 7. Layout

```
SKILL.md                     the skill entry point (capabilities, workflow, resource index)
README.md                    this file
references/
  component-catalog.json     74 components, 322 attributes, 1402 geometry sweeps (measured)
  component-index.json       index + naming traps
  evidence-log.md            every measured fact, with the experiment that produced it
  logisim-format.md          verified .circ format rules
  libraries-and-components.md
  geometry-and-layout.md
  workflows/                 analysis, generate, verify, repair, sequential, build
scripts/py/                  parsing, generation, layout, validation (flat: they import each other)
scripts/py/  specs/      tests and circuit specifications
                    verified circuits
docs/                        installation, agent compatibility, troubleshooting
```

---

## 8. Things that will bite you

Three failure modes were found the hard way and are worth knowing up front. All three are documented
in full in `references/evidence-log.md`.

1. **Silent component drops.** An unknown component name, or a library ordinal that does not match the
   project header's canonical numbering, makes the loader discard the component **without any error**.
   The file opens, the table prints, the values are wrong. Canonical ordinals:
   `0 #Wiring, 1 #Gates, 2 #Plexers, 3 #Arithmetic, 4 #FPArithmetic, 5 #Memory, 6 #I/O, 7 #TTL,
   8 #TCL, 9 #Base, 10 #BFH-Praktika, 11 #Input/Output-Extra, 12 #Soc`.
2. **Stale `.autosave` poisons every later load.** If Logisim is killed mid-run it leaves
   `.<name>.circ.autosave`; afterwards **every** attempt to open that circuit fails with
   `Error loading circuit file`. Delete the autosave. This cost a long debugging session.
3. **An empty toolbar means the file has no `<toolbar>`.** `ToolbarData` starts with no entries and
   only `<toolbar>` fills it. Generated files must carry the project header; the package applies it
   automatically, and refuses to write a circuit that would still lack it.

---

## 9. Licence

No `LICENSE` file is included yet: this project depends on Logisim Evolution (GPL) and contains no
third-party code copied into it, but the intended licence has not been chosen. **Pick one before
publishing.** See `docs/installation.md` §"Licensing" for the considerations.
