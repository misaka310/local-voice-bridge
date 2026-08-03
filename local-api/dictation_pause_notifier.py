from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


class YouTubePauseNotifier:
    DEFAULT_STATE_URL = "http://127.0.0.1:17654/state"
    SOURCE = "local-voice-bridge"

    def __init__(
        self,
        *,
        state_url: str | None = None,
        timeout_seconds: float = 0.5,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        configured_url = state_url or os.environ.get("YOUTUBE_DICTATION_PAUSE_STATE_URL")
        self.state_url = self._normalize_state_url(configured_url)
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self._opener = opener or urllib.request.urlopen

    @classmethod
    def _normalize_state_url(cls, value: str | None) -> str:
        candidate = str(value or cls.DEFAULT_STATE_URL).strip()
        try:
            parsed = urllib.parse.urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return cls.DEFAULT_STATE_URL
        if (
            parsed.scheme.lower() != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or port is None
            or parsed.path != "/state"
            or parsed.query
            or parsed.fragment
        ):
            return cls.DEFAULT_STATE_URL
        return f"http://127.0.0.1:{port}/state"

    def set_active(self, active: bool) -> bool:
        payload = json.dumps(
            {"active": bool(active), "source": self.SOURCE},
            ensure_ascii=True,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.state_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status_value = getattr(response, "status", None)
                if status_value is None:
                    status_value = response.getcode()
                return 200 <= int(status_value) < 300
        except (OSError, ValueError, urllib.error.URLError):
            return False
