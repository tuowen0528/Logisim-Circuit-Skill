# Troubleshooting

## `harness_error: empty table output`

The Logisim JAR was not found, or it crashed. Check, in order:

1. Does the JAR exist and is it executable?
   `java -jar scripts/bin/logisim-evolution-all.jar --locale en --tty stats adder4.circ`
2. Is `LOGISIM_JAR` set to a real file?
3. **Is `--locale en` being passed?** The table parser reads English column headings; another locale
   produces unparsable output.

## `Error loading circuit file: <name>`

**The most common cause is a stale autosave.** If Logisim was killed while the file was open, it leaves
`.<name>.circ.autosave` next to it, and every later load tries to recover from that file and fails.

```bash
rm -f .<name>.circ.autosave      # or: Get-ChildItem -Recurse -Force -Filter '.*.autosave' | Remove-Item
```

This is not hypothetical: it made a working circuit look corrupt for several rounds of debugging,
while a byte-identical copy loaded perfectly. **Delete autosaves before concluding a file is broken.**

Other causes: a malformed XML element, or a `<lib>` ordinal mismatch (see below).

## The circuit opens but the toolbar is empty

The file has no `<toolbar>` element. `ToolbarData` starts with an empty list and only `<toolbar>`
fills it, so a file without one gives an empty toolbar and nothing can be placed.

Generated files must go through `scripts/py/project_header.py`, which applies the header and
**refuses to write** a file that would still lack it.

## Components are missing from `--tty stats`

Two causes, both silent:

1. **Wrong component XML name.** Use the real names: `NoConnect`, `BitSelector`, `SquareRoot`, `MinMax`,
   `BitAdder`, `BitFinder`, `Random`, `DualRAM`, `POR`, `J-K Flip-Flop`, `S-R Flip-Flop`.
2. **Library ordinal mismatch.** Ordinals must be dense from 0 and match:
   `0 #Wiring, 1 #Gates, 2 #Plexers, 3 #Arithmetic, 4 #FPArithmetic, 5 #Memory, 6 #I/O, 7 #TTL,
   8 #TCL, 9 #Base, 10 #BFH-Praktika, 11 #Input/Output-Extra, 12 #Soc`.
   A component under the wrong ordinal is discarded **with no error**.

Always confirm the census with `--tty stats` after generating.

## A gate input reads undefined and its output is a constant

The gate's input port offset is wrong, so the wire ends next to the port instead of on it. Offsets are
**per gate kind** and per `size`:

| gates | offset |
|---|---|
| AND, OR | `-(size + 0)` |
| NAND, NOR, XOR | `-(size + 10)` |
| XNOR | `-(size + 20)` |
| NOT | `-size` |
| Buffer | fixed `-20` |

Use `scripts/py/geom.py show "<component>" [attr=value ...]` — it returns measured coordinates, or
raises if that configuration has never been measured. **Never guess a port coordinate.**

## `ValueError: I/O operation on closed file`

A module replaced `sys.stdout` at import time, so the same buffer got wrapped twice and the inner
wrapper closed it. Every module that wraps stdout must gate the wrap on
`if __name__ == "__main__":`. This bit the project four times.

## `ValueError: invalid literal for int() with base 16: 'UU'`

`UU` is Logisim's rendering of an **undefined** value: an output is not driven. Run
`python scripts/py/netcheck.py <circuit> --netlist <netlist.json>` to find which net is broken.

`0xEE` is different — it is an **error** value (contention or an undefined input), and usually means
two drivers on one net, or a wire that ends on someone else's port.

## Windows specifics

* Paths with spaces break naive quoting. The scripts use `subprocess` with argument lists, which is
  safe; do not interpolate paths into a shell string yourself.
* **Never kill the Logisim JVM with `Stop-Process`/`kill -9` mid-run** — that is what leaves the
  autosave described above. Let it exit, or give it a timeout and clean up autosaves afterwards.
* Batch files are used for JVM runs in the tests because `Start-Process` mishandles some paths; this
  affects the tests, not the skill.

## Tests are skipped

Tests that need the JAR skip themselves when it is absent. Install the JAR, or set
`LOGISIM_JAR`, to run them. They are **skipped**, never silently passed.

## The Tunnel glyph is drawn on top of the component

`facing` is set the wrong way round. It is purely cosmetic — Tunnels connect by label, so the circuit
still simulates correctly — which is why only `audit_visual.py` catches it.

Ask **where the neighbour is** and point the glyph away from it:

| Tunnel sits on | neighbour | use |
|---|---|---|
| a gate input | right | `east` |
| an output Pin (its body extends LEFT) | left | `west` |
| a driver stub right of a gate output | left | `west` |

Measured: `west` draws the body to the **RIGHT** of the port, `east` to the **LEFT**. The names are
inverted relative to what they draw — which is exactly why the rule is stated as "away from the
neighbour" instead of as a table to memorise. `generator.py`/`generator.py` do this through
`tunnel_facing(neighbour_on_the_right)`.

## The schematic is unreadable but it simulates fine

Run the visual audit:

```bash
python scripts/py/audit_visual.py mycircuit.circ
```

`OVERLAP` and `LABEL-CLASH` are real defects. The usual cause is a long net name: a Tunnel glyph is
`8*len(label)+10` px wide, so `zero` and `n_mulhi` are very different widths and two Tunnels 10 px
apart will collide once their labels are long enough. Shorten labels or space the Tunnels further
apart along the column.
