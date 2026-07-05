# M2 — Structured Control Flow (execution plan)

Baseline: `main` @ `ca2abb1` (clean). Work branch (to be created at first commit, owner-gated):
`m2-control-flow`. Risk: **medium** (extends the shared `interp/decoder.py` + `interp/machine.py`;
reversible, local, no external effects). Audit: **independent-subagents** (medium ⇒ cannot claim
completed via self-check). Size: **standard**.

All changes are additive files plus backward-compatible extensions; M0/M1 behavior is preserved
and re-verified. `vendor/` and `build/` stay gitignored/regenerable.

## Design decisions (locked from Step-0 data)

1. **Targets** = `{labels.wast, switch.wast}` (frozen in NEW `manifest_m2.json`). Data-selected as
   the only integer-value-clean, control-flow-only, no-new-section candidates. `manifest_m0.json`
   untouched.
2. **Decoder stays flat.** Function body remains a flat `list[Instr]` (block/loop/if/else/end/br/…
   are flat tokens), so `tests/decoder_selftest.py`'s `[ins.op for ins in body]` matches
   `wasm-objdump -d` token-for-token with no change to its comparison logic. New immediates:
   block-type (empty/`i32`/`i64`; float/typeidx ⇒ `Unsupported`) and br_table (`vec(u32)`+`u32`).
   The existing linear read to the code-entry boundary already consumes nested blocks — only the
   immediate decoders and opcode table grow.
3. **Interpreter parses structure from the flat body, then recurses.** `machine` builds a nested
   block tree (block/loop/if with then/else) from the flat list once per function, then evaluates
   with a shared value stack and a label stack carrying `(kind, result-arity, stack-base)`. `br l`
   arranges results and raises `Branch(l)`; block/if frames catch depth 0 (exit past `end`), loop
   frames catch depth 0 (re-enter at loop start); `return` raises to the function frame. This makes
   branch semantics auditable and avoids hand-rolled jump-offset bugs. Integer opcode handling is
   reused unchanged from M1.
4. **Shared tools parameterized, not duplicated, with backward-compatible defaults** (`--manifest`
   defaulting to `manifest_m0.json`; `convert.py` also `--report`): `scripts/convert.py`,
   `tools/assert_operand_purity.py`, `tools/body_purity_check.py`, `tests/decoder_selftest.py`.
   M0/M1 CI invokes them with no args ⇒ identical behavior (proven by re-running M0+M1 gates).
   `scripts/run_m1.py` and `tools/enumerate_m1_scope.py` and the pinned `m1-scope.txt` are **not**
   touched; `run_m2.py` imports `run_m1.run_file`/`FileResult` (import, not modify) to stay DRY
   while keeping M1's runner independent.

## Milestones (each with a binary validation check)

### M2.0 — Freeze targets + Step-0 scope evidence (data gate, FAIL-CLOSED)
- Add `manifest_m2.json` (targets + reasoned exclusions).
- Add `tools/enumerate_m2_scope.py`; convert the M2 targets; generate
  `goal-runs/m2-control-flow/scope.txt` (sections, opcodes+counts, block-type forms, command
  inventory, per-file provenance, scope findings). Unlike the M1 enumerator (report-only), this one
  **asserts the subset and exits nonzero** on any violation — the gate is the exit code, not a
  human reading `scope.txt`.
- **Verify (binary):** `enumerate_m2_scope.py` exits 0 **only if** sections ⊆
  `{Type,Function,Export,Code}`, opcodes ⊆ the frozen M2 set
  `{…M1 integer set…} ∪ {nop,block,loop,if,else,br,br_if,br_table,drop}`, block-types ⊆
  `{empty,i32,i64}`, float opcodes = NONE, memory opcodes = NONE. Any superset item ⇒ **nonzero
  exit** (blocks M2.1), and the run STOPS for re-scope (owner-visible). Then
  `git diff --exit-code goal-runs/m2-control-flow/scope.txt` (reproducible).

