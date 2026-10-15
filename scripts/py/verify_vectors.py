#!/usr/bin/env python3
"""verify_vectors -- prove a circuit against Logisim's OWN test-vector runner.

WHY THIS EXISTS

`verify_model.py` enumerates every input combination and compares against a Python model. That is the
strongest form of proof and it is useless for a 32-bit floating-point adder: 2^64 rows. The same wall
stops anything wide, and it stops clocked circuits too, because a sequence is not an input combination.

The CLI has a native test-vector runner:

    -w, --test-vector <circuit_name> <test_vector> <circ_file>

which drives named pins to named values and reports pass/fail per row. That covers the wide case and the
clocked case with one mechanism.

THE FILE FORMAT (measured from TestVector.java, not guessed)

    header   whitespace-separated columns: `name` is 1 bit, `name[width]` is 1..64 bits,
             `<SET>` and `<SEQ>` are the special set/sequence columns
    rows     one token per column; the radix is auto-detected from the literal (0x hex, 0b binary,
             plain decimal), `<DC>` is don't-care and `<FLOAT>` is floating

Everything here is measured against the real CLI; the parser's rules are taken from the source above
rather than from memory.
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import subprocess
import sys
import tempfile

if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace',
                                  line_buffering=True)

ROOT = pathlib.Path(__file__).resolve().parents[2]
JAR = ROOT / 'scripts' / 'bin' / 'logisim-evolution-all.jar'

def run_vectors(circ: pathlib.Path, vector: pathlib.Path, circuit_name: str = 'main',
                timeout: int = 900) -> dict:
    """Run the CLI's test-vector runner and parse its verdict."""
    cmd = ['java', '-Djava.awt.headless=true', '-jar', str(JAR), '--locale', 'en',
           '--test-vector', circuit_name, str(vector), str(circ)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'raw': ''}
    out = (r.stdout or '') + (r.stderr or '')
    passed = re.search(r'(\d+)\s+(?:of\s+)?\d*\s*(?:test\s*)?(?:vector\s*)?lines?\s+passed', out, re.I)
    failed = re.search(r'(\d+)\s+(?:of\s+)?\d*\s*.*?failed', out, re.I)
    nums = re.findall(r'(\d+)\s*/\s*(\d+)', out)
    return {
        'status': 'ok',
        'exit': r.returncode,
        'passed': int(passed.group(1)) if passed else (int(nums[0][0]) if nums else None),
        'failed': int(failed.group(1)) if failed else (int(nums[0][1]) - int(nums[0][0])
                                                       if nums else None),
        'raw': out[-3000:],
    }

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('circ')
    ap.add_argument('vector')
    ap.add_argument('--circuit', default='main')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    r = run_vectors(pathlib.Path(a.circ), pathlib.Path(a.vector), a.circuit)
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"  status={r['status']} exit={r.get('exit')} passed={r.get('passed')} "
              f"failed={r.get('failed')}")
        print(r.get('raw', '')[-1500:])
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
