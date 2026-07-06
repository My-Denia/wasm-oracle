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
tools/enumerate_m1_scope.py  M1 Step 0: enumerate real opcodes+sections -> goal-runs/m1-scope.txt
interp/                   integer core + M2 control flow: decoder, values, machine (interpreter), runner
scripts/run_m1.py         M1 assert-runner: execute the 4 targets, diff vs oracle, PASS/FAIL/UNSUPPORTED
tests/                    gates: decoder self-test, semantics + control-flow units, comparator positive controls
.github/workflows/m0.yml  Linux CI: fetch/build oracle -> convert -> run skeleton -> assert
.github/workflows/m1.yml  Linux CI: fetch -> convert -> purity gates -> M1 tests -> execution gate
goal-runs/m1-scope.txt    committed evidence: the enumerated M1 opcode/section scope
manifest_m2.json          frozen: M2 targets (labels, switch) + reasoned exclusions (M0/M1 manifest untouched)
tools/enumerate_m2_scope.py  M2 Step 0: FAIL-CLOSED scope gate -> goal-runs/m2-control-flow/scope.txt
scripts/run_m2.py         M2 assert-runner: execute labels/switch, diff vs oracle (reuses run_m1.run_file)
.github/workflows/m2.yml  Linux CI: fetch -> convert(m2) -> purity(m2) -> scope gate -> decoder/units/positive -> execution gate
tools/enumerate_m4_validation_scope.py  M4 Step 0: FAIL-CLOSED validation curation -> goal-runs/m4-validation/scope.*
.github/workflows/m4.yml  Linux CI: fetch -> convert M1/M2/M3 -> M4 validation curation + M1/M2/M3 count regression
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

## M1 — Integer Core Execution (implemented)

M1 implements interpreter semantics for the integer core and runs it against the frozen M0 manifest —
the converted JSON of the 4 pinned files (**1035 commands**). The oracle is embedded and frozen: the
`expected` values were authored by the WebAssembly spec reference interpreter, so diffing our result
against `expected` **is** diffing against the reference-interpreter oracle (zero authored expected
values here).

**Scope is derived from real data, never guessed** (`goal-runs/m1-scope.txt`, regenerated and
checked in CI): the 22 instantiated target modules use exactly **4 binary sections** (Type, Function,
Export, Code) and **71 integer opcodes** — no structured control flow, no memory, no floats (those are
M2–M5). The decoder and interpreter implement exactly that enumerated scope; anything outside it is
reported `UNSUPPORTED`, never mis-executed.

Result over the 4 targets: every in-scope value assertion matches the oracle — **PASS=877, FAIL=0**
(843 `assert_return` + 34 `assert_trap`, covering the two integer trap texts — `integer divide by
zero` from `div_s`/`div_u`/`rem_s`/`rem_u` by zero, and `integer overflow` from signed division
`div_s` of INT_MIN by -1; signed remainder `rem_s` of INT_MIN by -1 does not trap, it yields 0)
— with **UNSUPPORTED=136** (the `assert_invalid` /
`assert_malformed` validation commands, reported with a count, not skipped) and 22 modules
instantiated. `modules + PASS + FAIL + UNSUPPORTED == 1035`: nothing is dropped, turning M0's
all-`UNSUPPORTED` inventory into an honest supported/unsupported split. A comparator positive control
(`tests/positive_control.py`) feeds a deliberately wrong `expected` and confirms `FAIL` fires, so a
green run is evidence the comparator works.

```sh
python scripts/convert.py               # (after fetch_oracle.py) -> build/converted/*/*.json + *.wasm
python tools/enumerate_m1_scope.py      # -> goal-runs/m1-scope.txt (the enumerated scope evidence)
python tests/decoder_selftest.py        # decoder vs pinned wasm-objdump (all 22 modules)
python tests/test_semantics.py          # interpreter integer-semantics unit tests
python tests/positive_control.py        # prove the comparator FAILS on a wrong expected
python scripts/run_m1.py                # -> build/report/m1_summary.json (nonzero on any FAIL)
```

Validation (`assert_invalid` / `assert_malformed`) and floats remain out of scope and are deferred
to later milestones (M4–M5); structured control flow is M2 and linear memory is M3, below.

## M2 — Structured Control Flow (implemented)

M2 extends the interpreter to **structured control flow** — `block`, `loop`, `if`/`else`, `br`,
`br_if`, `br_table`, `return`, `drop`, `nop` (plus `local.set`) — and **nothing else**: no linear
memory, no globals, no calls, no validation execution, no floats.

