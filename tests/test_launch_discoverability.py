from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

import tray_controller as tray  # noqa: E402
import windows_integration as windows  # noqa: E402
from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402
from tests.test_tray_qt_runtime import (  # noqa: E402
    FakeControlClient,
    FakeController,
    FakeConversationController,
    FakeKeyboardHook,
    TrayQtRuntimeTests,
)


class LaunchDiscoverabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = tray.create_qt_application([])

    def _create_runtime(self, temp_dir: str) -> tray.VoiceBridgeQtRuntime:
        runtime = tray.VoiceBridgeQtRuntime(
            self.app,
            controller=FakeController(),
            pet_root=TrayQtRuntimeTests._create_pet_root(temp_dir),
            settings_path=Path(temp_dir) / "settings.json",
            control_panel_client=FakeControlClient(),
            panel_state_path=Path(temp_dir) / "panel-window.json",
            conversation_controller=FakeConversationController(),
            keyboard_hook=FakeKeyboardHook(),
            start_panel_polling=False,
            start_monitor=False,
            show_tray=False,
        )
        runtime.app = SimpleNamespace(quit=lambda: None)
        return runtime

    def test_windows_startup_launches_in_background_mode(self) -> None:
        launcher = Path(r"C:\Voice Bridge\LocalVoiceBridge.exe")
        self.assertEqual(windows.startup_command(launcher), f'"{launcher}" --background')

    def test_manual_duplicate_launch_requests_existing_panel_activation(self) -> None:
        with (
            mock.patch.object(tray, "IS_WINDOWS", True),
            mock.patch.object(tray, "configure_logging"),
            mock.patch.object(tray, "migrate_legacy_startup"),
            mock.patch.object(tray, "acquire_single_instance", return_value=False),
            mock.patch.object(tray, "request_existing_instance_activation", create=True) as request_activation,
            mock.patch.object(sys, "argv", ["tray_controller.py"]),
        ):
            self.assertEqual(tray.main(), 0)

        request_activation.assert_called_once_with()

    def test_runtime_accepts_explicit_show_panel_on_start_policy(self) -> None:
        parameters = inspect.signature(tray.VoiceBridgeQtRuntime.__init__).parameters
        self.assertIn("show_panel_on_start", parameters)

    def test_tray_double_click_opens_the_windows_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._create_runtime(temp_dir)
            self.app.processEvents()
            self.assertFalse(runtime.control_panel.isVisible())

            runtime.tray_icon.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
            self.app.processEvents()

            self.assertTrue(runtime.control_panel.isVisible())
            runtime.shutdown()

    def test_tray_menu_uses_the_same_japanese_product_language_as_the_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._create_runtime(temp_dir)
            action_texts = [action.text() for action in runtime.menu.actions()]

            self.assertIn("小窓を表示", action_texts)
            self.assertIn("デスクトップペットを戻す", action_texts)
            self.assertIn("Local Voice Bridge を再起動", action_texts)
            self.assertIn("Windows起動時に開始", action_texts)
            self.assertIn("終了して環境を修復", action_texts)
            self.assertIn("終了", action_texts)
            runtime.shutdown()

    def test_native_launcher_forwards_background_mode_to_python_tray(self) -> None:
        source = (ROOT / "scripts" / "launcher" / "VoiceBridgeLauncher.cs").read_text(encoding="utf-8")
        self.assertIn('bool background = HasArgument(args, "--background")', source)
        self.assertIn('Arguments = Quote(controller) + (background ? " --background" : "")', source)


if __name__ == "__main__":
    unittest.main()
