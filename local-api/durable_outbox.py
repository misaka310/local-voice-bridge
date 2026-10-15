from __future__ import annotations

import math
import re
import time
import uuid
from typing import Any

from state_normalization import normalize_cancel_grace_ms

ALLOWED_COMMANDS = {"next", "regen", "replay", "stop", "reload_extension"}
ALLOWED_CONVERSATION_EVENTS = {"cancel_pending", "transcript"}
COMMAND_OUTBOX_LIMIT = 256
CONVERSATION_EVENT_OUTBOX_LIMIT = 128
CONSUMER_ACK_LIMIT = 32
CONSUMER_ACK_TTL_SECONDS = 7 * 24 * 60 * 60
CONSUMER_SEEN_WRITE_INTERVAL_SECONDS = 60
LEGACY_CONSUMER_MIGRATION_KEEP = 4
LEGACY_CONSUMER_ID = "legacy"

_SAFE_CONSUMER_ID = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_DELIVERY_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def normalize_consumer_id(value: Any) -> str:
    normalized = _SAFE_CONSUMER_ID.sub("-", str(value or "").strip())
    normalized = normalized.strip(".-_")[:64]
    return normalized or LEGACY_CONSUMER_ID


def normalize_delivery_id(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if _SAFE_DELIVERY_ID.fullmatch(candidate) else str(uuid.uuid4())


def safe_after_id(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def safe_seen_at(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return numeric if math.isfinite(numeric) and numeric > 0.0 else 0.0


def normalize_command_items(value: Any) -> list[dict[str, Any]]:
    return _normalize_items(value, event=False)


def normalize_event_items(value: Any) -> list[dict[str, Any]]:
    return _normalize_items(value, event=True)


def _normalize_items(value: Any, *, event: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        try:
            item_id = int(raw_item.get("id"))
            created_at = float(raw_item.get("createdAt"))
        except (TypeError, ValueError):
            continue
        if item_id < 1 or created_at < 0:
            continue
        if event:
            event_type = str(raw_item.get("type") or "").strip().lower()
            payload = raw_item.get("payload")
            if event_type not in ALLOWED_CONVERSATION_EVENTS or not isinstance(payload, dict):
                continue
            item = {"id": item_id, "type": event_type, "payload": dict(payload), "createdAt": created_at}
        else:
            command = str(raw_item.get("command") or "").strip().lower()
            if command not in ALLOWED_COMMANDS:
                continue
            item = {"id": item_id, "command": command, "createdAt": created_at}
        items.append(item)
    return sorted(items, key=lambda item: int(item["id"]))


class DurableOutbox:
    """Two-stream durable delivery outbox with independent per-consumer ACK cursors."""

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []
        self.conversation_events: list[dict[str, Any]] = []
        self.next_command_id = 1
        self.next_conversation_event_id = 1
        self.consumer_acks: dict[str, dict[str, int | float]] = {}
        self.dirty = False

    def mark_persisted(self) -> None:
        self.dirty = False

    @staticmethod
    def _consumer_progress(cursor: dict[str, int | float]) -> tuple[int, int]:
        return (
            safe_after_id(cursor.get("conversationEvent")),
            safe_after_id(cursor.get("command")),
        )

    def _prune_consumers(
        self,
        *,
        now: float | None = None,
        preserve: set[str] | None = None,
        migrate_legacy: bool = False,
    ) -> bool:
        timestamp = float(time.time() if now is None else now)
        protected = set(preserve or ())
        changed = False
        legacy_rows = [
            (index, consumer_id, cursor)
            for index, (consumer_id, cursor) in enumerate(self.consumer_acks.items())
            if safe_seen_at(cursor.get("lastSeenAt")) <= 0.0
        ]
        if migrate_legacy and legacy_rows:
            positive = [row for row in legacy_rows if max(self._consumer_progress(row[2])) > 0]
            ranked = sorted(
                positive or legacy_rows,
                key=lambda row: (*self._consumer_progress(row[2]), row[0]),
                reverse=True,
            )
            keep_count = min(LEGACY_CONSUMER_MIGRATION_KEEP, len(ranked)) if positive else 1
            legacy_keep = {consumer_id for _index, consumer_id, _cursor in ranked[:keep_count]}
            for _index, consumer_id, cursor in legacy_rows:
                if consumer_id in legacy_keep or consumer_id in protected:
                    cursor["lastSeenAt"] = timestamp
                else:
                    self.consumer_acks.pop(consumer_id, None)
                changed = True

        stale_before = timestamp - CONSUMER_ACK_TTL_SECONDS
        for consumer_id, cursor in list(self.consumer_acks.items()):
            if consumer_id in protected:
                continue
            seen_at = safe_seen_at(cursor.get("lastSeenAt"))
            if seen_at > 0.0 and seen_at < stale_before:
                self.consumer_acks.pop(consumer_id, None)
                changed = True

        if len(self.consumer_acks) > CONSUMER_ACK_LIMIT:
            removable = sorted(
                (
                    (safe_seen_at(cursor.get("lastSeenAt")), *self._consumer_progress(cursor), consumer_id)
                    for consumer_id, cursor in self.consumer_acks.items()
                    if consumer_id not in protected
                ),
            )
            for _seen_at, _event_cursor, _command_cursor, consumer_id in removable:
                if len(self.consumer_acks) <= CONSUMER_ACK_LIMIT:
                    break
                self.consumer_acks.pop(consumer_id, None)
                changed = True

        if changed:
            self.dirty = True
        return changed

    def load(self, raw: Any, *, now: float | None = None) -> bool:
        state = raw if isinstance(raw, dict) else {}
        self.commands = normalize_command_items(state.get("commands"))
        self.conversation_events = normalize_event_items(state.get("conversationEvents"))
        try:
            self.next_command_id = max(1, int(state.get("nextCommandId") or 1))
        except (TypeError, ValueError):
            self.next_command_id = 1
        try:
            self.next_conversation_event_id = max(1, int(state.get("nextConversationEventId") or 1))
        except (TypeError, ValueError):
            self.next_conversation_event_id = 1
        if self.commands:
            self.next_command_id = max(self.next_command_id, int(self.commands[-1]["id"]) + 1)
        if self.conversation_events:
            self.next_conversation_event_id = max(
                self.next_conversation_event_id,
                int(self.conversation_events[-1]["id"]) + 1,
            )
        self.consumer_acks = {}
        raw_consumers = state.get("consumerAcks")
        if isinstance(raw_consumers, dict):
            for consumer_id, raw_cursor in raw_consumers.items():
                if not isinstance(raw_cursor, dict):
                    continue
                normalized = normalize_consumer_id(consumer_id)
                self.consumer_acks[normalized] = {
                    "command": safe_after_id(raw_cursor.get("command")),
                    "conversationEvent": safe_after_id(raw_cursor.get("conversationEvent")),
                    "lastSeenAt": safe_seen_at(raw_cursor.get("lastSeenAt")),
                }
        changed = self._prune_consumers(now=now, migrate_legacy=True)
        if self.compact_acknowledged(now=now):
            changed = True
        return changed

    def export(self) -> dict[str, Any]:
        return {
            "nextCommandId": self.next_command_id,
            "nextConversationEventId": self.next_conversation_event_id,
            "commands": self.commands,
            "conversationEvents": self.conversation_events,
            "consumerAcks": self.consumer_acks,
        }

    def enqueue_command(self, command: str, *, now: float | None = None) -> dict[str, Any]:
        normalized = str(command or "").strip().lower()
        if normalized not in ALLOWED_COMMANDS:
            raise ValueError(f"unsupported command: {normalized}")
        self.compact_acknowledged(now=now)
        if len(self.commands) >= COMMAND_OUTBOX_LIMIT:
            raise RuntimeError("command outbox is full; acknowledge pending commands before enqueueing more")
        item = {
            "id": self.next_command_id,
            "command": normalized,
            "createdAt": float(time.time() if now is None else now),
        }
        self.next_command_id += 1
        self.commands.append(item)
        self.dirty = True
        return dict(item)

    def enqueue_conversation_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(event_type or "").strip().lower()
        if normalized_type not in ALLOWED_CONVERSATION_EVENTS:
            raise ValueError(f"unsupported conversation event: {normalized_type}")
        safe_payload = dict(payload) if isinstance(payload, dict) else {}
        if normalized_type == "transcript":
            text = str(safe_payload.get("text") or "").strip()
            if not text:
                raise ValueError("transcript text is required")
            safe_payload = {
                "sessionId": safe_after_id(safe_payload.get("sessionId")),
                "text": text[:4000],
                "cancelGraceMs": normalize_cancel_grace_ms(safe_payload.get("cancelGraceMs")),
                "deliveryId": normalize_delivery_id(safe_payload.get("deliveryId")),
            }
        else:
            safe_payload = {"sessionId": safe_after_id(safe_payload.get("sessionId"))}
        self.compact_acknowledged(now=now)
        if len(self.conversation_events) >= CONVERSATION_EVENT_OUTBOX_LIMIT:
            raise RuntimeError("conversation event outbox is full; acknowledge pending events before enqueueing more")
        item = {
            "id": self.next_conversation_event_id,
            "type": normalized_type,
            "payload": safe_payload,
            "createdAt": float(time.time() if now is None else now),
        }
        self.next_conversation_event_id += 1
        self.conversation_events.append(item)
        self.dirty = True
        return dict(item)

    def register_consumer(
        self,
        consumer_id: Any,
        *,
        replay_existing: bool,
        now: float | None = None,
    ) -> str:
        normalized = normalize_consumer_id(consumer_id)
        timestamp = float(time.time() if now is None else now)
        if normalized not in self.consumer_acks:
            self.consumer_acks[normalized] = {
                "command": 0 if replay_existing or normalized == LEGACY_CONSUMER_ID else self.next_command_id - 1,
                "conversationEvent": (
                    0
                    if replay_existing or normalized == LEGACY_CONSUMER_ID
                    else self.next_conversation_event_id - 1
                ),
                "lastSeenAt": timestamp,
            }
            self.dirty = True
        else:
            cursor = self.consumer_acks[normalized]
            last_seen_at = safe_seen_at(cursor.get("lastSeenAt"))
            if last_seen_at <= 0.0 or timestamp - last_seen_at >= CONSUMER_SEEN_WRITE_INTERVAL_SECONDS:
                cursor["lastSeenAt"] = timestamp
                self.dirty = True
        self._prune_consumers(now=timestamp, preserve={normalized})
        return normalized

    @staticmethod
    def _items_after(items: list[dict[str, Any]], after_id: Any, cursor: int) -> list[dict[str, Any]]:
        minimum_id = max(safe_after_id(after_id), cursor)
        return [dict(item) for item in items if int(item["id"]) > minimum_id]

    def poll(
        self,
        *,
        after_command_id: Any = 0,
        after_event_id: Any = 0,
        consumer_id: Any = None,
        replay_existing: bool = False,
        now: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        consumer = self.register_consumer(consumer_id, replay_existing=replay_existing, now=now)
        cursor = self.consumer_acks[consumer]
        return (
            self._items_after(self.commands, after_command_id, cursor["command"]),
            self._items_after(self.conversation_events, after_event_id, cursor["conversationEvent"]),
        )

    def poll_commands(
        self,
        after_id: Any,
        *,
        consumer_id: Any = None,
        replay_existing: bool = False,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        commands, _ = self.poll(
            after_command_id=after_id,
            after_event_id=self.next_conversation_event_id - 1,
            consumer_id=consumer_id,
            replay_existing=replay_existing,
            now=now,
        )
        return commands

    def acknowledge(
        self,
        consumer_id: Any,
        item_id: Any,
        *,
        stream: str,
        now: float | None = None,
    ) -> int:
        if stream not in {"command", "conversationEvent"}:
            raise ValueError(f"unsupported outbox stream: {stream}")
        consumer = self.register_consumer(consumer_id, replay_existing=False, now=now)
        acknowledged = safe_after_id(item_id)
        previous = safe_after_id(self.consumer_acks[consumer][stream])
        self.consumer_acks[consumer][stream] = max(previous, acknowledged)
        if self.consumer_acks[consumer][stream] != previous:
            self.dirty = True
        self.compact_acknowledged(now=now)
        return int(self.consumer_acks[consumer][stream])

    def compact_acknowledged(self, *, now: float | None = None) -> bool:
        changed = self._prune_consumers(now=now)
        if not self.consumer_acks:
            return changed
        command_floor = min(safe_after_id(cursor["command"]) for cursor in self.consumer_acks.values())
        event_floor = min(safe_after_id(cursor["conversationEvent"]) for cursor in self.consumer_acks.values())
        commands = [item for item in self.commands if int(item["id"]) > command_floor]
        conversation_events = [
            item for item in self.conversation_events if int(item["id"]) > event_floor
        ]
        if commands != self.commands or conversation_events != self.conversation_events:
            self.commands = commands
            self.conversation_events = conversation_events
            self.dirty = True
            changed = True
        return changed

    def claim_commands(self, after_id: Any) -> list[dict[str, Any]]:
        safe_after = safe_after_id(after_id)
        claimed = [dict(item) for item in self.commands if int(item["id"]) > safe_after]
        claimed_ids = {int(item["id"]) for item in claimed}
        if claimed_ids:
            self.commands = [item for item in self.commands if int(item["id"]) not in claimed_ids]
            self.dirty = True
        return claimed

    def claim_conversation_events(self) -> list[dict[str, Any]]:
        claimed = [dict(item) for item in self.conversation_events]
        if claimed:
            self.conversation_events = []
            self.dirty = True
        return claimed
