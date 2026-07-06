#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
M4 = ROOT / 'goal-runs' / 'm4-validation'
EXPECTED_TOTAL = 200
REQUIRED = ['contract.md', 'plan.md', 'state.json', 'execution-log.md']

def fail(msg: str) -> int:
    print(f'FAIL: {msg}')
    return 1

def main() -> int:
    ap = argparse.ArgumentParser(description='Validate M4 goal-run artifact schema.')
    ap.add_argument('--require-scope', action='store_true')
    args = ap.parse_args()
    missing = [name for name in REQUIRED if not (M4 / name).exists()]
    if missing:
        return fail(f'missing M4 artifact(s): {missing}')
    try:
        state = json.loads((M4 / 'state.json').read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as e:
        return fail(f'state.json is not valid JSON: {e}')
    inv = state.get('validation_inventory') or {}
    if inv.get('expected_total') != EXPECTED_TOTAL:
        return fail(f'state expected_total is {inv.get("expected_total")}, expected {EXPECTED_TOTAL}')
    cts = inv.get('command_types') or {}
    if cts.get('assert_invalid') != 169 or cts.get('assert_malformed') != 31:
        return fail(f'state command type counts are wrong: {cts}')
    owners = state.get('owner_boundaries') or {}
    for key in ('local_commit_requires_owner_approval', 'push_requires_owner_approval', 'pull_request_requires_owner_approval', 'force_push_forbidden_without_explicit_owner_approval', 'merge_forbidden'):
        if owners.get(key) is not True:
            return fail(f'owner boundary {key} is not true')
    if args.require_scope:
        for name in ('scope.txt', 'scope.json'):
            if not (M4 / name).exists():
                return fail(f'missing generated scope artifact: {name}')
        try:
            scope = json.loads((M4 / 'scope.json').read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError as e:
            return fail(f'scope.json is not valid JSON: {e}')
        totals = scope.get('totals') or {}
        if totals.get('validation_assertions') != EXPECTED_TOTAL:
            return fail(f'scope validation_assertions is {totals.get("validation_assertions")}, expected {EXPECTED_TOTAL}')
        if totals.get('included', 0) + totals.get('unsupported', 0) != EXPECTED_TOTAL:
            return fail(f'scope included+unsupported accounting mismatch: {totals}')
        if scope.get('policy_violations'):
            return fail(f'scope has policy violations: {scope["policy_violations"][:5]}')
        records = scope.get('records') or []
        if len(records) != EXPECTED_TOTAL:
            return fail(f'scope records length is {len(records)}, expected {EXPECTED_TOTAL}')
        bad = [r for r in records if r.get('decision') not in {'INCLUDED', 'UNSUPPORTED'}]
        if bad:
            return fail(f'scope has invalid decisions, first: {bad[0]}')
    print('M4 goal-run artifacts: OK')
    return 0
if __name__ == '__main__':
    sys.exit(main())
