# M3 — Linear Memory (handoff)

Status: implemented + independently audited on branch `m3-linear-memory` (cut from `origin/main`
@ f6a2574, the PR #4 / M2 merge). Commit/push/PR are OWNER-GATED — nothing is committed or pushed
until the owner approves.

## What M3 delivered (data-bound, minimal)

- Targets (frozen in `manifest_m3.json`): `store.wast`, `memory_size.wast` — the ONLY two
  integer-value-clean, MVP-memory files in all 97 `test/core` at pin `82cd4f9` (proven by a probe +
  a whole-directory sweep). `manifest_m0.json` / `manifest_m2.json` untouched.
- Scope: sections `{Type,Function,Export,Code,Memory}`; new opcodes `{i32.store (0x36),
  memory.size (0x3F), memory.grow (0x40)}`; memory limits flags `{0x00 min, 0x01 min+max}`;
  memarg (align+offset) and reserved-memidx immediates.
- Result: `scripts/run_m3.py` → `modules=5, PASS=45, FAIL=0, UNSUPPORTED=60` (110 commands;
  `5+45+0+60==110`). Zero authored expected values.
- Fail-closed Step-0 gate: `tools/enumerate_m3_scope.py` (asymmetric allow-set + residual bans +
  inline self-check) → `goal-runs/m3-linear-memory/scope.txt` (deterministic, CI git-diffed).
- Independent CI: `.github/workflows/m3.yml` (does not touch m0/m1/m2 workflows).

## Seams for M4+ (deferred — where to add each)

- **Loads (`i32/i64.load*`, 0x28–0x35), `i64.store`, narrow stores (`i32/i64.storeN`, 0x37–0x3E):**
  currently `Unsupported` in `interp.decoder` (not in `OPCODES`). No integer-pure `test/core` file
  at this pin exercises them (every load/round-trip/endianness file is float- or call/table-
  contaminated). Add the opcode bytes to `decoder.OPCODES` (with `IMM_MEMARG`) and the read paths to
  `interp.machine._exec_instr` when a floats or calls milestone brings an integer-observable target.
  Load extension (`load8/16/32_s/u`) uses `interp.values.to_signed`/`to_unsigned` (already present).
- **Data section (id 11) / active data-segment init:** `Unsupported` in `decoder.decode`. Neither M3
  target declares one. Add `_decode_data_section` + apply segments in `machine.instantiate()` (which
  already allocates the memory) when a target needs pre-initialized bytes.
- **`memory.copy`/`fill`/`init`, `data.drop`, passive data:** bulk-memory — rejected at conversion by
  the `--disable-bulk-memory` guardrail. Shared / `memory64` / multi-memory limits flags → the
  Memory decoder raises `Unsupported`.
- **M4 validation execution (`assert_invalid`/`assert_malformed`):** still `UNSUPPORTED` by design
  (counted, not skipped). `store.wast` alone carries 51 `assert_invalid` + 7 `assert_malformed` —
  a rich validation-execution target set already sitting in the converted data.
- **M5 floats:** `f32`/`f64` stack values are deliberately absent; the operand + body purity gates
  guarantee the current targets need none. The comparator (`interp.runner.decode_operand`) rejects
  non-integer operands, which is what keeps float-mixed memory files (address/endianness/…) out.
- **`call`/`call_indirect`, globals, tables, imports, start, elem, `select`, `unreachable`,
  multi-value blocks:** unchanged deferrals. `interp.machine._exec_instr` is the opcode seam;
  `decoder.decode` is the section seam.

## Reusable pattern

The fail-closed enumerator with an ASYMMETRIC predicate (allow-set + residual bans re-expressed
against it, NOT a categorical ban) + an inline `_assert_gate_live` self-check is the template for any
future milestone that ADMITS a subset of a family whose siblings must stay out (M3 admits `i32.store`
while banning every other load/store). Copying a prior milestone's categorical ban is a bug — the
plan-audit caught exactly that here.

## Reproduce (Linux/WSL after fetch_oracle.py)

```sh
python3 scripts/convert.py --manifest manifest_m3.json --report build/report/conversion_report_m3.json
WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump python3 tools/enumerate_m3_scope.py   # fail-closed; git diff scope.txt
python3 tests/decoder_selftest.py --manifest manifest_m3.json
python3 tests/test_memory.py
python3 tests/positive_control_m3.py
python3 scripts/run_m3.py            # PASS=45 FAIL=0 UNSUPPORTED=60
```
