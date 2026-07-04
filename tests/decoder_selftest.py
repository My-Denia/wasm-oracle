#!/usr/bin/env python3
"""decoder_selftest.py — verify interp.decoder against the pinned WABT disassembler (M1.2 gate).

For every INSTANTIATED module (`type=="module"` in the WABT JSON — the same set the body-purity
gate and the scope enumerator use), decode it with interp.decoder and cross-check three things
against WABT's own `wasm-objdump`:
  1. function count            (vs `-h` Code section count)
  2. exported function names    (vs `-x` Export section)
  3. per-function OPCODE stream (vs `-d` disassembly) — this validates every opcode byte and
     every immediate boundary in decoder.OPCODES: a wrong byte or mis-sized immediate desyncs
     the stream and mismatches here.

This makes the decoder's opcode table evidence-checked against the authoritative toolchain,
not trusted. No silent skip: a missing module/tool or ANY mismatch is a hard failure.

Reproduce (Linux/WSL, after scripts/convert.py):
    WASM_OBJDUMP=vendor/wabt/bin/wasm-objdump python3 tests/decoder_selftest.py
Exit 0 = decoder matches WABT for all modules; 1 = mismatch or evidence/tool missing.
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import decoder as dec  # noqa: E402

MANIFEST = ROOT / "manifest_m0.json"
CONVERTED = ROOT / "build" / "converted"
OBJDUMP = os.environ.get("WASM_OBJDUMP", str(ROOT / "vendor" / "wabt" / "bin" / "wasm-objdump"))

_FUNC_HDR = re.compile(r"^[0-9a-f]+ func\[\d+\]")


def _objdump(args: list[str], wasm: Path) -> str:
    res = subprocess.run([OBJDUMP, *args, str(wasm)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        raise RuntimeError(f"wasm-objdump {args} failed on {wasm}: {res.stderr.strip()}")
    return res.stdout


def objdump_func_count(wasm: Path) -> int:
    for ln in _objdump(["-h"], wasm).splitlines():
        s = ln.strip()
        if s.startswith("Code") and "count:" in s:
            return int(s.split("count:")[1].strip())
    return 0


def objdump_export_funcs(wasm: Path) -> set[str]:
    """Export names from `-x`. Lines look like:  - func[0] <name> -> "name" """
    out, names, in_export = _objdump(["-x"], wasm), set(), False
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("Export["):
            in_export = True
            continue
        if in_export:
            if s.startswith("- func["):
                m = re.search(r'-> "(.*)"$', s)
                if m:
                    names.add(m.group(1))
            elif s and not s.startswith("-"):
                in_export = False
    return names


def objdump_func_opcode_streams(wasm: Path) -> list[list[str]]:
    """Ordered opcode mnemonics per function from `-d` (token after '|')."""
    streams: list[list[str]] = []
    cur: list[str] | None = None
    for ln in _objdump(["-d"], wasm).splitlines():
        if _FUNC_HDR.match(ln.strip()):
            cur = []
            streams.append(cur)
            continue
        if cur is not None and "|" in ln:
            rhs = ln.split("|", 1)[1].strip()
            if rhs:
                mn = rhs.split()[0]
                # `-d` prints a function's LOCAL DECLARATIONS as `local[0] type=i32` lines before
                # the body; those are declarations, not instructions (the decoder holds them in
                # Func.local_types, not the body). Skip them — the real ops are `local.get`/
                # `local.set` (dot, not bracket). M1 modules declared no locals, so this is inert
                # there and only matters for the M2 targets.
                if mn.startswith("local["):
                    continue
                cur.append(mn)
    return streams


def modules_instantiated(conv: dict) -> list[str]:
    return [c["filename"] for c in conv["commands"]
            if c.get("type") == "module" and c.get("filename")]


def main() -> int:
    ap = argparse.ArgumentParser(description="Decoder self-test vs pinned wasm-objdump.")
    ap.add_argument("--manifest", default=str(MANIFEST),
                    help="manifest JSON whose targets to check (default: manifest_m0.json)")
    args = ap.parse_args()
    if not (os.path.exists(OBJDUMP) or _on_path(OBJDUMP)):
        print(f"FAIL: wasm-objdump not found at {OBJDUMP} (run scripts/convert.py first).")
        return 1
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    targets = [t["name"] for t in manifest["targets"]]
    checked, mismatches = 0, []
    for name in targets:
        stem = name[:-5] if name.endswith(".wast") else name
        cj = CONVERTED / stem / f"{stem}.json"
        if not cj.exists():
            print(f"FAIL: converted JSON missing (not skipping): {cj}")
            return 1
        conv = json.loads(cj.read_text(encoding="utf-8"))
        for fn in modules_instantiated(conv):
            wp = CONVERTED / stem / fn
            if not wp.exists():
                print(f"FAIL: module file missing: {wp}")
                return 1
            checked += 1
            data = wp.read_bytes()
            try:
                m = dec.decode(data)
            except (dec.Unsupported, dec.DecodeError) as e:
                mismatches.append(f"{fn}: decode raised {type(e).__name__}: {e}")
                continue
            # 1. function count
            oc = objdump_func_count(wp)
            if len(m.funcs) != oc:
                mismatches.append(f"{fn}: func count {len(m.funcs)} != objdump {oc}")
            # 2. export function names
            ours_exp = set(m.exports)
            theirs_exp = objdump_export_funcs(wp)
            if ours_exp != theirs_exp:
                only_ours = sorted(ours_exp - theirs_exp)[:5]
                only_theirs = sorted(theirs_exp - ours_exp)[:5]
                mismatches.append(f"{fn}: exports differ (+ours={only_ours} +theirs={only_theirs})")
            # 3. per-function opcode streams
            ours_streams = [[ins.op for ins in f.body] for f in m.funcs]
            theirs_streams = objdump_func_opcode_streams(wp)
            if len(ours_streams) != len(theirs_streams):
                mismatches.append(f"{fn}: func stream count {len(ours_streams)} != {len(theirs_streams)}")
            else:
                for i, (a, b) in enumerate(zip(ours_streams, theirs_streams)):
                    if a != b:
                        mismatches.append(f"{fn}: func[{i}] opcode stream differs\n"
                                          f"        ours={a}\n        wabt={b}")
                        break
    print(f"decoder self-test: {checked} instantiated modules checked against wasm-objdump")
    if mismatches:
        print(f"MISMATCHES ({len(mismatches)}):")
        for msg in mismatches[:20]:
            print(f"  >>> {msg}")
        if len(mismatches) > 20:
            print(f"  ... (+{len(mismatches) - 20} more)")
        return 1
    print("VERDICT: decoder matches WABT for func count, exports, and every opcode stream.")
    return 0


def _on_path(name: str) -> bool:
    from shutil import which
    return which(name) is not None


if __name__ == "__main__":
    sys.exit(main())
