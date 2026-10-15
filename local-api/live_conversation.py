from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from conversation_submission import (
    ConversationSubmissionStore,
    SubmissionConflict,
    SubmissionError,
)
from runtime_events import NullRuntimeEventLogger
from tts_profiles import TtsProfileError, resolve_tts_profile
from voice_runtime import VoiceRuntime, VoiceRuntimeError


class LiveConversationError(RuntimeError):
    pass


class LiveChunkConflict(LiveConversationError):
    pass


class LiveBackpressure(LiveConversationError):
    def __init__(self, retry_after_ms: int = 100) -> None:
        self.retry_after_ms = max(25, int(retry_after_ms))
        super().__init__("live chunk queue is at the configured look-ahead limit")


def _required_text(payload: Mapping[str, Any], key: str, *, maximum: int = 4096) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise LiveConversationError(f"{key} is required")
    if len(value) > maximum:
        raise LiveConversationError(f"{key} is too long")
    return value


def _required_int(payload: Mapping[str, Any], key: str, *, minimum: int = 0) -> int:
    try:
        value = int(payload.get(key))
    except (TypeError, ValueError) as exc:
        raise LiveConversationError(f"{key} must be an integer") from exc
    if value < minimum:
        raise LiveConversationError(f"{key} must be >= {minimum}")
    return value


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LiveConversationService:
    """Coordinates durable submission binding with asynchronous Live TTS chunks."""

    def __init__(
        self,
        *,
        runtime: VoiceRuntime,
        state_path: Path | str,
        max_pending_chunks: int = 2,
        submission_store: ConversationSubmissionStore | None = None,
        event_logger: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.submissions = submission_store or ConversationSubmissionStore(state_path)
        self._events = event_logger or NullRuntimeEventLogger()
        self.max_pending_chunks = max(1, int(max_pending_chunks))
        self._lock = threading.RLock()
        self._jobs: dict[tuple[str, int, str], Any] = {}
        self._seen: dict[tuple[str, int, str], dict[str, Any]] = {}
        self._final_key: tuple[str, int, str] | None = None
        self._last_error = ""
        self._active_submission_id = ""

    def _emit(self, event: str, record: Mapping[str, Any] | None = None, **extra: Any) -> None:
        source = dict(record or {})
        fields = {
            "sessionId": source.get("sessionId"),
            "turnId": source.get("turnId"),
            "submissionId": source.get("submissionId"),
            "tabId": source.get("tabId"),
            "pageInstanceId": source.get("pageInstanceId"),
            "conversationKey": source.get("conversationKey"),
            "assistantMessageKey": source.get("assistantMessageKey"),
            "cancelEpoch": source.get("cancelEpoch"),
            **extra,
        }
        try:
            self._events.emit(event, **fields)
        except Exception:
            return

    @staticmethod
    def _owner(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "submissionId": payload.get("submissionId"),
            "tabId": payload.get("tabId"),
            "pageInstanceId": payload.get("pageInstanceId"),
            "conversationKey": payload.get("conversationKey"),
        }

    @staticmethod
    def _identity_payload(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sessionId": record.get("sessionId"),
            "turnId": record.get("turnId"),
            "submissionId": record.get("submissionId"),
            "tabId": record.get("tabId"),
            "pageInstanceId": record.get("pageInstanceId"),
            "conversationKey": record.get("conversationKey"),
            "cancelEpoch": record.get("cancelEpoch"),
        }

    def apply_submission(self, action: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        record = self.submissions.apply(normalized, payload)
        event_names = {
            "arm": "mic_submit_persisted",
            "commit": "chatgpt_submit_completed",
            "bind": "assistant_reply_bound",
            "invalidate": "assistant_reply_binding_rejected",
            "complete": "playback_completed",
        }
        self._emit(event_names.get(normalized, "submission_updated"), record, phase=record.get("phase"))
        with self._lock:
            if normalized == "arm":
                submission_id = str(record.get("submissionId") or "")
                if submission_id != self._active_submission_id:
                    self._jobs.clear()
                    self._seen.clear()
                    self._final_key = None
                    self._last_error = ""
                    self._active_submission_id = submission_id
                self.runtime.adopt_live_turn(dict(record))
            elif normalized == "bind":
                self.runtime.bind_live_submission(dict(record))
            elif normalized == "invalidate":
                self.runtime.interrupt(str(record.get("invalidatedReason") or "submission_invalidated"))
                self._jobs.clear()
                self._final_key = None
            elif normalized == "complete":
                self._jobs.clear()
                self._final_key = None
        return record

    def _prune_locked(self) -> None:
        finished: list[tuple[str, int, str]] = []
        failure: BaseException | None = None
        for key, job in self._jobs.items():
            if not job.done.is_set():
                continue
            finished.append(key)
            if job.error is not None and failure is None:
                failure = job.error
        for key in finished:
            self._jobs.pop(key, None)
        if failure is not None:
            self._last_error = str(failure)
            current = self.submissions.snapshot().get("current") or {}
            if current and str(current.get("phase") or "") == "bound":
                try:
                    self.submissions.apply(
                        "invalidate",
                        {
                            **self._identity_payload(current),
                            "reason": "live_chunk_failed",
                        },
                    )
                except SubmissionError:
                    pass
            self.runtime.interrupt("live_chunk_failed")
            self._jobs.clear()
            self._final_key = None
            return
        if self._final_key is not None and self._final_key not in self._jobs:
            current = self.submissions.snapshot().get("current") or {}
            if current and str(current.get("phase") or "") == "bound":
                try:
                    self.submissions.apply("complete", self._identity_payload(current))
                except SubmissionError:
                    return
            self._final_key = None

    def enqueue_chunk(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        record = self.submissions.require_bound(payload)
        text = _required_text(payload, "text", maximum=1600)
        supplied_hash = _required_text(payload, "textHash", maximum=128).lower()
        actual_hash = _sha256_text(text)
        if supplied_hash != actual_hash:
            raise LiveChunkConflict("textHash does not match text")
        generation_id = _required_text(payload, "generationId")
        chunk_index = _required_int(payload, "chunkIndex", minimum=0)
        assistant_message_key = _required_text(payload, "assistantMessageKey")
        if assistant_message_key != str(record.get("assistantMessageKey") or ""):
            raise LiveChunkConflict("assistantMessageKey does not match the bound reply")
        try:
            profile = resolve_tts_profile(payload.get("profile") or payload.get("ttsProfile"), live=True)
        except TtsProfileError as exc:
            raise LiveConversationError(str(exc)) from exc
        is_final = bool(payload.get("isFinal"))
        key = (generation_id, chunk_index, actual_hash)
        with self._lock:
            self._prune_locked()
            existing = self._seen.get(key)
            if existing is not None:
                expected = {
                    "assistantMessageKey": assistant_message_key,
                    "textHash": actual_hash,
                    "profile": profile.name,
                }
                actual = {name: existing.get(name) for name in expected}
                if actual != expected:
                    raise LiveChunkConflict("duplicate live chunk key has conflicting content")
                if is_final and not bool(existing.get("isFinal")):
                    upgraded = dict(existing)
                    upgraded["isFinal"] = True
                    self._seen[key] = upgraded
                    if self._final_key is not None and self._final_key != key:
                        raise LiveChunkConflict("more than one final live chunk was submitted")
                    self._final_key = key
                    self._prune_locked()
                return {
                    "accepted": True,
                    "duplicate": True,
                    "generationId": generation_id,
                    "chunkIndex": chunk_index,
                    "pendingChunks": len(self._jobs),
                    "capacity": max(0, self.max_pending_chunks - len(self._jobs)),
                }
            if len(self._jobs) >= self.max_pending_chunks:
                raise LiveBackpressure()
            identity = self.runtime.live_identity()
            if (
                identity.turn_id != str(record.get("turnId") or "")
                or identity.cancel_epoch != int(record.get("cancelEpoch") or 0)
                or identity.submission_id != str(record.get("submissionId") or "")
                or identity.page_instance_id != str(record.get("pageInstanceId") or "")
                or identity.conversation_key != str(record.get("conversationKey") or "")
            ):
                raise LiveChunkConflict("runtime turn identity is stale")
            identity = replace(identity, generation_id=generation_id)
            runtime_payload = {
                **dict(payload),
                "text": text,
                "live": True,
                "ttsProfile": profile.name,
                "resolvedTtsProfile": profile.name,
            }
            try:
                volume = min(1.0, max(0.0, float(payload.get("voiceVolume", 0.6))))
            except (TypeError, ValueError):
                volume = 0.6
            try:
                job = self.runtime.enqueue_live(
                    runtime_payload,
                    text=text,
                    volume=volume,
                    identity=identity,
                )
            except VoiceRuntimeError as exc:
                raise LiveBackpressure() from exc
            self._jobs[key] = job
            self._emit(
                "assistant_chunk_stable",
                record,
                assistantMessageKey=assistant_message_key,
                generationId=generation_id,
                chunkIndex=chunk_index,
                textLength=len(text),
                textHash=actual_hash,
                ttsProfile=profile.name,
                capacity=max(0, self.max_pending_chunks - len(self._jobs)),
            )
            self._seen[key] = {
                "assistantMessageKey": assistant_message_key,
                "textHash": actual_hash,
                "profile": profile.name,
                "isFinal": is_final,
            }
            if is_final:
                if self._final_key is not None and self._final_key != key:
                    raise LiveChunkConflict("more than one final live chunk was submitted")
                self._final_key = key
            return {
                "accepted": True,
                "duplicate": False,
                "generationId": generation_id,
                "chunkIndex": chunk_index,
                "pendingChunks": len(self._jobs),
                "capacity": max(0, self.max_pending_chunks - len(self._jobs)),
                "profile": profile.name,
            }

    def interrupt(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        reason = str(payload.get("reason") or "interrupt").strip() or "interrupt"
        try:
            observed_epoch = int(payload.get("cancelEpoch", 0))
        except (TypeError, ValueError) as exc:
            raise LiveConversationError("cancelEpoch must be an integer") from exc
        current = self.submissions.snapshot().get("current") or {}
        if current and str(current.get("phase") or "") in {"armed", "committed", "bound"}:
            try:
                self.submissions.apply(
                    "invalidate",
                    {
                        **self._identity_payload(current),
                        "reason": reason,
                    },
                )
            except SubmissionConflict:
                pass
        result = self.runtime.interrupt(reason, requested_epoch=observed_epoch + 1)
        with self._lock:
            self._jobs.clear()
            self._final_key = None
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            runtime = self.runtime.snapshot()
            return {
                "ok": True,
                "submission": self.submissions.snapshot(),
                "conversationPhase": (
                    "responding"
                    if self.submissions.snapshot().get("phase") == "bound"
                    else "idle"
                    if self.submissions.snapshot().get("phase") in {"idle", "completed", "invalidated"}
                    else "waiting_for_assistant"
                ),
                "generationPhase": runtime.get("generationPhase", "idle"),
                "playbackPhase": runtime.get("playbackPhase", "idle"),
                "turnId": runtime.get("turnId", ""),
                "cancelEpoch": runtime.get("cancelEpoch", 0),
                "pendingChunks": len(self._jobs),
                "capacity": max(0, self.max_pending_chunks - len(self._jobs)),
                "maxPendingChunks": self.max_pending_chunks,
                "lastError": self._last_error,
                "voiceRuntime": runtime,
            }
