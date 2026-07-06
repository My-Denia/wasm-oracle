# wasm-oracle: oracle-backed WebAssembly interpreter subset, M0-M4

`wasm-oracle` is a verification-before-implementation demo for a deliberately
small WebAssembly interpreter and validator subset. It is not a complete
WebAssembly implementation. The project demonstrates a development method:
freeze an external oracle first, derive scope from real data, fail closed when
new surface appears, and account for every command as `PASS`, `FAIL`, or
`UNSUPPORTED`.

M0-M4 are the current demo boundary. Continuing into calls, floats, globals,
tables, broader memory behavior, or full validation is possible, but it would
be product implementation work rather than necessary evidence for this method
demo.

## What This Project Demonstrates

The project shows how to grow a small interpreter subset without relying on
hand-authored expected outputs or guessed coverage:

- The semantic oracle is external: the pinned WebAssembly/spec reference
  interpreter and official `test/core` suite.
- WABT is pinned as a conversion and disassembly toolchain only. It is not the
  semantic oracle.
- Manifests freeze the target files, tool versions, feature flags, and explicit
  exclusions.
- Scope is enumerated from converted artifacts before implementation.
- Enumerators fail closed when real data steps outside the current milestone
  boundary.
- Unsupported commands are counted and explained instead of skipped.
- Positive controls inject wrong expectations so a green run proves the
  comparator can fail.
- Independent CI gates preserve each milestone's accounting and evidence.
- Review-loop fixes are treated as evidence that the harness catches latent
  bugs and overclaims before the project expands scope.

## Core Method

1. Freeze the external oracle and conversion toolchain.
2. Curate a target set with reasoned inclusions and exclusions.
3. Enumerate sections, opcodes, command types, and deferred features from real
   converted data.
4. Implement only the enumerated surface.
5. Run the milestone runner against the frozen oracle outputs.
6. Keep no-silent-skip accounting:
   `modules + PASS + FAIL + UNSUPPORTED == total`.
7. Add positive controls that deliberately make the comparator fail.
8. Lock the evidence in CI with independent workflow files per milestone.

## Milestone Results

| Milestone | Scope | Targets | Result | Claim |
| --- | --- | --- | --- | --- |
| M0 | Oracle harness only | `i32.wast`, `i64.wast`, `int_exprs.wast`, `int_literals.wast` | `supported=0 unsupported=1035` | Reproducible harness and frozen manifest; no semantics. |
| M1 | Integer core | same four M0 targets | `PASS=877 FAIL=0 UNSUPPORTED=136` | Integer value assertions and integer traps match the frozen oracle. |
| M2 | Structured control flow | `labels.wast`, `switch.wast` | `PASS=51 FAIL=0 UNSUPPORTED=4` | Data-selected control-flow assertions match the frozen oracle. |
| M3 | Minimal linear memory | `store.wast`, `memory_size.wast` | `PASS=45 FAIL=0 UNSUPPORTED=60` | Memory section, `i32.store`, `memory.size`, and `memory.grow` match the frozen oracle over the curated slice. |
| M4 | Validation curation plus first validator execution slice | validation assertions from M0/M2/M3 manifests | `PASS=65 FAIL=0 UNSUPPORTED=135` over 200 validation records | Decoder-accepted binary invalid modules in the frozen M1-M3 surface reject with the recorded validation category. |

## Why PASS Count Is Not the Only Metric

`UNSUPPORTED` is not a failure and not a skip. It is an explicit accounting
bucket for out-of-scope surface. For this project, honest unsupported counts
matter because they show exactly where the demo boundary is: validation before
M4, floats, calls, globals, tables, loads, data segments, wider or narrow
stores, text WAT malformed cases, and other deferred WebAssembly surface.

The important invariant is that every command lands in a bucket. A milestone is
healthy when in-scope assertions pass, failures are zero, and unsupported
records are counted with reasons.

## Review Findings As Evidence

The Codex review loop found and fixed real latent issues without expanding the
project's claims:

- M2 fixed nested `if`/`else` ownership, where an inner else-less `if` could
  incorrectly claim an outer `else`.
- M2 fixed implicit function-label handling for branches targeting the function
  body depth.
- M3 hardened `memory.grow` against host allocation failure so a large in-cap
  grow returns `-1` instead of risking a process kill.
- M3 decoupled the frozen enumerator opcode policy from mutable decoder state.
- M3 corrected wording around `memory.grow`: the oracle observes refused growth
  through later `memory.size` results, while the `-1` return is unit-tested.
- M4 added memory limit validation for memory32 bounds and min/max ordering.
- M4 added `i32.store` alignment validation for alignment greater than natural
  alignment.
- M4 rejects duplicate export names before the decoded export dictionary can
  collapse them.

These are useful not because they increase coverage, but because they show the
process can catch hidden bugs, stale assumptions, and overclaims while keeping
scope frozen.

## Non-Claims

This project does not claim:

- full WebAssembly conformance;
- full validation conformance;
- floating-point support;
- function calls or `call_indirect`;
- globals, imports, tables, element segments, or start functions;
- loads, data segments, `i64.store`, narrow stores, or broader memory behavior
  beyond the M3 slice;
- bulk-memory, reference types, memory64, or multi-memory;
- JIT, AOT, performance, or production runtime readiness.

## Reproduction Quickstart

On Linux or WSL, from the repository root:

```sh
python scripts/fetch_oracle.py
python scripts/convert.py
python scripts/run_skeleton.py
python scripts/run_m1.py
python scripts/run_m2.py
python scripts/run_m3.py
python scripts/run_m4.py
python tools/validate_m4_goal_run.py --require-scope
```

On Windows, WABT-dependent checks must run through WSL because the pinned WABT
asset is `linux-x64`. That includes conversion, scope enumeration,
`decoder_selftest.py`, and `body_purity_check.py`. The milestone runners are
pure Python once converted artifacts exist.

Useful scope checks:

```sh
wsl.exe -e bash -lc 'cd /mnt/c/Files/wasm-oracle && python3 tools/enumerate_m1_scope.py && git diff --exit-code -- goal-runs/m1-scope.txt'
wsl.exe -e bash -lc 'cd /mnt/c/Files/wasm-oracle && python3 tools/enumerate_m2_scope.py && git diff --exit-code -- goal-runs/m2-control-flow/scope.txt'
wsl.exe -e bash -lc 'cd /mnt/c/Files/wasm-oracle && python3 tools/enumerate_m3_scope.py && git diff --exit-code -- goal-runs/m3-linear-memory/scope.txt'
wsl.exe -e bash -lc 'cd /mnt/c/Files/wasm-oracle && python3 tools/enumerate_m4_validation_scope.py && git diff --exit-code -- goal-runs/m4-validation/scope.txt goal-runs/m4-validation/scope.json'
```

## Suggested Tag Or Release

Suggested owner-created tag names:

- `method-demo-m0-m4`
- `v0.4-validation-slice`

Do not create a tag or GitHub release without owner approval.