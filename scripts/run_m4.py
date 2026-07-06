#!/usr/bin/env python3
"""M4 validation runner over the committed curation scope.

This runner consumes only goal-runs/m4-validation/scope.json. The 65 INCLUDED
binary assert_invalid records are expected to be rejected by interp.validator
with the curation category recorded in scope.json. The 135 UNSUPPORTED records
remain unsupported and are never decoded, validated, or reclassified as PASS.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from interp import decoder as dec  # noqa: E402
from interp import validator as val  # noqa: E402

SCOPE = ROOT / "goal-runs" / "m4-validation" / "scope.json"
REPORT = ROOT / "build" / "report" / "m4_summary.json"
EXPECTED_TOTAL = 200
EXPECTED_INCLUDED = 65
EXPECTED_UNSUPPORTED = 135
EXPECTED_CATEGORIES = {val.TYPE_MISMATCH, val.UNKNOWN_LABEL}


@dataclass
class RecordOutcome:
    status: str
    detail: str
    actual_category: str | None = None


def _artifact_path(record: dict[str, Any]) -> Path:
    return ROOT / "build" / "converted" / Path(record["source_file"]).stem / record["module_filename"]


def _fail_scope(message: str) -> SystemExit:
    return SystemExit(f"invalid M4 scope: {message}")


def load_scope(path: Path) -> dict[str, Any]:
    scope = json.loads(path.read_text(encoding="utf-8-sig"))
    totals = scope.get("totals") or {}
    if totals.get("validation_assertions") != EXPECTED_TOTAL:
        raise _fail_scope(f"validation_assertions={totals.get('validation_assertions')} expected {EXPECTED_TOTAL}")
    if totals.get("included") != EXPECTED_INCLUDED:
        raise _fail_scope(f"included={totals.get('included')} expected {EXPECTED_INCLUDED}")
    if totals.get("unsupported") != EXPECTED_UNSUPPORTED:
        raise _fail_scope(f"unsupported={totals.get('unsupported')} expected {EXPECTED_UNSUPPORTED}")
    if totals.get("policy_violations") != 0 or scope.get("policy_violations"):
        raise _fail_scope("policy_violations must be zero")
    records = scope.get("records") or []
    if len(records) != EXPECTED_TOTAL:
        raise _fail_scope(f"records length={len(records)} expected {EXPECTED_TOTAL}")
    decisions = Counter(r.get("decision") for r in records)
    if decisions.get("INCLUDED", 0) != EXPECTED_INCLUDED:
        raise _fail_scope(f"record INCLUDED count={decisions.get('INCLUDED', 0)} expected {EXPECTED_INCLUDED}")
    if decisions.get("UNSUPPORTED", 0) != EXPECTED_UNSUPPORTED:
        raise _fail_scope(f"record UNSUPPORTED count={decisions.get('UNSUPPORTED', 0)} expected {EXPECTED_UNSUPPORTED}")
    for r in records:
        decision = r.get("decision")
        if decision == "INCLUDED":
            _assert_included_record(r)
        elif decision == "UNSUPPORTED":
            if not r.get("reason"):
                raise _fail_scope(f"UNSUPPORTED record missing reason at validation_index={r.get('validation_index')}")
        else:
            raise _fail_scope(f"unknown decision {decision!r} at validation_index={r.get('validation_index')}")
    return scope


def _assert_included_record(record: dict[str, Any]) -> None:
    if record.get("command_type") != "assert_invalid":
        raise _fail_scope(f"INCLUDED record is not assert_invalid: {record.get('validation_index')}")
    if record.get("module_type") != "binary":
        raise _fail_scope(f"INCLUDED record is not binary: {record.get('validation_index')}")
    if record.get("current_decoder", {}).get("status") != "decoder_accept":
        raise _fail_scope(f"INCLUDED record was not decoder_accept: {record.get('validation_index')}")
    if record.get("category") not in EXPECTED_CATEGORIES:
        raise _fail_scope(f"INCLUDED record has unexpected category: {record.get('category')}")
    if record.get("match") != "category":
        raise _fail_scope(f"INCLUDED record must require category match: {record.get('validation_index')}")
    if record.get("deferred_features") or record.get("extra_sections") or record.get("extra_opcodes"):
        raise _fail_scope(f"INCLUDED record carries deferred surface: {record.get('validation_index')}")


def evaluate_record(record: dict[str, Any]) -> RecordOutcome:
    """Evaluate one scope record. Used by tests and positive controls."""
    if record.get("decision") == "UNSUPPORTED":
        return RecordOutcome("UNSUPPORTED", record.get("reason", "unsupported"))
    expected_category = record.get("category")
    artifact = _artifact_path(record)
    if not artifact.exists():
        return RecordOutcome("FAIL", f"artifact missing: {artifact.relative_to(ROOT)}")
    try:
        module = dec.decode(artifact.read_bytes())
        val.validate_module(module)
    except val.ValidationError as e:
        if e.category == expected_category:
            return RecordOutcome("PASS", str(e), actual_category=e.category)
        return RecordOutcome(
            "FAIL",
            f"wrong validation category: got {e.category}, expected {expected_category}; detail: {e}",
            actual_category=e.category,
        )
    except dec.Unsupported as e:
        return RecordOutcome("FAIL", f"decoder Unsupported on INCLUDED record: {e}")
    except dec.DecodeError as e:
        return RecordOutcome("FAIL", f"decoder DecodeError on INCLUDED record: {e}")
    return RecordOutcome("FAIL", "validator accepted expected-invalid module")


def run_scope(scope_path: Path) -> dict[str, Any]:
    scope = load_scope(scope_path)
    rows: list[dict[str, Any]] = []
    totals = Counter()
    reasons: Counter[str] = Counter()
    pass_categories: Counter[str] = Counter()
    fail_details: list[str] = []
    by_file: Counter[str] = Counter()

    for record in scope["records"]:
        outcome = evaluate_record(record)
        totals[outcome.status] += 1
        by_file[record["source_file"]] += 1
        if outcome.status == "UNSUPPORTED":
            reasons[outcome.detail] += 1
        if outcome.status == "PASS" and outcome.actual_category:
            pass_categories[outcome.actual_category] += 1
        if outcome.status == "FAIL":
            fail_details.append(
                f"#{record.get('validation_index'):03d} {record.get('source_file')} "
                f"{record.get('module_filename')}: {outcome.detail}"
            )
        rows.append({
            "validation_index": record.get("validation_index"),
            "source_file": record.get("source_file"),
            "module_filename": record.get("module_filename"),
            "expected_category": record.get("category"),
            "status": outcome.status,
            "actual_category": outcome.actual_category,
            "detail": outcome.detail,
        })

    summary_totals = {
        "total": len(scope["records"]),
        "PASS": totals.get("PASS", 0),
        "FAIL": totals.get("FAIL", 0),
        "UNSUPPORTED": totals.get("UNSUPPORTED", 0),
        "unsupported_reasons": dict(sorted(reasons.items())),
        "pass_categories": dict(sorted(pass_categories.items())),
        "by_file": dict(sorted(by_file.items())),
    }
    if summary_totals["PASS"] + summary_totals["FAIL"] + summary_totals["UNSUPPORTED"] != EXPECTED_TOTAL:
        raise AssertionError("M4 accounting mismatch")
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "milestone": "M4",
        "scope": "curation-bounded validation slice over 65 binary assert_invalid modules",
        "full_validation_conformance": False,
        "input_scope": str(scope_path.relative_to(ROOT)),
        "expected": {
            "total": EXPECTED_TOTAL,
            "PASS": EXPECTED_INCLUDED,
            "FAIL": 0,
            "UNSUPPORTED": EXPECTED_UNSUPPORTED,
        },
        "totals": summary_totals,
        "records": rows,
        "fail_details": fail_details[:100],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="M4 curation-bounded validation runner.")
    ap.add_argument("--scope", default=str(SCOPE), help="scope.json path")
    ap.add_argument("--report", default=str(REPORT), help="summary report path")
    args = ap.parse_args()

    scope_path = Path(args.scope)
    report_path = Path(args.report)
    summary = run_scope(scope_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    t = summary["totals"]
    print("=== M4 validation execution summary ===")
    print(f"scope={scope_path.relative_to(ROOT)}")
    print(f"commands={t['total']}  PASS={t['PASS']}  FAIL={t['FAIL']}  UNSUPPORTED={t['UNSUPPORTED']}")
    print(f"pass categories: {t['pass_categories']}")
    print(f"unsupported reasons: {t['unsupported_reasons']}")
    print(f"wrote {report_path.relative_to(ROOT)}")
    if t["FAIL"]:
        print(f"\nGATE: FAIL - {t['FAIL']} validation record(s) failed.")
        for detail in summary["fail_details"][:20]:
            print(f"  FAIL {detail}")
        return 1
    if (t["PASS"], t["UNSUPPORTED"]) != (EXPECTED_INCLUDED, EXPECTED_UNSUPPORTED):
        print("\nGATE: FAIL - M4 PASS/UNSUPPORTED counts drifted.")
        return 1
    print("\nGATE: PASS - 65 included invalid modules rejected with matching category; "
          "135 deferred validation cases counted UNSUPPORTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
