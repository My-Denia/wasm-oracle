#!/usr/bin/env python3
"""run_m1.py — the M1 assert-runner: execute the frozen targets and diff against the oracle.

Turns M0's all-UNSUPPORTED inventory into an honest supported/unsupported split. For each
WABT-converted target it walks the `commands`, instantiates each `module`, and for every
value assertion invokes the exported function and classifies the outcome — never silently
skipping a command (same no-drop invariant M0 enforces):

  PASS         invoke result equals `expected` (bitwise, i32/i64); or a trap occurred exactly
               where assert_trap expected it, with the matching trap kind.
  FAIL         a mismatch — wrong value, unexpected/absent/mis-kinded trap, missing export, or
               an in-scope module that fails to decode. ANY FAIL fails the gate (nonzero exit).
  UNSUPPORTED  a command outside M1 scope (assert_invalid / assert_malformed validation, or an
               opcode/section the enumerated scope excludes). Reported with a count, not dropped.

The oracle is embedded and frozen: the `expected` values were authored by the WebAssembly spec
reference interpreter (M0 manifest: "we author no expected outputs"), so diffing our result
against `expected` IS diffing against the reference-interpreter oracle.

Emits build/report/m1_summary.json. Exit 0 iff FAIL==0. Reproduce (after scripts/convert.py):
    python3 scripts/run_m1.py
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import decoder as dec              # noqa: E402
from interp import machine as M                # noqa: E402
from interp import runner as R                 # noqa: E402

MANIFEST = ROOT / "manifest_m0.json"
BUILD = ROOT / "build"
REPORT = BUILD / "report"
CONVERSION_REPORT = REPORT / "conversion_report.json"

VALIDATION = {"assert_invalid", "assert_malformed", "assert_unlinkable", "assert_uninstantiable"}


def log(msg: str) -> None:
    print(msg, flush=True)


class FileResult:
    def __init__(self, name: str):
        self.name = name
        self.total = 0
        self.modules_ok = 0
        self.passed = 0
        self.failed = 0
        self.unsupported = 0
        self.fail_details: list[str] = []
        self.unsupported_reasons: Counter[str] = Counter()

    def as_dict(self) -> dict:
        return {
            "file": self.name, "total": self.total, "modules_instantiated": self.modules_ok,
            "PASS": self.passed, "FAIL": self.failed, "UNSUPPORTED": self.unsupported,
            "unsupported_reasons": dict(sorted(self.unsupported_reasons.items())),
            "fail_details": self.fail_details[:50],
        }


def _invoke_args(action: dict) -> list[int]:
    return [R.decode_operand(a) for a in action.get("args", [])]


def run_file(json_path: Path) -> FileResult:
    data = json.loads(json_path.read_text(encoding="utf-8"))  # bad JSON -> hard error (never a skip)
    commands = data.get("commands", [])
    src = data.get("source_filename") or json_path.name
    fr = FileResult(Path(src).name)
    fr.total = len(commands)
    wasm_dir = json_path.parent
    instance = None                                   # current module instance (last `module`)

    for cmd in commands:
        ctype = cmd.get("type")

        if ctype == "module":
            fn = cmd.get("filename")
            wp = wasm_dir / fn if fn else None
            if not wp or not wp.exists():
                instance = None
                fr.failed += 1
                fr.fail_details.append(f"line {cmd.get('line')}: module binary missing: {fn}")
                continue
            try:
                instance = M.instantiate(dec.decode(wp.read_bytes()))
                fr.modules_ok += 1
            except dec.Unsupported as e:               # in-scope files shouldn't hit this
                instance = None
                fr.unsupported += 1
                fr.unsupported_reasons[f"module decode unsupported: {e}"] += 1
            except dec.DecodeError as e:                # our decoder is wrong on an in-scope module
                instance = None
                fr.failed += 1
                fr.fail_details.append(f"line {cmd.get('line')}: module decode FAILED: {e}")

        elif ctype == "assert_return":
            ok, detail = _do_return(instance, cmd)
            _tally(fr, ok, detail, cmd)

        elif ctype == "assert_trap":
            ok, detail = _do_trap(instance, cmd)
            _tally(fr, ok, detail, cmd)

        elif ctype in VALIDATION:
            fr.unsupported += 1
            fr.unsupported_reasons[f"{ctype} (validation — out of M1 scope)"] += 1

        else:
            # No such commands exist in the 4 targets, but never drop one silently.
            fr.unsupported += 1
            fr.unsupported_reasons[f"unhandled command type: {ctype}"] += 1

    # No-drop accounting: every command landed in exactly one bucket.
    assert (fr.modules_ok + fr.passed + fr.failed + fr.unsupported) == fr.total, \
        f"accounting mismatch in {fr.name}"
    return fr


def _tally(fr: FileResult, ok: bool | None, detail: str, cmd: dict) -> None:
    if ok is None:                                    # UNSUPPORTED (no instance / out-of-scope op)
        fr.unsupported += 1
        fr.unsupported_reasons[detail] += 1
    elif ok:
        fr.passed += 1
    else:
        fr.failed += 1
        fr.fail_details.append(f"line {cmd.get('line')}: {detail}")


def _do_return(instance, cmd) -> tuple[bool | None, str]:
    if instance is None:
        return None, "assert_return with no live module instance"
    act = cmd.get("action") or {}
    field = act.get("field")
    try:
        args = _invoke_args(act)
        results = M.invoke(instance, field, args)
    except KeyError:
        return False, f"export {field!r} not found"
    except M.Trap as t:
        return False, f"unexpected trap {t.kind!r} (expected a value from {field!r})"
    except M.Unsupported as e:
        return None, f"out-of-scope opcode in {field!r}: {e}"
    except (ValueError, IndexError) as e:
        return False, f"execution error in {field!r}: {e}"
    return R.compare_return(results, cmd.get("expected") or [])


def _do_trap(instance, cmd) -> tuple[bool | None, str]:
    if instance is None:
        return None, "assert_trap with no live module instance"
    act = cmd.get("action") or {}
    field = act.get("field")
    want = cmd.get("text", "")
    try:
        args = _invoke_args(act)
        M.invoke(instance, field, args)
    except KeyError:
        return False, f"export {field!r} not found"
    except M.Trap as t:
        if R.trap_matches(t.kind, want):
            return True, "ok"
        return False, f"trap kind {t.kind!r} != expected {want!r} (from {field!r})"
    except M.Unsupported as e:
        return None, f"out-of-scope opcode in {field!r}: {e}"
    except (ValueError, IndexError) as e:
        return False, f"execution error in {field!r}: {e}"
    return False, f"expected trap {want!r} but {field!r} returned normally"


def manifest_target_names() -> list[str]:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [Path(t["upstream_path"]).name for t in m["targets"]]


def iter_json_paths(args) -> tuple[list[Path], bool]:
    """Same completeness discipline as run_skeleton.py: manifest-driven mode refuses a partial
    conversion (a missing target's JSON is a hard error, never a silent subset)."""
    if args.json:
        return [Path(p).resolve() for p in args.json], False
    if CONVERSION_REPORT.exists():
        rep = json.loads(CONVERSION_REPORT.read_text(encoding="utf-8"))
        if not rep.get("all_ok", False):
            failed = [f.get("name") for f in rep.get("files", []) if not f.get("ok")]
            raise SystemExit(f"conversion_report.all_ok is false; failed targets: {failed}. "
                             f"Re-run scripts/convert.py.")
        return [ROOT / f["json"] for f in rep["files"] if f.get("ok") and f.get("json")], True
    paths, missing = [], []
    for name in manifest_target_names():
        stem = Path(name).stem
        p = BUILD / "converted" / stem / f"{stem}.json"
        (paths.append(p) if p.exists() else missing.append(name))
    if missing:
        raise SystemExit(f"missing converted JSON for manifest targets: {missing}. "
                         f"Run scripts/convert.py.")
    return paths, True


def main() -> int:
    ap = argparse.ArgumentParser(description="M1 integer-core assert-runner (oracle diff).")
    ap.add_argument("--json", nargs="+", help="explicit JSON paths (default: manifest-driven)")
    args = ap.parse_args()

    paths, enforce = iter_json_paths(args)
    if not paths:
        raise SystemExit("no JSON found. Run scripts/convert.py first.")

    results = []
    for jp in paths:
        if not jp.exists():
            raise SystemExit(f"JSON listed but missing (not skipping): {jp}")
        fr = run_file(jp)
        results.append(fr)
        reasons = ", ".join(f"{k}×{v}" for k, v in sorted(fr.unsupported_reasons.items())) or "-"
        log(f"{fr.name:18} total={fr.total:4} modules={fr.modules_ok:2} "
            f"PASS={fr.passed:4} FAIL={fr.failed:3} UNSUPPORTED={fr.unsupported:4}")
        for d in fr.fail_details[:10]:
            log(f"    FAIL {d}")

    tot = {
        "total": sum(r.total for r in results),
        "modules_instantiated": sum(r.modules_ok for r in results),
        "PASS": sum(r.passed for r in results),
        "FAIL": sum(r.failed for r in results),
        "UNSUPPORTED": sum(r.unsupported for r in results),
    }
    grand_reasons: Counter[str] = Counter()
    for r in results:
        grand_reasons.update(r.unsupported_reasons)

    if enforce:
        got = {r.name for r in results}
        want = set(manifest_target_names())
        if want - got:
            raise SystemExit(f"runner did not cover all manifest targets: {sorted(want - got)}")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "milestone": "M1",
        "semantics_implemented": True,
        "scope": "integer core (i32/i64), straight-line; sections {Type,Function,Export,Code}",
        "oracle": "frozen expected values authored by the WebAssembly spec reference interpreter",
        "files": [r.as_dict() for r in results],
        "totals": {**tot, "unsupported_reasons": dict(sorted(grand_reasons.items()))},
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    out = REPORT / "m1_summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    log(f"\n=== M1 integer-core execution summary ===")
    log(f"files={len(results)}  commands={tot['total']}  modules={tot['modules_instantiated']}  "
        f"PASS={tot['PASS']}  FAIL={tot['FAIL']}  UNSUPPORTED={tot['UNSUPPORTED']}")
    log(f"unsupported reasons: {dict(sorted(grand_reasons.items()))}")
    log(f"wrote {out.relative_to(ROOT)}")

    # Accounting integrity across all files (no command dropped), then the gate.
    assert (tot["modules_instantiated"] + tot["PASS"] + tot["FAIL"] + tot["UNSUPPORTED"]) \
        == tot["total"], "global accounting mismatch"
    if tot["FAIL"]:
        log(f"\nGATE: FAIL — {tot['FAIL']} assertion(s) did not match the oracle.")
        return 1
    log(f"\nGATE: PASS — 0 FAIL; {tot['PASS']} in-scope assertions match the oracle, "
        f"{tot['UNSUPPORTED']} out-of-scope reported (not skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
