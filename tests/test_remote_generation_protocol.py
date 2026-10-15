from __future__ import annotations

import io
import json
import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from remote_generation_protocol import (  # noqa: E402
    MAX_HEADER_BYTES,
    PROTOCOL_VERSION,
    RemoteGenerationProtocolError,
    read_frame,
    write_frame,
)


class RemoteGenerationProtocolTests(unittest.TestCase):
    def test_round_trip_frame(self) -> None:
        stream = io.BytesIO()
        write_frame(stream, {"version": PROTOCOL_VERSION, "op": "synthesize", "requestId": "r1"}, b"RIFFdata")
        stream.seek(0)

        header, payload = read_frame(stream, max_payload_bytes=1024)

        self.assertEqual(header["version"], 1)
        self.assertEqual(header["op"], "synthesize")
        self.assertEqual(header["requestId"], "r1")
        self.assertEqual(header["payloadBytes"], 8)
        self.assertEqual(payload, b"RIFFdata")

    def test_rejects_truncated_header_length(self) -> None:
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "header length"):
            read_frame(io.BytesIO(b"\x00\x00"), max_payload_bytes=1024)

    def test_rejects_truncated_header(self) -> None:
        stream = io.BytesIO(struct.pack(">I", 10) + b"{}")
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "header"):
            read_frame(stream, max_payload_bytes=1024)

    def test_rejects_truncated_payload(self) -> None:
        header = json.dumps({"version": 1, "op": "x", "payloadBytes": 5}).encode("utf-8")
        stream = io.BytesIO(struct.pack(">I", len(header)) + header + b"ab")
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "payload"):
            read_frame(stream, max_payload_bytes=1024)

    def test_rejects_invalid_json(self) -> None:
        raw = b"not-json"
        stream = io.BytesIO(struct.pack(">I", len(raw)) + raw)
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "JSON"):
            read_frame(stream, max_payload_bytes=1024)

    def test_rejects_wrong_protocol_version(self) -> None:
        stream = io.BytesIO()
        write_frame(stream, {"version": 999, "op": "x"})
        stream.seek(0)
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "version"):
            read_frame(stream, max_payload_bytes=1024)

    def test_rejects_header_over_one_mib(self) -> None:
        stream = io.BytesIO(struct.pack(">I", MAX_HEADER_BYTES + 1))
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "header"):
            read_frame(stream, max_payload_bytes=1024)

    def test_rejects_payload_larger_than_caller_limit_before_reading_payload(self) -> None:
        header = json.dumps({"version": 1, "op": "x", "payloadBytes": 1025}).encode("utf-8")
        stream = io.BytesIO(struct.pack(">I", len(header)) + header)
        with self.assertRaisesRegex(RemoteGenerationProtocolError, "payload"):
            read_frame(stream, max_payload_bytes=1024)

    def test_write_frame_overrides_untrusted_payload_size(self) -> None:
        stream = io.BytesIO()
        write_frame(stream, {"version": 1, "op": "x", "payloadBytes": 999999}, b"abc")
        stream.seek(0)
        header, payload = read_frame(stream, max_payload_bytes=3)
        self.assertEqual(header["payloadBytes"], 3)
        self.assertEqual(payload, b"abc")


if __name__ == "__main__":
    unittest.main()
