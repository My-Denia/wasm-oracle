# M1 handoff — Integer Core Execution

## State
M1 COMPLETE (pending independent execution-audit sign-off). Branch `m1-integer-core` off
`main`@`f5096f0`. All changes additive (new files) + a README doc update. Not committed/pushed
yet (owner-only boundary — awaiting user OK).

## What M1 delivers
An integer-only WASM executor (Python, stdlib) that runs the 4 frozen targets and diffs every
value assertion against the embedded frozen reference-interpreter oracle:
`PASS=877, FAIL=0, UNSUPPORTED=136` over 1035 commands (22 modules instantiated). Scope was
DERIVED from real data (not guessed): 4 sections {Type,Function,Export,Code}, 71 integer opcodes,
no control-flow/memory/float — see `goal-runs/m1-scope.txt`.

## Files
- `interp/` — engine: `values.py` (width math), `decoder.py` (binary decoder + opcode table),
  `machine.py` (interpreter + Trap), `runner.py` (pure comparator).
- `scripts/run_m1.py` — assert-runner CLI → `build/report/m1_summary.json`, nonzero on FAIL.
- `tools/enumerate_m1_scope.py` — Step 0 scope enumerator → `goal-runs/m1-scope.txt`.
- `tests/decoder_selftest.py` (vs wasm-objdump), `tests/test_semantics.py` (17 units),
  `tests/positive_control.py` (anti-false-pass).
- `.github/workflows/m1.yml` — CI gate (separate from m0.yml).

## Reproduce (Linux/WSL; WABT binaries are linux-x64 in vendor/wabt/bin)
```sh
export WASM2WAT=vendor/wabt/bin/wasm2wat WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump
python3 scripts/fetch_oracle.py && python3 scripts/convert.py   # if vendor/ build/ absent
python3 tools/enumerate_m1_scope.py
python3 tests/decoder_selftest.py
python3 tests/test_semantics.py
python3 tests/positive_control.py
python3 scripts/run_m1.py            # PASS=877 FAIL=0 UNSUPPORTED=136
```
On this Windows host, prefix each with: `wsl.exe -e bash -lc 'cd /mnt/c/Files/wasm-oracle && ... '`.

## Seams for M2+ (deferred)
- M2 structured control flow: the enumerated scope has NONE (no block/loop/if/br*), so the
  interpreter is a linear body evaluator (`interp/machine.py:_run`). M2 must add a block/label
  stack + branch handling and widen `decoder.OPCODES`. The decoder rejects unknown opcodes as
  Unsupported, so new opcodes surface as UNSUPPORTED until implemented — no silent mis-exec.
- M3 memory / M4 validation / M5 floats: new sections (Memory/Data/Global/Import) currently
  raise `Unsupported` in `decoder.decode`; add them there. Floats would need f32/f64 stack values
  (deliberately absent) — the purity gates guarantee the current targets need none.
- `interp/machine.instantiate()` is a named seam (no start/imports/globals today).

## Drift from the contract (recorded)
Contract prose said C++/CTest/MSVC/g++/core-ctest.yml/PR#2/"loadout core" — none exist here
(repo is Python-only; verified by the plan-auditor too). M1 realizes the contract SUBSTANCE in
Python. The "CTest gate" = `.github/workflows/m1.yml` + `scripts/run_m1.py` (nonzero on FAIL).
If a future milestone truly needs a C++ engine, that is a fresh decision, not implied here.
