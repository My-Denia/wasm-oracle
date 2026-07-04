#!/usr/bin/env python3
"""positive_control.py — prove the comparator FIRES on a wrong answer (M1.5 gate, 防复发).

A green M1 run must be evidence the comparator works, not that it silently passes. This test
deliberately feeds WRONG oracle values and confirms the runner classifies them FAIL — mirroring
the const.wast positive control that proved the M0 purity gates fire. If any injected-wrong case
is reported PASS, the comparator is broken and this script exits nonzero.

Two levels:
  1. unit — interp.runner.compare_return / trap_matches must reject a mismatch.
  2. end-to-end — take a REAL converted module + assertion, corrupt one `expected` value, and run
     it through scripts/run_m1.run_file; the corrupted run must yield FAIL>=1 while the pristine
     run yields FAIL==0 (control, so we know the harness isn't just always-failing).

Reproduce (after scripts/convert.py):  python3 tests/positive_control.py
Exit 0 = comparator fires on wrong answers AND passes correct ones; 1 = comparator broken / setup missing.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from interp import runner as R                 # noqa: E402
import run_m1                                   # noqa: E402

CONVERTED = ROOT / "build" / "converted"


def _unit_level() -> list[str]:
    fails = []
    # correct return -> PASS
    ok, _ = R.compare_return([3], [{"type": "i32", "value": "3"}])
    if not ok:
        fails.append("compare_return rejected a CORRECT value")
    # wrong return -> must FAIL
    ok, _ = R.compare_return([4], [{"type": "i32", "value": "3"}])
    if ok:
        fails.append("compare_return ACCEPTED a wrong value (4 vs 3)")
    # arity mismatch -> must FAIL
    ok, _ = R.compare_return([3, 3], [{"type": "i32", "value": "3"}])
    if ok:
        fails.append("compare_return ACCEPTED an arity mismatch")
    # trap kind matching
    if not R.trap_matches("integer divide by zero", "integer divide by zero"):
        fails.append("trap_matches rejected an identical kind")
    if R.trap_matches("integer overflow", "integer divide by zero"):
        fails.append("trap_matches ACCEPTED a mismatched trap kind")
    return fails


def _end_to_end() -> list[str]:
    fails = []
    cj = CONVERTED / "int_literals" / "int_literals.json"
    if not cj.exists():
        return [f"setup missing: {cj} (run scripts/convert.py first)"]
    data = json.loads(cj.read_text(encoding="utf-8"))

    module_cmd = next(c for c in data["commands"] if c.get("type") == "module")
    areturn = next(c for c in data["commands"]
                   if c.get("type") == "assert_return" and c.get("expected")
                   and c["expected"][0].get("value") is not None)

    def write_and_run(cmds, tag) -> "run_m1.FileResult":
        tmp = cj.parent / f"_positive_control_{tag}.json"
        tmp.write_text(json.dumps({"source_filename": f"positive_control_{tag}", "commands": cmds}),
                       encoding="utf-8")
        try:
            return run_m1.run_file(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    # control: pristine module + assertion -> 0 FAIL, >=1 PASS
    clean = write_and_run([module_cmd, areturn], "clean")
    if clean.failed != 0 or clean.passed < 1:
        fails.append(f"control run not clean: PASS={clean.passed} FAIL={clean.failed} "
                     f"(expected PASS>=1, FAIL==0)")

    # injected: corrupt the expected value by +1 (mod width) -> must FAIL
    exp = areturn["expected"][0]
    width = R.WIDTH[exp["type"]]
    wrong = (int(exp["value"]) + 1) & ((1 << width) - 1)
    bad_return = json.loads(json.dumps(areturn))       # deep copy
    bad_return["expected"][0]["value"] = str(wrong)
    injected = write_and_run([module_cmd, bad_return], "bad")
    if injected.failed < 1:
        fails.append(f"INJECTED WRONG VALUE NOT CAUGHT: run reported FAIL={injected.failed} "
                     f"(expected >=1). Comparator did not fire — a green run would be meaningless.")
    else:
        print(f"  injected wrong expected ({exp['value']} -> {wrong}) correctly classified FAIL: "
              f"{injected.fail_details[0]}")
    return fails


def main() -> int:
    print("positive control: proving the comparator fires on wrong answers")
    fails = _unit_level() + _end_to_end()
    if fails:
        print("BROKEN COMPARATOR:")
        for f in fails:
            print(f"  >>> {f}")
        return 1
    print("VERDICT: comparator fires on every injected wrong answer and passes correct ones. "
          "A green run is meaningful evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
