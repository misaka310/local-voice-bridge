from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ALLOWED_COMMANDS = {"next", "regen", "replay", "stop"}
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "voiceVolume": 0.6,
    "referenceVoice": "",
    "referenceVoiceExplicit": False,
    "micConversationEnabled": False,
    "sttModel": "small",
    "cancelGraceMs": 700,
}
DEFAULT_CONVERSATION_STATE: dict[str, Any] = {
    "phase": "off",
    "statusText": "マイク会話オフ",
    "sttDevice": "",
    "sttModel": "small",
    "error": "",
    "updatedAt": 0.0,
}
ALLOWED_CONVERSATION_PHASES = {
    "off",
    "idle",
    "recording",
    "preparing_model",
    "transcribing",
    "pending_send",
    "sending",
    "waiting_response",
    "speaking",
    "error",
}
ALLOWED_CONVERSATION_EVENTS = {"cancel_pending", "transcript"}
DEFAULT_EXTENSION_STATE: dict[str, Any] = {
    "connected": False,
    "statusText": "Waiting for ChatGPT",
    "statusLevel": "info",
    "currentText": "",
    "queueSize": 0,
    "isPlaying": False,
    "playbackPhase": "idle",
    "replayAvailable": False,
    "tabsCount": 0,
    "loadedVersion": "",
    "updatedAt": 0.0,
}


def _clamp_volume(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_SETTINGS["voiceVolume"])
    return min(1.0, max(0.0, number))


def _normalize_reference_voice(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized.lower() in {"none", "qwen", "qwen3"}:
        return ""
    if "/" in normalized or "\\" in normalized:
        return ""
    return normalized


def _normalize_stt_model(value: Any) -> str:
    normalized = str(value or DEFAULT_SETTINGS["sttModel"]).strip()
    return normalized if normalized in {"small", "medium", "large-v3-turbo"} else str(DEFAULT_SETTINGS["sttModel"])


def _normalize_cancel_grace_ms(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS["cancelGraceMs"])
    return min(5000, max(0, number))


def _normalize_settings(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    reference_voice = _normalize_reference_voice(raw.get("referenceVoice", raw.get("voiceId", "")))
    if "referenceVoiceExplicit" in raw:
        reference_voice_explicit = bool(raw.get("referenceVoiceExplicit"))
    else:
        reference_voice_explicit = bool(reference_voice)
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_SETTINGS["enabled"])),
        "voiceVolume": _clamp_volume(raw.get("voiceVolume", DEFAULT_SETTINGS["voiceVolume"])),
        "referenceVoice": reference_voice,
        "referenceVoiceExplicit": reference_voice_explicit,
        "micConversationEnabled": bool(
            raw.get("micConversationEnabled", DEFAULT_SETTINGS["micConversationEnabled"])
        ),
        "sttModel": _normalize_stt_model(raw.get("sttModel")),
        "cancelGraceMs": _normalize_cancel_grace_ms(raw.get("cancelGraceMs")),
    }


def _normalize_conversation_state(value: Any, *, now: float) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    phase = str(raw.get("phase") or "idle").strip().lower()
    if phase not in ALLOWED_CONVERSATION_PHASES:
        phase = "error"
    return {
        "phase": phase,
        "statusText": str(raw.get("statusText") or "待機中"),
        "sttDevice": str(raw.get("sttDevice") or ""),
        "sttModel": _normalize_stt_model(raw.get("sttModel")),
        "error": str(raw.get("error") or ""),
        "updatedAt": float(now),
    }


def _normalize_extension_state(value: Any, *, now: float) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    status_text = str(raw.get("statusText") or "Ready")
    status_level = str(raw.get("statusLevel") or "info")
    current_text = str(raw.get("currentText") or "")
    playback_phase = str(raw.get("playbackPhase") or "idle")
    try:
        queue_size = max(0, int(raw.get("queueSize") or 0))
    except (TypeError, ValueError):
        queue_size = 0
    try:
        tabs_count = max(0, int(raw.get("tabsCount") or 0))
    except (TypeError, ValueError):
        tabs_count = 0
    return {
        "connected": True,
        "statusText": status_text,
        "statusLevel": status_level,
        "currentText": current_text,
        "queueSize": queue_size,
        "isPlaying": bool(raw.get("isPlaying")),
        "playbackPhase": playback_phase,
        "replayAvailable": bool(raw.get("replayAvailable")),
        "tabsCount": tabs_count,
        "loadedVersion": str(raw.get("loadedVersion") or "").strip()[:32],
        "updatedAt": float(now),
    }


