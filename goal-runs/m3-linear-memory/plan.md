# M3 — Linear Memory (execution plan)

Baseline: `origin/main` @ `f6a2574` (Merge PR #4; clean). Work branch (already created, owner-gated
for commit/push): `m3-linear-memory`. Risk: **medium** (extends the shared `interp/decoder.py` +
`interp/machine.py`; reversible, local, no external effects). Audit: **independent-subagents**
(medium ⇒ cannot claim completed via self-check). Size: **standard**.

All changes are additive files plus backward-compatible extensions; M0/M1/M2 behavior is preserved
and re-verified. `vendor/` and `build/` stay gitignored/regenerable.

## Design decisions (locked from Step-0 data)

1. **Targets** = `{store.wast, memory_size.wast}` (frozen in NEW `manifest_m3.json`). Data-selected
   (probe over all 97 `test/core` files) as the only integer-value-clean, memory-semantic-bearing,
   no-out-of-scope-section candidates. `manifest_m0.json` / `manifest_m2.json` untouched.
2. **Sections in scope = `{Type,Function,Export,Code,Memory}`.** The **Data** section is NOT in
   scope — neither target declares a data segment — so it stays `Unsupported` (fail-closed), and no
   data-segment-init code is written. This keeps M3 to exactly what the data exercises.
3. **New opcodes = `{i32.store (0x36), memory.size (0x3F), memory.grow (0x40)}` only.** No load
   opcodes, no `i64.store`, no narrow stores — none appear in an integer-pure target at this pin.
   The decoder keeps 0x28–0x35 (loads) and 0x37–0x3E (other stores) `Unsupported`.
4. **Decoder stays flat; add two immediate kinds.** memarg = `align:u32 + offset:u32` (for
   `i32.store`); reserved memidx = one byte that must be `0x00` (for `memory.size`/`memory.grow`).
   The Memory section decoder reads `vec(limits)` with `flags ∈ {0x00,0x01}` (else `Unsupported`).
   `tests/decoder_selftest.py`'s `[ins.op for ins in body]` stays token-for-token equal to
   `wasm-objdump -d` (the new opcodes are flat tokens like every other), so its comparison logic is
   unchanged.
5. **Interpreter gains a per-instance memory, threaded through execution.** `instantiate()` (already
   the documented seam) allocates a `Memory` (page `bytearray` + declared max) from the Memory
   section and attaches it to the module/instance; `invoke()` threads it into the recursive
   evaluator. `i32.store` / `memory.size` / `memory.grow` are the only ops that touch it; integer
   (M1) and control-flow (M2) opcode handling is reused byte-for-byte. Memory persists across
   invokes on the same instance (required by `memory_size.wast`).
6. **Shared tools already parameterized (from M2), not re-touched.** `scripts/convert.py`,
   `tools/assert_operand_purity.py`, `tools/body_purity_check.py`, `tests/decoder_selftest.py` all
   accept `--manifest` (added in M2 with `manifest_m0.json` defaults). M3 invokes them with
   `--manifest manifest_m3.json`; M0/M1/M2 CI invocations are unchanged. `scripts/run_m1.py` /
   `run_m2.py`, `tools/enumerate_m1_scope.py` / `enumerate_m2_scope.py`, and the pinned scope files
   are **not** touched; `run_m3.py` imports `run_m1.run_file`/`FileResult` (import, not modify).

## Milestones (each with a binary validation check)

