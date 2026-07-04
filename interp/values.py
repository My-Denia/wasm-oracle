"""values.py — fixed-width (32/64-bit) integer math for the M1 integer core.

WASM i32/i64 values are held as UNSIGNED canonical Python ints in [0, 2**bits). Every
operation that produces a value masks its result back into that range, so the stack is always
canonical for the value's width. Signedness is a per-operation interpretation, applied via
to_signed() where the opcode is a signed one (div_s, lt_s, shr_s, ...).
"""
from __future__ import annotations

MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1


def mask(bits: int) -> int:
    return (1 << bits) - 1


def to_unsigned(bits: int, v: int) -> int:
    """Canonical unsigned representative of v's low `bits` bits."""
    return v & mask(bits)


def to_signed(bits: int, v: int) -> int:
    """Interpret the low `bits` bits of v as a two's-complement signed integer."""
    v &= mask(bits)
    return v - (1 << bits) if (v >> (bits - 1)) & 1 else v


def clz(bits: int, v: int) -> int:
    """Count leading zero bits of the `bits`-wide value v (clz(0) == bits)."""
    v &= mask(bits)
    return bits if v == 0 else bits - v.bit_length()


def ctz(bits: int, v: int) -> int:
    """Count trailing zero bits of the `bits`-wide value v (ctz(0) == bits)."""
    v &= mask(bits)
    return bits if v == 0 else (v & -v).bit_length() - 1


def popcnt(bits: int, v: int) -> int:
    return bin(v & mask(bits)).count("1")


def trunc_div(a: int, b: int) -> int:
    """Integer division truncated TOWARD ZERO (C / WASM semantics), not Python's floor."""
    q = a // b
    if (a % b != 0) and ((a < 0) != (b < 0)):
        q += 1
    return q
