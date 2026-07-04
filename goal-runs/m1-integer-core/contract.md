# M1 — Integer Core Execution (goal contract, verbatim from user)

Follows M0 (COMPLETE: harness-only, frozen manifest at a provably-stable pin, 4 integer
targets, two sound purity gates in CI). M1 implements the integer execution core and runs it
against the frozen 4 files, diffing every value assertion against the reference-interpreter
oracle. No structured control flow, no memory, no validation, no floats (those are M2–M5).

## Completion criteria & coverage (exhaustive-task discipline)

DELIVERED by M1:
- A WASM binary decoder scoped to the sections the 4 files actually use.
- An integer-only interpreter for the opcode set the 4 files actually contain.
- An assert-runner that executes each `assert_return`/`assert_trap` and classifies it
  PASS / FAIL / UNSUPPORTED — never silently skipped (consistent with M0's 1035-command,
  supported=0 baseline and the no-silent-skip invariant).
- A new CTest gate over the 4 files, in CI, nonzero on any FAIL.

GATED ON-MACHINE (from real data):
- The exact distinct integer opcode set across the 4 files' INSTANTIATED modules.
- The exact set of binary sections those modules use (decoder scope).

DEFERRED: M2 structured control flow, M3 linear memory, M4 validation, M5 floats.

## Step 0 — derive scope from real data (on-machine, first)

Before any executor code, enumerate — using the SAME disassembly the now-sound body gate uses
(over the instantiated `.wasm`, JSON `type=="module"` entries):
1. The distinct opcodes actually present → the M1 instruction-implementation list.
2. The distinct binary sections present → the decoder's scope.
Emit both as committed artifacts (`tools/enumerate_m1_scope.py` → `goal-runs/m1-scope.txt`).

## Oracle & acceptance

Oracle is embedded and frozen: the `expected` values in each `assert_return` were authored by
the WebAssembly spec reference interpreter. Diffing our result against `expected` IS diffing
against the reference-interpreter oracle.

Per-command classification (tallied, none dropped):
- PASS — invoke result equals `expected` (bitwise for i32/i64).
- FAIL — mismatch. Any FAIL fails the gate.
- UNSUPPORTED — command/opcode outside M1 scope (e.g. assert_invalid, or an opcode not in the
  enumerated set). Reported with a count, never skipped.
- assert_trap — execution must trap where a trap is expected. In-scope integer traps have two
  texts: "integer divide by zero" (div_s/div_u/rem_s/rem_u by zero) and "integer overflow"
  (signed division div_s of INT_MIN by -1). Signed remainder rem_s of INT_MIN by -1 does NOT
  trap; it yields 0. [Corrected from the original contract's "div_s/rem_s overflow at INT_MIN/-1",
  which wrongly implied rem_s overflow-traps.]

Integer semantics that MUST be exact: wrapping arithmetic mod 2^N; shift counts masked mod
32/64; arithmetic vs logical shift for shr_s/shr_u; rotl/rotr; clz/ctz/popcnt; sign of rem_s
following the dividend; eqz and comparisons producing i32 0/1; i32.wrap_i64;
i64.extend_i32_s/_u; i32/i64.extendN_s sign-extension ops if present in the enumerated set.

Acceptance:
- All in-scope assert_return/assert_trap across the 4 files → PASS.
- Out-of-scope → UNSUPPORTED with a count; zero FAIL.
- Determinism: identical results across runs and toolchains.
- External reproduction: a CI gate over the 4 files (nonzero on any FAIL).
- Comparator positive control: the test suite MUST include a case that feeds a deliberately
  wrong `expected` and confirms FAIL fires. A green run must be evidence the comparator works.

## Executor architecture (engine-agnostic, integer-only)
- Decoder: parse the sections the 4 files use. Reject/UNSUPPORTED anything outside scope.
- Value stack: i32/i64 only. Any float opcode → UNSUPPORTED, not mis-executed.
- Interpreter: dispatch over the enumerated integer opcode set. Genuine structured control
  flow is M2 — if enumeration shows control-flow opcodes are needed, record it, don't silently
  pull forward.
- Assert-runner: iterate JSON commands; on module load/instantiate; on assert_return/
  assert_trap invoke + classify; tally PASS/FAIL/UNSUPPORTED; exit nonzero on any FAIL.

## Note on harness realization
The contract's "CTest gate / MSVC+g++ / core-ctest.yml / PR #2 / loadout core" describe a C++
build that does not exist in this repo (verified: repo is Python-only — runner
`scripts/run_skeleton.py`, gates `tools/*.py`, CI `.github/workflows/m0.yml`). M1 realizes the
contract's SUBSTANCE in Python to match the codebase: enumerate-first scope, exact integer
semantics, PASS/FAIL/UNSUPPORTED with trap-matching, comparator positive-control, and a CI
gate nonzero on any FAIL. "CTest gate" ⇒ a new CI step + self-contained assertion, wired like
M0's gates.
