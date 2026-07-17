"""decoder.py — strict full-surface WASM binary decoder for M5.

Scope = everything the pinned toolchain + frozen guardrail flags can emit for the 55
convertible test/core files: sections {Custom, Type, Import, Function, Table, Memory, Global,
Export, Start, Elem, Code, Data, DataCount(consistency only)}, value types i32/i64/f32/f64,
funcref tables, and the full MVP + sign-extension + saturating-truncation opcode set.

Two failure classes, same discipline as the frozen interp/ decoder:

- DecodeError — the binary is MALFORMED. The message IS the spec-canonical text used by
  assert_malformed matching. The texts this decoder can emit for the enumerated M5 surface:
  "unexpected end"                                        (truncated field/stream)
  "length out of bounds"                                  (declared size overruns the file)
  "malformed section id"                                  (section id > 12)
  "function and code section have inconsistent lengths"
  "data count and data section have inconsistent lengths"
  "malformed UTF-8 encoding"                              (any name that is not valid UTF-8)
  plus structural texts not asserted by the corpus ("magic header not detected",
  "unknown binary version", "section out of order", "section size mismatch",
  "integer representation too long", "integer too large").
- Unsupported — the binary is (or may be) WELL-FORMED but uses surface beyond the frozen
  guardrail boundary (0xFC subopcode >= 8 (bulk-memory), 0xFD (SIMD), reference-type opcodes
  0xD0-0xD2 / typed select 0x1C, externref tables, multi-table, passive/declarative element
  or data segments, shared/64-bit memory limits, unknown opcode bytes). Never a silent skip.

The decoded body stays FLAT (like interp/): the machine re-derives block structure.
Verified against the pinned wasm-objdump by tests/test_m5_decoder_selftest.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

UNEXPECTED_END = "unexpected end"
END_EXPECTED = "END opcode expected"
LENGTH_OOB = "length out of bounds"
MALFORMED_SECTION_ID = "malformed section id"
FUNC_CODE_MISMATCH = "function and code section have inconsistent lengths"
DATACOUNT_MISMATCH = "data count and data section have inconsistent lengths"
MALFORMED_UTF8 = "malformed UTF-8 encoding"


class Unsupported(Exception):
    """Well-formed (or undecidable) construct beyond the enumerated M5 surface."""


class DecodeError(Exception):
    """Malformed binary; str(exc) is the spec-canonical assert_malformed text."""


VALTYPES = {0x7F: "i32", 0x7E: "i64", 0x7D: "f32", 0x7C: "f64"}

# Immediate kinds
IMM_NONE, IMM_U32, IMM_S32, IMM_S64, IMM_F32, IMM_F64 = range(6)
IMM_BLOCKTYPE, IMM_BRTABLE, IMM_MEMARG, IMM_MEMIDX, IMM_CALLIND, IMM_FC = range(6, 12)

OPCODES: dict[int, tuple[str, int]] = {
    0x00: ("unreachable", IMM_NONE), 0x01: ("nop", IMM_NONE),
    0x02: ("block", IMM_BLOCKTYPE), 0x03: ("loop", IMM_BLOCKTYPE), 0x04: ("if", IMM_BLOCKTYPE),
    0x05: ("else", IMM_NONE), 0x0B: ("end", IMM_NONE),
    0x0C: ("br", IMM_U32), 0x0D: ("br_if", IMM_U32), 0x0E: ("br_table", IMM_BRTABLE),
    0x0F: ("return", IMM_NONE), 0x10: ("call", IMM_U32), 0x11: ("call_indirect", IMM_CALLIND),
    0x1A: ("drop", IMM_NONE), 0x1B: ("select", IMM_NONE),
    0x20: ("local.get", IMM_U32), 0x21: ("local.set", IMM_U32), 0x22: ("local.tee", IMM_U32),
    0x23: ("global.get", IMM_U32), 0x24: ("global.set", IMM_U32),
    0x28: ("i32.load", IMM_MEMARG), 0x29: ("i64.load", IMM_MEMARG),
    0x2A: ("f32.load", IMM_MEMARG), 0x2B: ("f64.load", IMM_MEMARG),
    0x2C: ("i32.load8_s", IMM_MEMARG), 0x2D: ("i32.load8_u", IMM_MEMARG),
    0x2E: ("i32.load16_s", IMM_MEMARG), 0x2F: ("i32.load16_u", IMM_MEMARG),
    0x30: ("i64.load8_s", IMM_MEMARG), 0x31: ("i64.load8_u", IMM_MEMARG),
    0x32: ("i64.load16_s", IMM_MEMARG), 0x33: ("i64.load16_u", IMM_MEMARG),
    0x34: ("i64.load32_s", IMM_MEMARG), 0x35: ("i64.load32_u", IMM_MEMARG),
    0x36: ("i32.store", IMM_MEMARG), 0x37: ("i64.store", IMM_MEMARG),
    0x38: ("f32.store", IMM_MEMARG), 0x39: ("f64.store", IMM_MEMARG),
    0x3A: ("i32.store8", IMM_MEMARG), 0x3B: ("i32.store16", IMM_MEMARG),
    0x3C: ("i64.store8", IMM_MEMARG), 0x3D: ("i64.store16", IMM_MEMARG),
    0x3E: ("i64.store32", IMM_MEMARG),
    0x3F: ("memory.size", IMM_MEMIDX), 0x40: ("memory.grow", IMM_MEMIDX),
    0x41: ("i32.const", IMM_S32), 0x42: ("i64.const", IMM_S64),
    0x43: ("f32.const", IMM_F32), 0x44: ("f64.const", IMM_F64),
    0x45: ("i32.eqz", IMM_NONE), 0x46: ("i32.eq", IMM_NONE), 0x47: ("i32.ne", IMM_NONE),
    0x48: ("i32.lt_s", IMM_NONE), 0x49: ("i32.lt_u", IMM_NONE),
    0x4A: ("i32.gt_s", IMM_NONE), 0x4B: ("i32.gt_u", IMM_NONE),
    0x4C: ("i32.le_s", IMM_NONE), 0x4D: ("i32.le_u", IMM_NONE),
    0x4E: ("i32.ge_s", IMM_NONE), 0x4F: ("i32.ge_u", IMM_NONE),
    0x50: ("i64.eqz", IMM_NONE), 0x51: ("i64.eq", IMM_NONE), 0x52: ("i64.ne", IMM_NONE),
    0x53: ("i64.lt_s", IMM_NONE), 0x54: ("i64.lt_u", IMM_NONE),
    0x55: ("i64.gt_s", IMM_NONE), 0x56: ("i64.gt_u", IMM_NONE),
    0x57: ("i64.le_s", IMM_NONE), 0x58: ("i64.le_u", IMM_NONE),
    0x59: ("i64.ge_s", IMM_NONE), 0x5A: ("i64.ge_u", IMM_NONE),
    0x5B: ("f32.eq", IMM_NONE), 0x5C: ("f32.ne", IMM_NONE), 0x5D: ("f32.lt", IMM_NONE),
    0x5E: ("f32.gt", IMM_NONE), 0x5F: ("f32.le", IMM_NONE), 0x60: ("f32.ge", IMM_NONE),
    0x61: ("f64.eq", IMM_NONE), 0x62: ("f64.ne", IMM_NONE), 0x63: ("f64.lt", IMM_NONE),
    0x64: ("f64.gt", IMM_NONE), 0x65: ("f64.le", IMM_NONE), 0x66: ("f64.ge", IMM_NONE),
    0x67: ("i32.clz", IMM_NONE), 0x68: ("i32.ctz", IMM_NONE), 0x69: ("i32.popcnt", IMM_NONE),
    0x6A: ("i32.add", IMM_NONE), 0x6B: ("i32.sub", IMM_NONE), 0x6C: ("i32.mul", IMM_NONE),
    0x6D: ("i32.div_s", IMM_NONE), 0x6E: ("i32.div_u", IMM_NONE),
    0x6F: ("i32.rem_s", IMM_NONE), 0x70: ("i32.rem_u", IMM_NONE),
    0x71: ("i32.and", IMM_NONE), 0x72: ("i32.or", IMM_NONE), 0x73: ("i32.xor", IMM_NONE),
    0x74: ("i32.shl", IMM_NONE), 0x75: ("i32.shr_s", IMM_NONE), 0x76: ("i32.shr_u", IMM_NONE),
    0x77: ("i32.rotl", IMM_NONE), 0x78: ("i32.rotr", IMM_NONE),
    0x79: ("i64.clz", IMM_NONE), 0x7A: ("i64.ctz", IMM_NONE), 0x7B: ("i64.popcnt", IMM_NONE),
    0x7C: ("i64.add", IMM_NONE), 0x7D: ("i64.sub", IMM_NONE), 0x7E: ("i64.mul", IMM_NONE),
    0x7F: ("i64.div_s", IMM_NONE), 0x80: ("i64.div_u", IMM_NONE),
    0x81: ("i64.rem_s", IMM_NONE), 0x82: ("i64.rem_u", IMM_NONE),
    0x83: ("i64.and", IMM_NONE), 0x84: ("i64.or", IMM_NONE), 0x85: ("i64.xor", IMM_NONE),
    0x86: ("i64.shl", IMM_NONE), 0x87: ("i64.shr_s", IMM_NONE), 0x88: ("i64.shr_u", IMM_NONE),
    0x89: ("i64.rotl", IMM_NONE), 0x8A: ("i64.rotr", IMM_NONE),
    0x8B: ("f32.abs", IMM_NONE), 0x8C: ("f32.neg", IMM_NONE), 0x8D: ("f32.ceil", IMM_NONE),
    0x8E: ("f32.floor", IMM_NONE), 0x8F: ("f32.trunc", IMM_NONE),
    0x90: ("f32.nearest", IMM_NONE), 0x91: ("f32.sqrt", IMM_NONE),
    0x92: ("f32.add", IMM_NONE), 0x93: ("f32.sub", IMM_NONE), 0x94: ("f32.mul", IMM_NONE),
    0x95: ("f32.div", IMM_NONE), 0x96: ("f32.min", IMM_NONE), 0x97: ("f32.max", IMM_NONE),
    0x98: ("f32.copysign", IMM_NONE),
    0x99: ("f64.abs", IMM_NONE), 0x9A: ("f64.neg", IMM_NONE), 0x9B: ("f64.ceil", IMM_NONE),
    0x9C: ("f64.floor", IMM_NONE), 0x9D: ("f64.trunc", IMM_NONE),
    0x9E: ("f64.nearest", IMM_NONE), 0x9F: ("f64.sqrt", IMM_NONE),
    0xA0: ("f64.add", IMM_NONE), 0xA1: ("f64.sub", IMM_NONE), 0xA2: ("f64.mul", IMM_NONE),
    0xA3: ("f64.div", IMM_NONE), 0xA4: ("f64.min", IMM_NONE), 0xA5: ("f64.max", IMM_NONE),
    0xA6: ("f64.copysign", IMM_NONE),
    0xA7: ("i32.wrap_i64", IMM_NONE),
    0xA8: ("i32.trunc_f32_s", IMM_NONE), 0xA9: ("i32.trunc_f32_u", IMM_NONE),
    0xAA: ("i32.trunc_f64_s", IMM_NONE), 0xAB: ("i32.trunc_f64_u", IMM_NONE),
    0xAC: ("i64.extend_i32_s", IMM_NONE), 0xAD: ("i64.extend_i32_u", IMM_NONE),
    0xAE: ("i64.trunc_f32_s", IMM_NONE), 0xAF: ("i64.trunc_f32_u", IMM_NONE),
    0xB0: ("i64.trunc_f64_s", IMM_NONE), 0xB1: ("i64.trunc_f64_u", IMM_NONE),
    0xB2: ("f32.convert_i32_s", IMM_NONE), 0xB3: ("f32.convert_i32_u", IMM_NONE),
    0xB4: ("f32.convert_i64_s", IMM_NONE), 0xB5: ("f32.convert_i64_u", IMM_NONE),
    0xB6: ("f32.demote_f64", IMM_NONE),
    0xB7: ("f64.convert_i32_s", IMM_NONE), 0xB8: ("f64.convert_i32_u", IMM_NONE),
    0xB9: ("f64.convert_i64_s", IMM_NONE), 0xBA: ("f64.convert_i64_u", IMM_NONE),
    0xBB: ("f64.promote_f32", IMM_NONE),
    0xBC: ("i32.reinterpret_f32", IMM_NONE), 0xBD: ("i64.reinterpret_f64", IMM_NONE),
    0xBE: ("f32.reinterpret_i32", IMM_NONE), 0xBF: ("f64.reinterpret_i64", IMM_NONE),
    0xC0: ("i32.extend8_s", IMM_NONE), 0xC1: ("i32.extend16_s", IMM_NONE),
    0xC2: ("i64.extend8_s", IMM_NONE), 0xC3: ("i64.extend16_s", IMM_NONE),
    0xC4: ("i64.extend32_s", IMM_NONE),
}

_FC_SUBOPS = {
    0: "i32.trunc_sat_f32_s", 1: "i32.trunc_sat_f32_u",
    2: "i32.trunc_sat_f64_s", 3: "i32.trunc_sat_f64_u",
    4: "i64.trunc_sat_f32_s", 5: "i64.trunc_sat_f32_u",
    6: "i64.trunc_sat_f64_s", 7: "i64.trunc_sat_f64_u",
}

# Known section ids and the REQUIRED relative order of the non-custom ones (DataCount=12 sits
# between Element=9 and Code=10 in the spec ordering).
_SECTION_ORDER = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9, 12: 10, 10: 11, 11: 12}
SECTION_NAMES = {0: "Custom", 1: "Type", 2: "Import", 3: "Function", 4: "Table", 5: "Memory",
                 6: "Global", 7: "Export", 8: "Start", 9: "Elem", 10: "Code", 11: "Data",
                 12: "DataCount"}


@dataclass
class Instr:
    op: str
    imm: int | None = None
    bt: tuple | None = None              # blocktype: ("val", [result types]) | ("type", typeidx)
    targets: list[int] | None = None
    default: int | None = None
    align: int | None = None
    offset: int | None = None


@dataclass
class FuncType:
    params: list[str]
    results: list[str]


@dataclass
class Import:
    module: str
    field: str
    kind: str                             # "func" | "table" | "memory" | "global"
    desc: object                          # func: typeidx; table: (elemtype, limits);
                                          # memory: limits; global: (valtype, mutable)


@dataclass
class Export:
    name: str
    kind: str
    idx: int


@dataclass
class Global:
    valtype: str
    mutable: bool
    init: list[Instr]


@dataclass
class ElemSeg:
    tableidx: int
    offset: list[Instr]
    funcidxs: list[int]


@dataclass
class DataSeg:
    memidx: int
    offset: list[Instr]
    data: bytes


@dataclass
class Func:
    typeidx: int
    local_types: list[str]
    body: list[Instr]


@dataclass
class Module:
    types: list[FuncType] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    func_typeidx: list[int] = field(default_factory=list)      # DEFINED funcs only
    tables: list[tuple[str, tuple[int, int | None]]] = field(default_factory=list)
    mems: list[tuple[int, int | None]] = field(default_factory=list)
    globals: list[Global] = field(default_factory=list)        # DEFINED globals only
    exports: list[Export] = field(default_factory=list)
    start: int | None = None
    elems: list[ElemSeg] = field(default_factory=list)
    funcs: list[Func] = field(default_factory=list)            # DEFINED funcs (Code section)
    datas: list[DataSeg] = field(default_factory=list)
    datacount: int | None = None

    # ---- index spaces (imports first, then definitions) ----
    def imported(self, kind: str) -> list[Import]:
        return [im for im in self.imports if im.kind == kind]

    def n_funcs(self) -> int:
        return len(self.imported("func")) + len(self.funcs)

    def func_type(self, funcidx: int) -> FuncType:
        imps = self.imported("func")
        if funcidx < len(imps):
            return self.types[imps[funcidx].desc]
        return self.types[self.funcs[funcidx - len(imps)].typeidx]

    def n_tables(self) -> int:
        return len(self.imported("table")) + len(self.tables)

    def n_mems(self) -> int:
        return len(self.imported("memory")) + len(self.mems)

    def n_globals(self) -> int:
        return len(self.imported("global")) + len(self.globals)

    def global_type(self, idx: int) -> tuple[str, bool]:
        imps = self.imported("global")
        if idx < len(imps):
            return imps[idx].desc
        g = self.globals[idx - len(imps)]
        return (g.valtype, g.mutable)


class _Reader:
    __slots__ = ("d", "p", "limit")

    def __init__(self, data: bytes, start: int = 0, limit: int | None = None):
        self.d = data
        self.p = start
        self.limit = len(data) if limit is None else limit

    def byte(self) -> int:
        if self.p >= self.limit:
            raise DecodeError(UNEXPECTED_END)
        b = self.d[self.p]
        self.p += 1
        return b

    def bytes(self, n: int) -> bytes:
        if self.p + n > self.limit:
            raise DecodeError(UNEXPECTED_END)
        out = self.d[self.p:self.p + n]
        self.p += n
        return out

    def uleb(self, bits: int = 32) -> int:
        result = shift = 0
        maxbytes = (bits + 6) // 7
        for i in range(maxbytes):
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                if shift + 7 > bits and (b >> (bits - shift)):
                    raise DecodeError("integer too large")
                return result
            shift += 7
        raise DecodeError("integer representation too long")

    def sleb(self, bits: int) -> int:
        result = shift = 0
        maxbytes = (bits + 6) // 7
        for i in range(maxbytes):
            b = self.byte()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                if shift < bits and (b & 0x40):
                    result |= (~0 << shift)
                if shift > bits:
                    # unused bits must be a sign extension
                    signed = result if not (b & 0x40) else result | (~0 << shift)
                    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
                    if signed < lo or signed > hi:
                        raise DecodeError("integer too large")
                    result = signed
                return result
        raise DecodeError("integer representation too long")

    def name(self) -> str:
        n = self.uleb()
        raw = self.bytes(n)
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise DecodeError(MALFORMED_UTF8) from None

    def eof(self) -> bool:
        return self.p >= self.limit


def _valtype(b: int) -> str:
    if b in VALTYPES:
        return VALTYPES[b]
    if b in (0x70, 0x6F):
        raise Unsupported(f"reference value type 0x{b:02x} (reference-types beyond frozen flags)")
    raise DecodeError("malformed value type")


def _limits(r: _Reader, what: str) -> tuple[int, int | None]:
    flags = r.byte()
    if flags == 0x00:
        return r.uleb(), None
    if flags == 0x01:
        mn = r.uleb()
        return mn, r.uleb()
    if flags == 0x03:
        raise Unsupported(f"shared {what} limits flag 0x03 (threads)")
    if flags in (0x04, 0x05):
        raise Unsupported(f"memory64 {what} limits flag 0x{flags:02x}")
    raise DecodeError("malformed limits flags")


def _blocktype(r: _Reader) -> tuple:
    """Blocktype = one of: 0x40 empty, a single valtype byte, or a POSITIVE sleb33 typeidx."""
    b = r.d[r.p] if r.p < r.limit else None
    if b is None:
        raise DecodeError(UNEXPECTED_END)
    if b == 0x40:
        r.p += 1
        return ("val", [])
    if b in VALTYPES:
        r.p += 1
        return ("val", [VALTYPES[b]])
    if b in (0x70, 0x6F):
        raise Unsupported(f"reference block type 0x{b:02x}")
    idx = r.sleb(33)
    if idx < 0:
        raise DecodeError("malformed block type")
    return ("type", idx)


def _const_expr(r: _Reader) -> list[Instr]:
    """Decode a constant-expression (global init / elem / data offset) up to its `end`.
    Structurally any non-control instruction sequence is accepted here; the VALIDATOR enforces
    const-ness ("constant expression required")."""
    body: list[Instr] = []
    depth = 0
    while True:
        ins = _instr(r)
        if ins.op == "end":
            if depth == 0:
                return body
            depth -= 1
        elif ins.op in ("block", "loop", "if"):
            depth += 1
        body.append(ins)


def _instr(r: _Reader) -> Instr:
    b = r.byte()
    if b == 0xFC:
        sub = r.uleb()
        if sub in _FC_SUBOPS:
            return Instr(_FC_SUBOPS[sub])
        raise Unsupported(f"0xFC subopcode {sub} (bulk-memory beyond frozen flags)")
    if b == 0xFD:
        raise Unsupported("0xFD SIMD prefix (beyond frozen flags)")
    if b in (0xD0, 0xD1, 0xD2):
        raise Unsupported(f"reference-type opcode 0x{b:02x} (beyond frozen flags)")
    if b == 0x1C:
        raise Unsupported("typed select 0x1C (reference-types beyond frozen flags)")
    if b not in OPCODES:
        raise Unsupported(f"opcode 0x{b:02x} (not in enumerated M5 surface)")
    op, kind = OPCODES[b]
    if kind == IMM_NONE:
        return Instr(op)
    if kind == IMM_U32:
        return Instr(op, r.uleb())
    if kind == IMM_S32:
        return Instr(op, r.sleb(32))
    if kind == IMM_S64:
        return Instr(op, r.sleb(64))
    if kind == IMM_F32:
        return Instr(op, int.from_bytes(r.bytes(4), "little"))
    if kind == IMM_F64:
        return Instr(op, int.from_bytes(r.bytes(8), "little"))
    if kind == IMM_BLOCKTYPE:
        return Instr(op, bt=_blocktype(r))
    if kind == IMM_BRTABLE:
        n = r.uleb()
        tgts = [r.uleb() for _ in range(n)]
        return Instr(op, targets=tgts, default=r.uleb())
    if kind == IMM_MEMARG:
        align = r.uleb()
        offset = r.uleb()
        return Instr(op, align=align, offset=offset)
    if kind == IMM_MEMIDX:
        idx = r.byte()
        if idx != 0x00:
            raise Unsupported(f"non-zero memory index {idx} for {op} (multi-memory)")
        return Instr(op)
    if kind == IMM_CALLIND:
        typeidx = r.uleb()
        tableidx = r.byte()
        if tableidx != 0x00:
            raise Unsupported(f"non-zero table index {tableidx} for call_indirect (multi-table)")
        return Instr(op, typeidx)
    raise AssertionError(kind)


def _decode_instrs(r: _Reader, end: int) -> list[Instr]:
    """Decode a function body up to its declared size boundary. The body MUST contain a
    function-level `end` (sticky `closed` below): a size that runs out before it is the
    spec-malformed "END opcode expected". Instructions AFTER a function-level `end` are
    deliberately still decoded — the validator rejects that shape (frozen boundary)."""
    body: list[Instr] = []
    depth = 0
    closed = False
    while r.p < end:
        ins = _instr(r)
        body.append(ins)
        if ins.op in ("block", "loop", "if"):
            depth += 1
        elif ins.op == "end":
            if depth == 0:
                closed = True
            else:
                depth -= 1
    if r.p != end:
        raise DecodeError("section size mismatch")
    if not closed:
        raise DecodeError(END_EXPECTED)
    return body


def decode(data: bytes) -> Module:
    r = _Reader(data)
    if r.bytes(4) != b"\x00asm":
        raise DecodeError("magic header not detected")
    if int.from_bytes(r.bytes(4), "little") != 1:
        raise DecodeError("unknown binary version")
    m = Module()
    last_order = 0
    n_code_entries = None
    n_data_entries = 0
    saw_data_section = False
    while not r.eof():
        sec_id = r.byte()
        if sec_id > 12:
            raise DecodeError(MALFORMED_SECTION_ID)
        sec_len = r.uleb()
        if r.p + sec_len > len(data):
            raise DecodeError(LENGTH_OOB)
        sec_end = r.p + sec_len
        sr = _Reader(data, r.p, sec_end)
        if sec_id == 0:
            sr.name()                                   # custom-section NAME must be valid UTF-8
            r.p = sec_end                               # content skipped (names, producers, ...)
            continue
        order = _SECTION_ORDER[sec_id]
        if order <= last_order:
            raise DecodeError("section out of order")
        last_order = order
        if sec_id == 1:
            for _ in range(sr.uleb()):
                form = sr.byte()
                if form != 0x60:
                    raise Unsupported(f"non-func type form 0x{form:02x} (GC beyond frozen flags)")
                params = [_valtype(sr.byte()) for _ in range(sr.uleb())]
                results = [_valtype(sr.byte()) for _ in range(sr.uleb())]
                m.types.append(FuncType(params, results))
        elif sec_id == 2:
            for _ in range(sr.uleb()):
                mod = sr.name()
                fld = sr.name()
                kind = sr.byte()
                if kind == 0x00:
                    m.imports.append(Import(mod, fld, "func", sr.uleb()))
                elif kind == 0x01:
                    et = sr.byte()
                    if et != 0x70:
                        raise Unsupported(f"imported table elem type 0x{et:02x}")
                    m.imports.append(Import(mod, fld, "table", ("funcref", _limits(sr, "table"))))
                elif kind == 0x02:
                    m.imports.append(Import(mod, fld, "memory", _limits(sr, "memory")))
                elif kind == 0x03:
                    vt = _valtype(sr.byte())
                    mut = sr.byte()
                    if mut > 1:
                        raise DecodeError("malformed mutability")
                    m.imports.append(Import(mod, fld, "global", (vt, mut == 1)))
                else:
                    raise DecodeError("malformed import kind")
        elif sec_id == 3:
            for _ in range(sr.uleb()):
                m.func_typeidx.append(sr.uleb())
        elif sec_id == 4:
            count = sr.uleb()
            if count + len(m.imported("table")) > 1:
                raise Unsupported(f"multi-table ({count} tables) beyond frozen flags")
            for _ in range(count):
                et = sr.byte()
                if et != 0x70:
                    raise Unsupported(f"table elem type 0x{et:02x} (externref beyond frozen flags)")
                m.tables.append(("funcref", _limits(sr, "table")))
        elif sec_id == 5:
            count = sr.uleb()
            if count + len(m.imported("memory")) > 1:
                raise Unsupported(f"multi-memory ({count} memories) beyond frozen flags")
            for _ in range(count):
                m.mems.append(_limits(sr, "memory"))
        elif sec_id == 6:
            for _ in range(sr.uleb()):
                vt = _valtype(sr.byte())
                mut = sr.byte()
                if mut > 1:
                    raise DecodeError("malformed mutability")
                m.globals.append(Global(vt, mut == 1, _const_expr(sr)))
        elif sec_id == 7:
            for _ in range(sr.uleb()):
                name = sr.name()
                kind = sr.byte()
                if kind > 3:
                    raise DecodeError("malformed export kind")
                m.exports.append(Export(name, ("func", "table", "memory", "global")[kind],
                                        sr.uleb()))
        elif sec_id == 8:
            m.start = sr.uleb()
        elif sec_id == 9:
            for _ in range(sr.uleb()):
                flags = sr.uleb()
                if flags != 0:
                    raise Unsupported(f"element segment flags {flags} (bulk-memory/passive "
                                      f"beyond frozen flags)")
                off = _const_expr(sr)
                m.elems.append(ElemSeg(0, off, [sr.uleb() for _ in range(sr.uleb())]))
        elif sec_id == 12:
            m.datacount = sr.uleb()
        elif sec_id == 10:
            n_code_entries = sr.uleb()
            for _ in range(n_code_entries):
                size = sr.uleb()
                entry_end = sr.p + size
                if entry_end > sec_end:
                    raise DecodeError(LENGTH_OOB)
                local_types: list[str] = []
                for _ in range(sr.uleb()):
                    n = sr.uleb()
                    if n + len(local_types) > 1_000_000:
                        raise DecodeError("too many locals")
                    local_types.extend([_valtype(sr.byte())] * n)
                body = _decode_instrs(sr, entry_end)
                m.funcs.append(Func(typeidx=0, local_types=local_types, body=body))
        elif sec_id == 11:
            saw_data_section = True
            n_data_entries = sr.uleb()
            for _ in range(n_data_entries):
                flags = sr.uleb()
                if flags != 0:
                    raise Unsupported(f"data segment flags {flags} (bulk-memory/passive "
                                      f"beyond frozen flags)")
                off = _const_expr(sr)
                m.datas.append(DataSeg(0, off, sr.bytes(sr.uleb())))
        if sr.p != sec_end:
            raise DecodeError("section size mismatch")
        r.p = sec_end
    # cross-section consistency
    if len(m.func_typeidx) != len(m.funcs):
        raise DecodeError(FUNC_CODE_MISMATCH)
    if m.datacount is not None and m.datacount != (n_data_entries if saw_data_section else 0):
        raise DecodeError(DATACOUNT_MISMATCH)
    for f, tidx in zip(m.funcs, m.func_typeidx):
        f.typeidx = tidx                                 # range-checked by the validator
    return m
