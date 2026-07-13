"""interp5 — the M5 full-surface engine (decoder / validator / machine / float core).

A NEW package, deliberately separate from the frozen M1–M3 `interp/` package: M4's committed
curation artifacts are derived from what `interp.decoder` accepts, so `interp/` must not change.
interp5 re-implements the full surface that the pinned toolchain + frozen guardrail flags let
through: all MVP sections (plus Custom skip and DataCount consistency checking), i32/i64/f32/f64,
structured control flow with multi-value, calls / call_indirect / tables / elem segments,
globals, linear memory with loads/stores and data segments, start functions, imports
(spectest host + registered instances), and a full validator for the enumerated
assert_invalid surface.

Value representation: every runtime value is an unsigned Python int holding the BIT PATTERN of
the wasm value (i32→32-bit, i64→64-bit, f32→IEEE-754 binary32 bits, f64→binary64 bits). This
matches the WABT JSON operand encoding exactly, makes comparisons bitwise, and keeps NaN
payloads exact.
"""
