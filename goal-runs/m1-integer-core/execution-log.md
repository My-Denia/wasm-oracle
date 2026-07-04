# M1 execution log (append-only)

## Workspace gate
- Baseline: `main` @ `f5096f0` (Merge PR #2). Branch: `m1-integer-core` (agent-created).
- Dirty at start: only untracked `out/` (prior-context scratch: extract_assert_types.py,
  summarize_manifest.py, body_purity_check.py copy, m0_verification_dump.txt) — NOT touched.
- Artifact root: `goal-runs/m1-integer-core/` (run tracking) + `goal-runs/m1-scope.txt`
  (contract-specified scope evidence). `vendor/`+`build/` gitignored, agent-generated.
- Validation commands: WSL + pinned WABT (reproduces CI). Rollback: delete branch.

## M1.1 — Step 0 (data-gated scope) — DONE
- `wsl python3 scripts/fetch_oracle.py` → spec@82cd4f9 + wabt@1.0.41 (sha256 OK). exit 0.
- `wsl python3 scripts/convert.py` → build/converted/{i32,i64,int_exprs,int_literals}/*.json+*.wasm.
  4/4 converted, 1035 commands. exit 0.
- Wrote `tools/enumerate_m1_scope.py`; ran under WSL → `goal-runs/m1-scope.txt`. exit 0.
  Determinism: byte-identical re-run. 22 modules; 4 sections; 71 opcodes; findings: no
  control-flow/memory/float. Scope bound (see plan.md M1.1 RESULT).

## Read-only recon (informs M1.4; done while plan-audit runs)
- assert_trap texts (34): 'integer divide by zero' x30, 'integer overflow' x4 (both in-scope).
- All 877 actions are type=invoke. assert_return arity always 1. expected/arg types i32/i64 only.
- Validation: assert_invalid=112 (binary), assert_malformed=24 (text) -> UNSUPPORTED.
- Overflow trap example: div_s(2147483648, 4294967295) = div_s(INT_MIN,-1).

## M1.2 — Decoder — DONE
- interp/{__init__,values,decoder}.py. Decoder scoped to {Type,Function,Export,Code}; unknown
  section/valtype/export-kind/opcode -> Unsupported (no silent accept).
- tests/decoder_selftest.py: decode all 22 instantiated modules; cross-check func count,
  export names, and per-function opcode stream vs pinned wasm-objdump. exit 0 (all match).
  => opcode byte-table is evidence-checked against WABT, not trusted.

## M1.3 — Interpreter — DONE
- interp/machine.py: i32/i64 stack; dispatch over the 71 opcodes; exact semantics (wrap,
  masked shifts, arith/logical shr, rotate, clz/ctz/popcnt, rem_s sign, div/rem traps, wrap/extend).
- tests/test_semantics.py: 17 unit tests (edge + trap cases). exit 0.

## M1.4 — Assert-runner — DONE
- scripts/run_m1.py + interp/runner.py. Classifies PASS/FAIL/UNSUPPORTED; trap-KIND matching
  (divide-by-zero vs overflow); no-drop accounting; exit nonzero on FAIL; build/report/m1_summary.json.
- Result: files=4 commands=1035 modules=22 PASS=877 FAIL=0 UNSUPPORTED=136
  (assert_invalid×112 + assert_malformed×24). Accounting 22+877+0+136=1035. exit 0.

## M1.5 — Comparator positive control — DONE
- tests/positive_control.py: unit (compare_return/trap_matches reject mismatches) + end-to-end
  (corrupt a real expected via run_m1.run_file -> FAIL fires; clean copy -> 0 FAIL). exit 0.
  Injected 195940365->195940366 correctly classified FAIL. Green runs are meaningful.

## M1.6 — CI gate + M0 green — DONE
- .github/workflows/m1.yml (SEPARATE from m0.yml; m0.yml untouched -> zero M0 regression surface).
  fetch->convert->purity gates->enumerate(+determinism git-diff)->decoder/semantics/positive->run_m1->assert.
- Full local chain green under WSL. M0 not regressed: assert_operand_purity, body_purity_check,
  run_skeleton (supported==0) all exit 0. git status: only ADDED files + README doc edit.

## Hygiene
- 1565 LOC; stdlib-only; no temp-file leaks; source all LF (only gitignored .pyc are binary);
  no TODO/debug/hardcoded paths.
