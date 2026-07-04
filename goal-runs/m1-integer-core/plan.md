# M1 — Integer Core Execution — Plan

Run slug: `m1-integer-core` · Baseline: `main` @ `f5096f0` · Branch: `m1-integer-core`
Size: standard · Risk: medium · Audit: independent-subagents

## Contract-vs-repo drift (must be resolved before executor design)

The M1 contract prose describes a **C++/CMake/CTest** world: "CTest gate `m1_execute`",
"MSVC/g++", "`core-ctest.yml`'s generic ctest", "PR #2 picks it up", "the loadout core".
None of these exist in this repo. Verifiable repo facts:

- Runner skeleton: `scripts/run_skeleton.py` (Python).
- Purity gates: `tools/assert_operand_purity.py`, `tools/body_purity_check.py` (Python).
- CI: `.github/workflows/m0.yml` (Linux; Python + OCaml). There is **no** `core-ctest.yml`,
  no `CMakeLists.txt`, no C/C++ anywhere.

Decision (per "match the surrounding code" + "record drift, resolve with verifiable facts"):
**implement M1 in Python**, realizing the contract's *substance* — enumerate-first scope,
exact integer semantics, PASS/FAIL/UNSUPPORTED classification with trap-matching, a comparator
positive-control, a CI gate that is nonzero on any FAIL, and M0's two purity gates kept green —
not its drifted C++ mechanics. The "CTest gate" becomes a new CI step + a self-contained
`pytest`-free assertion script, consistent with how M0's gates are wired.

## Data-gating discipline (the core of this milestone)

The executor's opcode set and the decoder's section scope are **derived from real data**, never
guessed. Step 0 (Milestone 1) enumerates them from the instantiated `.wasm` of the 4 frozen
files and emits committed evidence. Milestones 2+ are specified *against that evidence*; this
plan deliberately leaves the opcode list unbound until Milestone 1 completes, then re-plans.

## Milestones

### M1.1 — Step 0: enumerate real scope (DATA-GATED, blocks everything else)
- Generate `build/converted/<stem>/{<stem>.json,*.wasm}` for the 4 targets (WSL + pinned WABT,
  reproducing CI exactly). *(fetch+convert already running.)*
- Write `tools/enumerate_m1_scope.py`: over the JSON `type=="module"` instantiated `.wasm`
  (the SAME set `body_purity_check.py` uses), disassemble with `wasm2wat` and enumerate
  (a) the distinct instruction opcodes, (b) the distinct binary sections present. Also tally
  the command-type inventory and the distinct `action.field` exports invoked.
- Emit `goal-runs/m1-scope.txt` (committed): the opcode list, the section list, per-file
  provenance, and counts.
- **Verify (binary):** `goal-runs/m1-scope.txt` exists; opcode list non-empty; every listed
  section ∈ a known WASM section set; re-running the tool is byte-identical (determinism).
- **CHECKPOINT:** read the enumerated scope, present it, then bind M1.3's opcode list to it.

### M1.2 — Decoder (scoped to the enumerated sections)
- Pure-stdlib Python WASM binary decoder for exactly the sections M1.1 found (expected: type,
  function, export, code — confirmed, not assumed, by M1.1). Unknown section ⇒ explicit
  UNSUPPORTED, never silent accept.
- **Verify (binary):** for each instantiated `.wasm`, decoded export names + function count
  match `wasm2wat` output; a decoder self-test script exits 0.

### M1.3 — Interpreter (scoped to the enumerated opcodes) — opcode list bound by M1.1
- i32/i64 value stack (stdlib ints, masked to 32/64 bits). Dispatch over the enumerated opcode
  set only. Any opcode outside the set ⇒ UNSUPPORTED (not mis-executed). Exact semantics:
  wrapping arith mod 2^N; shift counts mod 32/64; arithmetic vs logical shr; rotl/rotr;
  clz/ctz/popcnt; rem_s sign follows dividend; comparisons/eqz ⇒ i32 0/1; wrap/extend ops;
  div/rem traps (÷0, INT_MIN/-1 overflow for signed div/rem).
- **Verify (binary):** targeted unit tests for each tricky semantic (edge values, trap cases)
  pass; determinism holds.

### M1.4 — Assert-runner (classify, never skip)
- Iterate JSON `commands`; on `module` load+instantiate; on `assert_return`/`assert_trap`/
  `action` invoke + classify PASS/FAIL/UNSUPPORTED; validation commands (`assert_invalid`/
  `assert_malformed`/…) ⇒ UNSUPPORTED with a count. Bitwise compare i32/i64 result vs
  `expected.value` (unsigned decimal). Trap expected ⇒ execution must trap (in-scope int traps).
  Exit nonzero on any FAIL. Emits `build/report/m1_summary.json`.
