# ORACLE.md — the external oracle, pinned and reproducible

**M0 implements NO interpreter semantics and claims NO conformance.** This file records the two
external dependencies, exactly how they are pinned, how the reference interpreter is built, and the
exact command to run a `.wast` through it. The **authoritative** machine-readable pins live in
[`manifest_m0.json`](manifest_m0.json); the values below are a human-readable mirror of it.

## 1. The semantic oracle — WebAssembly/spec (authoritative)

The official reference interpreter and the official `test/core` `.wast` files ARE the oracle. We
author no expected outputs; the `.wast` files carry the official assertions, and the reference
interpreter is the semantic authority.

| field | value |
| --- | --- |
| repo | https://github.com/WebAssembly/spec |
| pinned commit | `82cd4f9555dbf80b20611d8d3a2c6e6f444b228c` (2026-06-27) |
| reproducible tarball | `https://github.com/WebAssembly/spec/archive/82cd4f9555dbf80b20611d8d3a2c6e6f444b228c.tar.gz` |
| interpreter dir | `interpreter/` |
| conformance tests | `test/core/*.wast` (97 files at this commit) |

### Build the reference interpreter

Requires **OCaml ≥ 4.14**, **dune**, and **menhir** (per the interpreter's `README.md` /
`dune-project`). Verified locally with OCaml 5.2.1, dune 3.24.0, menhir 20260209.

```sh
# after scripts/fetch_oracle.py has populated vendor/spec
cd vendor/spec/interpreter
make                 # wraps `dune build wasm.exe`, links ./wasm
```

Produces the executable `./wasm`.

### Run a `.wast` through the oracle (this is the exact oracle command)

```sh
vendor/spec/interpreter/wasm vendor/spec/test/core/i32.wast   # exit 0 = all assertions in the script hold
```

`./wasm <file>.wast` loads the script and checks every assertion in it against the reference
semantics. This is the mechanism a future interpreter (M1+) will be measured against.

## 2. The conversion toolchain — WABT (toolchain only, NOT the oracle)

WABT is used only to convert `.wast` scripts into the machine-readable spec JSON (`wast2json`). It
is **not** the semantic oracle.

| field | value |
| --- | --- |
| repo | https://github.com/WebAssembly/wabt |
| pinned release | `1.0.41` (2026-05-07) |
| release commit | `03a00a1334e6121fb0cce4fccbd6bb109b68acaa` |
| linux-x64 asset | `wabt-1.0.41-linux-x64.tar.gz` |
| asset sha256 | `83f8122e924745fcd70636e3594bc01c4c47f2d4c8f3c63b5d70d3f83a482677` |

`scripts/fetch_oracle.py` downloads this asset and **verifies the sha256 before extracting**
(mismatch = hard failure). The `wast2json` binary lands at `vendor/wabt/bin/wast2json`.

### Convert (default features — scope guardrail)

```sh
vendor/wabt/bin/wast2json vendor/spec/test/core/i32.wast -o build/converted/i32/i32.json
```

No `--enable-*` proposal flags are passed. The default (integer-core) feature set is a scope
guardrail: it rejects out-of-scope proposal syntax rather than silently converting it.

## 3. One-shot reproduction

```sh
python scripts/fetch_oracle.py                          # fetch + sha256-verify (build/fetch_provenance.json)
( cd vendor/spec/interpreter && make )                  # build the oracle -> ./wasm
vendor/spec/interpreter/wasm vendor/spec/test/core/i32.wast   # oracle runs a .wast
python scripts/convert.py                               # wast2json over the frozen manifest
python scripts/run_skeleton.py                          # command inventory; every command UNSUPPORTED
```

CI (`.github/workflows/m0.yml`) performs exactly this on `ubuntu-latest` and asserts the M0
conditions. It does **not** attempt interpreter conformance and makes **no** conformance claim.
