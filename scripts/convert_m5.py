#!/usr/bin/env python3
"""convert_m5.py — convert ALL manifest_m5 targets (the full test/core file set) to WABT JSON.

Differences from the frozen scripts/convert.py (which is NOT modified):

- CONVERT-FAIL is DATA, not an error: a target that wast2json rejects under the frozen
  guardrail flags is recorded with its stderr as the reason and the run continues. The run
  exits nonzero only on DRIFT — the actual convert-fail set differing from the manifest's
  expected_convert_fail enumeration — or on a judgment-boundary violation (below).
- PATH ISOLATION: writes ONLY build/converted_m5/<stem>/ and build/report/m5_*.json. The
  frozen M0–M4 toolchain paths (build/converted/, build/report/conversion_report.json) are
  never touched, so the frozen gates always re-read their own artifacts.
- FAIL-CLOSED TEXT INVENTORY: rebuilds the per-expected-text inventory from the converted
  JSON and exits nonzero if any BINARY assert_invalid/assert_malformed text, any
  trap/exhaustion/uninstantiable text, any action type, or any command type appears that is
  not enumerated in manifest_m5.json judgment_boundaries — a new judgment surface must be
  enumerated first, never absorbed silently. (TEXT-format module texts are inventory-only:
  those commands are UNSUPPORTED at this milestone.)
- PIN/FLAG FREEZE CHECK: asserts manifest_m5's spec pin, wabt tag, and disable_features are
  byte-equal to manifest_m0's before converting anything.

Run (WSL/Linux, after scripts/fetch_oracle.py):
    python3 scripts/convert_m5.py
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from convert import convert_one, find_wast2json  # frozen helpers, imported read-only  # noqa: E402

MANIFEST = ROOT / "manifest_m5.json"
MANIFEST_M0 = ROOT / "manifest_m0.json"
VENDOR = ROOT / "vendor"
CONVERTED = ROOT / "build" / "converted_m5"
REPORT_DIR = ROOT / "build" / "report"


def log(msg: str) -> None:
    print(msg, flush=True)


def check_pin_freeze(m5: dict, m0: dict) -> list[str]:
    """The pin and the guardrail flags are FROZEN: byte-equality against manifest_m0."""
    errs = []
    if m5["spec"]["commit"] != m0["spec"]["commit"]:
        errs.append(f"spec pin drift: {m5['spec']['commit']} != {m0['spec']['commit']}")
    if m5["spec"]["test_core_dir"] != m0["spec"]["test_core_dir"]:
        errs.append("test_core_dir drift")
    if m5["wabt"]["tag"] != m0["wabt"]["tag"]:
        errs.append(f"wabt tag drift: {m5['wabt']['tag']} != {m0['wabt']['tag']}")
    if m5["conversion"]["disable_features"] != m0["conversion"]["disable_features"]:
        errs.append(f"guardrail flag drift: {m5['conversion']['disable_features']} "
                    f"!= {m0['conversion']['disable_features']}")
    return errs


def build_text_inventory(files: list[dict]) -> dict:
    """Aggregate every judgment-relevant text/type from the converted JSON."""
    inv = {
        "assert_invalid_binary_texts": Counter(), "assert_invalid_text_texts": Counter(),
        "assert_malformed_binary_texts": Counter(), "assert_malformed_text_texts": Counter(),
        "assert_trap_texts": Counter(), "assert_exhaustion_texts": Counter(),
        "assert_uninstantiable_texts": Counter(),
        "action_types": Counter(), "command_types": Counter(),
        "named_module_files": Counter(), "register_count": 0,
    }
    for rec in files:
        if not rec.get("ok"):
            continue
        data = json.loads((ROOT / rec["json"]).read_text(encoding="utf-8"))
        for c in data.get("commands", []):
            t = c["type"]
            inv["command_types"][t] += 1
            if t == "assert_invalid":
                key = "assert_invalid_binary_texts" if c.get("module_type") == "binary" \
                    else "assert_invalid_text_texts"
                inv[key][c.get("text", "")] += 1
            elif t == "assert_malformed":
                key = "assert_malformed_binary_texts" if c.get("module_type") == "binary" \
                    else "assert_malformed_text_texts"
                inv[key][c.get("text", "")] += 1
            elif t == "assert_trap":
                inv["assert_trap_texts"][c.get("text", "")] += 1
            elif t == "assert_exhaustion":
                inv["assert_exhaustion_texts"][c.get("text", "")] += 1
            elif t == "assert_uninstantiable":
                inv["assert_uninstantiable_texts"][c.get("text", "")] += 1
            elif t == "register":
                inv["register_count"] += 1
            a = c.get("action")
            if a:
                inv["action_types"][a.get("type", "")] += 1
                if a.get("module"):
                    inv["named_module_files"][rec["name"]] += 1
            if t == "module" and c.get("name"):
                inv["named_module_files"][rec["name"]] += 1
    return {k: (dict(sorted(v.items())) if isinstance(v, Counter) else v)
            for k, v in inv.items()}


def check_boundaries(inv: dict, bounds: dict) -> list[str]:
    """Fail-closed: every BINARY judgment text / action type / command type must be enumerated."""
    checks = [
        ("assert_invalid_binary_texts", "invalid_binary_texts"),
        ("assert_malformed_binary_texts", "malformed_binary_texts"),
        ("assert_trap_texts", "trap_texts"),
        ("assert_exhaustion_texts", "exhaustion_texts"),
        ("assert_uninstantiable_texts", "uninstantiable_texts"),
        ("action_types", "action_types"),
        ("command_types", "command_types"),
    ]
    errs = []
    for inv_key, bound_key in checks:
        allowed = set(bounds[bound_key])
        seen = set(inv[inv_key])
        extra = seen - allowed
        if extra:
            errs.append(f"{inv_key}: unenumerated value(s) {sorted(extra)} — extend "
                        f"manifest_m5.json judgment_boundaries.{bound_key} explicitly first")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="M5 full-sweep converter (CONVERT-FAIL is data).")
    ap.add_argument("--wast2json", help="override path to wast2json")
    args = ap.parse_args()

    m5 = json.loads(MANIFEST.read_text(encoding="utf-8"))
    m0 = json.loads(MANIFEST_M0.read_text(encoding="utf-8"))
    pin_errs = check_pin_freeze(m5, m0)
    if pin_errs:
        for e in pin_errs:
            log(f"PIN FREEZE VIOLATION: {e}")
        return 2

    wast2json = Path(args.wast2json) if args.wast2json else find_wast2json()
    core = VENDOR / "spec" / m5["spec"]["test_core_dir"]
    if not core.is_dir():
        raise SystemExit(f"spec test/core not found at {core}. Run scripts/fetch_oracle.py.")
    flags = [f"--disable-{x}" for x in m5["conversion"]["disable_features"]]
    log(f"wast2json: {wast2json}")
    log(f"frozen guardrail flags: {' '.join(flags)}")

    files = []
    for t in m5["targets"]:
        base = Path(t["upstream_path"]).name
        if t.get("name") != base:
            raise SystemExit(f"manifest target name {t.get('name')!r} != basename {base!r}")
        src = core / base
        stem = src.stem
        if not src.exists():
            files.append({"name": base, "ok": False, "returncode": None,
                          "stderr": [f"source .wast missing: {src}"]})
            log(f"  MISSING {base}")
            continue
        rec = convert_one(wast2json, flags, src, CONVERTED / stem, stem)
        if rec["ok"]:
            # rec["json"] from convert_one is already ROOT-relative (build/converted_m5/...)
            log(f"  ok           {rec['name']:28} total={rec['total_commands']:5}")
        else:
            log(f"  CONVERT-FAIL {rec['name']:28} rc={rec['returncode']}")
        files.append(rec)

    actual_fail = sorted(f["name"] for f in files if not f["ok"])
    expected_fail = sorted(m5["expected_convert_fail"])
    drift = []
    if actual_fail != expected_fail:
        drift.append({"unexpected_convert_fail": sorted(set(actual_fail) - set(expected_fail)),
                      "unexpected_convert_ok": sorted(set(expected_fail) - set(actual_fail))})

    inv = build_text_inventory(files)
    boundary_errs = check_boundaries(inv, m5["judgment_boundaries"])

    now = datetime.now(timezone.utc).isoformat()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    conv_report = {
        "generated_utc": now, "milestone": "M5", "tool": "wast2json",
        "wast2json_path": str(wast2json), "guardrail_flags": flags,
        "spec_commit": m5["spec"]["commit"],
        "converted_ok": len(files) - len(actual_fail), "convert_fail": len(actual_fail),
        "convert_fail_names": actual_fail, "drift_vs_expected": drift,
        "total_commands": sum(f.get("total_commands", 0) for f in files if f.get("ok")),
        "files": files,
    }
    (REPORT_DIR / "m5_conversion_report.json").write_text(
        json.dumps(conv_report, indent=2) + "\n", encoding="utf-8")
    (REPORT_DIR / "m5_text_inventory.json").write_text(
        json.dumps({"generated_utc": now, "boundary_errors": boundary_errs, **inv},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ok_n = conv_report["converted_ok"]
    log(f"\n{ok_n}/{len(files)} converted; {len(actual_fail)} CONVERT-FAIL (recorded); "
        f"{conv_report['total_commands']} commands")
    log("wrote build/report/m5_conversion_report.json, build/report/m5_text_inventory.json")
    if drift:
        log(f"CONVERT-FAIL DRIFT vs manifest expectation: {drift}")
        return 1
    if boundary_errs:
        for e in boundary_errs:
            log(f"JUDGMENT BOUNDARY VIOLATION: {e}")
        return 1
    log("convert-fail set matches manifest expectation; all judgment texts enumerated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