**The targets were a curation problem, resolved by data.** The canonical control-flow files in
`test/core` (`block`, `loop`, `if`, `br`, `return`, …) are *not* integer-value-clean: each has one
big instantiated module that mixes `f32`/`f64` functions in with the integer ones and pulls in
`call` plus memory/global/table sections, so it fails the body-purity discipline. Several dedicated
files (`br_if`, `br_table`, `select`, `call_indirect`, `func`) *fail conversion* under the frozen
guardrail flags (they use typed function references / typed `select` / `externref` / `declare`) —
the guardrail working as designed. A read-only probe over every candidate identified exactly **two**
files that are integer-value-clean, stay inside the existing 4 sections, pull in no
call/memory/global/float, and exercise real control flow: **`labels.wast`** and **`switch.wast`**
(frozen in a new `manifest_m2.json`; `manifest_m0.json` is untouched). Together they cover the full
minimal MVP structured-control-flow opcode set — with no `select`, no `unreachable`, and **no new
binary sections** (still `{Type,Function,Export,Code}`). One opcode, `local.set`, was surfaced by
the **fail-closed** Step-0 enumerator (`tools/enumerate_m2_scope.py`, which *exits nonzero* if the
real data steps outside the frozen scope) — a required opcode forced into the open before any
interpreter code, not discovered by a later failure.

The decoder keeps function bodies flat (so the decoder self-test still matches `wasm-objdump`
token-for-token); the interpreter parses the flat stream into a nested block tree and evaluates it
with a value stack + a label stack (`interp/machine.py`). Block-type immediates are decoded as
signed-LEB and restricted to `empty`/`i32`/`i64` (float/multi-value ⇒ `Unsupported`).

Result over the 2 targets (57 commands): every in-scope control-flow value assertion matches the
oracle — **PASS=51, FAIL=0**, with **UNSUPPORTED=4** (the `assert_invalid` validation commands,
reported with a count, not skipped) and 2 modules instantiated.
`modules + PASS + FAIL + UNSUPPORTED == 57`: nothing dropped. A comparator positive control
(`tests/positive_control_m2.py`) feeds a deliberately wrong `expected` on a real control-flow
assertion and confirms `FAIL` fires, so a green run is evidence the comparator works. M1 is not
regressed: `scripts/run_m1.py` still reports `PASS=877, FAIL=0, UNSUPPORTED=136`.

```sh
python scripts/convert.py --manifest manifest_m2.json \
    --report build/report/conversion_report_m2.json      # -> build/converted/{labels,switch}/*
python tools/enumerate_m2_scope.py       # FAIL-CLOSED scope gate -> goal-runs/m2-control-flow/scope.txt
python tests/decoder_selftest.py --manifest manifest_m2.json   # decoder vs wasm-objdump (labels/switch)
python tests/test_control_flow.py        # structured-control-flow semantics units
python tests/positive_control_m2.py      # prove the comparator FAILS on a wrong expected
python scripts/run_m2.py                 # -> build/report/m2_summary.json (nonzero on any FAIL)
```

M2 claims exactly this and no more: an integer interpreter that executes structured control flow
over two data-selected `test/core` files and matches the reference-interpreter oracle on every
in-scope assertion. It is **not** a conformance-complete WebAssembly implementation — memory,
validation, floats, calls, and the many control-flow files that require them remain out of scope.

## M3 — Linear Memory (implemented)

M3 extends the interpreter to **linear memory** — the **Memory** section (min/max page limits),
`i32.store` (little-endian, with an out-of-bounds trap), `memory.size`, and `memory.grow` — and
**nothing else**: no loads, no data segments, no `i64`/narrow stores, no globals/calls/tables, no
validation execution, no floats.

**The targets were, again, a curation problem resolved by data.** Linear memory is where `test/core`
mixes concerns most: `memory.wast` / `data.wast` / `align.wast` *fail conversion* under the frozen
`--disable-bulk-memory` guardrail (bulk-memory / passive-data syntax at the pin); `address`,
`endianness`, `memory_redundancy`, `float_memory`, and `memory_trap` carry `f32`/`f64` load/store in
the same modules; `load` and `memory_grow` pull in `Global`/`Table`/`Elem`(/`Import`) sections plus
`call`/`select`. A read-only probe over **all 97** `test/core` files found exactly **two** that are
integer-value-clean, stay inside `{Type,Function,Export,Code,Memory}`, and exercise real memory
semantics: **`store.wast`** (`i32.store` inside every control construct) and **`memory_size.wast`**
(`memory.size`/`memory.grow` page-and-limit arithmetic across four memories, including grow-past-max
failure). They are frozen in a new `manifest_m3.json`; `manifest_m0.json` and `manifest_m2.json` are
untouched. The **fail-closed** Step-0 enumerator (`tools/enumerate_m3_scope.py`) uses an *asymmetric*
predicate — it admits exactly `i32.store`/`memory.size`/`memory.grow` + the Memory section while
still *exiting nonzero* on any load, wider/narrow store, `memory.*` beyond size/grow, Data section,
or float/call/global/table — and a built-in self-check proves that ban actually fires.

