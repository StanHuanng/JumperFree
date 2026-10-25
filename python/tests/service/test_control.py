"""Control acceptance against real routing, board plans and the memory device."""

from __future__ import annotations

import contextlib
import copy
from dataclasses import replace
import io
import json
import unittest
from unittest import mock

from jumperfree.board import BoardProfile, compile_plan, shadow_from_state
from jumperfree.cli import main
from jumperfree.core import canonical_json
from jumperfree.host import ApplyResult, DeviceClient, HostError
from jumperfree.host.mock_device import MockDevice
from jumperfree.routing.steiner import crossbar_init_state, crossbar_query
from jumperfree.service.control import ControlService


class FaultWire:
    """Wrap public transport calls; loss never changes MockDevice internals."""

    def __init__(self):
        self.device = MockDevice()
        self.now = 0.0
        self.mode = None
        self.drop = False

    def write(self, data):
        if self.mode == "transport":
            raise OSError("injected wire failure")
        if self.mode == "timeout" or self.mode == "unresolved" and b"TXN COMMIT" in data:
            self.drop = True
        return self.device.write(data)

    def readline(self):
        self.now += 0.01
        reply = self.device.readline()
        return b"" if self.drop else reply

    def close(self):
        self.device.close()


class ControlServiceTests(unittest.TestCase):
    def setUp(self):
        self.profile = BoardProfile.load()
        self.device = MockDevice()
        self.client, self.service = self.make_service(self.device, self.profile)

    def make_service(self, device, profile, **kwargs):
        client = DeviceClient(device, topology_id=profile.topology_id,
                              board_id=profile.board_id, max_ops=profile.limits.max_ops, **kwargs)
        self.addCleanup(client.close)
        client.connect()
        return client, ControlService(client, profile)

    def create_pair(self):
        self.assertTrue(self.service.create("NET_A", ["Clip1", "Clip8"]).ok)
        self.assertTrue(self.service.create("NET_B", ["Clip2", "Clip9"]).ok)

    def assert_confirmed(self, epoch):
        self.assertEqual(self.service._epoch, epoch)
        self.assertEqual(self.device.epoch, epoch)
        self.assertEqual(self.device.shadow, shadow_from_state(self.service._confirmed, self.profile))
        self.assertEqual(self.client.status.shadow, self.device.shadow)
        self.assertEqual(self.client.status.epoch, epoch)
        self.assertEqual(self.service.query().netlist, crossbar_query(self.service._confirmed))

    def assert_failure(self, result, code, operation):
        self.assertFalse(result.ok)
        self.assertEqual(result.operation, operation)
        self.assertEqual(result.error.code, code)
        self.assertIsNone(result.netlist)
        self.assertEqual(result.device, "MockDevice")
        self.assertFalse(result.verified)

    def test_empty_query_and_legal_create(self):
        initial = self.service.query()
        self.assertTrue(initial.ok)
        self.assertEqual(initial.operation, "query")
        self.assertEqual(initial.netlist["nets"], [])
        self.assertIsNone(initial.apply)
        nodes = ["Clip1", "Clip8"]
        result = self.service.create("NET_A", nodes)
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.apply.evidence, "mock-device-applied")
        self.assertEqual(result.netlist["nets"][0]["nodes"], nodes)
        self.assertEqual(self.service._confirmed["meta"]["tags"], {"NET_A": 1})
        self.assert_confirmed(1)

    def test_multiple_nets_preserve_connections_epoch_and_tags(self):
        self.assertTrue(self.service.create("Z_KEEP", ["Clip1", "Clip8"]).ok)
        before = copy.deepcopy(self.service._confirmed["nets"]["Z_KEEP"])
        tag = self.service._confirmed["meta"]["tags"]["Z_KEEP"]
        self.assertTrue(self.service.create("A_NEW", ["Clip2", "Clip9"]).ok)
        self.assertEqual(self.service._confirmed["nets"]["Z_KEEP"], before)
        self.assertEqual(self.service._confirmed["meta"]["tags"], {"A_NEW": 2, "Z_KEEP": tag})
        self.assert_confirmed(2)

    def test_modify_uses_one_apply_and_preserves_other_net_and_tags(self):
        self.create_pair()
        keep = copy.deepcopy(self.service._confirmed["nets"]["NET_B"])
        tags = copy.deepcopy(self.service._confirmed["meta"]["tags"])
        with mock.patch.object(self.client, "apply", wraps=self.client.apply) as apply:
            result = self.service.modify("NET_A", ["Clip1", "Clip10"])
        self.assertTrue(result.ok, result.as_dict())
        apply.assert_called_once()
        plan = apply.call_args.args[0]
        self.assertEqual(plan.base_epoch, 2)
        self.assertTrue(plan.breaks)
        self.assertTrue(plan.makes)
        self.assertEqual(self.device.execution_log, plan.to_wire_tokens())
        self.assertEqual(self.service._confirmed["nets"]["NET_B"], keep)
        self.assertEqual(self.service._confirmed["meta"]["tags"], tags)
        self.assert_confirmed(3)

    def test_failed_modify_retains_original_and_does_not_apply(self):
        self.create_pair()
        original = copy.deepcopy(self.service._confirmed)
        writes = list(self.device.writes)
        for nodes, code in ((["Clip1", "missing"], "NODE_NOT_IN_TOPOLOGY"),
                            (["Clip1", "Clip9"], "NODES_ALREADY_IN_OTHER_NET"),
                            (["Clip1"], "NET_TOO_SMALL")):
            with self.subTest(nodes=nodes), mock.patch.object(self.client, "apply") as apply:
                result = self.service.modify("NET_A", nodes)
                self.assert_failure(result, code, "modify")
                apply.assert_not_called()
            self.assertEqual(self.service._confirmed, original)
            self.assertEqual(self.device.writes, writes)
            self.assert_confirmed(2)

    def test_delete_releases_resources_retains_other_net_and_allows_reuse(self):
        self.create_pair()
        keep = copy.deepcopy(self.service._confirmed["nets"]["NET_B"])
        tag = self.service._confirmed["meta"]["tags"]["NET_B"]
        before_used = self.service.query().netlist["occupancy"]["used"]
        result = self.service.delete("NET_A")
        self.assertTrue(result.ok)
        self.assertEqual(result.operation, "delete")
        self.assertEqual(self.service._confirmed["nets"], {"NET_B": keep})
        self.assertEqual(self.service._confirmed["meta"]["tags"], {"NET_B": tag})
        self.assertLess(result.netlist["occupancy"]["used"], before_used)
        self.assert_confirmed(3)
        self.assertTrue(self.service.create("REUSE", ["Clip1", "Clip8"]).ok)
        self.assert_confirmed(4)

    def test_validation_errors_preserve_existing_codes_details_and_never_apply(self):
        self.create_pair()
        original = copy.deepcopy(self.service._confirmed)
        cases = (
            ("create", ("bad", ["Clip0", "missing"]), "NODE_NOT_IN_TOPOLOGY"),
            ("create", ("owned", ["Clip1", "Clip10"]), "NODES_ALREADY_IN_OTHER_NET"),
            ("create", ("NET_A", ["Clip0", "Clip10"]), "NET_ALREADY_EXISTS"),
            ("create", ("", ["Clip0", "Clip10"]), "INVALID_NET_ID"),
            ("create", ("small", ["Clip0", "Clip0"]), "NET_TOO_SMALL"),
            ("delete", ("missing",), "NET_NOT_FOUND"),
            ("modify", ("missing", ["Clip0", "Clip10"]), "NET_NOT_FOUND"),
        )
        with mock.patch.object(self.client, "apply") as apply:
            for operation, args, code in cases:
                with self.subTest(operation=operation, code=code):
                    result = getattr(self.service, operation)(*args)
                    self.assert_failure(result, code, operation)
                    self.assertIsNone(result.router)
                    self.assertIsNone(result.apply)
                    self.assertEqual(result.as_dict()["error"], result.error.as_dict())
                    self.assertEqual(self.service._confirmed, original)
            apply.assert_not_called()
        self.assert_confirmed(2)

    def test_real_router_no_path_preserves_original_modify_candidate(self):
        topology = self.profile.topology
        topology["inter_chip_links"] = []
        profile = replace(self.profile, topology_json=canonical_json(topology))
        device = MockDevice()
        client, service = self.make_service(device, profile)
        self.assertTrue(service.create("original", ["Clip0", "Clip1"]).ok)
        before = copy.deepcopy(service._confirmed)
        writes = list(device.writes)
        with mock.patch.object(client, "apply") as apply:
            result = service.modify("original", ["Clip0", "Clip8"])
        self.assert_failure(result, "NO_FREE_PATH", "modify")
        self.assertEqual(result.error.details["resource_reason"], "RESOURCE_EXHAUSTED")
        apply.assert_not_called()
        self.assertEqual(service._confirmed, before)
        self.assertEqual(service._epoch, 1)
        self.assertEqual(device.writes, writes)

    def test_compile_failure_does_not_apply_or_change_confirmed(self):
        profile = replace(self.profile, limits=replace(self.profile.limits, max_ops=1))
        device = MockDevice()
        client, service = self.make_service(device, profile)
        original = copy.deepcopy(service._confirmed)
        with mock.patch.object(client, "apply") as apply:
            result = service.create("too-large", ["Clip1", "Clip8"])
        self.assert_failure(result, "PLAN_TOO_LARGE", "create")
        self.assertIsNotNone(result.router)
        self.assertIsNone(result.apply)
        apply.assert_not_called()
        self.assertEqual(service._confirmed, original)
        self.assertEqual((service._epoch, device.epoch), (0, 0))

    def test_every_non_ok_apply_keeps_confirmed_and_epoch_without_retry(self):
        self.create_pair()
        original = copy.deepcopy(self.service._confirmed)
        for kind in ("rejected", "device_error", "unresolved", "timeout", "transport"):
            with self.subTest(kind=kind):
                self.service.acknowledge_unresolved()
                failure = ApplyResult(kind, error=HostError("EXAMPLE_FAILURE", "unchanged error", detail=kind))
                with mock.patch.object(self.client, "apply", return_value=failure) as apply:
                    result = self.service.modify("NET_A", ["Clip1", "Clip10"])
                apply.assert_called_once()
                self.assert_failure(result, "EXAMPLE_FAILURE", "modify")
                self.assertIs(result.apply, failure)
                self.assertEqual(result.as_dict()["apply"], failure.as_dict())
                self.assertEqual(self.service._confirmed, original)
                self.assertEqual(self.service._epoch, 2)
                self.assertEqual(self.service._blocked, kind in {"unresolved", "timeout", "transport"})

    def test_uncertain_kinds_block_all_writes_before_router_and_ack_cannot_clear_client_stop(self):
        for kind in ("unresolved", "timeout", "transport"):
            with self.subTest(kind=kind):
                wire = FaultWire()
                client, service = self.make_service(wire, self.profile,
                                                    clock=lambda: wire.now, frame_timeout=0.05, retries=0)
                self.assertTrue(service.create("NET_A", ["Clip1", "Clip8"]).ok)
                original = copy.deepcopy(service._confirmed)
                wire.mode = kind
                result = service.modify("NET_A", ["Clip1", "Clip10"])
                self.assertFalse(result.ok)
                self.assertEqual(result.apply.kind, kind)
                self.assertIsNone(result.netlist)
                self.assertTrue(service._blocked)
                writes = list(wire.device.writes)
                with (
                    mock.patch("jumperfree.service.control.crossbar_create_net") as create,
                    mock.patch("jumperfree.service.control.crossbar_delete_net") as delete,
                    mock.patch("jumperfree.service.control.compile_plan") as compile_,
                    mock.patch.object(client, "apply") as apply,
                ):
                    for operation, args in (("create", ("B", ["Clip2", "Clip9"])),
                                            ("modify", ("NET_A", ["Clip1", "Clip10"])),
                                            ("delete", ("NET_A",))):
                        blocked = getattr(service, operation)(*args)
                        self.assert_failure(blocked, "CONTROL_BLOCKED", operation)
                        self.assertIsNone(blocked.router)
                        self.assertIsNone(blocked.apply)
                    for call in (create, delete, compile_, apply):
                        call.assert_not_called()
                history = service.query()
                self.assertIsNone(history.netlist)
                self.assertEqual(history.router, crossbar_query(original))
                self.assertIsNone(service.acknowledge_unresolved())
                self.assertFalse(service._blocked)
                self.assertEqual(service.query().as_dict(), history.as_dict())
                # Repair the wire only. The actual DeviceClient must still refuse.
                wire.mode, wire.drop = None, False
                retried = service.modify("NET_A", ["Clip1", "Clip10"])
                self.assertEqual(retried.apply.as_dict(), result.apply.as_dict())
                self.assertEqual(wire.device.writes, writes)
                self.assertEqual(service._confirmed, original)
                self.assertEqual(service._epoch, 1)
                if kind == "unresolved":
                    self.assertEqual(wire.device.epoch, 2)  # Lost confirmation after a real commit.
                    self.assertNotEqual(wire.device.shadow, shadow_from_state(original, self.profile))

    def test_interrupted_commit_keeps_history_without_claiming_current_netlist(self):
        self.create_pair()
        original = copy.deepcopy(self.service._confirmed)
        self.device.interrupt_next_commit()
        result = self.service.delete("NET_A")
        self.assert_failure(result, "DEVICE_ERROR", "delete")
        self.assertEqual(result.apply.state_changed, "yes")
        self.assertEqual(self.service._confirmed, original)
        self.assertEqual(self.service._epoch, 2)
        history = self.service.query()
        self.assert_failure(history, "STATE_UNCONFIRMED", "query")
        self.assertEqual(history.router, crossbar_query(original))
        self.service.acknowledge_unresolved()
        self.assertIsNone(self.service.query().netlist)

    def test_query_and_returned_views_are_detached_and_do_no_io(self):
        nodes = ["Clip1", "Clip8"]
        created = self.service.create("NET_A", nodes)
        original = copy.deepcopy(self.service._confirmed)
        nodes.append("Clip3")
        created.netlist["nets"][0]["nodes"].clear()
        created.router["nets"].clear()
        writes = list(self.device.writes)
        queried = self.service.query()
        payload = queried.as_dict()
        payload["netlist"]["nets"].clear()
        self.assertEqual(queried.netlist, crossbar_query(original))
        queried.netlist["nets"][0]["lines"].clear()
        queried.router["nets"].clear()
        self.assertEqual(self.service._confirmed, original)
        self.assertEqual(self.service.query().netlist, crossbar_query(original))
        self.assertEqual(self.device.writes, writes)

    def test_constructor_requires_connected_empty_device(self):
        client = DeviceClient(MockDevice(), topology_id=self.profile.topology_id)
        self.addCleanup(client.close)
        with self.assertRaises(HostError) as raised:
            ControlService(client, self.profile)
        self.assertEqual(raised.exception.code, "NOT_INITIALIZED")
        self.assertTrue(self.service.create("NET_A", ["Clip1", "Clip8"]).ok)
        with self.assertRaises(HostError) as raised:
            ControlService(self.client, self.profile)
        self.assertEqual(raised.exception.code, "PLAN_STATE_MISMATCH")

    def test_constructor_uses_connected_epoch_instead_of_assuming_zero(self):
        empty = crossbar_init_state(self.profile.topology)
        self.assertTrue(self.client.apply(compile_plan(empty, empty, self.profile)).ok)
        service = ControlService(self.client, self.profile)
        self.assertEqual(service._epoch, 1)
        result = service.create("NET_A", ["Clip1", "Clip8"])
        self.assertTrue(result.ok, result.as_dict())
        self.assertEqual(result.apply.status.epoch, 2)


