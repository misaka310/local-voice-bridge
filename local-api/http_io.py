from __future__ import annotations

import errno
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

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


MAX_POST_BODY_BYTES = 32 * 1024 * 1024
ALLOWED_BROWSER_ORIGIN_SCHEMES = {"chrome-extension"}


def browser_origin_allowed(origin: str | None) -> bool:
    value = str(origin or "").strip()
    if not value:
        # Native same-PC clients such as the Windows control panel do not send Origin.
        return True
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in ALLOWED_BROWSER_ORIGIN_SCHEMES
        and bool(parsed.netloc)
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def validate_post_request(handler: BaseHTTPRequestHandler) -> tuple[HTTPStatus, str] | None:
    origin = handler.headers.get("Origin")
    if not browser_origin_allowed(origin):
        return HTTPStatus.FORBIDDEN, "browser origin is not allowed"

    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json"

    raw_length = str(handler.headers.get("Content-Length") or "0").strip()
    try:
        content_length = int(raw_length)
    except ValueError:
        return HTTPStatus.BAD_REQUEST, "invalid Content-Length"
    if content_length < 0:
        return HTTPStatus.BAD_REQUEST, "invalid Content-Length"
    if content_length > MAX_POST_BODY_BYTES:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request body exceeds {MAX_POST_BODY_BYTES} bytes"
    return None


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
