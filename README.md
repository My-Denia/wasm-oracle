# wasm-oracle — a spec-conformant WebAssembly interpreter subset, verified against the official oracle

This project builds a WebAssembly interpreter subset whose correctness is checked against an
**external, authoritative oracle** that nobody here authored: the official
[WebAssembly reference interpreter](https://github.com/WebAssembly/spec) and the official
`test/core` conformance suite. The discipline is **verification-before-implementation** — the
test scaffold is built and frozen *before* any interpreter semantics exist, so the interpreter
is later forced to pass an oracle it cannot influence.

## M0 — Oracle Harness (this milestone)

**M0 implements NO interpreter semantics and claims NO conformance.** M0 builds only the
verification scaffold:

- an **external oracle** fetch/build chain (WebAssembly/spec reference interpreter, pinned),
- a **test-conversion chain** (WABT `wast2json`, pinned, toolchain-only),
- a **frozen test manifest** (`manifest_m0.json`) of integer-value-clean `.wast` files, and
- a **runner skeleton** that reads the converted JSON and reports the command inventory.

What M0 may claim, and nothing more: *the oracle harness is reproducible, the test manifest is
frozen, and the runner skeleton reads and reports commands.* At M0 the runner supports nothing —
**every command is reported `UNSUPPORTED` by design.** Nothing is faked and nothing is skipped.

See [`ORACLE.md`](ORACLE.md) for the pinned SHAs and exact reproduction commands.

## Layout

```
manifest_m0.json          frozen: spec pin, WABT pin, 4 integer-value targets, 4 reasoned exclusions
ORACLE.md                 pinned SHAs + build commands + exact "run a .wast" command
scripts/fetch_oracle.py   fetch spec@SHA (oracle) + WABT@release (toolchain), sha256-verified
scripts/convert.py        wast2json over the manifest -> JSON + .wasm, per-file command counts
scripts/run_skeleton.py   read JSON, classify every command, report UNSUPPORTED, machine summary
.github/workflows/m0.yml  Linux CI: fetch/build oracle -> convert -> run skeleton -> assert
vendor/  build/           generated (gitignored): fetched oracle/toolchain, converted JSON, reports
```

## Reproduce M0 locally (Linux / WSL)

```sh
python scripts/fetch_oracle.py          # pin-fetch oracle + toolchain (writes build/fetch_provenance.json)
( cd vendor/spec/interpreter && make )  # build the reference interpreter -> ./wasm  (needs OCaml+dune+menhir)
./vendor/spec/interpreter/wasm vendor/spec/test/core/i32.wast   # the oracle runs a .wast
python scripts/convert.py               # -> build/report/conversion_report.json
python scripts/run_skeleton.py          # -> build/report/run_summary.json  (supported=0, all UNSUPPORTED)
```

## Scope lock (M0 non-goals)

NO interpreter/execution semantics · NO `.wast` parser (WABT converts) · NO validation/type-checking ·
NO floating point · NO extensions (SIMD, GC, threads, memory64, multi-memory, exceptions,
relaxed-simd, bulk-memory) · NO performance/JIT/AOT · NO authoring of expected outputs.

`wast2json` is run with post-MVP extensions **disabled** (`--disable-simd --disable-bulk-memory
--disable-reference-types`, from `manifest_m0.json` → `conversion.disable_features`): a real guardrail
that makes conversion *reject* out-of-scope proposal syntax rather than silently accepting it. Two
honesty notes: WABT's *defaults* are **not** an integer guardrail — those extensions are standardized
and on unless disabled (only non-standardized proposals like typed function references are off by
default, which is why `local_tee.wast` fails without `--enable-function-references`). And `f32`/`f64`
are MVP-core and cannot be disabled by any flag, so **integer-value purity is enforced by manifest
curation, not flags**: `const`, `local_get`, `local_set` (float-bearing) and `local_tee` (function
references) are explicit, reasoned exclusions — see `manifest_m0.json` → `excluded`.

## Next: M1 (integer-core execution)

M1 begins implementing interpreter semantics for the integer core. It will be forced to work against
the frozen M0 manifest — the converted JSON of the 4 pinned files (**1035 commands**) — through the
external oracle: for every **execution** command (`assert_return` / `assert_trap`, plus module
instantiation and `action` invocations), the interpreter's result must match the official reference
interpreter's, with **zero** authored expected values. Validation commands (`assert_invalid` /
`assert_malformed`) stay `UNSUPPORTED` and reported (not skipped) until a later validation milestone —
turning today's all-`UNSUPPORTED` inventory into an honest supported/unsupported split.
