# M4 - Validation Execution (goal contract)

M4 follows M3 on `main`: M1 integer core, M2 structured control flow, and M3 minimal linear memory are implemented and merged. Existing runners remain independent and must keep their exact accounting: M1 `PASS=877, FAIL=0, UNSUPPORTED=136`; M2 `PASS=51, FAIL=0, UNSUPPORTED=4`; M3 `modules=5, PASS=45, FAIL=0, UNSUPPORTED=60`.

M4 is not full WebAssembly validation conformance. M4 starts with validation curation over the already pinned and converted M1/M2/M3 targets. The scope is data-bound: only current `assert_invalid` / `assert_malformed` commands in the frozen manifests are inventoried, classified, and either admitted for a future validator or left `UNSUPPORTED` with a reason.

## Step 0 Scope

Source manifests:

- `manifest_m0.json`: `i32.wast`, `i64.wast`, `int_exprs.wast`, `int_literals.wast`.
- `manifest_m2.json`: `labels.wast`, `switch.wast`.
- `manifest_m3.json`: `store.wast`, `memory_size.wast`.

The current validation assertion inventory is frozen at 200 commands: 169 `assert_invalid` and 31 `assert_malformed`.

No command may be dropped. The Step 0 enumerator must prove:

`INCLUDED + UNSUPPORTED == 200`

It must exit nonzero on any unknown command type, expected text, module type, section, opcode, feature contamination, or unhandled policy category.

## M4 Curation Policy

An assertion is an M4 validator candidate only when all of these are true:

1. It is a binary `assert_invalid` artifact.
2. The current decoder parses it successfully.
3. Its sections and opcodes stay within the frozen M1-M3 implemented surface.
4. Its expected text is explicitly allowlisted.
5. It requires only structural/type validation over existing metadata and instructions.

Allowed candidate expected texts:

- `type mismatch`: category match initially; exact text matching may be required by the future runner once validator diagnostics are pinned.
- `unknown label`: category match initially; exact text matching may be required by the future runner once validator diagnostics are pinned.

Malformed text assertions are not admitted in the curation-only scope because this repository has no WAT parser. Unsupported-feature assertions are not admitted.

## Deferred Validation Categories

These remain out of M4 Step 0's admitted validator subset and must be counted as `UNSUPPORTED`:

- Text malformed assertions (`unknown operator`, `unexpected token`) until a WAT parsing milestone.
- Floats (`f32`, `f64`) and float value types/opcodes, deferred to M5.
- Calls and indirect calls.
- Globals, imports, tables, element segments, start functions.
- Loads, `i64.store`, and narrow stores.
- Data segments and bulk-memory.
- `select`, `local.tee`, `unreachable`, multi-value/typeidx block types.
- Reference-types, memory64, multi-memory, shared memory, SIMD, and other proposal features.

## Acceptance

M4 curation PR acceptance:

- `python3 tools/validate_m4_goal_run.py --require-scope` exits 0.
- `python3 tools/enumerate_m4_validation_scope.py` exits 0.
- `git diff --exit-code goal-runs/m4-validation/scope.txt goal-runs/m4-validation/scope.json` exits 0 after regeneration.
- Scope evidence records all 200 validation assertions with deterministic include/exclude decisions and no authored expected values.
- Old M1/M2/M3 runners keep their exact counts.

Future validator acceptance, only after Step 0 policy permits implementation:

- `scripts/run_m4.py` asserts `PASS + FAIL + UNSUPPORTED == 200`.
- Any `FAIL` exits nonzero.
- Included expected-invalid/malformed rejection classifies `PASS` only when the validator rejects with the pinned category/text mapping.
- Unexpected accept is `FAIL`.
- Wrong rejection category/text is `FAIL` where exact matching is pinned.
- Positive control mutates an accepted invalid/reason and must force `FAIL`.

## Owner Boundaries And Rollback

No local commit, push, PR creation, force-push, merge, history rewrite, or self-merge is permitted without explicit owner approval.

Rollback for this curation-only step is removal or revert of M4-only files plus rerunning M0-M3 baseline commands. No oracle expected values are authored or modified.
