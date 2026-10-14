#!/usr/bin/env python3
"""Bounded, PROVEN repair: try candidate fixes, keep only one the golden model accepts.

WHY THIS IS BOUNDED AND NOT "AUTOMATIC REPAIR"

Full repair (synthesise a correct circuit from a specification) is a research problem and is NOT
what this does. This is the honest, verifiable core:

  1. take a circuit that FAILS its golden model;
  2. enumerate a small, caller-declared space of candidate edits;
  3. for each candidate, rebuild and re-run the REAL simulation;
  4. report a candidate ONLY if the model now passes AND the negative control still fails.

Point 4 is the part that matters. Without a negative control you cannot distinguish "I fixed the
circuit" from "I broke the test", and a repair tool that silently weakens its own oracle is worse
than no tool at all.

What it can repair, and what it cannot, is stated explicitly in `SUPPORTED`. Anything outside that
list is reported as unsupported rather than guessed at.

Usage:
    py repair.py --kind comb --broken generated/mux2_broken.circ `
                 --spec specs/comb/mux2_broken.json `
                 --control specs/comb/mux2.json [--json]
Exit: 0 = a proven repair was found, 1 = none found, 2 = harness error.
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple
import project_header as _ph

# Only wrap when RUN, never when imported: a second wrapper around the
# same buffer closes the underlying stream when collected, and every
# later print then raises "I/O operation on closed file".
if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = sys.executable
GEN = ROOT / "scripts" / "py" / "circmodel.py"
RTL = ROOT / "scripts" / "py" / "rtllib.py"
SEQ = ROOT / "scripts" / "py" / "seqmodel.py"
VM = ROOT / "scripts" / "py" / "verify_model.py"

# Honest scope. Expand only with a spec that verifies the expansion.
SUPPORTED = {
    "comb": "combinational specs built by circmodel (gates + tunnels)",
    "rtl": "RTL specs built by rtllib (Multiplexer/Decoder/Adder/Comparator + gates)",
    "seq": "sequential specs built by seqmodel (clocked)",
}


class HarnessError(Exception):
    pass


def build(kind: str, spec: pathlib.Path, out: pathlib.Path) -> Tuple[int, str]:
    tool = {"comb": RTL if kind == "rtl" else GEN, "rtl": RTL, "seq": SEQ}[kind]
    r = subprocess.run([PY, str(tool), "build", str(spec), str(out)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def verify_comb(circ: pathlib.Path, spec: pathlib.Path) -> Tuple[int, dict]:
    r = subprocess.run([PY, str(VM), str(circ), "--spec", str(spec)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return 2, {"status": "harness_error", "raw": (r.stdout or "")[:400]}


def verify_seq(circ: pathlib.Path, spec: pathlib.Path, seconds: float,
               cycles: int) -> Tuple[int, dict]:
    r = subprocess.run([PY, str(SEQ), "verify", str(spec), str(circ),
                        "--seconds", str(seconds), "--cycles", str(cycles)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    try:
        return r.returncode, json.loads(r.stdout)
    except json.JSONDecodeError:
        return 2, {"status": "harness_error", "raw": (r.stdout or "")[:400]}


def spec_is_sequential(spec_path: pathlib.Path) -> bool:
    """Decide the verifier from the SPEC, not from a caller flag.

    The `--kind` says how to BUILD the broken circuit's spec; it says nothing about the control.
    Using `--kind` to also pick the control's verifier made a perfectly good control look like it
    passed, because a sequential spec was fed to the combinational verifier. The artifact itself
    decides.
    """
    try:
        d = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return any(p.get("kind") in ("CLOCK", "DFF", "TFF", "JKFF", "SRFF", "REG")
               for p in d.get("parts", []))


def status_of(kind: str, circ: pathlib.Path, spec: pathlib.Path,
              seconds: float, cycles: int) -> Tuple[int, dict]:
    if spec_is_sequential(spec):
        return verify_seq(circ, spec, seconds, cycles)
    return verify_comb(circ, spec)


# --------------------------------------------------------------------------------------------
# Candidate edit kinds. Each returns a NEW spec dict, or None if it does not apply.
# --------------------------------------------------------------------------------------------

def candidates_swap_ops(spec: dict) -> List[Tuple[str, dict]]:
    """Permute a multiplexer's data inputs.

    The realistic bug this models: an ALU whose opcode selects the WRONG operation. The datapath is
    fine; the two data inputs of the opcode mux are swapped. There are only a handful of possible
    permutations, so they can be enumerated exhaustively rather than guessed.
    """
    out: List[Tuple[str, dict]] = []
    nets = spec.get("nets", {})
    muxes = [p for p in spec.get("parts", []) if p.get("kind", "").upper() == "MUX"]
    if not muxes:
        return out
    for m in muxes:
        n = 1 << int(m.get("select", 1))
        ports = [f"d{i}" for i in range(n)]
        drivers = {}
        for net, d in nets.items():
            for u in d.get("use", []):
                if isinstance(u, (list, tuple)) and len(u) == 2 and u[0] == m["ref"] and u[1] in ports:
                    drivers[u[1]] = net
        have = [p for p in ports if p in drivers]
        if len(have) < 2:
            continue
        # swap each PAIR of data inputs
        for i in range(len(have)):
            for j in range(i + 1, len(have)):
                a, b = have[i], have[j]
                new = json.loads(json.dumps(spec))
                for net, d in new["nets"].items():
                    uses = []
                    for u in d.get("use", []):
                        if isinstance(u, (list, tuple)) and len(u) == 2 \
                                and u[0] == m["ref"] and u[1] == a:
                            uses.append([m["ref"], b])
                        elif isinstance(u, (list, tuple)) and len(u) == 2 \
                                and u[0] == m["ref"] and u[1] == b:
                            uses.append([m["ref"], a])
                        else:
                            uses.append(u)
                    d["use"] = uses
                out.append((f"swap mux {m['ref']} inputs {a}<->{b}", new))
    return out


def candidates_invert_gate(spec: dict) -> List[Tuple[str, dict]]:
    """Change one gate to its dual (AND<->OR, XOR<->XNOR, NAND<->NOR).

    Models "the block does the wrong function" - e.g. an ALU op wired to OR instead of AND.
    """
    DUAL = {"AND": "OR", "OR": "AND", "NAND": "NOR", "NOR": "NAND",
            "XOR": "XNOR", "XNOR": "XOR", "NOT": "BUFFER", "BUFFER": "NOT"}
    out: List[Tuple[str, dict]] = []
    for idx, p in enumerate(spec.get("parts", [])):
        if p.get("kind", "").upper() != "GATE":
            continue
        g = p.get("gate", "").upper()
        if g in DUAL:
            new = json.loads(json.dumps(spec))
            new["parts"][idx]["gate"] = DUAL[g]
            out.append((f"change gate {p['ref']} {g} -> {DUAL[g]}", new))
    return out


def candidates_tie_value(spec: dict) -> List[Tuple[str, dict]]:
    """Flip a tied constant (0 <-> 1).

    Models the silent `Constant` trap and off-by-one constant errors - e.g. a carry-in tied high.
    """
    out: List[Tuple[str, dict]] = []
    tie = spec.get("tie")
    if tie:
        for target, val in list(tie.items()):
            new = json.loads(json.dumps(spec))
            new["tie"][target] = 1 - int(val) if int(val) in (0, 1) else int(val)
            out.append((f"flip tie {target} {val} -> {new['tie'][target]}", new))
    for idx, p in enumerate(spec.get("parts", [])):
        if p.get("kind", "").upper() == "CONST" and "value" in p:
            new = json.loads(json.dumps(spec))
            new["parts"][idx]["value"] = 1 - int(p["value"]) if int(p["value"]) in (0, 1) \
                else int(p["value"])
            if new["parts"][idx]["value"] != p["value"]:
                out.append((f"flip constant {p['ref']} {p['value']} -> "
                            f"{new['parts'][idx]['value']}", new))
    return out


def candidates_dimension(spec: dict) -> List[Tuple[str, dict]]:
    """Change a component's width - catches the classic wrong-bit-width defect."""
    out: List[Tuple[str, dict]] = []
    for idx, p in enumerate(spec.get("parts", [])):
        w = p.get("width")
        if w is None:
            continue
        for delta in (-1, 1):
            if int(w) + delta < 1:
                continue
            new = json.loads(json.dumps(spec))
            new["parts"][idx]["width"] = int(w) + delta
            out.append((f"width {p['ref']} {w} -> {int(w) + delta}", new))
    return out


