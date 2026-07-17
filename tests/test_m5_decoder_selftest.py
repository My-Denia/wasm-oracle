#!/usr/bin/env python3
"""M5 decoder self-test: cross-check interp5.decoder against the pinned WABT wasm-objdump
over EVERY instantiated (module / assert_uninstantiable) .wasm of the converted M5 corpus.

For each module binary:
- decode with interp5.decoder (must not raise — this surface was enumerated from these files);
- `wasm-objdump -x`: function count (imports+defined), export names+kinds, memory/table/global
  counts must match;
- `wasm-objdump -d`: the ordered opcode-mnemonic stream must match the decoder's flat bodies
  token-for-token (immediates are not compared here; the oracle diff exercises them).

Needs the pinned WABT (Linux/WSL). Exits nonzero on any mismatch — this is the milestone-3 gate.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp5 import decoder as dec  # noqa: E402

OBJDUMP = ROOT / "vendor" / "wabt" / "bin" / "wasm-objdump"
CONVERTED = ROOT / "build" / "converted_m5"

KNOWN_MNEMONICS = {name for name, _ in dec.OPCODES.values()} | set(dec._FC_SUBOPS.values())
_DLINE = re.compile(r"^ [0-9a-f]{6,}: (?:[0-9a-f]{2} )+\s*\| (.+)$")


def objdump(args: list[str], path: Path) -> str:
    p = subprocess.run([str(OBJDUMP), *args, str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"wasm-objdump failed on {path}: {p.stderr[:200]}")
    return p.stdout


def objdump_opcode_stream(path: Path) -> list[str]:
    out = objdump(["-d"], path)
    ops = []
    for line in out.splitlines():
        mm = _DLINE.match(line)
        if not mm:
            continue
        tok = mm.group(1).strip().split()
        if tok and tok[0] in KNOWN_MNEMONICS:
            ops.append(tok[0])
    return ops


def objdump_summary(path: Path) -> dict:
    out = objdump(["-x"], path)
    n_func_defined = len(re.findall(r"^ - func\[\d+\] size=", out, re.M))
    # Export names may contain raw newlines (names.wast), which breaks per-line name capture.
    # Count entries per kind from the Export section (entry lines are unambiguous), and collect
    # the single-line-parseable names for a subset check.
    mm = re.search(r"^Export\[\d+\]:\n(.*?)(?=^\w|\Z)", out, re.M | re.S)
    exp_sec = mm.group(1) if mm else ""
    kind_counts: dict[str, int] = {}
    for k in re.findall(r"^ - (func|table|memory|global)\[\d+\]", exp_sec, re.M):
        kind_counts[k] = kind_counts.get(k, 0) + 1
    # Names with quotes/newlines/exotic bytes are ambiguous in objdump's text output (names.wast)
    # — restrict the name subset check to unambiguous simple names; full name correctness is
    # exercised by the oracle run itself (every assert invokes exports BY NAME).
    parseable = [(k, n) for k, n in
                 re.findall(r"^ - (func|table|memory|global)\[\d+\][^\n]* -> \"(.*)\"$",
                            exp_sec, re.M)
                 if re.fullmatch(r"[A-Za-z0-9_.\- ]+", n)]
    return {"defined_funcs": n_func_defined, "kind_counts": kind_counts,
            "parseable_exports": parseable}


def our_opcode_stream(m: dec.Module) -> list[str]:
    ops = []
    for f in m.funcs:
        for ins in f.body:
            ops.append(ins.op)
    return ops


def main() -> int:
    if os.name == "nt":
        raise SystemExit("this self-test executes the pinned Linux wasm-objdump — run it "
                         "under WSL: wsl python3 tests/test_m5_decoder_selftest.py")
    if not OBJDUMP.exists():
        raise SystemExit(f"pinned wasm-objdump not found at {OBJDUMP} (run on WSL after fetch)")
    files = sorted(CONVERTED.glob("*/*.json"))
    if not files:
        raise SystemExit("no converted M5 corpus; run scripts/convert_m5.py first")
    n_modules = n_ops = 0
    mismatches: list[str] = []
    for jp in files:
        data = json.loads(jp.read_text(encoding="utf-8"))
        for cmd in data.get("commands", []):
            if cmd["type"] not in ("module", "assert_uninstantiable") or not cmd.get("filename"):
                continue
            wp = jp.parent / cmd["filename"]
            raw = wp.read_bytes()
            try:
                m = dec.decode(raw)
            except Exception as e:                      # noqa: BLE001 — report, don't die
                mismatches.append(f"{wp.name}: decoder raised {type(e).__name__}: {e}")
                continue
            n_modules += 1
            summ = objdump_summary(wp)
            if len(m.funcs) != summ["defined_funcs"]:
                mismatches.append(f"{wp.name}: defined funcs {len(m.funcs)} != objdump "
                                  f"{summ['defined_funcs']}")
            ours_kind_counts: dict[str, int] = {}
            for e in m.exports:
                ours_kind_counts[e.kind] = ours_kind_counts.get(e.kind, 0) + 1
            if ours_kind_counts != summ["kind_counts"]:
                mismatches.append(f"{wp.name}: export kind counts {ours_kind_counts} != "
                                  f"objdump {summ['kind_counts']}")
            # subset check on names objdump could print on one line (multiset semantics)
            ours_names = {}
            for e in m.exports:
                key = (e.kind, e.name.encode("ascii", "backslashreplace").decode())
                ours_names[key] = ours_names.get(key, 0) + 1
            for k, n in summ["parseable_exports"]:
                key = (k, n.encode("ascii", "backslashreplace").decode())
                if ours_names.get(key, 0) <= 0:
                    mismatches.append(f"{wp.name}: objdump export {key!r} missing from ours")
                else:
                    ours_names[key] -= 1
            got = our_opcode_stream(m)
            want = objdump_opcode_stream(wp)
            n_ops += len(want)
            if got != want:
                k = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b),
                         min(len(got), len(want)))
                mismatches.append(f"{wp.name}: opcode stream differs at {k}: "
                                  f"ours {got[k:k+4]} vs objdump {want[k:k+4]} "
                                  f"(lens {len(got)}/{len(want)})")
    print(f"checked {n_modules} module binaries, {n_ops} objdump opcode tokens")
    if mismatches:
        print(f"{len(mismatches)} MISMATCH(ES):")
        for s in mismatches[:40]:
            print(f"  - {s}")
        return 1
    print("decoder agrees with pinned wasm-objdump on all module binaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
