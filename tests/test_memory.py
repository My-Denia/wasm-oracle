#!/usr/bin/env python3
"""test_memory.py — unit tests for the M3 linear-memory model (M3.2 gate).

The AUTHORITATIVE oracle for M3 is the 45 frozen assert_return over store/memory_size
(scripts/run_m3.py). These hand-built cases localize a memory bug fast and pin the named invariants
the plan-auditor called out, INCLUDING two paths the oracle targets do NOT exercise:
  - i32.store little-endian byte layout (at address 0 and at a static offset)
  - the memarg ALIGN is a hint, not a bound — a "misaligned" store must NOT trap on alignment
  - out-of-bounds store traps exactly "out of bounds memory access" (NO in-scope target hits this;
    the trap string is the spec-canonical constant from the upstream oracle, memory_trap.wast:23)
  - memory.size initial; memory.grow success (returns prev, grows) and failure past the declared
    max (returns -1, memory unchanged)
  - PER-INSTANCE fresh memory — a FIRST-RUN path: growing one instance must not leak into another
    (run_file re-instantiates a fresh Memory at every `module` command; memory_size has 4 modules)
  - regression: M1 integer traps and M2 branch/label evaluation are unchanged.

Stdlib only (unittest), pure Python (no WABT). Reproduce:  python3 tests/test_memory.py
"""
from __future__ import annotations
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import machine as M, values as V           # noqa: E402
from interp import decoder as dec                        # noqa: E402

PAGE = M.PAGE_SIZE                                        # 65536


# --- flat-instruction builders ---
def C32(v):       return dec.Instr("i32.const", v)
def GET(i):       return dec.Instr("local.get", i)
def SET(i):       return dec.Instr("local.set", i)
def STORE(offset=0, align=2): return dec.Instr("i32.store", align=align, offset=offset)
def OP(name):     return dec.Instr(name)
def BLOCK(results=()): return dec.Instr("block", bt=list(results))
def BR(l):        return dec.Instr("br", l)
SIZE = dec.Instr("memory.size")
GROW = dec.Instr("memory.grow")
DROP = dec.Instr("drop")
END = dec.Instr("end")


def run_mem(body, params=(), results=(), args=(), locals_=(), mem=None):
    """Build a single-function module (optionally with a memory), instantiate it (allocating the
    linear memory from `mem`=(min,max) or leaving it None), invoke "f", and return (result, module)
    so tests can white-box the resulting module.mem.data."""
    m = dec.Module()
    m.types = [dec.FuncType(list(params), list(results))]
    m.func_typeidx = [0]
    m.funcs = [dec.Func(typeidx=0, local_types=list(locals_), body=list(body))]
    m.exports = {"f": 0}
    if mem is not None:
        m.mems = [mem]
    inst = M.instantiate(m)                               # allocates inst.mem from m.mems (or None)
    result = M.invoke(inst, "f", list(args))
    return result, inst


class Store(unittest.TestCase):
    def test_i32_store_little_endian_at_zero(self):
        # store 0x04030201 at address 0 -> bytes 01 02 03 04 (little-endian)
        _, m = run_mem([C32(0), C32(0x04030201), STORE(), END], mem=(1, None))
        self.assertEqual(bytes(m.mem.data[0:4]), b"\x01\x02\x03\x04")

    def test_i32_store_at_static_offset(self):
        # memarg offset 8 + base 0 -> effective address 8
        _, m = run_mem([C32(0), C32(0xAABBCCDD), STORE(offset=8), END], mem=(1, None))
        self.assertEqual(bytes(m.mem.data[8:12]), b"\xdd\xcc\xbb\xaa")
        self.assertEqual(bytes(m.mem.data[0:8]), b"\x00" * 8)   # nothing written before the offset

    def test_i32_store_at_dynamic_base_plus_offset(self):
        # base (param) 4 + offset 4 -> address 8
        _, m = run_mem([GET(0), C32(0x11223344), STORE(offset=4), END],
                       params=["i32"], args=[4], mem=(1, None))
        self.assertEqual(bytes(m.mem.data[8:12]), b"\x44\x33\x22\x11")

    def test_misaligned_store_does_not_trap_on_alignment(self):
        # align=2 (natural 4-byte) but address 1 is misaligned; WASM memory is unaligned-accessible,
        # so this must NOT trap — align is only a hint.
        _, m = run_mem([C32(1), C32(0x01020304), STORE(align=2), END], mem=(1, None))
        self.assertEqual(bytes(m.mem.data[1:5]), b"\x04\x03\x02\x01")


