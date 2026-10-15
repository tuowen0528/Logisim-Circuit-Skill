#!/usr/bin/env python3
"""tabletool - exhaustive Logisim Evolution simulation + independent golden-model verification.

This module is the project's evidence base. Its governing rule is that a verification
harness which cannot fail is worthless, so every ambiguous condition is classified as a
harness error rather than as success.

Mechanism
---------
`java -Djava.awt.headless=true -jar <jar> --locale en --tty table <circ>` makes the official
Logisim CLI load a circuit and print a complete truth table: one row per input combination.
No test-vector file is involved. `--test-vector` is deliberately NOT used (it is unusable
headlessly: `Startup.parseArgs` sets `Main.headless` only for `--tty`/`--test-fpga`).

Empirically established table format (see docs/internals/CAPABILITIES.md)
--------------------------------------------------------------
* Header: one token per Pin, in declaration order. A multi-bit pin renders as ``A[4]``;
  a 1-bit pin renders as bare ``a``. Fields are whitespace-separated and right-padded.
* Rows: one per input combination, emitted in ASCENDING counting order with the FIRST
  header pin as the most-significant field.
* Values are rendered by pin width, per pin:
    - width 1..6  -> binary, exactly `width` characters   (e.g. ``0011``, ``00000``)
    - width >= 7  -> hexadecimal, ``0x`` + lowercase, padded to ceil(width/4) digits
                     (e.g. width 7 -> ``0x00``; width 8 -> ``0x00``; width 16 -> ``0x0000``)
* A non-binary/high-impedance value renders as a letter such as ``U`` (undefined).
  Such a value is a legal simulation RESULT and is parsed as ``None``, so that
  verification reports it as a mismatch instead of crashing.

Exit codes (do NOT trust them as a correctness signal)
------------------------------------------------------
Measured: a circuit with correct syntax but WRONG behaviour exits 0, as does a circuit
whose component name is unknown (which prints an EMPTY table). Non-zero exit appears only
for gross failures (unreadable/malformed file). Therefore correctness is decided solely by
parsing the table, never by the exit code.

All file writes use UTF-8 without BOM; all CLI invocations pin `--locale en` and
`-Djava.awt.headless=true` (this host's JVM defaults to locale `zh_CN` and is not headless).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

# tabletool.py lives at <root>/scripts/py/tabletool.py
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_JAR = os.path.join(_PROJECT_ROOT, "scripts", "bin", "logisim-evolution-all.jar")

# Guards against accidentally enumerating an astronomically large input space
# (2^48 rows would never terminate). Callers can raise it explicitly.
DEFAULT_MAX_ROWS = 5_000_000
DEFAULT_TIMEOUT_S = 900

# --------------------------------------------------------------------------------------
# Result classification
# --------------------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_SIM_ERROR = "sim_error"
STATUS_HARNESS_ERROR = "harness_error"

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"

# A rendered value that is not a valid literal for its declared width. Logisim uses
# letters for unknown/high-impedance/error values (e.g. 'U').
_UNDEFINED_VALUE_RE = re.compile(r"^[UZXEuxze]+$")

class TableParseError(Exception):
    """The CLI output could not be turned into a trustworthy table."""

@dataclass
class Pin:
    """One column of the truth table, as declared by the circuit's Pin label/width."""

    name: str
    width: int

    def max_value(self) -> int:
        return (1 << self.width) - 1

@dataclass
class SimResult:
    """Structured outcome of one `--tty table` run."""

    status: str
    command: List[str]
    circuit: str
    returncode: Optional[int] = None
    pins: List[Pin] = field(default_factory=list)
    input_pins: List[Pin] = field(default_factory=list)
    output_pins: List[Pin] = field(default_factory=list)
    # Each row maps pin name -> int, or None for an undefined/high-impedance value.
    rows: List[Dict[str, Optional[int]]] = field(default_factory=list)
    expected_rows: Optional[int] = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "circuit": self.circuit,
            "command": self.command,
            "returncode": self.returncode,
            "pins": [{"name": p.name, "width": p.width} for p in self.pins],
            "inputs": [p.name for p in self.input_pins],
            "outputs": [p.name for p in self.output_pins],
            "row_count": len(self.rows),
            "expected_rows": self.expected_rows,
            "rows": [
                {k: v for k, v in row.items()} for row in self.rows
            ],
            "problems": self.problems,
            "raw_stdout": self.raw_stdout,
            "raw_stderr": self.raw_stderr,
        }

