from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from conversation_turn import TurnIdentity  # noqa: E402
from live_conversation import (  # noqa: E402
    LiveBackpressure,
    LiveChunkConflict,
    LiveConversationService,
)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_payload() -> dict[str, object]:
    return {
        "sessionId": "session-1",
        "turnId": "turn-1",
        "submissionId": "submission-1",
        "tabId": 7,
        "pageInstanceId": "page-1",
        "conversationKey": "conversation-1",
        "cancelEpoch": 3,
    }


def arm_payload() -> dict[str, object]:
    return {
        **base_payload(),
        "assistantBaselineKey": "assistant-before",
        "assistantCountBefore": 2,
        "textLength": 10,
        "textHash": "prompt-hash",
    }


def chunk_payload(index: int, text: str, *, final: bool = False) -> dict[str, object]:
    return {
        **base_payload(),
        "assistantMessageKey": "assistant-3",
        "generationId": "generation-1",
        "chunkIndex": index,
        "text": text,
        "textHash": text_hash(text),
        "isFinal": final,
        "profile": "speed",
        "voiceVolume": 0.6,
    }


class FakeRuntime:
    def __init__(self) -> None:
        self.identity = TurnIdentity(turn_id="initial", cancel_epoch=0)
        self.jobs: list[SimpleNamespace] = []
        self.interrupts: list[tuple[str, int | None]] = []
        self.enqueued_payloads: list[dict[str, object]] = []

    def adopt_live_turn(self, payload):
        self.identity = TurnIdentity(
            turn_id=str(payload["turnId"]),
            cancel_epoch=int(payload["cancelEpoch"]),
            submission_id=str(payload["submissionId"]),
            tab_id=int(payload["tabId"]),
            page_instance_id=str(payload["pageInstanceId"]),
            conversation_key=str(payload["conversationKey"]),
        )
        return self.identity

    def bind_live_submission(self, payload):
        self.identity = TurnIdentity(
            turn_id=self.identity.turn_id,
            cancel_epoch=self.identity.cancel_epoch,
            submission_id=str(payload["submissionId"]),
            tab_id=self.identity.tab_id,
            page_instance_id=self.identity.page_instance_id,
            conversation_key=self.identity.conversation_key,
        )
        return self.identity

    def live_identity(self):
        return self.identity

    def enqueue_live(self, payload, *, text, volume, identity):
        job = SimpleNamespace(done=threading.Event(), error=None, result=None, identity=identity)
        self.jobs.append(job)
        self.enqueued_payloads.append({**payload, "text": text, "volume": volume})
        return job

    def interrupt(self, reason, *, requested_epoch=None):
        self.interrupts.append((reason, requested_epoch))
        if requested_epoch is None:
            requested_epoch = self.identity.cancel_epoch + 1
        self.identity = TurnIdentity(
            turn_id=self.identity.turn_id,
            cancel_epoch=max(self.identity.cancel_epoch, int(requested_epoch)),
            submission_id=self.identity.submission_id,
            tab_id=self.identity.tab_id,
            page_instance_id=self.identity.page_instance_id,
            conversation_key=self.identity.conversation_key,
        )
        return {"ok": True, "cancelEpoch": self.identity.cancel_epoch, "stopping": True}

    def snapshot(self):
        return {
            "generationPhase": "idle",
            "playbackPhase": "idle",
            "turnId": self.identity.turn_id,
            "cancelEpoch": self.identity.cancel_epoch,
        }


