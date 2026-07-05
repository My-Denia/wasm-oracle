#!/usr/bin/env python3
"""run_m3.py — the M3 assert-runner: execute the linear-memory targets, diff vs the oracle.

Reuses the M1 command loop UNCHANGED — `run_m1.run_file` / `run_m1.FileResult` classify each command
milestone-agnostically (instantiate modules; invoke value asserts; PASS / FAIL / UNSUPPORTED with the
same no-drop invariant `modules + PASS + FAIL + UNSUPPORTED == total`) — and drives it over the
`manifest_m3.json` targets (store, memory_size). The oracle is embedded and frozen: the `expected`
values were authored by the WebAssembly spec reference interpreter, so diffing our result against
`expected` IS diffing against the reference-interpreter oracle (zero authored expected values here).

M1's `scripts/run_m1.py` is imported, not modified, so its gate stays independently runnable.

Emits build/report/m3_summary.json. Exit 0 iff FAIL==0. Reproduce (after
`scripts/convert.py --manifest manifest_m3.json --report build/report/conversion_report_m3.json`):
    python3 scripts/run_m3.py
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import run_m1  # noqa: E402  (reuse run_file + FileResult; classification is milestone-agnostic)

MANIFEST = ROOT / "manifest_m3.json"
BUILD = ROOT / "build"
REPORT = BUILD / "report"


def log(msg: str) -> None:
    print(msg, flush=True)


def manifest_target_names(manifest_path: Path) -> list[str]:
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [Path(t["upstream_path"]).name for t in m["targets"]]


def iter_json_paths(names: list[str]) -> list[Path]:
    """Same completeness discipline as run_m1/run_m2: a missing target's JSON is a hard error, never
    a silent subset."""
    paths, missing = [], []
    for name in names:
        stem = Path(name).stem
        p = BUILD / "converted" / stem / f"{stem}.json"
        (paths.append(p) if p.exists() else missing.append(name))
    if missing:
        raise SystemExit(f"missing converted JSON for M3 targets: {missing}. "
                         f"Run scripts/convert.py --manifest manifest_m3.json.")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="M3 linear-memory assert-runner (oracle diff).")
    ap.add_argument("--manifest", default=str(MANIFEST), help="manifest (default: manifest_m3.json)")
    args = ap.parse_args()

    names = manifest_target_names(Path(args.manifest))
    paths = iter_json_paths(names)

    results = []
    for jp in paths:
        fr = run_m1.run_file(jp)                       # reuse the M1 per-file classifier verbatim
        # run_m1 tags validation commands "out of M1 scope"; neutralize the milestone wording for
        # M3's own report (run_m1.py itself is left untouched, so its gate stays independent).
        fr.unsupported_reasons = Counter(
            {k.replace("validation — out of M1 scope", "validation — deferred to M4"): v
             for k, v in fr.unsupported_reasons.items()})
        results.append(fr)
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

    got = {r.name for r in results}
    want = set(names)
    if want - got:
        raise SystemExit(f"runner did not cover all M3 targets: {sorted(want - got)}")

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "milestone": "M3",
        "semantics_implemented": True,
        "scope": ("integer core + structured control flow + linear memory "
                  "(Memory section, i32.store, memory.size, memory.grow); "
                  "sections {Type,Function,Memory,Export,Code}; no loads / data segments / floats"),
        "oracle": "frozen expected values authored by the WebAssembly spec reference interpreter",
        "files": [r.as_dict() for r in results],
        "totals": {**tot, "unsupported_reasons": dict(sorted(grand_reasons.items()))},
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    out = REPORT / "m3_summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    log("\n=== M3 linear-memory execution summary ===")
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
    log(f"\nGATE: PASS — 0 FAIL; {tot['PASS']} in-scope linear-memory assertions match the oracle, "
        f"{tot['UNSUPPORTED']} out-of-scope reported (not skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
