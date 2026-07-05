# M2 handoff — Structured Control Flow

## State
M2 COMPLETE (pending independent execution-audit sign-off). All work on `main`'s worktree off
`main`@`ca2abb1`. All changes additive files + backward-compatible edits + a README update. NOT
committed/pushed yet (owner-only boundary — awaiting user OK to branch/commit/push/PR).

## What M2 delivers
Structured control flow on top of the M1 integer core (Python, stdlib): block/loop/if/else/br/
br_if/br_table/return/drop/nop + local.set. Runs 2 data-selected targets and diffs every value
assertion against the embedded frozen reference-interpreter oracle:
`PASS=51, FAIL=0, UNSUPPORTED=4` over 57 commands (2 modules). No memory/globals/calls/validation/
floats. Scope DERIVED and FAIL-CLOSED-gated from real data — see `goal-runs/m2-control-flow/scope.txt`.

## Target selection (the M2 curation finding)
The canonical control-flow files (block/loop/if/br/return/unreachable/call) are NOT integer-clean —
one big module mixes f32/f64 + call + memory/global/table sections. br_if/br_table/select/
call_indirect/func REJECT at conversion under the guardrail flags. Only `labels.wast` + `switch.wast`
are integer-clean, control-flow-only, no new sections. Frozen in `manifest_m2.json` with reasoned
exclusions. `local.set` was surfaced by the fail-closed enumerator (not the first probe).

## Files
- `interp/decoder.py` — +10 opcodes, block-type (signed-LEB) + br_table immediates; body stays flat.
- `interp/machine.py` — `_structure` (flat→nested) + recursive evaluator with value/label stacks.
- `manifest_m2.json`, `tools/enumerate_m2_scope.py` (fail-closed) → `goal-runs/m2-control-flow/scope.txt`.
- `scripts/run_m2.py` (imports run_m1.run_file), `tests/test_control_flow.py` (19 units),
  `tests/positive_control_m2.py`, `.github/workflows/m2.yml`.
- Backward-compatible `--manifest` on: `scripts/convert.py` (+`--report`),
  `tests/decoder_selftest.py`, `tools/assert_operand_purity.py`, `tools/body_purity_check.py`.

## Reproduce (Linux/WSL; WABT binaries are linux-x64 in vendor/wabt/bin)
```sh
export WASM2WAT=vendor/wabt/bin/wasm2wat WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump
python3 scripts/fetch_oracle.py                                                   # if vendor/ absent
python3 scripts/convert.py --manifest manifest_m2.json --report build/report/conversion_report_m2.json
python3 tools/enumerate_m2_scope.py            # fail-closed scope gate -> scope.txt
python3 tests/decoder_selftest.py --manifest manifest_m2.json
python3 tests/test_control_flow.py
python3 tests/positive_control_m2.py
python3 scripts/run_m2.py                      # PASS=51 FAIL=0 UNSUPPORTED=4
```
On Windows host prefix each with: `wsl.exe -e bash -lc 'cd /mnt/c/Files/wasm-oracle && … '`.
M1 non-regression: `python3 scripts/run_m1.py` still `PASS=877 FAIL=0 UNSUPPORTED=136`.

## Seams for M3+ (deferred)
- **Widening M2 control flow**: `select`, `unreachable`, and multi-value block types are NOT in the
  labels/switch data, so they are not implemented; the decoder raises `Unsupported` for them and
  for any non-{empty,i32,i64} block-type. Add when a target needs them.
- **`call` / `call_indirect`**: integer-clean files `fac.wast`/`forward.wast` (and stack/nop) are
  excluded because they need calls (and globals/memory/table). A call milestone would add the Call
  opcode + call-frame handling; `interp/machine._exec_instr` is the seam.
- **M3 memory / M4 validation / M5 floats**: new sections (Memory/Data/Global/Import/Table)
  currently raise `Unsupported` in `decoder.decode`; add them there. Floats need f32/f64 stack
  values (deliberately absent). Validation execution (assert_invalid/assert_malformed) is still
  UNSUPPORTED by design.
- The fail-closed `enumerate_m2_scope.py` pattern (assert subset, nonzero on violation) is the
  template for future Step-0 gates — stronger than the M1 report-only enumerator.

## Owner action needed
Branch `m2-control-flow`, commit (signed, no AI trailer), push, open PR to `main` — all pending
explicit user OK. CI m2.yml will run on push/PR; m0.yml/m1.yml unchanged.
