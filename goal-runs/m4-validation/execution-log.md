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

## M4 first validator slice implementation

- Plan audit round 1: `needs-replan`; required explicit owner-only boundaries, rollback notes, and a test proving deferred M4 records remain unsupported and unvalidated.
- Plan audit round 2: `pass` after replan.
- Implementation scope: `interp/validator.py`, `scripts/run_m4.py`, `tests/test_validation.py`, `tests/positive_control_m4.py`, `.github/workflows/m4.yml`, `README.md`, `goal-runs/m4-validation/state.json`, and `goal-runs/m4-validation/handoff.md`.
- Runner contract: `scope.json` is the only M4 input boundary; 65 included binary invalid modules must reject with matching category; 135 unsupported records are counted unsupported and never validated.
- Expected local gates: `python tests/test_validation.py`, `python tests/positive_control_m4.py`, `python scripts/run_m4.py`, `python tools/validate_m4_goal_run.py --require-scope`, `python tools/enumerate_m4_validation_scope.py` plus `git diff --exit-code` on scope artifacts, and M0-M3 runner count regression.
