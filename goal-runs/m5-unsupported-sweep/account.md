# M5 per-file account — full test/core sweep at pin 82cd4f9

Branch `m5-unsupported-sweep` off `dc29ee8`. Frozen pin: spec@82cd4f9555dbf80b20611d8d3a2c6e6f444b228c, WABT 1.0.41. Frozen guardrail flags: `--disable-simd --disable-bulk-memory --disable-reference-types`. Oracle = expected values/texts in the WABT-converted JSON, authored by the spec reference interpreter; we author no expected outputs.

## Headline

| metric | value |
|---|---|
| test/core .wast files at pin | 97 |
| converted (runnable) | 55 |
| CONVERT-FAIL (recorded, expected set matched) | 42 |
| commands in converted files | 18149 |
| PASS | 17053 |
| FAIL | 0 |
| UNSUPPORTED | 447 |
| modules instantiated | 605 | 
| registered / bare actions | 2 / 42 |

Per-file identity `modules_ok + registered + actions_ok + PASS + FAIL + UNSUPPORTED == total` asserted for every file and globally. Identical results on Windows (Python 3.14) and WSL Ubuntu (Python 3.12).

## Converted files (55) — every command classified

| file | total | modules | reg | act | PASS | FAIL | UNSUPPORTED | unsupported reason |
|---|---|---|---|---|---|---|---|---|
| address.wast | 260 | 4 | 0 | 0 | 255 | 0 | 1 | assert_invalid on text-format module (no .wat parser at M5) ×1 |
| block.wast | 223 | 1 | 0 | 0 | 207 | 0 | 15 | assert_malformed on text-format module (no .wat parser at M5) ×15 |
| br.wast | 97 | 1 | 0 | 0 | 96 | 0 | 0 | — |
| call.wast | 91 | 1 | 0 | 0 | 90 | 0 | 0 | — |
| comments.wast | 8 | 5 | 0 | 0 | 3 | 0 | 0 | — |
| const.wast | 778 | 402 | 0 | 0 | 300 | 0 | 76 | assert_malformed on text-format module (no .wat parser at M5) ×76 |
| conversions.wast | 619 | 1 | 0 | 0 | 618 | 0 | 0 | — |
| custom.wast | 11 | 3 | 0 | 0 | 8 | 0 | 0 | — |
| endianness.wast | 69 | 1 | 0 | 0 | 68 | 0 | 0 | — |
| f32.wast | 2514 | 1 | 0 | 0 | 2511 | 0 | 2 | assert_malformed on text-format module (no .wat parser at M5) ×2 |
| f32_bitwise.wast | 364 | 1 | 0 | 0 | 363 | 0 | 0 | — |
| f32_cmp.wast | 2407 | 1 | 0 | 0 | 2406 | 0 | 0 | — |
| f64.wast | 2514 | 1 | 0 | 0 | 2511 | 0 | 2 | assert_malformed on text-format module (no .wat parser at M5) ×2 |
| f64_bitwise.wast | 364 | 1 | 0 | 0 | 363 | 0 | 0 | — |
| f64_cmp.wast | 2407 | 1 | 0 | 0 | 2406 | 0 | 0 | — |
| fac.wast | 8 | 1 | 0 | 0 | 7 | 0 | 0 | — |
| float_exprs.wast | 927 | 98 | 0 | 10 | 819 | 0 | 0 | — |
| float_literals.wast | 179 | 2 | 0 | 0 | 99 | 0 | 78 | assert_malformed on text-format module (no .wat parser at M5) ×78 |
| float_memory.wast | 90 | 6 | 0 | 24 | 60 | 0 | 0 | — |
| float_misc.wast | 471 | 1 | 0 | 0 | 470 | 0 | 0 | — |
| forward.wast | 5 | 1 | 0 | 0 | 4 | 0 | 0 | — |
| func_ptrs.wast | 36 | 3 | 0 | 1 | 32 | 0 | 0 | — |
| i32.wast | 460 | 1 | 0 | 0 | 457 | 0 | 2 | assert_malformed on text-format module (no .wat parser at M5) ×2 |
| i64.wast | 416 | 1 | 0 | 0 | 413 | 0 | 2 | assert_malformed on text-format module (no .wat parser at M5) ×2 |
| if.wast | 241 | 1 | 0 | 0 | 216 | 0 | 24 | assert_malformed on text-format module (no .wat parser at M5) ×24 |
| inline-module.wast | 1 | 1 | 0 | 0 | 0 | 0 | 0 | — |
| int_exprs.wast | 108 | 19 | 0 | 0 | 89 | 0 | 0 | — |
| int_literals.wast | 51 | 1 | 0 | 0 | 30 | 0 | 20 | assert_malformed on text-format module (no .wat parser at M5) ×20 |
| labels.wast | 29 | 1 | 0 | 0 | 28 | 0 | 0 | — |
| left-to-right.wast | 96 | 1 | 0 | 0 | 95 | 0 | 0 | — |
| load.wast | 97 | 1 | 0 | 0 | 83 | 0 | 13 | assert_malformed on text-format module (no .wat parser at M5) ×13 |
| local_get.wast | 36 | 1 | 0 | 0 | 35 | 0 | 0 | — |
| local_set.wast | 53 | 1 | 0 | 0 | 52 | 0 | 0 | — |
| loop.wast | 121 | 1 | 0 | 0 | 105 | 0 | 15 | assert_malformed on text-format module (no .wat parser at M5) ×15 |
| memory_grow.wast | 106 | 8 | 2 | 0 | 96 | 0 | 0 | — |
| memory_redundancy.wast | 8 | 1 | 0 | 3 | 4 | 0 | 0 | — |
| memory_size.wast | 42 | 4 | 0 | 0 | 38 | 0 | 0 | — |
| memory_trap.wast | 182 | 2 | 0 | 0 | 180 | 0 | 0 | — |
| names.wast | 486 | 4 | 0 | 0 | 482 | 0 | 0 | — |
| nop.wast | 88 | 1 | 0 | 0 | 87 | 0 | 0 | — |
| obsolete-keywords.wast | 11 | 0 | 0 | 0 | 0 | 0 | 11 | assert_malformed on text-format module (no .wat parser at M5) ×11 |
| return.wast | 84 | 1 | 0 | 0 | 83 | 0 | 0 | — |
| skip-stack-guard-page.wast | 11 | 1 | 0 | 0 | 10 | 0 | 0 | — |
| stack.wast | 7 | 2 | 0 | 0 | 5 | 0 | 0 | — |
| start.wast | 20 | 5 | 0 | 4 | 10 | 0 | 1 | assert_malformed on text-format module (no .wat parser at M5) ×1 |
| store.wast | 68 | 1 | 0 | 0 | 60 | 0 | 7 | assert_malformed on text-format module (no .wat parser at M5) ×7 |
| switch.wast | 28 | 1 | 0 | 0 | 27 | 0 | 0 | — |
| traps.wast | 36 | 4 | 0 | 0 | 32 | 0 | 0 | — |
| type.wast | 3 | 1 | 0 | 0 | 0 | 0 | 2 | assert_malformed on text-format module (no .wat parser at M5) ×2 |
| unreachable.wast | 64 | 1 | 0 | 0 | 63 | 0 | 0 | — |
| unwind.wast | 50 | 1 | 0 | 0 | 49 | 0 | 0 | — |
| utf8-custom-section-id.wast | 176 | 0 | 0 | 0 | 176 | 0 | 0 | — |
| utf8-import-field.wast | 176 | 0 | 0 | 0 | 176 | 0 | 0 | — |
| utf8-import-module.wast | 176 | 0 | 0 | 0 | 176 | 0 | 0 | — |
| utf8-invalid-encoding.wast | 176 | 0 | 0 | 0 | 0 | 0 | 176 | assert_malformed on text-format module (no .wat parser at M5) ×176 |

