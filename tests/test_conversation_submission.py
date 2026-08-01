from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from conversation_submission import (  # noqa: E402
    ConversationSubmissionStore,
    SubmissionConflict,
    SubmissionValidationError,
)


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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
        "textLength": 18,
        "textHash": "abc123",
    }


class ConversationSubmissionStoreTests(unittest.TestCase):
    def make_store(self, temp_dir: str, clock: FakeClock | None = None) -> ConversationSubmissionStore:
        return ConversationSubmissionStore(
            Path(temp_dir) / "submission.json",
            expiry_seconds=30,
            clock=clock or FakeClock(),
        )

    def test_arm_commit_bind_complete_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            armed = store.apply("arm", arm_payload())
            committed = store.apply("commit", {**base_payload(), "clickCommitted": True})
            bound = store.apply(
                "bind",
                {**base_payload(), "assistantMessageKey": "assistant-3", "candidateCount": 1},
            )
            completed = store.apply("complete", base_payload())

            self.assertEqual(armed["phase"], "armed")
            self.assertEqual(committed["phase"], "committed")
            self.assertEqual(bound["phase"], "bound")
            self.assertEqual(bound["assistantMessageKey"], "assistant-3")
            self.assertEqual(completed["phase"], "completed")
            self.assertEqual(store.snapshot()["phase"], "completed")

    def test_same_payload_retries_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            first_arm = store.apply("arm", arm_payload())
            repeated_arm = store.apply("arm", arm_payload())
            commit_payload = {**base_payload(), "clickCommitted": True}
            first_commit = store.apply("commit", commit_payload)
            repeated_commit = store.apply("commit", commit_payload)

            self.assertEqual(first_arm, repeated_arm)
            self.assertEqual(first_commit, repeated_commit)

    def test_same_submission_id_with_different_payload_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            store.apply("arm", arm_payload())

            with self.assertRaisesRegex(SubmissionConflict, "arm payload conflicts"):
                store.apply("arm", {**arm_payload(), "textHash": "different"})

    def test_arm_expires_after_30_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = FakeClock()
            store = self.make_store(temp_dir, clock)
            store.apply("arm", arm_payload())
            clock.advance(31)

            snapshot = store.snapshot()

            self.assertEqual(snapshot["phase"], "invalidated")
            self.assertEqual(snapshot["current"]["invalidatedReason"], "arm_expired")
            with self.assertRaises(SubmissionConflict):
                store.apply("commit", {**base_payload(), "clickCommitted": True})

    def test_ambiguous_bind_is_rejected_and_does_not_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            store.apply("arm", arm_payload())
            store.apply("commit", {**base_payload(), "clickCommitted": True})

            with self.assertRaisesRegex(SubmissionConflict, "ambiguous"):
                store.apply(
                    "bind",
                    {**base_payload(), "assistantMessageKey": "assistant-3", "candidateCount": 2},
                )

            self.assertEqual(store.snapshot()["phase"], "committed")

    def test_bound_identity_must_match_live_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            store.apply("arm", arm_payload())
            store.apply("commit", {**base_payload(), "clickCommitted": True})
            store.apply(
                "bind",
                {**base_payload(), "assistantMessageKey": "assistant-3", "candidateCount": 1},
            )

            record = store.require_bound({**base_payload(), "assistantMessageKey": "assistant-3"})
            self.assertEqual(record["phase"], "bound")
            with self.assertRaises(SubmissionConflict):
                store.require_bound(
                    {
                        **base_payload(),
                        "assistantMessageKey": "assistant-other",
                    }
                )

    def test_restart_invalidates_active_submission_without_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submission.json"
            store = ConversationSubmissionStore(path, clock=FakeClock())
            store.apply("arm", arm_payload())

            reloaded = ConversationSubmissionStore(path, clock=FakeClock(1001.0))
            state = reloaded.snapshot()
            persisted = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(state["phase"], "invalidated")
            self.assertEqual(state["current"]["invalidatedReason"], "process_restart")
            self.assertNotIn("text", persisted["current"])
            self.assertIn("textHash", persisted["current"])

    def test_prompt_text_is_never_persisted_or_returned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "submission.json"
            store = ConversationSubmissionStore(path, clock=FakeClock())
            payload = {**arm_payload(), "text": "秘密の本文"}

            result = store.apply("arm", payload)
            persisted = path.read_text(encoding="utf-8")

            self.assertNotIn("秘密の本文", persisted)
            self.assertNotIn("text", result)
            self.assertEqual(result["textLength"], 18)

    def test_invalid_identity_and_false_commit_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.make_store(temp_dir)
            with self.assertRaises(SubmissionValidationError):
                store.apply("arm", {**arm_payload(), "submissionId": ""})
            store.apply("arm", arm_payload())
            with self.assertRaisesRegex(SubmissionValidationError, "clickCommitted"):
                store.apply("commit", {**base_payload(), "clickCommitted": False})


if __name__ == "__main__":
    unittest.main()
