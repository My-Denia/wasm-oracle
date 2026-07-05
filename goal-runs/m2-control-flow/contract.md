# M2 — Structured Control Flow (goal contract)

Follows M1 (COMPLETE, merged to `main` via PR #3): an integer straight-line execution core over
4 frozen targets, `PASS=877 / FAIL=0 / UNSUPPORTED=136` across 1035 commands, scope derived from
real data (4 sections `{Type,Function,Export,Code}`, 71 integer opcodes, no control flow / memory
/ float). M2 extends the interpreter to **structured control flow** — and nothing else. No linear
memory, no globals, no calls, no validation execution, no floats (those stay deferred to
M3–M5 and beyond).

The repo's standing discipline holds verbatim: **verification-before-implementation**,
**data-gated scope** (enumerate from real `.wasm`, never guess the instruction set),
**no silent skip** (`modules + PASS + FAIL + UNSUPPORTED == total`), **external oracle only** (the
`expected` values in the converted JSON were authored by the WebAssembly spec reference
interpreter; we author no expected values), and **Python-only** unless repo facts force otherwise.

## Step 0 finding — the target set is a curation problem, and the data resolves it

The canonical control-flow files in `test/core` (`block`, `loop`, `if`, `br`, `return`,
`unreachable`, `call`) are **not** integer-value-clean: each has exactly one big instantiated
module that mixes `f32`/`f64` functions in with the integer ones (`float_mods=1`), and each pulls
in `call` plus `Memory`/`Global`/`Table`/`Elem` sections. Adopting any of them wholesale would
violate the body-purity discipline and blow past control-flow-only scope. Several dedicated files
(`br_if`, `br_table`, `select`, `call_indirect`, `func`) **fail conversion** under the frozen M0
guardrail flags (`--disable-reference-types` etc.) because they use typed function references /
typed `select` / `externref` / `declare` — genuinely out of MVP-integer scope at pin `82cd4f9`.

A read-only probe (convert each candidate under the M0 guardrail flags; measure module float
purity via `wasm2wat`, operand types, sections, and opcodes via `wasm-objdump`) identifies exactly
**two** files that are integer-value-clean, stay inside the existing 4 sections, pull in no
call/memory/global/table/float, and exercise real structured control flow:

| target | assert_return | assert_invalid | control opcodes present |
|---|---|---|---|
| `labels.wast` | 25 | 3 | block, loop, if, else, br, br_if, br_table, return, drop |
| `switch.wast` | 26 | 1 | block, br, br_table, nop, return |

Their union is the complete minimal MVP structured-control-flow set
`{block, loop, if, else, br, br_if, br_table, return, drop, nop}` — with **no** `select`,
**no** `unreachable`, **no** new sections. Block-type immediates observed are **empty / `i32` /
`i64` only** (no multi-value typeidx, no float block types). `labels` contains `if` **without**
`else` (17 `if`, 9 `else`), so if-without-else must be handled.

**Drift (data-forced, recorded):** the fail-closed Step-0 enumerator surfaced one opcode the
first probe missed — **`local.set` (0x21)**, used heavily in both files (48 in `labels`, 7 in
`switch`) to store into i32/i64 locals. M1 implemented `local.get` (0x20) but never `local.set`
(the M1 targets used only params). `local.set` is integer-pure, not control flow, and needed by
these files — so M2 adds it alongside the control-flow ops. (`local.tee` (0x22) is **absent**.)
This is the gate working as intended: a required opcode was forced into the open before any
interpreter code, not discovered by a later FAIL. The frozen M2 opcode additions are therefore
`{nop, block, loop, if, else, br, br_if, br_table, drop, local.set}`.

### M2 targets (frozen in a NEW `manifest_m2.json`, M0/M1 contract untouched)
`labels.wast`, `switch.wast` — 57 commands total (2 module + 51 `assert_return` + 4
`assert_invalid`).

### Reasoned exclusions (data-backed; recorded in `manifest_m2.json`)
- `block, loop, if, br, return, unreachable, call`: has-float module (`float_mods=1`) + `call` +
  extra sections → fail body-purity and exceed control-flow-only scope.
- `br_if, br_table, select, call_indirect, func`: reject at conversion under the M0 guardrail
  (reference-types / typed-select / externref / declare). The `br_if` and `br_table` **opcodes**
  are still covered — by `labels` and `switch` respectively.
- `fac, forward`: integer-clean but need `call` — deferred with the call milestone.
- `stack, nop`: integer-clean operands but need global/memory/table/call + extra sections.

## Deliverables

DELIVERED by M2:
- A NEW `manifest_m2.json` freezing the 2 data-selected targets + reasoned exclusions. M0/M1's
  `manifest_m0.json` is not touched.
- Step 0 evidence: a new `tools/enumerate_m2_scope.py` writing committed
  `goal-runs/m2-control-flow/scope.txt` (sections, opcodes, block-types, command inventory,
  per-file provenance) — reproducible and CI-diffed, exactly like `m1-scope.txt`.
- Decoder extension (`interp/decoder.py`): decode `nop, block, loop, if, else, br, br_if,
  br_table, drop, local.set` + block-type and br_table immediates. Unknown
  section/valtype/opcode/blocktype stays `Unsupported` (no silent accept). M0/M1 modules must
  decode **identically** (they contain none of the new opcodes), proven by the unchanged M1
  decoder self-test.
- Interpreter extension (`interp/machine.py`): from the linear `_run` to a **structured evaluator**
  with a label/value stack — correct block/loop/if/else entry, branch depth, loop re-entry, br /
  br_if / br_table target transfer, and return escape. Input is the decoded `.wasm`, not re-parsed
  text. Integer semantics from M1 must not regress.
- A NEW `scripts/run_m2.py` (M2 assert-runner) → `build/report/m2_summary.json`, nonzero on any
  FAIL; PASS/FAIL/UNSUPPORTED classification, no-drop accounting. M1's `scripts/run_m1.py` stays
  independent and unchanged.
- New tests: `tests/test_control_flow.py` (block value, if branch selection, if-without-else,
  loop+br_if countdown, br depth, br_table default/in-range, return escape, drop, nop, result
  arity) and `tests/positive_control_m2.py` (corrupt one real M2 `expected` → FAIL must fire).
- New CI: `.github/workflows/m2.yml` — independent workflow; does not touch `m0.yml`/`m1.yml`.

GATED ON-MACHINE (from real data, Step 0, before any executor code) — **fail-closed**:
- `tools/enumerate_m2_scope.py` must not merely *report* the opcode/section/block-type inventory
  (as the M1 enumerator does): it must **exit nonzero** if any enumerated opcode ∉ the frozen M2
  set `{end, return, local.get, i32/i64 integer ops…} ∪ {nop, block, loop, if, else, br, br_if,
  br_table, drop}`, any section ∉ `{Type,Function,Export,Code}`, any block-type ∉
  `{empty, i32, i64}`, or any float/memory opcode appears. The data gate is a machine assertion
  that blocks M2.1, not a note a human reads off `scope.txt`. (This repo has twice been burned by
  scope decided by inspection instead of a fail-closed check — AGENTS.md §2.)

## Named decode / branch invariants (audit-required checks, allocated before code)

These are named here so a specific check is allocated to each — not left as implementation detail:

- **Block-type is a signed LEB128, decoded by its OWN reader** (not the unsigned single-byte
  `decoder._valtype`, which would misread `0x40`): `0x40 → empty`, `0x7F → i32`, `0x7E → i64`;
  `0x7D/0x7C → Unsupported` (float block result); any **non-negative** value → `Unsupported`
  (multi-value typeidx). Evidence: the M2 decoder self-test over the real modules must decode every
  `(block …)/(loop …)/(if …)` header (empty and `i32`/`i64` result forms both occur) without
  raising, and match `wasm-objdump`.
- **`else` and `end` are standalone flat tokens.** The decoder must emit `Instr("else")` (0x05)
  and `Instr("end")` (0x0B) as their own flat tokens so `tests/decoder_selftest.py`'s
  `[ins.op for ins in body]` stays token-for-token equal to `wasm-objdump -d`'s first-token stream
  (which provably renders structural opcodes flat — M1 already emits `end`/`return` flat and its
  self-test is green over 22 modules). If objdump rendered `else` differently the "no change to the
  comparator" claim would break; the M2 decoder self-test is the evidence it does not.
- **Structure-parse pairs interior `end`s with their openers; the terminal `end` is the body
  sentinel.** `labels.wast` has a function mixing then-only `if` and then/else `if`; the parser
  must disambiguate optional `else`, and must not consume the function's final `end` as a block
  close. (The flat linear read to the code-entry boundary already stops correctly; the *nested
  parse* is what must pair `end`s.)
- **Branch label-depth convention (named invariant):** `br l` targets the `l`-th enclosing label
  counting from **0 = innermost**. For a `block`/`if` target, control transfers **past** its `end`
  (exit); for a `loop` target, control transfers to the loop **header** (re-entry). An off-by-one
  here surfaces as a wrong VALUE in an instantiated module → **FAIL** (not UNSUPPORTED), so it is
  caught by the oracle diff, not hidden. `br_table` decodes as `vec(u32)` targets + `u32` default;
  `switch.wast:114` is a **zero-target** `br_table 0` (default only) and must resolve correctly.
- **Result-value transfer on branch is a check distinct from correct depth.** `labels.block`
  carries a value out via `br` with a block result; `switch`'s `arg` threads values through
  `br_table` with block results. "br to the correct depth" and "the block/loop result value is
  transferred on br" are two independent bugs; both are asserted — the unit tests localize them and
  `run_m2.py` over the real modules is the authoritative oracle check.

DEFERRED (unchanged): M3 linear memory, M4 validation, M5 floats; also `call`/`call_indirect`,
`select`, `unreachable`, `global`, `table`, multi-value block types — none are needed by the M2
targets and none are implemented.

## Oracle & acceptance

Oracle is embedded and frozen: each `assert_return.expected` was authored by the reference
interpreter, so bitwise-diffing our invoke result against `expected` **is** diffing against the
reference-interpreter oracle. No expected value is authored or modified here.

Per-command classification (tallied, none dropped):
- PASS — invoke result equals `expected` (bitwise i32/i64).
- FAIL — any mismatch (wrong value, wrong arity, unexpected/absent trap, missing export, or an
  in-scope module that fails to decode). Any FAIL fails the gate (nonzero exit).
- UNSUPPORTED — command/opcode outside M2 scope (`assert_invalid`/`assert_malformed` validation, or
  an out-of-scope opcode). Reported with a count, never skipped.

Expected M2 result over the 2 targets: `modules=2, PASS=51, FAIL=0, UNSUPPORTED=4`
(`2 + 51 + 0 + 4 == 57`). The 4 UNSUPPORTED are the `assert_invalid` validation commands.
(Neither target contains `assert_trap`, so M2 asserts control-flow **values**, not traps; the
integer traps remain covered by M1.)

Acceptance:
- M0 gates still green (unchanged).
- M1 gates still green; `scripts/run_m1.py` still `PASS=877, FAIL=0, UNSUPPORTED=136`.
- M2 scope evidence reproducible and deterministic (CI re-derives and `git diff --exit-code`).
- M2 runner: zero FAIL over `{labels, switch}`; every command bucketed; every UNSUPPORTED counted
  with a reason.
- Comparator positive control for M2: a deliberately wrong `expected` on a real M2 assertion must
  classify FAIL — a green run must be evidence the comparator fires.
- Decoder self-test still passes over the M1 modules (M0/M1 decode unchanged) and, extended to the
  M2 modules, matches `wasm-objdump` for func count, exports, and every opcode stream (validating
  the new opcode bytes and immediate boundaries against the authoritative disassembler).
- README gains an M2 section that does not overstate conformance.

## Non-goals / invariants preserved
- Do not modify `manifest_m0.json`, the M0/M1 CI assertions, or the pinned `m1-scope.txt`.
- Do not weaken the two purity gates; M2 targets must pass the same integer-purity discipline.
- No `Co-Authored-By`/AI trailer in commits. No push/PR/force-push without explicit owner OK.
- No authored expected values; WABT stays toolchain-only, never a semantic oracle.
