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

from control_panel import LocalVoiceControlPanel  # noqa: E402
from control_state import ControlStateStore  # noqa: E402
from state_normalization import normalize_settings  # noqa: E402


class FakeControlClient:
    def __init__(self, snapshot: dict[str, object] | None = None) -> None:
        self.settings_calls: list[dict[str, object]] = []
        self.commands: list[str] = []
        self._snapshot = snapshot or self.ready_snapshot()

    @staticmethod
    def ready_snapshot() -> dict[str, object]:
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
                "liveTtsProfile": "bridge",
            },
            "referenceVoices": [
                {"id": "", "label": "none"},
                {"id": "asuka", "label": "asuka"},
            ],
            "conversation": {
                "phase": "idle",
                "statusText": "待機中",
                "sttDevice": "cuda",
                "sttModel": "medium",
                "error": "",
            },
            "components": {"sttInstalled": True},
            "extension": {
                "connected": True,
                "statusText": "Ready",
                "statusLevel": "info",
                "currentText": "直近の返答です。",
                "queueSize": 1,
                "isPlaying": False,
                "playbackPhase": "idle",
                "replayAvailable": True,
                "tabsCount": 3,
                "autoScopeTabs": 3,
                "manualTargetTabId": 22,
                "manualTargetTitle": "Tab B",
                "playbackSourceTabId": 11,
                "playbackSourceTitle": "Tab A",
                "updateRequired": False,
                "supportsExtensionReload": True,
            },
        }

    def get_snapshot(self) -> dict[str, object]:
        return self._snapshot

    def update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        self.settings_calls.append(dict(payload))
        return {"ok": True}

    def send_command(self, command: str) -> dict[str, object]:
        self.commands.append(command)
        return {"ok": True}


class UxResponsibilityFinalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def create_panel(self, client: FakeControlClient, temp_dir: str) -> LocalVoiceControlPanel:
        panel = LocalVoiceControlPanel(
            client,
            state_path=Path(temp_dir) / "panel-window.json",
            start_polling=False,
            async_requests=False,
        )
        panel.refresh_now()
        self.app.processEvents()
        return panel

    def test_auto_toggle_does_not_mutate_microphone_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            panel = self.create_panel(client, temp_dir)

            self.assertTrue(panel.auto_button.isChecked())
            self.assertTrue(panel.mic_button.isChecked())
            panel.auto_button.click()
            self.app.processEvents()

            self.assertFalse(panel.auto_button.isChecked())
            self.assertTrue(panel.mic_button.isChecked())
            self.assertEqual(client.settings_calls[-1], {"enabled": False})
            panel.shutdown()

    def test_microphone_toggle_does_not_mutate_auto_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = FakeControlClient.ready_snapshot()
            settings = dict(snapshot["settings"])
            settings.update({"enabled": False, "micConversationEnabled": False})
            snapshot["settings"] = settings
            client = FakeControlClient(snapshot)
            panel = self.create_panel(client, temp_dir)

            self.assertFalse(panel.auto_button.isChecked())
            self.assertFalse(panel.mic_button.isChecked())
            panel.mic_button.click()
            self.app.processEvents()

            self.assertFalse(panel.auto_button.isChecked())
            self.assertTrue(panel.mic_button.isChecked())
            self.assertEqual(client.settings_calls[-1], {"micConversationEnabled": True})
            panel.shutdown()

    def test_disconnected_extension_waits_without_self_reload_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = FakeControlClient.ready_snapshot()
            extension = dict(snapshot["extension"])
            extension.update({
                "connected": False,
                "statusText": "Waiting for ChatGPT",
                "loadedVersion": "",
                "updateRequired": False,
                "supportsExtensionReload": False,
            })
            snapshot["extension"] = extension
            panel = self.create_panel(FakeControlClient(snapshot), temp_dir)

            self.assertEqual(panel.status_label.text(), "ChatGPTとの接続待ち")
            self.assertIn("拡張機能の接続", panel.current_text_label.toolTip())
            self.assertTrue(panel.reload_extension_button.isHidden())
            panel.shutdown()

    def test_connected_update_required_offers_self_reload_only_when_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = FakeControlClient.ready_snapshot()
            extension = dict(snapshot["extension"])
            extension.update({
                "connected": True,
                "updateRequired": True,
                "supportsExtensionReload": True,
                "loadedVersion": "0.4.2",
                "expectedVersion": "0.4.3",
            })
            snapshot["extension"] = extension
            panel = self.create_panel(FakeControlClient(snapshot), temp_dir)

            self.assertEqual(panel.status_label.text(), "拡張機能の再読み込みが必要")
            self.assertFalse(panel.reload_extension_button.isHidden())
            self.assertTrue(panel.reload_extension_button.isEnabled())
            panel.shutdown()

    def test_runtime_failure_exposes_repair_action_without_script_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = FakeControlClient.ready_snapshot()
            snapshot["readiness"] = {
                "deviceOrModel": "failed",
                "repairRequired": True,
                "tabs": 3,
                "ready": False,
            }
            snapshot["voiceRuntime"] = {
                "readiness": "failed",
                "repairRequired": True,
                "error": "transformers is required",
                "queueSize": 0,
                "replayAvailable": False,
            }
            panel = self.create_panel(FakeControlClient(snapshot), temp_dir)
            emitted: list[bool] = []
            panel.repair_requested.connect(lambda: emitted.append(True))

            self.assertEqual(panel.status_label.text(), "音声ランタイムの修復が必要")
            self.assertTrue(hasattr(panel, "repair_button"))
            self.assertFalse(panel.repair_button.isHidden())
            self.assertEqual(panel.repair_button.text(), "環境を修復")
            self.assertNotIn("setup-voice-env.cmd", panel.current_text_label.toolTip())
            panel.repair_button.click()
            self.app.processEvents()
            self.assertEqual(emitted, [True])
            panel.shutdown()

    def test_multi_tab_scope_and_action_targets_are_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            panel = self.create_panel(FakeControlClient(), temp_dir)

            self.assertTrue(hasattr(panel, "context_label"))
            text = panel.context_label.text()
            self.assertIn("Auto: 全3タブ", text)
            self.assertIn("操作対象: Tab B", text)
            self.assertIn("再生元: Tab A", text)
            panel.shutdown()

    def test_live_tts_profile_is_normalized_and_persisted_by_local_api(self) -> None:
        self.assertEqual(normalize_settings({"liveTtsProfile": "bridge"})["liveTtsProfile"], "bridge")
        self.assertEqual(normalize_settings({"liveTtsProfile": "invalid"})["liveTtsProfile"], "speed")

        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "control-state.json"
            store = ControlStateStore(state_path)
            snapshot = store.update_settings({"liveTtsProfile": "balanced", "initialized": True})
            self.assertEqual(snapshot["settings"]["liveTtsProfile"], "balanced")

            restored = ControlStateStore(state_path)
            self.assertEqual(restored.snapshot()["settings"]["liveTtsProfile"], "balanced")

    def test_windows_advanced_settings_owns_runtime_fields_and_links_browser_preview(self) -> None:
        module_path = LOCAL_API / "advanced_settings_dialog.py"
        self.assertTrue(module_path.is_file(), "advanced_settings_dialog.py must provide the Windows-owned settings UI")
        from advanced_settings_dialog import AdvancedSettingsDialog  # noqa: E402

        client = FakeControlClient()
        browser_settings_calls: list[bool] = []
        dialog = AdvancedSettingsDialog(
            client,
            open_browser_settings=lambda: browser_settings_calls.append(True),
        )
        dialog.load_snapshot(client.get_snapshot())

        self.assertEqual(dialog.stt_model_combo.currentData(), "medium")
        self.assertEqual(dialog.cancel_grace_spin.value(), 0.9)
        self.assertEqual(dialog.live_profile_combo.currentData(), "bridge")
        dialog.stt_model_combo.setCurrentIndex(dialog.stt_model_combo.findData("large-v3-turbo"))
        dialog.cancel_grace_spin.setValue(1.4)
        dialog.live_profile_combo.setCurrentIndex(dialog.live_profile_combo.findData("balanced"))
        dialog.save_button.click()
        self.app.processEvents()
        self.assertEqual(
            client.settings_calls[-1],
            {
                "sttModel": "large-v3-turbo",
                "cancelGraceMs": 1400,
                "liveTtsProfile": "balanced",
            },
        )

        dialog.browser_settings_button.click()
        self.app.processEvents()
        self.assertEqual(browser_settings_calls, [True])
        dialog.close()


if __name__ == "__main__":
    unittest.main()