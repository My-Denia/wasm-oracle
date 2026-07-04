#!/usr/bin/env python3
"""enumerate_m1_scope.py - derive the M1 executor's scope FROM REAL DATA, not a guess.

M1 implements an integer execution core. Its instruction set and its decoder's section
scope must be the EXACT opcodes/sections the 4 frozen targets' INSTANTIATED modules actually
contain -- enumerated here as committed evidence, so the implementation list is data, not a
guess. (Guessing the instruction set is the failure mode this milestone exists to avoid.)

METHOD (authoritative, pinned-toolchain): over the SAME module set the body-purity gate uses
-- the `.wasm` named by `type=="module"` commands in the WABT JSON (the only modules a value
assertion instantiates; assert_invalid/assert_malformed binaries are never instantiated) --
run WABT's own `wasm-objdump`:
  * `-h`  -> the binary SECTIONS present (Type/Function/Export/Code/...) = decoder scope.
  * `-d`  -> the instruction OPCODES present (mnemonic after `|`)        = interpreter scope.
wasm-objdump is the pinned WABT toolchain's authoritative disassembler; the opcode mnemonics
are read directly from its output, not inferred from source text.

Also tallies, for context: the command-type inventory per file, and the distinct exported
fields that value assertions invoke.

No silent skipping: a missing/undisassemblable module is a hard failure, never dropped.

Requires wabt's wasm-objdump (same pinned toolchain as convert.py / body_purity_check.py).
Reproduce (Linux/WSL, after scripts/convert.py):
    WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump python3 tools/enumerate_m1_scope.py
Writes goal-runs/m1-scope.txt. Exit 0 = enumerated; 1 = tool/evidence missing or a module failed.
"""
from __future__ import annotations
import json, os, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest_m0.json"
CONVERTED = ROOT / "build" / "converted"
OUT = ROOT / "goal-runs" / "m1-scope.txt"
OBJDUMP = os.environ.get("WASM_OBJDUMP", str(ROOT / "vendor" / "wabt" / "bin" / "wasm-objdump"))


