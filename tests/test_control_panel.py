from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from control_panel import LocalVoiceControlPanel, clamp_window_position  # noqa: E402


class FakeControlClient:
    def __init__(self) -> None:
        self.settings_calls: list[dict[str, object]] = []
        self.commands: list[str] = []

    def get_snapshot(self) -> dict[str, object]:
        return {
            "ok": True,
            "initialized": True,
            "settings": {
                "enabled": True,
                "voiceVolume": 0.4,
                "referenceVoice": "asuka",
                "micConversationEnabled": True,
                "sttModel": "medium",
                "cancelGraceMs": 900,
            },
            "referenceVoices": [
                {"id": "", "label": "none"},
                {"id": "asuka", "label": "asuka"},
            ],
            "conversation": {
                "phase": "recording",
                "statusText": "録音中",
                "sttDevice": "cuda",
                "sttModel": "medium",
                "error": "",
            },
            "components": {"sttInstalled": True},
            "extension": {
                "connected": True,
                "statusText": "Playing chunk 1/2",
                "statusLevel": "info",
                "currentText": "全タブから届いた返答です。",
                "queueSize": 2,
                "isPlaying": True,
                "playbackPhase": "playing",
                "replayAvailable": True,
                "tabsCount": 3,
            },
        }

    def update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        self.settings_calls.append(dict(payload))
        return {"ok": True}

    def send_command(self, command: str) -> dict[str, object]:
        self.commands.append(command)
        return {"ok": True}


class ControlPanelQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_snapshot_populates_external_panel_controls_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            panel = LocalVoiceControlPanel(
                client,
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=False,
            )

            panel.refresh_now()
            panel.show()
            self.app.processEvents()

            self.assertEqual(panel.reference_combo.currentData(), "asuka")
            self.assertEqual(panel.volume_slider.value(), 40)
            self.assertTrue(panel.auto_button.isChecked())
            self.assertTrue(panel.mic_button.isChecked())
            self.assertFalse(hasattr(panel, "stt_model_combo"))
            self.assertFalse(hasattr(panel, "cancel_grace_spin"))
            self.assertIn("録音中", panel.status_label.text())
            self.assertFalse(hasattr(panel, "mic_detail_label"))
            self.assertIn("マイク会話", panel.mic_button.text())
            self.assertNotIn("\n", panel.mic_button.text())
            self.assertEqual(panel.mic_button.minimumHeight(), 30)
            self.assertIn("右Ctrl＋＼", panel.mic_button.text())
            self.assertIn("STT medium", panel.mic_button.toolTip())
            self.assertIn("CUDA", panel.mic_button.toolTip())
            self.assertLessEqual(
                panel.mic_button.fontMetrics().horizontalAdvance(panel.mic_button.text()),
                panel.mic_button.width() - 16,
            )
            self.assertEqual(panel.current_text_label.toolTip(), "全タブから届いた返答です。")
            self.assertTrue(panel.current_text_label.text().startswith("全タブから届い"))
            self.assertIn("Queue 2", panel.queue_label.text())
            self.assertIn("3 tabs", panel.queue_label.text())
            self.assertTrue(panel.replay_button.isEnabled())
            panel.shutdown()

    def test_disconnected_extension_requests_extension_reload_not_chatgpt_tab_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            original = client.get_snapshot

            def disconnected_snapshot() -> dict[str, object]:
                payload = original()
                extension = dict(payload["extension"])
                extension.update({"connected": False, "loadedVersion": "", "tabsCount": 0})
                payload["extension"] = extension
                return payload

            client.get_snapshot = disconnected_snapshot  # type: ignore[method-assign]
            panel = LocalVoiceControlPanel(
                client,
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=False,
            )
            panel.refresh_now()
            self.app.processEvents()

            self.assertEqual(panel.status_label.text(), "拡張機能を再読み込みしてください")
            self.assertIn("ChatGPTタブの再読み込みは不要", panel.current_text_label.toolTip())
            self.assertNotIn("Open or reload", panel.current_text_label.toolTip())
            panel.shutdown()

    def test_controls_send_settings_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            panel = LocalVoiceControlPanel(
                client,
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=False,
            )
            panel.refresh_now()
            self.app.processEvents()

            panel.auto_button.click()
            self.assertFalse(panel.mic_button.isChecked())
            panel.volume_slider.setValue(25)
            panel.reference_combo.setCurrentIndex(0)
            panel.next_button.click()
            panel.regen_button.click()
            panel.replay_button.click()
            self.app.processEvents()

            self.assertIn(
                {"enabled": False, "micConversationEnabled": False},
                client.settings_calls,
            )
            self.assertIn({"voiceVolume": 0.25}, client.settings_calls)
            self.assertIn({"referenceVoice": ""}, client.settings_calls)
            self.assertEqual(client.commands, ["next", "regen", "replay"])
            panel.shutdown()

    def test_missing_stt_component_disables_microphone_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            original = client.get_snapshot

            def snapshot_without_stt() -> dict[str, object]:
                payload = original()
                payload["components"] = {"sttInstalled": False}
                return payload

            client.get_snapshot = snapshot_without_stt  # type: ignore[method-assign]
            panel = LocalVoiceControlPanel(
                client,
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=False,
            )
            panel.refresh_now()
            self.app.processEvents()

            self.assertFalse(panel.mic_button.isEnabled())
            self.assertIn("追加セットアップ", panel.mic_button.text())
            self.assertIn("読み上げ + マイク会話", panel.mic_button.toolTip())
            panel.shutdown()

    def test_old_extension_version_shows_reload_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            original = client.get_snapshot

            def snapshot_with_old_extension() -> dict[str, object]:
                payload = original()
                extension = dict(payload["extension"])
                extension.update(
                    {
                        "loadedVersion": "0.1.0",
                        "expectedVersion": "0.2.0",
                        "updateRequired": True,
                    }
                )
                payload["extension"] = extension
                return payload

            client.get_snapshot = snapshot_with_old_extension  # type: ignore[method-assign]
            panel = LocalVoiceControlPanel(
                client,
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=False,
            )
            panel.refresh_now()
            self.app.processEvents()

            self.assertEqual(panel.status_label.text(), "拡張機能の再読み込みが必要")
            self.assertIn("0.1.0 → 0.2.0", panel.current_text_label.toolTip())
            panel.shutdown()

    def test_toggle_visibility_and_close_hide_the_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = LocalVoiceControlPanel(
                FakeControlClient(),
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=False,
            )
            self.assertFalse(panel.isVisible())

            panel.toggle_visibility()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())

            panel.close()
            self.app.processEvents()
            self.assertFalse(panel.isVisible())

            panel.toggle_visibility()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())
            panel.shutdown()

    def test_polling_runs_only_while_the_panel_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = LocalVoiceControlPanel(
                FakeControlClient(),
                state_path=Path(temp_dir) / "panel-window.json",
                start_polling=True,
            )

            self.assertFalse(panel.isVisible())
            self.assertFalse(panel.refresh_timer.isActive())

            panel.show_panel()
            self.app.processEvents()
            self.assertTrue(panel.refresh_timer.isActive())

            panel.hide_panel()
            self.app.processEvents()
            self.assertFalse(panel.refresh_timer.isActive())
            panel.shutdown()


    def test_show_panel_recovers_a_saved_position_outside_current_screens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "panel-window.json"
            state_path.write_text('{"version": 1, "x": -618, "y": 54}\n', encoding="utf-8")
            panel = LocalVoiceControlPanel(
                FakeControlClient(),
                state_path=state_path,
                start_polling=False,
            )

            panel.show_panel()
            self.app.processEvents()

            available = self.app.primaryScreen().availableGeometry()
            self.assertTrue(available.contains(panel.frameGeometry()))
            self.assertEqual(panel.state_store.load_position(), panel.pos())
            panel.shutdown()


class ClampWindowPositionTests(unittest.TestCase):
    def test_valid_position_is_unchanged(self) -> None:
        position = QPoint(200, 100)
        self.assertEqual(
            clamp_window_position(position, QSize(320, 280), [QRect(0, 0, 1920, 1032)]),
            position,
        )

    def test_position_on_disconnected_left_monitor_is_clamped_to_primary(self) -> None:
        self.assertEqual(
            clamp_window_position(QPoint(-618, 54), QSize(320, 280), [QRect(0, 0, 1920, 1032)]),
            QPoint(8, 54),
        )


if __name__ == "__main__":
    unittest.main()
