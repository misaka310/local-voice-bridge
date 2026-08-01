from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping


_IDENTIFIER_FIELDS = {
    "traceId",
    "sessionId",
    "turnId",
    "submissionId",
    "tabId",
    "pageInstanceId",
    "conversationKey",
    "assistantMessageKey",
    "generationId",
    "playbackId",
    "chunkIndex",
    "cancelEpoch",
    "ttsProfile",
    "sttDevice",
    "gpuOwner",
    "result",
    "reason",
    "textLength",
    "textHash",
    "waitSeconds",
    "durationSeconds",
    "passed",
    "reasons",
    "phase",
    "duplicate",
    "capacity",
}


class RuntimeEventLogger:
    """Append-only JSONL event log that never accepts prompt or reply bodies."""

    def __init__(self, path: Path | str | None) -> None:
        self.path = Path(path).expanduser().resolve() if path else None
        self._lock = threading.Lock()

    @staticmethod
    def _sanitize(fields: Mapping[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in _IDENTIFIER_FIELDS or value is None:
                continue
            if key in {"tabId", "chunkIndex", "cancelEpoch", "textLength", "capacity"}:
                try:
                    safe[key] = int(value)
                except (TypeError, ValueError):
                    continue
            elif key in {"waitSeconds", "durationSeconds"}:
                try:
                    safe[key] = float(value)
                except (TypeError, ValueError):
                    continue
            elif key in {"passed", "duplicate"}:
                safe[key] = bool(value)
            elif key == "reasons":
                safe[key] = [str(item)[:120] for item in list(value or [])[:16]]
            else:
                safe[key] = str(value)[:512]
        return safe

    def emit(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        name = str(event or "").strip()
        if not name:
            return
        record = {
            "event": name[:96],
            "wallTimeNs": time.time_ns(),
            "monotonicNs": time.perf_counter_ns(),
            "pid": os.getpid(),
            **self._sanitize(fields),
        }
        encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(encoded)
        except (OSError, UnicodeError):
            return


class NullRuntimeEventLogger(RuntimeEventLogger):
    def __init__(self) -> None:
        super().__init__(None)


def default_event_log_path(app_root: Path | str) -> Path:
    override = str(os.environ.get("LOCAL_VOICE_EVENT_LOG") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(app_root).resolve() / "local-api" / "runtime" / "live-events.jsonl"


def event_fields(identity: Any, **extra: Any) -> dict[str, Any]:
    fields = {
        "turnId": getattr(identity, "turn_id", ""),
        "submissionId": getattr(identity, "submission_id", ""),
        "tabId": getattr(identity, "tab_id", None),
        "pageInstanceId": getattr(identity, "page_instance_id", ""),
        "conversationKey": getattr(identity, "conversation_key", ""),
        "generationId": getattr(identity, "generation_id", ""),
        "playbackId": getattr(identity, "playback_id", ""),
        "cancelEpoch": getattr(identity, "cancel_epoch", 0),
    }
    fields.update(extra)
    return fields
