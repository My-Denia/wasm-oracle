# Handoff: m5-unsupported-sweep

<!-- gah:auto -->
Status: completed | Risk: medium | Audit: independent-subagents
Gates: plan=pass execution=pass
Latest checkpoint: run closed
<!-- /gah:auto -->

## What Changed

ALL NEW FILES on branch `m5-unsupported-sweep` (off `dc29ee8`), zero modifications to tracked
pre-existing files, NOTHING COMMITTED (owner-only):

- `interp5/` — new engine package: `fvalues.py` (exact IEEE f32/f64 over bit patterns, custom
  correctly-rounded int→f32), `decoder.py` (strict full-surface decoder, 6 spec malformed
  texts, objdump-verified), `validator.py` (full validator, 8 byte-exact invalid texts),
  `machine.py` (full executor: multi-value, calls/call_indirect, tables/elem, globals, lazy
  4 GiB memory, all loads/stores, data/start, spectest+register linking, exhaustion cap).
- `scripts/convert_m5.py` (CONVERT-FAIL is data; disjoint paths `build/converted_m5/` +
  `build/report/m5_*`; fail-closed text inventory), `scripts/run_m5.py` (all 9 command types,
  per-file identity), `scripts/check_regression_m5.py` (binary M0-M4 non-regression gate).
- `manifest_m5.json` (97 targets, frozen pin/flags inherited from M0, expected_convert_fail
  enumerated, judgment_boundaries fail-closed).
- `tests/test_m5_floats.py`, `test_m5_machine.py`, `test_m5_validator.py`,
  `test_m5_decoder_selftest.py` (WSL), `positive_control_m5.py`.
- `goal-runs/m5-unsupported-sweep/` — plan (rev 2), account.md (per-file account), state,
  execution log.

## Result

55/97 files convert under the frozen pin+flags; 42 CONVERT-FAIL recorded with wast2json
stderr (byte-matches the manifest expectation). 18,149 commands: **PASS=17,053 FAIL=0
UNSUPPORTED=447** — the 447 are exactly the two text-format-module boundaries (446
assert_malformed text + 1 assert_invalid text; no .wat parser by design). Identity holds per
file and globally; identical results Windows + WSL. Regression gate exit 0 (M0 0/1035,
M1 877/0/136, M2 51/0/4, M3 45/0/60, M4 curation 200=65+135/0, M4 exec 65/0/135). Both
audits (plan r2, execution r1) passed with independent verification.

## Next Safe Action

Owner decisions only: review `account.md`, then commit the branch (no AI trailer), optionally
add an M5 README section + CI workflow (deliberately NOT added — README/workflows are shared
files; a proposed m5.yml would mirror m4.yml: fetch → convert_m5 → unit tests → decoder
selftest → positive controls → run_m5 → check_regression_m5). Rebuildable local state:
`vendor/`, `build/` (gitignored) via fetch_oracle.py + convert*.py.
