from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from control_panel_onboarding import FirstRunControlPanel, OnboardingStateStore  # noqa: E402


class FakeControlClient:
    def __init__(self) -> None:
        self.test_speech_calls: list[dict[str, object]] = []
        self.get_snapshot_calls = 0

    def get_snapshot(self) -> dict[str, object]:
        self.get_snapshot_calls += 1
        return self.snapshot(connected=False)

    def update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "settings": dict(payload)}

    def send_command(self, command: str) -> dict[str, object]:
        return {"ok": True, "command": command}

    def send_conversation_event(self, event_type: str, payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    def update_conversation_state(self, payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    def test_speech(self, *, reference_voice: str, voice_volume: float) -> dict[str, object]:
        self.test_speech_calls.append(
            {"referenceVoice": reference_voice, "voiceVolume": voice_volume}
        )
        return {"ok": True, "playedLocally": True, "playbackCompleted": True}

    @staticmethod
    def snapshot(*, connected: bool) -> dict[str, object]:
        return {
            "ok": True,
            "initialized": True,
            "settings": {
                "enabled": False,
                "voiceVolume": 0.6,
                "referenceVoice": "",
                "micConversationEnabled": False,
                "sttModel": "small",
                "cancelGraceMs": 700,
                "liveTtsProfile": "speed",
            },
            "referenceVoices": [{"id": "", "label": "none"}],
            "conversation": {"phase": "off", "statusText": "オフ"},
            "components": {"sttInstalled": False},
            "extension": {
                "connected": connected,
                "statusText": "Ready" if connected else "Waiting for ChatGPT",
                "queueSize": 0,
                "tabsCount": 1 if connected else 0,
                "autoScopeTabs": 1 if connected else 0,
                "updateRequired": False,
                "supportsExtensionReload": True,
            },
        }


class OnboardingAndSnapshotSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_onboarding_completion_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "control-panel-onboarding.json"
            store = OnboardingStateStore(path)
            self.assertFalse(store.is_complete())
            store.mark_complete()
            self.assertTrue(store.is_complete())
            self.assertTrue(OnboardingStateStore(path).is_complete())

    def test_first_run_waits_for_extension_then_uses_existing_speak_route_and_stays_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "panel-window.json"
            client = FakeControlClient()
            panel = FirstRunControlPanel(
                client,
                state_path=state_path,
                start_polling=False,
                async_requests=False,
            )

            self.assertTrue(panel.needs_onboarding())
            self.assertFalse(panel.onboarding_widget.isHidden())
            self.assertIn("拡張機能", panel.onboarding_widget.instructions_label.text())

            panel.apply_snapshot(client.snapshot(connected=False))
            self.assertFalse(panel.onboarding_widget.test_button.isEnabled())
            self.assertIn("接続", panel.onboarding_widget.connection_label.text())

            panel.apply_snapshot(client.snapshot(connected=True))
            self.assertTrue(panel.onboarding_widget.test_button.isEnabled())
            panel.onboarding_widget.test_button.click()
            self.app.processEvents()

            self.assertEqual(
                client.test_speech_calls,
                [{"referenceVoice": "", "voiceVolume": 0.6}],
            )
            self.assertFalse(panel.needs_onboarding())
            self.assertTrue(panel.onboarding_widget.isHidden())
            panel.shutdown()

            restored = FirstRunControlPanel(
                client,
                state_path=state_path,
                start_polling=False,
                async_requests=False,
            )
            self.assertFalse(restored.needs_onboarding())
            self.assertTrue(restored.onboarding_widget.isHidden())
            restored.shutdown()

    def test_hidden_panel_keeps_the_same_snapshot_pipeline_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "panel-window.json"
            client = FakeControlClient()
            panel = FirstRunControlPanel(
                client,
                state_path=state_path,
                start_polling=True,
                async_requests=False,
            )
            panel.onboarding_store.mark_complete()
            panel.onboarding_widget.hide()
            panel.hide_panel()

            self.assertFalse(panel.refresh_timer.isActive())
            self.assertTrue(panel.background_refresh_timer.isActive())
            before = client.get_snapshot_calls
            panel._refresh_while_hidden()
            self.assertEqual(client.get_snapshot_calls, before + 1)
            self.assertTrue(panel.background_refresh_timer.isActive())

            panel.show_panel()
            self.assertFalse(panel.background_refresh_timer.isActive())
            panel.shutdown()

    def test_tray_uses_panel_snapshot_signal_instead_of_fixed_500ms_sync_timers(self) -> None:
        source = (LOCAL_API / "tray_controller.py").read_text(encoding="utf-8")
        self.assertNotIn("conversation_settings_timer", source)
        self.assertNotIn("pet_settings_timer", source)
        self.assertNotIn("setInterval(500)", source)
        self.assertIn(
            "self.control_panel.snapshot_applied.connect(self.sync_runtime_from_snapshot)",
            source,
        )
        self.assertIn(
            "if show_panel_on_start or (show_tray and self.control_panel.needs_onboarding()):",
            source,
        )
        self.assertIn(
            "QTimer.singleShot(0, self.show_control_panel)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
