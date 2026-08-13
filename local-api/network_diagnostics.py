from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping


ALLOWED_CHATGPT_HOSTS = {"chatgpt.com", "chat.openai.com"}
DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_BACKUP_COUNT = 2


def sanitize_network_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    host = str(payload.get("host") or "").strip().lower()
    if host not in ALLOWED_CHATGPT_HOSTS:
        raise ValueError("unsupported ChatGPT host")

    raw_path = str(payload.get("path") or "/").strip()
    path = raw_path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = f"/{path}"
    path = path[:2048] or "/"

    try:
        status_code = int(payload.get("statusCode"))
    except (TypeError, ValueError) as exc:
        raise ValueError("statusCode must be an integer") from exc
    if status_code < 0 or status_code > 999:
        raise ValueError("statusCode is out of range")

    try:
        tab_id = int(payload.get("tabId", -1))
    except (TypeError, ValueError):
        tab_id = -1

    method = str(payload.get("method") or "GET").strip().upper()[:16] or "GET"
    request_type = str(payload.get("type") or "other").strip().lower()[:32] or "other"
    observed_at = str(payload.get("observedAt") or "").strip()[:64]

    return {
        "observedAt": observed_at,
        "method": method,
        "statusCode": status_code,
        "type": request_type,
        "tabId": tab_id,
        "host": host,
        "path": path,
        "synthetic": bool(payload.get("synthetic", False)),
    }


class ChatgptNetworkEventLogger:
    def __init__(
        self,
        path: Path | str,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.max_bytes = max(1024, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self._lock = threading.Lock()

    def _backup_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.name}.{index}")

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current_size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if current_size + max(0, int(incoming_bytes)) <= self.max_bytes:
            return
        if self.backup_count <= 0:
            self.path.unlink(missing_ok=True)
            return
        for index in range(self.backup_count, 1, -1):
            source = self._backup_path(index - 1)
            if source.exists():
                source.replace(self._backup_path(index))
        self.path.replace(self._backup_path(1))

    def record(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = sanitize_network_event(payload)
        record = {
            "recordedAtUnixMs": int(time.time() * 1000),
            **event,
        }
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_if_needed(len(encoded.encode("utf-8")))
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
        return record


def default_chatgpt_network_log_path(local_api_root: Path | str) -> Path:
    return Path(local_api_root).resolve() / "runtime" / "debug" / "chatgpt-network-events.jsonl"
