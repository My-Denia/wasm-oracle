#!/usr/bin/env python3
"""enumerate_m2_scope.py - derive AND GATE the M2 control-flow scope FROM REAL DATA.

M2 adds structured control flow. Its opcode/section/block-type scope must be EXACTLY what the
M2 targets' INSTANTIATED modules actually contain -- enumerated here as committed evidence AND
enforced as a FAIL-CLOSED assertion, so the scope is data, not a guess (guessing the instruction
set is the failure mode this repo has been corrected on twice -- see AGENTS.md).

Unlike tools/enumerate_m1_scope.py (which only REPORTS), this tool EXITS NONZERO if the real data
steps outside the frozen M2 scope: any section not in {Type,Function,Export,Code}, any opcode not
in the frozen M2 set (M1 integer core + {nop,block,loop,if,else,br,br_if,br_table,drop}), any
block-type not in {empty,i32,i64}, or any float/memory opcode. That machine assertion is what
gates the decoder/interpreter work -- it must pass before M2.1.

METHOD (authoritative, pinned toolchain): over the modules a value assertion instantiates (the
`.wasm` named by `type=="module"` in the WABT JSON), run WABT's own `wasm-objdump`:
  * `-h`  -> binary SECTIONS present (decoder scope).
  * `-d`  -> instruction OPCODES present + block/loop/if BLOCK-TYPE signatures (interpreter scope).
wasm-objdump is the pinned authoritative disassembler; mnemonics/signatures are read from its
output, not inferred.

Requires wabt's wasm-objdump (same pinned toolchain as convert.py). Reproduce (Linux/WSL, after
`scripts/convert.py --manifest manifest_m2.json ...`):
    WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump python3 tools/enumerate_m2_scope.py
Writes goal-runs/m2-control-flow/scope.txt. Exit 0 = enumerated AND in-scope; 1 = tool/evidence
missing, a module failed, OR the data stepped outside the frozen M2 scope (fail-closed).
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import decoder as dec  # noqa: E402  (single source of truth for opcode mnemonics)

DEFAULT_MANIFEST = ROOT / "manifest_m2.json"
CONVERTED = ROOT / "build" / "converted"
OUT = ROOT / "goal-runs" / "m2-control-flow" / "scope.txt"
OBJDUMP = os.environ.get("WASM_OBJDUMP", str(ROOT / "vendor" / "wabt" / "bin" / "wasm-objdump"))

ALLOWED_SECTIONS = {"Type", "Function", "Export", "Code"}
ALLOWED_BLOCKTYPES = {"empty", "i32", "i64"}

# Frozen M2 opcode scope = the M1 integer core + the structured-control-flow ops this milestone
# adds. The control-flow set is stated EXPLICITLY here (this file owns the scope POLICY), while the
# integer mnemonics are taken from interp.decoder.OPCODES (single source of truth for the byte
# table). The union is the same whether or not the decoder has been extended yet -- block/loop/if/
# ... are added explicitly -- so this gate is order-independent and can block the decoder work.
CONTROL_FLOW_OPS = {"nop", "block", "loop", "if", "else", "end", "br", "br_if", "br_table",
                    "return", "drop"}
FROZEN_M2_OPS = {mn for (mn, _imm) in dec.OPCODES.values()} | CONTROL_FLOW_OPS
# The control-flow opcodes we EXPECT to actually see exercised (proves M2 targets test control flow)
STRUCTURED_OPS = {"block", "loop", "if", "else", "br", "br_if", "br_table", "drop", "nop"}


def objdump(args: list[str], wasm: Path) -> str:
    res = subprocess.run([OBJDUMP, *args, str(wasm)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"wasm-objdump {' '.join(args)} failed on {wasm}: {res.stderr.strip()}")
    return res.stdout


def sections_of(wasm: Path) -> list[str]:
    out = objdump(["-h"], wasm)
    names, in_sections = [], False
    for ln in out.splitlines():
        if ln.strip() == "Sections:":
            in_sections = True
            continue
        if in_sections:
            s = ln.strip()
            if s:
                names.append(s.split()[0])
    return names


def instrs_of(wasm: Path) -> tuple[list[str], set[str]]:
    """Return (opcode mnemonics, block-type signature forms) from `wasm-objdump -d`.
    A block/loop/if line reads e.g. 'block', 'block i32', 'loop i32', 'if' -- the token after the
    mnemonic (if any) is the block result type; absent -> 'empty'."""
    out = objdump(["-d"], wasm)
    ops: list[str] = []
    blocktypes: set[str] = set()
    for ln in out.splitlines():
        if "|" not in ln:
            continue
        rhs = ln.split("|", 1)[1].strip()
        if not rhs:
            continue
        toks = rhs.split()
        mn = toks[0]
        # `wasm-objdump -d` prints a function's LOCAL DECLARATIONS as `local[0] type=i32` lines
        # (bytes like `01 7f`) before the body. Those are declarations, not instructions — skip
        # them (distinguished from the real `local.get`/`local.set` opcodes by the `local[` bracket
        # vs the `local.` dot). M1 modules declared no locals, so this never arose before M2.
        if mn.startswith("local["):
            continue
        ops.append(mn)
        if mn in ("block", "loop", "if"):
            blocktypes.add(toks[1] if len(toks) > 1 else "empty")
    return ops, blocktypes


def modules_instantiated(conv: dict) -> list[str]:
    return [c["filename"] for c in conv["commands"]
            if c.get("type") == "module" and c.get("filename")]


def _on_path(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate + fail-closed-gate the M2 control-flow scope.")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help="manifest JSON (default: manifest_m2.json)")
    args = ap.parse_args()

    if not (os.path.exists(OBJDUMP) or _on_path(OBJDUMP)):
        print(f"FAIL: wasm-objdump not found at {OBJDUMP} (need pinned wabt; run scripts/convert.py first).")
        return 1
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    targets = [t["name"] for t in manifest["targets"]]

    sec_files: dict[str, set[str]] = defaultdict(set)
    op_occ: Counter[str] = Counter()
    op_mods: dict[str, set[str]] = defaultdict(set)
    blocktypes_all: set[str] = set()
    cmd_types: Counter[str] = Counter()
    invoked_fields: set[str] = set()
    per_file = []
    module_count = 0

    for name in targets:
        stem = name[:-5] if name.endswith(".wast") else name
        cj = CONVERTED / stem / f"{stem}.json"
        if not cj.exists():
            print(f"FAIL: converted JSON missing (not skipping): {cj}. "
                  f"Run scripts/convert.py --manifest {Path(args.manifest).name}.")
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
        f_bts: set[str] = set()
        for fn in mods:
            wp = CONVERTED / stem / fn
            if not wp.exists():
                print(f"FAIL: module file missing (not skipping): {wp}")
                return 1
            module_count += 1
            for s in sections_of(wp):
                sec_files[s].add(name)
                f_secs.add(s)
            ops, bts = instrs_of(wp)
            for op in ops:
                op_occ[op] += 1
                op_mods[op].add(fn)
                f_ops.add(op)
            f_bts |= bts
            blocktypes_all |= bts
        per_file.append({"file": name, "modules": len(mods), "sections": sorted(f_secs),
                         "opcodes": sorted(f_ops), "blocktypes": sorted(f_bts)})

    sections_sorted = sorted(sec_files)
    opcodes_sorted = sorted(op_occ)
    blocktypes_sorted = sorted(blocktypes_all)

    # ---- FAIL-CLOSED scope assertions (the machine gate that blocks decoder/interpreter work) ----
    violations: list[str] = []
    extra_secs = sorted(set(sections_sorted) - ALLOWED_SECTIONS)
    if extra_secs:
        violations.append(f"section(s) outside {{Type,Function,Export,Code}}: {extra_secs}")
    bad_ops = sorted(set(opcodes_sorted) - FROZEN_M2_OPS)
    if bad_ops:
        violations.append(f"opcode(s) outside the frozen M2 scope: {bad_ops}")
    float_ops = sorted(o for o in opcodes_sorted if o.startswith("f32.") or o.startswith("f64."))
    if float_ops:
        violations.append(f"float opcode(s) present (M2 is integer-only): {float_ops}")
    mem_ops = sorted(o for o in opcodes_sorted
                     if ".load" in o or ".store" in o or o.startswith("memory."))
    if mem_ops:
        violations.append(f"memory opcode(s) present (M2 has no linear memory): {mem_ops}")
    bad_bts = sorted(set(blocktypes_sorted) - ALLOWED_BLOCKTYPES)
    if bad_bts:
        violations.append(f"block-type(s) outside {{empty,i32,i64}}: {bad_bts}")
    if not (set(opcodes_sorted) & STRUCTURED_OPS):
        violations.append("no structured-control-flow opcode present — M2 targets do not exercise "
                          "control flow (unexpected; check target selection)")

    # ---- write committed evidence artifact ----
    OUT.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("M2 SCOPE — enumerated from real data (instantiated modules of the M2 targets), FAIL-CLOSED")
    L.append("=" * 78)
    L.append(f"spec commit : {manifest['spec']['commit']}")
    L.append(f"wabt        : {manifest['wabt']['tag']} (wasm-objdump; authoritative disassembler)")
    L.append(f"targets     : {', '.join(targets)}")
    L.append(f"modules enumerated (type==module instances): {module_count}")
    L.append("method      : wasm-objdump -h (sections) + -d (opcodes + block-type signatures) over")
    L.append("              the instantiated module set. Read from the disassembler, not guessed.")
    L.append("              Deterministic: sorted; re-run is byte-identical.")
    L.append("")
    L.append(f"BINARY SECTIONS PRESENT (decoder scope) — {len(sections_sorted)} distinct:")
    for s in sections_sorted:
        L.append(f"    {s:12} in: {', '.join(sorted(sec_files[s]))}")
    L.append("")
    L.append(f"BLOCK-TYPE SIGNATURES PRESENT (block/loop/if result forms) — {len(blocktypes_sorted)}:")
    L.append(f"    {', '.join(blocktypes_sorted)}")
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
        L.append(f"  {r['file']:14} modules={r['modules']:2}  sections={r['sections']}")
        L.append(f"  {'':14} blocktypes={r['blocktypes']}")
        L.append(f"  {'':14} opcodes={r['opcodes']}")
    L.append("")
    L.append("SCOPE FINDINGS (control-flow milestone):")
    ctrl = sorted(o for o in opcodes_sorted if o in STRUCTURED_OPS)
    L.append(f"  structured-control-flow opcodes present: {ctrl if ctrl else 'NONE'}")
    L.append(f"  'end'/'return' present: {[o for o in ('end','return') if o in op_occ]}")
    mem = sorted(o for o in opcodes_sorted if ".load" in o or ".store" in o or o.startswith("memory."))
    L.append(f"  memory opcodes present (must be NONE): {mem if mem else 'NONE'}")
    flt = sorted(o for o in opcodes_sorted if o.startswith("f32.") or o.startswith("f64."))
    L.append(f"  float opcodes present (must be NONE): {flt if flt else 'NONE'}")
    call = sorted(o for o in opcodes_sorted if o in ("call", "call_indirect"))
    L.append(f"  call opcodes present (must be NONE for M2): {call if call else 'NONE'}")
    L.append("")
    L.append("SCOPE GATE (fail-closed):")
    if violations:
        L.append("  VERDICT: OUT OF SCOPE — the following must be re-scoped before any executor code:")
        for v in violations:
            L.append(f"    >>> {v}")
    else:
        L.append("  VERDICT: IN SCOPE — sections ⊆ {Type,Function,Export,Code}; opcodes ⊆ frozen M2 set;")
        L.append("           block-types ⊆ {empty,i32,i64}; float/memory/call = NONE. Decoder work unblocked.")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---- stdout summary ----
    print("\n".join(L))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    if violations:
        print("\nFAIL: M2 data stepped outside the frozen scope (see SCOPE GATE above). "
              "STOP and re-scope — do not extend the decoder to silently absorb it.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
