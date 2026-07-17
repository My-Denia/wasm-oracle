# Plan — M5 unsupported-sweep (branch m5-unsupported-sweep off dc29ee8)

Status: proposed (rev 2 — plan-audit round-1 findings applied)
Created: 2026-07-13T20:20:49+00:00

## Goal contract

Push UNSUPPORTED across ALL vendor/spec/test/core/*.wast (97 files at pin 82cd4f9) to the
furthest reachable point under the FROZEN pin (spec@82cd4f9, WABT 1.0.41) and FROZEN guardrail
flags (--disable-simd --disable-bulk-memory --disable-reference-types). FAIL does not block:
record and continue. Deliverable = per-file account (PASS/FAIL/UNSUPPORTED/CONVERT-FAIL, each
with reason) + where we stopped and why. No commit/push (owner-only). No merge to main.

## Hard constraints (from owner + repo convention)

1. Pin and guardrail flags byte-identical to manifest_m0 values.
2. M0-M4 existing files UNTOUCHED (zero modifications to interp/, scripts/run_m1..m4.py,
   scripts/convert.py, scripts/run_skeleton.py, manifests m0/m2/m3, existing tools/, tests/
   files, workflows, committed goal-runs artifacts).
3. Regression evidence required: M0 skeleton supported=0/unsupported=1035; M1 877/0/136;
   M2 51/0/4; M3 45/0/60; M4 65/0/135.
4. Per-file accounting identity: modules_ok + registered + actions_ok + PASS + FAIL +
   UNSUPPORTED == total commands. Unknown command types explicitly classified, never dropped.
5. Every PASS-capable judgment class has a positive control proving it can emit FAIL.
6. Expected values/texts come only from the WABT-converted JSON (authored by the reference
   interpreter); we author no expected outputs.

## Key design decision — separate interp5 package, zero shared-file edits

M4's frozen curation (goal-runs/m4-validation/scope.*) is derived from "what the CURRENT
interp.decoder accepts". Extending interp/decoder.py in place would silently change that
derivation and drift M4's committed scope artifacts. Therefore M5 is a NEW package interp5/
(fresh decoder/machine/validator/floats; may import interp.values read-only) plus new scripts.
This makes constraints 2+3 structural rather than behavioral.

## Evidence base (phase-0/1 sweep: build/report/m5_sweep.json + m5_inventory.json)

- 97 .wast files; 55 convert under pinned toolchain+flags; 42 CONVERT-FAIL (externref/GC/
  tail-call/multi-memory/annotations/bulk-memory syntax/64-bit offsets — each recorded with
  wast2json stderr as reason).
- 18,149 commands in the 55 converted files: assert_return 15,386; assert_malformed 982
  (536 binary / 446 text); assert_invalid 685 (684 binary / 1 text); assert_trap 433;
  module 605; action 42 (all invoke); assert_exhaustion 13; register 2; assert_uninstantiable 1.
- Full opcode surface: complete MVP numeric/parametric/control/memory set + sign-ext +
  saturating trunc (0xFC 0-7), all f32/f64. Sections: Custom, Type, Import, Function, Table,
  Memory, Global, Export, Start, Elem, Code, Data.
- Imports actually used: spectest.print_i32, spectest.print, registered-instance memory
  re-import (memory_grow.wast via register; 6 named-module references).
- assert_invalid binary texts (8): type mismatch 657, unknown local 12, unknown label 4,
  unknown function 3, unknown table 2, constant expression required 2, unknown type 2,
  start function 2.
- assert_malformed binary texts (6): malformed UTF-8 encoding 528, unexpected end 3, length
  out of bounds 2, malformed section id 1, function/code inconsistent lengths 1, data count
  inconsistent 1.
- assert_trap texts (6): out of bounds memory access 239, unreachable 66, integer overflow 41,
  invalid conversion to integer 40, integer divide by zero 38, undefined element 9.
- assert_exhaustion: "call stack exhausted" x13 (call, fac, skip-stack-guard-page).
- assert_uninstantiable: start.wast "unreachable" x1.

## Milestones

1. Scaffold: manifest_m5.json (inherits pin from M0, same disable_features, targets = all 97
   files with expected-CONVERT-FAIL marking) + scripts/convert_m5.py (convert all; CONVERT-FAIL
   recorded per file with stderr, never a hard exit).
   PATH ISOLATION (audit finding 3): convert_m5 writes ONLY build/converted_m5/<stem>/ and
   build/report/m5_conversion_report.json — never build/converted/ or
   build/report/conversion_report.json, which the frozen M1/M4 toolchain reads.
   TEXT INVENTORY (audit finding 5): convert_m5 also persists
   build/report/m5_text_inventory.json — per-expected-text counts for assert_invalid (binary/
   text), assert_malformed (binary/text), assert_trap, assert_exhaustion, assert_uninstantiable,
   action types, named-module refs — and FAILS CLOSED (nonzero exit) if any text/type outside
   the enumerated sets in this plan appears, so a 9th invalid text is a loud stop, not an
   unexplained milestone-7 FAIL.
   Verify: convert_m5 exit 0; report shows 55 ok / 42 convert-fail; flags in report byte-equal
   to manifest_m0 conversion.disable_features; text inventory matches the evidence base above.
2. interp5/fvalues.py — f32/f64 bit-level float ops: bitcast helpers, arithmetic via double with
   single-rounding-safe demote (Figueroa 2p+2), custom correctly-rounded i64->f32, min/max/
   nearest/copysign/abs/neg per spec (bit ops for sign/NaN payloads), NaN class predicates,
   trunc/trunc_sat with exact bounds.
   Verify: tests/test_m5_floats.py green (i64->f32 double-rounding vectors, NaN payload
   preservation, +-0 min/max, demote/promote edges).
3. interp5/decoder.py — STRICT full decoder: strict LEB (max-bytes + unused-bits), UTF-8
   validation of all names, section order/duplication, length consistency, all sections and
   opcodes above; DecodeError(text) carries the 6 spec malformed texts; anything beyond the
   surface (0xFC>=8, 0xFD, ref ops, shared/64-bit limits, multi-table, etc.) raises Unsupported.
   Verify: tests/test_m5_decoder_selftest.py — decode every module .wasm of the 55 files,
   cross-check func count/exports/opcode stream vs pinned wasm-objdump (WSL).
4. interp5/machine.py — full executor: typed value stack, structured control incl. multi-value
   blocks (typeidx blocktypes, loop params), call/call_indirect (+type-check traps), tables+
   elem, globals (mutable, const-expr init), linear memory with LAZY page allocation (4 GiB
   grow succeeds without 4 GiB RSS), all loads/stores LE + OOB, start section, data segments,
   imports (spectest host print/print_i32; registered-instance objects), instance registry +
   register, wasm-frame cap -> Trap("call stack exhausted") with RecursionError backstop.
   Verify: tests/test_m5_machine.py unit tests (control-flow, memory, calls, indirect, globals,
   multi-value, exhaustion).
5. interp5/validator.py — full validator over the decoded surface emitting EXACTLY the 8 spec
   texts; PASS iff rejects with expected text; acceptance or wrong text = FAIL. (Executor-
   subagent authored against the decoder Module API; orchestrator-reviewed.)
   Verify: tests/test_m5_validator.py per-text unit cases.
6. scripts/run_m5.py + tests/positive_control_m5.py — all 9 command types classified, nothing
   dropped; per-file identity assert; build/report/m5_summary.json + account.md. Positive
   controls: corrupted expected (int/float/NaN-class), wrong trap text, non-exhausting
   exhaustion, valid-claimed-invalid, well-formed-claimed-malformed, non-trapping
   uninstantiable, multi-value arity — each must flip to FAIL.
   STANDING NEGATIVE CONTROL (audit finding 2): every `module` command is decoded AND validated
   by interp5.validator BEFORE instantiation; a validator rejection of a corpus (oracle-valid)
   module counts as FAIL in the identity, never as a skip. The 605 valid modules thereby
   pressure-test the validator against over-rejection; the 684 assert_invalid pressure-test it
   against under-rejection.
   FAILURE SEMANTICS (audit finding 6): `action` — success → actions_ok; unexpected trap or
   execution error → FAIL; out-of-scope construct → UNSUPPORTED. `register` — named instance
   live → registered; instance absent because its module was UNSUPPORTED → UNSUPPORTED
   (chained reason); instance absent for any other cause → FAIL. No path raises an uncounted
   exception.
   Verify: positive_control_m5 green; smoke run over i32/f32_cmp/memory_grow with identity.
7. Full run + FAIL triage loop: run all 55; diagnose every FAIL; fix implementation bugs; keep
   irreducible mismatches as recorded FAILs with reasons (deviation is data). UNSUPPORTED only
   with a reason tied to a frozen boundary (text-format module / feature beyond pinned surface /
   import outside implemented host surface).
   Verify: final report; identity holds for all 55; every FAIL has a triage note.
8. Regression (audit finding 4 — binary, not narrative): NEW script
   scripts/check_regression_m5.py runs the frozen pipeline in the m4.yml order (convert m0 →
   run_skeleton → run_m1 → convert m2 → run_m2 → convert m3 → run_m3 → enumerate_m4 → run_m4)
   and ASSERTS the locked counts (M0 supported=0/unsupported=1035; M1 877/0/136; M2 51/0/4;
   M3 45/0/60; M4 65/0/135), exiting nonzero on any drift. It runs LAST (after all M5 conversion
   work) so shared build/converted/ state left behind is the frozen toolchain's own output.
   Also asserts `git status --porcelain` shows no modification to any tracked pre-existing file.
   Verify: check_regression_m5.py exit 0 in WSL; output captured in execution log.
9. Closeout: account.md per-file table (incl. 42 CONVERT-FAIL reasons + text-malformed
   UNSUPPORTED + stop boundaries), handoff.md, state.json final; diff stat; NO commit
   (owner-only).

## Assumptions to verify en route

- A1: multi-value blocktypes actually appear in converted corpus (decoder selftest will show;
  machine paths unit-tested regardless).
- A2: memory_grow.wast expects 4 GiB grow to SUCCEED -> lazy memory (verify by reading its JSON
  at milestone 4).
- A3: Python strict utf-8 decode == WASM UTF-8 validation for the 528 utf8 cases (deviations
  become triaged FAILs).
- A4: no get actions / assert_unlinkable / non-spectest func imports in corpus (verified by
  sweep).

## Rollback

All new files on a private branch with no commits; rollback = git clean of new files.
build/ and vendor/ are gitignored and regenerable.

## Owner-only boundaries

commit, push, PR, README edits (deferred to closeout note), any pin/flag change (prohibited).
