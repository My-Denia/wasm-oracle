# M3 — Linear Memory (goal contract)

Follows M2 (COMPLETE, merged to `main` via PR #4): an integer core + structured control flow over
`{labels.wast, switch.wast}`, `PASS=51 / FAIL=0 / UNSUPPORTED=4` across 57 commands, scope derived
from real data (4 sections `{Type,Function,Export,Code}`; M1 integer opcodes + `{nop,block,loop,if,
else,br,br_if,br_table,drop,local.set}`; block-types `{empty,i32,i64}`; no memory/call/global/
table/float). M3 extends the interpreter to **linear memory** — the minimal, data-selected MVP
subset — and nothing else. No loads, no data segments, no validation execution, no floats, no
calls/globals/tables (those stay deferred to M4/M5 and beyond).

The repo's standing discipline holds verbatim: **verification-before-implementation**,
**data-gated scope** (enumerate from real `.wasm`, never guess the instruction set),
**no silent skip** (`modules + PASS + FAIL + UNSUPPORTED == total`), **external oracle only** (the
`expected` values in the converted JSON were authored by the WebAssembly spec reference
interpreter; we author no expected values), and **Python-only** unless repo facts force otherwise.

## Step 0 finding — the target set is a curation problem, and the data resolves it

Linear memory is where `test/core` pervasively mixes concerns. A read-only probe converted every
memory-bearing candidate under the frozen M0/M2 guardrail flags
(`--disable-simd --disable-bulk-memory --disable-reference-types`) and, over each instantiated
module, measured sections/opcodes (pinned `wasm-objdump -h/-d/-x`), float-body purity, float assert
operands, calls, globals/tables/refs, memory limits, and trap texts. A confirming sweep ran the
same measurement over **all 97** `test/core` files. The result is unambiguous:

- `memory.wast`, `data.wast`, `align.wast` — **reject at conversion** under `--disable-bulk-memory`
  (they use bulk-memory / passive-data-segment syntax at pin `82cd4f9`). The guardrail excludes
  them automatically, exactly as designed.
- `address`, `endianness`, `memory_redundancy`, `float_memory`, `memory_trap` — **have-float**:
  their instantiated modules carry `f32`/`f64` load/store in the same bodies (and float assert
  operands). Outside integer-value scope; would fail the body-purity discipline.
- `load`, `memory_grow` — pull in `Elem`/`Global`/`Table`(/`Import`) sections plus `call` /
  `call_indirect` / `select`. Outside M3's memory-only scope.
- `inline-module` — integer-pure and sections-in-scope, but declares `(memory 0)` with **zero**
  memory opcodes and **zero** value assertions (1 module command only). It tests inline-module
  *syntax*, not linear-memory *semantics*; its `(memory 0)` shape is already covered by
  `memory_size.wast`. Excluded to keep M3 targets semantically memory-bearing.

Exactly **two** files are integer-value-clean, stay inside sections
`{Type,Function,Export,Code,Memory}`, pull in no float/call/global/table/data, and exercise real
linear-memory semantics:

| target | module(s) | assert_return | assert_invalid | assert_malformed | memory opcodes present |
|---|---|---|---|---|---|
| `store.wast` | 1 × `(memory 1)` | 9 | 51 | 7 | `i32.store` |
| `memory_size.wast` | 4 × `(memory 0)` / `(memory 1)` / `(memory 0 2)` / … | 36 | 2 | 0 | `memory.size`, `memory.grow` |

Their union is the minimal data-bound M3 surface: the **Memory** section (limits: min-only
`(memory N)` and min+max `(memory N M)`), the opcode `i32.store`, and `memory.size` / `memory.grow`
(with grow-past-max failure). `store.wast` stores at address 0 inside every control construct
(block/loop/if/else/br/br_if/br_table/return — all reused unchanged from M2); `memory_size.wast`
threads `memory.size`/`memory.grow` across invokes, including a `(memory 0 2)` module whose
`grow 3` and `grow 4` must **fail** (return −1) against the declared max of 2.

Neither target contains a **Data** section, an `assert_trap`, or a **load** opcode. So M3's
oracle-verified surface is precisely: memory declaration + limits, `i32.store` executing in
context, and `memory.size`/`memory.grow` page-and-limit arithmetic. Loads, `i64`/narrow stores,
active data-segment init, and out-of-bounds *traps* are **not** exercised by any integer-pure
`test/core` file at this pin — they are deferred (see DEFERRED), not silently half-built.

### M3 targets (frozen in a NEW `manifest_m3.json`, M0/M1/M2 contracts untouched)
`store.wast`, `memory_size.wast` — 110 commands total (5 module + 45 `assert_return` + 53
`assert_invalid` + 7 `assert_malformed`).

### Reasoned exclusions (data-backed; recorded in `manifest_m3.json`)
- `memory, data, align`: reject at conversion under `--disable-bulk-memory` (bulk-memory / passive
  data at pin `82cd4f9`). Genuinely out of MVP-memory scope; revisit at a bulk-memory milestone.
- `address, endianness, memory_redundancy, float_memory, memory_trap`: instantiated modules mix
  `f32`/`f64` load/store (and float assert operands) — fail body/operand integer purity. Revisit
  with floats (M5). `memory_trap` is where OOB traps live, but it is float-contaminated.
- `load, memory_grow`: need `Global`/`Table`/`Elem`(/`Import`) sections + `call`/`call_indirect` +
  `select`. Out of M3 memory-only scope; revisit with the calls/globals/tables milestones.
- `inline-module`: integer-pure but no memory opcodes and no asserts (syntax-only; `(memory 0)`
  already covered by `memory_size.wast`).

## Deliverables

DELIVERED by M3:
- A NEW `manifest_m3.json` freezing the 2 data-selected targets + reasoned exclusions. `manifest_m0`
  and `manifest_m2` are not touched. `conversion.disable_features` is IDENTICAL to M0/M2.
- Step 0 evidence: a new `tools/enumerate_m3_scope.py` writing committed
  `goal-runs/m3-linear-memory/scope.txt` (sections, opcodes+counts, memory limits, memarg
  align/offset immediates, command inventory, per-file provenance, scope findings) — reproducible
  and CI-diffed, and **fail-closed** (exits nonzero on any out-of-scope section/opcode/float),
  matching the M2 enumerator's discipline (stronger than M1's report-only enumerator).
