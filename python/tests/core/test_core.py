from __future__ import annotations

import inspect
import json
import unittest

from jumperfree.core import SCHEMA, JfError, canonical_json, crc32, crc32_hex, error_envelope
from jumperfree.host.errors import HostError
from jumperfree.routing.model import RouteError


class CoreTests(unittest.TestCase):
    def test_schema_constants(self) -> None:
        self.assertEqual(SCHEMA, {
            "topology": "jf.topology/1", "state": "jf.routing-state/1",
            "plan": "jf.switch-plan/1", "calib": "jf.calib/1", "led": "jf.led-plan/1",
        })

    def test_canonical_json_matches_legacy_bytes(self) -> None:
        value = {"z": [1, True, None], "a": {"中文": "指引", "b": 2}}
        self.assertEqual(canonical_json(value), json.dumps(value, sort_keys=True, separators=(",", ":")))
        self.assertEqual(canonical_json({"z": 1, "a": 2}), '{"a":2,"z":1}')
        self.assertEqual(canonical_json({"a": 2, "z": 1}), canonical_json({"z": 1, "a": 2}))

    def test_crc32_known_vectors_unsigned_and_hex_width(self) -> None:
        for data, expected, hex_value in (
            (b"", 0, "00000000"), (b"123456789", 0xCBF43926, "cbf43926"),
            (b"a", 0xE8B7BE43, "e8b7be43"), (b"hello", 0x3610A686, "3610a686"),
        ):
            with self.subTest(data=data):
                self.assertEqual(crc32(data), expected)
                self.assertEqual(crc32_hex(data), hex_value)

    def test_existing_error_contract_and_import_paths(self) -> None:
        self.assertEqual(HostError.__module__, "jumperfree.host.errors")
        self.assertEqual(RouteError.__module__, "jumperfree.routing.model")
        for cls in (JfError, HostError, RouteError):
            self.assertTrue(issubclass(cls, ValueError))
            self.assertTrue(issubclass(cls, JfError))
            self.assertEqual(inspect.signature(cls), inspect.signature(JfError))
            error = cls("CODE", "message")
            self.assertEqual(str(error), "message")
            self.assertEqual(error.args, ("message",))
            self.assertEqual(error.as_dict(), {"code": "CODE", "message": "message"})
            error = cls("CODE", "message", net="a", device_code="BUSY")
            self.assertEqual(error.as_dict(), {
                "code": "CODE", "message": "message", "details": {"net": "a", "device_code": "BUSY"},
            })
            error.details["close_error"] = {"code": "CLOSE"}
            self.assertEqual(error.as_dict()["details"]["close_error"], {"code": "CLOSE"})

    def test_envelope_defaults_and_allowed_categories(self) -> None:
        for layer in ("routing", "plan", "protocol", "device", "safety", "consistency"):
            self.assertEqual(error_envelope("CODE", layer, "message"), {
                "ok": False, "error": {
                    "code": "CODE", "layer": layer, "message": "message", "details": {},
                    "retryable": False, "state_changed": "no",
                },
            })
        details = {"txn_id": 41}
        for changed in ("no", "yes", "unknown"):
            envelope = error_envelope("TIMEOUT", "protocol", "timeout", details, True, changed)
            self.assertEqual(envelope["error"], {
                "code": "TIMEOUT", "layer": "protocol", "message": "timeout", "details": details,
                "retryable": True, "state_changed": changed,
            })
            self.assertIsNot(envelope["error"]["details"], details)

    def test_envelope_rejects_invalid_categories(self) -> None:
        for layer in ("host", "", "Routing", None):
            with self.subTest(layer=layer), self.assertRaisesRegex(ValueError, "invalid error layer"):
                error_envelope("CODE", layer, "message")
        for changed in ("maybe", "", "NO", None):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "invalid state_changed"):
                error_envelope("CODE", "device", "message", state_changed=changed)
