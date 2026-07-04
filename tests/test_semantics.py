#!/usr/bin/env python3
"""test_semantics.py — unit tests for the M1 interpreter's exact integer semantics (M1.3 gate).

A developer safety net for the tricky, spec-defined edge cases (wrapping, masked shifts,
arithmetic vs logical shr, rotate, clz/ctz/popcnt, rem_s sign, div/rem traps, wrap/extend).
The AUTHORITATIVE oracle is the 877 frozen assert_return/assert_trap (scripts/run_m1.py); these
hand-written expectations are spec constants used to localize a semantic bug fast.

Stdlib only (unittest). Reproduce:  python3 tests/test_semantics.py
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import machine as M, values as V           # noqa: E402
from interp import decoder as dec                        # noqa: E402

U32 = V.MASK32
U64 = V.MASK64
I32_MIN = 0x80000000            # -2147483648 as unsigned
I64_MIN = 0x8000000000000000


def _mod(params, results, instrs, locals_=None):
    m = dec.Module()
    m.types = [dec.FuncType(list(params), list(results))]
    m.func_typeidx = [0]
    body = [dec.Instr(*x) if isinstance(x, tuple) else dec.Instr(x) for x in instrs]
    m.funcs = [dec.Func(typeidx=0, local_types=list(locals_ or []), body=body)]
    m.exports = {"f": 0}
    return m


def _invoke(params, results, instrs, args, locals_=None):
    return M.invoke(_mod(params, results, instrs, locals_), "f", list(args))


class Binops(unittest.TestCase):
    def test_wrapping_add_sub_mul(self):
        self.assertEqual(M._binop(32, "add", U32, 1), 0)
        self.assertEqual(M._binop(32, "sub", 0, 1), U32)
        self.assertEqual(M._binop(32, "mul", 0x10000, 0x10000), 0)           # 2**32 wraps to 0
        self.assertEqual(M._binop(64, "add", U64, 1), 0)

    def test_div_s_trunc_toward_zero(self):
        self.assertEqual(V.to_signed(32, M._binop(32, "div_s", V.to_unsigned(32, -7), 2)), -3)
        self.assertEqual(V.to_signed(32, M._binop(32, "div_s", 7, V.to_unsigned(32, -2))), -3)
        self.assertEqual(V.to_signed(32, M._binop(32, "div_s", V.to_unsigned(32, -7),
                                                  V.to_unsigned(32, -2))), 3)

    def test_div_traps(self):
        for bits, mn in ((32, I32_MIN), (64, I64_MIN)):
            with self.assertRaises(M.Trap) as c:
                M._binop(bits, "div_s", mn, V.mask(bits))  # INT_MIN / -1
            self.assertEqual(c.exception.kind, M.OVERFLOW)
            for op in ("div_s", "div_u", "rem_s", "rem_u"):
                with self.assertRaises(M.Trap) as c:
                    M._binop(bits, op, 1, 0)
                self.assertEqual(c.exception.kind, M.DIV_ZERO)

    def test_rem_s_sign_follows_dividend_no_overflow_trap(self):
        self.assertEqual(V.to_signed(32, M._binop(32, "rem_s", V.to_unsigned(32, -7), 3)), -1)
        self.assertEqual(V.to_signed(32, M._binop(32, "rem_s", 7, V.to_unsigned(32, -3))), 1)
        # INT_MIN % -1 == 0 and must NOT trap
        self.assertEqual(M._binop(32, "rem_s", I32_MIN, V.mask(32)), 0)
        self.assertEqual(M._binop(64, "rem_s", I64_MIN, V.mask(64)), 0)

    def test_rem_u(self):
        self.assertEqual(M._binop(32, "rem_u", 7, 3), 1)
        self.assertEqual(M._binop(32, "rem_u", U32, 2), 1)

    def test_shifts_masked_count(self):
        self.assertEqual(M._binop(32, "shl", 1, 32), 1)           # 32 % 32 == 0
        self.assertEqual(M._binop(32, "shl", 1, 33), 2)
        self.assertEqual(M._binop(64, "shl", 1, 64), 1)
        self.assertEqual(M._binop(32, "shr_u", I32_MIN, 31), 1)
        self.assertEqual(M._binop(32, "shr_s", I32_MIN, 31), U32)  # arithmetic: -2**31 >> 31 == -1
        self.assertEqual(M._binop(32, "shr_s", I32_MIN, 32), I32_MIN)  # count masked to 0

    def test_rotate(self):
        self.assertEqual(M._binop(32, "rotl", 1, 1), 2)
        self.assertEqual(M._binop(32, "rotl", I32_MIN, 1), 1)
        self.assertEqual(M._binop(32, "rotr", 1, 1), I32_MIN)
        self.assertEqual(M._binop(32, "rotl", 0xDEADBEEF, 0), 0xDEADBEEF)
        self.assertEqual(M._binop(32, "rotl", 0xDEADBEEF, 32), 0xDEADBEEF)

    def test_bitwise(self):
        self.assertEqual(M._binop(32, "and", 0xF0, 0x3C), 0x30)
        self.assertEqual(M._binop(32, "or", 0xF0, 0x0F), 0xFF)
        self.assertEqual(M._binop(32, "xor", 0xFF, 0x0F), 0xF0)


class Unops(unittest.TestCase):
    def test_clz(self):
        self.assertEqual(M._unop(32, "clz", 1), 31)
        self.assertEqual(M._unop(32, "clz", 0), 32)
        self.assertEqual(M._unop(32, "clz", I32_MIN), 0)
        self.assertEqual(M._unop(64, "clz", 0), 64)

    def test_ctz(self):
        self.assertEqual(M._unop(32, "ctz", 0), 32)
        self.assertEqual(M._unop(32, "ctz", 1), 0)
        self.assertEqual(M._unop(32, "ctz", I32_MIN), 31)

    def test_popcnt(self):
        self.assertEqual(M._unop(32, "popcnt", U32), 32)
        self.assertEqual(M._unop(32, "popcnt", 0), 0)
        self.assertEqual(M._unop(64, "popcnt", U64), 64)


class Compare(unittest.TestCase):
    def test_signed_vs_unsigned(self):
        neg1 = U32
        self.assertEqual(M._compare(32, "lt_s", neg1, 0), 1)     # -1 < 0 signed
        self.assertEqual(M._compare(32, "lt_u", neg1, 0), 0)     # 0xFFFFFFFF > 0 unsigned
        self.assertEqual(M._compare(32, "gt_s", neg1, 0), 0)
        self.assertEqual(M._compare(32, "eq", 5, 5), 1)
        self.assertEqual(M._compare(32, "ne", 5, 6), 1)
        self.assertEqual(M._compare(32, "ge_s", 0, neg1), 1)
        self.assertEqual(M._compare(32, "le_u", 0, neg1), 1)


class Convert(unittest.TestCase):
    def test_wrap_and_extend(self):
        self.assertEqual(M._convert("i32.wrap_i64", (1 << 32) | 1), 1)
        self.assertEqual(M._convert("i64.extend_i32_s", U32), U64)          # -1 -> -1
        self.assertEqual(M._convert("i64.extend_i32_u", U32), U32)          # -1 -> 0xFFFFFFFF
        self.assertEqual(M._convert("i32.extend8_s", 0xFF), U32)            # -1
        self.assertEqual(M._convert("i32.extend16_s", 0x8000), 0xFFFF8000)
        self.assertEqual(M._convert("i64.extend32_s", I32_MIN), 0xFFFFFFFF80000000)
        self.assertEqual(M._convert("i32.extend8_s", 0x7F), 0x7F)           # positive unchanged


class EndToEnd(unittest.TestCase):
    def test_add_via_locals(self):
        r = _invoke(["i32", "i32"], ["i32"],
                    [("local.get", 0), ("local.get", 1), "i32.add", "end"], [7, 35])
        self.assertEqual(r, [42])

    def test_return_short_circuits(self):
        r = _invoke([], ["i32"],
                    [("i32.const", 99), "return", ("i32.const", 1), "i32.add", "end"], [])
        self.assertEqual(r, [99])

    def test_eqz_and_const_wrap(self):
        self.assertEqual(_invoke([], ["i32"], [("i32.const", 0), "i32.eqz", "end"], []), [1])
        self.assertEqual(_invoke([], ["i32"], [("i32.const", 5), "i32.eqz", "end"], []), [0])
        # i32.const stores the 32-bit pattern of a negative literal
        self.assertEqual(_invoke([], ["i32"], [("i32.const", -1), "end"], []), [U32])

    def test_declared_locals_zero_initialized(self):
        # one param + one declared local; return the (untouched) local => 0
        r = _invoke(["i32"], ["i32"], [("local.get", 1), "end"], [7], locals_=["i32"])
        self.assertEqual(r, [0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