The decoder gains the Memory section (limits flags restricted to `0x00`/`0x01`; shared/`memory64`
⇒ `Unsupported`) and the three opcodes with their memarg/memidx immediates; all loads and the wider
stores stay `Unsupported` (fail-closed). The interpreter (`interp/machine.py`) gains a per-instance
page-granular `bytearray` memory, allocated at `instantiate()` and persisted across invokes;
`i32.store` writes 4 little-endian bytes with an effective-address bounds check that traps
`out of bounds memory access` (the spec-canonical string, read from the upstream oracle — no in-scope
target triggers it, so it is proven by `tests/test_memory.py`); `memory.grow` zero-extends within the
declared max (or the memory32 engine cap) and returns the previous page count or −1.

Result over the 2 targets (110 commands): every in-scope linear-memory assertion matches the oracle
— **PASS=45, FAIL=0**, with **UNSUPPORTED=60** (the `assert_invalid` / `assert_malformed` validation
commands, reported with a count, not skipped) and 5 modules instantiated.
`modules + PASS + FAIL + UNSUPPORTED == 110`: nothing dropped. A comparator positive control
(`tests/positive_control_m3.py`) feeds a deliberately wrong `expected` on a real `memory.size`
assertion and confirms `FAIL` fires. M1 and M2 are not regressed: `scripts/run_m1.py` still reports
`PASS=877, FAIL=0, UNSUPPORTED=136` and `scripts/run_m2.py` still `PASS=51, FAIL=0, UNSUPPORTED=4`.

```sh
python scripts/convert.py --manifest manifest_m3.json \
    --report build/report/conversion_report_m3.json     # -> build/converted/{store,memory_size}/*
python tools/enumerate_m3_scope.py       # FAIL-CLOSED scope gate -> goal-runs/m3-linear-memory/scope.txt
python tests/decoder_selftest.py --manifest manifest_m3.json   # decoder vs wasm-objdump (store/memory_size)
python tests/test_memory.py              # linear-memory semantics units (store LE, size/grow, OOB, fresh mem)
python tests/positive_control_m3.py      # prove the comparator FAILS on a wrong expected
python scripts/run_m3.py                 # -> build/report/m3_summary.json (nonzero on any FAIL)
```

M3 claims exactly this and no more: an integer interpreter that executes a **minimal, data-selected
subset** of linear memory (store + size/grow) over two `test/core` files and matches the
reference-interpreter oracle on every in-scope assertion. It is **not** a conformance-complete
WebAssembly implementation — loads, data segments, validation, floats, calls, and the many memory
files that require them remain out of scope, deferred to later milestones.

## M4 - Validation Execution (first curation-bounded slice)

M4 now has a **first validator execution slice**, not a complete WebAssembly validator. Its input is
strictly the merged M4 curation artifact: the current pinned M1/M2/M3 converted data contains
**200** validation assertions across the eight target files: 169 `assert_invalid` and 31
`assert_malformed`. The Step-0 enumerator (`tools/enumerate_m4_validation_scope.py`) inventories
every one of them and writes deterministic evidence to `goal-runs/m4-validation/scope.txt` and
`scope.json`.

Current curation result: **65** binary `assert_invalid` modules are validator inputs because the
current decoder parses them and their sections/opcodes stay inside the frozen M1-M3 implemented
surface. `scripts/run_m4.py` validates only those 65 records and reports PASS only when
`interp/validator.py` rejects the module with the category recorded in `scope.json` (`type mismatch`
or `unknown label`). If an included module is accepted, or rejected for the wrong category, M4
reports FAIL and exits nonzero.

The remaining **135** validation assertions are still `UNSUPPORTED` with explicit reasons and are
not decoded, validated, or reclassified as PASS: text malformed WAT requires a WAT parser, or the
binary uses deferred features such as floats, calls, tables/elem, globals, loads, wider/narrow
stores, `select`, or `local.tee`.

This is a fail-closed gate, not a report-only probe:

```sh
python tools/enumerate_m4_validation_scope.py
python tools/validate_m4_goal_run.py --require-scope
git diff --exit-code goal-runs/m4-validation/scope.txt goal-runs/m4-validation/scope.json
python tests/test_validation.py
python tests/positive_control_m4.py
python scripts/run_m4.py              # -> build/report/m4_summary.json
```

M4 currently reports **PASS=65, FAIL=0, UNSUPPORTED=135** and makes **no full WebAssembly
validation conformance claim**. Old runners keep their accounting unchanged; M1 remains
`PASS=877, FAIL=0, UNSUPPORTED=136`, M2 remains `PASS=51, FAIL=0, UNSUPPORTED=4`, and M3 remains
`PASS=45, FAIL=0, UNSUPPORTED=60`.
