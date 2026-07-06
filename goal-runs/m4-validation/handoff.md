# M4 Validation Execution Handoff

Status: first validator slice implemented over the committed M4 curation scope.

## Scope

- Input artifact: `goal-runs/m4-validation/scope.json`.
- Total validation records: 200.
- Included records: 65 binary `assert_invalid` modules with decoder status `decoder_accept`.
- Unsupported records: 135 deferred validation cases; they remain counted `UNSUPPORTED`.
- No full WebAssembly validation conformance is claimed.

## Runtime Contract

- `interp/validator.py` validates decoded modules over the existing M1-M3 surface only.
- `scripts/run_m4.py` sends only `INCLUDED` records to the validator.
- An included record passes only when validation rejects it with the category recorded in `scope.json`.
- Validator acceptance or wrong-category rejection is a runner `FAIL`.
- Unsupported records are not decoded, validated, or reclassified as pass.

## Verification

Run after converting M0/M2/M3 manifests:

```sh
python tools/enumerate_m4_validation_scope.py
python tools/validate_m4_goal_run.py --require-scope
git diff --exit-code goal-runs/m4-validation/scope.txt goal-runs/m4-validation/scope.json
python tests/test_validation.py
python tests/positive_control_m4.py
python scripts/run_m4.py
python scripts/run_skeleton.py
python scripts/run_m1.py
python scripts/run_m2.py
python scripts/run_m3.py
```

Expected execution counts:

- M4: `PASS=65 FAIL=0 UNSUPPORTED=135`.
- M1: `PASS=877 FAIL=0 UNSUPPORTED=136`.
- M2: `PASS=51 FAIL=0 UNSUPPORTED=4`.
- M3: `PASS=45 FAIL=0 UNSUPPORTED=60`.

## Boundaries

- No floats, calls, imports/globals/tables/start/elem, loads, i64/narrow stores, data segments,
  bulk-memory, reference-types, memory64, multi-memory, or text WAT malformed execution.
- No push, PR, merge, force-push, or local commit without owner approval.