class ControlDemoTests(unittest.TestCase):
    def invoke(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["host", "control-demo", "--mock"])
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("HOST_JSON "))
        payload = json.loads(lines[1].removeprefix("HOST_JSON "))
        self.assertEqual(lines[1], "HOST_JSON " + canonical_json(payload))
        return code, payload, lines[0]

    def test_scenarios_a_through_e_and_canonical_mock_json(self):
        devices = [MockDevice(), MockDevice()]
        with mock.patch("jumperfree.service.control_demo.MockDevice", side_effect=devices):
            code, payload, summary = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("MockDevice", summary)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["transport"], "mock")
        results = payload["results"]
        self.assertEqual([r["scenario"] for r in results], [
            "A", "B", "C", "D", "E-invalid-node", "E-net-conflict", "E-route-failure", "E-device-failure",
        ])
        self.assertEqual([r["operation"] for r in results[:4]], ["create", "create", "modify", "delete"])
        for epoch, result in enumerate(results[:4], 1):
            self.assertTrue(result["ok"])
            self.assertEqual(result["apply"]["status"]["epoch"], epoch)
            self.assertEqual(result["apply"]["evidence"], "mock-device-applied")
        self.assertEqual({n["net"]: n["nodes"] for n in results[2]["netlist"]["nets"]},
                         {"NET_A": ["Clip1", "Clip10"], "NET_B": ["Clip2", "Clip9"]})
        self.assertEqual([n["net"] for n in results[3]["netlist"]["nets"]], ["NET_B"])
        self.assertEqual(results[1]["netlist"]["nets"][1], results[3]["netlist"]["nets"][0])
        for result in results:
            self.assertTrue(result["passed"])
            self.assertFalse(result["verified"])
            self.assertEqual(result["device"], "MockDevice")
        for result in results[4:]:
            self.assertFalse(result["ok"])
            self.assertIsNone(result["netlist"])
            self.assertEqual(result["error"]["code"], result["expected_error"])
        self.assertTrue(all(device.closed for device in devices))

    def test_unexpected_apply_failure_is_nonzero_and_closes_device(self):
        device = MockDevice()
        device.interrupt_next_commit()
        with mock.patch("jumperfree.service.control_demo.MockDevice", return_value=device):
            code, payload, summary = self.invoke()
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["results"][0]["passed"])
        self.assertEqual(payload["error"]["code"], "CONTROL_DEMO_FAILED")
        self.assertIn("FAIL", summary)
        self.assertTrue(device.closed)

    def test_connect_failure_closes_and_returns_structured_error(self):
        device = MockDevice()
        device.fault("hw")
        with mock.patch("jumperfree.service.control_demo.MockDevice", return_value=device):
            code, payload, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "DEVICE_FAULT")
        self.assertTrue(device.closed)

    def test_close_failure_is_nonzero(self):
        with mock.patch("jumperfree.service.control_demo.DeviceClient.close",
                        side_effect=HostError("TRANSPORT_ERROR", "close failed")):
            code, payload, _ = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"]["code"], "TRANSPORT_ERROR")

    def test_mock_flag_is_required_and_serial_is_not_supported(self):
        for argv in (["host", "control-demo"], ["host", "control-demo", "--port", "/dev/test"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(argv)
                self.assertEqual(raised.exception.code, 2)
