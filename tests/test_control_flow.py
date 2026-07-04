#!/usr/bin/env python3
"""test_control_flow.py — unit tests for the M2 structured-control-flow evaluator (M2.2 gate).

The AUTHORITATIVE oracle for M2 is the 51 frozen assert_return over labels/switch
(scripts/run_m2.py). These hand-built cases localize a control-flow bug fast and pin the named
invariants the plan-auditor called out as distinct checks:
  - block result value carried out by `br` (value transfer)             — separate from —
  - `br l` targeting the correct label depth (0/1/2)                     — branch depth
  - loop re-entry via `br_if` (loop header target, not block end)
  - `if` branch selection, incl. a then-ONLY `if` and a then/else `if` in one function
  - `br_table` in-range, default fallthrough, AND the zero-target `br_table … 0` form
  - `return` escaping nested blocks; `drop`; `nop`; result arity 0 vs 1.

Stdlib only (unittest), pure Python (no WABT). Reproduce:  python3 tests/test_control_flow.py
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import machine as M, values as V           # noqa: E402
from interp import decoder as dec                        # noqa: E402


# --- flat-instruction builders (block/loop/if/else/end are flat tokens; the machine re-nests) ---
def BLOCK(results=()): return dec.Instr("block", bt=list(results))
def LOOP(results=()):  return dec.Instr("loop", bt=list(results))
def IF(results=()):    return dec.Instr("if", bt=list(results))
ELSE = dec.Instr("else")
END = dec.Instr("end")
def BR(l):        return dec.Instr("br", l)
def BR_IF(l):     return dec.Instr("br_if", l)
def BR_TABLE(ts, d): return dec.Instr("br_table", targets=list(ts), default=d)
def GET(i):       return dec.Instr("local.get", i)
def SET(i):       return dec.Instr("local.set", i)
def C32(v):       return dec.Instr("i32.const", v)
def C64(v):       return dec.Instr("i64.const", v)
def OP(name):     return dec.Instr(name)
DROP = dec.Instr("drop")
NOP = dec.Instr("nop")
RET = dec.Instr("return")


def run(params, results, body, args=(), locals_=()):
    m = dec.Module()
    m.types = [dec.FuncType(list(params), list(results))]
    m.func_typeidx = [0]
    m.funcs = [dec.Func(typeidx=0, local_types=list(locals_), body=list(body))]
    m.exports = {"f": 0}
    return M.invoke(m, "f", list(args))


class BlockAndBr(unittest.TestCase):
    def test_block_result_value_carried_by_br(self):
        # block (result i32) { i32.const 7; br 0; i32.const 999 }  -> br carries 7, skips 999
        body = [BLOCK(["i32"]), C32(7), BR(0), C32(999), END, END]
        self.assertEqual(run([], ["i32"], body), [7])

    def test_block_falls_through_without_br(self):
        body = [BLOCK(["i32"]), C32(5), END, END]
        self.assertEqual(run([], ["i32"], body), [5])

    def test_br_value_transfer_across_depth1(self):
        # value transfer proven SEPARATELY from depth: br 1 carries 42 out of the inner block to
        # the depth-1 block's result, skipping the trailing const.
        body = [BLOCK(["i32"]), BLOCK([]), C32(42), BR(1), END, C32(0), END, END]
        self.assertEqual(run([], ["i32"], body), [42])


class BranchDepth(unittest.TestCase):
    # Three nested empty blocks; br D accumulates into local 0 so each depth yields a distinct value:
    #   D=0 -> exit inner, run +2 then +4 = 6 ; D=1 -> skip +2, run +4 = 4 ; D=2 -> skip both = 0.
    def _depth_body(self, d):
        return [BLOCK([]), BLOCK([]), BLOCK([]), BR(d), END,
                GET(0), C32(2), OP("i32.add"), SET(0), END,
                GET(0), C32(4), OP("i32.add"), SET(0), END,
                GET(0), END]

    def test_br_depth0(self):
        self.assertEqual(run([], ["i32"], self._depth_body(0), locals_=["i32"]), [6])

    def test_br_depth1(self):
        self.assertEqual(run([], ["i32"], self._depth_body(1), locals_=["i32"]), [4])

    def test_br_depth2(self):
        self.assertEqual(run([], ["i32"], self._depth_body(2), locals_=["i32"]), [0])


class IfSelection(unittest.TestCase):
    def test_if_true_false(self):
        body = [GET(0), IF(["i32"]), C32(10), ELSE, C32(20), END, END]
        self.assertEqual(run(["i32"], ["i32"], body, [1]), [10])
        self.assertEqual(run(["i32"], ["i32"], body, [0]), [20])

    def test_then_only_and_then_else_in_one_function(self):
        # then-ONLY if (empty result, no else) followed by a then/else if — both in one function.
        body = [GET(0), IF([]), NOP, END,
                GET(0), IF(["i32"]), C32(5), ELSE, C32(6), END, END]
        self.assertEqual(run(["i32"], ["i32"], body, [1]), [5])
        self.assertEqual(run(["i32"], ["i32"], body, [0]), [6])

    def test_if_without_else_empty_result_false_is_noop(self):
        # if (no else): on false do nothing; local stays 0. On true set it to 9.
        body = [GET(0), IF([]), C32(9), SET(1), END, GET(1), END]
        self.assertEqual(run(["i32"], ["i32"], body, [0], locals_=["i32"]), [0])
        self.assertEqual(run(["i32"], ["i32"], body, [1], locals_=["i32"]), [9])


class Loop(unittest.TestCase):
    def test_loop_br_if_countdown_sum(self):
        # acc(local1) += counter(local0); counter -= 1; br_if 0 while counter != 0.
        # n=5 -> 5+4+3+2+1 = 15 (proves loop re-entry to the HEADER, not the block end).
        body = [LOOP([]),
                GET(1), GET(0), OP("i32.add"), SET(1),
                GET(0), C32(1), OP("i32.sub"), SET(0),
                GET(0), BR_IF(0),
                END,
                GET(1), END]
        self.assertEqual(run(["i32"], ["i32"], body, [5], locals_=["i32"]), [15])
        self.assertEqual(run(["i32"], ["i32"], body, [1], locals_=["i32"]), [1])


class BrTable(unittest.TestCase):
    # br_table [0,1] default 2 over three nested blocks: idx 0->100, idx 1->200, else->300.
    def _switch_body(self):
        return [BLOCK([]), BLOCK([]), BLOCK([]),
                GET(0), BR_TABLE([0, 1], 2), END,
                C32(100), RET, END,
                C32(200), RET, END,
                C32(300), END]

    def test_br_table_in_range(self):
        self.assertEqual(run(["i32"], ["i32"], self._switch_body(), [0]), [100])
        self.assertEqual(run(["i32"], ["i32"], self._switch_body(), [1]), [200])

    def test_br_table_default_fallthrough(self):
        self.assertEqual(run(["i32"], ["i32"], self._switch_body(), [2]), [300])
        self.assertEqual(run(["i32"], ["i32"], self._switch_body(), [99]), [300])

    def test_br_table_zero_target_form(self):
        # `br_table` with an EMPTY target vector + default 0 (as in switch.wast): the popped index
        # is irrelevant, control always takes the default (br 0 -> exit the enclosing block).
        body = [BLOCK([]), C32(5), BR_TABLE([], 0), C32(1), END, C32(77), END]
        self.assertEqual(run([], ["i32"], body), [77])


class ReturnDropNopArity(unittest.TestCase):
    def test_return_escapes_nested_blocks(self):
        body = [BLOCK([]), BLOCK([]), C32(88), RET, END, C32(1), END, C32(2), END]
        self.assertEqual(run([], ["i32"], body), [88])

    def test_drop(self):
        self.assertEqual(run([], ["i32"], [C32(1), C32(2), DROP, END]), [1])

    def test_nop(self):
        self.assertEqual(run([], ["i32"], [NOP, C32(5), NOP, END]), [5])

    def test_arity_zero_function(self):
        self.assertEqual(run([], [], [NOP, END]), [])

    def test_block_arity_zero_side_effect_via_local(self):
        body = [BLOCK([]), C32(9), SET(0), END, GET(0), END]
        self.assertEqual(run([], ["i32"], body, locals_=["i32"]), [9])

    def test_i64_block_result(self):
        # switch.wast exercises an i64 block result; confirm the 64-bit path works.
        body = [BLOCK(["i64"]), C64(0x1_0000_0000), END, END]
        self.assertEqual(run([], ["i64"], body), [0x1_0000_0000])


if __name__ == "__main__":
    unittest.main(verbosity=2)
