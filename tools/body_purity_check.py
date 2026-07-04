#!/usr/bin/env python3
"""body_purity_check.py - prove the CURATED target modules contain no f32/f64 anywhere
in the COMPILED module: function-body opcodes, float conversions, initializer
const-expressions (globals/data/elem), and float value-types. Closes the gap that
assert_operand_purity.py cannot (assert-operand purity != module purity).

METHOD (sound-by-construction): disassemble the compiled `.wasm` with `wasm2wat` and
search its text for any f32/f64 token. This is disassembly of the COMPILED module, NOT a
.wast source grep, so it cannot be fooled by comments/source formatting.

Why wasm2wat and not `wasm-objdump -d`: objdump -d disassembles function BODIES only, so it
misses (a) float value-types like `(param f32)`, (b) float const-exprs in global/data/elem
INITIALIZERS, and its mnemonic-prefix view invites missing float CONVERSIONS
(`i32.trunc_f32_s`, `i32.reinterpret_f32`). wasm2wat renders the whole module, and the plain
`f32`/`f64` token search below catches all of: float opcodes (`f32.add`), float conversions
(`i32.trunc_f32_s`), initializer floats (`(global f32 (f32.const 0))`), and value-types
(`(param f32)`). Quoted strings (export/import names) are stripped first so a function merely
NAMED "...f32..." does not cause a false failure.

Scope: only modules a value assertion runs against -- the `.wasm` named by `module` commands
in the WABT JSON. assert_invalid / assert_malformed modules are never instantiated for value
asserts (they carry their own command types), so they are excluded by design.

Requires wabt's wasm2wat (same pinned toolchain as convert.py).
Reproduce: python scripts/convert.py && WASM2WAT=vendor/wabt/bin/wasm2wat python tools/body_purity_check.py
Exit 0 = clean (no f32/f64 in any instantiated module); 1 = float found or evidence/tool missing.
"""
import argparse, json, os, re, shutil, subprocess, sys

MANIFEST  = "manifest_m0.json"
CONVERTED = "build/converted"
WASM2WAT  = os.environ.get("WASM2WAT", "wasm2wat")
FLOAT     = re.compile(r"f(32|64)")        # any f32/f64 token: value-types, opcodes, conversions, initializers
STRING    = re.compile(r'"[^"]*"')          # export/import name literals -> stripped before matching


def modules_run_by_asserts(conv):
    # value asserts run against the most-recent `module` instance; invalid/malformed excluded
    return [c["filename"] for c in conv["commands"]
            if c.get("type") == "module" and c.get("filename")]


def float_lines(wasm_path):
    res = subprocess.run([WASM2WAT, wasm_path],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"wasm2wat failed on {wasm_path}: {res.stderr.strip()}")
    out = []
    for ln in res.stdout.splitlines():
        code = STRING.sub("", ln)           # drop string literals so export names can't false-match
        if FLOAT.search(code):
            out.append(ln.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description="Gate: instantiated target modules contain no f32/f64.")
    ap.add_argument("--manifest", default=MANIFEST,
                    help="manifest JSON whose targets to check (default: manifest_m0.json)")
    args = ap.parse_args()
    if not (shutil.which(WASM2WAT) or os.path.exists(WASM2WAT)):
        print(f"FAIL: {WASM2WAT} not found (need pinned wabt).")
        return 1
    m = json.load(open(args.manifest, encoding="utf-8"))
    targets = [t["name"] for t in m["targets"]]
    overall_clean = True
    print(f"{'file':18} {'modules':>7} {'float_lines':>11}  status")
    for name in targets:
        stem = name[:-5] if name.endswith(".wast") else name
        cj = os.path.join(CONVERTED, stem, f"{stem}.json")
        if not os.path.exists(cj):
            print(f"{name:18} {'-':>7} {'-':>11}  CONVERTED JSON NOT FOUND: {cj}")
            overall_clean = False
            continue
        conv = json.load(open(cj, encoding="utf-8"))
        mods = modules_run_by_asserts(conv)
        if not mods:
            print(f"{name:18} {0:7d} {'-':>11}  NO INSTANTIATED MODULE (unexpected)")
            overall_clean = False
            continue
        total_hits, detail = 0, []
        for fn in mods:
            wp = os.path.join(CONVERTED, stem, fn)
            if not os.path.exists(wp):
                print(f"{name:18} module file missing: {wp}")
                overall_clean = False
                continue
            hits = float_lines(wp)
            if hits:
                total_hits += len(hits)
                detail.append((fn, hits))
        if total_hits:
            overall_clean = False
        status = "clean" if total_hits == 0 else f"FLOAT ({total_hits})"
        print(f"{name:18} {len(mods):7d} {total_hits:11d}  {status}")
        for fn, hits in detail:
            print(f"    >>> {fn}: {len(hits)} f32/f64 line(s)")
            for h in hits[:8]:
                print(f"          {h}")
            if len(hits) > 8:
                print(f"          ... (+{len(hits) - 8} more)")
    print()
    print("VERDICT:",
          "ALL targets body-pure (no f32/f64 in compiled modules: bodies, conversions, "
          "initializers, value-types)" if overall_clean else
          "FLOAT PRESENT or evidence missing -- see above")
    return 0 if overall_clean else 1


if __name__ == "__main__":
    sys.exit(main())