class LiveConversationServiceTests(unittest.TestCase):
    def make_service(self, temp_dir: str, *, max_pending: int = 2):
        runtime = FakeRuntime()
        service = LiveConversationService(
            runtime=runtime,
            state_path=Path(temp_dir) / "live.json",
            max_pending_chunks=max_pending,
        )
        service.apply_submission("arm", arm_payload())
        service.apply_submission("commit", {**base_payload(), "clickCommitted": True})
        service.apply_submission(
            "bind",
            {**base_payload(), "assistantMessageKey": "assistant-3", "candidateCount": 1},
        )
        return service, runtime

    def test_submission_lifecycle_adopts_and_binds_runtime_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)

            self.assertEqual(runtime.identity.turn_id, "turn-1")
            self.assertEqual(runtime.identity.cancel_epoch, 3)
            self.assertEqual(runtime.identity.submission_id, "submission-1")
            self.assertEqual(service.snapshot()["submission"]["phase"], "bound")

    def test_chunk_is_accepted_asynchronously_and_duplicate_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)
            payload = chunk_payload(0, "最初の文です。")

            accepted = service.enqueue_chunk(payload)
            duplicate = service.enqueue_chunk(payload)

            self.assertTrue(accepted["accepted"])
            self.assertFalse(accepted["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(len(runtime.jobs), 1)
            self.assertTrue(runtime.enqueued_payloads[0]["live"])
            self.assertEqual(runtime.enqueued_payloads[0]["ttsProfile"], "speed")

    def test_duplicate_chunk_can_be_upgraded_to_final_without_second_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)
            initial = chunk_payload(0, "最後の文です。", final=False)
            service.enqueue_chunk(initial)
            runtime.jobs[0].result = {"ok": True}
            runtime.jobs[0].done.set()
            service.snapshot()

            upgraded = service.enqueue_chunk({**initial, "isFinal": True})

            self.assertTrue(upgraded["duplicate"])
            self.assertEqual(len(runtime.jobs), 1)
            self.assertEqual(service.snapshot()["submission"]["phase"], "completed")

    def test_text_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, _runtime = self.make_service(temp_dir)
            payload = chunk_payload(0, "本文")
            payload["textHash"] = "wrong"

            with self.assertRaisesRegex(LiveChunkConflict, "textHash"):
                service.enqueue_chunk(payload)

    def test_backpressure_rejects_third_pending_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir, max_pending=2)
            service.enqueue_chunk(chunk_payload(0, "一文目です。"))
            service.enqueue_chunk(chunk_payload(1, "二文目です。"))

            with self.assertRaises(LiveBackpressure) as raised:
                service.enqueue_chunk(chunk_payload(2, "三文目です。"))

            self.assertEqual(raised.exception.retry_after_ms, 100)
            self.assertEqual(len(runtime.jobs), 2)

    def test_completed_job_frees_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir, max_pending=1)
            service.enqueue_chunk(chunk_payload(0, "一文目です。"))
            runtime.jobs[0].done.set()

            second = service.enqueue_chunk(chunk_payload(1, "二文目です。"))

            self.assertTrue(second["accepted"])
            self.assertEqual(len(runtime.jobs), 2)

    def test_final_job_completion_marks_submission_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)
            service.enqueue_chunk(chunk_payload(0, "最後の文です。", final=True))
            runtime.jobs[0].result = {"ok": True}
            runtime.jobs[0].done.set()

            snapshot = service.snapshot()

            self.assertEqual(snapshot["submission"]["phase"], "completed")
            self.assertEqual(snapshot["pendingChunks"], 0)

    def test_failed_job_invalidates_submission_and_interrupts_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)
            service.enqueue_chunk(chunk_payload(0, "失敗する文です。"))
            runtime.jobs[0].error = RuntimeError("quality rejected")
            runtime.jobs[0].done.set()

            snapshot = service.snapshot()

            self.assertEqual(snapshot["submission"]["phase"], "invalidated")
            self.assertIn("quality rejected", snapshot["lastError"])
            self.assertEqual(runtime.interrupts[-1][0], "live_chunk_failed")

    def test_stale_runtime_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)
            runtime.identity = TurnIdentity(
                turn_id="other",
                cancel_epoch=3,
                submission_id="submission-1",
                tab_id=7,
                page_instance_id="page-1",
                conversation_key="conversation-1",
            )

            with self.assertRaisesRegex(LiveChunkConflict, "stale"):
                service.enqueue_chunk(chunk_payload(0, "本文です。"))

    def test_interrupt_invalidates_submission_and_advances_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service, runtime = self.make_service(temp_dir)

            result = service.interrupt({**base_payload(), "reason": "input"})

            self.assertEqual(result["cancelEpoch"], 4)
            self.assertEqual(service.snapshot()["submission"]["phase"], "invalidated")
            self.assertEqual(runtime.interrupts[-1], ("input", 4))


if __name__ == "__main__":
    unittest.main()
