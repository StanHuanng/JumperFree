"""Exercise the public tools over real ControlService, router and MockDevice."""

from contextlib import ExitStack
import copy
from dataclasses import replace
from functools import partial
from itertools import product
import json
import unittest
from unittest import mock

from jumperfree.agent import (
    MockAgentSession, TOOL_DESCRIPTIONS, TOOL_SCHEMAS,
    create_net, delete_net, get_state, modify_net,
)
from jumperfree.board import BoardProfile
from jumperfree.core import canonical_json
from jumperfree.host import ApplyResult, DeviceClient, HostError
from jumperfree.host.mock_device import MockDevice
from jumperfree.service.control import ControlService
from tests.service.test_control import FaultWire


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.session = MockAgentSession()
        self.addCleanup(self.session.close)

    def make_session(self, profile=None, device=None, *, fault_wire=False):
        with ExitStack() as stack:
            if profile is not None:
                stack.enter_context(mock.patch(
                    "jumperfree.agent.session.BoardProfile.load", return_value=profile))
            if device is not None:
                stack.enter_context(mock.patch(
                    "jumperfree.agent.session.MockDevice", return_value=device))
            if fault_wire:
                def client_factory(*args, **kwargs):
                    return DeviceClient(*args, **kwargs, clock=lambda: device.now,
                                        frame_timeout=0.05)
                stack.enter_context(mock.patch(
                    "jumperfree.agent.session.DeviceClient", side_effect=client_factory))
            session = MockAgentSession()
        self.addCleanup(session.close)
        return session

    def assert_result(self, result, operation, code=None):
        self.assertEqual(set(result), {
            "ok", "operation", "netlist", "router", "error", "apply", "device", "verified",
        })
        self.assertEqual(result["operation"], operation)
        self.assertEqual(result["device"], "MockDevice")
        self.assertIs(result["verified"], False)
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertIs(result["ok"], code is None)
        if code is None:
            self.assertIsNone(result["error"])
            self.assertIsNotNone(result["netlist"])
        else:
            self.assertIsNone(result["netlist"])
            self.assertEqual(result["error"]["code"], code)

    @staticmethod
    def nets(result):
        return {net["net"]: net["nodes"] for net in result["netlist"]["nets"]}

    def test_four_tools_share_state_and_preserve_other_nets(self):
        calls = [
            (create_net, ("NET_A", ["Clip1", "Clip8"]), "create",
             {"NET_A": ["Clip1", "Clip8"]}),
            (create_net, ("NET_B", ["Clip2", "Clip9"]), "create",
             {"NET_A": ["Clip1", "Clip8"], "NET_B": ["Clip2", "Clip9"]}),
            (modify_net, ("NET_A", ["Clip1", "Clip10"]), "modify",
             {"NET_A": ["Clip1", "Clip10"], "NET_B": ["Clip2", "Clip9"]}),
            (get_state, (), "query",
             {"NET_A": ["Clip1", "Clip10"], "NET_B": ["Clip2", "Clip9"]}),
            (delete_net, ("NET_B",), "delete", {"NET_A": ["Clip1", "Clip10"]}),
        ]
        with mock.patch.object(self.session._client, "apply",
                               wraps=self.session._client.apply) as apply:
            for tool, args, operation, expected in calls:
                result = tool(self.session, *args)
                self.assert_result(result, operation)
                self.assertEqual(self.nets(result), expected)
            self.assertEqual(apply.call_count, 4)
        self.assertEqual(self.session._device.epoch, 4)

    def test_empty_query_has_no_device_io(self):
        writes = list(self.session._device.writes)
        result = get_state(self.session)
        self.assert_result(result, "query")
        self.assertEqual(self.nets(result), {})
        self.assertIsNone(result["apply"])
        self.assertEqual(self.session._device.writes, writes)

    def test_delegates_without_changing_names_nodes_or_results(self):
        nodes = ["Clip8", "Clip1"]
        with mock.patch.object(self.session._control, "create",
                               wraps=self.session._control.create) as create:
            result = create_net(self.session, " A ", nodes)
        create.assert_called_once_with(" A ", nodes)
        self.assertIs(create.call_args.args[1], nodes)
        self.assertEqual(nodes, ["Clip8", "Clip1"])
        self.assertIn(" A ", self.nets(result))
        for tool, method, args in (
            (modify_net, "modify", (" A ", ["Clip1", "Clip10"])),
            (delete_net, "delete", (" A ",)),
            (get_state, "query", ()),
        ):
            control_result = getattr(self.session._control, method)(*args)
            with mock.patch.object(self.session._control, method,
                                   return_value=control_result) as call:
                self.assertEqual(tool(self.session, *args), control_result.as_dict())
                call.assert_called_once_with(*args)

    def test_invalid_names_on_all_writes_never_apply(self):
        with mock.patch.object(self.session._client, "apply") as apply:
            for name in ("", " \t\n", None, 1, [], {}):
                for tool, operation, tail in (
                    (create_net, "create", (["Clip1", "Clip8"],)),
                    (modify_net, "modify", (["Clip1", "Clip8"],)),
                    (delete_net, "delete", ()),
                ):
                    with self.subTest(name=name, operation=operation):
                        result = tool(self.session, name, *tail)
                        self.assert_result(result, operation, "INVALID_NET_ID")
                        self.assertIsNone(result["apply"])
            apply.assert_not_called()

    def test_nodes_must_be_a_string_array(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        with mock.patch.object(self.session._client, "apply") as apply:
            for nodes in (None, "Clip1 Clip8", ("Clip1", "Clip8"), {}, ["Clip1", 8]):
                for tool, operation in ((create_net, "create"), (modify_net, "modify")):
                    with self.subTest(nodes=nodes, operation=operation):
                        self.assert_result(tool(self.session, "A", nodes), operation, "BAD_ARG")
            apply.assert_not_called()

    def test_unknown_ambiguous_and_too_small_nodes_are_errors_without_substitution(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        original = get_state(self.session)
        writes = list(self.session._device.writes)
        with mock.patch.object(self.session._client, "apply") as apply:
            for nodes, code in (
                (["Clip1", "missing"], "NODE_NOT_IN_TOPOLOGY"),
                (["clip1", "Clip8"], "NODE_NOT_IN_TOPOLOGY"),
                (["Clip1 ", "Clip8"], "NODE_NOT_IN_TOPOLOGY"),
                (["Clip1", "Clip8/Clip9"], "NODE_NOT_IN_TOPOLOGY"),
                (["Clip1", "C1.Y0"], "NODE_NOT_IN_TOPOLOGY"),
                ([], "NET_TOO_SMALL"), (["Clip1"], "NET_TOO_SMALL"),
                (["Clip1", "Clip1"], "NET_TOO_SMALL"),
            ):
                for tool, name, operation in (
                    (create_net, "B", "create"), (modify_net, "A", "modify"),
                ):
                    with self.subTest(nodes=nodes, operation=operation):
                        before = list(nodes)
                        self.assert_result(tool(self.session, name, nodes), operation, code)
                        self.assertEqual(nodes, before)
            apply.assert_not_called()
        self.assertEqual(get_state(self.session), original)
        self.assertEqual(self.session._device.writes, writes)

    def test_net_conflicts_and_missing_nets_preserve_codes_details_and_state(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        original = get_state(self.session)
        with mock.patch.object(self.session._client, "apply") as apply:
            for tool, args, operation, code in (
                (create_net, ("A", ["Clip2", "Clip9"]), "create", "NET_ALREADY_EXISTS"),
                (create_net, ("B", ["Clip1", "Clip9"]), "create", "NODES_ALREADY_IN_OTHER_NET"),
                (modify_net, ("missing", ["Clip2", "Clip9"]), "modify", "NET_NOT_FOUND"),
                (delete_net, ("missing",), "delete", "NET_NOT_FOUND"),
            ):
                with self.subTest(code=code):
                    expected = getattr(self.session._control, operation)(*args).as_dict()
                    result = tool(self.session, *args)
                    self.assert_result(result, operation, code)
                    self.assertEqual(result, expected)
            apply.assert_not_called()
        self.assertEqual(get_state(self.session), original)

    def test_modify_conflict_preserves_original_and_other_net(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        create_net(self.session, "B", ["Clip2", "Clip9"])
        original = get_state(self.session)
        with mock.patch.object(self.session._client, "apply") as apply:
            result = modify_net(self.session, "A", ["Clip1", "Clip9"])
            self.assert_result(result, "modify", "NODES_ALREADY_IN_OTHER_NET")
            apply.assert_not_called()
        self.assertEqual(get_state(self.session), original)

    def test_real_router_failure_never_applies_or_loses_original(self):
        profile = BoardProfile.load()
        topology = profile.topology
        topology["inter_chip_links"] = []
        session = self.make_session(replace(profile, topology_json=canonical_json(topology)))
        create_net(session, "A", ["Clip0", "Clip1"])
        original = get_state(session)
        writes = list(session._device.writes)
        with mock.patch.object(session._client, "apply") as apply:
            result = modify_net(session, "A", ["Clip0", "Clip8"])
            self.assert_result(result, "modify", "NO_FREE_PATH")
            self.assertEqual(result["error"]["details"]["resource_reason"], "RESOURCE_EXHAUSTED")
            self.assertIsNone(result["apply"])
            apply.assert_not_called()
        self.assertEqual(get_state(session), original)
        self.assertEqual(session._device.writes, writes)

    def test_compile_failure_returns_candidate_only_and_never_applies(self):
        profile = BoardProfile.load()
        session = self.make_session(replace(profile, limits=replace(profile.limits, max_ops=1)))
        with mock.patch.object(session._client, "apply") as apply:
            result = create_net(session, "A", ["Clip1", "Clip8"])
            self.assert_result(result, "create", "PLAN_TOO_LARGE")
            self.assertIsNotNone(result["router"])
            self.assertIsNone(result["apply"])
            apply.assert_not_called()
        self.assertEqual(self.nets(get_state(session)), {})

    def test_interrupted_device_commit_returns_error_and_unconfirmed_history(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        original = get_state(self.session)["netlist"]
        self.session._device.interrupt_next_commit()
        with mock.patch.object(self.session._client, "apply",
                               wraps=self.session._client.apply) as apply:
            result = delete_net(self.session, "A")
            apply.assert_called_once()
        self.assert_result(result, "delete", "DEVICE_ERROR")
        self.assertEqual(result["error"], result["apply"]["error"])
        self.assertEqual(result["apply"]["state_changed"], "yes")
        writes = list(self.session._device.writes)
        history = get_state(self.session)
        self.assert_result(history, "query", "STATE_UNCONFIRMED")
        self.assertEqual(history["router"], original)
        self.assertEqual(self.session._device.writes, writes)

    def test_every_failed_apply_is_serialized_unchanged_and_never_retried(self):
        for kind, code in (("rejected", "EPOCH_MISMATCH"), ("device_error", "DEVICE_ERROR"),
                           ("unresolved", "COMMIT_UNRESOLVED"), ("timeout", "TIMEOUT"),
                           ("transport", "TRANSPORT_ERROR")):
            with self.subTest(kind=kind):
                session = self.make_session()
                failure = ApplyResult(kind, error=HostError(code, "original message", reason=kind))
                with mock.patch.object(session._client, "apply", return_value=failure) as apply:
                    result = create_net(session, "A", ["Clip1", "Clip8"])
                    apply.assert_called_once()
                self.assert_result(result, "create", code)
                self.assertEqual(result["apply"], failure.as_dict())
                self.assertEqual(result["error"], failure.error.as_dict())
                self.assert_result(get_state(session), "query", "STATE_UNCONFIRMED")

    def test_real_uncertain_wire_blocks_all_writes_without_retry_or_acknowledgment(self):
        for kind, code in (("unresolved", "TIMEOUT"), ("timeout", "TIMEOUT"),
                           ("transport", "TRANSPORT_ERROR")):
            with self.subTest(kind=kind):
                wire = FaultWire()
                session = self.make_session(device=wire, fault_wire=True)
                create_net(session, "A", ["Clip1", "Clip8"])
                original = get_state(session)["netlist"]
                start = len(wire.device.writes)
                wire.mode = kind
                with (
                    mock.patch.object(session._client, "apply", wraps=session._client.apply) as apply,
                    mock.patch.object(ControlService, "acknowledge_unresolved") as acknowledge,
                ):
                    result = modify_net(session, "A", ["Clip1", "Clip10"])
                    self.assert_result(result, "modify", code)
                    self.assertEqual(result["apply"]["kind"], kind)
                    apply.assert_called_once()
                    writes = list(wire.device.writes)
                    # Repairing the wire cannot implicitly recover the write gate or client stop.
                    wire.mode, wire.drop = None, False
                    with (
                        mock.patch("jumperfree.service.control.crossbar_create_net") as create,
                        mock.patch("jumperfree.service.control.crossbar_delete_net") as delete,
                        mock.patch("jumperfree.service.control.compile_plan") as compile_,
                    ):
                        for tool, args, operation in (
                            (create_net, ("B", ["Clip2", "Clip9"]), "create"),
                            (modify_net, ("A", ["Clip1", "Clip10"]), "modify"),
                            (delete_net, ("A",), "delete"),
                        ):
                            blocked = tool(session, *args)
                            self.assert_result(blocked, operation, "CONTROL_BLOCKED")
                            self.assertIsNone(blocked["apply"])
                            self.assertIsNone(blocked["router"])
                        for call in (create, delete, compile_):
                            call.assert_not_called()
                    apply.assert_called_once()
                    acknowledge.assert_not_called()
                history = get_state(session)
                self.assert_result(history, "query", "STATE_UNCONFIRMED")
                self.assertEqual(history["router"], original)
                self.assertEqual(wire.device.writes, writes)
                failed_writes = writes[start:]
                transaction_frames = [line for line in failed_writes if b"TXN " in line]
                self.assertEqual(len(transaction_frames), len(set(transaction_frames)))
                self.assertLessEqual(sum(b"TXN COMMIT" in line for line in failed_writes), 1)
                self.assertLessEqual(sum(b"TXN QUERY" in line for line in failed_writes), 1)
                if kind == "timeout":
                    self.assertEqual(failed_writes, [b"JF2 SW STATUS\n"])
                self.assertFalse(any(b"ALLOPEN" in line or b"FAULT CLEAR" in line for line in writes))
                if kind == "unresolved":
                    self.assertEqual(wire.device.epoch, 2)

    def test_inflight_device_errors_preserve_unknown_state_and_block_writes(self):
        for command, cause in product((b"BEGIN", b"ADD", b"COMMIT"), ("lease", "hw")):
            with self.subTest(command=command, cause=cause):
                wire = FaultWire()
                wire.device.write(b"JF2 HELLO 2 a\nJF2 LEASE 1000\n")
                wire.device.reset_input()
                session = self.make_session(device=wire, fault_wire=True)
                self.assert_result(create_net(session, "A", ["Clip1", "Clip8"]), "create")
                original = copy.deepcopy(session._control._confirmed)
                history = get_state(session)["netlist"]
                epoch = session._control._epoch

                def fail(data):
                    if data.startswith(b"JF2 TXN " + command + b" "):
                        if cause == "lease":
                            wire.device.advance_ms(1000)
                        else:
                            wire.device.fault("hw")

                wire.before_write = fail
                control_modify = session._control.modify
                results = []

                def modify(*args):
                    result = control_modify(*args)
                    results.append(result)
                    return result

                with mock.patch.object(session._control, "modify", side_effect=modify):
                    result = modify_net(session, "A", ["Clip1", "Clip10"])
                self.assertEqual(result, results[0].as_dict())
                self.assert_result(result, "modify", "DEVICE_ERROR")
                self.assertEqual(result["apply"]["kind"], "device_error")
                self.assertEqual(result["apply"]["state_changed"], "unknown")
                self.assertEqual(result["error"], result["apply"]["error"])
                writes = list(wire.device.writes)
                with (
                    mock.patch.object(session._client, "apply") as apply,
                    mock.patch.object(session._client, "connect") as connect,
                    mock.patch.object(ControlService, "acknowledge_unresolved") as acknowledge,
                ):
                    for tool, args, operation in (
                        (create_net, ("B", ["Clip2", "Clip9"]), "create"),
                        (modify_net, ("A", ["Clip1", "Clip10"]), "modify"),
                        (delete_net, ("A",), "delete"),
                    ):
                        blocked = tool(session, *args)
                        self.assert_result(blocked, operation, "CONTROL_BLOCKED")
                        self.assertEqual(blocked, getattr(session._control, operation)(*args).as_dict())
                    queried = get_state(session)
                    self.assert_result(queried, "query", "STATE_UNCONFIRMED")
                    self.assertEqual(queried, session._control.query().as_dict())
                    self.assertEqual(queried["router"], history)
                    stopped = session._client._stopped
                    self.assertIsNotNone(stopped)
                    shadow, faults = wire.device.shadow, wire.device.faults
                    session.close()
                    session.close()
                    self.assertEqual(wire.device.shadow, shadow)
                    self.assertEqual(wire.device.faults, faults)
                    self.assertIs(session._client._stopped, stopped)
                    for call in (apply, connect, acknowledge):
                        call.assert_not_called()
                self.assertEqual(session._control._confirmed, original)
                self.assertEqual(session._control._epoch, epoch)
                self.assertEqual(wire.device.writes, writes)

    def test_results_and_input_are_detached_from_session_state(self):
        nodes = ["Clip1", "Clip8"]
        result = create_net(self.session, "A", nodes)
        expected = copy.deepcopy(result["netlist"])
        nodes.clear()
        result["netlist"]["nets"].clear()
        result["router"]["nets"].clear()
        queried = get_state(self.session)
        self.assertEqual(queried["netlist"], expected)
        queried["netlist"]["nets"].clear()
        self.assertEqual(get_state(self.session)["netlist"], expected)

    def test_session_components_created_and_connected_once_then_closed_once(self):
        with (
            mock.patch("jumperfree.agent.session.BoardProfile.load", wraps=BoardProfile.load) as load,
            mock.patch("jumperfree.agent.session.MockDevice", wraps=MockDevice) as device,
            mock.patch("jumperfree.agent.session.DeviceClient", wraps=DeviceClient) as client,
            mock.patch("jumperfree.agent.session.ControlService", wraps=ControlService) as control,
            mock.patch.object(DeviceClient, "connect", autospec=True,
                              side_effect=DeviceClient.connect) as connect,
        ):
            with MockAgentSession() as session:
                create_net(session, "A", ["Clip1", "Clip8"])
                get_state(session)
                for call in (load, device, client, control, connect):
                    call.assert_called_once()
                self.assertEqual(client.call_args.kwargs["retries"], 0)
            with mock.patch.object(session._client, "close") as close:
                session.close()
                close.assert_not_called()
            self.assertTrue(session._device.closed)

    def test_context_closes_client_on_exception_and_explicit_finally_works(self):
        with self.assertRaisesRegex(RuntimeError, "caller failed"):
            with MockAgentSession() as session:
                raise RuntimeError("caller failed")
        self.assertTrue(session._device.closed)
        session = MockAgentSession()
        try:
            self.assert_result(get_state(session), "query")
        finally:
            session.close()
        self.assertTrue(session._device.closed)

    def test_connect_and_service_construction_failures_close_client(self):
        device = MockDevice()
        device.fault("hw")
        with mock.patch("jumperfree.agent.session.MockDevice", return_value=device):
            with self.assertRaises(HostError) as raised:
                MockAgentSession()
        self.assertEqual(raised.exception.code, "DEVICE_FAULT")
        self.assertTrue(device.closed)
        device = MockDevice()
        with (
            mock.patch("jumperfree.agent.session.MockDevice", return_value=device),
            mock.patch("jumperfree.agent.session.ControlService", side_effect=RuntimeError("failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                MockAgentSession()
        self.assertTrue(device.closed)

    def test_close_failure_preserves_primary_structured_error(self):
        with mock.patch.object(self.session._client, "close",
                               side_effect=HostError("TRANSPORT_ERROR", "close failed")):
            with self.assertRaises(HostError) as raised:
                with self.session:
                    raise HostError("DEVICE_ERROR", "primary failure")
        self.assertEqual(raised.exception.code, "DEVICE_ERROR")
        self.assertEqual(raised.exception.details["close_error"]["code"], "TRANSPORT_ERROR")
        self.session._client.close()

    def test_close_failure_without_primary_error_propagates(self):
        with mock.patch.object(self.session._client, "close",
                               side_effect=HostError("TRANSPORT_ERROR", "close failed")):
            with self.assertRaises(HostError) as raised:
                with self.session:
                    pass
        self.assertEqual(raised.exception.code, "TRANSPORT_ERROR")
        self.session._client.close()

    def test_closed_session_cannot_claim_confirmed_state_or_write(self):
        self.assert_result(create_net(self.session, "A", ["Clip1", "Clip8"]), "create")
        shadow = self.session._device.shadow
        writes = list(self.session._device.writes)
        self.session.close()
        self.assertEqual(self.session._device.shadow, shadow)
        for tool, args, operation in (
            (create_net, ("A", ["Clip1", "Clip8"]), "create"),
            (modify_net, ("A", ["Clip1", "Clip8"]), "modify"),
            (delete_net, ("A",), "delete"), (get_state, (), "query"),
            (create_net, ("", ["Clip1", "Clip8"]), "create"),
            (modify_net, ("A", None), "modify"),
            (delete_net, (None,), "delete"),
        ):
            self.assert_result(tool(self.session, *args), operation, "SESSION_CLOSED")
        self.assertEqual(self.session._device.writes, writes)

    def test_separate_sessions_start_empty_and_have_no_recovery_tool(self):
        create_net(self.session, "A", ["Clip1", "Clip8"])
        other = self.make_session()
        self.assertEqual(self.nets(get_state(other)), {})
        self.assertFalse(hasattr(other, "acknowledge_unresolved"))
        self.assertEqual(set(TOOL_SCHEMAS), {"create_net", "modify_net", "delete_net", "get_state",
                                              "plan_net_change", "guide_leds", "all_open"})
        self.assertNotIn("acknowledge_unresolved", TOOL_SCHEMAS)

    def test_mcp_schemas_and_binding_use_only_user_facing_signatures(self):
        self.assertEqual(set(TOOL_DESCRIPTIONS), set(TOOL_SCHEMAS))
        for name, definition in TOOL_SCHEMAS.items():
            self.assertEqual(definition["name"], name)
            self.assertEqual(definition["description"], TOOL_DESCRIPTIONS[name])
            schema = definition["inputSchema"]
            if name in {"plan_net_change", "guide_leds"}:
                continue  # covered in test_plan.py / test_guide_all_open.py
            expected = [] if name in {"get_state", "all_open"} else ["name"]
            if name in {"create_net", "modify_net"}:
                expected.append("nodes")
                self.assertEqual(schema["properties"]["nodes"]["type"], "array")
                self.assertEqual(schema["properties"]["nodes"]["items"], {"type": "string"})
                self.assertEqual(schema["properties"]["nodes"]["minItems"], 2)
            self.assertEqual(schema["required"], expected)
            self.assertFalse(schema["additionalProperties"])
            if expected:
                self.assertEqual(schema["properties"]["name"]["pattern"], r"\S")
        bound_create = partial(create_net, self.session)
        self.assert_result(bound_create(name="A", nodes=["Clip1", "Clip8"]), "create")
