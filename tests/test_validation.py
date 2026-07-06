#!/usr/bin/env python3
"""M4 validation unit and scope tests.

Reproduce after converting M0/M2/M3 manifests:
    python tests/test_validation.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from interp import decoder as dec  # noqa: E402
from interp import validator as V  # noqa: E402

SCOPE = ROOT / "goal-runs" / "m4-validation" / "scope.json"
CONVERTED = ROOT / "build" / "converted"
SOURCE_MANIFESTS = ("manifest_m0.json", "manifest_m2.json", "manifest_m3.json")


def uleb(value):
    out = []
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def section(section_id, payload):
    return bytes([section_id]) + uleb(len(payload)) + payload


def duplicate_export_module_bytes():
    type_section = section(1, b"\x01\x60\x00\x00")
    function_section = section(3, b"\x01\x00")
    export_payload = b"\x02" + b"\x01f\x00\x00" + b"\x01f\x00\x00"
    export_section = section(7, export_payload)
    code_section = section(10, b"\x01\x02\x00\x0b")
    return b"\x00asm" + (1).to_bytes(4, "little") + type_section + function_section + export_section + code_section


def C32(v=0):
    return dec.Instr("i32.const", v)


def C64(v=0):
    return dec.Instr("i64.const", v)


def OP(name):
    return dec.Instr(name)


def BLOCK(results=()):
    return dec.Instr("block", bt=list(results))


def IF(results=()):
    return dec.Instr("if", bt=list(results))


def BR(depth):
    return dec.Instr("br", depth)


def BRTABLE(targets, default):
    return dec.Instr("br_table", targets=list(targets), default=default)


END = dec.Instr("end")
ELSE = dec.Instr("else")
DROP = dec.Instr("drop")
RETURN = dec.Instr("return")
SIZE = dec.Instr("memory.size")
GROW = dec.Instr("memory.grow")
STORE = dec.Instr("i32.store", align=2, offset=0)


def module_for(body, params=(), results=(), locals_=(), mem=False):
    m = dec.Module()
    m.types = [dec.FuncType(list(params), list(results))]
    m.func_typeidx = [0]
    m.funcs = [dec.Func(typeidx=0, local_types=list(locals_), body=list(body))]
    m.exports = {"f": 0}
    if mem:
        m.mems = [(1, None)]
    return m


def assert_invalid_category(testcase, module, category):
    with testcase.assertRaises(V.ValidationError) as cm:
        V.validate_module(module)
    testcase.assertEqual(cm.exception.category, category)


def load_scope():
    if not SCOPE.exists():
        raise AssertionError(f"setup missing: {SCOPE}")
    return json.loads(SCOPE.read_text(encoding="utf-8-sig"))


def iter_manifest_targets():
    for manifest_name in SOURCE_MANIFESTS:
        manifest = json.loads((ROOT / manifest_name).read_text(encoding="utf-8-sig"))
        for target in manifest["targets"]:
            yield Path(target["upstream_path"]).name


class ValidatorUnits(unittest.TestCase):
    def test_simple_valid_i32_expression(self):
        V.validate_module(module_for([C32(1), C32(2), OP("i32.add"), END], results=["i32"]))

    def test_operand_underflow_is_type_mismatch(self):
        assert_invalid_category(self, module_for([OP("i32.eqz"), END]), V.TYPE_MISMATCH)

    def test_wrong_numeric_operand_type_is_type_mismatch(self):
        assert_invalid_category(self, module_for([C64(0), OP("i32.clz"), END], results=["i32"]),
                                V.TYPE_MISMATCH)

    def test_function_result_mismatch_is_type_mismatch(self):
        assert_invalid_category(self, module_for([C64(0), END], results=["i32"]), V.TYPE_MISMATCH)

    def test_unknown_label_category(self):
        assert_invalid_category(self, module_for([C32(0), BRTABLE([], 3), END]), V.UNKNOWN_LABEL)

    def test_unreachable_suffix_is_stack_polymorphic(self):
        V.validate_module(module_for([BLOCK(), BR(0), DROP, END, END]))

    def test_br_if_preserves_label_values_on_fallthrough(self):
        V.validate_module(module_for([BLOCK(["i32"]), C32(7), C32(1), dec.Instr("br_if", 0), END, END],
                                     results=["i32"]))

    def test_if_result_requires_else(self):
        assert_invalid_category(self, module_for([C32(1), IF(["i32"]), C32(7), END, END],
                                                 results=["i32"]),
                                V.TYPE_MISMATCH)

    def test_if_else_result_validates(self):
        V.validate_module(module_for([C32(1), IF(["i32"]), C32(7), ELSE, C32(8), END, END],
                                     results=["i32"]))

    def test_memory_ops_require_memory(self):
        assert_invalid_category(self, module_for([SIZE, END]), V.TYPE_MISMATCH)
        assert_invalid_category(self, module_for([C32(1), GROW, END]), V.TYPE_MISMATCH)
        assert_invalid_category(self, module_for([C32(0), C32(1), STORE, END]), V.TYPE_MISMATCH)

    def test_memory_ops_typecheck_with_memory(self):
        V.validate_module(module_for([SIZE, DROP, C32(0), GROW, DROP, C32(0), C32(1), STORE, END],
                                     mem=True))

    def test_store_needs_two_i32_operands(self):
        assert_invalid_category(self, module_for([C32(0), STORE, END], mem=True), V.TYPE_MISMATCH)

    def test_memory_limits_validate(self):
        V.validate_module(module_for([END], mem=True))
        for mem in [(-1, None), (65537, None), (2, 1), (0, 65537)]:
            m = module_for([END])
            m.mems = [mem]
            assert_invalid_category(self, m, V.TYPE_MISMATCH)

    def test_store_alignment_must_not_exceed_natural(self):
        bad_store = dec.Instr("i32.store", align=3, offset=0)
        assert_invalid_category(self, module_for([C32(0), C32(1), bad_store, END], mem=True),
                                V.TYPE_MISMATCH)

    def test_validate_bytes_rejects_duplicate_exports(self):
        with self.assertRaises(V.ValidationError) as cm:
            V.validate_bytes(duplicate_export_module_bytes())
        self.assertEqual(cm.exception.category, V.TYPE_MISMATCH)


class ScopeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scope = load_scope()

    def test_scope_counts_locked(self):
        totals = self.scope["totals"]
        self.assertEqual(totals["validation_assertions"], 200)
        self.assertEqual(totals["included"], 65)
        self.assertEqual(totals["unsupported"], 135)
        self.assertEqual(totals["policy_violations"], 0)
        self.assertFalse(self.scope["policy_violations"])

    def test_all_included_records_reject_with_expected_category(self):
        failures = []
        for record in self.scope["records"]:
            if record["decision"] != "INCLUDED":
                continue
            artifact = CONVERTED / Path(record["source_file"]).stem / record["module_filename"]
            try:
                V.validate_bytes(artifact.read_bytes())
            except V.ValidationError as e:
                if e.category != record["category"]:
                    failures.append((record["validation_index"], e.category, record["category"], str(e)))
            except Exception as e:  # pragma: no cover - kept for diagnostic quality
                failures.append((record["validation_index"], type(e).__name__, record["category"], str(e)))
            else:
                failures.append((record["validation_index"], "ACCEPTED", record["category"], artifact.name))
        self.assertEqual(failures, [])

    def test_unsupported_records_stay_deferred_and_unvalidated(self):
        unsupported = [r for r in self.scope["records"] if r["decision"] == "UNSUPPORTED"]
        self.assertEqual(len(unsupported), 135)
        for record in unsupported:
            self.assertEqual(record["match"], "none")
            self.assertTrue(record["reason"])
            self.assertNotEqual(record["current_decoder"]["status"], "decoder_accept")

    def test_valid_execution_modules_validate(self):
        failures = []
        validated = 0
        for target_name in iter_manifest_targets():
            stem = Path(target_name).stem
            json_path = CONVERTED / stem / f"{stem}.json"
            if not json_path.exists():
                self.fail(f"setup missing: {json_path}")
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
            for cmd in data["commands"]:
                if cmd.get("type") != "module":
                    continue
                artifact = CONVERTED / stem / cmd["filename"]
                try:
                    V.validate_bytes(artifact.read_bytes())
                    validated += 1
                except Exception as e:  # pragma: no cover - kept for diagnostic quality
                    failures.append((target_name, cmd.get("line"), cmd["filename"], type(e).__name__, str(e)))
        self.assertEqual(failures, [])
        self.assertGreater(validated, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
