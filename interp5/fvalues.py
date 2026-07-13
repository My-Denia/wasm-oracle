"""fvalues.py — exact IEEE-754 f32/f64 semantics over BIT PATTERNS (stdlib only).

All functions take and return unsigned ints holding the raw IEEE bits (f32: 32, f64: 64).
Python floats are IEEE binary64 with round-to-nearest-even on x86-64 (SSE), so:

- f64 add/sub/mul/div/sqrt map directly onto Python float ops (divide-by-zero and NaN handled
  explicitly — Python raises where IEEE returns inf/NaN).
- f32 add/sub/mul/div/sqrt are computed in binary64 and demoted with ONE further rounding.
  This is correctly rounded by the standard double-rounding bound (Figueroa): rounding an
  exact result to precision p2 then p1 equals direct rounding to p1 when p2 >= 2*p1 + 2;
  here 53 >= 2*24 + 2. Subnormal f32 results only lower the effective p1, keeping the bound.
- int→f32 conversions get a hand-rolled round-half-even (a 64-bit int can be inexact in
  binary64, so float(v)-then-demote would double-round — the one case the bound does NOT
  cover, since the first rounding then happens at p2=53 < 2*24+2 from 64 significant bits).
- abs/neg/copysign and the reinterpret casts are pure bit ops (NaN payloads preserved
  exactly, as the oracle's exact-bit assertions for them require).
- Every ARITHMETIC op that produces NaN returns the CANONICAL NaN (positive quiet NaN, zero
  payload). The oracle accepts that for both nan:canonical and nan:arithmetic expectations
  (canonical IS arithmetic), matching the deterministic-profile reference interpreter.

Trap conditions (float→int truncation) are signalled with FTrap carrying the spec-canonical
text; the machine converts FTrap into its own Trap type.
"""
from __future__ import annotations

import math
import struct

F32_SIGN = 0x8000_0000
F32_ABS = 0x7FFF_FFFF
F32_EXP = 0x7F80_0000
F32_QUIET = 0x0040_0000
F32_CANON_NAN = 0x7FC0_0000
F64_SIGN = 0x8000_0000_0000_0000
F64_ABS = 0x7FFF_FFFF_FFFF_FFFF
F64_EXP = 0x7FF0_0000_0000_0000
F64_QUIET = 0x0008_0000_0000_0000
F64_CANON_NAN = 0x7FF8_0000_0000_0000

INVALID_CONVERSION = "invalid conversion to integer"
INTEGER_OVERFLOW = "integer overflow"


