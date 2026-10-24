from __future__ import annotations

import unittest

from jumperfree.host import HostError, Response
from jumperfree.host.protocol import encode_request, parse_request, parse_response


class ProtocolTests(unittest.TestCase):
    def test_structured_error(self) -> None:
        error = HostError("TEST", "failure")
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "failure")
        self.assertEqual(error.as_dict(), {"code": "TEST", "message": "failure"})
        self.assertEqual(HostError("TEST", "failure", clip=16).as_dict()["details"], {"clip": 16})

    def test_canonical_request_and_response_frames(self) -> None:
        for command, values, wire in (
            ("SET", (0, 0, 128, 255), b"JF1 SET 0 0 128 255\n"),
            ("SET", (15, 255, 0, 0), b"JF1 SET 15 255 0 0\n"),
            ("CLR", (15,), b"JF1 CLR 15\n"),
            ("CLRALL", (), b"JF1 CLRALL\n"),
            ("STATUS", (), b"JF1 STATUS\n"),
        ):
            with self.subTest(command=command, values=values):
                self.assertEqual(encode_request(command, *values), wire)
                self.assertEqual(parse_request(wire), (command, values))
                self.assertEqual(parse_request(wire[:-1] + b"\r\n"), (command, values))
                if command != "STATUS":
                    response = parse_response(wire.replace(b"JF1 ", b"JF1 OK ", 1))
                    self.assertEqual(response, Response(command, values))
                    self.assertEqual(
                        response.as_dict(), {"command": command, "values": list(values)},
                    )

    def test_status_has_16_rgb_triples(self) -> None:
        wire = b"JF1 OK STATUS" + b" 255 128 0" * 16 + b"\r\n"
        self.assertEqual(parse_response(wire), Response("STATUS", (255, 128, 0) * 16))

    def test_clip_validation_rejects_invalid_types_and_range(self) -> None:
        for clip in (-1, 16, 999, True, 1.0, "0", None):
            for command, values in (("SET", (clip, 0, 0, 0)), ("CLR", (clip,))):
                with self.subTest(clip=clip, command=command):
                    with self.assertRaises(HostError) as raised:
                        encode_request(command, *values)
                    self.assertEqual(raised.exception.code, "CLIP_OUT_OF_RANGE")

    def test_rgb_validation_rejects_invalid_types_and_range(self) -> None:
        for value in (-1, 256, False, 0.5, "255", None):
            for channel in range(3):
                rgb = [0, 0, 0]
                rgb[channel] = value
                with self.subTest(value=value, channel=channel):
                    with self.assertRaises(HostError) as raised:
                        encode_request("SET", 0, *rgb)
                    self.assertEqual(raised.exception.code, "INVALID_RGB")

    def test_invalid_commands_and_argument_counts(self) -> None:
        for command, values in (("SWITCH", ()), ("SET", (0,)), ("CLR", ()), ("STATUS", (0,))):
            with self.subTest(command=command):
                with self.assertRaises(HostError) as raised:
                    encode_request(command, *values)
                self.assertEqual(raised.exception.code, "INVALID_COMMAND")
        with self.assertRaises(HostError) as raised:
            parse_request(b"JF1\n")
        self.assertEqual(raised.exception.code, "INVALID_COMMAND")

    def test_malformed_responses(self) -> None:
        frames = (
            b"", b"JF1 OK CLRALL", b"JF1 OK CLRALL\nextra\n",
            b"JF1 OK CLRALL\xff\n", b"JF1 OK CLRALL\t\n", b"JF1  OK CLRALL\n",
            b" JF1 OK CLRALL\n", b"JF1 OK CLRALL \n", b"\n", b"JF2 OK CLRALL\n",
            b"JF1\n", b"JF1 OK\n", b"JF1 ACK CLRALL\n", b"JF1 OK UNKNOWN\n",
            b"JF1 OK SET 0 1 2\n", b"JF1 OK SET 0 256 0 0\n", b"JF1 OK CLR 16\n",
            b"JF1 OK CLR -1\n", b"JF1 OK CLR +1\n", b"JF1 OK CLR 01\n",
            b"JF1 OK CLR 1.0\n", b"JF1 OK CLR one\n", b"JF1 OK STATUS\n",
            b"JF1 OK STATUS" + b" 0" * 47 + b"\n",
            b"JF1 OK STATUS" + b" 0" * 49 + b"\n",
            b"JF1 OK STATUS 256" + b" 0" * 47 + b"\n",
            b"JF1 ERR\n", b"JF1 ERR BUSY\n", b"JF1 ERR bad-code message\n",
            b"JF1 ERR 123 message\n", b"JF1 ERR BUSY message\r\r\n",
            b"JF1 OK CLR " + b"1" * 512 + b"\n", "JF1 OK CLRALL\n", None,
        )
        for frame in frames:
            with self.subTest(frame=frame):
                with self.assertRaises(HostError) as raised:
                    parse_response(frame)
                self.assertEqual(raised.exception.code, "MALFORMED_RESPONSE")

    def test_device_errors_preserve_code_and_message(self) -> None:
        for message in ("busy", "LED driver is busy"):
            with self.subTest(message=message):
                with self.assertRaises(HostError) as raised:
                    parse_response(f"JF1 ERR BUSY {message}\n".encode("ascii"))
                self.assertEqual(raised.exception.as_dict(), {
                    "code": "DEVICE_ERROR", "message": message, "details": {"device_code": "BUSY"},
                })

    def test_extra_arity_responses_are_malformed(self) -> None:
        for frame in (
            b"JF1 OK SET 0 1 2 3 4\n", b"JF1 OK CLR 0 1\n", b"JF1 OK CLRALL 1\n",
            b"JF1 OK STATUS" + b" 0" * 49 + b"\n",
        ):
            with self.subTest(frame=frame):
                with self.assertRaises(HostError) as raised:
                    parse_response(frame)
                self.assertEqual(raised.exception.code, "MALFORMED_RESPONSE")
