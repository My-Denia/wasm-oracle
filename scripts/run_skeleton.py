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
    # Accounting integrity: prove nothing was dropped.
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


def iter_json_paths(args) -> list[Path]:
    if args.json:
        return [Path(p) for p in args.json]
    if CONVERSION_REPORT.exists():
        rep = json.loads(CONVERSION_REPORT.read_text(encoding="utf-8"))
        return [ROOT / f["json"] for f in rep["files"] if f.get("ok") and f.get("json")]
    # fallback: scan converted tree
    return sorted((BUILD / "converted").glob("*/*.json"))


def main() -> int:
    ap = argparse.ArgumentParser(description="M0 runner skeleton: classify + report commands.")
    ap.add_argument("--json", nargs="*", help="explicit JSON paths (default: from conversion_report)")
    args = ap.parse_args()

    paths = iter_json_paths(args)
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

    # M0 self-checks: nothing executed, nothing dropped.
    assert t["supported"] == 0, "M0 must support nothing"
    assert t["unsupported"] == t["total_commands"] > 0, "unsupported must equal total (>0)"
    return 0


if __name__ == "__main__":
    sys.exit(main())
