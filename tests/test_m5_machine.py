#!/usr/bin/env python3
"""Unit tests for interp5.machine — executor behaviors the oracle run will lean on.

Modules are hand-assembled as raw wasm bytes (tiny builder below), decoded with
interp5.decoder, and executed — so these tests exercise the decode→instantiate→invoke chain
end to end without any toolchain dependency.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from interp5 import decoder as dec  # noqa: E402
from interp5 import machine as M    # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


# ---- minimal wasm builder --------------------------------------------------------------------

def uleb(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def sleb(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if (v == 0 and not b & 0x40) or (v == -1 and b & 0x40):
            out.append(b)
            return bytes(out)
        out.append(b | 0x80)


def vec(items: list[bytes]) -> bytes:
    return uleb(len(items)) + b"".join(items)


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + uleb(len(payload)) + payload


def functype(params: list[int], results: list[int]) -> bytes:
    return b"\x60" + vec([bytes([p]) for p in params]) + vec([bytes([r]) for r in results])


def name(s: str) -> bytes:
    raw = s.encode("utf-8")
    return uleb(len(raw)) + raw


def code_entry(local_groups: list[tuple[int, int]], body: bytes) -> bytes:
    decls = vec([uleb(n) + bytes([t]) for n, t in local_groups])
    payload = decls + body
    return uleb(len(payload)) + payload


def module(*sections: bytes) -> bytes:
    return b"\x00asm\x01\x00\x00\x00" + b"".join(sections)


I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C


def inst_of(raw: bytes, store: M.Store | None = None) -> M.Instance:
    return M.instantiate(dec.decode(raw), store)


def test_multivalue() -> None:
    print("[multi-value]")
    # type0: () -> (i32, i32); type1: (i32, i32) -> (i32); block uses type1 (params!)
    raw = module(
        section(1, vec([functype([], [I32, I32]), functype([I32, I32], [I32])])),
        section(3, vec([uleb(0)])),
        section(7, vec([name("two") + b"\x00" + uleb(0)])),
        section(10, vec([code_entry([], bytes([
            0x41, 3,            # i32.const 3
            0x41, 4,            # i32.const 4
            0x02, 0x01,         # block (type 1: [i32,i32] -> [i32])
            0x6A,               #   i32.add
            0x0B,               # end
            0x41, 5,            # i32.const 5
            0x0B]))])),
    )
    inst = inst_of(raw)
    check(M.invoke(inst, "two", []) == [7, 5], "block with params + multi-result function")

    # loop (param i32) (result i32):
    #   dup via local.tee; if v < 5, br 0 with v on stack (br arity 1 = loop params)
    raw = module(
        section(1, vec([functype([], [I32]), functype([I32], [I32])])),
        section(3, vec([uleb(0)])),
        section(7, vec([name("count") + b"\x00" + uleb(0)])),
        section(10, vec([code_entry([(1, I32)], bytes([
            0x41, 0,                  # seed 0
            0x03, 0x01,               # loop (param i32) (result i32)
            0x41, 1, 0x6A,            #   v = param + 1
            0x22, 0x00,               #   local.tee 0     (keep v on stack)
            0x41, 5, 0x48,            #   v < 5 ?
            0x04, 0x40,               #   if (empty)
            0x20, 0x00,               #     local.get 0
            0x0C, 0x01,               #     br 1  -> the LOOP, carrying 1 value (its param)
            0x0B,                     #   end if
            0x20, 0x00,               #   local.get 0 (loop result)
            0x0B,                     # end loop
            0x0B]))])),
    )
    inst = inst_of(raw)
    check(M.invoke(inst, "count", []) == [5], "loop param carried by br (counts to 5)")


def test_calls_and_tables() -> None:
    print("[call / call_indirect]")
    # two funcs: f0 = const 11 (type0 ()->i32), f1(x)=x+1 (type1 (i32)->i32)
    # table [f0, None, f1]; dispatcher(i, x) -> call_indirect type1
    raw = module(
        section(1, vec([functype([], [I32]), functype([I32], [I32]),
                        functype([I32, I32], [I32])])),
        section(3, vec([uleb(0), uleb(1), uleb(2)])),
        section(4, vec([b"\x70\x00" + uleb(4)])),
        section(7, vec([name("disp") + b"\x00" + uleb(2), name("f0") + b"\x00" + uleb(0)])),
        section(9, vec([uleb(0) + bytes([0x41, 0, 0x0B]) + vec([uleb(0)]),
                        uleb(0) + bytes([0x41, 2, 0x0B]) + vec([uleb(1)])])),
        section(10, vec([
            code_entry([], bytes([0x41, 11, 0x0B])),
            code_entry([], bytes([0x20, 0x00, 0x41, 1, 0x6A, 0x0B])),
            code_entry([], bytes([0x20, 0x01, 0x20, 0x00, 0x11, 0x01, 0x00, 0x0B])),
        ])),
    )
    inst = inst_of(raw)
    check(M.invoke(inst, "disp", [2, 41]) == [42], "call_indirect dispatches f1(41)=42")

    def trap_kind(*args):
        try:
            M.invoke(inst, "disp", list(args))
            return None
        except M.Trap as t:
            return t.kind
    check(trap_kind(9, 0) == M.UNDEFINED_ELEMENT, "call_indirect OOB -> undefined element")
    check(trap_kind(1, 0) == M.UNINIT_ELEMENT, "null entry -> uninitialized element")
    check(trap_kind(0, 0) == M.INDIRECT_MISMATCH, "wrong type -> indirect call type mismatch")


def test_memory() -> None:
    print("[lazy memory]")
    mem = M.Memory(0, None)
    check(mem.grow(65536) == 0 and mem.n_pages == 65536 and len(mem.pages) == 0,
          "grow to 4 GiB is O(1), no page materialized")
    check(mem.read(0xFFFF_FFFC, 4) == b"\x00\x00\x00\x00", "read of untouched page is zeros")
    mem.write(0xFFFF - 2, b"\xAA\xBB\xCC\xDD")          # crosses page 0 -> page 1
    check(mem.read(0xFFFF - 2, 4) == b"\xAA\xBB\xCC\xDD", "cross-page write/read roundtrip")
    check(mem.grow(1) == -1, "grow past 4 GiB cap -> -1")
    try:
        mem.read(0x1_0000_0000 - 3, 4)
        check(False, "OOB read traps")
    except M.Trap as t:
        check(t.kind == M.OOB_MEM, "OOB read traps 'out of bounds memory access'")

    # data segment init + loads through the machine
    raw = module(
        section(1, vec([functype([I32], [I32])])),
        section(3, vec([uleb(0)])),
        section(5, vec([b"\x00" + uleb(1)])),
        section(7, vec([name("ld8") + b"\x00" + uleb(0)])),
        section(10, vec([code_entry([], bytes([
            0x20, 0x00, 0x2D, 0x00, 0x00, 0x0B]))])),   # i32.load8_u align=0 offset=0
        section(11, vec([uleb(0) + bytes([0x41, 16, 0x0B]) + uleb(3) + b"\x07\x80\xFF"])),
    )
    inst = inst_of(raw)
    check(M.invoke(inst, "ld8", [17]) == [0x80], "active data segment initialized memory")
    check(M.invoke(inst, "ld8", [15]) == [0], "byte before segment is zero")

    # data segment OOB -> instantiation trap (uninstantiable)
    raw_bad = module(
        section(5, vec([b"\x00" + uleb(1)])),
        section(11, vec([uleb(0) + bytes([0x41] + list(sleb(65536 - 2))) + b"\x0B"
                         + uleb(3) + b"\x01\x02\x03"])),
    )
    try:
        inst_of(raw_bad)
        check(False, "OOB data segment traps at instantiation")
    except M.Trap as t:
        check(t.kind == M.OOB_MEM, "OOB data segment -> out of bounds memory access")


def test_exhaustion_and_start() -> None:
    print("[exhaustion / start / globals]")
    # runaway: func 0 calls itself
    raw = module(
        section(1, vec([functype([], [])])),
        section(3, vec([uleb(0)])),
        section(7, vec([name("run") + b"\x00" + uleb(0)])),
        section(10, vec([code_entry([], bytes([0x10, 0x00, 0x0B]))])),
    )
    inst = inst_of(raw)
    try:
        M.invoke(inst, "run", [])
        check(False, "runaway recursion exhausts")
    except M.Trap as t:
        check(t.kind == M.EXHAUSTED, "runaway recursion -> call stack exhausted")

    # start section runs at instantiation (sets a global read back via export)
    raw = module(
        section(1, vec([functype([], [])])),
        section(3, vec([uleb(0)])),
        section(6, vec([bytes([I32, 0x01]) + bytes([0x41, 0, 0x0B])])),
        section(7, vec([name("g") + b"\x03" + uleb(0)])),
        section(8, uleb(0)),
        section(10, vec([code_entry([], bytes([0x41, 42, 0x24, 0x00, 0x0B]))])),
    )
    inst = inst_of(raw)
    check(M.read_global(inst, "g") == 42, "start function ran (global.set observed)")

    # trapping start -> uninstantiable
    raw = module(
        section(1, vec([functype([], [])])),
        section(3, vec([uleb(0)])),
        section(8, uleb(0)),
        section(10, vec([code_entry([], bytes([0x00, 0x0B]))])),
    )
    try:
        inst_of(raw)
        check(False, "trapping start traps")
    except M.Trap as t:
        check(t.kind == M.UNREACHABLE, "trapping start -> unreachable at instantiation")


def test_linking() -> None:
    print("[spectest / register]")
    store = M.Store()
    # module A: imports spectest.print_i32, exports memory + a func writing to it
    raw_a = module(
        section(1, vec([functype([I32], []), functype([], [])])),
        section(2, vec([name("spectest") + name("print_i32") + b"\x00" + uleb(0)])),
        section(3, vec([uleb(1)])),
        section(5, vec([b"\x00" + uleb(1)])),
        section(7, vec([name("mem") + b"\x02" + uleb(0), name("go") + b"\x00" + uleb(1)])),
        section(10, vec([code_entry([], bytes([
            0x41, 8, 0x41, 0x7F, 0x36, 0x02, 0x00,      # i32.store (align=2, offset=0) mem[8]=-1
            0x41, 7, 0x10, 0x00,                        # print_i32(7)
            0x0B]))])),
    )
    inst_a = inst_of(raw_a, store)
    check(M.invoke(inst_a, "go", []) == [], "spectest.print_i32 import callable (no-op)")
    store.registered["A"] = inst_a
    # module B: imports A.mem, reads what A wrote
    raw_b = module(
        section(1, vec([functype([], [I32])])),
        section(2, vec([name("A") + name("mem") + b"\x02\x00" + uleb(1)])),
        section(3, vec([uleb(0)])),
        section(7, vec([name("peek") + b"\x00" + uleb(0)])),
        section(10, vec([code_entry([], bytes([
            0x41, 8, 0x28, 0x02, 0x00, 0x0B]))])),      # i32.load mem[8]
    )
    inst_b = inst_of(raw_b, store)
    check(M.invoke(inst_b, "peek", []) == [0xFFFFFFFF],
          "registered-instance memory shared (B sees A's store)")


def test_float_through_machine() -> None:
    print("[float ops through machine]")
    f32b = lambda x: int.from_bytes(struct.pack("<f", x), "little")
    raw = module(
        section(1, vec([functype([F32, F32], [F32])])),
        section(3, vec([uleb(0)])),
        section(7, vec([name("mul") + b"\x00" + uleb(0)])),
        section(10, vec([code_entry([], bytes([0x20, 0x00, 0x20, 0x01, 0x94, 0x0B]))])),
    )
    inst = inst_of(raw)
    check(M.invoke(inst, "mul", [f32b(3.0), f32b(0.5)]) == [f32b(1.5)], "f32.mul exact bits")
    check(M.invoke(inst, "mul", [0x7FC12345, f32b(1.0)]) == [0x7FC00000],
          "f32.mul NaN operand -> canonical NaN bits")


def test_body_termination() -> None:
    """Review fix: a code body whose declared size runs out before a function-level `end`
    is malformed ("END opcode expected") — the decoder must reject it, never return an
    unterminated instruction list."""
    print("[body termination]")
    from interp5 import validator as VAL

    def mod_with_body(body: bytes) -> bytes:
        return module(
            section(1, vec([functype([], [])])),
            section(3, vec([uleb(0)])),
            section(10, vec([code_entry([], body)])),
        )

    def expect_malformed(raw: bytes, label: str) -> None:
        try:
            dec.decode(raw)
        except dec.DecodeError as e:
            check(str(e) == dec.END_EXPECTED, f"{label}: text {str(e)!r}")
            return
        check(False, f"{label}: decoder accepted unterminated body")

    expect_malformed(mod_with_body(bytes([0x01])), "bare nop, no end")
    expect_malformed(mod_with_body(b""), "empty body, no end")
    expect_malformed(mod_with_body(bytes([0x02, 0x40, 0x0B])),
                     "block closed but function-level end missing")
    check(dec.decode(mod_with_body(bytes([0x0B]))) is not None, "plain `end` body decodes")
    check(dec.decode(mod_with_body(bytes([0x02, 0x40, 0x0B, 0x0B]))) is not None,
          "block + function end decodes")
    # Frozen boundary: instructions AFTER the function-level end still decode; the
    # VALIDATOR rejects that shape (type mismatch), exactly as before this fix.
    trailing = dec.decode(mod_with_body(bytes([0x0B, 0x01])))
    check(trailing is not None, "trailing instr after final end still decodes")
    try:
        VAL.validate_module(trailing)
        check(False, "validator accepted instr after final end")
    except VAL.ValidationError as e:
        check(str(e) == "type mismatch", "validator still rejects trailing instr")


def test_import_type_matching() -> None:
    """Review fix: a resolved import whose actual external type does not match the declared
    import type must raise IncompatibleImport (spec "incompatible import type")."""
    print("[import type matching]")
    store = M.Store()
    # provider: func "f" () -> i32, memory "m" {min 1, no max}
    raw_a = module(
        section(1, vec([functype([], [I32])])),
        section(3, vec([uleb(0)])),
        section(5, vec([b"\x00" + uleb(1)])),
        section(7, vec([name("f") + b"\x00" + uleb(0), name("m") + b"\x02" + uleb(0)])),
        section(10, vec([code_entry([], bytes([0x41, 42, 0x0B]))])),
    )
    inst_a = inst_of(raw_a, store)
    store.registered["A"] = inst_a

    def importer(*imports: bytes, ntypes: list[bytes] | None = None) -> bytes:
        return module(
            section(1, vec(ntypes if ntypes is not None else [functype([], [I32])])),
            section(2, vec(list(imports))),
        )

    def expect_incompatible(raw: bytes, label: str) -> None:
        try:
            inst_of(raw, store)
        except M.IncompatibleImport as e:
            check(str(e).startswith("incompatible import type"), f"{label}: text {str(e)!r}")
            return
        except M.LinkError as e:
            check(False, f"{label}: plain LinkError (would be UNSUPPORTED): {e}")
            return
        check(False, f"{label}: mismatched import accepted")

    # func: exact signature match required
    check(inst_of(importer(name("A") + name("f") + b"\x00" + uleb(0)), store) is not None,
          "matching func import links")
    expect_incompatible(
        importer(name("A") + name("f") + b"\x00" + uleb(0),
                 ntypes=[functype([I32], [I32])]),
        "func signature mismatch")
    # memory limits: actual {1,None} vs declared
    check(inst_of(importer(name("A") + name("m") + b"\x02\x00" + uleb(1)), store) is not None,
          "memory {min 1} import links")
    expect_incompatible(importer(name("A") + name("m") + b"\x02\x00" + uleb(2)),
                        "memory min too large")
    expect_incompatible(importer(name("A") + name("m") + b"\x02\x01" + uleb(1) + uleb(5)),
                        "declared max but actual has none")
    # grown memory matches by CURRENT size (reference-interpreter behavior)
    inst_a.mems[0].grow(2)
    check(inst_of(importer(name("A") + name("m") + b"\x02\x00" + uleb(3)), store) is not None,
          "grown memory matches by current size")
    # spectest globals are immutable i32/i64/f32/f64
    check(inst_of(importer(name("spectest") + name("global_i32") + b"\x03\x7F\x00"), store)
          is not None, "spectest.global_i32 (i32, const) links")
    expect_incompatible(importer(name("spectest") + name("global_i32") + b"\x03\x7F\x01"),
                        "global mutability mismatch")
    expect_incompatible(importer(name("spectest") + name("global_i32") + b"\x03\x7E\x00"),
                        "global valtype mismatch")
    # spectest memory is {1,2}: max must be covered when declared
    check(inst_of(importer(name("spectest") + name("memory") + b"\x02\x01" + uleb(1) + uleb(2)),
                  store) is not None, "spectest.memory {1,2} links")
    expect_incompatible(
        importer(name("spectest") + name("memory") + b"\x02\x01" + uleb(1) + uleb(1)),
        "memory max exceeds declared max")


def main() -> int:
    test_multivalue()
    test_calls_and_tables()
    test_memory()
    test_exhaustion_and_start()
    test_linking()
    test_float_through_machine()
    test_body_termination()
    test_import_type_matching()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nall machine unit tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
