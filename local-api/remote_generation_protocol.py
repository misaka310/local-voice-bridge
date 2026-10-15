from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO


PROTOCOL_VERSION = 1
MAX_HEADER_BYTES = 1024 * 1024


class RemoteGenerationProtocolError(ValueError):
    pass


def _read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    if size < 0:
        raise RemoteGenerationProtocolError(f"invalid {label} size")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise RemoteGenerationProtocolError(f"truncated {label}")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def write_frame(stream: BinaryIO, header: dict[str, Any], payload: bytes = b"") -> None:
    safe_header = dict(header)
    safe_header["payloadBytes"] = len(payload)
    raw_header = json.dumps(safe_header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw_header) > MAX_HEADER_BYTES:
        raise RemoteGenerationProtocolError("header exceeds limit")
    stream.write(struct.pack(">I", len(raw_header)))
    stream.write(raw_header)
    if payload:
        stream.write(payload)
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def read_frame(stream: BinaryIO, *, max_payload_bytes: int) -> tuple[dict[str, Any], bytes]:
    raw_length = stream.read(4)
    if len(raw_length) != 4:
        raise RemoteGenerationProtocolError("truncated header length")
    (header_length,) = struct.unpack(">I", raw_length)
    if header_length > MAX_HEADER_BYTES:
        raise RemoteGenerationProtocolError("header exceeds limit")
    raw_header = _read_exact(stream, header_length, "header")
    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteGenerationProtocolError("invalid header JSON") from exc
    if not isinstance(header, dict):
        raise RemoteGenerationProtocolError("header JSON must be an object")
    if header.get("version") != PROTOCOL_VERSION:
        raise RemoteGenerationProtocolError("unsupported protocol version")
    payload_size = header.get("payloadBytes", 0)
    if isinstance(payload_size, bool) or not isinstance(payload_size, int) or payload_size < 0:
        raise RemoteGenerationProtocolError("invalid payload size")
    if payload_size > max_payload_bytes:
        raise RemoteGenerationProtocolError("payload exceeds limit")
    payload = _read_exact(stream, payload_size, "payload") if payload_size else b""
    return header, payload
