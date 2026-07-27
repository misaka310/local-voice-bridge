from __future__ import annotations

import errno
import json
from http.server import BaseHTTPRequestHandler
from typing import Any

_CLIENT_DISCONNECT_ERRNOS = {
    errno.EPIPE,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    10053,
    10054,
    10058,
}


class ResponseWriteError(RuntimeError):
    """A response could not be written after headers may have been sent."""


def is_normal_client_disconnect(exc: BaseException) -> bool:
    return isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)) or (
        isinstance(exc, OSError) and exc.errno in _CLIENT_DISCONNECT_ERRNOS
    )


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> bool:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except OSError as exc:
        if is_normal_client_disconnect(exc):
            return False
        raise ResponseWriteError("response write failed") from exc
    return True


def request_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("request JSON must be object")
    return parsed