- Decoder extension (`interp/decoder.py`): decode the **Memory** section (limits) and the opcodes
  `i32.store` (0x36), `memory.size` (0x3F), `memory.grow` (0x40), plus the memarg immediate
  (align + offset) and the reserved-memidx byte. Unknown section/valtype/opcode/limit-flag stays
  `Unsupported` (no silent accept). The **Data** section (11), all **load** opcodes (0x28–0x35),
  and the other store opcodes (`i64.store`, `i32/i64.storeN`, 0x37–0x3E) remain `Unsupported`
  (fail-closed — deferred). M0/M1/M2 modules must decode **identically** (they contain no Memory
  section and none of the new opcodes), proven by the unchanged M1/M2 decoder self-tests.
- Interpreter extension (`interp/machine.py`): a linear-memory model — a page-granular
  (`64 KiB`) `bytearray` with min/max limits — allocated at `instantiate()` and **persisted on the
  instance** across invokes; little-endian 4-byte `i32.store` with an effective-address bounds
  check that traps `out of bounds memory access` on overflow; `memory.size` returning the current
  page count; `memory.grow` growing (zero-filled) within the declared max (else engine cap 65536
  pages) and returning the previous page count or −1. Integer (M1) and control-flow (M2) semantics
  must not regress.
- A NEW `scripts/run_m3.py` (M3 assert-runner) → `build/report/m3_summary.json`, nonzero on any
  FAIL; PASS/FAIL/UNSUPPORTED classification, no-drop accounting; imports `run_m1.run_file`/
  `FileResult` (import, not modify), exactly as `run_m2.py` does. M1/M2 runners stay independent.
- New tests: `tests/test_memory.py` (i32.store little-endian byte layout; store at a non-zero
  address; memory.size; memory.grow success; grow-past-declared-max → −1; out-of-bounds store →
  trap `out of bounds memory access`; alignment immediate accepted but never relaxes bounds; a
  regression that M1 integer traps and M2 branch/label fixes are unchanged) and
  `tests/positive_control_m3.py` (corrupt one real M3 `expected` → FAIL must fire).
- New CI: `.github/workflows/m3.yml` — independent workflow; does not touch `m0.yml`/`m1.yml`/
  `m2.yml`.