class FTrap(Exception):
    """A trapping float→int conversion; `kind` is the spec-canonical trap text."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


# ---- bit <-> float ------------------------------------------------------------------------

def b2f32(bits: int) -> float:
    return struct.unpack("<f", (bits & 0xFFFFFFFF).to_bytes(4, "little"))[0]


def b2f64(bits: int) -> float:
    return struct.unpack("<d", (bits & F64_ABS | (bits & F64_SIGN)).to_bytes(8, "little"))[0]


def f2b64(x: float) -> int:
    """Python float → f64 bits. NaN results of arithmetic are canonicalized by callers'
    NaN checks before reaching here, but canonicalize defensively anyway."""
    if math.isnan(x):
        return F64_CANON_NAN
    return int.from_bytes(struct.pack("<d", x), "little")


def f2b32(x: float) -> int:
    """Python float (binary64) → f32 bits with ONE round-to-nearest-even (C double→float cast
    via struct). struct raises OverflowError only when the correctly-rounded result is
    infinite, so that branch returns the signed infinity."""
    if math.isnan(x):
        return F32_CANON_NAN
    try:
        return int.from_bytes(struct.pack("<f", x), "little")
    except OverflowError:
        return 0xFF80_0000 if x < 0 else 0x7F80_0000  # rounds to +-inf


def is_nan32(bits: int) -> bool:
    return (bits & F32_ABS) > F32_EXP


def is_nan64(bits: int) -> bool:
    return (bits & F64_ABS) > F64_EXP


def is_canonical_nan32(bits: int) -> bool:
    return (bits & F32_ABS) == F32_CANON_NAN


def is_canonical_nan64(bits: int) -> bool:
    return (bits & F64_ABS) == F64_CANON_NAN


def is_arithmetic_nan32(bits: int) -> bool:
    return (bits & F32_CANON_NAN) == F32_CANON_NAN


def is_arithmetic_nan64(bits: int) -> bool:
    return (bits & F64_CANON_NAN) == F64_CANON_NAN


# ---- f32/f64 binary arithmetic ------------------------------------------------------------

def _div(a: float, b: float) -> float:
    """IEEE division on Python floats (Python raises ZeroDivisionError where IEEE defines
    inf/NaN). NaN operands are handled by the callers before this."""
    if b == 0.0:
        if a == 0.0:
            return math.nan                                   # 0/0 -> NaN
        sign = (math.copysign(1.0, a) * math.copysign(1.0, b)) < 0
        return -math.inf if sign else math.inf                # x/0 -> +-inf
    return a / b


def _binop_f(op: str, a: float, b: float) -> float:
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        # inf * 0 -> NaN comes out of Python naturally? Python: inf*0.0 -> nan. Yes.
        return a * b
    if op == "div":
        return _div(a, b)
    raise AssertionError(op)


def f32_binop(op: str, ab: int, bb: int) -> int:
    a, b = b2f32(ab), b2f32(bb)
    if math.isnan(a) or math.isnan(b):
        return F32_CANON_NAN
    return f2b32(_binop_f(op, a, b))


def f64_binop(op: str, ab: int, bb: int) -> int:
    a, b = b2f64(ab), b2f64(bb)
    if math.isnan(a) or math.isnan(b):
        return F64_CANON_NAN
    return f2b64(_binop_f(op, a, b))


# ---- min / max (spec: NaN-propagating as canonical; min(-0,+0) = -0, max(-0,+0) = +0) ------

def f32_min(ab: int, bb: int) -> int:
    a, b = b2f32(ab), b2f32(bb)
    if math.isnan(a) or math.isnan(b):
        return F32_CANON_NAN
    if a == 0.0 and b == 0.0:
        return F32_SIGN if (ab | bb) & F32_SIGN else 0     # -0 wins if present
    return ab if a < b else bb


def f32_max(ab: int, bb: int) -> int:
    a, b = b2f32(ab), b2f32(bb)
    if math.isnan(a) or math.isnan(b):
        return F32_CANON_NAN
    if a == 0.0 and b == 0.0:
        return F32_SIGN if (ab & bb) & F32_SIGN else 0     # +0 wins if present
    return ab if a > b else bb


def f64_min(ab: int, bb: int) -> int:
    a, b = b2f64(ab), b2f64(bb)
    if math.isnan(a) or math.isnan(b):
        return F64_CANON_NAN
    if a == 0.0 and b == 0.0:
        return F64_SIGN if (ab | bb) & F64_SIGN else 0
    return ab if a < b else bb


def f64_max(ab: int, bb: int) -> int:
    a, b = b2f64(ab), b2f64(bb)
    if math.isnan(a) or math.isnan(b):
        return F64_CANON_NAN
    if a == 0.0 and b == 0.0:
        return F64_SIGN if (ab & bb) & F64_SIGN else 0
    return ab if a > b else bb


# ---- unary ops -----------------------------------------------------------------------------

def f32_abs(bits: int) -> int:
    return bits & F32_ABS                                   # pure bit op: NaN payload preserved


def f32_neg(bits: int) -> int:
    return bits ^ F32_SIGN


def f32_copysign(ab: int, bb: int) -> int:
    return (ab & F32_ABS) | (bb & F32_SIGN)


def f64_abs(bits: int) -> int:
    return bits & F64_ABS


def f64_neg(bits: int) -> int:
    return bits ^ F64_SIGN


def f64_copysign(ab: int, bb: int) -> int:
    return (ab & F64_ABS) | (bb & F64_SIGN)


def f32_sqrt(bits: int) -> int:
    x = b2f32(bits)
    if math.isnan(x):
        return F32_CANON_NAN
    if x < 0.0:
        return F32_CANON_NAN                                # sqrt(negative) -> NaN
    return f2b32(math.sqrt(x))                              # sqrt(-0.0) = -0.0 (math.sqrt keeps it)


def f64_sqrt(bits: int) -> int:
    x = b2f64(bits)
    if math.isnan(x):
        return F64_CANON_NAN
    if x < 0.0:
        return F64_CANON_NAN
    return f2b64(math.sqrt(x))


def _round_int(op: str, x: float) -> float:
    """ceil/floor/trunc/nearest on a finite float, as a float with the SIGN OF ZERO taken from
    the argument (IEEE roundToIntegral*: the result inherits the sign, so e.g. ceil(-0.4) = -0,
    nearest(-0.4) = -0). `round` is Python's banker's rounding == roundTiesToEven."""
    if op == "ceil":
        r = float(math.ceil(x))
    elif op == "floor":
        r = float(math.floor(x))
    elif op == "trunc":
        r = float(math.trunc(x))
    else:  # nearest
        r = float(round(x))
    return math.copysign(0.0, x) if r == 0.0 else r


def f32_round(op: str, bits: int) -> int:
    x = b2f32(bits)
    if math.isnan(x):
        return F32_CANON_NAN
    if math.isinf(x) or x == 0.0:
        return bits
    return f2b32(_round_int(op, x))                         # integral f32 range: demote exact


def f64_round(op: str, bits: int) -> int:
    x = b2f64(bits)
    if math.isnan(x):
        return F64_CANON_NAN
    if math.isinf(x) or x == 0.0:
        return bits
    return f2b64(_round_int(op, x))


# ---- comparisons (result: i32 0/1; any NaN -> false, except ne -> true) --------------------

_CMP = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "lt": lambda a, b: a < b, "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b, "ge": lambda a, b: a >= b,
}


def f32_cmp(op: str, ab: int, bb: int) -> int:
    return 1 if _CMP[op](b2f32(ab), b2f32(bb)) else 0       # Python float compare is IEEE


def f64_cmp(op: str, ab: int, bb: int) -> int:
    return 1 if _CMP[op](b2f64(ab), b2f64(bb)) else 0


# ---- promote / demote ----------------------------------------------------------------------

def f64_promote_f32(bits: int) -> int:
    if is_nan32(bits):
        return F64_CANON_NAN
    return f2b64(b2f32(bits))                               # exact (binary32 subset of binary64)


def f32_demote_f64(bits: int) -> int:
    if is_nan64(bits):
        return F32_CANON_NAN
    return f2b32(b2f64(bits))                               # one correct rounding


# ---- int -> float conversions --------------------------------------------------------------

def _int_to_f32_bits(v: int) -> int:
    """Correctly-rounded (round-half-even) signed-int -> binary32, done in integer arithmetic:
    float(v) would first round to binary64 (inexact for |v| >= 2^53) and demoting would round
    AGAIN — 64 source bits exceed the safe double-rounding bound, so this path is exact."""
    if v == 0:
        return 0
    sign = F32_SIGN if v < 0 else 0
    a = -v if v < 0 else v
    n = a.bit_length()
    if n <= 24:
        return sign | f2b32(float(a)) & F32_ABS             # exact, no rounding at all
    shift = n - 24
    keep = a >> shift
    rem = a & ((1 << shift) - 1)
    half = 1 << (shift - 1)
    if rem > half or (rem == half and keep & 1):
        keep += 1
        if keep == 1 << 24:                                 # mantissa overflow: renormalize
            keep >>= 1
            shift += 1
    # keep (<= 2^24) and 2.0**shift are both exact in binary64; product <= 2^64 << f32 max
    return sign | (f2b32(float(keep) * 2.0 ** shift) & F32_ABS)


def f32_convert_i32_s(v: int) -> int:
    v = v & 0xFFFFFFFF
    return _int_to_f32_bits(v - 0x1_0000_0000 if v & 0x8000_0000 else v)


def f32_convert_i32_u(v: int) -> int:
    return _int_to_f32_bits(v & 0xFFFFFFFF)


def f32_convert_i64_s(v: int) -> int:
    v = v & 0xFFFF_FFFF_FFFF_FFFF
    return _int_to_f32_bits(v - 0x1_0000_0000_0000_0000 if v & F64_SIGN else v)


def f32_convert_i64_u(v: int) -> int:
    return _int_to_f32_bits(v & 0xFFFF_FFFF_FFFF_FFFF)


def f64_convert_i32_s(v: int) -> int:
    v = v & 0xFFFFFFFF
    return f2b64(float(v - 0x1_0000_0000 if v & 0x8000_0000 else v))   # <= 32 bits: exact

def f64_convert_i32_u(v: int) -> int:
    return f2b64(float(v & 0xFFFFFFFF))


def f64_convert_i64_s(v: int) -> int:
    v = v & 0xFFFF_FFFF_FFFF_FFFF
    return f2b64(float(v - 0x1_0000_0000_0000_0000 if v & F64_SIGN else v))  # CPython rounds half-even


def f64_convert_i64_u(v: int) -> int:
    return f2b64(float(v & 0xFFFF_FFFF_FFFF_FFFF))


# ---- float -> int truncations (trapping and saturating) ------------------------------------

_TRUNC_BOUNDS = {
    ("i32", True): (-(1 << 31), (1 << 31) - 1),
    ("i32", False): (0, (1 << 32) - 1),
    ("i64", True): (-(1 << 63), (1 << 63) - 1),
    ("i64", False): (0, (1 << 64) - 1),
}


def _trunc(target: str, signed: bool, x: float, sat: bool) -> int:
    lo, hi = _TRUNC_BOUNDS[(target, signed)]
    mask = 0xFFFFFFFF if target == "i32" else 0xFFFF_FFFF_FFFF_FFFF
    if math.isnan(x):
        if sat:
            return 0
        raise FTrap(INVALID_CONVERSION)
    if math.isinf(x):
        if sat:
            return (hi if x > 0 else lo) & mask
        raise FTrap(INTEGER_OVERFLOW)
    t = math.trunc(x)                                       # exact: Python ints are unbounded
    if t < lo or t > hi:
        if sat:
            return (hi if t > hi else lo) & mask
        raise FTrap(INTEGER_OVERFLOW)
    return t & mask


def trunc_f32(target: str, signed: bool, bits: int, sat: bool = False) -> int:
    return _trunc(target, signed, b2f32(bits), sat)


def trunc_f64(target: str, signed: bool, bits: int, sat: bool = False) -> int:
    return _trunc(target, signed, b2f64(bits), sat)
