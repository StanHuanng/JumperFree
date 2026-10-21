from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from jumperfree.routing.model import RouteError
from jumperfree.routing.scenarios import Scenario, load_scenario, scenario_to_route_requests


def valid_scenario() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "schema-test",
        "title": "Schema test",
        "topology": {
            "chips": 2, "x_lines": 4, "y_lines": 4, "links_per_pair": 1,
            "clip_rows": 2, "clips_per_row": 20, "mcu_links": 1,
        },
        "mcu": {"kind": "adapter", "pins": {"A": "R0C0"}},
        "modules": [{
            "id": "part", "kind": "part", "placement": {"row_range": [0, 2]},
            "pins": {"A": {"offset": 0}, "UNUSED": None},
        }],
        "nets": [{"id": "a", "members": ["mcu.A", "part.A"]}],
        "sources": ["https://example.com/source"],
        "assumptions": [],
        "expected": {"routeable": True},
    }


def changed(path: tuple[object, ...], value: object = None, *, delete: bool = False) -> dict[str, object]:
    data = valid_scenario()
    target: object = data
    for item in path[:-1]:
        target = target[item]  # type: ignore[index]
    if delete:
        del target[path[-1]]  # type: ignore[index]
    else:
        target[path[-1]] = value  # type: ignore[index]
    return data


