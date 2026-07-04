# M2 execution log (append-only)

Baseline: `main` @ `ca2abb1` (clean). All work on-disk on `main`'s worktree; NOT committed/pushed
(owner-gated). WSL + pinned WABT for all wast2json/wasm2wat/wasm-objdump steps.

## Phase 2 — plan audit (independent-subagents)
- plan-auditor decision: **needs-replan**. Verified target arithmetic against pinned source
  (labels 29 / switch 28 / total 57) and the Step-0 method; required 5 named checks folded in:
  (1) br_table label-depth + zero-target case, (2) signed-LEB block-type own-decoder + standalone
  else/end tokens, (3) FAIL-CLOSED enumerate_m2_scope, (4) if-without-else + terminal-end
  structure-parse, (5) result-arity-on-branch distinct from depth. All folded into contract.md /
  plan.md; auditor pre-cleared execution once folded. Proceeded to Phase 3.

## Phase 3 — execution

### M2.0 — targets + fail-closed Step-0 scope
- Wrote `manifest_m2.json` (labels, switch + reasoned exclusions).
- Parameterized `scripts/convert.py` (+`--manifest` default m0, +`--report`); fixed relative
  `--report` path resolution.
- `convert.py --manifest manifest_m2.json --report …_m2.json` → 2/2 converted, 57 commands
  (labels 29 = 1+25+3; switch 28 = 1+26+1).
- Wrote fail-closed `tools/enumerate_m2_scope.py`. First run **EXIT 1** — gate FIRED, catching
  `local.set` (0x21, 55 uses) not in decoder scope, and objdump `local[N]` declaration artifacts.
  → data-forced scope refinement: added `local.set` (integer-pure; `local.tee` 0x22 absent),
  filtered `local[` artifacts. Recorded drift in contract/plan/manifest/state.
- Re-run after decoder extension → **EXIT 0**, VERDICT: IN SCOPE. `scope.txt` committed evidence.

### M2.1 — decoder extension
- `interp/decoder.py`: +opcodes nop/block/loop/if/else/br/br_if/br_table/drop/local.set;
  +IMM_BLOCKTYPE (signed-LEB own-decoder: 0x40→empty, 0x7F→i32, 0x7E→i64, float/typeidx→Unsupported)
  +IMM_BRTABLE (vec(u32)+u32); Instr gains bt/targets/default fields; body stays FLAT.
- Parameterized `tests/decoder_selftest.py` (+`--manifest`; skip `local[` decl lines).
- decoder_selftest (no-arg, 22 M1 modules) EXIT 0 (unchanged); `--manifest manifest_m2.json`
  (labels/switch) EXIT 0 — new opcode bytes + immediate boundaries match wasm-objdump.

### M2.2 — structured evaluator
- `interp/machine.py`: replaced linear `_run` with `_structure` (flat→nested block tree, pairs
  interior `end`s, disambiguates optional `else`, terminal `end` = body sentinel) + recursive
  `_exec_seq`/`_exec_block`/`_exec_instr` over a value stack + label stack; `_do_br` (br depth,
  block/if→past end, loop→header re-entry), `_Return` escape; +local.set/drop/nop. Integer ops
  unchanged. Removed now-unused `Func` import.
- `tests/test_control_flow.py` (19 units): block value via br, br depth 0/1/2, if selection,
  then-only + then/else in one func, if-without-else, loop+br_if countdown, br_table
  in-range/default/zero-target, return escape, drop, nop, arity 0/1, i64 block result. EXIT 0.
- `tests/test_semantics.py` (17 M1 units) EXIT 0 — no integer regression through the new evaluator.

### M2.3 — M2 runner
- `scripts/run_m2.py` imports `run_m1.run_file`/`FileResult` (run_m1.py untouched); manifest_m2
  discovery; neutralizes run_m1's "out of M1 scope" reason label in M2's report only.
- `run_m2.py` → modules=2, PASS=51, FAIL=0, UNSUPPORTED=4, 2+51+0+4==57. EXIT 0. First correct run.

### M2.4 — positive control
- `tests/positive_control_m2.py` over real labels assertion: pristine PASS≥1/FAIL==0; corrupted
  `expected` (block 1→2) → FAIL fires. EXIT 0.

### M2.5 — purity gates over M2
- Parameterized `tools/assert_operand_purity.py` + `tools/body_purity_check.py` (+`--manifest`
  default m0). M0 no-arg: both EXIT 0 (unchanged). M2 `--manifest manifest_m2.json`: labels/switch
  operand- and body-integer-pure, both EXIT 0.

### M2.6 — CI + README
- `.github/workflows/m2.yml` (independent; fetch→convert(m2)→purity(m2)→fail-closed scope +
  git-diff→decoder selftest(m2)→control-flow units→positive control→run_m2→self-contained
  assertions). All three workflow YAMLs parse; m2 assertion block passes locally; m0.yml/m1.yml
  unmodified. README: Layout + M2 section (curation finding, fail-closed gate, result, no
  overstated conformance).

### M2.7 — non-regression sweep
- M0: convert(no-arg) 4/4/1035; operand+body purity EXIT 0; run_skeleton supported==0.
- M1: enumerate_m1_scope reproduces m1-scope.txt (git diff --exit-code EXIT 0); decoder_selftest
  (no-arg) EXIT 0; test_semantics EXIT 0; positive_control EXIT 0; run_m1 PASS=877/FAIL=0/
  UNSUPPORTED=136 EXIT 0.
- Final M2 chain re-run end-to-end: all EXIT 0.

## Changed vs new files
- Modified (additive / backward-compatible): interp/decoder.py, interp/machine.py,
  scripts/convert.py, tests/decoder_selftest.py, tools/assert_operand_purity.py,
  tools/body_purity_check.py, README.md.
- New: manifest_m2.json, tools/enumerate_m2_scope.py, scripts/run_m2.py,
  tests/test_control_flow.py, tests/positive_control_m2.py, .github/workflows/m2.yml,
  goal-runs/m2-control-flow/*.
- Untouched (verified via git status): manifest_m0.json, goal-runs/m1-scope.txt, scripts/run_m1.py,
  tools/enumerate_m1_scope.py, .github/workflows/m0.yml, .github/workflows/m1.yml,
  tests/test_semantics.py, tests/positive_control.py, interp/values.py, interp/runner.py.
