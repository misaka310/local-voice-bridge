from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from control_state import ControlStateStore  # noqa: E402


class ControlStateStoreTests(unittest.TestCase):
    def test_settings_are_normalized_persisted_and_reloaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "control-panel-state.json"
            store = ControlStateStore(path)

            initial = store.snapshot(now=100.0)
            self.assertFalse(initial["initialized"])
            self.assertFalse(initial["settings"]["enabled"])
            self.assertEqual(initial["settings"]["referenceVoice"], "")
            self.assertFalse(initial["settings"]["referenceVoiceExplicit"])

            updated = store.update_settings(
                {
                    "enabled": True,
                    "voiceVolume": 1.7,
                    "referenceVoice": "  asuka  ",
                    "initialized": True,
                }
            )

            self.assertTrue(updated["initialized"])
            self.assertEqual(updated["settings"]["voiceVolume"], 1.0)
            self.assertEqual(updated["settings"]["referenceVoice"], "asuka")
            self.assertTrue(updated["settings"]["referenceVoiceExplicit"])

            reloaded = ControlStateStore(path).snapshot(now=100.0)
            self.assertTrue(reloaded["initialized"])
            self.assertTrue(reloaded["settings"]["enabled"])
            self.assertEqual(reloaded["settings"]["voiceVolume"], 1.0)
            self.assertEqual(reloaded["settings"]["referenceVoice"], "asuka")
            self.assertTrue(reloaded["settings"]["referenceVoiceExplicit"])

    def test_explicit_none_is_distinguished_from_a_legacy_empty_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            store = ControlStateStore(path)

            selected_none = store.update_settings({"referenceVoice": "", "initialized": True})

            self.assertEqual(selected_none["settings"]["referenceVoice"], "")
            self.assertTrue(selected_none["settings"]["referenceVoiceExplicit"])
            reloaded = ControlStateStore(path).snapshot()
            self.assertTrue(reloaded["settings"]["referenceVoiceExplicit"])

    def test_commands_are_monotonic_and_polling_does_not_replay_old_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")

            first = store.enqueue_command("next")
            second = store.enqueue_command("replay")

            self.assertEqual(first["id"], 1)
            self.assertEqual(second["id"], 2)
            self.assertEqual([item["command"] for item in store.poll_commands(0)], ["next", "replay"])
            self.assertEqual([item["command"] for item in store.poll_commands(1)], ["replay"])
            self.assertEqual(store.poll_commands(2), [])
            self.assertEqual([item["command"] for item in store.claim_commands(0)], ["next", "replay"])
            self.assertEqual(store.claim_commands(0), [])

    def test_extension_snapshot_becomes_disconnected_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json", stale_after_seconds=3.0)
            store.update_extension_state(
                {
                    "statusText": "Playing chunk 1/1",
                    "statusLevel": "info",
                    "currentText": "全タブの返答です。",
                    "queueSize": 2,
                    "isPlaying": True,
                    "playbackPhase": "playing",
                    "replayAvailable": True,
                    "tabsCount": 3,
                    "loadedVersion": "0.2.0",
                },
                now=10.0,
            )

            connected = store.snapshot(now=12.0)["extension"]
            self.assertTrue(connected["connected"])
            self.assertEqual(connected["currentText"], "全タブの返答です。")
            self.assertEqual(connected["tabsCount"], 3)
            self.assertEqual(connected["loadedVersion"], "0.2.0")

            stale = store.snapshot(now=14.1)["extension"]
            self.assertFalse(stale["connected"])
            self.assertEqual(stale["statusText"], "Waiting for ChatGPT")

    def test_invalid_commands_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            with self.assertRaises(ValueError):
                store.enqueue_command("delete-everything")

    def test_microphone_settings_are_normalized_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            store = ControlStateStore(path)
            updated = store.update_settings(
                {
                    "micConversationEnabled": True,
                    "sttModel": "  medium  ",
                    "cancelGraceMs": 99999,
                    "initialized": True,
                }
            )
            self.assertTrue(updated["settings"]["micConversationEnabled"])
            self.assertEqual(updated["settings"]["sttModel"], "medium")
            self.assertEqual(updated["settings"]["cancelGraceMs"], 5000)
            reloaded = ControlStateStore(path).snapshot()
            self.assertTrue(reloaded["settings"]["micConversationEnabled"])
            self.assertEqual(reloaded["settings"]["sttModel"], "medium")
            self.assertEqual(reloaded["settings"]["cancelGraceMs"], 5000)

    def test_conversation_events_are_claimed_once_and_transcripts_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            store = ControlStateStore(path)
            first = store.enqueue_conversation_event("cancel_pending", {"sessionId": 7})
            second = store.enqueue_conversation_event(
                "transcript",
                {"sessionId": 7, "text": "これは保存してはいけない会話本文です。", "cancelGraceMs": 700},
            )
            self.assertEqual(first["id"], 1)
            self.assertEqual(second["id"], 2)
            polled = store.poll(0)
            self.assertEqual([item["type"] for item in polled["conversationEvents"]], ["cancel_pending", "transcript"])
            self.assertEqual(store.poll(0)["conversationEvents"], [])
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn("これは保存してはいけない", persisted)
            self.assertEqual(ControlStateStore(path).poll(0)["conversationEvents"], [])

    def test_conversation_state_exposes_phase_without_conversation_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            snapshot = store.update_conversation_state(
                {
                    "phase": "transcribing",
                    "statusText": "文字起こし中",
                    "sttDevice": "cuda",
                    "sttModel": "small",
                    "error": "",
                }
            )
            self.assertEqual(snapshot["conversation"]["phase"], "transcribing")
            self.assertEqual(snapshot["conversation"]["sttDevice"], "cuda")
            self.assertNotIn("text", snapshot["conversation"])


if __name__ == "__main__":
    unittest.main()