### M3.0 — Freeze targets + Step-0 scope evidence (data gate, FAIL-CLOSED)
- Add `manifest_m3.json` (targets + reasoned exclusions; `disable_features` identical to M0/M2).
- Add `tools/enumerate_m3_scope.py`; convert the M3 targets; generate
  `goal-runs/m3-linear-memory/scope.txt` (sections, opcodes+counts, memory limits, memarg
  align/offset immediates, command inventory, per-file provenance, scope findings). Like the M2
  enumerator (not M1's report-only), it **asserts the subset and exits nonzero** on any violation.
- **ASYMMETRIC fail-closed predicate (do NOT copy M2's memory ban).** M2's enumerator flags "any
  `.load`/`.store`/`memory.` opcode" as a violation; copied verbatim it would false-reject M3's own
  `i32.store`/`memory.size`/`memory.grow`, and deleting it would silently re-admit loads/`i64.store`/
  narrow stores. `enumerate_m3_scope.py` instead uses an allow-set + explicit residual bans:
  `ALLOWED_SECTIONS = {Type,Function,Export,Code,Memory}`;
  `FROZEN_M3_OPS = {decoder.OPCODES mnemonics} ∪ {i32.store, memory.size, memory.grow}` (listed
  explicitly so the gate runs BEFORE the decoder is extended — order-independent, like M2);
  then residual bans re-expressed against the allow-set: any `.load` ⇒ violation; any `.store` ∉
  `{i32.store}` ⇒ violation; any `memory.*` ∉ `{memory.size, memory.grow}` ⇒ violation; any
  `f32.`/`f64.` ⇒ violation; any `call`/`call_indirect` ⇒ violation; any `global.*`/`table.*`/
  `select` ⇒ violation; Data section (id 11) present ⇒ violation; and a POSITIVE check that at least
  one memory opcode is present.
- **Verify (binary):** `enumerate_m3_scope.py` exits 0 over `{store, memory_size}` (sections ⊆
  allowed; opcodes ⊆ frozen; no float/call/global/table/load/wide-store/Data; ≥1 memory op present).
  A self-contained **regression check** in the same run (or a tiny driver) injects an out-of-scope
  opcode (e.g. `i64.store`, `i32.load`) into the observed set and confirms the predicate returns a
  violation / nonzero — proving the ban is LIVE, not assumed (the gate is the predicate, not the
  `scope.txt` a human reads). Then
  `git diff --exit-code goal-runs/m3-linear-memory/scope.txt` (reproducible).

### M3.1 — Decoder extension
- Extend `interp/decoder.py`: decode the **Memory** section (id 5) as `vec(limits)`,
  `flags 0x00 → (min, None)`, `0x01 → (min, max)`, else `Unsupported`; add
  `OPCODES` entries `i32.store (0x36, memarg)`, `memory.size (0x3F, memidx)`,
  `memory.grow (0x40, memidx)`; add immediate kinds `IMM_MEMARG` (align:u32 + offset:u32) and
  `IMM_MEMIDX` (one reserved byte, must be `0x00` else `Unsupported`). Data section (11), load
  opcodes (0x28–0x35), and other store opcodes (0x37–0x3E) stay `Unsupported`.
- **Block-type collision guard:** `0x40` is the empty block-type byte AND the `memory.grow` opcode
  byte. They never collide — block-type is only read as the immediate of `block/loop/if`, while
  `0x40` as an *opcode* is read by the top-level instruction loop. Confirm the decode paths are
  disjoint (a self-test over `memory_size.wast`, which has `memory.grow`, and over M2 modules, which
  have empty block-types, both green).
- **Verify (binary):** `tests/decoder_selftest.py` (no arg) still passes over the 22 M1 modules and
  `--manifest manifest_m2.json` over the 2 M2 modules (M0/M1/M2 decode unchanged, byte-for-byte);
  `--manifest manifest_m3.json` passes over the 5 M3 modules — func count, exports, memory limits,
  and every opcode stream (incl. `i32.store` memarg, `memory.size`/`memory.grow` memidx) match
  `wasm-objdump`. This self-test IS the evidence the new opcode bytes + immediate boundaries are
  correct (proven against the disassembler, not memory).

### M3.2 — Interpreter memory model
- Add a `Memory` (page `bytearray`, `min`/`max` pages) to `interp` and allocate it in
  `machine.instantiate()` from the decoded Memory section (attach to the instance; None when no
  Memory section — M0/M1/M2 modules). Thread the memory into the recursive evaluator.
- Wire `i32.store` (LE 4-byte write at `base+offset`, bounds check → `Trap("out of bounds memory
  access")`), `memory.size` (page count), `memory.grow` (zero-extend within max/engine cap; return
  prev pages or −1). Alignment immediate accepted, never used for bounds. Integer (M1) + control
  flow (M2) reused unchanged.
- **Named invariant — grow/limit:** `memory.grow(delta)` fails (returns −1, no growth) iff
  `cur+delta > max` (declared) or `> 65536` (engine cap); else returns `cur` and zero-extends.
  `memory_size.wast`'s `(memory 0 2)` proves both the success and the past-max failure against the
  oracle.
- **Verify (binary):** `tests/test_semantics.py` (M1 units) + `tests/test_control_flow.py` (M2
  units) still green; new `tests/test_memory.py` green, covering as *distinct* cases: (a) `i32.store`
  writes exact little-endian bytes; (b) store at a non-zero address; (c) `memory.size` initial;
  (d) `memory.grow` success returns prev + increases size; (e) grow past declared max → −1, size
  unchanged; (f) out-of-bounds store → `Trap("out of bounds memory access")`; (g) a "misaligned"
  memarg does NOT trap on alignment; (h) **per-instance fresh memory** — instantiate two modules
  with different limits, grow the first, and confirm the second starts at its OWN declared min (no
  leaked/shared bytearray). This is a FIRST-RUN path: no prior milestone ran `run_file` over a file
  with >1 `type==module` command, so the "grow on instance N must not bleed into instance N+1" path
  is new code and is unit-tested here as well as oracle-checked in M3.3; (i) M1 integer trap + M2
  branch regression spot-checks. Authoritative oracle check is M3.3 (`run_m3.py`).

### M3.3 — M3 assert-runner + gate
- Add `scripts/run_m3.py` (imports `run_m1.run_file`/`FileResult`), manifest_m3-driven discovery,
  `build/report/m3_summary.json`, nonzero on FAIL, no-drop assertion, UNSUPPORTED reason re-tagging
  (validation → "deferred to M4").
- **Verify (binary):** `run_m3.py` exits 0 with `modules=5, PASS=45, FAIL=0, UNSUPPORTED=60`,
  `5+45+0+60==110`, every UNSUPPORTED reason is a validation command. This is the oracle check for
  grow's observable EFFECT (the exported `grow` DROPS its result, so the `assert_return`s carry an
  empty expected — the oracle sees a refused growth only INDIRECTLY, via the next `memory.size`):
  `memory_size.wast`'s four limit forms — `(memory 0)` (grows to 5 pages, no max), `(memory 1)`,
  `(memory 0 2)` (`grow 3` then size→0, `grow 4` then size→1: growth refused, size unchanged), and
  `(memory 3 8)` (`grow 2` at size 7 refused then size→7, `grow 1` then size→8) — must all PASS
  against the frozen `expected`, and `modules=5` confirms all 5 `type==module` commands (1 store +
  4 memory_size) instantiated with fresh per-instance memory. The −1 RETURN value itself is verified
  by `tests/test_memory.py`, not by the oracle.

### M3.4 — Positive control (anti-false-pass)
- Add `tests/positive_control_m3.py`: pristine real M3 module+assertion ⇒ FAIL==0, PASS≥1; corrupt
  one `expected` (a real `memory.size` result) ⇒ FAIL≥1.
- **Verify (binary):** exits 0 (comparator fires on the injected wrong answer, passes the clean one).

### M3.5 — Purity over M3 targets (shared tools already `--manifest`-aware)
- No tool code change expected (M2 already added `--manifest`). If a gap surfaces, add a
  backward-compatible flag only.
- **Verify (binary):** `assert_operand_purity.py --manifest manifest_m3.json` and
  `body_purity_check.py --manifest manifest_m3.json` both exit 0 (store/memory_size are operand- and
  body-integer-pure); M0/M1/M2 no-arg / `--manifest manifest_m2.json` invocations unchanged.

### M3.6 — CI workflow + README
- Add `.github/workflows/m3.yml`: fetch → convert (m3 manifest, own report path) → M3 purity gates →
  re-derive `scope.txt` + `git diff --exit-code` → decoder self-test (m3) → memory units → M3
  positive control → `run_m3.py` → self-contained assertions (FAIL==0, no-drop, total==conversion
  total, UNSUPPORTED are validation-only). Append an M3 section to `README.md` (no overstated
  conformance).
- **Verify (binary):** all `.github/workflows/*.yml` parse as YAML; local dry-run of every m3.yml
  step passes; M0/M1/M2 workflows unmodified (`git diff` shows only additive changes).

### M3.7 — Non-regression sweep + closeout
- Re-run the full M0 + M1 + M2 gate set and confirm no regression.
- **Verify (binary):** `run_skeleton.py` supported==0; `assert_operand_purity.py` /
  `body_purity_check.py` (no arg) exit 0; `enumerate_m1_scope.py` reproduces `m1-scope.txt` and
  `enumerate_m2_scope.py` reproduces `m2 scope.txt` (`git diff --exit-code`); `decoder_selftest.py`
  (no arg) + `--manifest manifest_m2.json` exit 0; `test_semantics.py` + `test_control_flow.py`
  exit 0; `positive_control.py` + `positive_control_m2.py` exit 0; `run_m1.py` ⇒ `877/0/136`;
  `run_m2.py` ⇒ `51/0/4`.

## Validation matrix (evidence → acceptance)

| Acceptance criterion | Command | Expected |
|---|---|---|
| M3 scope data-derived, reproducible, fail-closed | `tools/enumerate_m3_scope.py` then `git diff --exit-code scope.txt` | exit 0, no diff |
| Decoder correct vs authoritative disassembler | `tests/decoder_selftest.py --manifest manifest_m3.json` | exit 0 |
| Memory semantics | `tests/test_memory.py` | exit 0 |
| Oracle diff, zero FAIL, no-drop | `scripts/run_m3.py` | `PASS=45 FAIL=0 UNSUPPORTED=60`, exit 0 |
| Comparator actually fires | `tests/positive_control_m3.py` | exit 0 |
| M3 targets integer-pure | `assert_operand_purity.py` / `body_purity_check.py --manifest manifest_m3.json` | exit 0 |
| M2 not regressed | `scripts/run_m2.py` | `PASS=51 FAIL=0 UNSUPPORTED=4`, exit 0 |
| M1 not regressed | `scripts/run_m1.py` | `PASS=877 FAIL=0 UNSUPPORTED=136`, exit 0 |
| M0 not regressed | `run_skeleton.py` + M0 purity gates | supported=0; gates exit 0 |

## Assumptions & how each is verified
- *The 2 targets need only the enumerated sections/opcodes/limits.* → M3.0 fail-closed enumeration +
  M3.1 decoder self-test; anything else surfaces as `Unsupported`/mismatch, not silent mis-exec.
- *M0/M1/M2 modules are unaffected by decoder/machine changes.* → M3.7 re-runs M1+M2 decoder
  self-tests, semantics/control-flow units, and `run_m1.py`/`run_m2.py` with the pinned counts.
- *Predicted split 45/0/60 is correct.* → run `run_m3.py`; the split is verified, not asserted.
  (`store`: 1 mod + 9 PASS + 58 UNSUP; `memory_size`: 4 mod + 36 PASS + 2 UNSUP.)
- *No `assert_trap` in the M3 targets; OOB trap unexercised by oracle.* → Step-0 command inventory;
  the OOB path is unit-tested and its trap string is read from the oracle JSON of excluded files.

## Rollback
Every change is a new file or an additive/backward-compatible edit on a feature branch. Rollback =
discard the branch (nothing merged, nothing pushed until owner-approved). `manifest_m0.json`,
`manifest_m2.json`, `m1-scope.txt`, `m2 scope.txt`, `run_m1.py`, `run_m2.py`, the M0/M1/M2
enumerators, and the M0/M1/M2 workflows are not modified, so M0/M1/M2 remain intact regardless of
M3's state.

## Owner-only boundaries (pause for explicit OK)
- The `m3-linear-memory` branch's first commit, `git push`, and opening the PR to `main`.
- No force-push, no merge (owner merges), no history rewrite.