### M2.1 — Decoder extension
- Extend `interp/decoder.OPCODES` with `nop(0x01) block(0x02) loop(0x03) if(0x04) else(0x05)
  br(0x0C) br_if(0x0D) br_table(0x0E) drop(0x1A) local.set(0x21)`; keep unknown constructs
  `Unsupported`. (`local.set` is data-forced — the fail-closed enumerator found both targets use it
  on i32/i64 locals; integer-pure companion to the existing `local.get`. `local.tee` (0x22) absent.)
- **Block-type = signed LEB128, own reader (NOT `_valtype`):** `0x40→empty`, `0x7F→i32`,
  `0x7E→i64`, `0x7D/0x7C→Unsupported` (float), non-negative→`Unsupported` (multi-value typeidx).
  `_valtype` reads an unsigned byte and would misread `0x40`; block-type needs its own decode.
- **`else`/`end` stay standalone flat tokens** so `decoder_selftest`'s `[ins.op for ins in body]`
  stays token-equal to `wasm-objdump -d`. `br_table` immediate = `vec(u32)` targets + `u32` default
  (incl. the zero-target form).
- **Verify (binary):** `tests/decoder_selftest.py` (no arg) still passes over the 22 M1 modules
  (M0/M1 decode unchanged, byte-for-byte); `tests/decoder_selftest.py --manifest manifest_m2.json`
  passes over the M2 modules — func count, exports, and every opcode stream (incl. every standalone
  `else`/`end` and every `block/loop/if` header, empty and `i32`/`i64` result forms) match
  `wasm-objdump`. This self-test IS the evidence the new opcode bytes + block-type/br_table
  immediate boundaries are correct (opcode bytes proven against the disassembler, not memory).

### M2.2 — Interpreter structured evaluator
- Evolve `interp/machine.py`: structure-parse the flat body (pair interior `end`s with their
  openers, disambiguate optional `else`, treat the function's terminal `end` as the body sentinel —
  not a block close), then recursive evaluator with a label/value stack carrying
  `(kind, result-arity, stack-base)`; wire `block/loop/if/else/br/br_if/br_table/return/drop/nop`;
  integer ops reused unchanged from M1.
- **Named invariant — label depth:** `br l` targets the `l`-th enclosing label (0 = innermost);
  block/if target ⇒ transfer past `end`; loop target ⇒ transfer to loop header. Off-by-one ⇒ wrong
  VALUE ⇒ FAIL in the oracle diff (not hidden as UNSUPPORTED).
- **Verify (binary):** `tests/test_semantics.py` (M1 units) still green; new
  `tests/test_control_flow.py` green, covering as *distinct* cases: (a) block result value out via
  `br`; (b) if-true / if-false; (c) then-only `if` AND then/else `if` in one function both correct;
  (d) loop + `br_if` countdown (loop re-entry); (e) nested `br` depth 0/1/2; (f) **result-value
  transfer on `br`** proven separately from depth; (g) `br_table` in-range, default fallthrough,
  AND the zero-target `br_table 0` form (`switch.wast:114`); (h) `return` escape from nested blocks;
  (i) `drop`; (j) `nop`; (k) arity 0 vs 1. Authoritative oracle check is M2.3 (`run_m2.py`).

### M2.3 — M2 assert-runner + gate
- Add `scripts/run_m2.py` (imports `run_m1.run_file`/`FileResult`), manifest_m2-driven discovery,
  `build/report/m2_summary.json`, nonzero on FAIL, no-drop assertion.
- **Verify (binary):** `run_m2.py` exits 0 with `modules=2, PASS=51, FAIL=0, UNSUPPORTED=4`,
  `2+51+0+4==57`, every UNSUPPORTED reason is a validation command.

### M2.4 — Positive control (anti-false-pass)
- Add `tests/positive_control_m2.py`: pristine real M2 module+assertion ⇒ FAIL==0, PASS≥1;
  corrupt one `expected` ⇒ FAIL≥1.
- **Verify (binary):** exits 0 (comparator fires on the injected wrong answer, passes the clean one).

### M2.5 — Shared-tool parameterization + purity over M2 targets
- Add backward-compatible `--manifest` (+`--report` for convert) to `convert.py`,
  `assert_operand_purity.py`, `body_purity_check.py`, `decoder_selftest.py`.