@dataclass
class VerifyResult:
    """Structured outcome of comparing a simulated table to a golden model."""

    status: str
    spec: str
    passed: Optional[int] = None
    failed: Optional[int] = None
    expected_vectors: Optional[int] = None
    mismatches: List[dict] = field(default_factory=list)
    sim: Optional[SimResult] = None
    harness_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "spec": self.spec,
            "passed": self.passed,
            "failed": self.failed,
            "expected_vectors": self.expected_vectors,
            "mismatches": self.mismatches,
            "harness_error": self.harness_error,
            "sim": self.sim.to_dict() if self.sim else None,
        }

# --------------------------------------------------------------------------------------
# Header / value parsing
# --------------------------------------------------------------------------------------

_HEADER_TOKEN_RE = re.compile(r"^(?P<name>.+?)\[(?P<width>\d+)\]$")

def parse_header(header_line: str) -> List[Pin]:
    """Parse a header line into Pins. Raises TableParseError if it is not well-formed."""
    tokens = header_line.split()
    if not tokens:
        raise TableParseError("header line is empty")
    pins: List[Pin] = []
    seen = set()
    for token in tokens:
        m = _HEADER_TOKEN_RE.match(token)
        if m:
            name = m.group("name")
            width = int(m.group("width"))
        else:
            name, width = token, 1
        if not name:
            raise TableParseError(f"header token {token!r} has an empty pin name")
        if width < 1:
            raise TableParseError(f"header token {token!r} has non-positive width {width}")
        if name in seen:
            raise TableParseError(f"duplicate pin name {name!r} in header")
        seen.add(name)
        pins.append(Pin(name=name, width=width))
    return pins

def parse_value(text: str, width: int) -> Optional[int]:
    """Parse one rendered value. Returns None for undefined/high-impedance renderings.

    Raises TableParseError when the text cannot be a legal rendering for `width`.
    """
    if _UNDEFINED_VALUE_RE.match(text):
        return None
    if width >= 7:
        # Hexadecimal, '0x' + lowercase hex padded to ceil(width/4) digits.
        # Threshold is 7, NOT 5: TtyInterface.java:205 renders `width <= 6` as binary and
        # anything wider as hex. Measured independently by two team members
        # (widths 4,5,6 -> binary binaries like `00000`; widths 7,8 -> `0x00`).
        if not text.startswith("0x"):
            raise TableParseError(
                f"value {text!r} for width {width} is not hex with '0x' prefix"
            )
        digits = text[2:]
        expected_digits = (width + 3) // 4
        if not digits or not re.fullmatch(r"[0-9a-f]+", digits):
            raise TableParseError(f"value {text!r} for width {width} has invalid hex digits")
        if len(digits) != expected_digits:
            raise TableParseError(
                f"value {text!r} for width {width} has {len(digits)} hex digits, "
                f"expected {expected_digits}"
            )
        value = int(digits, 16)
    else:
        # Binary, exactly `width` characters.
        if not re.fullmatch(r"[01]+", text):
            raise TableParseError(
                f"value {text!r} for width {width} is not a binary literal"
            )
        if len(text) != width:
            raise TableParseError(
                f"value {text!r} for width {width} has {len(text)} bits, expected {width}"
            )
        value = int(text, 2)
    if value > (1 << width) - 1:
        raise TableParseError(f"value {text!r} exceeds width {width}")
    return value

