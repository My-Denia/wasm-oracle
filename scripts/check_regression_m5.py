#!/usr/bin/env python3
"""check_regression_m5.py — binary non-regression gate for the M5 sweep (plan milestone 8).

Re-runs the FROZEN M0–M4 pipeline in the m4.yml order and ASSERTS the locked counts:

    M0 skeleton   supported=0  unsupported=1035
    M1            PASS=877  FAIL=0  UNSUPPORTED=136
    M2            PASS=51   FAIL=0  UNSUPPORTED=4
    M3            PASS=45   FAIL=0  UNSUPPORTED=60
    M4 curation   200 = included 65 + unsupported 135, 0 policy violations (scope re-derived,
                  committed evidence must be byte-identical)
    M4 execution  PASS=65   FAIL=0  UNSUPPORTED=135

plus the additive-tree gate, enforced at BOTH layers:

  1. worktree: `git status --porcelain` must show NO modification to any tracked file
     (untracked new files are expected and fine; enumerate_m4's re-derived scope artifacts
     must be byte-identical to the committed ones or they show up here);
  2. committed history: `git diff --name-status <M4-baseline> HEAD` may contain only
     additions — the ONE sanctioned exception is README.md, which may be modified but only
     with ZERO deleted lines (the additive M5 section). A clean CI checkout always passes
     layer 1, so layer 2 is what actually binds committed changes to M0-M4 files.

Requires history down to the frozen baseline commit (CI: actions/checkout fetch-depth: 0);
a checkout too shallow to contain the baseline FAILS the gate rather than skipping it.

Exit 0 iff every assertion holds. Run on Linux/WSL (WABT steps), typically LAST, after all
M5 work:
    python3 scripts/check_regression_m5.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The frozen M0-M4 tree: main at the moment M5 branched ("Package M0-M4 method demo").
# Everything that existed at this commit is the protected surface of the additive gate.
BASELINE_SHA = "dc29ee836332e97dd42f858544c44a871ed5213d"


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    p = subprocess.run(cmd, cwd=ROOT)
    if p.returncode != 0:
        raise SystemExit(f"REGRESSION GATE: {' '.join(cmd)} exited {p.returncode}")


def jload(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def main() -> int:
    py = sys.executable
    # frozen pipeline, m4.yml order (conversions rewrite the SHARED build/converted/ paths the
    # frozen runners read — which is exactly why M5's own artifacts live in build/converted_m5/)
    run([py, "scripts/convert.py"])
    run([py, "scripts/convert.py", "--manifest", "manifest_m2.json",
         "--report", "build/report/conversion_report_m2.json"])
    run([py, "scripts/convert.py", "--manifest", "manifest_m3.json",
         "--report", "build/report/conversion_report_m3.json"])
    run([py, "tools/enumerate_m4_validation_scope.py"])          # self-check runs first inside
    run([py, "scripts/run_skeleton.py"])
    run([py, "scripts/run_m1.py"])
    run([py, "scripts/run_m2.py"])
    run([py, "scripts/run_m3.py"])
    run([py, "scripts/run_m4.py"])

    failures: list[str] = []

    def expect(label: str, got, want) -> None:
        if got == want:
            print(f"  ok   {label}: {got}")
        else:
            failures.append(f"{label}: got {got}, locked {want}")
            print(f"  FAIL {label}: got {got}, locked {want}")

    m0 = jload("build/report/run_summary.json")["totals"]
    expect("M0 (supported, unsupported)", (m0["supported"], m0["unsupported"]), (0, 1035))
    m1 = jload("build/report/m1_summary.json")["totals"]
    expect("M1 (PASS, FAIL, UNSUPPORTED)",
           (m1["PASS"], m1["FAIL"], m1["UNSUPPORTED"]), (877, 0, 136))
    m2 = jload("build/report/m2_summary.json")["totals"]
    expect("M2 (PASS, FAIL, UNSUPPORTED)",
           (m2["PASS"], m2["FAIL"], m2["UNSUPPORTED"]), (51, 0, 4))
    m3 = jload("build/report/m3_summary.json")["totals"]
    expect("M3 (PASS, FAIL, UNSUPPORTED)",
           (m3["PASS"], m3["FAIL"], m3["UNSUPPORTED"]), (45, 0, 60))
    sc = jload("goal-runs/m4-validation/scope.json")["totals"]
    expect("M4 curation (total, included, unsupported, violations)",
           (sc["validation_assertions"], sc["included"], sc["unsupported"],
            sc["policy_violations"]), (200, 65, 135, 0))
    m4 = jload("build/report/m4_summary.json")["totals"]
    expect("M4 execution (PASS, FAIL, UNSUPPORTED)",
           (m4["PASS"], m4["FAIL"], m4["UNSUPPORTED"]), (65, 0, 135))

    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                        capture_output=True, text=True)
    modified = [ln for ln in st.stdout.splitlines() if ln and not ln.startswith("??")]
    if modified:
        failures.append(f"tracked pre-existing files modified: {modified}")
        print(f"  FAIL tracked files modified:\n    " + "\n    ".join(modified))
    else:
        print("  ok   git status: no tracked pre-existing file modified (worktree layer)")

    # committed-history layer: on a clean CI checkout the porcelain check above is vacuous,
    # so bind the COMMITTED tree to the frozen baseline as well. Additions only; README.md
    # alone may be modified, and only additively (zero deleted lines).
    diff = subprocess.run(["git", "diff", "--name-status", BASELINE_SHA, "HEAD"],
                          cwd=ROOT, capture_output=True, text=True)
    if diff.returncode != 0:
        failures.append(
            f"cannot diff against baseline {BASELINE_SHA[:12]} (shallow clone? "
            f"fetch full history / fetch-depth: 0): {diff.stderr.strip()}")
        print(f"  FAIL baseline diff unavailable: {diff.stderr.strip()}")
    else:
        bad: list[str] = []
        for ln in diff.stdout.splitlines():
            if not ln.strip():
                continue
            status, _, path = ln.partition("\t")
            if status.startswith("A"):
                continue
            if status.startswith("M") and path == "README.md":
                num = subprocess.run(["git", "diff", "--numstat", BASELINE_SHA, "HEAD",
                                      "--", "README.md"], cwd=ROOT,
                                     capture_output=True, text=True)
                deleted = num.stdout.split()[1] if num.stdout.split() else "?"
                if deleted == "0":
                    continue
                bad.append(f"README.md modified NON-additively ({deleted} deleted lines)")
                continue
            bad.append(ln)
        if bad:
            failures.append(f"non-additive committed changes vs baseline: {bad}")
            print("  FAIL committed tree not additive vs baseline:\n    " + "\n    ".join(bad))
        else:
            print(f"  ok   committed tree vs baseline {BASELINE_SHA[:12]}: additions only "
                  "(README.md additive-only)")

    if failures:
        print(f"\nREGRESSION GATE: {len(failures)} FAILURE(S)")
        return 1
    print("\nREGRESSION GATE: PASS — all locked M0-M4 counts reproduced; tree additive-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
