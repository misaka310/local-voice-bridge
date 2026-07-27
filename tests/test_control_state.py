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
    # Reference selection persistence is part of the runtime contract.
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

    def test_polling_is_non_destructive_until_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            store = ControlStateStore(path)
            first = store.enqueue_conversation_event("cancel_pending", {"sessionId": 7})
            second = store.enqueue_conversation_event(
                "transcript",
                {"sessionId": 7, "text": "ACKまで保持する会話本文です。", "cancelGraceMs": 700},
            )
            self.assertEqual(first["id"], 1)
            self.assertEqual(second["id"], 2)
            polled = store.poll(0, consumer_id="panel", replay_existing=True)
            self.assertEqual([item["type"] for item in polled["conversationEvents"]], ["cancel_pending", "transcript"])
            self.assertEqual(store.poll(0, consumer_id="panel")["conversationEvents"], polled["conversationEvents"])
            self.assertEqual(store.poll(0, consumer_id="panel")["commands"], [])
            persisted = path.read_text(encoding="utf-8")
            self.assertIn("ACKまで保持する会話本文", persisted)
            restarted = ControlStateStore(path)
            self.assertEqual(
                restarted.poll(0, consumer_id="panel", replay_existing=True)["conversationEvents"],
                polled["conversationEvents"],
            )
            restarted.acknowledge_conversation_events(second["id"], consumer_id="panel")
            self.assertEqual(restarted.poll(0, consumer_id="panel")["conversationEvents"], [])
            self.assertNotIn("ACKまで保持する会話本文", path.read_text(encoding="utf-8"))

    def test_poll_uses_independent_command_and_event_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            store.enqueue_command("next")
            command = store.enqueue_command("replay")
            event = store.enqueue_conversation_event("cancel_pending", {"sessionId": 1})

            polled = store.poll(
                command["id"],
                after_event_id=0,
                consumer_id="panel",
                replay_existing=True,
            )

            self.assertEqual(polled["commands"], [])
            self.assertEqual([item["id"] for item in polled["conversationEvents"]], [event["id"]])

    def test_command_ack_is_persisted_and_hides_items_for_that_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            store = ControlStateStore(path)
            command = store.enqueue_command("next")

            reloaded = ControlStateStore(path)
            self.assertEqual(
                [item["id"] for item in reloaded.poll(0, consumer_id="controller", replay_existing=True)["commands"]],
                [command["id"]],
            )
            reloaded.acknowledge_commands(command["id"], consumer_id="controller")

            after_ack_restart = ControlStateStore(path)
            self.assertEqual(after_ack_restart.poll(0, consumer_id="controller")["commands"], [])

    def test_consumers_isolate_acknowledgements_and_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            command = store.enqueue_command("replay")
            store.poll(0, consumer_id="first")
            store.poll(0, consumer_id="second", replay_existing=True)
            store.acknowledge_commands(command["id"], consumer_id="first")
            self.assertEqual([item["id"] for item in store.poll(0, consumer_id="second") ["commands"]], [command["id"]])
            store.acknowledge_commands(command["id"], consumer_id="second")
            self.assertEqual(store._commands, [])

    def test_unused_legacy_consumer_does_not_block_command_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            command = store.enqueue_command("next")
            store.poll(0, consumer_id="extension", replay_existing=True)

            store.acknowledge_commands(command["id"], consumer_id="extension")

            self.assertEqual(store._commands, [])

    def test_new_consumer_starts_at_tail_unless_replay_is_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            command = store.enqueue_command("stop")
            self.assertEqual(store.poll(0, consumer_id="new-client")["commands"], [])
            self.assertEqual(
                [item["id"] for item in store.poll(0, consumer_id="replay-client", replay_existing=True)["commands"]],
                [command["id"]],
            )

    def test_transcript_delivery_id_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            supplied = store.enqueue_conversation_event(
                "transcript", {"sessionId": 1, "text": "hello", "deliveryId": "client-event-1"}
            )
            generated = store.enqueue_conversation_event("transcript", {"sessionId": 1, "text": "hello again"})
            self.assertEqual(supplied["payload"]["deliveryId"], "client-event-1")
            self.assertTrue(generated["payload"]["deliveryId"])
            self.assertEqual(
                store.poll(0, consumer_id="replay", replay_existing=True)["conversationEvents"][0]["payload"]["deliveryId"],
                "client-event-1",
            )

    def test_outbox_overflow_raises_without_dropping_unacknowledged_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ControlStateStore(Path(temp_dir) / "state.json")
            for _ in range(256):
                store.enqueue_command("next")
            with self.assertRaisesRegex(RuntimeError, "outbox is full"):
                store.enqueue_command("next")
            self.assertEqual(len(store.poll(0, consumer_id="legacy")["commands"]), 256)

    def test_browser_runtime_state_persists_tabs_latest_response_and_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            store = ControlStateStore(path)
            saved = store.update_browser_runtime(
                {
                    "tabs": [
                        {
                            "id": 101,
                            "title": "Tab A",
                            "url": "https://chatgpt.com/c/101",
                            "lastReadIndex": 0,
                            "lastAutoQueueSignature": "reply-1\u0000preview",
                            "lastAssistantMessage": {
                                "messageKey": "reply-1",
                                "chunks": ["最初です。", "続きです。"],
                                "capturedAt": 10,
                            },
                        }
                    ],
                    "selectedTabId": 101,
                    "uiOwnerTabId": 101,
                    "lastComposerFocusedTabId": 101,
                    "activeConversationTargetTabId": 101,
                    "conversationSessions": [
                        {
                            "sessionId": 77,
                            "tabId": 101,
                            "location": "https://chatgpt.com/c/101",
                        }
                    ],
                    "queue": [
                        {
                            "id": "q-1700000000000-7",
                            "mode": "next",
                            "reason": "next",
                            "tabId": 101,
                            "tabTitle": "Tab A",
                            "messageKey": "reply-1",
                            "chunkIndex": 1,
                            "chunkCount": 2,
                            "text": "続きです。",
                            "voiceProfile": "irodori-v3",
                            "referenceVoice": "asuka",
                        }
                    ],
                    "seq": 8,
                },
                now=20,
            )

            self.assertEqual(saved["selectedTabId"], 101)
            self.assertEqual(saved["queue"][0]["text"], "続きです。")
            reloaded = ControlStateStore(path).browser_runtime_snapshot()
            self.assertEqual(reloaded["tabs"][0]["lastAssistantMessage"]["chunks"], ["最初です。", "続きです。"])
            self.assertEqual(reloaded["queue"][0]["referenceVoice"], "asuka")
            self.assertEqual(reloaded["queue"][0]["id"], "q-1700000000000-7")
            self.assertEqual(reloaded["lastComposerFocusedTabId"], 101)
            self.assertEqual(reloaded["activeConversationTargetTabId"], 101)
            self.assertEqual(
                reloaded["conversationSessions"],
                [{"sessionId": 77, "tabId": 101, "location": "https://chatgpt.com/c/101"}],
            )
            self.assertEqual(reloaded["seq"], 8)

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
