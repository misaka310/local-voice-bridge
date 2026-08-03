from __future__ import annotations

import json
import urllib.request
from typing import Any


class ControlPanelApiClient:
    """Loopback-only HTTP client used by the Windows control panel and mic controller."""

    def __init__(self, base_url: str = "http://127.0.0.1:8717", *, timeout: float = 0.4) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout = max(0.1, float(timeout))

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError(str(body.get("error") if isinstance(body, dict) else "invalid response"))
        return body

    def get_snapshot(self) -> dict[str, Any]:
        return self._request("/v1/control-panel")

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v1/control-panel/settings", method="POST", payload=payload)

    def send_command(self, command: str) -> dict[str, Any]:
        return self._request("/v1/control-panel/command", method="POST", payload={"command": command})

    def send_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "/v1/conversation/event",
            method="POST",
            payload={"type": event_type, "payload": payload},
        )

    def update_conversation_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v1/conversation/state", method="POST", payload=payload)
