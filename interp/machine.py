"""machine.py — the integer interpreter (M1 core + M2 structured control flow).

Executes a decoded Module's function bodies over an i32/i64 value stack, with EXACT integer
semantics per the WebAssembly spec (stable, not time-sensitive): wrapping arithmetic mod 2**N;
shift counts masked mod 32/64; arithmetic vs logical shr; rotl/rotr; clz/ctz/popcnt; rem_s sign
following the dividend; eqz and comparisons yielding i32 0/1; wrap/extend ops; and the integer
traps. There are exactly two trap texts: "integer divide by zero" (from div_s / div_u / rem_s /
rem_u by zero) and "integer overflow" (from signed division div_s of INT_MIN by -1). Signed
remainder rem_s of INT_MIN by -1 does NOT trap — it yields 0.

M2 adds STRUCTURED CONTROL FLOW (block / loop / if / else / br / br_if / br_table / return / drop /
nop) plus local.set. The decoder keeps the body FLAT; here `_structure` parses it into a nested
block tree and `_exec_seq` / `_exec_block` evaluate it over a value stack + a label stack. `br l`
targets the l-th enclosing label (0 = innermost): a block/if target transfers PAST its `end`, a
loop target to the loop HEADER (re-entry); `return` escapes all blocks. Integer opcode semantics
are unchanged from M1. Any opcode outside the decoded scope cannot appear (the decoder rejects it).
"""
from __future__ import annotations

from . import values as V
from .decoder import Module, Instr, Unsupported

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
    seq = _structure(func.body)          # flat body -> nested block tree (structured control flow)
    nres = len(ftype.results)
    stack: list[int] = []
    # The function body is an implicit block whose label carries the function's results: a `br` to
    # the outermost depth (directly, or from within nested blocks) branches to the function end —
    # the same observable effect as `return`. Seed that label so such branches resolve instead of
    # indexing off the label stack.
    try:
        _exec_seq(seq, stack, locals_, [_Label("func", nres, 0)])
    except _Return:
        pass
    except _Branch:
        pass                             # branch to the function label: results already arranged
    if len(stack) < nres:
        raise Trap("stack underflow producing results")  # structurally impossible for valid modules
    return stack[len(stack) - nres:]


def _wbits(valtype: str) -> int:
    return 32 if valtype == "i32" else 64


# ---- Structured control flow (M2). Parse the flat instruction stream into a nested block tree,
# then evaluate recursively with a value stack + a label stack. `br l` targets the l-th enclosing
# label (0 = INNERMOST): for a block/if, transfer PAST its `end`; for a loop, transfer to the loop
# HEADER (re-entry). Integer ops below are byte-for-byte the M1 semantics. ----

class _Block:
    """A structured block parsed from the flat body. kind in {block, loop, if}; `results` are the
    block-type result value-types (0 or 1 in M2 scope); for `if`, `else_seq` is the else-branch
    (None when the source had no `else`)."""
    __slots__ = ("kind", "results", "then_seq", "else_seq")

    def __init__(self, kind: str, results: list[str], then_seq: list, else_seq):
        self.kind = kind
        self.results = results
        self.then_seq = then_seq
        self.else_seq = else_seq


class _Label:
    """An active control label. `branch_arity` = how many operand values a `br` to this label
    carries: the block/if RESULT arity, or the loop PARAM arity (0 in M2 — MVP loops take no
    params). `base` = the value-stack height at block entry."""
    __slots__ = ("kind", "branch_arity", "base")

    def __init__(self, kind: str, branch_arity: int, base: int):
        self.kind = kind
        self.branch_arity = branch_arity
        self.base = base


class _Branch(Exception):
    """Unwind signal for br / br_if / br_table. `depth` = label levels still to unwind."""
    def __init__(self, depth: int):
        super().__init__(depth)
        self.depth = depth


class _Return(Exception):
    """Unwind signal for the `return` opcode (escape all enclosing blocks)."""


def _structure(body: list[Instr]) -> list:
    """Flat instruction list (with block/loop/if/else/end tokens) -> nested sequence of plain Instr
    and _Block nodes. The function body's terminating `end` closes the top sequence."""
    seq, i, stop = _parse_seq(body, 0)
    if stop != "end" or i != len(body):
        raise Unsupported(f"malformed function body (stopped on {stop!r} at {i}/{len(body)})")
    return seq


def _parse_seq(body: list[Instr], i: int) -> tuple[list, int, str]:
    """Parse instructions into a sequence until the matching `end` (consumed) or an `else` (NOT
    consumed). Returns (seq, next_index, stop) where stop is "end" or "else". The stop flag is what
    tells `_parse_block` whether an `else` it lands on is its OWN: a token-only lookahead would let
    an else-less inner `if` (whose then-branch closed on its own `end`) wrongly claim the following
    `else` that actually belongs to an enclosing `if`."""
    seq: list = []
    n = len(body)
    while i < n:
        op = body[i].op
        if op == "end":
            return seq, i + 1, "end"
        if op == "else":
            return seq, i, "else"                # not consumed; the enclosing `if` owns it
        if op in ("block", "loop", "if"):
            blk, i = _parse_block(body, i)
            seq.append(blk)
        else:
            seq.append(body[i])
            i += 1
    raise Unsupported("unterminated block or function body (missing end)")  # unreachable: body ends in `end`