def parse_table(stdout: str, *, filename: str = "<stdout>") -> Tuple[List[Pin], List[Dict[str, Optional[int]]]]:
    """Parse the CLI's truth-table output into (pins, rows).

    Raises TableParseError on any structural problem. Never returns a partial table:
    a partially understood table must not be trusted.
    """
    lines = stdout.splitlines()
    # Tolerate leading/trailing blank lines; the first non-blank line is the header.
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        raise TableParseError("output contains no non-blank header line")
    header_line = lines[index]
    pins = parse_header(header_line)
    index += 1

    rows: List[Dict[str, Optional[int]]] = []
    for lineno, line in enumerate(lines[index:], start=index + 1):
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) != len(pins):
            raise TableParseError(
                f"{filename}:{lineno}: expected {len(pins)} fields, found {len(tokens)}: {line!r}"
            )
        row: Dict[str, Optional[int]] = {}
        for pin, token in zip(pins, tokens):
            row[pin.name] = parse_value(token, pin.width)
        rows.append(row)

    if not rows:
        raise TableParseError("header parsed but the table contains no data rows")
    return pins, rows

# --------------------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------------------

def build_command(jar: str, circuit: str) -> List[str]:
    """Build the exact, mandated CLI invocation."""
    return [
        "java",
        "-Djava.awt.headless=true",
        "-jar",
        jar,
        "--locale",
        "en",
        "--tty",
        "table",
        circuit,
    ]

def simulate(
    circuit: str,
    *,
    jar: str = DEFAULT_JAR,
    expect_rows: Optional[int] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> SimResult:
    """Run the official CLI and parse the exhaustive truth table.

    Classification:
      * sim_error     - the CLI exited non-zero (gross failure: unreadable/malformed file)
      * harness_error - anything else that prevents a trustworthy table: missing file,
                        empty output, unparsable header/rows, an undefined INPUT value,
                        or an unexpected row count
      * ok            - a complete, well-formed table

    An `ok` status never depends on the exit code alone, and a row-count mismatch is
    downgraded to harness_error so that a truncated table cannot masquerade as success.
    """
    circuit = str(circuit)
    command = build_command(jar, circuit)
    result = SimResult(status=STATUS_HARNESS_ERROR, command=command, circuit=circuit)

    if not os.path.isfile(circuit):
        result.problems.append(f"circuit file does not exist: {circuit}")
        return result
    if not os.path.isfile(jar):
        result.problems.append(f"logisim jar does not exist: {jar}")
        return result

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=_PROJECT_ROOT,
        )
    except subprocess.TimeoutExpired:
        result.problems.append(
            f"simulation timed out after {timeout_s}s (input space may be too large)"
        )
        return result
    except OSError as exc:
        result.problems.append(f"failed to launch the CLI: {exc}")
        return result

    result.returncode = proc.returncode
    result.raw_stdout = proc.stdout or ""
    result.raw_stderr = proc.stderr or ""

    # A non-zero exit is a gross failure. Note that exit 0 does NOT imply correctness.
    if proc.returncode != 0:
        result.status = STATUS_SIM_ERROR
        result.problems.append(
            f"CLI exited with code {proc.returncode}; stderr: {result.raw_stderr.strip()[:400]}"
        )
        return result

    try:
        pins, rows = parse_table(result.raw_stdout, filename=os.path.basename(circuit))
    except TableParseError as exc:
        result.status = STATUS_HARNESS_ERROR
        result.problems.append(f"could not parse truth table: {exc}")
        return result

    result.pins = pins
    result.rows = rows

    # Split columns into the input prefix and the output suffix.
    #
    # Pins are emitted in declaration order with inputs before outputs, so the split point
    # is the number of input pins. Logisim enumerates ONLY input combinations, so:
    #
    #     row_count == 2 ** (sum of input widths)
    #
    # This is an exact arithmetic constraint, not a heuristic: we search for the unique
    # prefix length satisfying it. Using value variation instead would be wrong -- a
    # single-bit output legitimately takes only the two values 0 and 1, and would be
    # indistinguishable from an input.
    total_bits = sum(p.width for p in pins)
    if total_bits >= 64:
        result.status = STATUS_HARNESS_ERROR
        result.problems.append(f"declared pin widths total {total_bits} bits; refusing to reason")
        return result

    candidates = [n for n in range(1, len(pins)) if (1 << sum(p.width for p in pins[:n])) == len(rows)]
    if len(candidates) != 1:
        result.status = STATUS_HARNESS_ERROR
        if not candidates:
            result.problems.append(
                f"no input/output split explains the table: {len(rows)} rows do not equal "
                f"2**(sum of any input prefix widths) for pins "
                f"{[p.name for p in pins]}"
            )
        else:
            result.problems.append(
                f"ambiguous input/output split: {len(candidates)} prefixes "
                f"({candidates}) all yield {len(rows)} rows"
            )
        return result

    split = candidates[0]
    result.input_pins = pins[:split]
    result.output_pins = pins[split:]
    total_input_bits = sum(p.width for p in result.input_pins)
    theoretical = 1 << total_input_bits
    result.expected_rows = theoretical

    if theoretical > max_rows:
        result.status = STATUS_HARNESS_ERROR
        result.problems.append(
            f"input space is 2^{total_input_bits} = {theoretical} rows, above "
            f"--max-rows {max_rows}"
        )
        return result

    # An undefined INPUT means the circuit's inputs were not driven as expected; the
    # table is not usable evidence.
    for row_index, row in enumerate(rows, start=1):
        for pin in result.input_pins:
            if row[pin.name] is None:
                result.status = STATUS_HARNESS_ERROR
                result.problems.append(
                    f"row {row_index}: input pin {pin.name!r} rendered undefined; "
                    "inputs must be fully driven"
                )
                return result

    if expect_rows is not None and len(rows) != expect_rows:
        result.status = STATUS_HARNESS_ERROR
        result.problems.append(
            f"expected {expect_rows} rows but the table has {len(rows)}"
        )
        return result

    result.status = STATUS_OK
    return result