def objdump(args: list[str], wasm: Path) -> str:
    res = subprocess.run([OBJDUMP, *args, str(wasm)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"wasm-objdump {' '.join(args)} failed on {wasm}: {res.stderr.strip()}")
    return res.stdout


def sections_of(wasm: Path) -> list[str]:
    """Section names from `wasm-objdump -h` (first token of each line under 'Sections:')."""
    out = objdump(["-h"], wasm)
    names, in_sections = [], False
    for ln in out.splitlines():
        if ln.strip() == "Sections:":
            in_sections = True
            continue
        if in_sections:
            s = ln.strip()
            if not s:
                continue
            names.append(s.split()[0])   # e.g. "Type", "Function", "Export", "Code"
    return names


def opcodes_of(wasm: Path) -> list[str]:
    """Instruction mnemonics from `wasm-objdump -d` (token after '|' on each instr line)."""
    out = objdump(["-d"], wasm)
    ops = []
    for ln in out.splitlines():
        if "|" not in ln:                # func headers / banners have no '|'
            continue
        rhs = ln.split("|", 1)[1].strip()
        if rhs:
            ops.append(rhs.split()[0])   # mnemonic, e.g. "i32.const", "i32.add", "return", "end"
    return ops


def modules_instantiated(conv: dict) -> list[str]:
    return [c["filename"] for c in conv["commands"]
            if c.get("type") == "module" and c.get("filename")]


def main() -> int:
    if not (os.path.exists(OBJDUMP) or _on_path(OBJDUMP)):
        print(f"FAIL: wasm-objdump not found at {OBJDUMP} (need pinned wabt; run scripts/convert.py first).")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = [t["name"] for t in manifest["targets"]]

    sec_files: dict[str, set[str]] = defaultdict(set)     # section -> {target files}
    op_occ: Counter[str] = Counter()                      # opcode -> total occurrences
    op_mods: dict[str, set[str]] = defaultdict(set)       # opcode -> {module filenames}
    cmd_types: Counter[str] = Counter()                   # command type -> count (all 4 files)
    invoked_fields: set[str] = set()                      # distinct exported fields invoked
    per_file = []                                          # provenance rows
    module_count = 0

    for name in targets:
        stem = name[:-5] if name.endswith(".wast") else name
        cj = CONVERTED / stem / f"{stem}.json"
        if not cj.exists():
            print(f"FAIL: converted JSON missing (not skipping): {cj}. Run scripts/convert.py.")
            return 1
        conv = json.loads(cj.read_text(encoding="utf-8"))
        for c in conv["commands"]:
            cmd_types[c.get("type", "<no-type>")] += 1
            act = c.get("action") or {}
            if act.get("type") == "invoke" and act.get("field"):
                invoked_fields.add(act["field"])
        mods = modules_instantiated(conv)
        if not mods:
            print(f"FAIL: {name} has no instantiated module (unexpected).")
            return 1
        f_secs: set[str] = set()
        f_ops: set[str] = set()
        for fn in mods:
            wp = CONVERTED / stem / fn
            if not wp.exists():
                print(f"FAIL: module file missing (not skipping): {wp}")
                return 1
            module_count += 1
            for s in sections_of(wp):
                sec_files[s].add(name)
                f_secs.add(s)
            for op in opcodes_of(wp):
                op_occ[op] += 1
                op_mods[op].add(fn)
                f_ops.add(op)
        per_file.append({"file": name, "modules": len(mods),
                         "sections": sorted(f_secs), "opcodes": sorted(f_ops)})

    sections_sorted = sorted(sec_files)
    opcodes_sorted = sorted(op_occ)

    # ---- write committed evidence artifact ----
    OUT.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("M1 SCOPE — enumerated from real data (instantiated modules of the 4 frozen targets)")
    L.append("=" * 78)
    L.append(f"spec commit : {manifest['spec']['commit']}")
    L.append(f"wabt        : {manifest['wabt']['tag']} (wasm-objdump; authoritative disassembler)")
    L.append(f"targets     : {', '.join(targets)}")
    L.append(f"modules enumerated (type==module instances): {module_count}")
    L.append("method      : wasm-objdump -h (sections) + -d (opcodes) over the SAME instantiated")
    L.append("              module set body_purity_check.py uses. Opcodes read from disassembler")
    L.append("              output, not guessed. Deterministic: sorted; re-run is byte-identical.")
    L.append("")
    L.append(f"BINARY SECTIONS PRESENT (decoder scope) — {len(sections_sorted)} distinct:")
    for s in sections_sorted:
        L.append(f"    {s:12} in: {', '.join(sorted(sec_files[s]))}")
    L.append("")
    L.append(f"INSTRUCTION OPCODES PRESENT (interpreter scope) — {len(opcodes_sorted)} distinct:")
    L.append(f"    {'opcode':22} {'occurrences':>11}  {'#modules':>8}")
    for op in opcodes_sorted:
        L.append(f"    {op:22} {op_occ[op]:11d}  {len(op_mods[op]):8d}")
    L.append("")
    L.append(f"COMMAND-TYPE INVENTORY ({len(targets)} files, {sum(cmd_types.values())} commands):")
    for t, n in sorted(cmd_types.items()):
        L.append(f"    {t:20} {n:5d}")
    L.append("")
    L.append(f"DISTINCT INVOKED EXPORT FIELDS (value asserts + actions): {len(invoked_fields)}")
    L.append("")
    L.append("PER-FILE PROVENANCE:")
    for r in per_file:
        L.append(f"  {r['file']:18} modules={r['modules']:2}  sections={r['sections']}")
        L.append(f"  {'':18} opcodes={r['opcodes']}")
    L.append("")
    L.append("SCOPE FINDINGS (for the executor plan):")
    ctrl = sorted(o for o in opcodes_sorted
                  if o in {"block", "loop", "if", "else", "br", "br_if", "br_table"})
    L.append(f"  structured-control-flow opcodes present: {ctrl if ctrl else 'NONE'}")
    L.append(f"  'end'/'return' present: "
             f"{[o for o in ('end','return') if o in op_occ]}")
    mem = sorted(o for o in opcodes_sorted if ".load" in o or ".store" in o or o.startswith("memory."))
    L.append(f"  memory opcodes present: {mem if mem else 'NONE'}")
    flt = sorted(o for o in opcodes_sorted if o.startswith("f32.") or o.startswith("f64."))
    L.append(f"  float opcodes present (must be NONE per purity gates): {flt if flt else 'NONE'}")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---- stdout summary ----
    print("\n".join(L))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


def _on_path(name: str) -> bool:
    from shutil import which
    return which(name) is not None


if __name__ == "__main__":
    sys.exit(main())
