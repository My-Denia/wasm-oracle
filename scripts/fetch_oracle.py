#!/usr/bin/env python3
"""Fetch the EXTERNAL oracle at pinned versions and record how.

M0 scope: this only fetches. It builds nothing and implements no semantics.
  - WebAssembly/spec  (the SEMANTIC ORACLE) at a pinned commit SHA -> vendor/spec
  - WABT              (TOOLCHAIN ONLY: wast2json)  at a pinned release, sha256-verified -> vendor/wabt

All pins come from manifest_m0.json (the single machine-readable pin source).
Writes build/fetch_provenance.json recording URLs, checksums, and results.

Stdlib only (urllib, tarfile, hashlib, json) so CI needs no pip install.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sys, tarfile, tempfile, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest_m0.json"
VENDOR = ROOT / "vendor"
BUILD = ROOT / "build"
UA = {"User-Agent": "wasm-oracle-m0-fetch"}


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    log(f"  download {url}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _safe_extract(tar_path: Path, out_dir: Path) -> None:
    with tarfile.open(tar_path) as tar:
        # filter='data' (py>=3.12) blocks path traversal / unsafe members.
        try:
            tar.extractall(out_dir, filter="data")
        except TypeError:
            tar.extractall(out_dir)


def extract_strip_top(tar_path: Path, final_dir: Path) -> str:
    """Extract a tarball whose contents live under a single top directory,
    move that top directory to `final_dir`. Returns the stripped top-dir name."""
    if final_dir.exists():
        shutil.rmtree(final_dir)
    with tempfile.TemporaryDirectory(dir=str(VENDOR)) as tmp:
        tmpp = Path(tmp)
        _safe_extract(tar_path, tmpp)
        tops = [p for p in tmpp.iterdir() if p.name not in (".", "..")]
        if len(tops) == 1 and tops[0].is_dir():
            shutil.move(str(tops[0]), str(final_dir))
            return tops[0].name
        # no single top dir: move the whole extracted tree
        final_dir.mkdir(parents=True)
        for p in tops:
            shutil.move(str(p), str(final_dir / p.name))
        return ""


def fetch_spec(spec: dict, skip_existing: bool) -> dict:
    dest_dir = VENDOR / "spec"
    prov = {"repo": spec["repo"], "commit": spec["commit"], "tarball": spec["tarball"]}
    interp_makefile = dest_dir / spec["interpreter_dir"] / "Makefile"
    if skip_existing and interp_makefile.exists():
        log(f"[spec] present, skip (found {interp_makefile.relative_to(ROOT)})")
        prov["status"] = "present-skipped"
        return prov
    log(f"[spec] fetching WebAssembly/spec @ {spec['commit']}")
    VENDOR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / "spec.tar.gz"
        download(spec["tarball"], tarball)
        prov["tarball_sha256"] = sha256_file(tarball)  # recorded (github archive tarballs are not officially pinned by hash)
        top = extract_strip_top(tarball, dest_dir)
        prov["stripped_top_dir"] = top
    ok = interp_makefile.exists() and (dest_dir / spec["test_core_dir"]).is_dir()
    prov["status"] = "ok" if ok else "FAILED"
    if not ok:
        raise SystemExit(f"[spec] FAILED: expected {interp_makefile} and {spec['test_core_dir']}/")
    log(f"[spec] ok -> {dest_dir.relative_to(ROOT)} (interpreter + test/core present)")
    return prov


def fetch_wabt(wabt: dict, skip_existing: bool) -> dict:
    dest_dir = VENDOR / "wabt"
    wast2json = dest_dir / "bin" / "wast2json"
    prov = {
        "repo": wabt["repo"], "tag": wabt["tag"], "commit": wabt["commit"],
        "asset": wabt["linux_x64_asset"], "url": wabt["linux_x64_url"],
        "expected_sha256": wabt["linux_x64_sha256"], "role": wabt["role"],
    }
    if skip_existing and wast2json.exists():
        log(f"[wabt] present, skip (found {wast2json.relative_to(ROOT)})")
        prov["status"] = "present-skipped"
        return prov
    log(f"[wabt] fetching WABT {wabt['tag']} (toolchain only)")
    VENDOR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / wabt["linux_x64_asset"]
        download(wabt["linux_x64_url"], tarball)
        actual = sha256_file(tarball)
        prov["actual_sha256"] = actual
        prov["sha256_match"] = (actual == wabt["linux_x64_sha256"])
        if not prov["sha256_match"]:
            prov["status"] = "FAILED-checksum"
            raise SystemExit(
                f"[wabt] sha256 MISMATCH\n  expected {wabt['linux_x64_sha256']}\n  actual   {actual}")
        log(f"[wabt] sha256 OK ({actual})")
        extract_strip_top(tarball, dest_dir)
    if not wast2json.exists():
        prov["status"] = "FAILED"
        raise SystemExit(f"[wabt] FAILED: {wast2json} not found after extract")
    os.chmod(wast2json, 0o755)
    prov["status"] = "ok"
    log(f"[wabt] ok -> {wast2json.relative_to(ROOT)}")
    return prov


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch pinned external oracle (spec) + toolchain (WABT).")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip download if the vendored tree already looks complete")
    ap.add_argument("--only", choices=["spec", "wabt"], help="fetch only one dependency")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    BUILD.mkdir(parents=True, exist_ok=True)
    prov = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "note": "M0 fetch: external oracle + toolchain only. No build, no semantics.",
    }
    if args.only in (None, "spec"):
        prov["spec"] = fetch_spec(manifest["spec"], args.skip_existing)
    if args.only in (None, "wabt"):
        prov["wabt"] = fetch_wabt(manifest["wabt"], args.skip_existing)

    out = BUILD / "fetch_provenance.json"
    out.write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    log(f"[provenance] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
