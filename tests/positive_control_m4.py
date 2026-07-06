#!/usr/bin/env python3
"""M4 positive control: prove the validation comparator fires.

Exit 0 means a green M4 run is meaningful evidence:
- a valid module falsely marked expected-invalid is classified FAIL;
- a real invalid module with the wrong expected category is classified FAIL;
- a real included invalid module with the correct category is classified PASS.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from interp import validator as V  # noqa: E402
import run_m4  # noqa: E402

SCOPE = ROOT / "goal-runs" / "m4-validation" / "scope.json"
CONVERTED = ROOT / "build" / "converted"


def _load_scope() -> dict:
    if not SCOPE.exists():
        raise SystemExit(f"setup missing: {SCOPE}")
    return json.loads(SCOPE.read_text(encoding="utf-8-sig"))


def _first_valid_module_record() -> dict:
    i32_json = CONVERTED / "i32" / "i32.json"
    if not i32_json.exists():
        raise SystemExit(f"setup missing: {i32_json} (run scripts/convert.py first)")
    data = json.loads(i32_json.read_text(encoding="utf-8-sig"))
    module_cmd = next(c for c in data["commands"] if c.get("type") == "module")
    return {
        "decision": "INCLUDED",
        "validation_index": 9000,
        "source_file": "i32.wast",
        "module_filename": module_cmd["filename"],
        "category": V.TYPE_MISMATCH,
    }


def main() -> int:
    print("M4 positive control: proving validator acceptance/category mismatches fail")
    scope = _load_scope()
    included = [r for r in scope["records"] if r["decision"] == "INCLUDED"]
    if not included:
        print("BROKEN SETUP: scope has no included records")
        return 1

    failures = []

    correct = run_m4.evaluate_record(included[0])
    if correct.status != "PASS":
        failures.append(f"real included invalid did not PASS under correct category: {correct}")
    else:
        print(f"  correct included invalid PASS: #{included[0]['validation_index']:03d} "
              f"{included[0]['module_filename']} -> {correct.actual_category}")

    valid_as_invalid = run_m4.evaluate_record(_first_valid_module_record())
    if valid_as_invalid.status != "FAIL" or "accepted expected-invalid" not in valid_as_invalid.detail:
        failures.append(f"valid module marked invalid was not classified FAIL: {valid_as_invalid}")
    else:
        print(f"  accepted expected-invalid correctly classified FAIL: {valid_as_invalid.detail}")

    unknown_label = next((r for r in included if r["category"] == V.UNKNOWN_LABEL), None)
    if unknown_label is None:
        failures.append("setup missing: no included unknown-label record")
    else:
        wrong_category = deepcopy(unknown_label)
        wrong_category["category"] = V.TYPE_MISMATCH
        wrong = run_m4.evaluate_record(wrong_category)
        if wrong.status != "FAIL" or "wrong validation category" not in wrong.detail:
            failures.append(f"wrong category was not classified FAIL: {wrong}")
        else:
            print(f"  wrong category correctly classified FAIL: {wrong.detail}")

    if failures:
        print("BROKEN M4 COMPARATOR:")
        for failure in failures:
            print(f"  >>> {failure}")
        return 1
    print("VERDICT: M4 comparator fires on accepted invalid and wrong-category injections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
