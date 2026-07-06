# M4 validation execution log

## Baseline before M4 edits

- `git status --short --branch`: `main...origin/main`, clean.
- `git log -1 --oneline --decorate`: `627c7aa (HEAD -> main, origin/main) Merge pull request #5 from My-Denia/m3-linear-memory`.
- M0 local baseline passed: `fetch_oracle.py`, reference interpreter build + `i32.wast` smoke, `convert.py`, `assert_operand_purity.py`, `body_purity_check.py`, `run_skeleton.py`.
- M1 local baseline passed: `run_m1.py` => `PASS=877, FAIL=0, UNSUPPORTED=136`.
- M2 local baseline passed: `run_m2.py` => `PASS=51, FAIL=0, UNSUPPORTED=4`.
- M3 local baseline passed: `run_m3.py` => `PASS=45, FAIL=0, UNSUPPORTED=60`.
- Current `test_memory.py` ran 30 tests.

## Plan audit

- First plan-auditor decision: `needs-replan`.
- Replan added binary gates for artifacts, 200-command accounting, exact include/exclude policy, reject text allowlist, owner-only boundaries, and rollback.
- Second plan-auditor decision: `pass`.
