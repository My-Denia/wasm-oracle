#!/usr/bin/env python3
"""Unit tests for interp5.fvalues — the float core M5 rests on.

The int→f32 conversion is checked against an INDEPENDENT reference implementation built on
fractions.Fraction (exact rational round-half-even), not against the code under test, over
boundary vectors plus a seeded random sweep. NaN classes, sign-of-zero, min/max, rounding
ops, demote overflow, and trunc/trunc_sat bounds all get explicit edge vectors.
"""
from __future__ import annotations

import math
import random
import struct
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from interp5 import fvalues as F  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


def f32bits(x: float) -> int:
    return int.from_bytes(struct.pack("<f", x), "little")


def f64bits(x: float) -> int:
    return int.from_bytes(struct.pack("<d", x), "little")


# ---- independent reference: round-half-even int -> binary32 via exact rationals ------------

def ref_int_to_f32(v: int) -> int:
    if v == 0:
        return 0
    sign = 0x8000_0000 if v < 0 else 0
    a = Fraction(abs(v))
    # find e with 2^e <= a < 2^(e+1)
    e = abs(v).bit_length() - 1
    if e < 24:
        return sign | f32bits(float(abs(v)))
    # significand steps of 2^(e-23); round a to nearest multiple, ties to even
    step = Fraction(2) ** (e - 23)
    q, r = divmod(a, step)
    if r * 2 > step or (r * 2 == step and q % 2 == 1):
        q += 1                                # q may become 2^24: q*step == 2^(e+1), still exact
    assert q * step < Fraction(2) ** 128, "would overflow f32 (cannot happen for 64-bit ints)"
    # q <= 2^24 and step is a power of two -> the product is exactly representable in binary64
    fv = float(q) * (2.0 ** (e - 23))
    return sign | f32bits(fv)


def test_int_to_f32() -> None:
    print("[int->f32 correct rounding vs Fraction reference]")
    vectors = [
        1, -1, (1 << 24) - 1, 1 << 24, (1 << 24) + 1, (1 << 25) - 1,
        (1 << 53) + (1 << 29) + 1,             # double-rounding trap: inexact in binary64
        (1 << 53) + (1 << 29),
        0x7FFFFFFFFFFFFFFF, -0x8000000000000000,
        0x00FFFFFF_FFFFFFFF, 0x01000000_00000001,
        0x0020000020000001,                    # classic i64->f32 double-rounding case
        (1 << 63) - 1, (1 << 62) + (1 << 38) + 1,
    ]
    rng = random.Random(20260713)
    vectors += [rng.getrandbits(64) - (1 << 63) for _ in range(20000)]
    bad = 0
    for v in vectors:
        got = F._int_to_f32_bits(v)
        want = ref_int_to_f32(v)
        if got != want:
            bad += 1
            if bad <= 5:
                print(f"    mismatch v={v}: got 0x{got:08x} want 0x{want:08x}")
    check(bad == 0, f"int->f32 matches Fraction reference on {len(vectors)} vectors")


def test_nan_semantics() -> None:
    print("[NaN semantics]")
    payload = 0x7F80_0000 | 0x0012_3456        # signaling-ish payload NaN (quiet bit clear)
    qpayload = 0x7FC1_2345
    check(F.f32_binop("add", payload, f32bits(1.0)) == F.F32_CANON_NAN,
          "f32.add(NaN payload, 1.0) -> canonical NaN")
    check(F.f32_min(qpayload, f32bits(1.0)) == F.F32_CANON_NAN,
          "f32.min(NaN, 1.0) -> canonical NaN")
    check(F.f32_abs(0xFFC1_2345) == 0x7FC1_2345, "f32.abs preserves NaN payload bits")
    check(F.f32_neg(0x7FC1_2345) == 0xFFC1_2345, "f32.neg preserves NaN payload bits")
    check(F.f32_copysign(0x7F81_1111, 0x8000_0000) == 0xFF81_1111,
          "f32.copysign preserves payload, flips sign")
    check(F.is_arithmetic_nan32(F.F32_CANON_NAN) and F.is_canonical_nan32(F.F32_CANON_NAN),
          "canonical NaN is both canonical and arithmetic")
    check(F.is_arithmetic_nan32(0x7FC1_2345) and not F.is_canonical_nan32(0x7FC1_2345),
          "payload quiet NaN is arithmetic, not canonical")
    check(not F.is_arithmetic_nan32(0x7F81_1111), "signaling NaN is not arithmetic")
    check(F.is_canonical_nan32(0xFFC0_0000), "negative canonical NaN accepted (sign ignored)")
    check(F.f64_binop("div", F.F64_CANON_NAN, f64bits(2.0)) == F.F64_CANON_NAN,
          "f64.div(NaN, 2.0) -> canonical NaN")


