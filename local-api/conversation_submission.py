from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping


ACTIVE_PHASES = {"armed", "committed", "bound"}
TERMINAL_PHASES = {"invalidated", "completed", "error"}
ALLOWED_ACTIONS = {"arm", "commit", "bind", "invalidate", "complete"}


class SubmissionError(RuntimeError):
    pass


class SubmissionValidationError(SubmissionError):
    pass


class SubmissionConflict(SubmissionError):
    pass


class SubmissionNotFound(SubmissionError):
    pass


def _text(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise SubmissionValidationError(f"{name} is required")
    if len(result) > 256:
        raise SubmissionValidationError(f"{name} is too long")
    return result


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SubmissionValidationError(f"{name} must be an integer") from exc
    if result < minimum:
        raise SubmissionValidationError(f"{name} must be >= {minimum}")
    return result


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ConversationSubmissionStore:
    """Durable, fail-closed lifecycle for one microphone submission at a time.

    Prompt text is never persisted. The record contains only IDs, lengths, hashes,
    DOM baselines, timestamps and state transition fingerprints.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        expiry_seconds: float = 30.0,
        clock: Any = time.time,
    ) -> None:
        self.path = Path(path)
        self.expiry_seconds = max(1.0, float(expiry_seconds))
        self._clock = clock
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"version": 1, "current": None, "updatedAt": 0.0}
        self._load_and_invalidate_after_restart()

    def _load_and_invalidate_after_restart(self) -> None:
        with self._lock:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                self._state = loaded
            current = self._state.get("current")
            if isinstance(current, dict) and str(current.get("phase") or "") in ACTIVE_PHASES:
                current = dict(current)
                current["phase"] = "invalidated"
                current["invalidatedReason"] = "process_restart"
                current["invalidatedAt"] = float(self._clock())
                current["updatedAt"] = float(self._clock())
                self._state["current"] = current
                self._persist_locked()

    def _persist_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state["version"] = 1
        self._state["updatedAt"] = float(self._clock())
        temporary = self.path.with_name(f".{self.path.name}.{threading.get_ident()}.tmp")
        try:
            temporary.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _public(record: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            return {}
        allowed = {
            "phase",
            "sessionId",
            "turnId",
            "submissionId",
            "tabId",
            "pageInstanceId",
            "conversationKey",
            "cancelEpoch",
            "assistantBaselineKey",
            "assistantCountBefore",
            "textLength",
            "textHash",
            "assistantMessageKey",
            "armedAt",
            "committedAt",
            "boundAt",
            "completedAt",
            "invalidatedAt",
            "invalidatedReason",
            "expiresAt",
            "updatedAt",
        }
        return {key: value for key, value in record.items() if key in allowed}

    def _expire_locked(self) -> bool:
        record = self._state.get("current")
        if not isinstance(record, dict) or str(record.get("phase") or "") != "armed":
            return False
        expires_at = float(record.get("expiresAt") or 0.0)
        if expires_at <= 0 or float(self._clock()) < expires_at:
            return False
        record = dict(record)
        record["phase"] = "invalidated"
        record["invalidatedReason"] = "arm_expired"
        record["invalidatedAt"] = float(self._clock())
        record["updatedAt"] = float(self._clock())
        self._state["current"] = record
        self._persist_locked()
        return True

    def expire(self) -> bool:
        with self._lock:
            return self._expire_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            return {
                "phase": str((self._state.get("current") or {}).get("phase") or "idle"),
                "current": self._public(self._state.get("current")),
                "updatedAt": float(self._state.get("updatedAt") or 0.0),
            }

    @staticmethod
    def _identity(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sessionId": _text(payload.get("sessionId"), "sessionId"),
            "turnId": _text(payload.get("turnId"), "turnId"),
            "submissionId": _text(payload.get("submissionId"), "submissionId"),
            "tabId": _integer(payload.get("tabId"), "tabId", minimum=0),
            "pageInstanceId": _text(payload.get("pageInstanceId"), "pageInstanceId"),
            "conversationKey": _text(payload.get("conversationKey"), "conversationKey"),
            "cancelEpoch": _integer(payload.get("cancelEpoch"), "cancelEpoch", minimum=0),
        }

    @staticmethod
    def _same_identity(record: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
        return all(record.get(key) == value for key, value in identity.items())

    def _idempotent_or_conflict(
        self,
        record: Mapping[str, Any],
        *,
        action: str,
        fingerprint: str,
    ) -> dict[str, Any] | None:
        previous = str(record.get(f"{action}Fingerprint") or "")
        if not previous:
            return None
        if previous != fingerprint:
            raise SubmissionConflict(f"{action} payload conflicts with the existing submission")
        return self._public(record)

    def apply(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in ALLOWED_ACTIONS:
            raise SubmissionValidationError(f"unsupported submission action: {normalized_action}")
        with self._lock:
            self._expire_locked()
            if normalized_action == "arm":
                return self._arm_locked(payload)
            record = self._state.get("current")
            if not isinstance(record, dict):
                raise SubmissionNotFound("no microphone submission is armed")
            identity = self._identity(payload)
            if not self._same_identity(record, identity):
                raise SubmissionConflict("submission identity does not match the current turn")
            if normalized_action == "commit":
                return self._commit_locked(record, payload)
            if normalized_action == "bind":
                return self._bind_locked(record, payload)
            if normalized_action == "invalidate":
                return self._invalidate_locked(record, payload)
            return self._complete_locked(record, payload)

    def _arm_locked(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity = self._identity(payload)
        arm_payload = {
            **identity,
            "assistantBaselineKey": str(payload.get("assistantBaselineKey") or ""),
            "assistantCountBefore": _integer(
                payload.get("assistantCountBefore", 0), "assistantCountBefore", minimum=0
            ),
            "textLength": _integer(payload.get("textLength"), "textLength", minimum=1),
            "textHash": _text(payload.get("textHash"), "textHash"),
        }
        fingerprint = _canonical_hash(arm_payload)
        existing = self._state.get("current")
        if isinstance(existing, dict) and existing.get("submissionId") == identity["submissionId"]:
            result = self._idempotent_or_conflict(existing, action="arm", fingerprint=fingerprint)
            if result is not None:
                return result
        if isinstance(existing, dict) and str(existing.get("phase") or "") in ACTIVE_PHASES:
            raise SubmissionConflict("another microphone submission is already active")
        now = float(self._clock())
        record = {
            **arm_payload,
            "phase": "armed",
            "armedAt": now,
            "updatedAt": now,
            "expiresAt": now + self.expiry_seconds,
            "armFingerprint": fingerprint,
        }
        self._state["current"] = record
        self._persist_locked()
        return self._public(record)

    def _commit_locked(self, record: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        commit_payload = {
            "identity": self._identity(payload),
            "clickCommitted": bool(payload.get("clickCommitted", True)),
        }
        fingerprint = _canonical_hash(commit_payload)
        result = self._idempotent_or_conflict(record, action="commit", fingerprint=fingerprint)
        if result is not None:
            return result
        if str(record.get("phase") or "") != "armed":
            raise SubmissionConflict(f"cannot commit submission from phase {record.get('phase')}")
        if not commit_payload["clickCommitted"]:
            raise SubmissionValidationError("clickCommitted must be true")
        now = float(self._clock())
        updated = dict(record)
        updated.update(
            {
                "phase": "committed",
                "committedAt": now,
                "updatedAt": now,
                "commitFingerprint": fingerprint,
            }
        )
        self._state["current"] = updated
        self._persist_locked()
        return self._public(updated)

    def _bind_locked(self, record: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        assistant_message_key = _text(payload.get("assistantMessageKey"), "assistantMessageKey")
        candidate_count = _integer(payload.get("candidateCount", 1), "candidateCount", minimum=0)
        bind_payload = {
            "identity": self._identity(payload),
            "assistantMessageKey": assistant_message_key,
            "candidateCount": candidate_count,
        }
        fingerprint = _canonical_hash(bind_payload)
        result = self._idempotent_or_conflict(record, action="bind", fingerprint=fingerprint)
        if result is not None:
            return result
        if str(record.get("phase") or "") != "committed":
            raise SubmissionConflict(f"cannot bind submission from phase {record.get('phase')}")
        if candidate_count != 1:
            raise SubmissionConflict("assistant reply binding is ambiguous")
        now = float(self._clock())
        updated = dict(record)
        updated.update(
            {
                "phase": "bound",
                "assistantMessageKey": assistant_message_key,
                "boundAt": now,
                "updatedAt": now,
                "bindFingerprint": fingerprint,
            }
        )
        self._state["current"] = updated
        self._persist_locked()
        return self._public(updated)

    def _invalidate_locked(self, record: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        reason = _text(payload.get("reason") or "invalidated", "reason")
        invalidate_payload = {"identity": self._identity(payload), "reason": reason}
        fingerprint = _canonical_hash(invalidate_payload)
        result = self._idempotent_or_conflict(record, action="invalidate", fingerprint=fingerprint)
        if result is not None:
            return result
        now = float(self._clock())
        updated = dict(record)
        updated.update(
            {
                "phase": "invalidated",
                "invalidatedReason": reason,
                "invalidatedAt": now,
                "updatedAt": now,
                "invalidateFingerprint": fingerprint,
            }
        )
        self._state["current"] = updated
        self._persist_locked()
        return self._public(updated)

    def _complete_locked(self, record: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        complete_payload = {"identity": self._identity(payload)}
        fingerprint = _canonical_hash(complete_payload)
        result = self._idempotent_or_conflict(record, action="complete", fingerprint=fingerprint)
        if result is not None:
            return result
        if str(record.get("phase") or "") != "bound":
            raise SubmissionConflict(f"cannot complete submission from phase {record.get('phase')}")
        now = float(self._clock())
        updated = dict(record)
        updated.update(
            {
                "phase": "completed",
                "completedAt": now,
                "updatedAt": now,
                "completeFingerprint": fingerprint,
            }
        )
        self._state["current"] = updated
        self._persist_locked()
        return self._public(updated)

    def require_bound(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        identity = self._identity(payload)
        with self._lock:
            self._expire_locked()
            record = self._state.get("current")
            if not isinstance(record, dict) or str(record.get("phase") or "") != "bound":
                raise SubmissionConflict("microphone submission is not bound to one assistant message")
            if not self._same_identity(record, identity):
                raise SubmissionConflict("submission identity does not match the bound assistant reply")
            assistant_message_key = _text(payload.get("assistantMessageKey"), "assistantMessageKey")
            if assistant_message_key != str(record.get("assistantMessageKey") or ""):
                raise SubmissionConflict("assistantMessageKey does not match the bound reply")
            return dict(record)