- **Verify (binary):** run over 4 files ⇒ zero FAIL; all in-scope asserts PASS; UNSUPPORTED
  tallied (== validation + any out-of-scope opcode commands); supported+unsupported == total
  (no silent skip, same invariant as M0).

### M1.5 — Comparator positive control (防复发)
- A test that feeds a deliberately wrong `expected` and asserts the comparator returns FAIL, so
  a green run proves the comparator fires (not that it silently passes). Mirrors the const.wast
  positive control that proved the purity gates fire.
- **Verify (binary):** positive-control script exits 0 *because* it observed the injected FAIL;
  if the comparator failed to fire, the script exits nonzero.

### M1.6 — CI gate + keep M0 green
- Add an M1 execution gate to CI (new step in `m0.yml`, or a sibling workflow) running the
  assert-runner over the 4 files, nonzero on any FAIL, printing the PASS/FAIL/UNSUPPORTED tally.
  Keep both M0 purity gates and the M0 skeleton assertions green alongside.
- **Verify (binary):** locally reproduce the full chain (fetch→convert→purity gates→M1 runner→
  positive control) green; `run_skeleton.py` M0 invariants still hold (M0 not regressed).

## Assumptions & how each is verified
- *Sections are exactly {type,function,export,code}* → **verified** by M1.1 enumeration (not assumed).
- *Opcodes are a straight-line integer core, no structured control flow* → **verified/refuted** by
  M1.1. If control-flow opcodes appear (block/loop/if/br*), that is a recorded scope finding →
  re-plan (implement the minimal control flow the 4 files need, or record as M2 boundary), NOT a
  silent pull-forward.
- *WABT `expected.value` is unsigned-decimal bit pattern* → verified against a real JSON sample in M1.1.
- *WSL pinned WABT == CI output* → same asset, sha256-verified; determinism cross-checked.

## Rollback
- All new code is additive (new files); `manifest_m0.json`, M0 scripts/gates unchanged. Branch
  `m1-integer-core` off `main`; revert = delete branch. `vendor/`+`build/` are gitignored.
- CI edit to `m0.yml` is the only edit to an existing tracked file; it only *adds* steps.

## Owner-only boundaries
- No `git push`, no PR creation, no force-push — pause for explicit user OK before any of these.
- No destructive ops. Nothing outside the repo.

---

## M1.1 RESULT — scope BOUND from real data (`goal-runs/m1-scope.txt`, determinism-verified)

Enumerated over the 22 instantiated (`type=="module"`) modules of the 4 frozen targets with
the pinned WABT `wasm-objdump`. **No re-plan required** — findings match the contract's
straight-line-integer-core hypothesis exactly.

**Decoder scope — 4 sections:** `Type`, `Function`, `Export`, `Code`. (No Import/Global/
Memory/Table/Data/Element/Start — decoder rejects anything else as UNSUPPORTED.)

**Interpreter scope — 71 opcodes (M1.3 is now bound to exactly these):**
- structural: `end`, `return`  (NO block/loop/if/else/br/br_if/br_table — confirmed absent)
- const/local: `i32.const`, `i64.const`, `local.get`  (NO local.set/local.tee)
- i32 binary: add sub mul div_s div_u rem_s rem_u and or xor shl shr_s shr_u rotl rotr
- i32 unary: clz ctz popcnt eqz extend8_s extend16_s
- i32 compare: eq ne lt_s lt_u le_s le_u gt_s gt_u ge_s ge_u
- i32 conv: `i32.wrap_i64`
- i64 binary: add sub mul div_s div_u rem_s rem_u and or xor shl shr_s shr_u rotl rotr
- i64 unary: clz ctz popcnt eqz extend8_s extend16_s extend32_s
- i64 compare: eq ne lt_s lt_u le_s le_u gt_s gt_u ge_s ge_u
- i64 conv: `i64.extend_i32_s`, `i64.extend_i32_u`

**Findings:** control-flow = NONE, memory = NONE, float = NONE. Interpreter = linear body
evaluator (execute in sequence; `return`/body-`end` finish with stack as results).

**Command inventory to classify:** module=22 (instantiate), assert_return=843 + assert_trap=34
= 877 in-scope value asserts (→ PASS/FAIL), assert_invalid=112 + assert_malformed=24 = 136
validation (→ UNSUPPORTED, counted). 126 distinct invoked export fields.
