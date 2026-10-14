#!/usr/bin/env python3
"""Verify a .circ against an INDEPENDENT golden model, using only its pin names/widths.

Why this is the right shape: `tabletool.py` verifies against a small set of named specs
(adder/identity/...). This verifier takes the *required behaviour* as a plain Python function
supplied by the spec, so it works for circuits nobody has seen before.

The golden model must be written independently of the circuit - from the requirement, not from
the implementation. It receives a dict of input values and returns a dict of output values.

Usage:
    py verify_model.py <circuit.circ> --spec <spec.json> [--max-rows N]

Spec JSON must provide either:
  * "model": a path to a .py file exposing `model(inputs: dict, widths: dict) -> dict`, or
  * "expression": a mapping {output: "python expression in the input names"}.

Exit codes: 0 = passed, 1 = mismatches, 2 = harness error (NOT a pass).
"""
from __future__ import annotations

import argparse
import io
import os
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

# --- Logisim JAR location (imports placed here so the order cannot break) ---
import os
import pathlib
_LOCAL_JAR = Path(__file__).resolve().parents[1] / "bin" / "logisim-evolution-all.jar"

JAR = pathlib.Path(os.environ.get("LOGISIM_JAR") or str(_LOCAL_JAR))
# Measured: width <= 6 renders binary, width >= 7 renders 0x-prefixed hex (TtyInterface.java:205).
HEX_MIN_WIDTH = 7
_UNDEFINED_RE = re.compile(r"^(U|E|Z|X|x)+$")


class HarnessError(Exception):
    """The run produced no usable evidence. Must never be reported as a pass."""


def decode_cli_output(raw: bytes) -> str:
    """The CLI writes UTF-16LE+BOM when stdout is redirected here; fall back to UTF-8/GBK."""
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be", errors="replace").lstrip("\ufeff")
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HarnessError("could not decode CLI output")