All 447 UNSUPPORTED are exactly the two text-format-module boundaries (446 `assert_malformed` text + 1 `assert_invalid` text, address.wast): judging them requires a .wat parser, which this repository deliberately does not have. Every BINARY assert in the corpus was judged.

## CONVERT-FAIL (42) — pinned toolchain + frozen flags reject at conversion

Recorded with wast2json stderr; the actual set byte-matches `manifest_m5.json.expected_convert_fail` (drift check in convert_m5.py).

| file | wast2json reason (first line) |
|---|---|
| align.wast | offset must be less than or equal to 0xffffffff |
| annotations.wast | annotations not enabled: a |
| binary-leb128.wast | error in binary module: @0x00000012: invalid memory index 2: bulk memory not allowed |
| binary.wast | error in binary module: @0x0000000a: invalid section code: 12 |
| br_if.wast | unexpected token (, expected ). |
| br_on_non_null.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| br_on_null.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| br_table.wast | value type not allowed: externref |
| call_indirect.wast | unexpected token "(", expected an offset expr (e.g. (i32.const 123)). |
| call_ref.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| data.wast | passive data segments are not allowed |
| elem.wast | unexpected token "funcref", expected an offset expr (e.g. (i32.const 123)). |
| exports.wast | tag not allowed |
| func.wast | unexpected token "declare", expected an offset expr (e.g. (i32.const 123)). |
| global.wast | value type not allowed: externref |
| id.wast | quoted identifiers are not supported without annotations |
| imports.wast | tag not allowed |
| instance.wast | unexpected token "definition", expected a module field. |
| linking.wast | unexpected token "declare", expected an offset expr (e.g. (i32.const 123)). |
| local_init.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| local_tee.wast | unexpected token (, expected ). |
| memory.wast | unexpected token "definition", expected a module field. |
| ref.wast | value type not allowed: funcref |
| ref_as_non_null.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| ref_func.wast | value type not allowed: funcref |
| ref_is_null.wast | value type not allowed: funcref |
| ref_null.wast | unexpected token anyref, expected ). |
| return_call.wast | opcode not allowed: return_call |
| return_call_indirect.wast | opcode not allowed: return_call |
| return_call_ref.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| select.wast | unexpected token "result", expected an expr. |
| table.wast | unexpected token "definition", expected a module field. |
| table_get.wast | value type not allowed: externref |
| table_grow.wast | value type not allowed: externref |
| table_set.wast | value type not allowed: externref |
| table_size.wast | value type not allowed: externref |
| token.wast | passive data segments are not allowed |
| type-canon.wast | unexpected token "rec", expected a module field. |
| type-equivalence.wast | unexpected token "(", expected i32, i64, f32, f64, v128, externref, exnref or funcref. |
| type-rec.wast | unexpected token (, expected ). |
| unreached-invalid.wast | opcode not allowed: ref.as_non_null |
| unreached-valid.wast | opcode not allowed: ref.is_null |