class ControlStateStore:
    def __init__(self, path: Path, *, stale_after_seconds: float = 3.0) -> None:
        self.path = Path(path)
        self.stale_after_seconds = max(0.5, float(stale_after_seconds))
        self._lock = threading.RLock()
        self._settings = dict(DEFAULT_SETTINGS)
        self._initialized = False
        self._settings_revision = 0
        self._commands: list[dict[str, Any]] = []
        self._next_command_id = 1
        self._conversation_events: list[dict[str, Any]] = []
        self._next_conversation_event_id = 1
        self._extension_state = dict(DEFAULT_EXTENSION_STATE)
        self._conversation_state = dict(DEFAULT_CONVERSATION_STATE)
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        self._settings = _normalize_settings(raw.get("settings"))
        self._initialized = bool(raw.get("initialized"))
        try:
            self._settings_revision = max(0, int(raw.get("settingsRevision") or 0))
        except (TypeError, ValueError):
            self._settings_revision = 0
        try:
            self._next_command_id = max(1, int(raw.get("nextCommandId") or 1))
        except (TypeError, ValueError):
            self._next_command_id = 1
        try:
            self._next_conversation_event_id = max(1, int(raw.get("nextConversationEventId") or 1))
        except (TypeError, ValueError):
            self._next_conversation_event_id = 1

    def _persist_locked(self) -> None:
        payload = {
            "version": 2,
            "initialized": self._initialized,
            "settingsRevision": self._settings_revision,
            "nextCommandId": self._next_command_id,
            "nextConversationEventId": self._next_conversation_event_id,
            "settings": self._settings,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = dict(self._settings)
            if "enabled" in payload:
                merged["enabled"] = bool(payload.get("enabled"))
            if "voiceVolume" in payload:
                merged["voiceVolume"] = _clamp_volume(payload.get("voiceVolume"))
            if "referenceVoice" in payload or "voiceId" in payload:
                merged["referenceVoice"] = _normalize_reference_voice(
                    payload.get("referenceVoice", payload.get("voiceId"))
                )
                merged["referenceVoiceExplicit"] = bool(payload.get("referenceVoiceExplicit", True))
            if "micConversationEnabled" in payload:
                merged["micConversationEnabled"] = bool(payload.get("micConversationEnabled"))
            if "sttModel" in payload:
                merged["sttModel"] = _normalize_stt_model(payload.get("sttModel"))
            if "cancelGraceMs" in payload:
                merged["cancelGraceMs"] = _normalize_cancel_grace_ms(payload.get("cancelGraceMs"))
            changed = merged != self._settings
            initialized = self._initialized or bool(payload.get("initialized"))
            if initialized != self._initialized:
                changed = True
            self._settings = merged
            self._initialized = initialized
            if changed:
                self._settings_revision += 1
                self._persist_locked()
            return self.snapshot()

    def enqueue_command(self, command: str) -> dict[str, Any]:
        normalized = str(command or "").strip().lower()
        if normalized not in ALLOWED_COMMANDS:
            raise ValueError(f"unsupported command: {normalized}")
        with self._lock:
            item = {
                "id": self._next_command_id,
                "command": normalized,
                "createdAt": time.time(),
            }
            self._next_command_id += 1
            self._commands.append(item)
            self._commands = self._commands[-64:]
            self._persist_locked()
            return dict(item)

    def poll_commands(self, after_id: int) -> list[dict[str, Any]]:
        try:
            safe_after = max(0, int(after_id))
        except (TypeError, ValueError):
            safe_after = 0
        with self._lock:
            return [dict(item) for item in self._commands if int(item["id"]) > safe_after]

    def claim_commands(self, after_id: int) -> list[dict[str, Any]]:
        try:
            safe_after = max(0, int(after_id))
        except (TypeError, ValueError):
            safe_after = 0
        with self._lock:
            claimed = [dict(item) for item in self._commands if int(item["id"]) > safe_after]
            claimed_ids = {int(item["id"]) for item in claimed}
            if claimed_ids:
                self._commands = [item for item in self._commands if int(item["id"]) not in claimed_ids]
            return claimed

    def enqueue_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized_type = str(event_type or "").strip().lower()
        if normalized_type not in ALLOWED_CONVERSATION_EVENTS:
            raise ValueError(f"unsupported conversation event: {normalized_type}")
        safe_payload = dict(payload) if isinstance(payload, dict) else {}
        if normalized_type == "transcript":
            text = str(safe_payload.get("text") or "").strip()
            if not text:
                raise ValueError("transcript text is required")
            safe_payload = {
                "sessionId": max(0, int(safe_payload.get("sessionId") or 0)),
                "text": text[:4000],
                "cancelGraceMs": _normalize_cancel_grace_ms(safe_payload.get("cancelGraceMs")),
            }
        else:
            safe_payload = {"sessionId": max(0, int(safe_payload.get("sessionId") or 0))}
        with self._lock:
            item = {
                "id": self._next_conversation_event_id,
                "type": normalized_type,
                "payload": safe_payload,
                "createdAt": time.time(),
            }
            self._next_conversation_event_id += 1
            self._conversation_events.append(item)
            self._conversation_events = self._conversation_events[-32:]
            self._persist_locked()
            return dict(item)

    def claim_conversation_events(self) -> list[dict[str, Any]]:
        with self._lock:
            claimed = [dict(item) for item in self._conversation_events]
            self._conversation_events = []
            return claimed

    def update_conversation_state(self, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._conversation_state = _normalize_conversation_state(payload, now=timestamp)
            return self.snapshot(now=timestamp)

    def update_extension_state(self, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._extension_state = _normalize_extension_state(payload, now=timestamp)
            return dict(self._extension_state)

    def _extension_snapshot_locked(self, *, now: float) -> dict[str, Any]:
        state = dict(self._extension_state)
        updated_at = float(state.get("updatedAt") or 0.0)
        if not updated_at or now - updated_at > self.stale_after_seconds:
            return dict(DEFAULT_EXTENSION_STATE)
        state["connected"] = True
        return state

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            return {
                "ok": True,
                "initialized": self._initialized,
                "settingsRevision": self._settings_revision,
                "settings": dict(self._settings),
                "extension": self._extension_snapshot_locked(now=timestamp),
                "conversation": dict(self._conversation_state),
                "lastCommandId": self._next_command_id - 1,
                "lastConversationEventId": self._next_conversation_event_id - 1,
            }

    def poll(self, after_id: int, *, now: float | None = None) -> dict[str, Any]:
        snapshot = self.snapshot(now=now)
        snapshot["commands"] = self.claim_commands(after_id)
        snapshot["conversationEvents"] = self.claim_conversation_events()
        return snapshot
