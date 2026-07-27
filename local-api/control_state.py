from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from browser_runtime_state import (
    DEFAULT_BROWSER_RUNTIME_STATE,
    clone_browser_runtime,
    normalize_browser_runtime,
)
from durable_outbox import (
    ALLOWED_COMMANDS,
    ALLOWED_CONVERSATION_EVENTS,
    COMMAND_OUTBOX_LIMIT,
    CONVERSATION_EVENT_OUTBOX_LIMIT,
    DurableOutbox,
)
from state_normalization import (
    ALLOWED_CONVERSATION_PHASES,
    DEFAULT_CONVERSATION_STATE,
    DEFAULT_EXTENSION_STATE,
    DEFAULT_SETTINGS,
    clamp_volume,
    normalize_cancel_grace_ms,
    normalize_conversation_state,
    normalize_extension_state,
    normalize_reference_voice,
    normalize_settings,
    normalize_stt_model,
)


class ControlStateStore:
    """Persistent coordinator for settings, runtime snapshots, and durable browser delivery.

    This class intentionally coordinates focused components instead of implementing
    normalization, browser-state schemas, or ACK/outbox algorithms itself.
    """

    def __init__(self, path: Path, *, stale_after_seconds: float = 3.0) -> None:
        self.path = Path(path)
        self.stale_after_seconds = max(0.5, float(stale_after_seconds))
        self._lock = threading.RLock()
        self._settings = dict(DEFAULT_SETTINGS)
        self._initialized = False
        self._settings_revision = 0
        self._outbox = DurableOutbox()
        self._extension_state = dict(DEFAULT_EXTENSION_STATE)
        self._conversation_state = dict(DEFAULT_CONVERSATION_STATE)
        self._browser_runtime = dict(DEFAULT_BROWSER_RUNTIME_STATE)
        self._load()

    # Compatibility accessors retained for existing diagnostics/tests. New code
    # must use the public enqueue/poll/ack methods instead of mutating these.
    @property
    def _commands(self) -> list[dict[str, Any]]:
        return self._outbox.commands

    @property
    def _conversation_events(self) -> list[dict[str, Any]]:
        return self._outbox.conversation_events

    @property
    def _next_command_id(self) -> int:
        return self._outbox.next_command_id

    @property
    def _next_conversation_event_id(self) -> int:
        return self._outbox.next_conversation_event_id

    @property
    def _consumer_acks(self) -> dict[str, dict[str, int]]:
        return self._outbox.consumer_acks

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        self._settings = normalize_settings(raw.get("settings"))
        self._initialized = bool(raw.get("initialized"))
        try:
            self._settings_revision = max(0, int(raw.get("settingsRevision") or 0))
        except (TypeError, ValueError):
            self._settings_revision = 0
        self._outbox.load(raw)
        self._browser_runtime = normalize_browser_runtime(
            raw.get("browserRuntime"),
            now=float(raw.get("savedAt") or time.time()),
        )

    def _persist_locked(self) -> None:
        payload = {
            "version": 5,
            "savedAt": time.time(),
            "initialized": self._initialized,
            "settingsRevision": self._settings_revision,
            "settings": self._settings,
            "browserRuntime": self._browser_runtime,
            **self._outbox.export(),
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
                merged["voiceVolume"] = clamp_volume(payload.get("voiceVolume"))
            if "referenceVoice" in payload or "voiceId" in payload:
                merged["referenceVoice"] = normalize_reference_voice(
                    payload.get("referenceVoice", payload.get("voiceId"))
                )
                merged["referenceVoiceExplicit"] = bool(payload.get("referenceVoiceExplicit", True))
            if "micConversationEnabled" in payload:
                merged["micConversationEnabled"] = bool(payload.get("micConversationEnabled"))
            if "sttModel" in payload:
                merged["sttModel"] = normalize_stt_model(payload.get("sttModel"))
            if "cancelGraceMs" in payload:
                merged["cancelGraceMs"] = normalize_cancel_grace_ms(payload.get("cancelGraceMs"))
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
        with self._lock:
            item = self._outbox.enqueue_command(command)
            self._persist_locked()
            return item

    def poll_commands(
        self,
        after_id: int,
        *,
        consumer_id: Any = None,
        replay_existing: bool = False,
    ) -> list[dict[str, Any]]:
        with self._lock:
            known_consumers = len(self._outbox.consumer_acks)
            items = self._outbox.poll_commands(
                after_id,
                consumer_id=consumer_id,
                replay_existing=replay_existing,
            )
            if len(self._outbox.consumer_acks) != known_consumers:
                self._persist_locked()
            return items

    def claim_commands(self, after_id: int) -> list[dict[str, Any]]:
        with self._lock:
            claimed = self._outbox.claim_commands(after_id)
            if claimed:
                self._persist_locked()
            return claimed

    def enqueue_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = self._outbox.enqueue_conversation_event(event_type, payload)
            self._persist_locked()
            return item

    def claim_conversation_events(self) -> list[dict[str, Any]]:
        with self._lock:
            claimed = self._outbox.claim_conversation_events()
            if claimed:
                self._persist_locked()
            return claimed

    def acknowledge_commands(self, command_id: int, *, consumer_id: Any = None) -> int:
        with self._lock:
            acknowledged = self._outbox.acknowledge(consumer_id, command_id, stream="command")
            self._persist_locked()
            return acknowledged

    def acknowledge_conversation_events(self, event_id: int, *, consumer_id: Any = None) -> int:
        with self._lock:
            acknowledged = self._outbox.acknowledge(
                consumer_id,
                event_id,
                stream="conversationEvent",
            )
            self._persist_locked()
            return acknowledged

    def update_browser_runtime(self, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._browser_runtime = normalize_browser_runtime(payload, now=timestamp)
            self._persist_locked()
            return clone_browser_runtime(self._browser_runtime)

    def browser_runtime_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return clone_browser_runtime(self._browser_runtime)

    def update_conversation_state(self, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._conversation_state = normalize_conversation_state(payload, now=timestamp)
            return self.snapshot(now=timestamp)

    def update_extension_state(self, payload: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            self._extension_state = normalize_extension_state(payload, now=timestamp)
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
                "browserRuntime": clone_browser_runtime(self._browser_runtime),
                "lastCommandId": self._outbox.next_command_id - 1,
                "lastConversationEventId": self._outbox.next_conversation_event_id - 1,
            }

    def poll(
        self,
        after_id: int | None = None,
        *,
        after_command_id: Any | None = None,
        after_event_id: Any | None = None,
        consumer_id: Any = None,
        replay_existing: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        command_cursor = after_id if after_command_id is None else after_command_id
        event_cursor = 0 if after_event_id is None else after_event_id
        with self._lock:
            snapshot = self.snapshot(now=now)
            known_consumers = len(self._outbox.consumer_acks)
            commands, conversation_events = self._outbox.poll(
                after_command_id=command_cursor,
                after_event_id=event_cursor,
                consumer_id=consumer_id,
                replay_existing=replay_existing,
            )
            if len(self._outbox.consumer_acks) != known_consumers:
                self._persist_locked()
            snapshot["commands"] = commands
            snapshot["conversationEvents"] = conversation_events
            return snapshot