def test_zero_and_minmax() -> None:
    print("[signed zero + min/max]")
    nz32, pz32 = 0x8000_0000, 0
    check(F.f32_min(pz32, nz32) == nz32, "f32.min(+0,-0) = -0")
    check(F.f32_max(nz32, pz32) == pz32, "f32.max(-0,+0) = +0")
    check(F.f64_min(0, F.F64_SIGN) == F.F64_SIGN, "f64.min(+0,-0) = -0")
    check(F.f64_max(F.F64_SIGN, F.F64_SIGN) == F.F64_SIGN, "f64.max(-0,-0) = -0")
    check(F.f32_binop("add", nz32, nz32) == nz32, "f32.add(-0,-0) = -0")
    check(F.f32_binop("div", f32bits(1.0), nz32) == f32bits(-math.inf),
          "f32.div(1, -0) = -inf")
    check(F.f64_binop("div", f64bits(0.0), f64bits(0.0)) == F.F64_CANON_NAN,
          "f64.div(0,0) = canonical NaN")


def test_rounding_ops() -> None:
    print("[ceil/floor/trunc/nearest]")
    check(F.f32_round("nearest", f32bits(0.5)) == f32bits(0.0), "nearest(0.5) = 0")
    check(F.f32_round("nearest", f32bits(1.5)) == f32bits(2.0), "nearest(1.5) = 2")
    check(F.f32_round("nearest", f32bits(2.5)) == f32bits(2.0), "nearest(2.5) = 2 (even)")
    check(F.f32_round("nearest", f32bits(-0.5)) == 0x8000_0000, "nearest(-0.5) = -0")
    check(F.f32_round("ceil", f32bits(-0.4)) == 0x8000_0000, "ceil(-0.4) = -0")
    check(F.f32_round("floor", f32bits(-0.4)) == f32bits(-1.0), "floor(-0.4) = -1")
    check(F.f32_round("trunc", f32bits(-0.9)) == 0x8000_0000, "trunc(-0.9) = -0")
    check(F.f64_round("nearest", f64bits(4503599627370497.0)) == f64bits(4503599627370497.0),
          "nearest of integral f64 is identity")
    check(F.f32_round("floor", 0x7F80_0000) == 0x7F80_0000, "floor(+inf) = +inf")
    check(F.f32_round("ceil", 0x7FC1_1111) == F.F32_CANON_NAN, "ceil(NaN) -> canonical NaN")


def test_demote_promote() -> None:
    print("[demote/promote]")
    # f32 max = (2 - 2^-23) * 2^127; the round-to-inf threshold is 2^128 - 2^103
    f32_max = float.fromhex("0x1.fffffep+127")
    below = float.fromhex("0x1.fffffe7p+127")   # rounds down to f32max
    above = float.fromhex("0x1.ffffffp+127")    # rounds to inf (exactly at threshold)
    check(F.f32_demote_f64(f64bits(f32_max)) == f32bits(f32_max), "demote(f32max) exact")
    check(F.f32_demote_f64(f64bits(below)) == f32bits(f32_max), "demote(just below threshold) = f32max")
    check(F.f32_demote_f64(f64bits(above)) == 0x7F80_0000, "demote(at threshold) = +inf")
    check(F.f32_demote_f64(f64bits(-1e300)) == 0xFF80_0000, "demote(-1e300) = -inf")
    check(F.f32_demote_f64(f64bits(1e-300)) == 0, "demote(tiny) = +0 (underflow)")
    check(F.f32_demote_f64(F.F64_CANON_NAN | 0x123) == F.F32_CANON_NAN, "demote(NaN) canonical")
    check(F.f64_promote_f32(f32bits(1.5)) == f64bits(1.5), "promote exact")
    check(F.f64_promote_f32(0x7FC1_2345) == F.F64_CANON_NAN, "promote(NaN) canonical")


