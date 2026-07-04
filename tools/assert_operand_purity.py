#!/usr/bin/env python3
"""assert_operand_purity.py - gate: every manifest target's assert_return / assert_trap
operands (action.args + expected results) use only i32/i64 -- no f32/f64.

This is the INTERFACE-layer purity gate. It pairs with body_purity_check.py (the
BODY-layer gate over instantiated module opcodes); together they turn "these 4 files
need no floating point" from a claim into two standing CI assertions.

Reproduce: python scripts/convert.py && python tools/assert_operand_purity.py
Exit 0 = clean (f32==f64==0 for every target); 1 = a float operand was found or JSON missing.
"""
import json, os, collections, sys

MANIFEST = "manifest_m0.json"
CONVERTED = "build/converted"

m = json.load(open(MANIFEST, encoding="utf-8"))
targets = [t["name"] for t in m["targets"]]


def operand_types(cmd):
    out = []
    act = cmd.get("action") or {}
    for el in act.get("args") or []:
        out.append(el.get("type", "?"))
    for el in cmd.get("expected") or []:      # assert_return expected results
        out.append(el.get("type", "?"))
    return out


print(f"{'file':18} {'asserts':>7} {'i32':>6} {'i64':>6} {'f32':>6} {'f64':>6} {'other':>6}")
all_clean = True
for name in targets:
    stem = name[:-5] if name.endswith(".wast") else name
    p = os.path.join(CONVERTED, stem, f"{stem}.json")
    if not os.path.exists(p):
        print(f"{name:18} CONVERTED JSON NOT FOUND: {p}")
        all_clean = False
        continue
    d = json.load(open(p, encoding="utf-8"))
    c = collections.Counter()
    n = 0
    for cmd in d["commands"]:
        if cmd["type"] in ("assert_return", "assert_trap"):
            n += 1
            for t in operand_types(cmd):
                c[t if t in ("i32", "i64", "f32", "f64") else "other"] += 1
    print(f"{name:18} {n:7d} {c['i32']:6d} {c['i64']:6d} {c['f32']:6d} {c['f64']:6d} {c['other']:6d}")
    if c["f32"] or c["f64"]:
        all_clean = False
        print(f"  >>> FAIL: {name} has f32={c['f32']} f64={c['f64']} in assert operands")

print()
print("VERDICT:",
      "ALL targets integer-clean (f32==f64==0 in assert operands)" if all_clean
      else "FLOAT OPERANDS PRESENT or evidence missing -- see above")
sys.exit(0 if all_clean else 1)
