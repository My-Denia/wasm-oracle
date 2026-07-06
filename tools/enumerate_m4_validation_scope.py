#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from collections import Counter
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from interp import decoder as dec  # noqa: E402
CONVERTED = ROOT / 'build' / 'converted'
OUT_DIR = ROOT / 'goal-runs' / 'm4-validation'
SCOPE_TXT = OUT_DIR / 'scope.txt'
SCOPE_JSON = OUT_DIR / 'scope.json'
BUILD_REPORT = ROOT / 'build' / 'report' / 'm4_scope.json'
OBJDUMP = os.environ.get('WASM_OBJDUMP', str(ROOT / 'vendor' / 'wabt' / 'bin' / 'wasm-objdump'))
SOURCE_MANIFESTS = ('manifest_m0.json', 'manifest_m2.json', 'manifest_m3.json')
EXPECTED_TOTAL = 200
VALIDATION_COMMANDS = {'assert_invalid', 'assert_malformed'}
EXPECTED_TEXT_POLICY = {
    ('assert_invalid', 'type mismatch'): {'decision_if_decoder_accepts': 'INCLUDED', 'category': 'validation_type_mismatch_existing_surface', 'match': 'category'},
    ('assert_invalid', 'unknown label'): {'decision_if_decoder_accepts': 'INCLUDED', 'category': 'branch_depth_or_unknown_label_existing_surface', 'match': 'category'},
    ('assert_malformed', 'unknown operator'): {'decision_if_decoder_accepts': 'UNSUPPORTED', 'category': 'text_malformed_requires_wat_parser', 'match': 'none'},
    ('assert_malformed', 'unexpected token'): {'decision_if_decoder_accepts': 'UNSUPPORTED', 'category': 'text_malformed_requires_wat_parser', 'match': 'none'},
}
ALLOWED_SECTIONS = frozenset({'Type', 'Function', 'Export', 'Code', 'Memory'})
FROZEN_M1_M3_OPS = frozenset({
    'i32.add','i32.and','i32.clz','i32.const','i32.ctz','i32.div_s','i32.div_u','i32.eq','i32.eqz','i32.extend16_s','i32.extend8_s','i32.ge_s','i32.ge_u','i32.gt_s','i32.gt_u','i32.le_s','i32.le_u','i32.lt_s','i32.lt_u','i32.mul','i32.ne','i32.or','i32.popcnt','i32.rem_s','i32.rem_u','i32.rotl','i32.rotr','i32.shl','i32.shr_s','i32.shr_u','i32.sub','i32.wrap_i64','i32.xor',
    'i64.add','i64.and','i64.clz','i64.const','i64.ctz','i64.div_s','i64.div_u','i64.eq','i64.eqz','i64.extend16_s','i64.extend32_s','i64.extend8_s','i64.extend_i32_s','i64.extend_i32_u','i64.ge_s','i64.ge_u','i64.gt_s','i64.gt_u','i64.le_s','i64.le_u','i64.lt_s','i64.lt_u','i64.mul','i64.ne','i64.or','i64.popcnt','i64.rem_s','i64.rem_u','i64.rotl','i64.rotr','i64.shl','i64.shr_s','i64.shr_u','i64.sub','i64.xor',
    'local.get','end','return','nop','block','loop','if','else','br','br_if','br_table','drop','local.set','i32.store','memory.size','memory.grow'
})

def _on_path(name: str) -> bool:
    return shutil.which(name) is not None

