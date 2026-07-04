#!/usr/bin/env python3
"""Convert the frozen manifest's .wast targets to WABT JSON (+ .wasm) and count commands.

M0 scope: this is FORMAT CONVERSION ONLY (WABT wast2json). It parses nothing itself and
implements no semantics. wast2json is run with DEFAULT features (no --enable-* flags): the
default feature set is a SCOPE GUARDRAIL that rejects out-of-scope proposal syntax instead
of silently converting it.

A target that fails conversion is reported as FAILED (never silently skipped) and makes the
whole run exit non-zero. Writes build/report/conversion_report.json.
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest_m0.json"
VENDOR = ROOT / "vendor"
BUILD = ROOT / "build"
CONVERTED = BUILD / "converted"
REPORT = BUILD / "report"


def log(msg: str) -> None:
    print(msg, flush=True)


def find_wast2json() -> Path:
    env = os.environ.get("WAST2JSON")
    cand = Path(env) if env else (VENDOR / "wabt" / "bin" / "wast2json")
    if not cand.exists():
        raise SystemExit(f"wast2json not found at {cand}. Run scripts/fetch_oracle.py first "
                         f"(or set $WAST2JSON).")
    return cand


def convert_one(wast2json: Path, flags: list, src: Path, out_dir: Path, stem: str) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    json_path = out_dir / f"{stem}.json"
    # `flags` are --disable-* for the post-MVP extensions M0 excludes (from the manifest):
    # a real guardrail that makes wast2json REJECT out-of-scope proposal syntax, rather than
    # relying on defaults (which enable those standardized extensions).
    # Decode stderr as UTF-8 explicitly (not the process locale) so a diagnostic with
    # non-locale bytes can't mojibake or crash convert_one on a non-UTF8 machine.
    proc = subprocess.run(
        [str(wast2json), *flags, str(src), "-o", str(json_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    rec: dict = {"name": src.name, "returncode": proc.returncode}
    if proc.returncode != 0 or not json_path.exists():
        rec["ok"] = False
        rec["stderr"] = proc.stderr.strip().splitlines()[:6]
        return rec
    data = json.loads(json_path.read_text(encoding="utf-8"))
    by_type = Counter(c["type"] for c in data.get("commands", []))
    rec.update({
        "ok": True,
        "json": str(json_path.relative_to(ROOT)),
        "total_commands": sum(by_type.values()),
        "by_type": dict(sorted(by_type.items())),
        "wasm_count": len(list(out_dir.glob("*.wasm"))),
        "wat_count": len(list(out_dir.glob("*.wat"))),
    })
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert frozen manifest .wast -> WABT JSON (+wasm).")
    ap.add_argument("--wast2json", help="override path to wast2json")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wast2json = Path(args.wast2json) if args.wast2json else find_wast2json()
    core = VENDOR / "spec" / manifest["spec"]["test_core_dir"]
    if not core.is_dir():
        raise SystemExit(f"spec test/core not found at {core}. Run scripts/fetch_oracle.py first.")

    disable = manifest["conversion"].get("disable_features", [])
    flags = [f"--disable-{x}" for x in disable]
    REPORT.mkdir(parents=True, exist_ok=True)
    log(f"wast2json: {wast2json}")
    log(f"guardrail flags (reject out-of-scope extensions): {' '.join(flags) or '(none)'}")
    log(f"spec test/core: {core}")

    files, grand = [], Counter()
    for t in manifest["targets"]:
        base = Path(t["upstream_path"]).name
        # Enforce the manifest's two-field contract: the declared name must match the
        # upstream basename, so a typo/rename can't silently resolve to a different file.
        if t.get("name") != base:
            raise SystemExit(f"manifest target name '{t.get('name')}' != upstream basename "
                             f"'{base}' for {t['upstream_path']}; fix manifest_m0.json.")
        src = core / base
        stem = src.stem
        if not src.exists():
            files.append({"name": src.name, "ok": False, "returncode": None,
                          "stderr": [f"source .wast missing: {src}"]})
            log(f"  FAIL   {src.name}: source missing")
            continue
        rec = convert_one(wast2json, flags, src, CONVERTED / stem, stem)
        files.append(rec)
        if rec["ok"]:
            grand.update(rec["by_type"])
            bt = ", ".join(f"{k}={v}" for k, v in rec["by_type"].items())
            log(f"  ok     {rec['name']:16} total={rec['total_commands']:4} wasm={rec['wasm_count']:3}  [{bt}]")
        else:
            log(f"  FAIL   {rec['name']:16} rc={rec['returncode']}  {rec.get('stderr')}")

    all_ok = all(f["ok"] for f in files)
    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "tool": "wast2json",
        "wast2json_path": str(wast2json),
        "guardrail_flags": flags,
        "spec_commit": manifest["spec"]["commit"],
        "all_ok": all_ok,
        "file_count": len(files),
        "files": files,
        "totals": {
            "converted_ok": sum(1 for f in files if f["ok"]),
            "total_commands": sum(grand.values()),
            "by_type": dict(sorted(grand.items())),
        },
    }
    out = REPORT / "conversion_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    log(f"\n{report['totals']['converted_ok']}/{len(files)} converted; "
        f"{report['totals']['total_commands']} total commands")
    log(f"wrote {out.relative_to(ROOT)}")
    if not all_ok:
        log("CONVERSION FAILED for at least one manifest target (reported, not skipped).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