def _is_input(pin: Pin, rows: Sequence[Mapping[str, Optional[int]]]) -> bool:
    """Whether a column varies across rows. Used only for reporting, not for the split."""
    values = {row[pin.name] for row in rows}
    return len(values) > 1

# --------------------------------------------------------------------------------------
# Golden models (independent of the circuit under test)
# --------------------------------------------------------------------------------------

Spec = Callable[[Mapping[str, int], Mapping[str, Pin]], Mapping[str, int]]

def _pick(inputs: Mapping[str, int], name: str) -> int:
    if name not in inputs:
        raise KeyError(f"spec requires input pin {name!r}, which the circuit does not have")
    return int(inputs[name])

def spec_identity(inputs: Mapping[str, int], pins: Mapping[str, Pin]) -> Mapping[str, int]:
    """y = a. First positive control."""
    return {"y": _pick(inputs, "a")}

def spec_not(inputs: Mapping[str, int], pins: Mapping[str, Pin]) -> Mapping[str, int]:
    """y = ~a truncated to the input width. Used as the negative control's true function."""
    width = pins["a"].width
    return {"y": (~_pick(inputs, "a")) & ((1 << width) - 1)}

def spec_adder(inputs: Mapping[str, int], pins: Mapping[str, Pin]) -> Mapping[str, int]:
    """{Cout,S} = A + B + Cin, computed with plain integer arithmetic.

    Widths are taken from the declared pin widths, so this is reusable for any width.
    """
    a = _pick(inputs, "A")
    b = _pick(inputs, "B")
    cin = _pick(inputs, "Cin")
    total = a + b + cin
    s_width = pins["S"].width if "S" in pins else max(pins["A"].width, pins["B"].width)
    return {"S": total & ((1 << s_width) - 1), "Cout": (total >> s_width) & 1}

def spec_passthrough(inputs: Mapping[str, int], pins: Mapping[str, Pin]) -> Mapping[str, int]:
    """A[4] -> S[4]: the identity on a multi-bit bus. Second positive control."""
    return {"S": _pick(inputs, "A")}

