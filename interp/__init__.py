"""interp — M1 integer execution core for wasm-oracle.

Engine-agnostic, integer-only WebAssembly executor scoped to EXACTLY the sections and
opcodes the 4 frozen targets' instantiated modules contain (enumerated in
goal-runs/m1-scope.txt): sections {Type, Function, Export, Code}; 71 i32/i64 opcodes; no
structured control flow, no memory, no floats (those are M2–M5).

Modules:
  values   — fixed-width integer math (mask / signed / clz / ctz / popcnt).
  decoder  — WASM binary decoder for the 4 in-scope sections + instruction decode.
  machine  — the interpreter: Trap/Unsupported, invoke(), exact integer semantics.
  runner   — pure classification helpers (compare a return, match a trap).
"""