- **Verify (binary):** M0/M1 invocations (no arg) unchanged and green; `assert_operand_purity.py
  --manifest manifest_m2.json` and `body_purity_check.py --manifest manifest_m2.json` both exit 0
  (labels/switch are operand- and body-integer-pure).

### M2.6 — CI workflow + README
- Add `.github/workflows/m2.yml`: fetch → convert (m2 manifest) → M2 purity gates → re-derive
  `scope.txt` + `git diff --exit-code` → decoder self-test (m2) → control-flow units → M2 positive
  control → `run_m2.py` → self-contained assertions (FAIL==0, no-drop, total==conversion total,
  UNSUPPORTED are validation-only). Append an M2 section to `README.md` (no overstated conformance).
- **Verify (binary):** `python -c "import yaml,glob;[yaml.safe_load(open(f)) for f in
  glob.glob('.github/workflows/*.yml')]"` parses; local dry-run of every m2.yml step passes; M0+M1
  workflows unmodified (`git diff` shows only additive/backward-compatible changes).

### M2.7 — Non-regression sweep + closeout
- Re-run the full M0 + M1 gate set and confirm no regression.
- **Verify (binary):** `run_skeleton.py` supported==0; `assert_operand_purity.py` /
  `body_purity_check.py` (no arg) exit 0; `enumerate_m1_scope.py` reproduces `m1-scope.txt`
  (`git diff --exit-code`); `decoder_selftest.py` (no arg) exit 0; `test_semantics.py` exit 0;
  `positive_control.py` exit 0; `run_m1.py` ⇒ `PASS=877, FAIL=0, UNSUPPORTED=136`.

## Validation matrix (evidence → acceptance)

| Acceptance criterion | Command | Expected |
|---|---|---|
| M2 scope is data-derived & reproducible | `tools/enumerate_m2_scope.py` then `git diff --exit-code scope.txt` | exit 0, no diff |
| Decoder correct vs authoritative disassembler | `tests/decoder_selftest.py --manifest manifest_m2.json` | exit 0 |
| Control-flow semantics | `tests/test_control_flow.py` | exit 0 |
| Oracle diff, zero FAIL, no-drop | `scripts/run_m2.py` | `PASS=51 FAIL=0 UNSUPPORTED=4`, exit 0 |
| Comparator actually fires | `tests/positive_control_m2.py` | exit 0 |
| M2 targets integer-pure | `assert_operand_purity.py` / `body_purity_check.py --manifest manifest_m2.json` | exit 0 |
| M1 not regressed | `scripts/run_m1.py` | `PASS=877 FAIL=0 UNSUPPORTED=136`, exit 0 |
| M0 not regressed | `run_skeleton.py` + M0 purity gates | supported=0; gates exit 0 |

## Assumptions & how each is verified
- *The 2 targets need only the enumerated opcodes/sections/block-types.* → M2.0 enumeration +
  M2.1 decoder self-test; anything else surfaces as `Unsupported`/mismatch, not silent mis-exec.
- *M0/M1 modules are unaffected by decoder/machine changes.* → M2.7 re-runs M1 decoder self-test,
  semantics, and `run_m1.py` (877/0/136) with no arg.
- *Backward-compatible `--manifest` default preserves M0/M1 CI.* → M2.5/M2.7 run those tools with
  no args and confirm identical results; `git diff` shows only additive arg-parsing.
- *No `assert_trap` in the M2 targets.* → Step-0 command inventory; runner still handles trap
  commands generically for future targets.

## Rollback
Every change is a new file or an additive/backward-compatible edit on a feature branch. Rollback =
discard the branch (nothing merged, nothing pushed until owner-approved). `manifest_m0.json`,
`m1-scope.txt`, `run_m1.py`, `enumerate_m1_scope.py`, and the M0/M1 workflows are not modified, so
M0/M1 remain intact regardless of M2's state.

## Owner-only boundaries (pause for explicit OK)
- Creating the `m2-control-flow` branch's first commit, `git push`, and opening the PR to `main`.
- No force-push, no merge (owner merges), no history rewrite.
