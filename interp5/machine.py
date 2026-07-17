"""machine.py — the M5 full-surface executor.

Executes decoded Modules over an untyped bit-pattern value stack (see package docstring):
exact integer semantics (same rules as the frozen interp/machine.py, re-stated here for the
wider opcode set), exact IEEE float semantics via interp5.fvalues, structured control flow
with MULTI-VALUE blocks (typeidx blocktypes; a br to a loop carries the loop's PARAM arity,
to a block/if its RESULT arity), calls and call_indirect with the three indirect-call traps,
funcref tables initialized from active element segments, mutable/immutable globals, a LAZY
page-granular linear memory (a 4 GiB grow succeeds without allocating 4 GiB — pages
materialize on first touch), every MVP load/store (little-endian, effective-address bounds
check), active data segments, start functions, and cross-module linking:

- a built-in `spectest` host module (print / print_i32 — the only spectest fields the
  converted corpus imports);
- a registry populated by the `register` command, so a later module can import a previously
  registered instance's exports (memory_grow.wast re-imports grown memories).

Call-stack exhaustion: a wasm-frame counter caps recursion at FRAME_CAP and raises
Trap("call stack exhausted") — the spec-canonical text asserted by assert_exhaustion — with a
RecursionError backstop mapped to the same trap.

Trap texts are the spec-canonical strings authored by the reference interpreter (read from
the oracle corpus, never invented): "integer divide by zero", "integer overflow",
"invalid conversion to integer", "out of bounds memory access", "undefined element",
"uninitialized element", "indirect call type mismatch", "unreachable",
"call stack exhausted", "out of bounds table access".
"""
from __future__ import annotations

import sys

from . import fvalues as F
from .decoder import Module, FuncType, Func, Instr, Unsupported

MASK32 = 0xFFFF_FFFF
MASK64 = 0xFFFF_FFFF_FFFF_FFFF

DIV_ZERO = "integer divide by zero"
OVERFLOW = "integer overflow"
OOB_MEM = "out of bounds memory access"
OOB_TABLE = "out of bounds table access"
UNDEFINED_ELEMENT = "undefined element"
UNINIT_ELEMENT = "uninitialized element"
INDIRECT_MISMATCH = "indirect call type mismatch"
UNREACHABLE = "unreachable"
EXHAUSTED = "call stack exhausted"

PAGE_SIZE = 65536
MAX_PAGES = 65536
FRAME_CAP = 1000                      # wasm call frames before "call stack exhausted"

sys.setrecursionlimit(400_000)        # our tree-walker uses several Python frames per wasm frame


