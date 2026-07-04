"""runner.py — pure classification helpers for the assert-runner (no I/O, unit-testable).

These are the comparator the milestone's positive-control test exercises: a green run must be
evidence the comparator actually fires on a mismatch, not that it silently passes.
"""
from __future__ import annotations

from . import values as V

WIDTH = {"i32": 32, "i64": 64}


def decode_operand(operand: dict) -> int:
    """WABT JSON operand {"type":"i32","value":"4294967295"} -> unsigned canonical int.
    `value` is the UNSIGNED decimal of the bit pattern (e.g. i32 -1 == "4294967295")."""
    t = operand["type"]
    if t not in WIDTH:
        raise ValueError(f"non-integer operand type {t!r} (out of M1 scope)")
    return int(operand["value"]) & V.mask(WIDTH[t])


def compare_return(result_vals: list[int], expected: list[dict]) -> tuple[bool, str]:
    """Bitwise-compare invoke results to the expected typed values. Returns (ok, detail)."""
    if len(result_vals) != len(expected):
        return False, f"arity mismatch: got {len(result_vals)} result(s), expected {len(expected)}"
    for i, (got, exp) in enumerate(zip(result_vals, expected)):
        want = decode_operand(exp)
        if got != want:
            w = WIDTH[exp["type"]]
            return False, (f"result[{i}] {exp['type']}: got {got} (0x{got:0{w//4}x}) "
                           f"!= expected {want} (0x{want:0{w//4}x})")
    return True, "ok"


def trap_matches(trap_kind: str, expected_text: str) -> bool:
    """Match the trap that occurred against assert_trap's expected `text`. Our Trap.kind values
    ARE the canonical spec texts ("integer divide by zero" / "integer overflow"), so exact
    equality is the correct, strict check (trap-KIND matching, not mere trap presence)."""
    return trap_kind == expected_text