class StoreBounds(unittest.TestCase):
    def test_out_of_bounds_store_traps(self):
        # memory 1 = 65536 bytes; storing at 65534 needs [65534,65538) -> out of bounds.
        with self.assertRaises(M.Trap) as cm:
            run_mem([C32(PAGE - 2), C32(1), STORE(), END], mem=(1, None))
        self.assertEqual(cm.exception.kind, "out of bounds memory access")

    def test_store_at_last_in_bounds_address_ok(self):
        # last aligned in-bounds 4-byte slot: [65532, 65536) is exactly in bounds.
        _, m = run_mem([C32(PAGE - 4), C32(0x0A0B0C0D), STORE(), END], mem=(1, None))
        self.assertEqual(bytes(m.mem.data[PAGE - 4:PAGE]), b"\x0d\x0c\x0b\x0a")

    def test_offset_pushes_access_out_of_bounds(self):
        # base 0 is fine, but a large static offset pushes the access past the end -> trap.
        with self.assertRaises(M.Trap) as cm:
            run_mem([C32(0), C32(1), STORE(offset=PAGE), END], mem=(1, None))
        self.assertEqual(cm.exception.kind, "out of bounds memory access")

    def test_oob_trap_text_is_spec_canonical_constant(self):
        # provenance guard: the string is the upstream reference-interpreter constant, not invented.
        self.assertEqual(M.OOB, "out of bounds memory access")


class Size(unittest.TestCase):
    def test_size_of_one_page(self):
        res, _ = run_mem([SIZE, END], results=["i32"], mem=(1, None))
        self.assertEqual(res, [1])

    def test_size_of_zero_pages(self):
        res, _ = run_mem([SIZE, END], results=["i32"], mem=(0, None))
        self.assertEqual(res, [0])

    def test_size_of_three_pages(self):
        res, _ = run_mem([SIZE, END], results=["i32"], mem=(3, 8))
        self.assertEqual(res, [3])


class Grow(unittest.TestCase):
    def test_grow_success_returns_previous_and_grows(self):
        # grow 2 pages on a 1-page memory: returns prev (1), memory now 3 pages.
        res, m = run_mem([C32(2), GROW, END], results=["i32"], mem=(1, None))
        self.assertEqual(res, [1])
        self.assertEqual(m.mem.pages, 3)
        self.assertEqual(len(m.mem.data), 3 * PAGE)

    def test_grow_zero_returns_current_size(self):
        res, m = run_mem([C32(0), GROW, END], results=["i32"], mem=(2, None))
        self.assertEqual(res, [2])
        self.assertEqual(m.mem.pages, 2)

    def test_grow_past_declared_max_returns_minus_one_unchanged(self):
        # memory (0 2): grow 3 exceeds max 2 -> returns -1 (0xFFFFFFFF), memory unchanged at 0.
        res, m = run_mem([C32(3), GROW, END], results=["i32"], mem=(0, 2))
        self.assertEqual(res, [0xFFFFFFFF])
        self.assertEqual(m.mem.pages, 0)

    def test_grow_to_exact_max_succeeds_then_beyond_fails(self):
        # memory (3 8): grow 5 -> 8 (prev 3), then grow 1 -> -1 (would be 9 > 8), size stays 8.
        res, m = run_mem([C32(5), GROW, DROP, C32(1), GROW, END], results=["i32"], mem=(3, 8))
        self.assertEqual(res, [0xFFFFFFFF])
        self.assertEqual(m.mem.pages, 8)

    def test_grow_with_no_max_uses_engine_cap(self):
        # no declared max: growth within the memory32 engine cap (65536 pages) succeeds.
        res, m = run_mem([C32(4), GROW, END], results=["i32"], mem=(1, None))
        self.assertEqual(res, [1])
        self.assertEqual(m.mem.pages, 5)

    def test_grow_host_allocation_failure_returns_minus_one_unchanged(self):
        # A min-only memory admits deltas up to 65536 pages (4 GiB) — cur+delta <= engine cap passes
        # the limit check, so a genuine host OOM must be caught and reported as -1 with the memory
        # UNCHANGED (spec: memory.grow never traps), rather than killing the runner. Simulate the
        # host refusing the allocation instead of actually trying to materialize 4 GiB.
        mem = M.Memory(1, None)

        class _OOMBytearray(bytearray):
            def extend(self, _):
                raise MemoryError("simulated host OOM")

        mem.data = _OOMBytearray(mem.data)
        before = len(mem.data)
        self.assertEqual(mem.grow(1), -1)            # allocation failure -> -1, not a crash
        self.assertEqual(len(mem.data), before)      # memory unchanged


class StoreThenObserveViaSize(unittest.TestCase):
    def test_store_persists_in_instance_memory(self):
        # white-box: after a store the byte is in the instance's memory (loads are deferred, so we
        # observe the bytearray directly rather than via i32.load).
        _, m = run_mem([C32(16), C32(0xDEADBEEF), STORE(), END], mem=(1, None))
        self.assertEqual(int.from_bytes(m.mem.data[16:20], "little"), 0xDEADBEEF)


