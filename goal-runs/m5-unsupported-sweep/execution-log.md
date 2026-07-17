# Execution Log

## 2026-07-13T20:20:49+00:00

- Status: planning
- Event: run initialized for `m5-unsupported-sweep`.

## 2026-07-13T20:21:07+00:00

- Workspace baseline: fresh clone of My-Denia/wasm-oracle at dc29ee8 (main), clean tree. Branch m5-unsupported-sweep created off dc29ee8. User prompt said main=40a5d45(M1) — stale: M2/M3/M4 already merged (drift recorded). vendor/ build/ gitignored, refetching via WSL. Locked regression counts: M0 0/1035, M1 877/0/136, M2 51/0/4, M3 45/0/60, M4 65/0/135.

## 2026-07-13T20:37:47+00:00

- SubagentStop: agent_type=plan-auditor agent_id=a014f3e23120a6137 tool_calls=? decision=needs-replan

## 2026-07-13T20:41:39+00:00

- SubagentStop: agent_type=plan-auditor agent_id=a014f3e23120a6137 tool_calls=? decision=pass

## 2026-07-13T20:41:53+00:00

- Plan gate PASS (round 2). Residuals: M5-5+M5-6 report as a pair; text-format assert_invalid lands UNSUPPORTED; enumerate_m4 self-check stays on in regression script.

## 2026-07-13T20:45:11+00:00

- M1(scaffold) DONE: manifest_m5.json (97 targets, 42 expected convert-fail, judgment_boundaries) + scripts/convert_m5.py. WSL run: exit 0, 55 ok/42 CONVERT-FAIL match, 18149 commands, boundaries clean. AC1 met.

## 2026-07-13T20:56:32+00:00

- M2(fvalues)+M3(decoder) DONE. Floats: tests/test_m5_floats.py all green (20k Fraction-reference vectors + edges; found+fixed reference renorm bug). Decoder: 606 module binaries, 19925 opcode tokens agree with pinned wasm-objdump; export-name compare restricted to unambiguous names (objdump text ambiguity), name correctness covered by oracle invokes. Dispatching validator executor subagent in parallel with machine work.

## 2026-07-13T21:05:39+00:00

- SubagentStop: agent_type=general-purpose agent_id=ab7bba8e32f5e75a2 tool_calls=? decision=no-decision

## 2026-07-13T21:09:45+00:00

- M4(machine)+M5(validator,subagent)+M6(runner+posctl)+M7(full run)+M8(regression) DONE. Full sweep: 55 files, 18149 commands, PASS=17053 FAIL=0 UNSUPPORTED=447 (exactly the 446+1 text-format .wat boundaries). Identical on Windows+WSL. Validator: 605/605 valid clean, 684/684 invalid byte-exact texts. Positive controls green both platforms. Regression gate exit 0: M0 0/1035, M1 877/0/136, M2 51/0/4, M3 45/0/60, M4 200=65+135 & 65/0/135, tree additive-only. account.md generated.

## 2026-07-13T21:17:25+00:00

- SubagentStop: agent_type=execution-auditor agent_id=a0c5d2222136d3316 tool_calls=? decision=pass

## 2026-07-13T21:18:35+00:00

- Execution audit PASS (independent re-run of all gates + auditor's own corrupted-oracle control + 5 hand-verified cases). Cosmetic fix applied after audit: decoder selftest now exits cleanly on native Windows with a WSL pointer; re-verified green in WSL.
