"""Validation for the curation-bounded M4 slice.

This module validates decoded modules over the existing M1-M3 execution
surface only. It does not decode or admit new WebAssembly features; deferred
features remain outside the M4 runner by scope.json policy and by the decoder's
fail-closed Unsupported paths.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import decoder as dec

TYPE_MISMATCH = "validation_type_mismatch_existing_surface"
UNKNOWN_LABEL = "branch_depth_or_unknown_label_existing_surface"
MEMORY32_MAX_PAGES = 65536


class ValidationError(Exception):
    """A validation rejection categorized for the M4 curation policy."""

    def __init__(self, category: str, message: str, text: str | None = None):
        super().__init__(message)
        self.category = category
        self.text = text or ("unknown label" if category == UNKNOWN_LABEL else "type mismatch")


@dataclass
class _Control:
    kind: str
    start_types: list[str]
    end_types: list[str]
    height: int
    unreachable: bool = False
    else_seen: bool = False


class _FuncValidator:
    def __init__(self, module: dec.Module, func: dec.Func):
        self.module = module
        self.func = func
        ftype = module.types[func.typeidx]
        self.locals = list(ftype.params) + list(func.local_types)
        self.stack: list[str] = []
        self.ctrls: list[_Control] = [_Control("func", [], list(ftype.results), 0)]

    def validate(self) -> None:
        for ins in self.func.body:
            if not self.ctrls:
                self._type_error("instruction after function end")
            self._instr(ins)
        if self.ctrls:
            self._type_error("function body missing final end")
        ftype = self.module.types[self.func.typeidx]
        if self.stack != list(ftype.results):
            self._type_error("function result stack mismatch")

    def _type_error(self, message: str) -> None:
        raise ValidationError(TYPE_MISMATCH, message)

    def _unknown_label(self, depth: int) -> None:
        raise ValidationError(UNKNOWN_LABEL, f"unknown label depth {depth}")

    @property
    def _ctrl(self) -> _Control:
        return self.ctrls[-1]

    def _pop(self, want: str | None = None) -> str:
        if len(self.stack) == self._ctrl.height and self._ctrl.unreachable:
            return want or "i32"
        if len(self.stack) <= self._ctrl.height:
            self._type_error(f"operand stack underflow, expected {want or 'value'}")
        got = self.stack.pop()
        if want is not None and got != want:
            self._type_error(f"expected {want}, got {got}")
        return got

    def _pop_many(self, types: list[str]) -> None:
        for t in reversed(types):
            self._pop(t)

    def _push_many(self, types: list[str]) -> None:
        self.stack.extend(types)

    def _unreachable(self) -> None:
        del self.stack[self._ctrl.height:]
        self._ctrl.unreachable = True

    def _label_types(self, depth: int) -> list[str]:
        if depth < 0 or depth >= len(self.ctrls):
            self._unknown_label(depth)
        frame = self.ctrls[len(self.ctrls) - 1 - depth]
        if frame.kind == "loop":
            return list(frame.start_types)
        return list(frame.end_types)

    def _end_frame(self) -> None:
        frame = self._ctrl
        if frame.kind == "if" and frame.end_types and not frame.else_seen:
            self._type_error("if with result type requires else branch")
        self._pop_many(frame.end_types)
        if len(self.stack) != frame.height:
            self._type_error(f"{frame.kind} left extra stack values")
        self.ctrls.pop()
        del self.stack[frame.height:]
        self._push_many(frame.end_types)

    def _else(self) -> None:
        frame = self._ctrl
        if frame.kind != "if" or frame.else_seen:
            self._type_error("else without matching if")
        self._pop_many(frame.end_types)
        if len(self.stack) != frame.height:
            self._type_error("if then-branch left extra stack values")
        del self.stack[frame.height:]
        frame.unreachable = False
        frame.else_seen = True

    def _branch(self, depth: int) -> None:
        label_types = self._label_types(depth)
        self._pop_many(label_types)
        self._unreachable()

    def _branch_if(self, depth: int) -> None:
        self._pop("i32")
        label_types = self._label_types(depth)
        self._pop_many(label_types)
        self._push_many(label_types)

    def _branch_table(self, targets: list[int], default: int) -> None:
        all_depths = list(targets) + [default]
        all_types = [self._label_types(d) for d in all_depths]
        want = all_types[-1]
        for got in all_types[:-1]:
            if got != want:
                self._type_error("br_table target label types differ")
        self._pop("i32")
        self._pop_many(want)
        self._unreachable()

    def _return(self) -> None:
        result_types = self.ctrls[0].end_types
        self._pop_many(result_types)
        self._unreachable()

    def _require_memory(self, op: str) -> None:
        if not self.module.mems:
            self._type_error(f"{op} requires a memory")

    def _instr(self, ins: dec.Instr) -> None:
        op = ins.op
        if op == "end":
            self._end_frame()
            return
        if op == "else":
            self._else()
            return
        if op == "nop":
            return
        if op == "drop":
            self._pop()
            return
        if op == "block":
            self.ctrls.append(_Control("block", [], list(ins.bt or []), len(self.stack)))
            return
        if op == "loop":
            self.ctrls.append(_Control("loop", [], list(ins.bt or []), len(self.stack)))
            return
        if op == "if":
            self._pop("i32")
            self.ctrls.append(_Control("if", [], list(ins.bt or []), len(self.stack)))
            return
        if op == "br":
            self._branch(ins.imm)
            return
        if op == "br_if":
            self._branch_if(ins.imm)
            return
        if op == "br_table":
            self._branch_table(ins.targets or [], ins.default)
            return
        if op == "return":
            self._return()
            return
        if op == "local.get":
            if ins.imm is None or ins.imm >= len(self.locals):
                self._type_error(f"unknown local {ins.imm}")
            self.stack.append(self.locals[ins.imm])
            return
        if op == "local.set":
            if ins.imm is None or ins.imm >= len(self.locals):
                self._type_error(f"unknown local {ins.imm}")
            self._pop(self.locals[ins.imm])
            return
        if op == "i32.const":
            self.stack.append("i32")
            return
        if op == "i64.const":
            self.stack.append("i64")
            return
        if op == "i32.store":
            self._require_memory(op)
            if ins.align is None or ins.align > 2:
                self._type_error("i32.store alignment exceeds natural alignment")
            self._pop("i32")
            self._pop("i32")
            return
        if op == "memory.size":
            self._require_memory(op)
            self.stack.append("i32")
            return
        if op == "memory.grow":
            self._require_memory(op)
            self._pop("i32")
            self.stack.append("i32")
            return
        self._numeric(op)

    def _numeric(self, op: str) -> None:
        if op == "i32.eqz":
            self._pop("i32")
            self.stack.append("i32")
            return
        if op == "i64.eqz":
            self._pop("i64")
            self.stack.append("i32")
            return
        if op == "i32.wrap_i64":
            self._pop("i64")
            self.stack.append("i32")
            return
        if op in ("i64.extend_i32_s", "i64.extend_i32_u"):
            self._pop("i32")
            self.stack.append("i64")
            return
        if op in ("i32.extend8_s", "i32.extend16_s"):
            self._pop("i32")
            self.stack.append("i32")
            return
        if op in ("i64.extend8_s", "i64.extend16_s", "i64.extend32_s"):
            self._pop("i64")
            self.stack.append("i64")
            return
        kind, _, rest = op.partition(".")
        if kind not in ("i32", "i64"):
            self._type_error(f"unsupported validator opcode {op}")
        vt = kind
        if rest in {"clz", "ctz", "popcnt"}:
            self._pop(vt)
            self.stack.append(vt)
            return
        if rest in {
            "add", "sub", "mul", "div_s", "div_u", "rem_s", "rem_u",
            "and", "or", "xor", "shl", "shr_s", "shr_u", "rotl", "rotr",
        }:
            self._pop(vt)
            self._pop(vt)
            self.stack.append(vt)
            return
        if rest in {"eq", "ne", "lt_s", "lt_u", "gt_s", "gt_u", "le_s", "le_u", "ge_s", "ge_u"}:
            self._pop(vt)
            self._pop(vt)
            self.stack.append("i32")
            return
        self._type_error(f"unsupported validator opcode {op}")


def validate_module(module: dec.Module) -> None:
    """Validate a decoded module or raise ValidationError."""
    for i, typeidx in enumerate(module.func_typeidx):
        if typeidx >= len(module.types):
            raise ValidationError(TYPE_MISMATCH, f"function {i} type index {typeidx} out of range")
    for name, funcidx in module.exports.items():
        if funcidx >= len(module.funcs):
            raise ValidationError(TYPE_MISMATCH, f"export {name!r} function index {funcidx} out of range")
    if len(module.mems) > 1:
        raise ValidationError(TYPE_MISMATCH, "more than one memory")
    for idx, (minimum, maximum) in enumerate(module.mems):
        if minimum < 0 or minimum > MEMORY32_MAX_PAGES:
            raise ValidationError(TYPE_MISMATCH, f"memory {idx} minimum out of bounds")
        if maximum is not None and (maximum < minimum or maximum > MEMORY32_MAX_PAGES):
            raise ValidationError(TYPE_MISMATCH, f"memory {idx} maximum out of bounds")
    for func in module.funcs:
        _FuncValidator(module, func).validate()


def validate_bytes(data: bytes) -> None:
    """Decode and validate bytes in the current M1-M3 binary surface."""
    _reject_duplicate_export_names(data)
    validate_module(dec.decode(data))


def _reject_duplicate_export_names(data: bytes) -> None:
    r = dec._Reader(data)
    if r.bytes(4) != b"\x00asm":
        raise dec.DecodeError("bad magic (not a WASM binary)")
    version = int.from_bytes(r.bytes(4), "little")
    if version != 1:
        return
    while not r.eof():
        sec_id = r.byte()
        sec_len = r.uleb()
        sec_end = r.p + sec_len
        if sec_id == dec.SEC_EXPORT:
            seen: set[str] = set()
            for _ in range(r.uleb()):
                name = r.bytes(r.uleb()).decode("utf-8")
                if name in seen:
                    raise ValidationError(TYPE_MISMATCH, f"duplicate export name {name!r}")
                seen.add(name)
                r.byte()
                r.uleb()
        else:
            r.bytes(sec_len)
        if r.p != sec_end:
            raise dec.DecodeError(f"section {sec_id} length mismatch")
