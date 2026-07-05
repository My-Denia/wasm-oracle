#!/usr/bin/env python3
"""positive_control_m3.py — prove the M3 comparator FIRES on a wrong answer (M3.4 gate, 防复发).

A green M3 run must be evidence the comparator works, not that it silently passes. This mirrors the
M1/M2 positive controls but over a REAL M3 linear-memory target (memory_size): corrupt one `expected`
value and confirm the runner classifies it FAIL, with a pristine control run confirming the harness
is not simply always-failing. Because run_m3 reuses run_m1.run_file, exercising run_file over a
corrupted M3 assertion proves the exact comparator path M3 relies on.

memory_size is used (not store) because store.wast's asserts are VOID (the stores return nothing, so
there is no `expected` value to corrupt); memory_size's `size` asserts carry real i32 expecteds.

Reproduce (after scripts/convert.py --manifest manifest_m3.json ...):  python3 tests/positive_control_m3.py
Exit 0 = comparator fires on wrong answers AND passes correct ones; 1 = comparator broken / setup missing.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from interp import runner as R                 # noqa: E402
import run_m1                                   # noqa: E402  (run_file is milestone-agnostic; run_m3 reuses it)

CONVERTED = ROOT / "build" / "converted"


def _unit_level() -> list[str]:
    fails = []
    ok, _ = R.compare_return([3], [{"type": "i32", "value": "3"}])
    if not ok:
        fails.append("compare_return rejected a CORRECT value")
    ok, _ = R.compare_return([4], [{"type": "i32", "value": "3"}])
    if ok:
        fails.append("compare_return ACCEPTED a wrong value (4 vs 3)")
    ok, _ = R.compare_return([3, 3], [{"type": "i32", "value": "3"}])
    if ok:
        fails.append("compare_return ACCEPTED an arity mismatch")
    return fails


def _end_to_end() -> list[str]:
    fails = []
    cj = CONVERTED / "memory_size" / "memory_size.json"
    if not cj.exists():
        return [f"setup missing: {cj} (run scripts/convert.py --manifest manifest_m3.json first)"]
    data = json.loads(cj.read_text(encoding="utf-8"))

    # First module + the first assert_return that carries an expected value (a `size` -> Npages
    # check on that module) — a self-consistent pair (the first module's initial size).
    module_cmd = next(c for c in data["commands"] if c.get("type") == "module")
    areturn = next(c for c in data["commands"]
                   if c.get("type") == "assert_return" and c.get("expected")
                   and c["expected"][0].get("value") is not None)

    def write_and_run(cmds, tag) -> "run_m1.FileResult":
        tmp = cj.parent / f"_positive_control_m3_{tag}.json"
        tmp.write_text(json.dumps({"source_filename": f"positive_control_m3_{tag}", "commands": cmds}),
                       encoding="utf-8")
        try:
            return run_m1.run_file(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    # control: pristine module + a real memory assertion -> 0 FAIL, >=1 PASS
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
                     f"(expected >=1). Comparator did not fire — a green M3 run would be meaningless.")
    else:
        print(f"  injected wrong expected on {areturn['action'].get('field')!r} "
              f"({exp['value']} -> {wrong}) correctly classified FAIL: {injected.fail_details[0]}")
    return fails


def main() -> int:
    print("M3 positive control: proving the comparator fires on wrong linear-memory answers")
    fails = _unit_level() + _end_to_end()
    if fails:
        print("BROKEN COMPARATOR:")
        for f in fails:
            print(f"  >>> {f}")
        return 1
    print("VERDICT: comparator fires on the injected wrong answer and passes the correct one. "
          "A green M3 run is meaningful evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
