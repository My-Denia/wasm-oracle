# M4 - Validation Execution (plan)

Risk: medium. Scope: M4-only curation artifacts and tooling first; no runtime/interpreter edits in the curation-only step. Audit mode: independent subagent plan-audit passed after replan.

## Milestones

### M4.0 - Goal-Run Artifact Skeleton

Add `goal-runs/m4-validation/contract.md`, `plan.md`, `state.json`, and `execution-log.md`.

Binary verification:

```sh
python3 tools/validate_m4_goal_run.py
```

The validator checks required files, `state.json` schema, expected validation total `200`, and owner-only boundaries.

### M4.1 - Step 0 Validation Scope Enumerator

Add `tools/enumerate_m4_validation_scope.py`. It reads converted JSON/artifacts from the M1/M2/M3 manifests, inventories every `assert_invalid` / `assert_malformed`, classifies current decoder outcome, sections, opcodes, expected text, contamination flags, and include/exclude policy.

Binary verification:

```sh
python3 tools/enumerate_m4_validation_scope.py
python3 tools/validate_m4_goal_run.py --require-scope
git diff --exit-code goal-runs/m4-validation/scope.txt goal-runs/m4-validation/scope.json
```

The enumerator must assert `included + unsupported == 200`. It must exit nonzero on unhandled command types, expected texts, module types, decoder outcomes, sections, opcodes, contamination flags, or admitted deferred features. Its inline self-check injects out-of-scope records and proves the policy gate fires.

### M4.2 - Curation Decision Gate

Use `scope.json` as the machine-readable policy artifact. Validator implementation may proceed only if every included assertion has an allowlisted expected text and matching mode, no deferred-feature contamination, current decoder acceptance, and only frozen M1-M3 sections/opcodes.

If the included set is broad or spans multiple validator subsystems, stop at the curation PR.

Binary verification:

```sh
python3 - <<'PY'
import json
s = json.load(open('goal-runs/m4-validation/scope.json', encoding='utf-8'))
assert s['totals']['validation_assertions'] == 200
assert s['totals']['included'] + s['totals']['unsupported'] == 200
assert not s['policy_violations']
for rec in s['records']:
    assert rec['decision'] in {'INCLUDED', 'UNSUPPORTED'}
PY
```

### M4.3 - Future Validator Path

Possible files: `interp/validator.py`, `scripts/run_m4.py`, `tests/test_validation.py`, `tests/positive_control_m4.py`, and `.github/workflows/m4.yml` extensions.

Binary verification:

```sh
python3 scripts/run_m4.py
python3 tests/test_validation.py
python3 tests/positive_control_m4.py
```

Future M4 runner must assert `PASS + FAIL + UNSUPPORTED == 200` and exit nonzero on any `FAIL`.

### M4.4 - Non-Regression

Re-run existing gates:

```sh
python3 scripts/run_skeleton.py
python3 scripts/run_m1.py
python3 scripts/run_m2.py
python3 scripts/run_m3.py
python3 tests/decoder_selftest.py
python3 tests/decoder_selftest.py --manifest manifest_m2.json
python3 tests/decoder_selftest.py --manifest manifest_m3.json
python3 tests/test_semantics.py
python3 tests/test_control_flow.py
python3 tests/test_memory.py
python3 tests/positive_control.py
python3 tests/positive_control_m2.py
python3 tests/positive_control_m3.py
```

Expected runner counts remain M0 supported 0 / unsupported 1035, M1 `877/0/136`, M2 `51/0/4`, and M3 `45/0/60`.

## Rollback

Remove/revert M4-only files and rerun M0-M3 gates. No old runner or interpreter file is modified in the curation-only step.

## Owner-Only Boundaries

No local commit, push, PR creation, force-push, merge, history rewrite, or self-merge without explicit owner approval.
