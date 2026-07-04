#!/usr/bin/env python3
"""body_purity_check.py - prove the CURATED target modules contain no f32/f64
*instructions* (not just no float assert operands). Closes the gap that
extract_assert_types.py cannot: assert-operand purity != function-body purity.

METHOD (sound-by-construction): scan the COMPILED module's opcodes, not the .wast
source. For each manifest target, disassemble every module a value assertion is
actually run against -- the .wasm files named by `module` commands in the WABT
JSON -- with `wasm-objdump -d`, and match f32./f64. instruction mnemonics.
assert_invalid / assert_malformed modules are NOT instantiated for value asserts,
so they are excluded from this gate by design.

Requires wabt's wasm-objdump on PATH (the same pinned toolchain as convert.py).
Reproduce: python scripts/convert.py && python tools/body_purity_check.py
Exit 0 = clean (no float opcodes); 1 = float opcode found or evidence/tool missing.
"""
import json, os, re, shutil, subprocess, sys

MANIFEST  = "manifest_m0.json"
CONVERTED = "build/converted"
OBJDUMP   = os.environ.get("WASM_OBJDUMP", "wasm-objdump")
FLOAT_OP  = re.compile(r"\b(f32|f64)\.")   # f32.add, f64.const, f32.load, ...


def modules_run_by_asserts(conv):
    # value asserts run against the most-recent `module` instance; invalid/malformed excluded
    return [c["filename"] for c in conv["commands"]
            if c.get("type") == "module" and c.get("filename")]


def float_opcodes(wasm_path):
    res = subprocess.run([OBJDUMP, "-d", wasm_path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"wasm-objdump failed on {wasm_path}: {res.stderr.strip()}")
    return [ln.strip() for ln in res.stdout.splitlines() if FLOAT_OP.search(ln)]


def main():
    if not shutil.which(OBJDUMP):
        print(f"FAIL: {OBJDUMP} not on PATH (need pinned wabt).")
        return 1
    m = json.load(open(MANIFEST, encoding="utf-8"))
    targets = [t["name"] for t in m["targets"]]
    overall_clean = True
    print(f"{'file':18} {'modules':>7} {'float_ops':>9}  status")
    for name in targets:
        stem = name[:-5] if name.endswith(".wast") else name
        cj = os.path.join(CONVERTED, stem, f"{stem}.json")
        if not os.path.exists(cj):
            print(f"{name:18} {'-':>7} {'-':>9}  CONVERTED JSON NOT FOUND: {cj}")
            overall_clean = False
            continue
        conv = json.load(open(cj, encoding="utf-8"))
        mods = modules_run_by_asserts(conv)
        if not mods:
            print(f"{name:18} {0:7d} {'-':>9}  NO INSTANTIATED MODULE (unexpected)")
            overall_clean = False
            continue
        total_hits, detail = 0, []
        for fn in mods:
            wp = os.path.join(CONVERTED, stem, fn)
            if not os.path.exists(wp):
                print(f"{name:18} module file missing: {wp}")
                overall_clean = False
                continue
            hits = float_opcodes(wp)
            if hits:
                total_hits += len(hits)
                detail.append((fn, hits))
        if total_hits:
            overall_clean = False
        status = "clean" if total_hits == 0 else f"FLOAT OPCODES ({total_hits})"
        print(f"{name:18} {len(mods):7d} {total_hits:9d}  {status}")
        for fn, hits in detail:
            print(f"    >>> {fn}: {len(hits)} f32/f64 instruction(s)")
            for h in hits[:8]:
                print(f"          {h}")
            if len(hits) > 8:
                print(f"          ... (+{len(hits) - 8} more)")
    print()
    print("VERDICT:",
          "ALL targets body-pure (no f32/f64 opcodes in instantiated modules)"
          if overall_clean else
          "FLOAT OPCODES PRESENT or evidence missing -- see above")
    return 0 if overall_clean else 1


if __name__ == "__main__":
    sys.exit(main())
