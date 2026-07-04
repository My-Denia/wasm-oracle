#!/usr/bin/env python3
"""Runner SKELETON — reads WABT-generated JSON and reports the command inventory.

This is scaffolding, NOT the interpreter. At M0 it implements NO execution semantics, so
EVERY command is reported UNSUPPORTED by design. It must never fake execution or results.

Guarantees enforced here:
  * No silent skipping: every command is accounted for; supported + unsupported == total.
  * Unknown command types are counted and flagged, never dropped.
  * A JSON that fails to load is a hard error (non-zero exit), never a skip.

Emits build/report/run_summary.json: {total, per-command-type, supported(=0), unsupported}.
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest_m0.json"
BUILD = ROOT / "build"
REPORT = BUILD / "report"
CONVERSION_REPORT = REPORT / "conversion_report.json"

# Command types WABT's spec JSON emits. Used ONLY to flag unknowns for visibility;
# it is NEVER used to decide whether to count a command. Nothing is dropped.
KNOWN_TYPES = {
    "module", "action", "register",
    "assert_return", "assert_trap", "assert_exhaustion",
    "assert_malformed", "assert_invalid", "assert_unlinkable",
    "assert_uninstantiable",
}
# At M0 the interpreter is not implemented, so NOTHING is executable yet.
SUPPORTED_TYPES: set[str] = set()


def log(msg: str) -> None:
    print(msg, flush=True)


def classify(json_path: Path) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))  # raises on bad JSON -> hard error
    commands = data.get("commands", [])
    by_type = Counter(c.get("type", "<no-type>") for c in commands)
    unknown = {t: n for t, n in by_type.items() if t not in KNOWN_TYPES}
    supported = sum(n for t, n in by_type.items() if t in SUPPORTED_TYPES)  # == 0 at M0
    total = sum(by_type.values())
    unsupported = total - supported
    # Accounting integrity: prove every command in the file was counted (nothing dropped
    # during classification) and split cleanly into supported+unsupported.
    assert total == len(commands), f"dropped a command while classifying {json_path.name}"
    assert supported + unsupported == total, "command accounting mismatch"
    return {
        "source_filename": data.get("source_filename"),
        "json": str(json_path.relative_to(ROOT)),
        "total": total,
        "by_type": dict(sorted(by_type.items())),
        "unknown_types": dict(sorted(unknown.items())),
        "supported": supported,
        "unsupported": unsupported,
    }


def manifest_target_names() -> list[str]:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [Path(t["upstream_path"]).name for t in m["targets"]]


def iter_json_paths(args) -> tuple[list[Path], bool]:
    """Return (json_paths, enforce_completeness).

    In explicit --json mode the caller chose the set, so manifest completeness is not
    enforced. In the default (manifest-driven) mode we REFUSE to proceed on a partial
    conversion: a report with all_ok=false, or any manifest target whose JSON is absent,
    is a hard error -- never a silent skip that would present an incomplete inventory as
    complete.
    """
    if args.json:
        return [Path(p) for p in args.json], False
    if CONVERSION_REPORT.exists():
        rep = json.loads(CONVERSION_REPORT.read_text(encoding="utf-8"))
        if not rep.get("all_ok", False):
            failed = [f.get("name") for f in rep.get("files", []) if not f.get("ok")]
            raise SystemExit(f"conversion_report.all_ok is false; failed targets (not skipping): "
                             f"{failed}. Re-run scripts/convert.py.")
        return [ROOT / f["json"] for f in rep["files"] if f.get("ok") and f.get("json")], True
    # No report: require EVERY manifest target's JSON to exist. Never glob a partial subset.
    paths, missing = [], []
    for name in manifest_target_names():
        stem = Path(name).stem
        p = BUILD / "converted" / stem / f"{stem}.json"
        (paths.append(p) if p.exists() else missing.append(name))
    if missing:
        raise SystemExit(f"missing converted JSON for manifest targets (not skipping): {missing}. "
                         f"Run scripts/convert.py.")
    return paths, True


def main() -> int:
    ap = argparse.ArgumentParser(description="M0 runner skeleton: classify + report commands.")
    ap.add_argument("--json", nargs="+", help="explicit JSON paths (default: manifest-driven from conversion_report)")
    args = ap.parse_args()

    paths, enforce = iter_json_paths(args)
    if not paths:
        raise SystemExit("no JSON found. Run scripts/convert.py first.")

    files, grand, grand_unknown = [], Counter(), Counter()
    for jp in paths:
        if not jp.exists():
            raise SystemExit(f"JSON listed but missing (not skipping): {jp}")
        rec = classify(jp)
        files.append(rec)
        grand.update(rec["by_type"])
        grand_unknown.update(rec["unknown_types"])
        name = rec["source_filename"] or jp.name
        bt = ", ".join(f"{k}={v}" for k, v in rec["by_type"].items())
        log(f"{name:16} total={rec['total']:4} supported={rec['supported']} "
            f"UNSUPPORTED={rec['unsupported']:4}  [{bt}]")

    total = sum(grand.values())
    supported = sum(n for t, n in grand.items() if t in SUPPORTED_TYPES)
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "milestone": "M0",
        "semantics_implemented": False,
        "conformance_claimed": False,
        "note": ("M0 runner skeleton: reads and classifies WABT commands only. "
                 "No interpreter semantics. Every command is UNSUPPORTED by design."),
        "files": files,
        "totals": {
            "files": len(files),
            "total_commands": total,
            "by_type": dict(sorted(grand.items())),
            "supported": supported,
            "unsupported": total - supported,
            "unknown_types": dict(sorted(grand_unknown.items())),
        },
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    out = REPORT / "run_summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    t = summary["totals"]
    log(f"\n=== M0 runner-skeleton summary ===")
    log(f"files={t['files']}  total_commands={t['total_commands']}  "
        f"supported={t['supported']}  unsupported={t['unsupported']}")
    log(f"by_type: {t['by_type']}")
    if t["unknown_types"]:
        log(f"UNKNOWN (flagged, not dropped): {t['unknown_types']}")
    log(f"NO interpreter semantics implemented. NO conformance claimed.")
    log(f"wrote {out.relative_to(ROOT)}")

    # Completeness: in manifest-driven mode the runner must have covered EVERY manifest
    # target (guards against a truncated/tampered report), and its command total must match
    # exactly what convert.py produced (guards against a dropped file).
    if enforce:
        got = {Path(r["source_filename"]).name for r in files if r.get("source_filename")}
        want = set(manifest_target_names())
        missing = want - got
        if missing:
            raise SystemExit(f"runner did not cover all manifest targets (not skipping): {sorted(missing)}")
        if CONVERSION_REPORT.exists():
            conv = json.loads(CONVERSION_REPORT.read_text(encoding="utf-8"))
            assert t["total_commands"] == conv["totals"]["total_commands"], \
                "runner total_commands != conversion_report total (a file or commands went missing)"

    # M0 self-checks: nothing executed (supported==0), nothing dropped (unsupported==total),
    # and the frozen manifest is non-empty. These are independent assertions, not one chained
    # comparison, so an empty command set fails with a clear message instead of a confusing crash.
    assert t["supported"] == 0, "M0 must support nothing"
    assert t["unsupported"] == t["total_commands"], "accounting: unsupported must equal total"
    assert t["total_commands"] > 0, "M0 manifest is non-empty; expected >0 commands"
    return 0


if __name__ == "__main__":
    sys.exit(main())
