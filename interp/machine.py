"""machine.py — the M1 integer interpreter.

Executes a decoded Module's function bodies over an i32/i64 value stack, with EXACT integer
semantics per the WebAssembly spec (stable, not time-sensitive): wrapping arithmetic mod 2**N;
shift counts masked mod 32/64; arithmetic vs logical shr; rotl/rotr; clz/ctz/popcnt; rem_s sign
following the dividend; eqz and comparisons yielding i32 0/1; wrap/extend ops; and the integer
traps. There are exactly two trap texts: "integer divide by zero" (from div_s / div_u / rem_s /
rem_u by zero) and "integer overflow" (from signed division div_s of INT_MIN by -1). Signed
remainder rem_s of INT_MIN by -1 does NOT trap — it yields 0.

M1 scope is a straight-line integer core: the enumerated opcode set contains NO structured
control flow (block/loop/if/br*), so a function body is executed as a linear instruction
sequence; `return` and the body-terminating `end` both finish the call, yielding the top
`len(results)` stack values. Any opcode outside the decoded scope cannot appear (the decoder
rejects it); a defensive KeyError-style guard still raises Unsupported.
"""
from __future__ import annotations

from . import values as V
from .decoder import Module, Func, Instr, Unsupported

# opcodes whose result is an i32 boolean (0/1) regardless of operand width
_CMP = {"eq", "ne", "lt_s", "lt_u", "gt_s", "gt_u", "le_s", "le_u", "ge_s", "ge_u"}
_BINOP = {"add", "sub", "mul", "div_s", "div_u", "rem_s", "rem_u",
          "and", "or", "xor", "shl", "shr_s", "shr_u", "rotl", "rotr"}
_UNOP = {"clz", "ctz", "popcnt"}


class Trap(Exception):
    """A WebAssembly trap. `kind` is the canonical spec text used for assert_trap matching."""
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


DIV_ZERO = "integer divide by zero"
OVERFLOW = "integer overflow"


def instantiate(module: Module) -> Module:
    """M1 modules have no start function, imports, globals, memory, or data — instantiation is
    just the decoded module. Kept as a named seam for later milestones."""
    return module


def invoke(module: Module, field_name: str, args: list[int]) -> list[int]:
    """Invoke an exported function by name with unsigned-canonical integer args; return its
    result values (unsigned canonical). Raises Trap on a trapping op, Unsupported out of scope,
    KeyError if the export is absent (caller classifies that)."""
    funcidx = module.exports[field_name]
    func = module.funcs[funcidx]
    ftype = module.types[func.typeidx]
    if len(args) != len(ftype.params):
        raise ValueError(f"arity: {field_name} expects {len(ftype.params)} args, got {len(args)}")
    # locals = params (masked to declared width) ++ declared locals (zero-initialized)
    locals_: list[int] = [V.to_unsigned(_wbits(pt), a) for pt, a in zip(ftype.params, args)]
    locals_ += [0] * len(func.local_types)
    stack = _run(func, locals_)
    nres = len(ftype.results)
    if len(stack) < nres:
        raise Trap("stack underflow producing results")  # structurally impossible for valid modules
    return stack[len(stack) - nres:]


def _wbits(valtype: str) -> int:
    return 32 if valtype == "i32" else 64


def _run(func: Func, locals_: list[int]) -> list[int]:
    stack: list[int] = []
    push, pop = stack.append, stack.pop
    for ins in func.body:
        op = ins.op
        if op == "end" or op == "return":
            break
        elif op == "local.get":
            push(locals_[ins.imm])
        elif op == "i32.const":
            push(ins.imm & V.MASK32)
        elif op == "i64.const":
            push(ins.imm & V.MASK64)
        else:
            kind, _, rest = op.partition(".")     # "i32", ".", "add"
            bits = 32 if kind == "i32" else 64
            if rest in _BINOP:
                b = pop(); a = pop()
                push(_binop(bits, rest, a, b))
            elif rest in _CMP:
                b = pop(); a = pop()
                push(_compare(bits, rest, a, b))
            elif rest == "eqz":
                push(1 if (pop() & V.mask(bits)) == 0 else 0)
            elif rest in _UNOP:
                push(_unop(bits, rest, pop()))
            else:
                push(_convert(op, pop()))
    return stack


def _binop(bits: int, op: str, a: int, b: int) -> int:
    m = V.mask(bits)
    a &= m; b &= m
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
        return V.to_unsigned(bits, V.to_signed(bits, a) >> (b % bits))
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
        sa, sb = V.to_signed(bits, a), V.to_signed(bits, b)
        if sb == 0:
            raise Trap(DIV_ZERO)
        if sa == -(1 << (bits - 1)) and sb == -1:
            raise Trap(OVERFLOW)
        return V.to_unsigned(bits, V.trunc_div(sa, sb))
    if op == "rem_s":
        sa, sb = V.to_signed(bits, a), V.to_signed(bits, b)
        if sb == 0:
            raise Trap(DIV_ZERO)
        # rem_s does NOT trap on INT_MIN % -1 — the result is 0 (abs(sb)==1).
        r = abs(sa) % abs(sb)
        return V.to_unsigned(bits, -r if sa < 0 else r)
    raise Unsupported(f"binop {op}")


def _compare(bits: int, op: str, a: int, b: int) -> int:
    m = V.mask(bits)
    a &= m; b &= m
    sa, sb = V.to_signed(bits, a), V.to_signed(bits, b)
    res = {
        "eq": a == b, "ne": a != b,
        "lt_u": a < b, "gt_u": a > b, "le_u": a <= b, "ge_u": a >= b,
        "lt_s": sa < sb, "gt_s": sa > sb, "le_s": sa <= sb, "ge_s": sa >= sb,
    }[op]
    return 1 if res else 0


def _unop(bits: int, op: str, a: int) -> int:
    if op == "clz":
        return V.clz(bits, a)
    if op == "ctz":
        return V.ctz(bits, a)
    if op == "popcnt":
        return V.popcnt(bits, a)
    raise Unsupported(f"unop {op}")


def _convert(op: str, a: int) -> int:
    if op == "i32.wrap_i64":
        return a & V.MASK32
    if op == "i64.extend_i32_s":
        return V.to_unsigned(64, V.to_signed(32, a))
    if op == "i64.extend_i32_u":
        return a & V.MASK32
    if op == "i32.extend8_s":
        return V.to_unsigned(32, V.to_signed(8, a))
    if op == "i32.extend16_s":
        return V.to_unsigned(32, V.to_signed(16, a))
    if op == "i64.extend8_s":
        return V.to_unsigned(64, V.to_signed(8, a))
    if op == "i64.extend16_s":
        return V.to_unsigned(64, V.to_signed(16, a))
    if op == "i64.extend32_s":
        return V.to_unsigned(64, V.to_signed(32, a))
    raise Unsupported(f"convert {op}")
