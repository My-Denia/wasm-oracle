#!/usr/bin/env python3
"""positive_control_m5.py — proof that every M5 PASS-capable judgment class can emit FAIL.

A green M5 run is only evidence if the comparator and judges demonstrably FIRE on deviations.
Two layers, mirroring the M1-M4 positive controls:

UNIT: feed each judge a deliberately wrong input and require the FAIL verdict —
  - integer value mismatch, float bit mismatch, NaN-class mismatch (canonical expected,
    payload NaN produced; arithmetic expected, non-NaN produced), multi-value arity mismatch;
  - trap-text mismatch and trap-instead-of-value;
  - exhaustion judged against a non-exhausting function;
  - a VALID module claimed invalid (validator must NOT reject -> runner must FAIL);
  - an invalid module with the WRONG expected text -> FAIL (category strictness);
  - a WELL-FORMED module claimed malformed -> FAIL;
  - a malformed module with the WRONG expected text -> FAIL;
  - a cleanly-instantiable module claimed uninstantiable -> FAIL.

END-TO-END: take real converted corpus files (i32, f32, float_exprs), corrupt ONE oracle
expectation (+1 on an integer, flip a float bit, replace nan:canonical by an exact non-NaN
bit pattern), run the REAL run_m5.run_file, and require: pristine FAIL==0 baseline, corrupted
FAIL>=1. This proves the wired pipeline, not just the helpers.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import run_m5 as R                          # noqa: E402
from interp5 import decoder as dec          # noqa: E402
from interp5 import machine as M            # noqa: E402
from interp5 import validator as VAL        # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


# ---- tiny builder (same encoding as tests/test_m5_machine.py) -------------------------------

def uleb(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def vec(items: list[bytes]) -> bytes:
    return uleb(len(items)) + b"".join(items)


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + uleb(len(payload)) + payload


def name(s: str) -> bytes:
    raw = s.encode("utf-8")
    return uleb(len(raw)) + raw


def code_entry(body: bytes) -> bytes:
    payload = vec([]) + body
    return uleb(len(payload)) + payload


VALID_ADD1 = (b"\x00asm\x01\x00\x00\x00"
              + section(1, vec([b"\x60" + vec([b"\x7f"]) + vec([b"\x7f"])]))
              + section(3, vec([uleb(0)]))
              + section(7, vec([name("f") + b"\x00" + uleb(0)]))
              + section(10, vec([code_entry(bytes([0x20, 0x00, 0x41, 1, 0x6A, 0x0B]))])))

MALFORMED_TRUNC = b"\x00asm\x01\x00\x00\x00" + b"\x00"          # custom id then EOF
INVALID_UNKNOWN_LOCAL = (b"\x00asm\x01\x00\x00\x00"
                         + section(1, vec([b"\x60" + vec([]) + vec([])]))
                         + section(3, vec([uleb(0)]))
                         + section(10, vec([code_entry(bytes([0x20, 0x05, 0x1A, 0x0B]))])))


def unit_controls() -> None:
    print("[unit judges]")
    ok, _ = R.compare_return([5], [{"type": "i32", "value": "6"}])
    check(not ok, "integer mismatch -> FAIL")
    ok, _ = R.compare_return([0x3F800000], [{"type": "f32", "value": str(0x3F800001)}])
    check(not ok, "float one-bit mismatch -> FAIL")
    ok, _ = R.compare_return([0x7FC12345], [{"type": "f32", "value": "nan:canonical"}])
    check(not ok, "payload NaN vs nan:canonical -> FAIL")
    ok, _ = R.compare_return([0x3F800000], [{"type": "f32", "value": "nan:arithmetic"}])
    check(not ok, "non-NaN vs nan:arithmetic -> FAIL")
    ok, _ = R.compare_return([0x7FC00000], [{"type": "f32", "value": "nan:canonical"}])
    check(ok, "canonical NaN vs nan:canonical -> PASS (bidirectional sanity)")
    ok, _ = R.compare_return([1, 2], [{"type": "i32", "value": "1"}])
    check(not ok, "multi-value arity mismatch -> FAIL")

    with tempfile.TemporaryDirectory() as td:
        wdir = Path(td)
        (wdir / "valid.wasm").write_bytes(VALID_ADD1)
        (wdir / "malformed.wasm").write_bytes(MALFORMED_TRUNC)
        (wdir / "invalid.wasm").write_bytes(INVALID_UNKNOWN_LOCAL)
        fr = R.FileResult("ctl")
        fr.total = 999                                     # identity asserted in e2e, not here
        r = R._Runner(wdir, fr)

        r.cmd_module({"type": "module", "filename": "valid.wasm", "line": 1})
        check(fr.modules_ok == 1, "valid module instantiates (baseline)")

        r.cmd_assert_return({"action": {"type": "invoke", "field": "f",
                                        "args": [{"type": "i32", "value": "1"}]},
                             "expected": [{"type": "i32", "value": "2"}], "line": 2})
        check(fr.passed == 1, "correct assert_return -> PASS (baseline)")
        r._assert_traplike({"action": {"type": "invoke", "field": "f",
                                       "args": [{"type": "i32", "value": "1"}]},
                            "text": "integer divide by zero", "line": 3}, "trap")
        check(fr.failed == 1, "value-returning call vs expected trap -> FAIL")
        r._assert_traplike({"action": {"type": "invoke", "field": "f",
                                       "args": [{"type": "i32", "value": "1"}]},
                            "text": "call stack exhausted", "line": 4}, "exhaustion")
        check(fr.failed == 2, "non-exhausting call vs assert_exhaustion -> FAIL")

        r.cmd_assert_invalid({"module_type": "binary", "filename": "valid.wasm",
                              "text": "type mismatch", "line": 5})
        check(fr.failed == 3, "VALID module claimed invalid -> FAIL (accept detected)")
        r.cmd_assert_invalid({"module_type": "binary", "filename": "invalid.wasm",
                              "text": "type mismatch", "line": 6})
        check(fr.failed == 4, "wrong invalid text (unknown local != type mismatch) -> FAIL")
        r.cmd_assert_invalid({"module_type": "binary", "filename": "invalid.wasm",
                              "text": "unknown local", "line": 7})
        check(fr.passed == 2, "right invalid text -> PASS (bidirectional sanity)")

        r.cmd_assert_malformed({"module_type": "binary", "filename": "valid.wasm",
                                "text": "unexpected end", "line": 8})
        check(fr.failed == 5, "WELL-FORMED module claimed malformed -> FAIL")
        r.cmd_assert_malformed({"module_type": "binary", "filename": "malformed.wasm",
                                "text": "length out of bounds", "line": 9})
        check(fr.failed == 6, "wrong malformed text -> FAIL")
        r.cmd_assert_malformed({"module_type": "binary", "filename": "malformed.wasm",
                                "text": "unexpected end", "line": 10})
        check(fr.passed == 3, "right malformed text -> PASS (bidirectional sanity)")

        r.cmd_assert_uninstantiable({"filename": "valid.wasm", "text": "unreachable",
                                     "line": 11})
        check(fr.failed == 7, "cleanly-instantiable claimed uninstantiable -> FAIL")


def _e2e_one(stem: str, corrupt) -> tuple[int, int]:
    """Run pristine + corrupted copies of a real converted file; return (fail0, fail1)."""
    src = ROOT / "build" / "converted_m5" / stem / f"{stem}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    fr0 = R.run_file(src)
    mutated = copy.deepcopy(data)
    corrupt(mutated)
    with tempfile.TemporaryDirectory() as td:
        # wasm files are referenced relative to the JSON: link the originals in
        tmp = Path(td) / f"{stem}.json"
        tmp.write_text(json.dumps(mutated), encoding="utf-8")
        for wasm in (src.parent).glob("*.wasm"):
            (Path(td) / wasm.name).write_bytes(wasm.read_bytes())
        fr1 = R.run_file(tmp)
    return fr0.failed, fr1.failed


def e2e_controls() -> None:
    print("[end-to-end corrupted-oracle controls]")

    def corrupt_int(data: dict) -> None:
        for c in data["commands"]:
            if c["type"] == "assert_return" and c.get("expected"):
                e = c["expected"][0]
                if e["type"] == "i32" and e["value"] != "nan:canonical":
                    e["value"] = str((int(e["value"]) + 1) & 0xFFFFFFFF)
                    return
        raise AssertionError("no i32 expected found")

    f0, f1 = _e2e_one("i32", corrupt_int)
    check(f0 == 0 and f1 >= 1, f"i32.wast: pristine FAIL=0, corrupted(+1) FAIL={f1}>=1")

    def corrupt_float(data: dict) -> None:
        for c in data["commands"]:
            if c["type"] == "assert_return" and c.get("expected"):
                e = c["expected"][0]
                if e["type"] == "f32" and not str(e["value"]).startswith("nan:"):
                    e["value"] = str(int(e["value"]) ^ 1)          # flip the LSB
                    return
        raise AssertionError("no f32 expected found")

    f0, f1 = _e2e_one("f32", corrupt_float)
    check(f0 == 0 and f1 >= 1, f"f32.wast: pristine FAIL=0, corrupted(bitflip) FAIL={f1}>=1")

    def corrupt_nan(data: dict) -> None:
        for c in data["commands"]:
            if c["type"] == "assert_return" and c.get("expected"):
                e = c["expected"][0]
                if str(e["value"]).startswith("nan:"):
                    e["value"] = str(0x3F800000 if e["type"] == "f32" else 0x3FF0000000000000)
                    return
        raise AssertionError("no nan-class expected found")

    f0, f1 = _e2e_one("float_exprs", corrupt_nan)
    check(f1 > f0, f"float_exprs.wast: NaN-class expectation corrupted -> FAIL rises "
                   f"({f0} -> {f1})")


def main() -> int:
    unit_controls()
    e2e_controls()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nall M5 positive controls passed — every judgment class can FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
