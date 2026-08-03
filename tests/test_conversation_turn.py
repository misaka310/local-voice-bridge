from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from conversation_turn import ConversationTurn  # noqa: E402


class ConversationTurnTests(unittest.TestCase):
    def test_new_turn_invalidates_previous_identity(self) -> None:
        state = ConversationTurn()
        first = state.begin_turn({"tabId": 7, "pageInstanceId": "page-a", "conversationKey": "conv-a"})
        second = state.begin_turn({"tabId": 7, "pageInstanceId": "page-a", "conversationKey": "conv-a"})

        self.assertNotEqual(first.turn_id, second.turn_id)
        self.assertFalse(state.is_current(first))
        self.assertTrue(state.is_current(second))

    def test_interrupt_is_idempotent_for_same_or_older_epoch(self) -> None:
        state = ConversationTurn()
        identity = state.begin_turn()
        first = state.interrupt("input", requested_epoch=identity.cancel_epoch + 1)
        repeated = state.interrupt("paste", requested_epoch=first.identity.cancel_epoch)
        older = state.interrupt("stop", requested_epoch=first.identity.cancel_epoch - 1)

        self.assertEqual(repeated, first)
        self.assertEqual(older, first)
        self.assertEqual(first.interrupt_reason, "input")

    def test_generation_and_playback_ids_are_independently_validated(self) -> None:
        state = ConversationTurn()
        state.begin_turn()
        generation = state.begin_generation()
        playback = state.begin_playback()

        self.assertTrue(state.is_current(generation, require_generation=True))
        self.assertTrue(state.is_current(playback, require_generation=True))
        self.assertTrue(state.is_current(playback, require_playback=True))

    def test_live_owner_requires_submission_page_and_conversation(self) -> None:
        state = ConversationTurn()
        state.begin_turn({"tabId": 3, "pageInstanceId": "page", "conversationKey": "conv"})
        bound = state.bind_submission("submission-1")

        self.assertTrue(state.validate_owner(bound, live=True))
        self.assertFalse(state.validate_owner(state.begin_turn({"tabId": 3}), live=True))


if __name__ == "__main__":
    unittest.main()
