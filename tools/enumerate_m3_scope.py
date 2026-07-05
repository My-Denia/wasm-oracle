#!/usr/bin/env python3
"""enumerate_m3_scope.py - derive AND GATE the M3 linear-memory scope FROM REAL DATA.

M3 adds linear memory. Its section/opcode/limit scope must be EXACTLY what the M3 targets'
INSTANTIATED modules actually contain -- enumerated here as committed evidence AND enforced as a
FAIL-CLOSED assertion, so the scope is data, not a guess (guessing the instruction set is the
failure mode this repo has been corrected on twice -- see AGENTS.md).

Like tools/enumerate_m2_scope.py (and unlike the report-only M1 enumerator) this tool EXITS NONZERO
if the real data steps outside the frozen M3 scope. BUT its predicate is ASYMMETRIC and is NOT a
copy of M2's: M2 bans "any .load/.store/memory. opcode" categorically -- that exact ban would
FALSE-REJECT M3's own i32.store / memory.size / memory.grow. So M3 uses an ALLOW-SET plus RESIDUAL
BANS re-expressed against it, so a new memory opcode can never slip in as "just another op":
  * sections must be subset of {Type,Function,Export,Code,Memory}  (bans Data=11, Global, Table,
    Import, Start, Elem);
  * opcodes must be subset of the frozen M3 set (M1/M2 set + {i32.store, memory.size, memory.grow});
  * AND residual bans: any `.load` opcode; any `.store` opcode NOT in {i32.store}
    (i64.store / i32.store8/16 / i64.store8/16/32 fail-close); any `memory.*` opcode NOT in
    {memory.size, memory.grow} (memory.copy/fill/init fail-close);
  * any f32./f64. opcode; any call/call_indirect; any global.*/table.*/select;
  * POSITIVE: at least one memory opcode present (else the targets don't exercise memory).
An inline self-check (_assert_gate_live) feeds synthetic out-of-scope op-sets through the SAME
predicate and confirms each is flagged -- so the ban is proven LIVE on every run, not assumed.

METHOD (authoritative, pinned toolchain): over the modules a value assertion instantiates (the
`.wasm` named by `type=="module"` in the WABT JSON), run WABT's own `wasm-objdump`:
  * `-h`  -> binary SECTIONS present (decoder scope).
  * `-d`  -> instruction OPCODES present + memarg (align/offset) / memidx immediates.
  * `-x`  -> Memory section limits (initial[, max] pages).
wasm-objdump is the pinned authoritative disassembler; read from its output, not inferred.

Requires wabt's wasm-objdump (same pinned toolchain as convert.py). Reproduce (Linux/WSL, after
`scripts/convert.py --manifest manifest_m3.json ...`):
    WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump python3 tools/enumerate_m3_scope.py
Writes goal-runs/m3-linear-memory/scope.txt. Exit 0 = enumerated AND in-scope AND gate-live;
1 = tool/evidence missing, a module failed, the data stepped outside the frozen M3 scope, OR the
gate self-check failed (fail-closed).
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MANIFEST = ROOT / "manifest_m3.json"
CONVERTED = ROOT / "build" / "converted"
OUT = ROOT / "goal-runs" / "m3-linear-memory" / "scope.txt"
OBJDUMP = os.environ.get("WASM_OBJDUMP", str(ROOT / "vendor" / "wabt" / "bin" / "wasm-objdump"))

ALLOWED_SECTIONS = {"Type", "Function", "Export", "Code", "Memory"}

# The ONLY linear-memory opcodes M3 admits (the residual-ban allow-sets are subsets of this).
MEMORY_OPS = {"i32.store", "memory.size", "memory.grow"}
ALLOWED_STORES = {"i32.store"}
ALLOWED_MEMORY_MISC = {"memory.size", "memory.grow"}

# Frozen M3 opcode scope = the M1 integer core + M2 control flow + the M3 memory ops. This is an
# EXPLICIT, PINNED snapshot — NOT derived from interp.decoder.OPCODES. Deriving it from the mutable
# decoder table would let this fail-closed gate silently EXPAND whenever a later milestone extends
# the decoder (e.g. a future `local.tee`/`unreachable`/load that the residual bans below don't
# name would join the frozen set and a target containing it would no longer be reported out of M3
# scope). The gate's job is to FREEZE M3's scope independently of the implementation; so the policy
# lives here as data. If a future milestone deliberately widens M3, it edits THIS set — a reviewed
# act, not an implicit side effect of touching the decoder. (Snapshot taken from decoder.OPCODES at
# M3: 71 integer + 10 control-flow + 3 memory = 84 mnemonics; the decoder self-test still validates
# their BYTES against wasm-objdump.)
FROZEN_M3_OPS = frozenset({
    # M1 integer core
    "i32.add", "i32.and", "i32.clz", "i32.const", "i32.ctz", "i32.div_s", "i32.div_u", "i32.eq",
    "i32.eqz", "i32.extend16_s", "i32.extend8_s", "i32.ge_s", "i32.ge_u", "i32.gt_s", "i32.gt_u",
    "i32.le_s", "i32.le_u", "i32.lt_s", "i32.lt_u", "i32.mul", "i32.ne", "i32.or", "i32.popcnt",
    "i32.rem_s", "i32.rem_u", "i32.rotl", "i32.rotr", "i32.shl", "i32.shr_s", "i32.shr_u",
    "i32.sub", "i32.wrap_i64", "i32.xor",
    "i64.add", "i64.and", "i64.clz", "i64.const", "i64.ctz", "i64.div_s", "i64.div_u", "i64.eq",
    "i64.eqz", "i64.extend16_s", "i64.extend32_s", "i64.extend8_s", "i64.extend_i32_s",
    "i64.extend_i32_u", "i64.ge_s", "i64.ge_u", "i64.gt_s", "i64.gt_u", "i64.le_s", "i64.le_u",
    "i64.lt_s", "i64.lt_u", "i64.mul", "i64.ne", "i64.or", "i64.popcnt", "i64.rem_s", "i64.rem_u",
    "i64.rotl", "i64.rotr", "i64.shl", "i64.shr_s", "i64.shr_u", "i64.sub", "i64.xor",
    "local.get", "end", "return",
    # M2 structured control flow (+ local.set)
    "nop", "block", "loop", "if", "else", "br", "br_if", "br_table", "drop", "local.set",
    # M3 linear memory
    "i32.store", "memory.size", "memory.grow",
})
# The memory opcodes we EXPECT to actually see exercised (proves M3 targets test memory).
EXPECTED_MEMORY_OPS = {"i32.store", "memory.size", "memory.grow"}


def scope_violations(sections: set[str], opcodes: set[str]) -> list[str]:
    """The FAIL-CLOSED M3 predicate as a PURE function (so it can be self-checked on synthetic
    inputs). Returns a list of human-readable violation strings; empty == in scope."""
    v: list[str] = []
    extra_secs = sorted(sections - ALLOWED_SECTIONS)
    if extra_secs:
        v.append(f"section(s) outside {{Type,Function,Export,Code,Memory}}: {extra_secs}")
    bad_ops = sorted(opcodes - FROZEN_M3_OPS)
    if bad_ops:
        v.append(f"opcode(s) outside the frozen M3 scope: {bad_ops}")
    # Residual bans re-expressed against the allow-set (NOT M2's categorical memory ban).
    load_ops = sorted(o for o in opcodes if ".load" in o)
    if load_ops:
        v.append(f"load opcode(s) present (M3 has no loads): {load_ops}")
    bad_stores = sorted(o for o in opcodes if ".store" in o and o not in ALLOWED_STORES)
    if bad_stores:
        v.append(f"store opcode(s) beyond i32.store (M3 scope): {bad_stores}")
    bad_mem = sorted(o for o in opcodes
                     if o.startswith("memory.") and o not in ALLOWED_MEMORY_MISC)
    if bad_mem:
        v.append(f"memory.* opcode(s) beyond size/grow: {bad_mem}")
    float_ops = sorted(o for o in opcodes if o.startswith("f32.") or o.startswith("f64."))
    if float_ops:
        v.append(f"float opcode(s) present (M3 is integer-only): {float_ops}")
    call_ops = sorted(o for o in opcodes if o in ("call", "call_indirect"))
    if call_ops:
        v.append(f"call opcode(s) present (M3 has no calls): {call_ops}")
    other = sorted(o for o in opcodes
                   if o.startswith("global.") or o.startswith("table.") or o == "select")
    if other:
        v.append(f"global/table/select opcode(s) present (out of M3 scope): {other}")
    if not any(o.startswith("memory.") or ".store" in o or ".load" in o for o in opcodes):
        v.append("no memory opcode present — M3 targets do not exercise linear memory "
                 "(unexpected; check target selection)")
    return v


def _assert_gate_live() -> list[str]:
    """Prove the fail-closed predicate actually FIRES: feed synthetic out-of-scope constructs
    through scope_violations and confirm each is flagged. If any injection is NOT caught, the gate
    is broken (a green run would be meaningless). Returns a list of self-check failures."""
    base_secs = {"Type", "Function", "Export", "Code", "Memory"}
    base_ops = {"i32.const", "i32.store", "memory.size", "memory.grow"}
    # sanity: the clean base must NOT violate
    fails: list[str] = []
    if scope_violations(base_secs, base_ops):
        fails.append(f"clean base wrongly flagged: {scope_violations(base_secs, base_ops)}")
    injections = [
        ("load op i32.load", base_secs, base_ops | {"i32.load"}),
        ("wide store i64.store", base_secs, base_ops | {"i64.store"}),
        ("narrow store i32.store8", base_secs, base_ops | {"i32.store8"}),
        ("bulk memory.copy", base_secs, base_ops | {"memory.copy"}),
        ("float f32.load", base_secs, base_ops | {"f32.load"}),
        ("call", base_secs, base_ops | {"call"}),
        ("select", base_secs, base_ops | {"select"}),
        ("global.get", base_secs, base_ops | {"global.get"}),
        ("Data section", base_secs | {"Data"}, base_ops),
        ("Global section", base_secs | {"Global"}, base_ops),
    ]
    for label, secs, ops in injections:
        if not scope_violations(secs, ops):
            fails.append(f"gate did NOT flag injected out-of-scope construct: {label}")
    return fails


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


def instrs_of(wasm: Path) -> tuple[list[str], set[str], set[str]]:
    """Return (opcode mnemonics, memarg forms, memidx forms) from `wasm-objdump -d`.
    A store/load line reads e.g. 'i32.store 2 0' (mnemonic, align-exponent, offset); a
    memory.size/grow line reads 'memory.size 0' (mnemonic, reserved memidx)."""
    out = objdump(["-d"], wasm)
    ops: list[str] = []
    memargs: set[str] = set()
    memidxs: set[str] = set()
    for ln in out.splitlines():
        if "|" not in ln:
            continue
        rhs = ln.split("|", 1)[1].strip()
        if not rhs:
            continue
        toks = rhs.split()
        mn = toks[0]
        if mn.startswith("local["):     # local declarations, not instructions (see decoder self-test)
            continue
        ops.append(mn)
        if (".store" in mn or ".load" in mn) and len(toks) >= 3:
            memargs.add(f"{mn} align={toks[1]} offset={toks[2]}")
        if mn in ("memory.size", "memory.grow") and len(toks) >= 2:
            memidxs.add(f"{mn} memidx={toks[1]}")
    return ops, memargs, memidxs


def memory_limits_of(wasm: Path) -> list[str]:
    """Memory-section limits from `wasm-objdump -x`. Lines look like
    ' - memory[0] pages: initial=0' or ' - memory[0] pages: initial=0 max=2'."""
    out = objdump(["-x"], wasm)
    lims: list[str] = []
    for ln in out.splitlines():
        s = ln.strip()
        m = re.search(r"pages:\s*(initial=\d+(?:\s+max=\d+)?)", s)
        if m:
            lims.append(m.group(1).replace("  ", " "))
    return lims


def modules_instantiated(conv: dict) -> list[str]:
    return [c["filename"] for c in conv["commands"]
            if c.get("type") == "module" and c.get("filename")]


def _on_path(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate + fail-closed-gate the M3 linear-memory scope.")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                    help="manifest JSON (default: manifest_m3.json)")
    args = ap.parse_args()

    # Gate self-check FIRST: prove the predicate fires on out-of-scope injections before trusting it.
    gate_fails = _assert_gate_live()
    if gate_fails:
        print("FAIL: M3 scope gate self-check failed — the fail-closed predicate does not fire:")
        for f in gate_fails:
            print(f"    >>> {f}")
        return 1

    if not (os.path.exists(OBJDUMP) or _on_path(OBJDUMP)):
        print(f"FAIL: wasm-objdump not found at {OBJDUMP} (need pinned wabt; run scripts/convert.py first).")
        return 1
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    targets = [t["name"] for t in manifest["targets"]]

    sec_files: dict[str, set[str]] = defaultdict(set)
    op_occ: Counter[str] = Counter()
    op_mods: dict[str, set[str]] = defaultdict(set)
    memargs_all: set[str] = set()
    memidxs_all: set[str] = set()
    limits_all: set[str] = set()
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
        f_lims: set[str] = set()
        for fn in mods:
            wp = CONVERTED / stem / fn
            if not wp.exists():
                print(f"FAIL: module file missing (not skipping): {wp}")
                return 1
            module_count += 1
            for s in sections_of(wp):
                sec_files[s].add(name)
                f_secs.add(s)
            ops, memargs, memidxs = instrs_of(wp)
            for op in ops:
                op_occ[op] += 1
                op_mods[op].add(fn)
                f_ops.add(op)
            memargs_all |= memargs
            memidxs_all |= memidxs
            for lim in memory_limits_of(wp):
                limits_all.add(lim)
                f_lims.add(lim)
        per_file.append({"file": name, "modules": len(mods), "sections": sorted(f_secs),
                         "opcodes": sorted(f_ops), "limits": sorted(f_lims)})

    sections_sorted = sorted(sec_files)
    opcodes_sorted = sorted(op_occ)
    limits_sorted = sorted(limits_all)

    # ---- FAIL-CLOSED scope assertions (the machine gate that blocks decoder/interpreter work) ----
    violations = scope_violations(set(sections_sorted), set(opcodes_sorted))

    # ---- write committed evidence artifact ----
    OUT.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append("M3 SCOPE — enumerated from real data (instantiated modules of the M3 targets), FAIL-CLOSED")
    L.append("=" * 78)
    L.append(f"spec commit : {manifest['spec']['commit']}")
    L.append(f"wabt        : {manifest['wabt']['tag']} (wasm-objdump; authoritative disassembler)")
    L.append(f"targets     : {', '.join(targets)}")
    L.append(f"modules enumerated (type==module instances): {module_count}")
    L.append("method      : wasm-objdump -h (sections) + -d (opcodes + memarg/memidx immediates) +")
    L.append("              -x (memory limits) over the instantiated module set. Read from the")
    L.append("              disassembler, not guessed. Deterministic: sorted; re-run is byte-identical.")
    L.append("")
    L.append(f"BINARY SECTIONS PRESENT (decoder scope) — {len(sections_sorted)} distinct:")
    for s in sections_sorted:
        L.append(f"    {s:12} in: {', '.join(sorted(sec_files[s]))}")
    L.append("")
    L.append(f"MEMORY LIMITS PRESENT (min[, max] pages) — {len(limits_sorted)}:")
    for lim in limits_sorted:
        L.append(f"    {lim}")
    L.append("")
    L.append(f"INSTRUCTION OPCODES PRESENT (interpreter scope) — {len(opcodes_sorted)} distinct:")
    L.append(f"    {'opcode':22} {'occurrences':>11}  {'#modules':>8}")
    for op in opcodes_sorted:
        L.append(f"    {op:22} {op_occ[op]:11d}  {len(op_mods[op]):8d}")
    L.append("")
    L.append(f"MEMARG IMMEDIATES (align-exponent, offset) — {len(sorted(memargs_all))}:")
    for ma in sorted(memargs_all):
        L.append(f"    {ma}")
    L.append(f"MEMIDX IMMEDIATES (reserved; MVP must be 0) — {len(sorted(memidxs_all))}:")
    for mi in sorted(memidxs_all):
        L.append(f"    {mi}")
    L.append("")
    L.append(f"COMMAND-TYPE INVENTORY ({len(targets)} files, {sum(cmd_types.values())} commands):")
    for t, n in sorted(cmd_types.items()):
        L.append(f"    {t:20} {n:5d}")
    L.append("")
    L.append(f"DISTINCT INVOKED EXPORT FIELDS (value asserts + actions): {len(invoked_fields)}")
    L.append("")
    L.append("PER-FILE PROVENANCE:")
    for r in per_file:
        L.append(f"  {r['file']:16} modules={r['modules']:2}  sections={r['sections']}")
        L.append(f"  {'':16} limits={r['limits']}")
        L.append(f"  {'':16} opcodes={r['opcodes']}")
    L.append("")
    L.append("SCOPE FINDINGS (linear-memory milestone):")
    mem = sorted(o for o in opcodes_sorted
                 if o.startswith("memory.") or ".store" in o or ".load" in o)
    L.append(f"  memory opcodes present: {mem if mem else 'NONE'}")
    loads = sorted(o for o in opcodes_sorted if ".load" in o)
    L.append(f"  load opcodes present (must be NONE for M3): {loads if loads else 'NONE'}")
    wide_stores = sorted(o for o in opcodes_sorted if ".store" in o and o not in ALLOWED_STORES)
    L.append(f"  non-i32.store store opcodes (must be NONE): {wide_stores if wide_stores else 'NONE'}")
    flt = sorted(o for o in opcodes_sorted if o.startswith("f32.") or o.startswith("f64."))
    L.append(f"  float opcodes present (must be NONE): {flt if flt else 'NONE'}")
    call = sorted(o for o in opcodes_sorted if o in ("call", "call_indirect"))
    L.append(f"  call opcodes present (must be NONE): {call if call else 'NONE'}")
    L.append(f"  Data section present (must be NONE for M3): "
             f"{'YES' if 'Data' in sections_sorted else 'NONE'}")
    L.append("")
    L.append("SCOPE GATE (fail-closed; asymmetric allow-set + residual bans; self-check passed):")
    if violations:
        L.append("  VERDICT: OUT OF SCOPE — the following must be re-scoped before any executor code:")
        for v in violations:
            L.append(f"    >>> {v}")
    else:
        L.append("  VERDICT: IN SCOPE — sections ⊆ {Type,Function,Export,Code,Memory}; opcodes ⊆ frozen")
        L.append("           M3 set; loads/non-i32-stores/non-size-grow-memory/float/call/Data = NONE;")
        L.append("           ≥1 memory opcode present. Decoder work unblocked.")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---- stdout summary ----
    print("\n".join(L))
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    print("gate self-check: PASS (predicate flags load/wide-store/bulk-memory/float/call/select/"
          "global/Data injections)")
    if violations:
        print("\nFAIL: M3 data stepped outside the frozen scope (see SCOPE GATE above). "
              "STOP and re-scope — do not extend the decoder to silently absorb it.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