BUILTIN_SPECS: Dict[str, Spec] = {
    "identity": spec_identity,
    "not": spec_not,
    "adder": spec_adder,
    "passthrough": spec_passthrough,
}

def load_spec(spec_name: str) -> Spec:
    """Resolve a spec by built-in name, or by ``module.py:function`` path."""
    if spec_name in BUILTIN_SPECS:
        return BUILTIN_SPECS[spec_name]
    if ":" in spec_name:
        module_path, _, func_name = spec_name.partition(":")
        import importlib.util

        if not os.path.isfile(module_path):
            raise FileNotFoundError(f"spec module not found: {module_path}")
        spec = importlib.util.spec_from_file_location("_tabletool_user_spec", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load spec module: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        func = getattr(module, func_name, None)
        if func is None:
            raise AttributeError(f"{module_path} has no attribute {func_name!r}")
        return func
    raise ValueError(
        f"unknown spec {spec_name!r}; built-ins: {sorted(BUILTIN_SPECS)}; "
        "or use 'path/to/spec.py:function'"
    )

# --------------------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------------------

def verify(
    circuit: str,
    spec_name: str,
    *,
    jar: str = DEFAULT_JAR,
    expect_rows: Optional[int] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> VerifyResult:
    """Simulate `circuit` and compare every row against an independent golden model.

    Status is one of:
      * harness_error - the simulation or the comparison could not be trusted
      * failed        - at least one row mismatched, or the row count was wrong
      * passed        - every row matched AND the row count equalled the expected count
    """
    sim = simulate(
        circuit, jar=jar, expect_rows=expect_rows, timeout_s=timeout_s, max_rows=max_rows
    )
    if not sim.ok:
        reason = "; ".join(sim.problems) or f"simulation status {sim.status}"
        return VerifyResult(
            status=STATUS_HARNESS_ERROR,
            spec=spec_name,
            harness_error=reason,
            sim=sim,
        )

    try:
        spec = load_spec(spec_name)
    except Exception as exc:  # noqa: BLE001 - surfaced as a harness error by design
        return VerifyResult(
            status=STATUS_HARNESS_ERROR,
            spec=spec_name,
            harness_error=f"could not load spec: {exc}",
            sim=sim,
        )

    pins_by_name = {p.name: p for p in sim.pins}
    rows = sim.rows
    expected_vectors = sim.expected_rows if sim.expected_rows is not None else len(rows)

    mismatches: List[dict] = []
    passed = 0

    for row_index, row in enumerate(rows, start=1):
        inputs = {p.name: row[p.name] for p in sim.input_pins}
        # Inputs are guaranteed non-None by simulate(); assert rather than assume.
        if any(v is None for v in inputs.values()):
            return VerifyResult(
                status=STATUS_HARNESS_ERROR,
                spec=spec_name,
                harness_error=f"row {row_index} has an undefined input after all",
                sim=sim,
            )
        try:
            expected = spec(inputs, pins_by_name)
        except Exception as exc:  # noqa: BLE001 - a broken spec is a harness error
            return VerifyResult(
                status=STATUS_HARNESS_ERROR,
                spec=spec_name,
                harness_error=f"golden model raised on row {row_index}: {exc}",
                sim=sim,
            )

        row_ok = True
        for name, want in expected.items():
            if name not in pins_by_name:
                return VerifyResult(
                    status=STATUS_HARNESS_ERROR,
                    spec=spec_name,
                    harness_error=(
                        f"golden model produced output {name!r}, which is not a pin in the "
                        "circuit"
                    ),
                    sim=sim,
                )
            got = row[name]
            width = pins_by_name[name].width
            want_masked = int(want) & ((1 << width) - 1)
            if got is None:
                row_ok = False
                mismatches.append(
                    {
                        "row": row_index,
                        "inputs": {k: _hex(v, pins_by_name[k].width) for k, v in inputs.items()},
                        "output": name,
                        "expected": _hex(want_masked, width),
                        "actual": "undefined (high-impedance/unknown)",
                        "reason": "output is not driven to a defined logic level",
                    }
                )
            elif got != want_masked:
                row_ok = False
                mismatches.append(
                    {
                        "row": row_index,
                        "inputs": {k: _hex(v, pins_by_name[k].width) for k, v in inputs.items()},
                        "output": name,
                        "expected": _hex(want_masked, width),
                        "actual": _hex(got, width),
                        "reason": "value mismatch",
                    }
                )
        if row_ok:
            passed += 1

    failed = len(rows) - passed
    if failed > 0:
        return VerifyResult(
            status=STATUS_FAILED,
            spec=spec_name,
            passed=passed,
            failed=failed,
            expected_vectors=expected_vectors,
            mismatches=mismatches,
            sim=sim,
        )

    # Row-count guard: zero mismatches over the WRONG number of rows is not a pass.
    if len(rows) != expected_vectors:
        return VerifyResult(
            status=STATUS_FAILED,
            spec=spec_name,
            passed=passed,
            failed=failed,
            expected_vectors=expected_vectors,
            mismatches=[],
            sim=sim,
            harness_error=(
                f"no row mismatched, but {len(rows)} rows were checked while "
                f"{expected_vectors} were expected"
            ),
        )

    return VerifyResult(
        status=STATUS_PASSED,
        spec=spec_name,
        passed=passed,
        failed=failed,
        expected_vectors=expected_vectors,
        mismatches=mismatches,
        sim=sim,
    )

def _hex(value: int, width: int) -> str:
    digits = max(1, (width + 3) // 4)
    return f"0x{value:0{digits}x}"

# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _write_json(payload: dict, path: Optional[str]) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    if path:
        # UTF-8 without BOM.
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
    print(text)

def _cmd_simulate(args: argparse.Namespace) -> int:
    sim = simulate(
        args.circuit,
        jar=args.jar,
        expect_rows=args.expect_rows,
        timeout_s=args.timeout,
        max_rows=args.max_rows,
    )
    _write_json(sim.to_dict(), args.json_out)
    if sim.status == STATUS_OK:
        return 0
    return 2 if sim.status == STATUS_HARNESS_ERROR else 1

def _cmd_verify(args: argparse.Namespace) -> int:
    result = verify(
        args.circuit,
        args.spec,
        jar=args.jar,
        expect_rows=args.expect_rows,
        timeout_s=args.timeout,
        max_rows=args.max_rows,
    )
    _write_json(result.to_dict(), args.json_out)
    if result.status == STATUS_PASSED:
        return 0
    return 2 if result.status == STATUS_HARNESS_ERROR else 1

def _cmd_list_specs(_: argparse.Namespace) -> int:
    for name in sorted(BUILTIN_SPECS):
        print(name)
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tabletool",
        description=(
            "Exhaustive Logisim Evolution simulation and independent golden-model "
            "verification."
        ),
    )
    parser.add_argument(
        "--jar",
        default=DEFAULT_JAR,
        help=f"path to the Logisim CLI jar (default: {DEFAULT_JAR})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("circuit", help="path to the .circ file")
        p.add_argument(
            "--expect-rows",
            type=int,
            default=None,
            help="fail unless the table has exactly this many rows",
        )
        p.add_argument(
            "--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="subprocess timeout (s)"
        )
        p.add_argument(
            "--max-rows",
            type=int,
            default=DEFAULT_MAX_ROWS,
            help="refuse to enumerate more than this many rows",
        )
        p.add_argument("--json-out", default=None, help="also write the JSON result here")

    p_sim = sub.add_parser("simulate", help="run the CLI and parse the truth table")
    add_common(p_sim)
    p_sim.set_defaults(func=_cmd_simulate)

    p_ver = sub.add_parser("verify", help="compare the table against a golden model")
    add_common(p_ver)
    p_ver.add_argument(
        "--spec",
        required=True,
        help="built-in spec name, or 'path/to/spec.py:function'",
    )
    p_ver.set_defaults(func=_cmd_verify)

    p_list = sub.add_parser("list-specs", help="list built-in golden models")
    p_list.set_defaults(func=_cmd_list_specs)

    return parser

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