class PerInstanceFreshMemory(unittest.TestCase):
    def test_grow_on_one_instance_does_not_leak_into_another(self):
        # FIRST-RUN path: memory_size.wast has 4 modules; run_file re-instantiates fresh memory at
        # each `module` command. Growing instance A must not affect a freshly instantiated B.
        _, a = run_mem([C32(2), GROW, END], results=["i32"], mem=(1, None))
        self.assertEqual(a.mem.pages, 3)
        _, b = run_mem([SIZE, END], results=["i32"], mem=(1, None))
        self.assertEqual(b.mem.pages, 1)                 # B starts at its OWN declared min
        self.assertIsNot(a.mem.data, b.mem.data)          # not a shared bytearray

    def test_two_instances_have_independent_stores(self):
        _, a = run_mem([C32(0), C32(0x11111111), STORE(), END], mem=(1, None))
        _, b = run_mem([C32(0), C32(0x22222222), STORE(), END], mem=(1, None))
        self.assertEqual(bytes(a.mem.data[0:4]), b"\x11\x11\x11\x11")
        self.assertEqual(bytes(b.mem.data[0:4]), b"\x22\x22\x22\x22")


class DecoderFailClosed(unittest.TestCase):
    """The deferred memory constructs must stay OUT of the decoder (fail-closed): loads, the wider/
    narrow stores, the Data section, and post-MVP memory-limits flags."""
    def test_load_opcodes_absent_from_table(self):
        for b in range(0x28, 0x36):                       # i32.load .. i64.load32_u (0x28-0x35)
            self.assertNotIn(b, dec.OPCODES, f"load opcode 0x{b:02x} should be deferred")

    def test_other_store_opcodes_absent_from_table(self):
        for b in range(0x37, 0x3F):                       # i64.store .. i64.store32 (0x37-0x3E)
            self.assertNotIn(b, dec.OPCODES, f"store opcode 0x{b:02x} should be deferred")

    def test_only_the_three_memory_opcodes_present(self):
        self.assertEqual(dec.OPCODES[0x36][0], "i32.store")
        self.assertEqual(dec.OPCODES[0x3F][0], "memory.size")
        self.assertEqual(dec.OPCODES[0x40][0], "memory.grow")

    def test_memory_section_in_scope_data_section_deferred(self):
        self.assertIn(5, dec.SECTION_NAMES)              # Memory
        self.assertNotIn(11, dec.SECTION_NAMES)          # Data — deferred (fail-closed)

    def test_limits_flags_mvp_only(self):
        self.assertEqual(dec._decode_limits(dec._Reader(bytes([0x00, 0x01]))), (1, None))
        self.assertEqual(dec._decode_limits(dec._Reader(bytes([0x01, 0x02, 0x05]))), (2, 5))
        for bad in (0x03, 0x04, 0x05, 0x02, 0x07):       # shared / memory64 / reserved
            with self.assertRaises(dec.Unsupported):
                dec._decode_limits(dec._Reader(bytes([bad, 0x01, 0x01])))

    def test_nonzero_memidx_rejected(self):
        # memory.size/grow reserved memidx byte must be 0x00; a nonzero byte is multi-memory -> reject.
        # body: memory.grow with memidx 0x01  (0x40 0x01) then end (0x0b)
        with self.assertRaises(dec.Unsupported):
            dec._decode_instrs(dec._Reader(bytes([0x40, 0x01, 0x0B])), 3)


class Regression(unittest.TestCase):
    """M3 must not disturb M1 integer traps or M2 control flow."""
    def test_m1_div_by_zero_still_traps(self):
        with self.assertRaises(M.Trap) as cm:
            run_mem([C32(1), C32(0), OP("i32.div_s"), END], results=["i32"], mem=None)
        self.assertEqual(cm.exception.kind, M.DIV_ZERO)

    def test_m1_int_min_div_minus_one_overflow(self):
        with self.assertRaises(M.Trap) as cm:
            run_mem([C32(0x80000000), C32(0xFFFFFFFF), OP("i32.div_s"), END],
                    results=["i32"], mem=None)
        self.assertEqual(cm.exception.kind, M.OVERFLOW)

    def test_m2_block_br_value_transfer_unchanged(self):
        res, _ = run_mem([BLOCK(["i32"]), C32(7), BR(0), C32(999), END, END],
                         results=["i32"], mem=None)
        self.assertEqual(res, [7])

    def test_memory_ops_coexist_with_control_flow(self):
        # store inside a block (as store.wast does), then the block falls through.
        _, m = run_mem([BLOCK([]), C32(0), C32(0x2A), STORE(), END, END], mem=(1, None))
        self.assertEqual(bytes(m.mem.data[0:4]), b"\x2a\x00\x00\x00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