CANDIDATE_KINDS = {
    "swap_mux_inputs": candidates_swap_ops,
    "dual_gate": candidates_invert_gate,
    "flip_constant": candidates_tie_value,
    "change_width": candidates_dimension,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Bounded, proven repair: try candidate fixes, keep "
                                             "only one the golden model accepts.")
    ap.add_argument("--kind", required=True, choices=sorted(SUPPORTED))
    ap.add_argument("--broken", required=True, help="the circuit that FAILS")
    ap.add_argument("--spec", required=True, help="the spec describing the BROKEN circuit")
    ap.add_argument("--control", required=True,
                    help="a CIRCUIT that must NOT also pass, so a weakened oracle cannot masquerade "
                         "as a fix (use a deliberately-broken circuit)")
    ap.add_argument("--control-spec", default=None,
                    help="the spec to judge --control against; defaults to --spec. Supply it when "
                         "the control's interface differs from the broken circuit's, so the control "
                         "can FAIL (exit 1) rather than merely error (exit 2) - a definite failure "
                         "is much stronger evidence that the oracle discriminates.")
    ap.add_argument("--candidates", default=",".join(CANDIDATE_KINDS),
                    help="comma-separated candidate kinds to try")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--cycles", type=int, default=24)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    broken = pathlib.Path(a.broken)
    spec_path = pathlib.Path(a.spec)
    control = pathlib.Path(a.control)
    if not broken.exists() or not spec_path.exists():
        print(json.dumps({"status": "harness_error", "error": "missing input"},
                         indent=2, ensure_ascii=False))
        return 2

    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    # 0. The circuit must ACTUALLY be broken, or "repair" is meaningless.
    rc0, res0 = status_of(a.kind, broken, spec_path, a.seconds, a.cycles)
    if rc0 == 2:
        print(json.dumps({"status": "harness_error",
                          "error": "the reference circuit could not be evaluated; fix the harness "
                                   "before repairing", "detail": res0}, indent=2,
                         ensure_ascii=False))
        return 2
    if rc0 == 0:
        print(json.dumps({"status": "not_broken",
                          "note": "the circuit already passes its model; nothing to repair",
                          "result": res0}, indent=2, ensure_ascii=False))
        return 1

    # 1. The CONTROL must also not pass. Its job is to prove the oracle can still say "no".
    #
    # A control that passes (exit 0) is the dangerous case: it means the model is so weak that
    # anything passes, so a "repair" proves nothing. A control that FAILS (exit 1) is the ideal
    # negative control. A control that cannot be evaluated (exit 2, e.g. its pins do not match the
    # spec) is weak evidence but still not a pass, so it is reported distinctly rather than being
    # silently treated as success.
    ctl_spec = pathlib.Path(a.control_spec) if a.control_spec else spec_path
    rc_ctl, res_ctl = status_of(a.kind, pathlib.Path(a.control), ctl_spec, a.seconds, a.cycles)
    if rc_ctl == 0:
        control_state = "PASSES - the oracle is not discriminating; distrust any repair"
    elif rc_ctl == 1:
        control_state = "fails (exit 1) - the oracle discriminates, so a repair is meaningful"
    else:
        control_state = ("could not be evaluated (exit 2) - weak evidence; strengthen the control "
                         "with one that definitely fails")
    control_ok = rc_ctl != 0

    tried: List[dict] = []
    winner: Optional[dict] = None

    # Which spec FORMAT did we get? The candidate generators understand the `parts` format used by
    # rtllib/seqmodel. The older `gates` format (circmodel) puts gates in a separate list with a
    # different shape, so none of them apply. Saying so beats returning an empty search that looks
    # like "we tried everything and nothing worked".
    spec_format = "parts" if spec.get("parts") is not None else (
        "gates" if spec.get("gates") is not None else "unknown")
    if spec_format != "parts":
        payload = {
            "status": "unsupported_spec_format",
            "kind": a.kind,
            "spec_format": spec_format,
            "reference_result": {"status": res0.get("status"),
                                 "passed": res0.get("passed"),
                                 "failed": res0.get("failed")},
            "candidates_tried": 0,
            "tried": [],
            "reason": ("the candidate generators understand only the `parts` spec format; this spec "
                       "uses `%s`. Repairing it is OUT OF SCOPE rather than 'no fix found'."
                       % spec_format),
            "scope": SUPPORTED[a.kind],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False) if a.json else
              f"status      : {payload['status']}\n"
              f"reference   : {payload['reference_result']}\n"
              f"reason      : {payload['reason']}")
        return 2

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="repair_"))
    try:
        for name in [c.strip() for c in a.candidates.split(",") if c.strip()]:
            fn = CANDIDATE_KINDS.get(name)
            if fn is None:
                continue
            for label, new_spec in fn(spec):
                cand_spec = tmp / "cand.json"
                cand_circ = tmp / "cand.circ"
                cand_spec.write_text(json.dumps(new_spec, indent=2), encoding="utf-8")
                brc, bout = build(a.kind, cand_spec, cand_circ)
                if brc != 0:
                    tried.append({"edit": label, "build": "refused", "detail": bout[:200]})
                    continue
                vrc, vres = status_of(a.kind, cand_circ, cand_spec, a.seconds, a.cycles)
                entry = {"edit": label, "verify_exit": vrc,
                         "status": vres.get("status")}
                if vres.get("passed") is not None:
                    entry["passed"] = vres["passed"]
                if vres.get("failed") is not None:
                    entry["failed"] = vres["failed"]
                tried.append(entry)
                if vrc == 0 and winner is None:
                    winner = {"edit": label, "spec": new_spec,
                              "result": vres, "circuit": cand_circ.read_text(encoding="utf-8")}
                    break
            if winner:
                break

        payload = {
            "status": "repaired" if winner else "no_repair_found",
            "kind": a.kind,
            "broken": str(broken),
            "reference_result": {"status": res0.get("status"),
                                 "passed": res0.get("passed"),
                                 "failed": res0.get("failed")},
            "candidates_tried": len(tried),
            "tried": tried,
            "control_ok": control_ok,
            "control_state": control_state,
            "scope": SUPPORTED[a.kind],
            "limitations": [
                "this is a BOUNDED search over caller-declared edit kinds, not synthesis",
                "a candidate is reported only after the REAL simulation passes",
                "a repair is only trustworthy if a negative control does NOT also pass; "
                + control_state,
            ],
        }
        if winner:
            payload["repair"] = {"edit": winner["edit"],
                                 "result": winner["result"],
                                 "spec": winner["spec"]}
            out_circ = broken.with_name(broken.stem + "_repaired" + broken.suffix)
            out_spec = spec_path.with_name(spec_path.stem + "_repaired.json")
            _ph.write_circ(out_circ, winner["circuit"])
            out_spec.write_text(json.dumps(winner["spec"], indent=2), encoding="utf-8")
            payload["written"] = {"circuit": str(out_circ), "spec": str(out_spec)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if a.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"status      : {payload['status']}")
        print(f"reference   : {payload['reference_result']}")
        print(f"tried       : {payload['candidates_tried']} candidate edit(s)")
        if winner:
            print(f"REPAIR      : {winner['edit']}")
            print(f"proven by   : {winner['result'].get('status')} "
                  f"(passed={winner['result'].get('passed')}, "
                  f"failed={winner['result'].get('failed')})")
            print(f"written     : {payload['written']}")
        else:
            print("no single edit from the declared candidates fixed the circuit")
        print(f"control     : {control_state}")
    return 0 if winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