def simulate(circuit: Path, timeout: int = 300) -> Tuple[List[Tuple[str, int]], List[List[Optional[int]]]]:
    cmd = ["java", "-Djava.awt.headless=true", "-jar", str(JAR),
           "--locale", "en", "--tty", "table", str(circuit)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HarnessError(
            f"timed out after {timeout}s - no result (if the input space is simply too large, "
            f"this circuit cannot be verified exhaustively by --tty table)"
        )
    text = decode_cli_output(proc.stdout)
    lines = [l for l in text.replace("\r\n", "\n").split("\n") if l.strip()]
    if not lines:
        raise HarnessError(f"empty table output (returncode={proc.returncode})")

    pins: List[Tuple[str, int]] = []
    for tok in lines[0].split():
        m = re.fullmatch(r"(.+?)\[(\d+)\]", tok)
        pins.append((m.group(1), int(m.group(2))) if m else (tok, 1))
    if not pins:
        raise HarnessError("could not parse a header line")

    rows: List[List[Optional[int]]] = []
    for line in lines[1:]:
        toks = line.split()
        if len(toks) != len(pins):
            raise HarnessError(f"ragged row with {len(toks)} fields, expected {len(pins)}: {line!r}")
        row: List[Optional[int]] = []
        for tok, (_, w) in zip(toks, pins):
            if _UNDEFINED_RE.match(tok):
                row.append(None)                    # undefined: a legal RESULT, becomes a mismatch
            elif w >= HEX_MIN_WIDTH:
                if not tok.startswith("0x"):
                    raise HarnessError(f"width {w} should render hex, got {tok!r}")
                row.append(int(tok[2:], 16))
            else:
                if not re.fullmatch(r"[01]+", tok):
                    raise HarnessError(f"width {w} should render binary, got {tok!r}")
                # MSB-first, and this is MEASURED to be right for the table: reversing it BREAKS
                # working specs (mux4 falls 64->48 with 16 fails, alu4 falls 1024->369 with 655),
                # which is the cleanest possible evidence. `TtyInterface.valueFormat` renders via
                # `v.toBinaryString()`, a standard MSB-first encoding, and a 2-bit + 2-bit Adder
                # table reconciles as A + B == S + 4*Cout on 16/16 rows this way and only 9/16 the
                # other way.
                #
                # So the multi-bit INPUT-vector ordering is NOT a decode problem here; the one
                # circuit that disagrees is the Comparator, and that is recorded as its own open
                # defect (references/00-probe-log.md section 29) rather than fixed by flipping a
                # convention that four other specs depend on.
                row.append(int(tok, 2))
        rows.append(row)
    return pins, rows


def load_model(spec: dict, spec_path: Path):
    if "model" in spec:
        import importlib.util
        mp = (spec_path.parent / spec["model"]).resolve()
        if not mp.exists():
            raise HarnessError(f"model file not found: {mp}")
        s = importlib.util.spec_from_file_location("golden_model", mp)
        mod = importlib.util.module_from_spec(s)
        s.loader.exec_module(mod)
        if not hasattr(mod, "model"):
            raise HarnessError(f"{mp} does not define `model(inputs, widths)`")
        return mod.model
    if "expression" in spec:
        exprs = spec["expression"]

        def model(inputs: dict, widths: dict) -> dict:
            return {k: int(eval(v, {"__builtins__": {}}, dict(inputs))) for k, v in exprs.items()}

        return model
    raise HarnessError("spec must provide either 'model' or 'expression'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("circuit")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--max-rows", type=int, default=200000)
    args = ap.parse_args()

    circuit = Path(args.circuit)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        model = load_model(spec, Path(args.spec))
        pins, rows = simulate(circuit)
    except HarnessError as e:
        print(json.dumps({"status": "harness_error", "circuit": str(circuit),
                          "harness_error": str(e)}, indent=2))
        return 2

    names = [n for n, _ in pins]
    widths = {n: w for n, w in pins}

    def _names(field, key):
        """Accept ["A","B"] or [["A",4],["B",4]] or {"A":4,...} and return just the names."""
        v = spec.get(field)
        if v is None:
            return None
        if isinstance(v, dict):
            return list(v.keys())
        out = []
        for e in v:
            if isinstance(e, str):
                out.append(e)
            elif isinstance(e, (list, tuple)) and e:
                out.append(str(e[0]))
            else:
                raise HarnessError(f"spec field {key!r} has a bad entry {e!r}")
        return out

    in_names = _names("inputs", "inputs")
    out_names = _names("outputs", "outputs")
    if in_names is None or out_names is None:
        raise SystemExit("spec must list 'inputs' and 'outputs'")

    missing = [n for n in list(in_names) + list(out_names) if n not in names]
    if missing:
        print(json.dumps({"status": "harness_error", "circuit": str(circuit),
                          "harness_error": f"circuit is missing expected pins {missing}; "
                                           f"found {names}"}, indent=2))
        return 2

    idx = {n: i for i, n in enumerate(names)}
    total_input_bits = sum(widths[n] for n in in_names)
    expected_rows = 2 ** total_input_bits

    if len(rows) != expected_rows:
        print(json.dumps({"status": "harness_error", "circuit": str(circuit),
                          "harness_error": f"row count {len(rows)} != 2**{total_input_bits} "
                                           f"= {expected_rows}"}, indent=2))
        return 2
    if expected_rows > args.max_rows:
        print(json.dumps({"status": "harness_error", "circuit": str(circuit),
                          "harness_error": f"{expected_rows} rows exceeds --max-rows {args.max_rows}"},
                         indent=2))
        return 2

    mismatches = []
    for r, row in enumerate(rows, 1):
        inputs = {n: row[idx[n]] for n in in_names}
        if any(v is None for v in inputs.values()):
            mismatches.append({"row": r, "reason": "an INPUT is undefined", "inputs": inputs})
            continue
        want = model(inputs, widths)
        for n in out_names:
            got = row[idx[n]]
            if got != want[n]:
                mismatches.append({"row": r, "pin": n, "expected": want[n], "actual": got,
                                   "inputs": inputs})

    # `passed` and `failed` must BOTH be counted in ROWS, so that pass + fail == `rows`.
    #
    # This was wrong: `passed` counted ROWS (distinct row indices that had no mismatch) while
    # `failed` counted OUTPUT MISMATCHES. With one output they coincide, so every earlier spec looked
    # right; the first three-output circuit (the 2-bit comparator) reported `passed: 8, failed: 16`
    # out of 16 rows - arithmetically impossible, and exactly the kind of number that destroys trust
    # in a report. A row is "failed" if ANY of its outputs mismatched.
    bad_rows = {m["row"] for m in mismatches}
    result = {
        "status": "failed" if mismatches else "passed",
        "circuit": str(circuit),
        "pins": names,
        "rows": len(rows),
        "expected_rows": expected_rows,
        "passed": len(rows) - len(bad_rows),
        "failed": len(bad_rows),
        "output_mismatches": len(mismatches),
        "mismatches": mismatches[:20],
    }
    print(json.dumps(result, indent=2))
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
