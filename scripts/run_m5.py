#!/usr/bin/env python3
"""run_m5.py — the M5 full-sweep assert-runner: every command of every converted test/core
file, classified against the frozen oracle. FAIL does not gate: every deviation is recorded
with detail and the run continues (deviation is data). Exit code 0 means the run COMPLETED
with intact accounting — NOT that FAIL==0; the account is the deliverable.

Buckets (per-file accounting identity, asserted):

    modules_ok + registered + actions_ok + PASS + FAIL + UNSUPPORTED == total commands

  module               decode -> VALIDATE -> instantiate. Success = modules_ok. A validator
                       rejection of a corpus module is a FAIL (the 605 oracle-valid modules
                       are the validator's standing negative control). A decoder DecodeError
                       on one is a FAIL. Unsupported / unresolvable import = UNSUPPORTED.
  register             names a live instance for later imports. Missing because the module
                       was UNSUPPORTED -> UNSUPPORTED (chained); otherwise missing -> FAIL.
  action               bare invoke for side effects: ok -> actions_ok, trap/error -> FAIL,
                       out-of-surface -> UNSUPPORTED.
  assert_return        bitwise compare (i32/i64/f32/f64) incl. nan:canonical/nan:arithmetic
                       class checks and multi-value arity.
  assert_trap /        must trap with EXACTLY the oracle's text.
  assert_exhaustion
  assert_invalid       binary: decoder must accept, validator must reject with EXACTLY the
                       oracle's text (accept = FAIL, wrong text = FAIL). text-format module:
                       UNSUPPORTED (no .wat parser at this milestone).
  assert_malformed     binary: decoder must reject with EXACTLY the oracle's text.
                       text-format module: UNSUPPORTED.
  assert_uninstantiable decode+validate must accept; instantiation must trap with the text.
  assert_unlinkable    none in this corpus; explicitly classified UNSUPPORTED if ever seen.
  anything else        UNSUPPORTED "unhandled command type: X" — never silently dropped.

The oracle is embedded and frozen: expected values/texts in the converted JSON were authored
by the WebAssembly spec reference interpreter (we author no expected outputs).

Emits build/report/m5_summary.json. Reproduce (after scripts/convert_m5.py):
    python3 scripts/run_m5.py [--json PATH ...] [--fail-details N]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp5 import decoder as dec        # noqa: E402
from interp5 import fvalues as F          # noqa: E402
from interp5 import machine as M          # noqa: E402
from interp5 import validator as VAL      # noqa: E402

MANIFEST = ROOT / "manifest_m5.json"
CONVERTED = ROOT / "build" / "converted_m5"
REPORT = ROOT / "build" / "report"
CONV_REPORT = REPORT / "m5_conversion_report.json"

MASK = {"i32": 0xFFFFFFFF, "f32": 0xFFFFFFFF,
        "i64": 0xFFFF_FFFF_FFFF_FFFF, "f64": 0xFFFF_FFFF_FFFF_FFFF}


def log(msg: str) -> None:
    print(msg, flush=True)


# ---- comparator (positive-controlled by tests/positive_control_m5.py) ----------------------

def decode_operand(operand: dict) -> int:
    t = operand["type"]
    if t not in MASK:
        raise VAL.ValidationError(f"operand type {t!r} outside M5 value surface")
    return int(operand["value"]) & MASK[t]


def compare_return(result_vals: list[int], expected: list[dict]) -> tuple[bool, str]:
    """Bitwise compare incl. NaN-class expectations. Multi-value: full arity must match."""
    if len(result_vals) != len(expected):
        return False, (f"arity mismatch: got {len(result_vals)} result(s), "
                       f"expected {len(expected)}")
    for i, (got, exp) in enumerate(zip(result_vals, expected)):
        t = exp["type"]
        v = exp["value"]
        if v == "nan:canonical":
            ok = F.is_canonical_nan32(got) if t == "f32" else F.is_canonical_nan64(got)
            if not ok:
                return False, f"result[{i}] {t}: 0x{got:x} is not a canonical NaN"
        elif v == "nan:arithmetic":
            ok = F.is_arithmetic_nan32(got) if t == "f32" else F.is_arithmetic_nan64(got)
            if not ok:
                return False, f"result[{i}] {t}: 0x{got:x} is not an arithmetic NaN"
        else:
            want = int(v) & MASK[t]
            if got != want:
                w = 8 if t in ("i32", "f32") else 16
                return False, (f"result[{i}] {t}: got 0x{got:0{w}x} != expected 0x{want:0{w}x}")
    return True, "ok"


class FileResult:
    def __init__(self, name: str):
        self.name = name
        self.total = 0
        self.modules_ok = 0
        self.registered = 0
        self.actions_ok = 0
        self.passed = 0
        self.failed = 0
        self.unsupported = 0
        self.fail_details: list[str] = []
        self.unsupported_reasons: Counter[str] = Counter()

    def as_dict(self, max_details: int) -> dict:
        return {
            "file": self.name, "total": self.total, "modules_ok": self.modules_ok,
            "registered": self.registered, "actions_ok": self.actions_ok,
            "PASS": self.passed, "FAIL": self.failed, "UNSUPPORTED": self.unsupported,
            "unsupported_reasons": dict(sorted(self.unsupported_reasons.items())),
            "fail_details": self.fail_details[:max_details],
        }


class _Runner:
    """Per-file execution state: fresh Store (register map), current + named instances."""

    def __init__(self, wasm_dir: Path, fr: FileResult):
        self.wasm_dir = wasm_dir
        self.fr = fr
        self.store = M.Store()
        self.current: M.Instance | None = None
        self.current_reason: str | None = None            # why current is None (chaining)
        self.named: dict[str, M.Instance] = {}
        self.named_reason: dict[str, str] = {}

    # -- helpers --

    def _tally(self, ok: bool | None, detail: str, cmd: dict) -> None:
        fr = self.fr
        if ok is None:
            fr.unsupported += 1
            fr.unsupported_reasons[detail] += 1
        elif ok:
            fr.passed += 1
        else:
            fr.failed += 1
            fr.fail_details.append(f"line {cmd.get('line')}: {detail}")

    def _instance_for(self, action: dict) -> tuple[M.Instance | None, str]:
        mod = action.get("module")
        if mod is not None:
            inst = self.named.get(mod)
            return inst, self.named_reason.get(mod, f"named module {mod!r} not instantiated")
        return self.current, self.current_reason or "no live module instance"

    def _invoke(self, action: dict):
        """Run an invoke action. Returns (results, None) or (None, (bucket, detail)) where
        bucket is False for FAIL or None for UNSUPPORTED."""
        if action.get("type") != "invoke":
            return None, (None, f"action type {action.get('type')!r} out of M5 scope")
        inst, reason = self._instance_for(action)
        if inst is None:
            return None, (None, reason)
        field = action.get("field")
        try:
            args = [decode_operand(a) for a in action.get("args", [])]
            return M.invoke(inst, field, args), None
        except KeyError:
            return None, (False, f"export {field!r} not found")
        except M.Trap as t:
            return None, ("trap", t)                       # caller decides pass/fail
        except (dec.Unsupported, M.LinkError) as e:
            return None, (None, f"out-of-surface in {field!r}: {e}")
        except (ValueError, IndexError) as e:
            return None, (False, f"execution error in {field!r}: {e}")

    # -- command handlers --

    def cmd_module(self, cmd: dict) -> None:
        fr = self.fr
        name = cmd.get("name")
        self.current, self.current_reason = None, None
        fn = cmd.get("filename")
        wp = self.wasm_dir / fn if fn else None
        if not wp or not wp.exists():
            fr.failed += 1
            fr.fail_details.append(f"line {cmd.get('line')}: module binary missing: {fn}")
            return
        reason = None
        try:
            module = dec.decode(wp.read_bytes())
            VAL.validate_module(module)                    # standing negative control (AC3)
            inst = M.instantiate(module, self.store)
            fr.modules_ok += 1
            self.current = inst
            if name:
                self.named[name] = inst
            return
        except dec.Unsupported as e:
            reason = f"module beyond frozen surface: {e}"
            fr.unsupported += 1
            fr.unsupported_reasons[reason] += 1
        except M.LinkError as e:
            reason = f"module import beyond host surface: {e}"
            fr.unsupported += 1
            fr.unsupported_reasons[reason] += 1
        except dec.DecodeError as e:
            reason = f"decoder rejected oracle-valid module: {e}"
            fr.failed += 1
            fr.fail_details.append(f"line {cmd.get('line')}: {reason}")
        except VAL.ValidationError as e:
            reason = f"validator rejected oracle-valid module: {e}"
            fr.failed += 1
            fr.fail_details.append(f"line {cmd.get('line')}: {reason}")
        except M.Trap as t:
            reason = f"instantiation trapped unexpectedly: {t.kind}"
            fr.failed += 1
            fr.fail_details.append(f"line {cmd.get('line')}: {reason}")
        self.current_reason = reason
        if name:
            self.named_reason[name] = reason

    def cmd_register(self, cmd: dict) -> None:
        fr = self.fr
        src = cmd.get("name")
        inst = self.named.get(src) if src else self.current
        if inst is None:
            reason = (self.named_reason.get(src) if src else self.current_reason)
            if reason:                                     # chained: module was out of surface
                fr.unsupported += 1
                fr.unsupported_reasons[f"register skipped: {reason}"] += 1
            else:
                fr.failed += 1
                fr.fail_details.append(f"line {cmd.get('line')}: register with no live instance")
            return
        self.store.registered[cmd["as"]] = inst
        fr.registered += 1

    def cmd_action(self, cmd: dict) -> None:
        results, err = self._invoke(cmd.get("action") or {})
        if err is None:
            self.fr.actions_ok += 1
            return
        bucket, detail = err
        if bucket == "trap":
            self._tally(False, f"action trapped: {detail.kind}", cmd)
        else:
            if bucket is False:
                self._tally(False, f"action failed: {detail}", cmd)
            else:
                self.fr.unsupported += 1
                self.fr.unsupported_reasons[str(detail)] += 1

    def cmd_assert_return(self, cmd: dict) -> None:
        results, err = self._invoke(cmd.get("action") or {})
        if err is not None:
            bucket, detail = err
            if bucket == "trap":
                self._tally(False, f"unexpected trap {detail.kind!r}", cmd)
            else:
                self._tally(bucket, str(detail), cmd)
            return
        ok, detail = compare_return(results, cmd.get("expected") or [])
        self._tally(ok, detail, cmd)

    def _assert_traplike(self, cmd: dict, what: str) -> None:
        want = cmd.get("text", "")
        results, err = self._invoke(cmd.get("action") or {})
        if err is None:
            self._tally(False, f"expected {what} {want!r} but call returned normally", cmd)
            return
        bucket, detail = err
        if bucket == "trap":
            if detail.kind == want:
                self._tally(True, "ok", cmd)
            else:
                self._tally(False, f"trap {detail.kind!r} != expected {want!r}", cmd)
        else:
            self._tally(bucket, str(detail), cmd)

    def cmd_assert_invalid(self, cmd: dict) -> None:
        if cmd.get("module_type") != "binary":
            self.fr.unsupported += 1
            self.fr.unsupported_reasons[
                "assert_invalid on text-format module (no .wat parser at M5)"] += 1
            return
        want = cmd.get("text", "")
        wp = self.wasm_dir / cmd["filename"]
        try:
            module = dec.decode(wp.read_bytes())
        except dec.Unsupported as e:
            self._tally(None, f"expected-invalid module beyond frozen surface: {e}", cmd)
            return
        except dec.DecodeError as e:
            self._tally(False, f"decoder called oracle-invalid module MALFORMED: {e}", cmd)
            return
        try:
            VAL.validate_module(module)
        except VAL.ValidationError as e:
            if str(e) == want:
                self._tally(True, "ok", cmd)
            else:
                self._tally(False, f"validator text {str(e)!r} != expected {want!r}", cmd)
            return
        self._tally(False, f"validator ACCEPTED expected-invalid module ({want!r})", cmd)

    def cmd_assert_malformed(self, cmd: dict) -> None:
        if cmd.get("module_type") != "binary":
            self.fr.unsupported += 1
            self.fr.unsupported_reasons[
                "assert_malformed on text-format module (no .wat parser at M5)"] += 1
            return
        want = cmd.get("text", "")
        wp = self.wasm_dir / cmd["filename"]
        try:
            dec.decode(wp.read_bytes())
        except dec.DecodeError as e:
            if str(e) == want:
                self._tally(True, "ok", cmd)
            else:
                self._tally(False, f"malformed text {str(e)!r} != expected {want!r}", cmd)
            return
        except dec.Unsupported as e:
            self._tally(None, f"expected-malformed module beyond frozen surface: {e}", cmd)
            return
        self._tally(False, f"decoder ACCEPTED expected-malformed module ({want!r})", cmd)

    def cmd_assert_uninstantiable(self, cmd: dict) -> None:
        want = cmd.get("text", "")
        wp = self.wasm_dir / cmd["filename"]
        try:
            module = dec.decode(wp.read_bytes())
            VAL.validate_module(module)
        except (dec.Unsupported, M.LinkError) as e:
            self._tally(None, f"expected-uninstantiable module beyond surface: {e}", cmd)
            return
        except (dec.DecodeError, VAL.ValidationError) as e:
            self._tally(False, f"rejected before instantiation: {e}", cmd)
            return
        try:
            M.instantiate(module, self.store)
        except M.Trap as t:
            if t.kind == want:
                self._tally(True, "ok", cmd)
            else:
                self._tally(False, f"instantiation trap {t.kind!r} != expected {want!r}", cmd)
            return
        except M.LinkError as e:
            self._tally(None, f"import beyond host surface: {e}", cmd)
            return
        self._tally(False, f"instantiated normally, expected trap {want!r}", cmd)


def run_file(json_path: Path) -> FileResult:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    commands = data.get("commands", [])
    src = data.get("source_filename") or json_path.name
    fr = FileResult(Path(src).name)
    fr.total = len(commands)
    r = _Runner(json_path.parent, fr)
    for cmd in commands:
        ctype = cmd.get("type")
        if ctype == "module":
            r.cmd_module(cmd)
        elif ctype == "register":
            r.cmd_register(cmd)
        elif ctype == "action":
            r.cmd_action(cmd)
        elif ctype == "assert_return":
            r.cmd_assert_return(cmd)
        elif ctype == "assert_trap":
            r._assert_traplike(cmd, "trap")
        elif ctype == "assert_exhaustion":
            r._assert_traplike(cmd, "exhaustion")
        elif ctype == "assert_invalid":
            r.cmd_assert_invalid(cmd)
        elif ctype == "assert_malformed":
            r.cmd_assert_malformed(cmd)
        elif ctype == "assert_uninstantiable":
            r.cmd_assert_uninstantiable(cmd)
        elif ctype == "assert_unlinkable":
            fr.unsupported += 1
            fr.unsupported_reasons["assert_unlinkable (link-error checks out of M5 scope)"] += 1
        else:
            fr.unsupported += 1
            fr.unsupported_reasons[f"unhandled command type: {ctype}"] += 1
    assert (fr.modules_ok + fr.registered + fr.actions_ok + fr.passed + fr.failed
            + fr.unsupported) == fr.total, f"accounting mismatch in {fr.name}"
    return fr


def iter_json_paths(args) -> list[Path]:
    if args.json:
        return [Path(p).resolve() for p in args.json]
    if not CONV_REPORT.exists():
        raise SystemExit("build/report/m5_conversion_report.json missing; run convert_m5.py")
    rep = json.loads(CONV_REPORT.read_text(encoding="utf-8"))
    return [ROOT / f["json"] for f in rep["files"] if f.get("ok") and f.get("json")]


def main() -> int:
    ap = argparse.ArgumentParser(description="M5 full-sweep assert-runner (oracle diff).")
    ap.add_argument("--json", nargs="+", help="explicit JSON paths (default: all converted)")
    ap.add_argument("--fail-details", type=int, default=50,
                    help="max fail details kept per file in the report")
    args = ap.parse_args()

    paths = iter_json_paths(args)
    if not paths:
        raise SystemExit("no converted M5 JSON found")

    results = []
    for jp in paths:
        if not jp.exists():
            raise SystemExit(f"JSON listed but missing (not skipping): {jp}")
        fr = run_file(jp)
        results.append(fr)
        log(f"{fr.name:30} total={fr.total:5} mod={fr.modules_ok:3} reg={fr.registered} "
            f"act={fr.actions_ok:2} PASS={fr.passed:5} FAIL={fr.failed:3} "
            f"UNSUP={fr.unsupported:4}")
        for d in fr.fail_details[:8]:
            log(f"    FAIL {d}")

    tot = {k: sum(getattr(r, a) for r in results) for k, a in [
        ("total", "total"), ("modules_ok", "modules_ok"), ("registered", "registered"),
        ("actions_ok", "actions_ok"), ("PASS", "passed"), ("FAIL", "failed"),
        ("UNSUPPORTED", "unsupported")]}
    grand_reasons: Counter[str] = Counter()
    for r in results:
        grand_reasons.update(r.unsupported_reasons)

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "milestone": "M5",
        "scope": "full test/core sweep under frozen pin+flags (55 convertible files)",
        "oracle": "frozen expected values/texts authored by the spec reference interpreter",
        "files": [r.as_dict(args.fail_details) for r in results],
        "totals": {**tot, "unsupported_reasons": dict(sorted(grand_reasons.items()))},
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    out = REPORT / "m5_summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    log(f"\n=== M5 full-sweep summary ===")
    log(f"files={len(results)} commands={tot['total']} modules_ok={tot['modules_ok']} "
        f"registered={tot['registered']} actions_ok={tot['actions_ok']}")
    log(f"PASS={tot['PASS']}  FAIL={tot['FAIL']}  UNSUPPORTED={tot['UNSUPPORTED']}")
    log(f"wrote {out.relative_to(ROOT)}")
    assert (tot["modules_ok"] + tot["registered"] + tot["actions_ok"] + tot["PASS"]
            + tot["FAIL"] + tot["UNSUPPORTED"]) == tot["total"], "global accounting mismatch"
    log("accounting identity holds for every file and globally "
        "(modules_ok+registered+actions_ok+PASS+FAIL+UNSUPPORTED == total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
