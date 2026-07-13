"""validator.py — full MVP+signext+truncsat validation over the M5 decoder Module.

Implements the spec-appendix validation algorithm (abstract value stack + control-frame
stack with stack-polymorphic `unreachable` handling) over the FLAT bodies produced by
decoder.py, plus all module-level checks: function type indices, import descriptors,
table/memory limits, global init / elem offset / data offset constant expressions,
start function shape, export index spaces, and duplicate export names.

str(ValidationError) is EXACTLY the spec-canonical assert_invalid text. Texts emitted:
  "type mismatch"                                   every operand/result/stack violation
  "unknown local" / "unknown label" / "unknown function" / "unknown table" /
  "unknown type" / "unknown global" / "unknown memory"
  "constant expression required"                    non-const / wrong-shape init exprs
  "start function"                                  start whose type is not [] -> []
  "duplicate export name"
  "size minimum must not be greater than maximum"
  "memory size must be at most 65536 pages"
  "alignment must not be larger than natural"
  "global is immutable"
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .decoder import Func, FuncType, Instr, Module

TYPE_MISMATCH = "type mismatch"
MEM_MAX_PAGES = 65536

# On the abstract stack a value type is "i32"/"i64"/"f32"/"f64"; None is the wildcard
# ("unknown") produced by popping in the unreachable-polymorphic regime.
ValType = str


class ValidationError(Exception):
    """str(exc) is EXACTLY the spec-canonical assert_invalid text."""


# ---------------------------------------------------------------------------
# systematic mnemonic -> (operand types, result types) map for plain value ops
# ---------------------------------------------------------------------------

def _build_op_types() -> dict[str, tuple[list[str], list[str]]]:
    ot: dict[str, tuple[list[str], list[str]]] = {}
    for t in ("i32", "i64", "f32", "f64"):
        ot[f"{t}.const"] = ([], [t])
    for t in ("i32", "i64"):
        for u in ("clz", "ctz", "popcnt"):
            ot[f"{t}.{u}"] = ([t], [t])
        for b in ("add", "sub", "mul", "div_s", "div_u", "rem_s", "rem_u",
                  "and", "or", "xor", "shl", "shr_s", "shr_u", "rotl", "rotr"):
            ot[f"{t}.{b}"] = ([t, t], [t])
        ot[f"{t}.eqz"] = ([t], ["i32"])
        for r in ("eq", "ne", "lt_s", "lt_u", "gt_s", "gt_u", "le_s", "le_u", "ge_s", "ge_u"):
            ot[f"{t}.{r}"] = ([t, t], ["i32"])
    for t in ("f32", "f64"):
        for u in ("abs", "neg", "ceil", "floor", "trunc", "nearest", "sqrt"):
            ot[f"{t}.{u}"] = ([t], [t])
        for b in ("add", "sub", "mul", "div", "min", "max", "copysign"):
            ot[f"{t}.{b}"] = ([t, t], [t])
        for r in ("eq", "ne", "lt", "gt", "le", "ge"):
            ot[f"{t}.{r}"] = ([t, t], ["i32"])
    # conversions: (mnemonic, src, dst)
    conv: list[tuple[str, str, str]] = [
        ("i32.wrap_i64", "i64", "i32"),
        ("f32.demote_f64", "f64", "f32"),
        ("f64.promote_f32", "f32", "f64"),
        ("i32.reinterpret_f32", "f32", "i32"),
        ("i64.reinterpret_f64", "f64", "i64"),
        ("f32.reinterpret_i32", "i32", "f32"),
        ("f64.reinterpret_i64", "i64", "f64"),
    ]
    for dst in ("i32", "i64"):
        for src in ("f32", "f64"):
            for s in ("s", "u"):
                conv.append((f"{dst}.trunc_{src}_{s}", src, dst))
                conv.append((f"{dst}.trunc_sat_{src}_{s}", src, dst))
    for dst in ("f32", "f64"):
        for src in ("i32", "i64"):
            for s in ("s", "u"):
                conv.append((f"{dst}.convert_{src}_{s}", src, dst))
    for s in ("s", "u"):
        conv.append((f"i64.extend_i32_{s}", "i32", "i64"))
    for op, src, dst in conv:
        ot[op] = ([src], [dst])
    for t, widths in (("i32", ("8", "16")), ("i64", ("8", "16", "32"))):
        for w in widths:
            ot[f"{t}.extend{w}_s"] = ([t], [t])
    return ot


OP_TYPES = _build_op_types()

# load/store: op -> (value type, natural alignment as log2(byte width))
_LOADS: dict[str, tuple[str, int]] = {}
_STORES: dict[str, tuple[str, int]] = {}
for _t, _lg in (("i32", 2), ("i64", 3), ("f32", 2), ("f64", 3)):
    _LOADS[f"{_t}.load"] = (_t, _lg)
    _STORES[f"{_t}.store"] = (_t, _lg)
for _w, _lg in (("8", 0), ("16", 1)):
    for _s in ("s", "u"):
        _LOADS[f"i32.load{_w}_{_s}"] = ("i32", _lg)
        _LOADS[f"i64.load{_w}_{_s}"] = ("i64", _lg)
    _STORES[f"i32.store{_w}"] = ("i32", _lg)
    _STORES[f"i64.store{_w}"] = ("i64", _lg)
for _s in ("s", "u"):
    _LOADS[f"i64.load32_{_s}"] = ("i64", 2)
_STORES["i64.store32"] = ("i64", 2)


# ---------------------------------------------------------------------------
# per-function validation: value stack + control frames (spec appendix)
# ---------------------------------------------------------------------------

@dataclass
class _Frame:
    opcode: str                       # "func" | "block" | "loop" | "if" | "else"
    start_types: list[str]
    end_types: list[str]
    height: int
    unreachable: bool = False


def _bt_types(m: Module, bt: tuple) -> tuple[list[str], list[str]]:
    kind, v = bt
    if kind == "val":
        return [], list(v)
    if v >= len(m.types):
        raise ValidationError("unknown type")
    ft = m.types[v]
    return list(ft.params), list(ft.results)


class _FuncValidator:
    def __init__(self, m: Module, func: Func):
        self.m = m
        ftype = m.types[func.typeidx]
        self.func = func
        self.locals: list[str] = list(ftype.params) + list(func.local_types)
        self.vals: list[ValType | None] = []
        self.ctrls: list[_Frame] = []
        self._push_ctrl("func", [], list(ftype.results))

    # ---- core stack discipline ----
    def _push(self, t: ValType | None) -> None:
        self.vals.append(t)

    def _pop(self, expect: ValType | None = None) -> ValType | None:
        f = self.ctrls[-1]
        if len(self.vals) <= f.height:
            if f.unreachable:
                return expect
            raise ValidationError(TYPE_MISMATCH)
        got = self.vals.pop()
        if got is not None and expect is not None and got != expect:
            raise ValidationError(TYPE_MISMATCH)
        return got if got is not None else expect

    def _pop_many(self, types: list[str]) -> None:
        for t in reversed(types):
            self._pop(t)

    def _push_ctrl(self, opcode: str, ins: list[str], outs: list[str]) -> None:
        self.ctrls.append(_Frame(opcode, ins, outs, len(self.vals)))
        self.vals.extend(ins)

    def _pop_ctrl(self) -> _Frame:
        f = self.ctrls[-1]
        self._pop_many(f.end_types)
        if len(self.vals) != f.height:
            raise ValidationError(TYPE_MISMATCH)
        self.ctrls.pop()
        return f

    def _set_unreachable(self) -> None:
        f = self.ctrls[-1]
        del self.vals[f.height:]
        f.unreachable = True

    def _label_types(self, depth: int) -> list[str]:
        if depth >= len(self.ctrls):
            raise ValidationError("unknown label")
        f = self.ctrls[len(self.ctrls) - 1 - depth]
        return list(f.start_types) if f.opcode == "loop" else list(f.end_types)

    # ---- driver ----
    def validate(self) -> None:
        for ins in self.func.body:
            if not self.ctrls:                       # instruction after the final end
                raise ValidationError(TYPE_MISMATCH)
            self._instr(ins)
        if self.ctrls:                               # missing final end (decoder-guarded)
            raise ValidationError(TYPE_MISMATCH)

    def _instr(self, ins: Instr) -> None:
        op = ins.op
        if op == "nop":
            return
        if op == "unreachable":
            self._set_unreachable()
            return
        if op == "drop":
            self._pop()
            return
        if op == "select":
            self._pop("i32")
            t1 = self._pop()
            t2 = self._pop(t1)
            self._push(t1 if t1 is not None else t2)
            return
        if op in ("block", "loop"):
            params, results = _bt_types(self.m, ins.bt)
            self._pop_many(params)
            self._push_ctrl(op, params, results)
            return
        if op == "if":
            params, results = _bt_types(self.m, ins.bt)
            self._pop("i32")
            self._pop_many(params)
            self._push_ctrl("if", params, results)
            return
        if op == "else":
            f = self.ctrls[-1]
            if f.opcode != "if":
                raise ValidationError(TYPE_MISMATCH)
            self._pop_ctrl()
            self._push_ctrl("else", list(f.start_types), list(f.end_types))
            return
        if op == "end":
            f = self.ctrls[-1]
            if f.opcode == "if" and f.start_types != f.end_types:
                # an if without else has an implicit [params]->[params] else branch
                raise ValidationError(TYPE_MISMATCH)
            self._pop_ctrl()
            self.vals.extend(f.end_types)
            return
        if op == "br":
            self._pop_many(self._label_types(ins.imm))
            self._set_unreachable()
            return
        if op == "br_if":
            self._pop("i32")
            types = self._label_types(ins.imm)
            self._pop_many(types)
            self.vals.extend(types)
            return
        if op == "br_table":
            self._pop("i32")
            default_types = self._label_types(ins.default)
            for tgt in ins.targets:
                types = self._label_types(tgt)
                if len(types) != len(default_types):
                    raise ValidationError(TYPE_MISMATCH)
                popped = [self._pop(t) for t in reversed(types)]
                self.vals.extend(reversed(popped))
            self._pop_many(default_types)
            self._set_unreachable()
            return
        if op == "return":
            self._pop_many(self.ctrls[0].end_types)
            self._set_unreachable()
            return
        if op == "call":
            if ins.imm >= self.m.n_funcs():
                raise ValidationError("unknown function")
            ft = self.m.func_type(ins.imm)
            self._pop_many(list(ft.params))
            self.vals.extend(ft.results)
            return
        if op == "call_indirect":
            if self.m.n_tables() == 0:
                raise ValidationError("unknown table")
            if ins.imm >= len(self.m.types):
                raise ValidationError("unknown type")
            ft = self.m.types[ins.imm]
            self._pop("i32")
            self._pop_many(list(ft.params))
            self.vals.extend(ft.results)
            return
        if op in ("local.get", "local.set", "local.tee"):
            if ins.imm >= len(self.locals):
                raise ValidationError("unknown local")
            t = self.locals[ins.imm]
            if op == "local.get":
                self._push(t)
            elif op == "local.set":
                self._pop(t)
            else:
                self._pop(t)
                self._push(t)
            return
        if op in ("global.get", "global.set"):
            if ins.imm >= self.m.n_globals():
                raise ValidationError("unknown global")
            vt, mutable = self.m.global_type(ins.imm)
            if op == "global.get":
                self._push(vt)
            else:
                if not mutable:
                    raise ValidationError("global is immutable")
                self._pop(vt)
            return
        if op in _LOADS:
            if self.m.n_mems() == 0:
                raise ValidationError("unknown memory")
            t, natural = _LOADS[op]
            if ins.align > natural:
                raise ValidationError("alignment must not be larger than natural")
            self._pop("i32")
            self._push(t)
            return
        if op in _STORES:
            if self.m.n_mems() == 0:
                raise ValidationError("unknown memory")
            t, natural = _STORES[op]
            if ins.align > natural:
                raise ValidationError("alignment must not be larger than natural")
            self._pop(t)
            self._pop("i32")
            return
        if op in ("memory.size", "memory.grow"):
            if self.m.n_mems() == 0:
                raise ValidationError("unknown memory")
            if op == "memory.grow":
                self._pop("i32")
            self._push("i32")
            return
        if op in OP_TYPES:
            operands, results = OP_TYPES[op]
            self._pop_many(operands)
            self.vals.extend(results)
            return
        raise AssertionError(f"validator: unhandled opcode {op!r}")


# ---------------------------------------------------------------------------
# module-level validation
# ---------------------------------------------------------------------------

def _check_table_limits(limits: tuple[int, int | None]) -> None:
    mn, mx = limits
    if mx is not None and mn > mx:
        raise ValidationError("size minimum must not be greater than maximum")


def _check_mem_limits(limits: tuple[int, int | None]) -> None:
    mn, mx = limits
    if mn > MEM_MAX_PAGES or (mx is not None and mx > MEM_MAX_PAGES):
        raise ValidationError("memory size must be at most 65536 pages")
    if mx is not None and mn > mx:
        raise ValidationError("size minimum must not be greater than maximum")


_CONST_OPS = {"i32.const": "i32", "i64.const": "i64", "f32.const": "f32", "f64.const": "f64"}


def _validate_const_expr(m: Module, expr: list[Instr], expected: str,
                         n_imported_globals: int) -> None:
    """A constant expression is EXACTLY ONE const-shaped instruction: a {i32,i64,f32,f64}.const
    or a global.get of an imported immutable global. Anything else (empty, multi-instruction,
    non-const op, defined/mutable global reference) -> "constant expression required".
    A const of the WRONG TYPE for the context -> "type mismatch"."""
    if len(expr) != 1:
        raise ValidationError("constant expression required")
    ins = expr[0]
    if ins.op in _CONST_OPS:
        t = _CONST_OPS[ins.op]
    elif ins.op == "global.get":
        if ins.imm >= m.n_globals():
            raise ValidationError("unknown global")
        if ins.imm >= n_imported_globals:
            raise ValidationError("constant expression required")
        t, mutable = m.global_type(ins.imm)
        if mutable:
            raise ValidationError("constant expression required")
    else:
        raise ValidationError("constant expression required")
    if t != expected:
        raise ValidationError(TYPE_MISMATCH)


def validate_module(m: Module) -> None:
    """Validate a decoded module; raises ValidationError on the first violation."""
    # import descriptors
    for im in m.imports:
        if im.kind == "func":
            if im.desc >= len(m.types):
                raise ValidationError("unknown type")
        elif im.kind == "table":
            _check_table_limits(im.desc[1])
        elif im.kind == "memory":
            _check_mem_limits(im.desc)
    # defined function type indices (decoder defers this range check to us)
    for tidx in m.func_typeidx:
        if tidx >= len(m.types):
            raise ValidationError("unknown type")
    # defined tables / memories
    for _elemtype, limits in m.tables:
        _check_table_limits(limits)
    for limits in m.mems:
        _check_mem_limits(limits)
    # global init expressions (context: imported globals only)
    n_imported_globals = len(m.imported("global"))
    for g in m.globals:
        _validate_const_expr(m, g.init, g.valtype, n_imported_globals)
    # function bodies
    for func in m.funcs:
        _FuncValidator(m, func).validate()
    # element segments
    for seg in m.elems:
        if m.n_tables() == 0:
            raise ValidationError("unknown table")
        _validate_const_expr(m, seg.offset, "i32", n_imported_globals)
        for fi in seg.funcidxs:
            if fi >= m.n_funcs():
                raise ValidationError("unknown function")
    # data segments
    for seg in m.datas:
        if m.n_mems() == 0:
            raise ValidationError("unknown memory")
        _validate_const_expr(m, seg.offset, "i32", n_imported_globals)
    # start function
    if m.start is not None:
        if m.start >= m.n_funcs():
            raise ValidationError("unknown function")
        ft = m.func_type(m.start)
        if ft.params or ft.results:
            raise ValidationError("start function")
    # exports
    seen: set[str] = set()
    for ex in m.exports:
        if ex.name in seen:
            raise ValidationError("duplicate export name")
        seen.add(ex.name)
        if ex.kind == "func":
            if ex.idx >= m.n_funcs():
                raise ValidationError("unknown function")
        elif ex.kind == "table":
            if ex.idx >= m.n_tables():
                raise ValidationError("unknown table")
        elif ex.kind == "memory":
            if ex.idx >= m.n_mems():
                raise ValidationError("unknown memory")
        elif ex.kind == "global":
            if ex.idx >= m.n_globals():
                raise ValidationError("unknown global")