## Judgment surface actually covered (fail-closed inventory)

From `build/report/m5_text_inventory.json` (rebuilt every conversion; any new text is a
nonzero-exit stop, never silently absorbed):

- assert_return 15,386 — all PASS (bitwise i32/i64/f32/f64 incl. multi-value and
  nan:canonical / nan:arithmetic class checks).
- assert_malformed binary 536 — all PASS with byte-exact texts: malformed UTF-8 encoding 528,
  unexpected end 3, length out of bounds 2, malformed section id 1, function and code section
  have inconsistent lengths 1, data count and data section have inconsistent lengths 1.
- assert_invalid binary 684 — all PASS with byte-exact texts: type mismatch 657, unknown
  local 12, unknown label 4, unknown function 3, unknown table 2, constant expression
  required 2, unknown type 2, start function 2.
- assert_trap 433 — all PASS with byte-exact texts: out of bounds memory access 239,
  unreachable 66, integer overflow 41, invalid conversion to integer 40, integer divide by
  zero 38, undefined element 9.
- assert_exhaustion 13 — all PASS ("call stack exhausted"; wasm-frame cap 1000 +
  RecursionError backstop).
- assert_uninstantiable 1 — PASS (start.wast, trapping start function, "unreachable").
- module 605 — all decode + VALIDATE + instantiate cleanly (the 605 oracle-valid modules are
  the validator's standing negative control against over-rejection).
- register 2, bare action 42 — all succeed (memory_grow.wast cross-instance memory imports).

## Positive-control evidence (PASS is falsifiable)

`tests/positive_control_m5.py` (green on Windows + WSL): every judgment class flips to FAIL
on corrupted input — integer +1, float bit-flip, NaN-class replacement (end-to-end through
run_m5.run_file on real i32/f32/float_exprs corpus copies), wrong/absent trap text,
non-exhausting exhaustion, valid-claimed-invalid, wrong invalid text, well-formed-claimed-
malformed, wrong malformed text, cleanly-instantiable-claimed-uninstantiable, multi-value
arity. Unit layers additionally prove the comparator and each judge fire in isolation.

## Where we stopped, and why (frozen boundaries)

1. 42 CONVERT-FAIL files: their syntax needs post-MVP features (reference-types/externref,
   GC / typed function references, tail-call, multi-memory, memory64-era 64-bit offsets,
   annotations, passive/bulk-memory segments, or a newer binary format than pinned WABT
   1.0.41 understands). The pin and the guardrail flags are FROZEN by the task contract —
   enabling any of them changes the oracle surface, so these stay CONVERT-FAIL by design.
2. 446 + 1 text-format assert_malformed / assert_invalid: need a .wat (text-format) parser.
   The repository's method boundary is "WABT converts, we never parse .wast text"; building a
   WAT parser is a different (large) milestone and would not add oracle-diff value for
   EXECUTION semantics.
3. assert_unlinkable / get actions / other spectest fields: absent from the convertible
   corpus (verified by the fail-closed inventory); handling exists structurally (explicit
   UNSUPPORTED classification for assert_unlinkable) but nothing exercises it.
4. Everything else — floats, calls, call_indirect + tables + elem, globals, all loads/stores,
   data segments, start, imports/register linking, multi-value, exhaustion, full validation
   over this surface, strict malformed detection incl. UTF-8 — is IMPLEMENTED and judged;
   zero deviations from the oracle remain (FAIL = 0).

## Non-regression (frozen milestones re-verified)

`scripts/check_regression_m5.py` (WSL, exit 0): M0 supported=0/unsupported=1035;
M1 877/0/136; M2 51/0/4; M3 45/0/60; M4 curation 200=65+135 with 0 violations (re-derived
scope byte-identical to committed evidence); M4 execution 65/0/135; `git status` shows no
tracked pre-existing file modified — M5 is additive-only (new package interp5/, new scripts,
new tests, manifest_m5.json; interp/, tools/, all prior scripts/tests/manifests/workflows and
committed goal-runs artifacts untouched).