class Trap(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class LinkError(Exception):
    """An import this engine cannot resolve (host surface gap) — classified UNSUPPORTED."""


class IncompatibleImport(LinkError):
    """An import that RESOLVED but whose actual external type does not match the module's
    declared import type (spec: "incompatible import type"). Unlike a plain LinkError this
    is an engine/link-state deviation for oracle-valid modules — the runner counts it FAIL,
    never UNSUPPORTED."""

    def __init__(self, detail: str):
        super().__init__(f"incompatible import type ({detail})")


# ---- runtime objects -----------------------------------------------------------------------

class Memory:
    """Lazy page-granular linear memory: pages materialize on first WRITE; reads of untouched
    pages come from a shared zero page. grow() is O(1) — no allocation — so growing to the
    4 GiB cap succeeds like the reference interpreter's."""
    __slots__ = ("pages", "n_pages", "max_pages")
    _ZERO = bytes(PAGE_SIZE)

    def __init__(self, min_pages: int, max_pages: int | None):
        self.pages: dict[int, bytearray] = {}
        self.n_pages = min_pages
        self.max_pages = max_pages

    @property
    def byte_size(self) -> int:
        return self.n_pages * PAGE_SIZE

    def grow(self, delta: int) -> int:
        cap = self.max_pages if self.max_pages is not None else MAX_PAGES
        if delta < 0 or self.n_pages + delta > cap:
            return -1
        prev = self.n_pages
        self.n_pages += delta
        return prev

    def read(self, ea: int, n: int) -> bytes:
        if ea + n > self.byte_size:
            raise Trap(OOB_MEM)
        out = bytearray(n)
        pos = 0
        while pos < n:
            pi, po = divmod(ea + pos, PAGE_SIZE)
            take = min(n - pos, PAGE_SIZE - po)
            page = self.pages.get(pi)
            out[pos:pos + take] = page[po:po + take] if page is not None else self._ZERO[:take]
            pos += take
        return bytes(out)

    def write(self, ea: int, data: bytes) -> None:
        n = len(data)
        if ea + n > self.byte_size:
            raise Trap(OOB_MEM)
        pos = 0
        while pos < n:
            pi, po = divmod(ea + pos, PAGE_SIZE)
            take = min(n - pos, PAGE_SIZE - po)
            page = self.pages.get(pi)
            if page is None:
                page = self.pages[pi] = bytearray(PAGE_SIZE)
            page[po:po + take] = data[pos:pos + take]
            pos += take


class Table:
    __slots__ = ("entries", "max_size")

    def __init__(self, min_size: int, max_size: int | None):
        self.entries: list[object | None] = [None] * min_size
        self.max_size = max_size


class GlobalCell:
    __slots__ = ("valtype", "mutable", "value")

    def __init__(self, valtype: str, mutable: bool, value: int):
        self.valtype = valtype
        self.mutable = mutable
        self.value = value


class HostFunc:
    """A host (spectest) function: fixed type, python behavior (all corpus imports are
    print-style no-ops returning [])."""
    __slots__ = ("ftype", "name")

    def __init__(self, ftype: FuncType, name: str):
        self.ftype = ftype
        self.name = name

    def __call__(self, args: list[int]) -> list[int]:
        return []


class WasmFunc:
    """A defined function bound to its defining instance (imports of re-exported functions
    keep executing in their home instance)."""
    __slots__ = ("inst", "func", "ftype")

    def __init__(self, inst: "Instance", func: Func, ftype: FuncType):
        self.inst = inst
        self.func = func
        self.ftype = ftype


class Instance:
    __slots__ = ("module", "funcs", "tables", "mems", "globals", "exports")

    def __init__(self, module: Module):
        self.module = module
        self.funcs: list[HostFunc | WasmFunc] = []
        self.tables: list[Table] = []
        self.mems: list[Memory] = []
        self.globals: list[GlobalCell] = []
        self.exports: dict[str, tuple[str, object]] = {}


SPECTEST_TYPES = {
    "print": FuncType([], []),
    "print_i32": FuncType(["i32"], []),
    "print_i64": FuncType(["i64"], []),
    "print_f32": FuncType(["f32"], []),
    "print_f64": FuncType(["f64"], []),
    "print_i32_f32": FuncType(["i32", "f32"], []),
    "print_f64_f64": FuncType(["f64", "f64"], []),
}


def _spectest_export(field: str, kind: str):
    """Resolve a spectest import lazily (fresh objects per lookup are fine: the corpus never
    checks spectest state aliasing). Values follow the reference interpreter's spectest."""
    if kind == "func" and field in SPECTEST_TYPES:
        return HostFunc(SPECTEST_TYPES[field], field)
    if kind == "global" and field in ("global_i32", "global_i64", "global_f32", "global_f64"):
        vt = field.rsplit("_", 1)[1]
        val = {"i32": 666, "i64": 666,
               "f32": F.f2b32(666.6), "f64": F.f2b64(666.6)}[vt]
        return GlobalCell(vt, False, val)
    if kind == "memory" and field == "memory":
        return Memory(1, 2)
    if kind == "table" and field == "table":
        return Table(10, 20)
    raise LinkError(f"spectest.{field} ({kind}) not in implemented host surface")


class Store:
    """Cross-module state: named instances (module command `name`) and the `register` map."""

    def __init__(self):
        self.registered: dict[str, Instance] = {}

    def resolve(self, module_name: str, field: str, kind: str):
        if module_name == "spectest":
            return _spectest_export(field, kind)
        inst = self.registered.get(module_name)
        if inst is None:
            raise LinkError(f"import module {module_name!r} not registered")
        exp = inst.exports.get(field)
        if exp is None:
            raise LinkError(f"import {module_name!r}.{field!r} not exported")
        if exp[0] != kind:
            raise LinkError(f"import {module_name!r}.{field!r} kind {exp[0]} != {kind}")
        return exp[1]


def _eval_const_expr(expr: list[Instr], globals_: list[GlobalCell]) -> int:
    """Evaluate a validated constant expression (single const or global.get)."""
    ins = expr[0]
    if ins.op in ("i32.const",):
        return ins.imm & MASK32
    if ins.op in ("i64.const",):
        return ins.imm & MASK64
    if ins.op in ("f32.const", "f64.const"):
        return ins.imm                                  # already raw bits from the decoder
    if ins.op == "global.get":
        return globals_[ins.imm].value
    raise Unsupported(f"non-constant initializer {ins.op}")


def _limits_match(actual_min: int, actual_max: int | None,
                  imp_min: int, imp_max: int | None) -> bool:
    """Spec limits matching: {n2,m2} <= {n1,m1} iff n2 >= n1 and (m1 empty, or m2 nonempty
    and m2 <= m1). actual_min is the CURRENT size (a grown memory/table matches by what it
    is now, like the reference interpreter)."""
    if actual_min < imp_min:
        return False
    return imp_max is None or (actual_max is not None and actual_max <= imp_max)


def _check_import_type(module: Module, im, obj) -> None:
    """External-type match of a resolved import against the module's declared import type.
    Mismatch raises IncompatibleImport (spec text "incompatible import type")."""
    if im.kind == "func":
        want = module.types[im.desc]
        if obj.ftype != want:
            raise IncompatibleImport(
                f"func {im.module}.{im.field}: export {obj.ftype} != declared {want}")
    elif im.kind == "table":
        _elemtype, (mn, mx) = im.desc
        if not _limits_match(len(obj.entries), obj.max_size, mn, mx):
            raise IncompatibleImport(
                f"table {im.module}.{im.field}: export limits "
                f"({len(obj.entries)},{obj.max_size}) do not match declared ({mn},{mx})")
    elif im.kind == "memory":
        mn, mx = im.desc
        if not _limits_match(obj.n_pages, obj.max_pages, mn, mx):
            raise IncompatibleImport(
                f"memory {im.module}.{im.field}: export limits "
                f"({obj.n_pages},{obj.max_pages}) do not match declared ({mn},{mx})")
    elif im.kind == "global":
        vt, mut = im.desc
        if obj.valtype != vt or obj.mutable != mut:
            raise IncompatibleImport(
                f"global {im.module}.{im.field}: export "
                f"({obj.valtype},mut={obj.mutable}) != declared ({vt},mut={mut})")


def instantiate(module: Module, store: Store | None = None) -> Instance:
    """Decode-side structures -> a runnable Instance: resolve imports, allocate
    tables/memories/globals, apply active elem/data segments (bounds-checked, trapping with
    the spec texts), run the start function. Raises Trap for instantiation-time traps
    (assert_uninstantiable), LinkError for unresolvable imports (UNSUPPORTED), Unsupported
    for out-of-surface constructs."""
    store = store or Store()
    inst = Instance(module)
    for im in module.imports:
        obj = store.resolve(im.module, im.field, im.kind)
        _check_import_type(module, im, obj)
        if im.kind == "func":
            inst.funcs.append(obj)
        elif im.kind == "table":
            inst.tables.append(obj)
        elif im.kind == "memory":
            inst.mems.append(obj)
        elif im.kind == "global":
            inst.globals.append(obj)
    for elemtype, (mn, mx) in module.tables:
        inst.tables.append(Table(mn, mx))
    for mn, mx in module.mems:
        inst.mems.append(Memory(mn, mx))
    for g in module.globals:
        inst.globals.append(GlobalCell(g.valtype, g.mutable,
                                       _eval_const_expr(g.init, inst.globals)))
    for f, tidx in zip(module.funcs, module.func_typeidx):
        inst.funcs.append(WasmFunc(inst, f, module.types[tidx]))
    for e in module.exports:
        if e.kind == "func":
            inst.exports[e.name] = ("func", inst.funcs[e.idx])
        elif e.kind == "table":
            inst.exports[e.name] = ("table", inst.tables[e.idx])
        elif e.kind == "memory":
            inst.exports[e.name] = ("memory", inst.mems[e.idx])
        elif e.kind == "global":
            inst.exports[e.name] = ("global", inst.globals[e.idx])
    # active element segments, then active data segments, then start (spec order)
    for seg in module.elems:
        table = inst.tables[seg.tableidx]
        off = _eval_const_expr(seg.offset, inst.globals) & MASK32
        if off + len(seg.funcidxs) > len(table.entries):
            raise Trap(OOB_TABLE)
        for i, fidx in enumerate(seg.funcidxs):
            table.entries[off + i] = inst.funcs[fidx]
    for seg in module.datas:
        mem = inst.mems[seg.memidx]
        off = _eval_const_expr(seg.offset, inst.globals) & MASK32
        mem.write(off, seg.data)                        # raises Trap(OOB_MEM) when out of range
    if module.start is not None:
        _call(inst.funcs[module.start], [], _Ctx())
    return inst


def invoke(inst: Instance, field: str, args: list[int]) -> list[int]:
    exp = inst.exports.get(field)
    if exp is None or exp[0] != "func":
        raise KeyError(field)
    fn = exp[1]
    masked = [a & (MASK32 if t in ("i32", "f32") else MASK64)
              for a, t in zip(args, fn.ftype.params)]
    if len(args) != len(fn.ftype.params):
        raise ValueError(f"arity: {field} expects {len(fn.ftype.params)}, got {len(args)}")
    try:
        return _call(fn, masked, _Ctx())
    except RecursionError:                              # backstop; FRAME_CAP should fire first
        raise Trap(EXHAUSTED) from None


def read_global(inst: Instance, field: str) -> int:
    exp = inst.exports.get(field)
    if exp is None or exp[0] != "global":
        raise KeyError(field)
    return exp[1].value


# ---- structured control flow ----------------------------------------------------------------

class _Block:
    __slots__ = ("kind", "bt", "then_seq", "else_seq")

    def __init__(self, kind: str, bt, then_seq: list, else_seq):
        self.kind = kind
        self.bt = bt
        self.then_seq = then_seq
        self.else_seq = else_seq


class _Label:
    __slots__ = ("arity", "base")

    def __init__(self, arity: int, base: int):
        self.arity = arity
        self.base = base


class _Branch(Exception):
    __slots__ = ("depth",)

    def __init__(self, depth: int):
        self.depth = depth


class _Return(Exception):
    pass


class _Ctx:
    __slots__ = ("depth",)

    def __init__(self):
        self.depth = 0


def _structure(body: list[Instr]) -> list:
    seq, i, stop = _parse_seq(body, 0)
    if stop != "end" or i != len(body):
        raise Unsupported(f"malformed function body (stopped on {stop!r} at {i}/{len(body)})")
    return seq


def _parse_seq(body: list[Instr], i: int) -> tuple[list, int, str]:
    seq: list = []
    n = len(body)
    while i < n:
        op = body[i].op
        if op == "end":
            return seq, i + 1, "end"
        if op == "else":
            return seq, i, "else"
        if op in ("block", "loop", "if"):
            opener = body[i]
            then_seq, i, stop = _parse_seq(body, i + 1)
            else_seq = None
            if stop == "else":
                if opener.op != "if":
                    raise Unsupported(f"`else` inside a {opener.op}")
                else_seq, i, stop2 = _parse_seq(body, i + 1)
                if stop2 != "end":
                    raise Unsupported("malformed if: else-branch not terminated by end")
            seq.append(_Block(opener.op, opener.bt, then_seq, else_seq))
        else:
            seq.append(body[i])
            i += 1
    raise Unsupported("unterminated block or function body (missing end)")


def _bt_types(inst: Instance, bt) -> tuple[list[str], list[str]]:
    if bt[0] == "val":
        return [], bt[1]
    ft = inst.module.types[bt[1]]
    return ft.params, ft.results


def _call(fn, args: list[int], ctx: _Ctx) -> list[int]:
    if isinstance(fn, HostFunc):
        return fn(args)
    if ctx.depth >= FRAME_CAP:
        raise Trap(EXHAUSTED)
    ctx.depth += 1
    try:
        inst = fn.inst
        func = fn.func
        locals_ = list(args)
        for vt in func.local_types:
            locals_.append(0)
        seq = getattr(func, "_seq", None)
        if seq is None:
            seq = _structure(func.body)
            func._seq = seq                             # cache: bodies are immutable
        nres = len(fn.ftype.results)
        stack: list[int] = []
        try:
            _exec_seq(seq, stack, locals_, [_Label(nres, 0)], inst, ctx)
        except _Return:
            pass
        except _Branch:
            pass                                        # branch to the function label
        if len(stack) < nres:
            raise Trap("stack underflow producing results")   # impossible for valid modules
        return stack[len(stack) - nres:]
    finally:
        ctx.depth -= 1


def _exec_seq(seq: list, stack: list[int], locals_: list[int], labels: list,
              inst: Instance, ctx: _Ctx) -> None:
    for item in seq:
        if type(item) is _Block:
            _exec_block(item, stack, locals_, labels, inst, ctx)
        else:
            _exec_instr(item, stack, locals_, labels, inst, ctx)


def _exec_block(blk: _Block, stack: list[int], locals_: list[int], labels: list,
                inst: Instance, ctx: _Ctx) -> None:
    params, results = _bt_types(inst, blk.bt)
    if blk.kind == "if":
        cond = stack.pop()
        base = len(stack) - len(params)
        chosen = blk.then_seq if cond & MASK32 else (blk.else_seq or [])
        try:
            _exec_seq(chosen, stack, locals_, labels + [_Label(len(results), base)], inst, ctx)
        except _Branch as b:
            if b.depth:
                raise _Branch(b.depth - 1) from None
        return
    if blk.kind == "loop":
        base = len(stack) - len(params)
        label = _Label(len(params), base)               # br to a loop carries its PARAMS
        while True:
            try:
                _exec_seq(blk.then_seq, stack, locals_, labels + [label], inst, ctx)
                return
            except _Branch as b:
                if b.depth:
                    raise _Branch(b.depth - 1) from None
                # depth 0: _do_br reset the stack to base+params; re-enter the loop body
    else:
        base = len(stack) - len(params)
        try:
            _exec_seq(blk.then_seq, stack, locals_, labels + [_Label(len(results), base)],
                      inst, ctx)
        except _Branch as b:
            if b.depth:
                raise _Branch(b.depth - 1) from None


def _do_br(depth: int, stack: list[int], labels: list) -> None:
    tgt = labels[len(labels) - 1 - depth]
    n = tgt.arity
    if n:
        vals = stack[len(stack) - n:]
        del stack[tgt.base:]
        stack.extend(vals)
    else:
        del stack[tgt.base:]
    raise _Branch(depth)


# ---- natural sizes for loads/stores ----------------------------------------------------------

_LOADS = {
    "i32.load": (4, "i32", None), "i64.load": (8, "i64", None),
    "f32.load": (4, "f32", None), "f64.load": (8, "f64", None),
    "i32.load8_s": (1, "i32", True), "i32.load8_u": (1, "i32", False),
    "i32.load16_s": (2, "i32", True), "i32.load16_u": (2, "i32", False),
    "i64.load8_s": (1, "i64", True), "i64.load8_u": (1, "i64", False),
    "i64.load16_s": (2, "i64", True), "i64.load16_u": (2, "i64", False),
    "i64.load32_s": (4, "i64", True), "i64.load32_u": (4, "i64", False),
}
_STORES = {
    "i32.store": 4, "i64.store": 8, "f32.store": 4, "f64.store": 8,
    "i32.store8": 1, "i32.store16": 2, "i64.store8": 1, "i64.store16": 2, "i64.store32": 4,
}

_F32_BINOPS = {"f32.add": "add", "f32.sub": "sub", "f32.mul": "mul", "f32.div": "div"}
_F64_BINOPS = {"f64.add": "add", "f64.sub": "sub", "f64.mul": "mul", "f64.div": "div"}
_TRUNCS = {
    "i32.trunc_f32_s": ("f32", "i32", True, False), "i32.trunc_f32_u": ("f32", "i32", False, False),
    "i32.trunc_f64_s": ("f64", "i32", True, False), "i32.trunc_f64_u": ("f64", "i32", False, False),
    "i64.trunc_f32_s": ("f32", "i64", True, False), "i64.trunc_f32_u": ("f32", "i64", False, False),
    "i64.trunc_f64_s": ("f64", "i64", True, False), "i64.trunc_f64_u": ("f64", "i64", False, False),
    "i32.trunc_sat_f32_s": ("f32", "i32", True, True), "i32.trunc_sat_f32_u": ("f32", "i32", False, True),
    "i32.trunc_sat_f64_s": ("f64", "i32", True, True), "i32.trunc_sat_f64_u": ("f64", "i32", False, True),
    "i64.trunc_sat_f32_s": ("f32", "i64", True, True), "i64.trunc_sat_f32_u": ("f32", "i64", False, True),
    "i64.trunc_sat_f64_s": ("f64", "i64", True, True), "i64.trunc_sat_f64_u": ("f64", "i64", False, True),
}


def _to_signed(bits: int, v: int) -> int:
    sign = 1 << (bits - 1)
    return v - (1 << bits) if v & sign else v


def _to_unsigned(bits: int, v: int) -> int:
    return v & ((1 << bits) - 1)


def _exec_instr(ins: Instr, stack: list[int], locals_: list[int], labels: list,
                inst: Instance, ctx: _Ctx) -> None:
    op = ins.op
    push, pop = stack.append, stack.pop

    # ---- most frequent first: consts, locals, parametric --------------------------------
    if op == "i32.const":
        push(ins.imm & MASK32); return
    if op == "i64.const":
        push(ins.imm & MASK64); return
    if op == "f32.const" or op == "f64.const":
        push(ins.imm); return
    if op == "local.get":
        push(locals_[ins.imm]); return
    if op == "local.set":
        locals_[ins.imm] = pop(); return
    if op == "local.tee":
        locals_[ins.imm] = stack[-1]; return
    if op == "global.get":
        push(inst.globals[ins.imm].value); return
    if op == "global.set":
        inst.globals[ins.imm].value = pop(); return
    if op == "nop":
        return
    if op == "drop":
        pop(); return
    if op == "select":
        c = pop(); b = pop(); a = pop()
        push(a if c & MASK32 else b); return
    if op == "unreachable":
        raise Trap(UNREACHABLE)
    if op == "return":
        raise _Return()
    if op == "br":
        _do_br(ins.imm, stack, labels)
    if op == "br_if":
        if pop() & MASK32:
            _do_br(ins.imm, stack, labels)
        return
    if op == "br_table":
        idx = pop() & MASK32
        tgt = ins.targets[idx] if idx < len(ins.targets) else ins.default
        _do_br(tgt, stack, labels)

    # ---- calls ----------------------------------------------------------------------------
    if op == "call":
        fn = inst.funcs[ins.imm]
        n = len(fn.ftype.params)
        args = stack[len(stack) - n:] if n else []
        if n:
            del stack[len(stack) - n:]
        stack.extend(_call(fn, args, ctx))
        return
    if op == "call_indirect":
        want = inst.module.types[ins.imm]
        eidx = pop() & MASK32
        table = inst.tables[0]
        if eidx >= len(table.entries):
            raise Trap(UNDEFINED_ELEMENT)
        fn = table.entries[eidx]
        if fn is None:
            raise Trap(UNINIT_ELEMENT)
        if fn.ftype.params != want.params or fn.ftype.results != want.results:
            raise Trap(INDIRECT_MISMATCH)
        n = len(fn.ftype.params)
        args = stack[len(stack) - n:] if n else []
        if n:
            del stack[len(stack) - n:]
        stack.extend(_call(fn, args, ctx))
        return

    # ---- memory ----------------------------------------------------------------------------
    ld = _LOADS.get(op)
    if ld is not None:
        n, target, signed = ld
        ea = (pop() & MASK32) + ins.offset
        raw = inst.mems[0].read(ea, n)
        v = int.from_bytes(raw, "little")
        if signed:
            v = _to_unsigned(32 if target == "i32" else 64, _to_signed(n * 8, v))
        push(v)
        return
    st = _STORES.get(op)
    if st is not None:
        val = pop()
        ea = (pop() & MASK32) + ins.offset
        inst.mems[0].write(ea, (val & ((1 << (st * 8)) - 1)).to_bytes(st, "little"))
        return
    if op == "memory.size":
        push(inst.mems[0].n_pages); return
    if op == "memory.grow":
        push(inst.mems[0].grow(pop() & MASK32) & MASK32); return

    # ---- float ops ---------------------------------------------------------------------------
    b = _F32_BINOPS.get(op)
    if b is not None:
        y = pop(); x = pop(); push(F.f32_binop(b, x, y)); return
    b = _F64_BINOPS.get(op)
    if b is not None:
        y = pop(); x = pop(); push(F.f64_binop(b, x, y)); return
    tr = _TRUNCS.get(op)
    if tr is not None:
        src, dst, signed, sat = tr
        x = pop()
        try:
            fn = F.trunc_f32 if src == "f32" else F.trunc_f64
            push(fn(dst, signed, x, sat))
        except F.FTrap as t:
            raise Trap(t.kind) from None
        return
    if op.startswith("f32."):
        rest = op[4:]
        if rest in ("eq", "ne", "lt", "gt", "le", "ge"):
            y = pop(); x = pop(); push(F.f32_cmp(rest, x, y)); return
        if rest == "min":
            y = pop(); x = pop(); push(F.f32_min(x, y)); return
        if rest == "max":
            y = pop(); x = pop(); push(F.f32_max(x, y)); return
        if rest == "copysign":
            y = pop(); x = pop(); push(F.f32_copysign(x, y)); return
        if rest == "abs":
            push(F.f32_abs(pop())); return
        if rest == "neg":
            push(F.f32_neg(pop())); return
        if rest == "sqrt":
            push(F.f32_sqrt(pop())); return
        if rest in ("ceil", "floor", "trunc", "nearest"):
            push(F.f32_round(rest, pop())); return
        if rest == "demote_f64":
            push(F.f32_demote_f64(pop())); return
        if rest == "convert_i32_s":
            push(F.f32_convert_i32_s(pop())); return
        if rest == "convert_i32_u":
            push(F.f32_convert_i32_u(pop())); return
        if rest == "convert_i64_s":
            push(F.f32_convert_i64_s(pop())); return
        if rest == "convert_i64_u":
            push(F.f32_convert_i64_u(pop())); return
        if rest == "reinterpret_i32":
            push(pop() & MASK32); return
        raise Unsupported(f"f32 op {op}")
    if op.startswith("f64."):
        rest = op[4:]
        if rest in ("eq", "ne", "lt", "gt", "le", "ge"):
            y = pop(); x = pop(); push(F.f64_cmp(rest, x, y)); return
        if rest == "min":
            y = pop(); x = pop(); push(F.f64_min(x, y)); return
        if rest == "max":
            y = pop(); x = pop(); push(F.f64_max(x, y)); return
        if rest == "copysign":
            y = pop(); x = pop(); push(F.f64_copysign(x, y)); return
        if rest == "abs":
            push(F.f64_abs(pop())); return
        if rest == "neg":
            push(F.f64_neg(pop())); return
        if rest == "sqrt":
            push(F.f64_sqrt(pop())); return
        if rest in ("ceil", "floor", "trunc", "nearest"):
            push(F.f64_round(rest, pop())); return
        if rest == "promote_f32":
            push(F.f64_promote_f32(pop())); return
        if rest == "convert_i32_s":
            push(F.f64_convert_i32_s(pop())); return
        if rest == "convert_i32_u":
            push(F.f64_convert_i32_u(pop())); return
        if rest == "convert_i64_s":
            push(F.f64_convert_i64_s(pop())); return
        if rest == "convert_i64_u":
            push(F.f64_convert_i64_u(pop())); return
        if rest == "reinterpret_i64":
            push(pop() & MASK64); return
        raise Unsupported(f"f64 op {op}")

    # ---- integer ops (i32./i64. prefixed) ------------------------------------------------------
    kind = op[:3]
    rest = op[4:]
    bits = 32 if kind == "i32" else 64
    m = MASK32 if bits == 32 else MASK64
    if rest in _IBINOPS:
        b2 = pop(); a2 = pop(); push(_ibinop(bits, rest, a2 & m, b2 & m)); return
    if rest in _ICMPS:
        b2 = pop(); a2 = pop(); push(_icompare(bits, rest, a2 & m, b2 & m)); return
    if rest == "eqz":
        push(1 if (pop() & m) == 0 else 0); return
    if rest == "clz":
        v = pop() & m
        push(bits if v == 0 else bits - v.bit_length()); return
    if rest == "ctz":
        v = pop() & m
        push(bits if v == 0 else (v & -v).bit_length() - 1); return
    if rest == "popcnt":
        push((pop() & m).bit_count()); return
    # conversions / reinterprets / extends
    if op == "i32.wrap_i64":
        push(pop() & MASK32); return
    if op == "i64.extend_i32_s":
        push(_to_unsigned(64, _to_signed(32, pop() & MASK32))); return
    if op == "i64.extend_i32_u":
        push(pop() & MASK32); return
    if op == "i32.extend8_s":
        push(_to_unsigned(32, _to_signed(8, pop() & 0xFF))); return
    if op == "i32.extend16_s":
        push(_to_unsigned(32, _to_signed(16, pop() & 0xFFFF))); return
    if op == "i64.extend8_s":
        push(_to_unsigned(64, _to_signed(8, pop() & 0xFF))); return
    if op == "i64.extend16_s":
        push(_to_unsigned(64, _to_signed(16, pop() & 0xFFFF))); return
    if op == "i64.extend32_s":
        push(_to_unsigned(64, _to_signed(32, pop() & MASK32))); return
    if op == "i32.reinterpret_f32":
        push(pop() & MASK32); return
    if op == "i64.reinterpret_f64":
        push(pop() & MASK64); return
    raise Unsupported(f"opcode {op} not implemented")


_IBINOPS = {"add", "sub", "mul", "div_s", "div_u", "rem_s", "rem_u",
            "and", "or", "xor", "shl", "shr_s", "shr_u", "rotl", "rotr"}
_ICMPS = {"eq", "ne", "lt_s", "lt_u", "gt_s", "gt_u", "le_s", "le_u", "ge_s", "ge_u"}


def _ibinop(bits: int, op: str, a: int, b: int) -> int:
    """Exact integer semantics — byte-for-byte the frozen interp/machine.py rules."""
    m = (1 << bits) - 1
    if op == "add":
        return (a + b) & m
    if op == "sub":
        return (a - b) & m
    if op == "mul":
        return (a * b) & m
    if op == "and":
        return a & b
    if op == "or":
        return a | b
    if op == "xor":
        return a ^ b
    if op == "shl":
        return (a << (b % bits)) & m
    if op == "shr_u":
        return a >> (b % bits)
    if op == "shr_s":
        return _to_unsigned(bits, _to_signed(bits, a) >> (b % bits))
    if op == "rotl":
        k = b % bits
        return a if k == 0 else ((a << k) | (a >> (bits - k))) & m
    if op == "rotr":
        k = b % bits
        return a if k == 0 else ((a >> k) | (a << (bits - k))) & m
    if op == "div_u":
        if b == 0:
            raise Trap(DIV_ZERO)
        return (a // b) & m
    if op == "rem_u":
        if b == 0:
            raise Trap(DIV_ZERO)
        return a % b
    if op == "div_s":
        sa, sb = _to_signed(bits, a), _to_signed(bits, b)
        if sb == 0:
            raise Trap(DIV_ZERO)
        if sa == -(1 << (bits - 1)) and sb == -1:
            raise Trap(OVERFLOW)
        q = abs(sa) // abs(sb)
        return _to_unsigned(bits, -q if (sa < 0) != (sb < 0) else q)
    if op == "rem_s":
        sa, sb = _to_signed(bits, a), _to_signed(bits, b)
        if sb == 0:
            raise Trap(DIV_ZERO)
        r = abs(sa) % abs(sb)
        return _to_unsigned(bits, -r if sa < 0 else r)
    raise Unsupported(f"binop {op}")


def _icompare(bits: int, op: str, a: int, b: int) -> int:
    if op == "eq":
        return 1 if a == b else 0
    if op == "ne":
        return 1 if a != b else 0
    if op in ("lt_u", "gt_u", "le_u", "ge_u"):
        res = {"lt_u": a < b, "gt_u": a > b, "le_u": a <= b, "ge_u": a >= b}[op]
        return 1 if res else 0
    sa, sb = _to_signed(bits, a), _to_signed(bits, b)
    res = {"lt_s": sa < sb, "gt_s": sa > sb, "le_s": sa <= sb, "ge_s": sa >= sb}[op]
    return 1 if res else 0