def _parse_block(body: list[Instr], i: int) -> tuple[_Block, int]:
    opener = body[i]
    kind = opener.op
    then_seq, i, stop = _parse_seq(body, i + 1)   # stops after THIS block's `end`, or at an `else`
    else_seq = None
    if stop == "else":
        # The `else` is only owned by the `if` whose then-branch we just parsed. If the then-branch
        # instead closed on its own `end` (stop == "end"), a following `else` belongs to an ENCLOSING
        # `if` and must not be consumed here.
        if kind != "if":
            raise Unsupported(f"`else` inside a {kind}, not an if")
        else_seq, i, stop2 = _parse_seq(body, i + 1)   # consume `else`, parse to the if's `end`
        if stop2 != "end":
            raise Unsupported("malformed if: else-branch not terminated by end")
    return _Block(kind, opener.bt or [], then_seq, else_seq), i


def _exec_seq(seq: list, stack: list[int], locals_: list[int], labels: list) -> None:
    for item in seq:
        if type(item) is _Block:
            _exec_block(item, stack, locals_, labels)
        else:
            _exec_instr(item, stack, locals_, labels)


def _exec_block(blk: _Block, stack: list[int], locals_: list[int], labels: list) -> None:
    if blk.kind == "if":
        cond = stack.pop() & V.MASK32
        base = len(stack)
        chosen = blk.then_seq if cond != 0 else (blk.else_seq or [])
        try:                                                 # `if` branches like a block (to end)
            _exec_seq(chosen, stack, locals_, labels + [_Label("block", len(blk.results), base)])
        except _Branch as b:
            if b.depth:
                raise _Branch(b.depth - 1)
        return
    if blk.kind == "loop":
        base = len(stack)
        label = _Label("loop", 0, base)                      # br to a loop carries 0 params, re-enters
        while True:
            try:
                _exec_seq(blk.then_seq, stack, locals_, labels + [label])
                return                                       # normal completion exits the loop
            except _Branch as b:
                if b.depth:
                    raise _Branch(b.depth - 1)
                # depth 0 -> branch to loop header: _do_br already reset the stack to base; re-enter
    else:  # block
        base = len(stack)
        try:
            _exec_seq(blk.then_seq, stack, locals_, labels + [_Label("block", len(blk.results), base)])
        except _Branch as b:
            if b.depth:
                raise _Branch(b.depth - 1)


def _do_br(depth: int, stack: list[int], labels: list) -> None:
    """Branch `depth` label levels out. Carry the target label's branch_arity operand values,
    unwind the value stack to the target's base, push the carried values, then raise _Branch."""
    tgt = labels[len(labels) - 1 - depth]
    n = tgt.branch_arity
    vals = stack[len(stack) - n:] if n else []
    del stack[tgt.base:]
    stack.extend(vals)
    raise _Branch(depth)


def _exec_instr(ins: Instr, stack: list[int], locals_: list[int], labels: list) -> None:
    op = ins.op
    push, pop = stack.append, stack.pop
    if op == "nop":
        return
    if op == "drop":
        pop(); return
    if op == "local.get":
        push(locals_[ins.imm]); return
    if op == "local.set":
        locals_[ins.imm] = pop(); return
    if op == "i32.const":
        push(ins.imm & V.MASK32); return
    if op == "i64.const":
        push(ins.imm & V.MASK64); return
    if op == "return":
        raise _Return()
    if op == "br":
        _do_br(ins.imm, stack, labels); return
    if op == "br_if":
        if (pop() & V.MASK32) != 0:
            _do_br(ins.imm, stack, labels)
        return
    if op == "br_table":
        idx = pop() & V.MASK32                               # index is i32, interpreted unsigned
        tgt = ins.targets[idx] if idx < len(ins.targets) else ins.default
        _do_br(tgt, stack, labels); return
    # integer numeric ops — identical semantics to M1
    kind, _, rest = op.partition(".")
    bits = 32 if kind == "i32" else 64
    if rest in _BINOP:
        b = pop(); a = pop(); push(_binop(bits, rest, a, b))
    elif rest in _CMP:
        b = pop(); a = pop(); push(_compare(bits, rest, a, b))
    elif rest == "eqz":
        push(1 if (pop() & V.mask(bits)) == 0 else 0)
    elif rest in _UNOP:
        push(_unop(bits, rest, pop()))
    else:
        push(_convert(op, pop()))


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
