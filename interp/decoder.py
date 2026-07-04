"""decoder.py — WASM binary decoder scoped to the M1 sections {Type, Function, Export, Code}.

Derived from goal-runs/m1-scope.txt: the 22 instantiated target modules use ONLY these 4
binary sections and 71 integer opcodes. This decoder implements exactly that scope. Anything
outside it — an unknown section id, a non-integer value type, a non-func export, or an opcode
byte not in OPCODES — raises Unsupported (never silently accepted or mis-decoded), consistent
with M0's no-silent-skip invariant.

Instruction decode is verified against the pinned WABT `wasm-objdump -d` disassembly by
tests/decoder_selftest.py, so the opcode byte table is checked against the authoritative
toolchain rather than trusted blindly.
"""
from __future__ import annotations
from dataclasses import dataclass, field


class Unsupported(Exception):
    """A construct outside the enumerated M1 scope (section, valtype, export kind, or opcode)."""


class DecodeError(Exception):
    """A structurally malformed binary (bad magic/version, truncated stream)."""


# valtype byte -> name. Only i32/i64 are in scope; f32/f64 would violate the purity gates and
# are rejected as Unsupported rather than given a stack representation.
VALTYPES = {0x7F: "i32", 0x7E: "i64"}
FLOAT_VALTYPES = {0x7D: "f32", 0x7C: "f64"}

# Immediate encodings.
IMM_NONE, IMM_S32, IMM_S64, IMM_U32 = "none", "s32", "s64", "u32"

# opcode byte -> (mnemonic, immediate-kind), for EXACTLY the 71 enumerated opcodes plus the
# two structural opcodes end/return. Byte values are the stable WASM spec numeric opcodes;
# the table is verified against wasm-objdump -d in tests/decoder_selftest.py.
OPCODES: dict[int, tuple[str, str]] = {
    0x0B: ("end", IMM_NONE),
    0x0F: ("return", IMM_NONE),
    0x20: ("local.get", IMM_U32),
    0x41: ("i32.const", IMM_S32),
    0x42: ("i64.const", IMM_S64),
    # i32 comparisons 0x45..0x4F
    0x45: ("i32.eqz", IMM_NONE), 0x46: ("i32.eq", IMM_NONE), 0x47: ("i32.ne", IMM_NONE),
    0x48: ("i32.lt_s", IMM_NONE), 0x49: ("i32.lt_u", IMM_NONE),
    0x4A: ("i32.gt_s", IMM_NONE), 0x4B: ("i32.gt_u", IMM_NONE),
    0x4C: ("i32.le_s", IMM_NONE), 0x4D: ("i32.le_u", IMM_NONE),
    0x4E: ("i32.ge_s", IMM_NONE), 0x4F: ("i32.ge_u", IMM_NONE),
    # i64 comparisons 0x50..0x5A
    0x50: ("i64.eqz", IMM_NONE), 0x51: ("i64.eq", IMM_NONE), 0x52: ("i64.ne", IMM_NONE),
    0x53: ("i64.lt_s", IMM_NONE), 0x54: ("i64.lt_u", IMM_NONE),
    0x55: ("i64.gt_s", IMM_NONE), 0x56: ("i64.gt_u", IMM_NONE),
    0x57: ("i64.le_s", IMM_NONE), 0x58: ("i64.le_u", IMM_NONE),
    0x59: ("i64.ge_s", IMM_NONE), 0x5A: ("i64.ge_u", IMM_NONE),
    # i32 numeric 0x67..0x78
    0x67: ("i32.clz", IMM_NONE), 0x68: ("i32.ctz", IMM_NONE), 0x69: ("i32.popcnt", IMM_NONE),
    0x6A: ("i32.add", IMM_NONE), 0x6B: ("i32.sub", IMM_NONE), 0x6C: ("i32.mul", IMM_NONE),
    0x6D: ("i32.div_s", IMM_NONE), 0x6E: ("i32.div_u", IMM_NONE),
    0x6F: ("i32.rem_s", IMM_NONE), 0x70: ("i32.rem_u", IMM_NONE),
    0x71: ("i32.and", IMM_NONE), 0x72: ("i32.or", IMM_NONE), 0x73: ("i32.xor", IMM_NONE),
    0x74: ("i32.shl", IMM_NONE), 0x75: ("i32.shr_s", IMM_NONE), 0x76: ("i32.shr_u", IMM_NONE),
    0x77: ("i32.rotl", IMM_NONE), 0x78: ("i32.rotr", IMM_NONE),
    # i64 numeric 0x79..0x8A
    0x79: ("i64.clz", IMM_NONE), 0x7A: ("i64.ctz", IMM_NONE), 0x7B: ("i64.popcnt", IMM_NONE),
    0x7C: ("i64.add", IMM_NONE), 0x7D: ("i64.sub", IMM_NONE), 0x7E: ("i64.mul", IMM_NONE),
    0x7F: ("i64.div_s", IMM_NONE), 0x80: ("i64.div_u", IMM_NONE),
    0x81: ("i64.rem_s", IMM_NONE), 0x82: ("i64.rem_u", IMM_NONE),
    0x83: ("i64.and", IMM_NONE), 0x84: ("i64.or", IMM_NONE), 0x85: ("i64.xor", IMM_NONE),
    0x86: ("i64.shl", IMM_NONE), 0x87: ("i64.shr_s", IMM_NONE), 0x88: ("i64.shr_u", IMM_NONE),
    0x89: ("i64.rotl", IMM_NONE), 0x8A: ("i64.rotr", IMM_NONE),
    # conversions
    0xA7: ("i32.wrap_i64", IMM_NONE),
    0xAC: ("i64.extend_i32_s", IMM_NONE), 0xAD: ("i64.extend_i32_u", IMM_NONE),
    # sign-extension ops
    0xC0: ("i32.extend8_s", IMM_NONE), 0xC1: ("i32.extend16_s", IMM_NONE),
    0xC2: ("i64.extend8_s", IMM_NONE), 0xC3: ("i64.extend16_s", IMM_NONE),
    0xC4: ("i64.extend32_s", IMM_NONE),
}

