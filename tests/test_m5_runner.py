#!/usr/bin/env python3
"""Unit tests for scripts/run_m5.py _Runner chaining semantics (review fixes).

No toolchain or converted corpus needed: tiny modules are hand-assembled with the builder
from test_m5_machine and driven through _Runner with synthetic spec-JSON command dicts.

Frozen semantics under test:
  - a module counted FAIL poisons later commands against it as FAIL, never UNSUPPORTED
    (UNSUPPORTED stays reserved for out-of-surface chains);
  - a module counted UNSUPPORTED keeps chaining UNSUPPORTED;
  - a failed redefinition of a script name DROPS the stale older instance.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_m5 as R                        # noqa: E402
from test_m5_machine import (             # noqa: E402
    I32, code_entry, functype, module, name, section, uleb, vec)

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL {label}")


# module binaries -----------------------------------------------------------------------------

# valid: export "f": () -> i32 = 42
GOOD = module(
    section(1, vec([functype([], [I32])])),
    section(3, vec([uleb(0)])),
    section(7, vec([name("f") + b"\x00" + uleb(0)])),
    section(10, vec([code_entry([], bytes([0x41, 42, 0x0B]))])),
)
# validator-rejected (oracle-valid modules never are -> counted FAIL): body type mismatch
BAD = module(
    section(1, vec([functype([], [I32])])),
    section(3, vec([uleb(0)])),
    section(7, vec([name("f") + b"\x00" + uleb(0)])),
    section(10, vec([code_entry([], bytes([0x0B]))])),      # returns nothing, declares i32
)
# out-of-surface: imports an unregistered module -> LinkError -> UNSUPPORTED
UNSUP = module(
    section(1, vec([functype([], [I32])])),
    section(2, vec([name("nowhere") + name("f") + b"\x00" + uleb(0)])),
)

RET42 = [{"type": "i32", "value": "42"}]


def new_runner(tmp: Path) -> R._Runner:
    (tmp / "good.wasm").write_bytes(GOOD)
    (tmp / "bad.wasm").write_bytes(BAD)
    (tmp / "unsup.wasm").write_bytes(UNSUP)
    return R._Runner(tmp, R.FileResult("synthetic"))


def invoke_f(mod: str | None = None) -> dict:
    a = {"type": "invoke", "field": "f", "args": []}
    if mod:
        a["module"] = mod
    return {"type": "assert_return", "line": 99, "action": a, "expected": RET42}


def test_fail_chain(tmp: Path) -> None:
    print("[FAIL module poisons later commands as FAIL]")
    r = new_runner(tmp)
    r.cmd_module({"type": "module", "line": 1, "filename": "bad.wasm"})
    fr = r.fr
    check((fr.failed, fr.unsupported) == (1, 0), "validator-rejected module counted FAIL")
    r.cmd_assert_return(invoke_f())
    check((fr.failed, fr.unsupported) == (2, 0),
          "assert_return after FAIL module is FAIL, not UNSUPPORTED")
    r.cmd_register({"type": "register", "line": 3, "as": "X"})
    check((fr.failed, fr.unsupported, fr.registered) == (3, 0, 0),
          "register after FAIL module is FAIL, not UNSUPPORTED")


def test_unsupported_chain(tmp: Path) -> None:
    print("[UNSUPPORTED module keeps chaining UNSUPPORTED]")
    r = new_runner(tmp)
    r.cmd_module({"type": "module", "line": 1, "filename": "unsup.wasm"})
    fr = r.fr
    check((fr.failed, fr.unsupported) == (0, 1), "unresolvable-import module UNSUPPORTED")
    r.cmd_assert_return(invoke_f())
    check((fr.failed, fr.unsupported) == (0, 2), "chained assert stays UNSUPPORTED")
    r.cmd_register({"type": "register", "line": 3, "as": "X"})
    check((fr.failed, fr.unsupported) == (0, 3), "chained register stays UNSUPPORTED")


def test_stale_name_dropped(tmp: Path) -> None:
    print("[failed redefinition drops the stale named instance]")
    r = new_runner(tmp)
    fr = r.fr
    r.cmd_module({"type": "module", "line": 1, "filename": "good.wasm", "name": "$m"})
    r.cmd_assert_return(invoke_f("$m"))
    check((fr.modules_ok, fr.passed, fr.failed) == (1, 1, 0), "named module works pre-redef")
    r.cmd_module({"type": "module", "line": 2, "filename": "bad.wasm", "name": "$m"})
    check("$m" not in r.named, "stale instance dropped on failed redefinition")
    r.cmd_assert_return(invoke_f("$m"))
    check((fr.passed, fr.failed) == (1, 2),
          "invoke on failed redefinition is FAIL, not a stale-instance PASS")
    # a later SUCCESSFUL redefinition brings the name back and clears the chain reason
    r.cmd_module({"type": "module", "line": 3, "filename": "good.wasm", "name": "$m"})
    r.cmd_assert_return(invoke_f("$m"))
    check((fr.modules_ok, fr.passed) == (2, 2), "successful redefinition is live again")
    check("$m" not in r.named_reason, "stale chain reason cleared on success")


def test_accounting_identity(tmp: Path) -> None:
    print("[accounting identity holds across chained buckets]")
    r = new_runner(tmp)
    fr = r.fr
    cmds = [
        {"type": "module", "line": 1, "filename": "bad.wasm"},
        invoke_f(),
        {"type": "module", "line": 3, "filename": "unsup.wasm"},
        invoke_f(),
        {"type": "module", "line": 5, "filename": "good.wasm"},
        invoke_f(),
    ]
    fr.total = len(cmds)
    for c in cmds:
        if c["type"] == "module":
            r.cmd_module(c)
        else:
            r.cmd_assert_return(c)
    lhs = fr.modules_ok + fr.registered + fr.actions_ok + fr.passed + fr.failed + fr.unsupported
    check(lhs == fr.total, f"identity {lhs} == {fr.total}")
    check((fr.modules_ok, fr.passed, fr.failed, fr.unsupported) == (1, 1, 2, 2),
          "buckets land exactly as designed (1 mod, 1 PASS, 2 FAIL, 2 UNSUP)")


def test_register_alias_cleanup(tmp: Path) -> None:
    print("[failed re-register drops the stale alias]")
    r = new_runner(tmp)
    fr = r.fr
    r.cmd_module({"type": "module", "line": 1, "filename": "good.wasm"})
    r.cmd_register({"type": "register", "line": 2, "as": "X"})
    check((fr.registered, "X" in r.store.registered) == (1, True), "alias X registered")
    r.cmd_module({"type": "module", "line": 3, "filename": "bad.wasm"})
    r.cmd_register({"type": "register", "line": 4, "as": "X"})
    check("X" not in r.store.registered,
          "failed re-register drops stale alias (no import can bind the old instance)")
    check((fr.registered, fr.failed) == (1, 2),
          "re-register after FAIL module counted FAIL (bad module + register)")


def test_partial_run_is_labeled(tmp: Path) -> None:
    """Review fix: a --json targeted run must not overwrite the canonical full-sweep
    summary, and must label itself PARTIAL."""
    print("[targeted --json run writes a labeled partial report]")
    (tmp / "good.wasm").write_bytes(GOOD)
    synth = {"source_filename": "synthetic.wast",
             "commands": [{"type": "module", "line": 1, "filename": "good.wasm"},
                          invoke_f()]}
    jp = tmp / "synthetic.json"
    jp.write_text(__import__("json").dumps(synth), encoding="utf-8")

    canonical = R.REPORT / "m5_summary.json"
    before = canonical.read_bytes() if canonical.exists() else None
    argv = sys.argv
    try:
        sys.argv = ["run_m5.py", "--json", str(jp)]
        rc = R.main()
    finally:
        sys.argv = argv
    check(rc == 0, "targeted run exits 0")
    after = canonical.read_bytes() if canonical.exists() else None
    check(before == after, "canonical m5_summary.json untouched by targeted run")
    partial = R.REPORT / "m5_summary_partial.json"
    check(partial.exists(), "m5_summary_partial.json written")
    if partial.exists():
        pj = __import__("json").loads(partial.read_text(encoding="utf-8"))
        check(pj["scope"].startswith("PARTIAL targeted run"),
              f"partial scope labeled: {pj['scope'][:60]!r}")
        check(pj["totals"]["total"] == 2, "partial totals cover only the targeted file")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_fail_chain(tmp)
        test_unsupported_chain(tmp)
        test_stale_name_dropped(tmp)
        test_accounting_identity(tmp)
        test_register_alias_cleanup(tmp)
        test_partial_run_is_labeled(tmp)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nall runner chaining tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
