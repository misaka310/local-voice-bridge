from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class TurnIdentity:
    turn_id: str
    cancel_epoch: int
    generation_id: str = ""
    playback_id: str = ""
    submission_id: str = ""
    tab_id: int | None = None
    page_instance_id: str = ""
    conversation_key: str = ""


@dataclass(frozen=True)
class TurnSnapshot:
    identity: TurnIdentity
    interrupt_reason: str = ""
    interrupted: bool = False


class ConversationTurn:
    """Thread-safe source of truth for turn identity and logical cancellation.

    The class deliberately contains no I/O. Callers attach the returned identity to
    asynchronous work and must validate it again before publishing any result.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = TurnSnapshot(identity=TurnIdentity(turn_id=self._new_id("turn"), cancel_epoch=0))

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def _normalized_owner(owner: Mapping[str, Any] | None) -> dict[str, Any]:
        source = dict(owner or {})
        tab_value = source.get("tabId")
        try:
            tab_id = int(tab_value) if tab_value is not None else None
        except (TypeError, ValueError):
            tab_id = None
        return {
            "submission_id": str(source.get("submissionId") or ""),
            "tab_id": tab_id,
            "page_instance_id": str(source.get("pageInstanceId") or ""),
            "conversation_key": str(source.get("conversationKey") or ""),
        }

    def snapshot(self) -> TurnSnapshot:
        with self._lock:
            return self._snapshot

    def begin_turn(self, owner: Mapping[str, Any] | None = None) -> TurnIdentity:
        normalized = self._normalized_owner(owner)
        with self._lock:
            identity = TurnIdentity(
                turn_id=self._new_id("turn"),
                cancel_epoch=self._snapshot.identity.cancel_epoch + 1,
                **normalized,
            )
            self._snapshot = TurnSnapshot(identity=identity)
            return identity

    def adopt_turn(
        self,
        turn_id: str,
        cancel_epoch: int,
        owner: Mapping[str, Any] | None = None,
    ) -> TurnIdentity:
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            raise ValueError("turn_id is required")
        try:
            normalized_epoch = int(cancel_epoch)
        except (TypeError, ValueError) as exc:
            raise ValueError("cancel_epoch must be an integer") from exc
        if normalized_epoch < 0:
            raise ValueError("cancel_epoch must be non-negative")
        normalized = self._normalized_owner(owner)
        with self._lock:
            current = self._snapshot.identity
            if normalized_epoch < current.cancel_epoch:
                raise ValueError("cancel_epoch is stale")
            if (
                normalized_turn_id == current.turn_id
                and normalized_epoch == current.cancel_epoch
            ):
                identity = replace(
                    current,
                    submission_id=normalized["submission_id"] or current.submission_id,
                    tab_id=normalized["tab_id"] if normalized["tab_id"] is not None else current.tab_id,
                    page_instance_id=normalized["page_instance_id"] or current.page_instance_id,
                    conversation_key=normalized["conversation_key"] or current.conversation_key,
                )
            else:
                identity = TurnIdentity(
                    turn_id=normalized_turn_id,
                    cancel_epoch=normalized_epoch,
                    **normalized,
                )
            self._snapshot = TurnSnapshot(identity=identity)
            return identity

    def bind_submission(self, submission_id: str, *, owner: Mapping[str, Any] | None = None) -> TurnIdentity:
        normalized = self._normalized_owner(owner)
        with self._lock:
            current = self._snapshot.identity
            identity = replace(
                current,
                submission_id=str(submission_id or normalized["submission_id"] or ""),
                tab_id=normalized["tab_id"] if normalized["tab_id"] is not None else current.tab_id,
                page_instance_id=normalized["page_instance_id"] or current.page_instance_id,
                conversation_key=normalized["conversation_key"] or current.conversation_key,
            )
            self._snapshot = replace(self._snapshot, identity=identity)
            return identity

    def begin_generation(self) -> TurnIdentity:
        with self._lock:
            identity = replace(self._snapshot.identity, generation_id=self._new_id("generation"))
            self._snapshot = replace(self._snapshot, identity=identity)
            return identity

    def begin_playback(self) -> TurnIdentity:
        with self._lock:
            identity = replace(self._snapshot.identity, playback_id=self._new_id("playback"))
            self._snapshot = replace(self._snapshot, identity=identity)
            return identity

    def interrupt(self, reason: str, *, requested_epoch: int | None = None) -> TurnSnapshot:
        """Invalidate current asynchronous work.

        A caller may provide the epoch it already observed. Repeating the same or an
        older interrupt is idempotent; only a newer request can advance the epoch.
        """

        normalized_reason = str(reason or "interrupt")
        with self._lock:
            current = self._snapshot
            if requested_epoch is not None:
                try:
                    requested = int(requested_epoch)
                except (TypeError, ValueError):
                    requested = current.identity.cancel_epoch + 1
                if requested <= current.identity.cancel_epoch:
                    return current
                next_epoch = requested
            else:
                next_epoch = current.identity.cancel_epoch + 1
            identity = replace(
                current.identity,
                cancel_epoch=next_epoch,
                generation_id="",
                playback_id="",
            )
            self._snapshot = TurnSnapshot(
                identity=identity,
                interrupt_reason=normalized_reason,
                interrupted=True,
            )
            return self._snapshot

    def is_current(
        self,
        identity: TurnIdentity,
        *,
        require_generation: bool = False,
        require_playback: bool = False,
        require_submission: bool = False,
    ) -> bool:
        with self._lock:
            current = self._snapshot.identity
            if identity.turn_id != current.turn_id or identity.cancel_epoch != current.cancel_epoch:
                return False
            if require_generation and (not identity.generation_id or identity.generation_id != current.generation_id):
                return False
            if require_playback and (not identity.playback_id or identity.playback_id != current.playback_id):
                return False
            if require_submission and (not identity.submission_id or identity.submission_id != current.submission_id):
                return False
            if identity.tab_id is not None and current.tab_id is not None and identity.tab_id != current.tab_id:
                return False
            if identity.page_instance_id and identity.page_instance_id != current.page_instance_id:
                return False
            if identity.conversation_key and identity.conversation_key != current.conversation_key:
                return False
            return True

    def validate_owner(self, identity: TurnIdentity, *, live: bool = False) -> bool:
        if not self.is_current(identity, require_submission=live):
            return False
        if not live:
            return True
        return bool(identity.page_instance_id and identity.conversation_key)
