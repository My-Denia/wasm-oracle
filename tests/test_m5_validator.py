#!/usr/bin/env python3
"""Unit tests for interp5.validator — spec-canonical validation over decoded modules.

Modules are hand-assembled as wasm bytes (also exercising the decoder) via tiny section
helpers, then decoded and validated. Covers a positive multi-value/loop-params/unreachable
suite plus negative cases for every corpus assert_invalid text ("type mismatch",
"unknown local/label/function/table/type", "constant expression required", "start function")
and the structural spec texts (alignment, immutable global, duplicate export, limits).
Finally sanity-runs the validator over the real converted_m5 oracle-valid modules.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from interp5 import decoder  # noqa: E402
from interp5.validator import ValidationError, validate_module  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


# ---- minimal wasm assembler -----------------------------------------------------------

I32, I64, F32, F64 = 0x7F, 0x7E, 0x7D, 0x7C


def uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def vec(items: list[bytes]) -> bytes:
    return uleb(len(items)) + b"".join(items)


def sec(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + uleb(len(payload)) + payload


def ftype(params: list[int], results: list[int]) -> bytes:
    return b"\x60" + vec([bytes([p]) for p in params]) + vec([bytes([r]) for r in results])


def typesec(*fts: bytes) -> bytes:
    return sec(1, vec(list(fts)))


def funcsec(*typeidxs: int) -> bytes:
    return sec(3, vec([uleb(i) for i in typeidxs]))


def limits(mn: int, mx: int | None = None) -> bytes:
    return (b"\x00" + uleb(mn)) if mx is None else (b"\x01" + uleb(mn) + uleb(mx))


def tablesec(mn: int, mx: int | None = None) -> bytes:
    return sec(4, vec([b"\x70" + limits(mn, mx)]))


def memsec(mn: int, mx: int | None = None) -> bytes:
    return sec(5, vec([limits(mn, mx)]))


def globalsec(*gs: bytes) -> bytes:                 # each g: valtype byte + mut + init expr
    return sec(6, vec(list(gs)))


def glob(vt: int, mut: int, init: bytes) -> bytes:  # init INCLUDES the trailing 0x0B end
    return bytes([vt, mut]) + init


def exportsec(*exps: tuple[str, int, int]) -> bytes:  # (name, kind, idx)
    return sec(7, vec([vec([bytes([c]) for c in n.encode()]) + bytes([k]) + uleb(i)
                       for (n, k, i) in exps]))


def startsec(funcidx: int) -> bytes:
    return sec(8, uleb(funcidx))


def elemsec(offset: bytes, funcidxs: list[int]) -> bytes:  # offset INCLUDES end
    return sec(9, vec([uleb(0) + offset + vec([uleb(i) for i in funcidxs])]))


def datasec(offset: bytes, data: bytes) -> bytes:
    return sec(11, vec([uleb(0) + offset + uleb(len(data)) + data]))


def codesec(*bodies: bytes) -> bytes:
    return sec(10, vec([uleb(len(b)) + b for b in bodies]))


def body(local_groups: list[tuple[int, int]], code: bytes) -> bytes:
    return vec([uleb(c) + bytes([t]) for (c, t) in local_groups]) + code


def module(*sections: bytes) -> bytes:
    return b"\x00asm\x01\x00\x00\x00" + b"".join(sections)


# ---- check drivers ---------------------------------------------------------------------

def expect_valid(label: str, data: bytes) -> None:
    try:
        validate_module(decoder.decode(data))
        check(True, label)
    except ValidationError as e:
        check(False, f"{label} (unexpected ValidationError: {e})")


def expect_invalid(label: str, data: bytes, text: str) -> None:
    try:
        validate_module(decoder.decode(data))
        check(False, f"{label} (expected {text!r}, validated clean)")
    except ValidationError as e:
        check(str(e) == text, f"{label} -> {text!r}" +
              ("" if str(e) == text else f" (got {str(e)!r})"))


# ---- positive cases --------------------------------------------------------------------

def test_positive() -> None:
    print("[positive]")
    # type 0: () -> i32; type 1: (i32,i32) -> i32 (multi-value blocktype);
    # type 2: (i32) -> i32 (loop with a parameter, branched to)
    types = typesec(ftype([], [I32]), ftype([I32, I32], [I32]), ftype([I32], [I32]))
    mv_block = bytes([0x41, 1, 0x41, 2,          # i32.const 1, i32.const 2
                      0x02, 0x01,                # block (type 1): [i32 i32] -> [i32]
                      0x6A,                      # i32.add
                      0x0B, 0x0B])               # end, end
    expect_valid("multi-value blocktype block",
                 module(types, funcsec(0), codesec(body([], mv_block))))
    loop_params = bytes([0x41, 5,                # i32.const 5
                         0x03, 0x02,             # loop (type 2): [i32] -> [i32]
                         0x41, 0,                # i32.const 0
                         0x0D, 0x00,             # br_if 0 (loop label arity = params [i32])
                         0x0B, 0x0B])
    expect_valid("loop with a parameter + br_if to it",
                 module(types, funcsec(0), codesec(body([], loop_params))))
    expect_valid("unreachable polymorphism (unreachable then i32.add)",
                 module(types, funcsec(0),
                        codesec(body([], bytes([0x00, 0x6A, 0x0B])))))
    expect_valid("select under unreachable",
                 module(types, funcsec(0),
                        codesec(body([], bytes([0x00, 0x1B, 0x0B])))))
    # a fuller module: memory + table + global + elem + data + start + exports
    full = module(
        typesec(ftype([], []), ftype([], [I32])),
        funcsec(0, 1),
        tablesec(1, 1),
        memsec(1, 2),
        globalsec(glob(I32, 1, bytes([0x41, 7, 0x0B]))),
        exportsec(("f", 0, 0), ("g", 3, 0), ("t", 1, 0), ("m", 2, 0)),
        startsec(0),
        elemsec(bytes([0x41, 0, 0x0B]), [0, 1]),
        codesec(
            body([], bytes([0x0B])),
            body([(1, I64)], bytes([
                0x41, 0,                          # i32.const 0
                0x28, 0x02, 0x00,                 # i32.load align=2 offset=0
                0x41, 1, 0x6A,                    # i32.const 1, i32.add
                0x24, 0x00,                       # global.set 0 (mutable)
                0x23, 0x00,                       # global.get 0
                0x0B]))),
        datasec(bytes([0x41, 0, 0x0B]), b"hi"),
    )
    expect_valid("full module (mem/table/global/elem/data/start/exports)", full)


# ---- negative cases: the 8 corpus texts ------------------------------------------------

def test_corpus_texts() -> None:
    print("[the 8 corpus assert_invalid texts]")
    t_i32 = typesec(ftype([], [I32]))
    t_void = typesec(ftype([], []))
    expect_invalid("wrong result type (i64.const for i32 result)",
                   module(t_i32, funcsec(0), codesec(body([], bytes([0x42, 0, 0x0B])))),
                   "type mismatch")
    expect_invalid("local.get out of range",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x20, 0x00, 0x1A, 0x0B])))),
                   "unknown local")
    expect_invalid("br depth out of range",
                   module(t_void, funcsec(0), codesec(body([], bytes([0x0C, 0x01, 0x0B])))),
                   "unknown label")
    expect_invalid("call funcidx out of range",
                   module(t_void, funcsec(0), codesec(body([], bytes([0x10, 0x01, 0x0B])))),
                   "unknown function")
    expect_invalid("start funcidx out of range",
                   module(t_void, funcsec(0), startsec(9), codesec(body([], bytes([0x0B])))),
                   "unknown function")
    expect_invalid("elem funcidx out of range",
                   module(t_void, funcsec(0), tablesec(1),
                          elemsec(bytes([0x41, 0, 0x0B]), [4]),
                          codesec(body([], bytes([0x0B])))),
                   "unknown function")
    expect_invalid("call_indirect with no table",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x41, 0, 0x11, 0x00, 0x00, 0x0B])))),
                   "unknown table")
    expect_invalid("elem segment with no table",
                   module(t_void, funcsec(0), elemsec(bytes([0x41, 0, 0x0B]), [0]),
                          codesec(body([], bytes([0x0B])))),
                   "unknown table")
    expect_invalid("call_indirect typeidx out of range",
                   module(t_void, funcsec(0), tablesec(1),
                          codesec(body([], bytes([0x41, 0, 0x11, 0x05, 0x00, 0x0B])))),
                   "unknown type")
    expect_invalid("function typeidx out of range",
                   module(t_void, funcsec(3), codesec(body([], bytes([0x0B])))),
                   "unknown type")
    expect_invalid("blocktype typeidx out of range",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x02, 0x09, 0x0B, 0x0B])))),
                   "unknown type")
    expect_invalid("multi-instruction global init",
                   module(globalsec(glob(I32, 0, bytes([0x41, 0, 0x41, 1, 0x6A, 0x0B])))),
                   "constant expression required")
    expect_invalid("empty global init",
                   module(globalsec(glob(I32, 0, bytes([0x0B])))),
                   "constant expression required")
    expect_invalid("non-const op in elem offset",
                   module(t_void, funcsec(0), tablesec(1),
                          elemsec(bytes([0x41, 0, 0x45, 0x0B]), [0]),
                          codesec(body([], bytes([0x0B])))),
                   "constant expression required")
    expect_invalid("global init referencing a defined global",
                   module(globalsec(glob(I32, 0, bytes([0x41, 0, 0x0B])),
                                    glob(I32, 0, bytes([0x23, 0x00, 0x0B])))),
                   "constant expression required")
    expect_invalid("start function with a result",
                   module(t_i32, funcsec(0), startsec(0),
                          codesec(body([], bytes([0x41, 0, 0x0B])))),
                   "start function")
    expect_invalid("global init const of wrong type",
                   module(globalsec(glob(I32, 0, bytes([0x42, 0, 0x0B])))),
                   "type mismatch")
    expect_invalid("f64 elem offset expr",
                   module(t_void, funcsec(0), tablesec(1),
                          elemsec(bytes([0x44, 0, 0, 0, 0, 0, 0, 0, 0, 0x0B]), [0]),
                          codesec(body([], bytes([0x0B])))),
                   "type mismatch")


# ---- negative cases: stack/control details ---------------------------------------------

def test_stack_details() -> None:
    print("[stack/control details]")
    t_i32 = typesec(ftype([], [I32]))
    t_void = typesec(ftype([], []))
    # br_table: outer block has arity 1 (i32), inner block arity 0 -> inconsistent
    br_table_bad = bytes([
        0x02, 0x7F,                    # block (result i32)
        0x02, 0x40,                    # block (no result)
        0x41, 0,                       # i32.const 0 (br_table index)
        0x0E, 0x01, 0x00, 0x01,        # br_table [0] default 1 (arities 0 vs 1)
        0x0B, 0x41, 1, 0x0B, 0x0B])
    expect_invalid("br_table targets with inconsistent label arities",
                   module(t_i32, funcsec(0), codesec(body([], br_table_bad))),
                   "type mismatch")
    expect_invalid("if with result but no else",
                   module(t_i32, funcsec(0),
                          codesec(body([], bytes([0x41, 1, 0x04, 0x7F, 0x41, 2,
                                                  0x0B, 0x0B])))),
                   "type mismatch")
    expect_invalid("values left on stack at function end",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x41, 1, 0x0B])))),
                   "type mismatch")
    expect_invalid("stack underflow (i32.add on empty stack)",
                   module(t_i32, funcsec(0),
                          codesec(body([], bytes([0x41, 1, 0x6A, 0x0B])))),
                   "type mismatch")
    expect_invalid("select with mismatched arms",
                   module(t_i32, funcsec(0),
                          codesec(body([], bytes([0x41, 1, 0x42, 2, 0x41, 0,
                                                  0x1B, 0x0B])))),
                   "type mismatch")
    expect_invalid("if condition not i32",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x42, 1, 0x04, 0x40, 0x0B, 0x0B])))),
                   "type mismatch")
    expect_invalid("values left on stack at block end",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x02, 0x40, 0x41, 1, 0x0B, 0x0B])))),
                   "type mismatch")
    expect_invalid("br operand type mismatch",
                   module(t_i32, funcsec(0),
                          codesec(body([], bytes([0x42, 1, 0x0C, 0x00, 0x0B])))),
                   "type mismatch")


# ---- negative cases: structural spec texts ---------------------------------------------

def test_structural() -> None:
    print("[structural spec texts]")
    t_void = typesec(ftype([], []))
    expect_invalid("load alignment over natural",
                   module(t_void, funcsec(0), memsec(1),
                          codesec(body([], bytes([0x41, 0, 0x28, 0x03, 0x00,
                                                  0x1A, 0x0B])))),
                   "alignment must not be larger than natural")
    expect_invalid("store8 alignment over natural",
                   module(t_void, funcsec(0), memsec(1),
                          codesec(body([], bytes([0x41, 0, 0x41, 0, 0x3A, 0x01, 0x00,
                                                  0x0B])))),
                   "alignment must not be larger than natural")
    expect_invalid("global.set of immutable global",
                   module(t_void, funcsec(0),
                          globalsec(glob(I32, 0, bytes([0x41, 0, 0x0B]))),
                          codesec(body([], bytes([0x41, 1, 0x24, 0x00, 0x0B])))),
                   "global is immutable")
    expect_invalid("global.get out of range in body",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x23, 0x00, 0x1A, 0x0B])))),
                   "unknown global")
    expect_invalid("load with no memory",
                   module(t_void, funcsec(0),
                          codesec(body([], bytes([0x41, 0, 0x28, 0x02, 0x00,
                                                  0x1A, 0x0B])))),
                   "unknown memory")
    expect_invalid("data segment with no memory",
                   module(t_void, funcsec(0), codesec(body([], bytes([0x0B]))),
                          datasec(bytes([0x41, 0, 0x0B]), b"x")),
                   "unknown memory")
    expect_invalid("duplicate export name",
                   module(t_void, funcsec(0, 0), exportsec(("f", 0, 0), ("f", 0, 1)),
                          codesec(body([], bytes([0x0B])), body([], bytes([0x0B])))),
                   "duplicate export name")
    expect_invalid("export func idx out of range",
                   module(t_void, funcsec(0), exportsec(("f", 0, 5)),
                          codesec(body([], bytes([0x0B])))),
                   "unknown function")
    expect_invalid("memory min > max",
                   module(memsec(2, 1)),
                   "size minimum must not be greater than maximum")
    expect_invalid("memory min > 65536 pages",
                   module(memsec(65537)),
                   "memory size must be at most 65536 pages")
    expect_invalid("table min > max",
                   module(tablesec(2, 1)),
                   "size minimum must not be greater than maximum")


# ---- oracle corpus sweep ---------------------------------------------------------------

CORPUS = ["block/block.json", "if/if.json", "loop/loop.json", "br/br.json",
          "call/call.json", "f32/f32.json", "load/load.json", "store/store.json",
          "memory_grow/memory_grow.json"]


def test_corpus_modules() -> None:
    print("[oracle-valid corpus modules]")
    root = Path(__file__).resolve().parent.parent / "build" / "converted_m5"
    n = 0
    bad: list[str] = []
    for rel in CORPUS:
        jpath = root / rel
        if not jpath.exists():
            bad.append(f"{rel}: json missing")
            continue
        spec = json.loads(jpath.read_text())
        for cmd in spec["commands"]:
            if cmd["type"] != "module":
                continue
            wasm = (jpath.parent / cmd["filename"]).read_bytes()
            try:
                validate_module(decoder.decode(wasm))
                n += 1
            except ValidationError as e:
                bad.append(f"{rel}/{cmd['filename']}: ValidationError: {e}")
    for b in bad:
        print(f"    {b}")
    check(not bad, f"all {n} oracle-valid corpus modules validate clean")


def main() -> int:
    test_positive()
    test_corpus_texts()
    test_stack_details()
    test_structural()
    test_corpus_modules()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nall validator unit tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