# Section ids in scope. Others (Import=2, Table=4, Memory=5, Global=6, Start=8, Element=9,
# Data=11, DataCount=12, Custom=0) are Unsupported for M1.
SEC_TYPE, SEC_FUNCTION, SEC_EXPORT, SEC_CODE = 1, 3, 7, 10
SECTION_NAMES = {SEC_TYPE: "Type", SEC_FUNCTION: "Function", SEC_EXPORT: "Export", SEC_CODE: "Code"}


@dataclass
class Instr:
    op: str
    imm: int | None = None


@dataclass
class FuncType:
    params: list[str]
    results: list[str]


@dataclass
class Func:
    typeidx: int
    local_types: list[str]          # declared locals (beyond params), each initialized to 0
    body: list[Instr]


@dataclass
class Module:
    types: list[FuncType] = field(default_factory=list)
    func_typeidx: list[int] = field(default_factory=list)   # Function section: func -> typeidx
    funcs: list[Func] = field(default_factory=list)          # Code section, aligned with func_typeidx
    exports: dict[str, int] = field(default_factory=dict)    # export name -> funcidx (funcs only)


class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def byte(self) -> int:
        if self.p >= len(self.d):
            raise DecodeError("unexpected end of binary")
        b = self.d[self.p]
        self.p += 1
        return b

    def bytes(self, n: int) -> bytes:
        if self.p + n > len(self.d):
            raise DecodeError("unexpected end of binary")
        out = self.d[self.p:self.p + n]
        self.p += n
        return out

    def uleb(self) -> int:
        result = shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                return result
            shift += 7

    def sleb(self, bits: int) -> int:
        result = shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                if shift < bits and (b & 0x40):
                    result |= (~0 << shift)
                return result

    def eof(self) -> bool:
        return self.p >= len(self.d)


def _valtype(b: int) -> str:
    if b in VALTYPES:
        return VALTYPES[b]
    if b in FLOAT_VALTYPES:
        raise Unsupported(f"float value type {FLOAT_VALTYPES[b]} (out of M1 integer scope)")
    raise Unsupported(f"unknown value type byte 0x{b:02x}")


def _decode_type_section(r: _Reader, m: Module) -> None:
    for _ in range(r.uleb()):
        form = r.byte()
        if form != 0x60:
            raise Unsupported(f"non-func type form 0x{form:02x}")
        params = [_valtype(r.byte()) for _ in range(r.uleb())]
        results = [_valtype(r.byte()) for _ in range(r.uleb())]
        m.types.append(FuncType(params, results))


def _decode_function_section(r: _Reader, m: Module) -> None:
    for _ in range(r.uleb()):
        m.func_typeidx.append(r.uleb())