class ScenarioSchemaTests(unittest.TestCase):
    def assert_code(self, source: object, code: str = "INVALID_SCENARIO") -> RouteError:
        with self.assertRaises(RouteError) as caught:
            load_scenario(source)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, code)
        self.assertIn("path", caught.exception.details)
        return caught.exception

    def test_dict_and_file_load_are_equal_and_immutable(self) -> None:
        data = valid_scenario()
        snapshot = copy.deepcopy(data)
        from_dict = load_scenario(data)
        self.assertIsInstance(from_dict, Scenario)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(load_scenario(path), from_dict)
            self.assertEqual(load_scenario(str(path)), from_dict)
        self.assertEqual(data, snapshot)
        data["id"] = "mutated"
        self.assertEqual(from_dict.id, "schema-test")
        with self.assertRaises(TypeError):
            from_dict.mcu.pins["B"] = from_dict.mcu.pins["A"]  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            from_dict.id = "x"  # type: ignore[misc]
        self.assertEqual(scenario_to_route_requests(from_dict), [("a", ["MCU_R0_C0", "N0"])])

    def test_file_json_and_source_failures_are_structured(self) -> None:
        class Uncopyable(dict[str, object]):
            def __deepcopy__(self, memo: dict[int, object]) -> object:
                raise RuntimeError("no copy")

        self.assert_code(3)
        self.assert_code(Uncopyable())
        self.assert_code(Path("definitely-missing-scenario.json"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "syntax.json": "{",
                "root.json": "[]",
                "duplicate.json": '{"id":"a","id":"b"}',
                "nan.json": '{"value":NaN}',
            }
            for name, content in files.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                self.assert_code(path)
            binary = root / "binary.json"
            binary.write_bytes(b"\xff")
            self.assert_code(binary)

    def test_root_identity_version_and_topology_validation(self) -> None:
        cases = [
            changed(("sources",), delete=True),
            {**valid_scenario(), "extra": 1},
            changed(("id",), "bad.id"),
            changed(("title",), " "),
            changed(("schema_version",), True),
            changed(("schema_version",), 2),
            changed(("topology",), []),
            changed(("topology",), None),
            changed(("topology", "unknown"), 1),
            changed(("topology", "chips"), True),
            changed(("topology", "chips"), 0),
            changed(("topology", "clip_rows"), 3),
            changed(("topology", "links_per_pair"), 5),
            changed(("topology", "mcu_links"), 4),
        ]
        for case in cases:
            with self.subTest(case=case):
                error = self.assert_code(case)
                if isinstance(case, dict) and case.get("id") == "schema-test":
                    self.assertEqual(error.details.get("scenario_id"), "schema-test")
        without_optional = valid_scenario()
        del without_optional["schema_version"]
        del without_optional["topology"]
        self.assertEqual(load_scenario(without_optional).schema_version, 1)
        nonscalar_key = valid_scenario()
        nonscalar_key[1] = "invalid"  # type: ignore[index]
        self.assert_code(nonscalar_key)

    def test_mcu_and_module_shape_validation(self) -> None:
        cases = [
            changed(("mcu",), []),
            changed(("mcu", "extra"), 1),
            changed(("mcu", "kind"), ""),
            changed(("mcu", "pins"), {}),
            changed(("mcu", "pins", "A"), "bad"),
            changed(("mcu", "pins", "A"), {"row": 0}),
            changed(("mcu", "pins", "A"), {"row": True, "col": 0}),
            changed(("modules",), []),
            changed(("modules", 0), []),
            changed(("modules", 0, "extra"), 1),
            changed(("modules", 0, "id"), "mcu"),
            changed(("modules", 0, "kind"), 1),
            changed(("modules", 0, "pins"), {}),
            changed(("modules", 0, "pins", "A"), True),
            changed(("modules", 0, "pins", "A"), {"offset": -1}),
            changed(("modules", 0, "pins", "A"), {"offset": 0, "x": 1}),
            changed(("modules", 0, "placement"), {}),
            changed(("modules", 0, "placement"), {"rows": [], "row_range": [0, 1]}),
            changed(("modules", 0, "placement"), {"row_range": [0]}),
            changed(("modules", 0, "placement"), {"row_range": [2, 1]}),
            changed(("modules", 0, "placement"), {"rows": []}),
            changed(("modules", 0, "placement"), {"rows": [0, 0]}),
        ]
        duplicate_module = valid_scenario()
        duplicate_module["modules"].append(copy.deepcopy(duplicate_module["modules"][0]))  # type: ignore[union-attr,index]
        cases.append(duplicate_module)
        for case in cases:
            with self.subTest(case=case):
                self.assert_code(case)

    def test_net_source_assumption_and_expectation_validation(self) -> None:
        cases = [
            changed(("nets",), []),
            changed(("nets", 0), []),
            changed(("nets", 0, "extra"), 1),
            changed(("nets", 0, "id"), "bad.id"),
            changed(("nets", 0, "members"), ["mcu.A"]),
            changed(("nets", 0, "members"), ["mcu.A", 2]),
            changed(("nets", 0, "members"), ["mcu.A", "bad"]),
            changed(("nets", 0, "members"), ["mcu.A", "mcu.A"]),
            changed(("sources",), []),
            changed(("sources",), ["ftp://example.com/a"]),
            changed(("assumptions",), "none"),
            changed(("assumptions",), [""]),
            changed(("expected",), []),
            changed(("expected", "routeable"), 1),
            changed(("expected", "error_code"), "NO_FREE_PATH"),
            changed(("expected",), {"routeable": False}),
            changed(("expected",), {"routeable": False, "error_code": "BAD.CODE", "known_limitation": "x"}),
        ]
        duplicate_net = valid_scenario()
        duplicate_net["nets"].append(copy.deepcopy(duplicate_net["nets"][0]))  # type: ignore[union-attr,index]
        cases.append(duplicate_net)
        endpoint_twice = valid_scenario()
        endpoint_twice["mcu"]["pins"]["B"] = "R0C1"  # type: ignore[index]
        endpoint_twice["modules"][0]["pins"]["B"] = None  # type: ignore[index]
        endpoint_twice["nets"].append({"id": "b", "members": ["mcu.A", "part.B"]})  # type: ignore[union-attr]
        cases.append(endpoint_twice)
        for case in cases:
            with self.subTest(case=case):
                self.assert_code(case)

        negative = changed(("expected",), {
            "routeable": False,
            "error_code": "NO_FREE_PATH",
            "known_limitation": "one trunk",
        })
        expected = load_scenario(negative).expected
        self.assertFalse(expected.routeable)
        self.assertEqual(expected.error_code, "NO_FREE_PATH")

    def test_malformed_source_url_is_a_structured_error(self) -> None:
        error = self.assert_code(changed(("sources",), ["http://["]))
        self.assertEqual(error.details["path"], "$.sources[0]")

    def test_reference_and_clip_errors_use_specific_codes(self) -> None:
        outside = changed(("mcu", "pins", "A"), {"row": 0, "col": 20})
        self.assert_code(outside, "MCU_PIN_OUTSIDE_CLIP_ZONE")

        duplicate_clip = valid_scenario()
        duplicate_clip["mcu"]["pins"]["B"] = "R0C0"  # type: ignore[index]
        error = self.assert_code(duplicate_clip, "CONFLICTING_PIN_PLACEMENT")
        self.assertEqual(error.details["pin"], "B")

        unknown = changed(("nets", 0, "members"), ["mcu.A", "missing.A"])
        error = self.assert_code(unknown, "UNKNOWN_PIN")
        self.assertEqual(error.details["endpoint"], "missing.A")


if __name__ == "__main__":
    unittest.main()