GATED ON-MACHINE (from real data, Step 0, before any executor code) — **fail-closed**:
- `tools/enumerate_m3_scope.py` must **exit nonzero** on any out-of-scope construct. Its predicate
  is ASYMMETRIC and must be stated as an allow-set plus explicit bans (NOT copied from
  `enumerate_m2_scope.py`, whose categorical "any `.load`/`.store`/`memory.` ⇒ violation" ban would
  false-reject M3's own `i32.store`/`memory.size`/`memory.grow`; and simply deleting that ban would
  silently re-admit loads/`i64.store`/narrow stores — the exact scope-by-inspection hole AGENTS.md
  §2 warns of). The frozen M3 predicate:
  - section ∉ `{Type,Function,Export,Code,Memory}` ⇒ violation (bans Data=11, Global, Table, Import,
    Start, Elem);
  - opcode ∉ the frozen M3 set (M1/M2 set ∪ `{i32.store, memory.size, memory.grow}`) ⇒ violation;
  - **and, as explicit positive bans re-expressed against the allow-set** (so a new memory opcode
    can never slip in as "just another op"): any `.load` opcode ⇒ violation; any `.store` opcode ∉
    `{i32.store}` ⇒ violation (so `i64.store` / `i32.store8/16` / `i64.store8/16/32` fail-close);
    any `memory.*` opcode ∉ `{memory.size, memory.grow}` ⇒ violation (so `memory.copy/fill/init`
    fail-close);
  - any `f32.`/`f64.` opcode ⇒ violation; any `call`/`call_indirect` ⇒ violation; any
    `global.*`/`table.*`/`select` ⇒ violation;
  - POSITIVE check: at least one memory opcode present (else the targets don't exercise memory).
  The gate is the PREDICATE (exit code), not a note a human reads off `scope.txt`. A regression test
  (M3.0 verify) monkeypatches an out-of-scope opcode into the enumerated set and confirms the gate
  exits nonzero — so the ban is proven live, not assumed. Then `git diff --exit-code scope.txt`
  (reproducible).

## Named decode / memory invariants (audit-required checks, allocated before code)

These are named here so a specific check is allocated to each — not left as implementation detail:

- **Memory-limits decode.** The Memory section is `vec(limits)`; `limits` = `flags:byte` then
  `min:u32` and (if flags bit 0) `max:u32`. `0x00 → (min, no-max)`, `0x01 → (min, max)`. `0x03`
  (shared) → `Unsupported`; `0x04`/`0x05` (memory64) → `Unsupported`; any other flag → `Unsupported`.
  MVP allows at most one memory. Evidence: the M3 decoder self-test over the 5 target modules
  matches `wasm-objdump -x` for memory count and each `(min[, max])` pair, and `(memory 0 2)`
  decodes `max=2`.
- **memarg immediate = align:u32 + offset:u32; alignment is a hint, not a bound.** `i32.store`
  carries `(align, offset)`. The effective address = `base` (an i32 operand, interpreted
  **unsigned**) `+ offset`, computed in full precision. The **align** value is decoded and
  **ignored** for bounds — a "misaligned" access must NOT trap on alignment (WASM linear memory is
  unaligned-accessible; align is only an optimization hint). Evidence: a unit test stores at an
  address whose natural alignment differs from the memarg align and asserts no trap; `store.wast`
  memargs match `wasm-objdump -d`.
- **`i32.store` little-endian + bounds trap.** Pops value (i32), pops base (i32); writes the low 32
  bits as 4 little-endian bytes at `[ea, ea+4)`; if `ea + 4 > size_bytes` it traps
  `out of bounds memory access`. **The trap string is a stable, spec-canonical constant read once
  from the upstream oracle**, not authored by us: the reference interpreter's `.wast` sources carry
  it verbatim as `assert_trap … "out of bounds memory access"` (`memory_trap.wast:23`,
  `memory_grow.wast:86`; present across 8 pinned `test/core` files). Those files are EXCLUDED from
  M3 targets, so the M3 conversion pipeline never produces their JSON — the string is copied from
  the upstream oracle at implementation time; it is NOT a runtime artifact the M3 run generates. No
  in-scope target triggers this path, so it is proven by a `test_memory.py` unit test, not by an
  oracle assertion — recorded honestly. Evidence: unit test asserts the exact LE byte layout after a
  store and asserts the OOB trap; `store.wast` PASS=9 (every store in-bounds).
- **`memory.grow` / `memory.size` page-and-limit arithmetic.** `memory.size` returns the current
  page count (i32). `memory.grow(delta)`: if `cur + delta` exceeds the declared max (or the engine
  cap of 65536 pages when no max) it returns −1 (`0xFFFFFFFF`) and does not grow; otherwise it
  zero-extends the memory by `delta` pages and returns the previous page count. Both take a reserved
  memidx byte that must be `0x00` (nonzero → `Unsupported`, multi-memory). Evidence:
  `memory_size.wast`'s `(memory 0 2)` module — `grow 3` (0+3 > 2 → −1, size stays 0) and `grow 4`
  (1+4 > 2 → −1) — matches the oracle; the `(memory 0)` module grows to 5 pages across invokes.
- **Memory persists on the instance across invokes.** The memory is allocated once at
  `instantiate()` and mutated in place, so a `grow` in one invoke is visible to the next `size`.
  Evidence: `memory_size.wast` interleaves `grow`/`size` across separate invokes and matches the
  oracle sequence.

DEFERRED (unchanged from M2, plus M3's data-forced deferrals): M4 validation execution, M5 floats;
`call`/`call_indirect`, `global`, `table`, `import`, `start`, `elem`, `select`, `unreachable`,
multi-value block types. NEW deferrals surfaced by the M3 probe: all **load** opcodes, `i64.store`,
`i32/i64.storeN`, the **Data** section / active data-segment init, `memory.copy`/`fill`/`init`,
`data.drop`, passive data, shared/`memory64`/multi-memory — none are exercised by an integer-pure
`test/core` file at pin `82cd4f9`, so the decoder keeps them `Unsupported` (fail-closed).

## Oracle & acceptance

Oracle is embedded and frozen: each `assert_return.expected` / `assert_trap.text` was authored by
the reference interpreter, so bitwise-diffing our invoke result against `expected` (and matching
trap kind against `text`) **is** diffing against the reference-interpreter oracle. No expected value
is authored or modified here.

Per-command classification (tallied, none dropped):
- PASS — invoke result equals `expected` (bitwise i32/i64), or a trap occurred exactly where
  `assert_trap` expected it with the matching kind.
- FAIL — any mismatch (wrong value, wrong arity, unexpected/absent/mis-kinded trap, missing export,
  or an in-scope module that fails to decode). Any FAIL fails the gate (nonzero exit).
- UNSUPPORTED — command/opcode outside M3 scope (`assert_invalid`/`assert_malformed` validation, or
  an out-of-scope opcode/section). Reported with a count, never skipped.

Expected M3 result over the 2 targets: `modules=5, PASS=45, FAIL=0, UNSUPPORTED=60`
(`5 + 45 + 0 + 60 == 110`). Breakdown — `store.wast`: 1 module + 9 assert_return (PASS) + 58
validation (UNSUPPORTED: 51 assert_invalid + 7 assert_malformed); `memory_size.wast`: 4 module +
36 assert_return (PASS) + 2 assert_invalid (UNSUPPORTED). Neither target contains `assert_trap`, so
M3 asserts memory **values** (store execution, size/grow arithmetic), not traps; the OOB trap path
is unit-tested. (This predicted split is verified by running `run_m3.py`, not asserted by us.)

Acceptance:
- M0 gates still green (unchanged).
- M1 gates still green; `scripts/run_m1.py` still `PASS=877, FAIL=0, UNSUPPORTED=136`.
- M2 gates still green; `scripts/run_m2.py` still `PASS=51, FAIL=0, UNSUPPORTED=4`.
- M3 scope evidence reproducible and deterministic (CI re-derives and `git diff --exit-code`),
  fail-closed (out-of-scope data → nonzero exit).
- M3 runner: zero FAIL over `{store, memory_size}`; every command bucketed; every UNSUPPORTED
  counted with a reason.
- Comparator positive control for M3: a deliberately wrong `expected` on a real M3 assertion must
  classify FAIL — a green run must be evidence the comparator fires.
- Decoder self-test still passes over the M1 modules (22) and M2 modules (2) unchanged, and,
  extended to the M3 modules (5), matches `wasm-objdump` for func count, exports, memory limits,
  and every opcode stream (validating the new opcode bytes and memarg/limit immediate boundaries
  against the authoritative disassembler).
- README gains an M3 section that does not overstate conformance.

## Non-goals / invariants preserved
- Do not modify `manifest_m0.json`, `manifest_m2.json`, the M0/M1/M2 CI assertions, `m1-scope.txt`,
  or `m2 scope.txt`.
- Do not weaken the two purity gates; M3 targets must pass the same integer-purity discipline
  (`assert_operand_purity.py` / `body_purity_check.py --manifest manifest_m3.json` exit 0).
- No `Co-Authored-By`/AI trailer in commits. No push/PR/force-push without explicit owner OK; no
  self-merge.
- No authored expected values; WABT stays toolchain-only, never a semantic oracle.