def _decode_export_section(r: _Reader, m: Module) -> None:
    for _ in range(r.uleb()):
        name = r.bytes(r.uleb()).decode("utf-8")
        kind = r.byte()
        idx = r.uleb()
        if kind != 0x00:                       # 0=func; table/mem/global can't occur (no such sections)
            raise Unsupported(f"non-func export kind 0x{kind:02x} for {name!r}")
        m.exports[name] = idx


def _decode_instrs(r: _Reader, end: int) -> list[Instr]:
    """Decode a flat instruction sequence up to byte offset `end` (the code entry boundary).
    The final instruction is the body-terminating `end`. No nested blocks exist in M1 scope,
    so a single linear pass is exhaustive."""
    body: list[Instr] = []
    while r.p < end:
        b = r.byte()
        if b not in OPCODES:
            raise Unsupported(f"opcode 0x{b:02x} (not in enumerated M1 scope)")
        op, imm_kind = OPCODES[b]
        if imm_kind == IMM_NONE:
            body.append(Instr(op))
        elif imm_kind == IMM_U32:
            body.append(Instr(op, r.uleb()))
        elif imm_kind == IMM_S32:
            body.append(Instr(op, r.sleb(32)))
        elif imm_kind == IMM_S64:
            body.append(Instr(op, r.sleb(64)))
        else:                                   # unreachable
            raise DecodeError(f"bad immediate kind {imm_kind}")
    if r.p != end:
        raise DecodeError("instruction stream overran code-entry boundary")
    return body


def _decode_code_section(r: _Reader, m: Module) -> None:
    count = r.uleb()
    for _ in range(count):
        size = r.uleb()
        entry_end = r.p + size
        # local declarations: vec of (count, valtype)
        local_types: list[str] = []
        for _ in range(r.uleb()):
            n = r.uleb()
            vt = _valtype(r.byte())
            local_types.extend([vt] * n)
        body = _decode_instrs(r, entry_end)
        m.funcs.append(Func(typeidx=0, local_types=local_types, body=body))  # typeidx filled below


def decode(data: bytes) -> Module:
    """Decode a WASM binary module limited to the M1 in-scope sections."""
    r = _Reader(data)
    if r.bytes(4) != b"\x00asm":
        raise DecodeError("bad magic (not a WASM binary)")
    version = int.from_bytes(r.bytes(4), "little")
    if version != 1:
        raise Unsupported(f"WASM binary version {version} (only MVP version 1 in scope)")
    m = Module()
    last_id = 0
    while not r.eof():
        sec_id = r.byte()
        sec_len = r.uleb()
        sec_end = r.p + sec_len
        if sec_id == 0:
            raise Unsupported("custom section (id 0) not in M1 scope")
        if sec_id not in SECTION_NAMES:
            raise Unsupported(f"section id {sec_id} ({_guess_section(sec_id)}) not in M1 scope")
        # WASM requires known sections in ascending id order, each at most once.
        if sec_id <= last_id:
            raise DecodeError(f"section id {sec_id} out of order (after {last_id})")
        last_id = sec_id
        if sec_id == SEC_TYPE:
            _decode_type_section(r, m)
        elif sec_id == SEC_FUNCTION:
            _decode_function_section(r, m)
        elif sec_id == SEC_EXPORT:
            _decode_export_section(r, m)
        elif sec_id == SEC_CODE:
            _decode_code_section(r, m)
        if r.p != sec_end:
            raise DecodeError(f"section {SECTION_NAMES[sec_id]} length mismatch "
                              f"(consumed {r.p}, declared end {sec_end})")
    # Align Function-section typeidx onto the Code-section funcs (no imports in scope, so
    # funcidx space == defined funcs). A mismatch is a structural error, not a silent drop.
    if len(m.func_typeidx) != len(m.funcs):
        raise DecodeError(f"function/code count mismatch: {len(m.func_typeidx)} vs {len(m.funcs)}")
    for f, tidx in zip(m.funcs, m.func_typeidx):
        if tidx >= len(m.types):
            raise DecodeError(f"function typeidx {tidx} out of range ({len(m.types)} types)")
        f.typeidx = tidx
    return m


def _guess_section(sec_id: int) -> str:
    return {2: "Import", 4: "Table", 5: "Memory", 6: "Global",
            8: "Start", 9: "Element", 11: "Data", 12: "DataCount"}.get(sec_id, "unknown")