def test_trunc() -> None:
    print("[trunc / trunc_sat]")
    def trap_kind(fn, *args):
        try:
            fn(*args)
            return None
        except F.FTrap as t:
            return t.kind
    check(F.trunc_f32("i32", True, f32bits(-2147483648.0)) == 0x8000_0000,
          "i32.trunc_f32_s(-2^31) ok")
    check(trap_kind(F.trunc_f32, "i32", True, f32bits(2147483648.0)) == F.INTEGER_OVERFLOW,
          "i32.trunc_f32_s(2^31) traps overflow")
    check(trap_kind(F.trunc_f32, "i32", True, F.F32_CANON_NAN) == F.INVALID_CONVERSION,
          "i32.trunc_f32_s(NaN) traps invalid conversion")
    check(F.trunc_f32("i32", True, f32bits(-1.9)) == 0xFFFF_FFFF, "trunc toward zero: -1.9 -> -1")
    check(F.trunc_f64("i64", False, f64bits(1.9e18)) == 1900000000000000000,
          "i64.trunc_f64_u(1.9e18) exact")
    check(trap_kind(F.trunc_f64, "i64", False, f64bits(-1.0)) == F.INTEGER_OVERFLOW,
          "i64.trunc_f64_u(-1) traps overflow")
    check(F.trunc_f64("i64", False, f64bits(-0.9)) == 0, "i64.trunc_f64_u(-0.9) = 0")
    check(F.trunc_f32("i32", True, F.F32_CANON_NAN, sat=True) == 0, "trunc_sat(NaN) = 0")
    check(F.trunc_f32("i32", True, 0x7F80_0000, sat=True) == 0x7FFF_FFFF,
          "trunc_sat_s(+inf) = INT32_MAX")
    check(F.trunc_f32("i32", False, 0xFF80_0000, sat=True) == 0, "trunc_sat_u(-inf) = 0")
    check(F.trunc_f64("i64", True, f64bits(-9.3e18), sat=True) == 0x8000_0000_0000_0000,
          "trunc_sat_s(-9.3e18) saturates to INT64_MIN")


def test_arith_correct_rounding_spot() -> None:
    print("[f32 arithmetic spot vectors]")
    one_plus = f32bits(float.fromhex("0x1.000002p+0"))     # 1 + 2^-23 (next f32 above 1)
    tiny = f32bits(float.fromhex("0x1p-24"))                # exactly half ulp of 1.0
    check(F.f32_binop("add", f32bits(1.0), tiny) == f32bits(1.0),
          "1.0 + 2^-24 rounds to 1.0 (tie to even)")
    tiny_above = f32bits(float.fromhex("0x1.000002p-24"))
    check(F.f32_binop("add", f32bits(1.0), tiny_above) == one_plus,
          "1.0 + (2^-24 + ulp) rounds up")
    check(F.f32_binop("mul", f32bits(float.fromhex("0x1p-126")), f32bits(0.5)) ==
          f32bits(float.fromhex("0x1p-127")), "f32 subnormal product exact")
    check(F.f32_sqrt(f32bits(2.0)) == f32bits(float.fromhex("0x1.6a09e6p+0")),
          "f32.sqrt(2) correctly rounded")
    check(F.f64_binop("add", f64bits(0.1), f64bits(0.2)) == f64bits(0.1 + 0.2),
          "f64 add == native double")


def main() -> int:
    test_int_to_f32()
    test_nan_semantics()
    test_zero_and_minmax()
    test_rounding_ops()
    test_demote_promote()
    test_trunc()
    test_arith_correct_rounding_spot()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nall float unit tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
