# M3 — Linear Memory (execution log, append-only)

## Phase 0/1 — audit + baselines
- Verified real git state: `origin/main` = f6a2574 (Merge PR #4, M2 merged); branched
  `m3-linear-memory` from `origin/main` (local `main` was 3 behind — not used).
- Baselines re-run (WSL not needed; pure-Python runtime): `run_m1.py` → 877/0/136 ✓;
  `run_m2.py` → 51/0/4 ✓.
- Mapped decoder (fail-closed sections {1,3,7,10}; unknown → Unsupported), machine
  (`instantiate()` seam for memory; two integer trap texts), runner (`run_file` milestone-agnostic,
  accounting invariant), convert (`disable_features` → `--disable-*` guardrail), and the M2
  fail-closed enumerator template.

## Phase 2 — data-gated target curation (Step-0 probe, read-only)
- Probe converted every memory candidate under `--disable-simd --disable-bulk-memory
  --disable-reference-types`; measured sections/opcodes/float-purity/calls/traps via pinned
  wasm-objdump; confirming sweep over all 97 test/core files.
- Result: exactly 2 integer-pure MVP-memory files — `store.wast` (i32.store) and
  `memory_size.wast` (memory.size/grow). `memory`/`data`/`align` reject at conversion (bulk-memory);
  `address`/`endianness`/`memory_redundancy`/`float_memory`/`memory_trap` are float-contaminated;
  `load`/`memory_grow` need globals/tables/calls; `inline-module` is syntax-only (no mem opcodes).
- Canonical OOB trap string captured from oracle JSON (memory_grow/memory_trap):
  `out of bounds memory access`.

## Phase 3 — contract + plan + manifest
- Wrote contract.md, plan.md, manifest_m3.json, state.json (this run folder).
- Scope locked from data: sections {Type,Function,Export,Code,Memory}; new opcodes
  {i32.store, memory.size, memory.grow}; Data section + loads DEFERRED (fail-closed Unsupported).
- Predicted result (to verify): modules=5, PASS=45, FAIL=0, UNSUPPORTED=60 (110 commands).
- Owner chose "build the clean slice" (minimal data-bound memory implementation).
- Converted M3 targets via existing convert.py --manifest manifest_m3.json: store 68 / memory_size
  42 = 110 commands, matching the prediction. Standing purity gates (assert_operand_purity +
  body_purity_check) on manifest_m3.json both exit 0 (targets integer-clean).

## Phase 3 gate — independent plan audit (plan-auditor subagent)
- Decision: needs-replan (1 blocking + 3 recommended) -> all folded -> conditionally cleared.
- Blocking: enumerate_m3_scope.py must use an ASYMMETRIC allow-set + residual bans, NOT M2's
  categorical .load/.store/memory. ban (which would false-reject i32.store/memory.size/memory.grow).
  Folded into contract.md GATED-ON-MACHINE + plan.md M3.0, plus a live monkeypatch regression.
- Recommended (folded): per-instance fresh-memory first-run test; OOB provenance precision
  (constant read from upstream memory_trap.wast:23 / memory_grow.wast:86, not an M3 artifact);
  memory_size 4 limit forms named as the grow oracle check.

## Phase 4 — fail-closed enumerator (M3.0)
- Wrote tools/enumerate_m3_scope.py with ASYMMETRIC predicate (allow-set + residual bans) + inline
  _assert_gate_live() self-check. Ran it: VERDICT IN SCOPE, self-check PASS, exit 0.
- scope.txt: 5 sections {Type,Function,Memory,Export,Code}; 15 opcodes; memory limits
  {initial=0, initial=0 max=2, initial=1, initial=3 max=8}; memarg i32.store align=2 offset=0;
  memidx memory.size/grow=0. Command inventory 110 (module 5 / assert_return 45 / assert_invalid 53
  / assert_malformed 7).

## Phase 5 — decoder + machine (M3.1, M3.2, M3.3)
- decoder.py: added SEC_MEMORY(5) + SECTION_NAMES; IMM_MEMARG/IMM_MEMIDX; OPCODES i32.store(0x36),
  memory.size(0x3F), memory.grow(0x40); Instr.align/offset; Module.mems/mem; _decode_limits
  (flags 0x00/0x01 only, else Unsupported) + _decode_memory_section (>1 memory Unsupported);
  memarg/memidx decode (nonzero memidx Unsupported). Data section + loads stay Unsupported.
- decoder self-test vs wasm-objdump: M1 22 modules + M2 2 modules BYTE-IDENTICAL (0x40 collision
  safe), M3 5 modules match func count / exports / opcode streams. All exit 0.
- machine.py: Memory class (page bytearray + max_pages, grow); instantiate() allocates+attaches
  per-instance memory (persists across invokes); threaded mem through _exec_seq/_exec_block/
  _exec_instr; i32.store (LE 4-byte, bounds→Trap OOB), memory.size, memory.grow. Integer+control
  flow unchanged.
- Regression: test_semantics.py 17 OK, test_control_flow.py 23 OK (mem threading transparent).
- run_m3.py (mirrors run_m2.py): store 1/9/58 + memory_size 4/36/2 = modules 5 / PASS 45 / FAIL 0 /
  UNSUPPORTED 60. GATE PASS. EXACTLY the predicted split; oracle diff zero FAIL.

## Phase 6 — tests, positive control, CI, non-regression (M3.4-M3.7)
- tests/test_memory.py: 29 tests OK — store LE (at 0 / offset / dynamic base+offset / misaligned
  no-trap), OOB trap (+ exact text constant), size (0/1/3), grow (success/zero/past-max -1/exact-max
  boundary/engine-cap), store persistence, PER-INSTANCE FRESH MEMORY (no leak + independent stores),
  decoder fail-closed (loads 0x28-0x35 absent, stores 0x37-0x3E absent, only 3 mem opcodes, Data
  deferred, limits flags MVP-only, nonzero memidx rejected), regression (M1 div-zero/overflow, M2
  block/br, mem+control-flow coexist).
- tests/positive_control_m3.py: injected wrong 'size' expected (0->1) correctly classified FAIL;
  clean run passes. Comparator fires.
- .github/workflows/m3.yml: mirrors m2.yml (own manifest/report/scope paths); all 4 workflows parse
  as valid YAML (UTF-8). scope.txt byte-deterministic on re-run.
- Non-regression sweep (AFTER shared decoder/machine edits): run_m1 877/0/136, run_m2 51/0/4,
  positive_control(_m2) OK, test_semantics/test_control_flow OK, run_skeleton supported=0, M0/M1
  purity gates OK, m1-scope.txt + m2 scope.txt UNCHANGED (git diff --exit-code), decoder self-test
  M1(22)+M2(2) byte-identical. NO regression.
- README.md: added "## M3 — Linear Memory (implemented)" section (data curation, asymmetric
  fail-closed gate, memory model, 45/0/60 result, reproduce commands, explicit non-conformance
  disclaimer + deferred list). Updated M1-section deferral pointer.

## Phase 7 gate — independent execution audit (execution-auditor subagent)
- Decision: PASS (9/9 acceptance criteria verified from raw evidence in fresh processes).
- Adversarially drove scope_violations: in-scope {i32.store} -> [] (asymmetry proven); all 16
  out-of-scope injections fire; _assert_gate_live runs first in main(). Decoder rejects every
  deferred construct. 0x40 opcode vs empty-block-type disjoint; M2 byte-identical. Memory semantics
  (LE store, OOB trap text, grow prev/-1/max/cap, align-ignored, per-instance-fresh) all confirmed.
- Non-regression: run_m1 877/0/136, run_m2 51/0/4, scope files no drift, PROTECTED files diff vs
  origin/main = EMPTY. No authored expected. Clean diff hygiene.
- Auditor note: owner boundary (first commit / push / PR) NOT authorized by the audit — stands.

## Closeout — STOPPED at owner boundary
- Implementation complete + independently audited PASS. Nothing committed or pushed.
- Awaiting owner approval to: commit on branch m3-linear-memory (signed, no AI trailer), push, open
  PR to main. Agent does not self-merge.

## Ship (owner-approved) + CI
- Owner approved "commit + push + open PR". Signed commit 70f7f56 (Good git signature, no AI
  trailer), pushed origin/m3-linear-memory, opened PR #5 to main, requested @codex review.
- GitHub Actions: m0 oracle-harness PASS (1m41s), m1 integer-core PASS, m2 control-flow PASS, m3
  linear-memory PASS. All 4 green.

## Codex review round 1 — 3 inline findings, all addressed on evidence (not blindly)
- P2 machine.py grow: min-only memory admits delta up to 65536 pages -> grow of ~4GiB would OOM the
  runner instead of returning -1. FIXED: catch MemoryError/OverflowError in Memory.grow -> return -1,
  memory unchanged (spec: grow never traps). Regression test
  test_grow_host_allocation_failure_returns_minus_one_unchanged (simulates host OOM via a bytearray
  whose extend raises).
- P2 enumerate_m3_scope.py: FROZEN_M3_OPS derived from mutable dec.OPCODES -> a future decoder
  extension could silently expand the frozen gate. FIXED: pinned an EXPLICIT 84-mnemonic snapshot
  (verified == current dec.OPCODES exactly), decoupled from the decoder. scope.txt output unchanged.
- P3 contract/plan/manifest: overstated that grow 3/grow 4 "return -1 matches the oracle". FIXED:
  the exported grow DROPS the result, so the oracle checks grow's EFFECT via the following
  memory.size assert; the -1 return is unit-tested only. Wording corrected in contract.md, plan.md,
  manifest_m3.json.
- Re-verified after fixes: test_memory 30/30, run_m3 45/0/60, positive_control_m3 OK, enumerate_m3
  IN SCOPE + self-check + scope.txt byte-unchanged, decoder self-test M3 5/5, run_m1 877/0/136,
  run_m2 51/0/4.