def objdump(args: list[str], wasm: Path) -> tuple[str | None, str | None]:
    res = subprocess.run([OBJDUMP, *args, str(wasm)], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if res.returncode != 0:
        return None, res.stderr.strip()
    return res.stdout, None

def sections_of(wasm: Path) -> tuple[list[str], str | None]:
    out, err = objdump(['-h'], wasm)
    if err:
        return [], err
    names, in_sections = [], False
    for ln in out.splitlines():
        if ln.strip() == 'Sections:':
            in_sections = True
            continue
        if in_sections and ln.strip():
            names.append(ln.strip().split()[0])
    return names, None

def opcodes_of(wasm: Path) -> tuple[list[str], str | None]:
    out, err = objdump(['-d'], wasm)
    if err:
        return [], err
    ops = []
    for ln in out.splitlines():
        if '|' not in ln:
            continue
        rhs = ln.split('|', 1)[1].strip()
        if not rhs:
            continue
        mn = rhs.split()[0]
        if not mn.startswith('local['):
            ops.append(mn)
    return ops, None

def manifest_targets(manifest_name: str) -> list[str]:
    manifest = json.loads((ROOT / manifest_name).read_text(encoding='utf-8-sig'))
    return [Path(t['upstream_path']).name for t in manifest['targets']]

def source_files() -> list[tuple[str, str]]:
    out = []
    for manifest in SOURCE_MANIFESTS:
        for target in manifest_targets(manifest):
            out.append((manifest, target))
    return out

def decoder_outcome(path: Path, module_type: str) -> tuple[str, str]:
    if module_type != 'binary':
        return 'text_artifact', 'text artifact (WAT parser out of scope)'
    try:
        dec.decode(path.read_bytes())
    except dec.DecodeError as e:
        return 'decode_error', str(e)
    except dec.Unsupported as e:
        return 'unsupported', str(e)
    except Exception as e:
        return 'unexpected_exception', f'{type(e).__name__}: {e}'
    return 'decoder_accept', 'current decoder accepts binary'

def deferred_features(sections: set[str], opcodes: set[str], outcome_detail: str) -> list[str]:
    feats = set()
    if {'Table', 'Elem'} & sections or {'call_indirect'} & opcodes:
        feats.add('tables_elem_call_indirect')
    if 'Global' in sections or any(o.startswith('global.') for o in opcodes):
        feats.add('globals')
    if any(o in {'call', 'call_indirect'} for o in opcodes):
        feats.add('calls')
    if 'select' in opcodes:
        feats.add('select')
    if 'local.tee' in opcodes:
        feats.add('local_tee')
    if 'unreachable' in opcodes:
        feats.add('unreachable')
    if any('.load' in o for o in opcodes):
        feats.add('loads')
    if any('.store' in o and o != 'i32.store' for o in opcodes):
        feats.add('wide_or_narrow_or_float_stores')
    if any(o.startswith('f32.') or o.startswith('f64.') for o in opcodes):
        feats.add('floats')
    if 'float value type' in outcome_detail:
        feats.add('floats')
    if 'section id 2' in outcome_detail or 'Import' in sections:
        feats.add('imports')
    if 'section id 8' in outcome_detail or 'Start' in sections:
        feats.add('start')
    if 'section id 11' in outcome_detail or 'Data' in sections:
        feats.add('data_segments')
    if 'memory64' in outcome_detail:
        feats.add('memory64')
    if 'multi-memory' in outcome_detail or 'non-zero memory index' in outcome_detail:
        feats.add('multi_memory')
    if 'shared memory' in outcome_detail:
        feats.add('shared_memory')
    return sorted(feats)

def classify_policy(*, command_type: str, text: str, module_type: str, decoder_status: str, decoder_detail: str, sections: list[str], opcodes: list[str]) -> dict[str, Any]:
    key = (command_type, text)
    if command_type not in VALIDATION_COMMANDS:
        raise ValueError(f'unknown validation command type {command_type!r}')
    if key not in EXPECTED_TEXT_POLICY:
        raise ValueError(f'unexpected validation text {key!r}')
    policy = EXPECTED_TEXT_POLICY[key]
    sec_set, op_set = set(sections), set(opcodes)
    extra_sections = sorted(sec_set - ALLOWED_SECTIONS)
    extra_opcodes = sorted(op_set - FROZEN_M1_M3_OPS)
    feats = deferred_features(sec_set, op_set, decoder_detail)
    if module_type == 'text':
        if command_type != 'assert_malformed':
            raise ValueError(f'text artifact for non-malformed command {command_type!r}')
        return {'decision':'UNSUPPORTED','category':'text_malformed_requires_wat_parser','reason':'text WAT malformed assertion; repository has no WAT parser','match':'none','deferred_features':['wat_parser'],'extra_sections':extra_sections,'extra_opcodes':extra_opcodes}
    if module_type != 'binary':
        raise ValueError(f'unknown module_type {module_type!r}')
    if decoder_status == 'decoder_accept':
        if extra_sections:
            raise ValueError(f'decoder accepted artifact with sections outside M1-M3 surface: {extra_sections}')
        if extra_opcodes:
            raise ValueError(f'decoder accepted artifact with opcodes outside M1-M3 surface: {extra_opcodes}')
        if feats:
            raise ValueError(f'decoder accepted artifact with deferred features: {feats}')
        if command_type != 'assert_invalid':
            raise ValueError(f'decoder accepted non-assert_invalid artifact {command_type!r}')
        if policy['decision_if_decoder_accepts'] != 'INCLUDED':
            raise ValueError(f'policy does not admit decoder-accepted artifact for {key!r}')
        return {'decision':'INCLUDED','category':policy['category'],'reason':'binary assert_invalid parsed by current decoder and stays within frozen M1-M3 surface','match':policy['match'],'deferred_features':[],'extra_sections':[],'extra_opcodes':[]}
    if decoder_status == 'unsupported':
        if not (feats or extra_sections or extra_opcodes):
            raise ValueError(f'decoder Unsupported without a classified deferred feature: {decoder_detail!r}')
        reason_bits = feats or extra_sections or extra_opcodes
        return {'decision':'UNSUPPORTED','category':'unsupported_feature_contamination','reason':'requires deferred feature(s): ' + ', '.join(reason_bits),'match':'none','deferred_features':feats,'extra_sections':extra_sections,'extra_opcodes':extra_opcodes}
    if decoder_status == 'decode_error':
        raise ValueError(f'binary DecodeError requires explicit M4 policy before admission: {decoder_detail!r}')
    raise ValueError(f'unexpected decoder status {decoder_status!r}: {decoder_detail!r}')

def _assert_gate_live() -> list[str]:
    failures = []
    clean = classify_policy(command_type='assert_invalid', text='type mismatch', module_type='binary', decoder_status='decoder_accept', decoder_detail='ok', sections=['Type','Function','Code'], opcodes=['i32.const','i32.add','end'])
    if clean['decision'] != 'INCLUDED':
        failures.append('clean decoder-accepted type mismatch was not included')
    text = classify_policy(command_type='assert_malformed', text='unknown operator', module_type='text', decoder_status='text_artifact', decoder_detail='text', sections=[], opcodes=[])
    if text['decision'] != 'UNSUPPORTED':
        failures.append('text malformed artifact was not unsupported')
    injections = [
        ('unknown expected text', dict(command_type='assert_invalid', text='new text', module_type='binary', decoder_status='decoder_accept', decoder_detail='ok', sections=['Type','Function','Code'], opcodes=['end'])),
        ('accepted float opcode', dict(command_type='assert_invalid', text='type mismatch', module_type='binary', decoder_status='decoder_accept', decoder_detail='ok', sections=['Type','Function','Code'], opcodes=['f32.const','end'])),
        ('unsupported but unclassified', dict(command_type='assert_invalid', text='type mismatch', module_type='binary', decoder_status='unsupported', decoder_detail='opaque unsupported', sections=['Type','Function','Code'], opcodes=['i32.const','end'])),
        ('decode error needs policy', dict(command_type='assert_malformed', text='unexpected token', module_type='binary', decoder_status='decode_error', decoder_detail='bad magic', sections=[], opcodes=[])),
    ]
    for label, kwargs in injections:
        try:
            classify_policy(**kwargs)
        except ValueError:
            continue
        failures.append(f'gate did not flag injected out-of-scope case: {label}')
    return failures

def gather_records() -> tuple[list[dict[str, Any]], list[str]]:
    violations, records, validation_index = [], [], 0
    for manifest_name, target_name in source_files():
        stem = Path(target_name).stem
        json_path = CONVERTED / stem / f'{stem}.json'
        if not json_path.exists():
            violations.append(f'converted JSON missing: {json_path.relative_to(ROOT)}')
            continue
        data = json.loads(json_path.read_text(encoding='utf-8-sig'))
        for command_index, cmd in enumerate(data.get('commands', [])):
            ctype = cmd.get('type')
            if ctype not in VALIDATION_COMMANDS:
                continue
            module_type, filename, text = cmd.get('module_type'), cmd.get('filename'), cmd.get('text', '')
            if not filename:
                violations.append(f'{target_name} command {command_index}: validation command missing filename')
                continue
            artifact = CONVERTED / stem / filename
            if not artifact.exists():
                violations.append(f'{target_name} command {command_index}: artifact missing {filename}')
                continue
            status, detail = decoder_outcome(artifact, module_type)
            sections, opcodes, objdump_errors = [], [], []
            if module_type == 'binary':
                sections, sec_err = sections_of(artifact)
                opcodes, op_err = opcodes_of(artifact)
                if sec_err: objdump_errors.append(f'sections: {sec_err}')
                if op_err: objdump_errors.append(f'opcodes: {op_err}')
                if objdump_errors: violations.append(f'{target_name} command {command_index}: wasm-objdump failed: {"; ".join(objdump_errors)}')
            try:
                policy = classify_policy(command_type=ctype, text=text, module_type=module_type, decoder_status=status, decoder_detail=detail, sections=sections, opcodes=opcodes)
            except ValueError as e:
                policy = {'decision':'POLICY_VIOLATION','category':'policy_violation','reason':str(e),'match':'none','deferred_features':[],'extra_sections':[],'extra_opcodes':[]}
                violations.append(f'{target_name} command {command_index}: {e}')
            records.append({'validation_index':validation_index,'manifest':manifest_name,'source_file':target_name,'command_index':command_index,'line':cmd.get('line'),'command_type':ctype,'module_filename':filename,'module_type':module_type,'artifact_kind':'binary' if module_type == 'binary' else 'text','expected_text':text,'current_decoder':{'status':status,'detail':detail},'sections':sorted(set(sections)),'opcodes':sorted(set(opcodes)),'uses_only_existing_surface':status == 'decoder_accept' and not policy['extra_sections'] and not policy['extra_opcodes'] and not policy['deferred_features'],'decision':policy['decision'],'category':policy['category'],'reason':policy['reason'],'match':policy['match'],'deferred_features':policy['deferred_features'],'extra_sections':policy['extra_sections'],'extra_opcodes':policy['extra_opcodes']})
            validation_index += 1
    if validation_index != EXPECTED_TOTAL:
        violations.append(f'validation assertion total {validation_index} != expected {EXPECTED_TOTAL}')
    return records, violations

def summarize(records: list[dict[str, Any]], violations: list[str]) -> dict[str, Any]:
    by_file = Counter({target: 0 for _manifest, target in source_files()})
    by_file.update(r['source_file'] for r in records)
    by_type = Counter(r['command_type'] for r in records)
    by_text = Counter(f"{r['command_type']}::{r['expected_text']}" for r in records)
    by_decoder = Counter(r['current_decoder']['status'] for r in records)
    by_decision = Counter(r['decision'] for r in records)
    by_category = Counter(r['category'] for r in records)
    by_feature = Counter(f for r in records for f in r['deferred_features'])
    return {'milestone':'M4','title':'Validation curation scope','source_manifests':list(SOURCE_MANIFESTS),'expected_total':EXPECTED_TOTAL,'policy':{'allowed_candidate_texts':[{'command_type':k[0],'expected_text':k[1],**v} for k,v in sorted(EXPECTED_TEXT_POLICY.items())],'allowed_sections':sorted(ALLOWED_SECTIONS),'allowed_opcodes':sorted(FROZEN_M1_M3_OPS),'deferred_policy':['text WAT malformed assertions require a WAT parser','floats remain M5','calls, tables/elem, globals/imports/start remain future milestones','loads, wider/narrow stores, data segments, bulk-memory remain future memory milestones','select, local.tee, unreachable, multi-value/typeidx block types remain future control-flow validation']},'totals':{'validation_assertions':len(records),'included':by_decision.get('INCLUDED',0),'unsupported':by_decision.get('UNSUPPORTED',0),'policy_violations':by_decision.get('POLICY_VIOLATION',0),'by_file':dict(sorted(by_file.items())),'by_command_type':dict(sorted(by_type.items())),'by_expected_text':dict(sorted(by_text.items())),'by_decoder_status':dict(sorted(by_decoder.items())),'by_decision':dict(sorted(by_decision.items())),'by_category':dict(sorted(by_category.items())),'by_deferred_feature':dict(sorted(by_feature.items()))},'policy_violations':violations,'records':records}

def write_json(report: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_REPORT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    SCOPE_JSON.write_text(text, encoding='utf-8')
    BUILD_REPORT.write_text(text, encoding='utf-8')

def write_text(report: dict[str, Any]) -> None:
    totals, lines = report['totals'], []
    lines += ['M4 VALIDATION SCOPE - Step 0 curation, FAIL-CLOSED', '=' * 72, 'source manifests : ' + ', '.join(SOURCE_MANIFESTS), f"validation total : {totals['validation_assertions']} (expected {EXPECTED_TOTAL})", f"included         : {totals['included']}", f"unsupported      : {totals['unsupported']}", f"policy violations: {totals['policy_violations']}", '']
    for title, key in [('COMMAND TYPES','by_command_type'),('EXPECTED TEXTS','by_expected_text'),('DECODER OUTCOMES','by_decoder_status'),('DECISIONS','by_decision'),('CATEGORIES','by_category'),('DEFERRED FEATURES','by_deferred_feature')]:
        lines.append(title + ':')
        vals = totals[key]
        if vals:
            for k, v in vals.items():
                lines.append(f'  {k:48} {v:4}')
        else:
            lines.append('  NONE')
        lines.append('')
    lines.append('PER-FILE:')
    for file_name, count in totals['by_file'].items():
        inc = sum(1 for r in report['records'] if r['source_file'] == file_name and r['decision'] == 'INCLUDED')
        uns = sum(1 for r in report['records'] if r['source_file'] == file_name and r['decision'] == 'UNSUPPORTED')
        lines.append(f'  {file_name:18} total={count:3} included={inc:3} unsupported={uns:3}')
    lines += ['', 'INCLUDED ASSERTIONS (future validator candidates):']
    for r in report['records']:
        if r['decision'] == 'INCLUDED':
            lines.append(f"  #{r['validation_index']:03d} {r['source_file']:18} cmd={r['command_index']:3} line={r['line']:4} text={r['expected_text']!r} category={r['category']}")
    lines += ['', 'UNSUPPORTED SUMMARY:']
    for reason, count in sorted(Counter(r['reason'] for r in report['records'] if r['decision'] == 'UNSUPPORTED').items()):
        lines.append(f'  {count:3} x {reason}')
    lines += ['', 'FULL PER-COMMAND INVENTORY:', '  See goal-runs/m4-validation/scope.json records[].', '', 'SCOPE GATE:']
    if report['policy_violations']:
        lines.append('  VERDICT: OUT OF SCOPE - policy violations present.')
        for v in report['policy_violations'][:20]: lines.append(f'    >>> {v}')
    else:
        lines += ['  VERDICT: IN SCOPE FOR CURATION - all 200 validation assertions accounted for;', '           validator candidates are limited to decoder-accepted binary invalid modules', '           over the frozen M1-M3 surface; all other cases remain UNSUPPORTED.']
    SCOPE_TXT.write_text('\n'.join(lines) + '\n', encoding='utf-8')

def main() -> int:
    ap = argparse.ArgumentParser(description='Enumerate and gate M4 validation curation scope.')
    ap.add_argument('--skip-self-check', action='store_true')
    args = ap.parse_args()
    if not args.skip_self_check:
        self_fails = _assert_gate_live()
        if self_fails:
            print('FAIL: M4 scope policy self-check failed:')
            for f in self_fails: print(f'  >>> {f}')
            return 1
    if not (os.path.exists(OBJDUMP) or _on_path(OBJDUMP)):
        print(f'FAIL: wasm-objdump not found at {OBJDUMP}. Run scripts/fetch_oracle.py first.')
        return 1
    records, violations = gather_records()
    report = summarize(records, violations)
    write_json(report)
    write_text(report)
    totals = report['totals']
    print(SCOPE_TXT.read_text(encoding='utf-8'))
    print(f'wrote {SCOPE_TXT.relative_to(ROOT)}')
    print(f'wrote {SCOPE_JSON.relative_to(ROOT)}')
    print(f'wrote {BUILD_REPORT.relative_to(ROOT)}')
    print('gate self-check: PASS (policy flags unknown text, accepted deferred feature, unclassified Unsupported, and binary DecodeError without policy)')
    if totals['validation_assertions'] != EXPECTED_TOTAL:
        print(f"FAIL: expected {EXPECTED_TOTAL} validation assertions, got {totals['validation_assertions']}")
        return 1
    if totals['included'] + totals['unsupported'] != EXPECTED_TOTAL:
        print(f'FAIL: included+unsupported accounting mismatch: {totals}')
        return 1
    if report['policy_violations']:
        print('FAIL: policy violations present; stop and update M4 curation policy.')
        return 1
    return 0
if __name__ == '__main__':
    sys.exit(main())
