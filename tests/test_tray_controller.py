from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "local-api" / "tray_controller.py"
SPEC = importlib.util.spec_from_file_location("tray_controller", MODULE_PATH)
assert SPEC and SPEC.loader
tray = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tray)


class TrayControllerContractTests(unittest.TestCase):
    def test_expected_health_payload_is_accepted(self) -> None:
        self.assertTrue(
            tray.compatible_health_payload(
                {
                    "ok": True,
                    "runtime": "irodori_direct",
                    "defaultModel": "irodori-v3",
                }
            )
        )

    def test_wrong_or_incomplete_health_payload_is_rejected(self) -> None:
        self.assertFalse(tray.compatible_health_payload({"ok": True}))
        self.assertFalse(
            tray.compatible_health_payload(
                {
                    "ok": True,
                    "runtime": "another-service",
                    "defaultModel": "irodori-v3",
                }
            )
        )
        self.assertFalse(tray.compatible_health_payload(None))

    def test_server_is_started_directly_with_the_venv_python(self) -> None:
        command = tray.server_command()
        self.assertEqual(command[0], str(tray.SERVER_PYTHON))
        self.assertEqual(command[1], str(tray.SERVER_SCRIPT))
        self.assertNotIn("cmd.exe", " ".join(command).lower())
        self.assertNotIn(".bat", " ".join(command).lower())

    def test_preflight_keeps_the_existing_cuda_contract(self) -> None:
        command = tray.preflight_command()
        self.assertIn("--strict-cuda", command)
        self.assertIn("--quick", command)
        self.assertEqual(command[0], str(tray.SERVER_PYTHON))

    def test_startup_command_targets_the_small_exe_launcher(self) -> None:
        launcher = Path(r"C:\Voice Bridge\LocalVoiceBridge.exe")
        self.assertEqual(tray.startup_command(launcher), f'"{launcher}"')

    def test_startup_toggle_uses_the_current_user_run_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher = Path(temp_dir) / "LocalVoiceBridge.exe"
            launcher.write_bytes(b"launcher")
            legacy_entry = Path(temp_dir) / "ChatGPT Local Voice Bridge.vbs"
            legacy_entry.write_text("legacy", encoding="utf-8")
            with (
                mock.patch.object(tray, "LAUNCHER_EXE", launcher),
                mock.patch.object(tray, "legacy_startup_entry_paths", return_value=(legacy_entry,)),
                mock.patch.object(tray, "_write_startup_command") as write_startup,
                mock.patch.object(tray, "_delete_startup_command") as delete_startup,
            ):
                tray.set_startup_enabled(True)
                write_startup.assert_called_once_with(f'"{launcher}"')
                delete_startup.assert_called_once_with(tray.LEGACY_WINDOWS_RUN_VALUE)
                self.assertFalse(legacy_entry.exists())

                delete_startup.reset_mock()
                tray.set_startup_enabled(False)
                self.assertEqual(
                    delete_startup.call_args_list,
                    [mock.call(), mock.call(tray.LEGACY_WINDOWS_RUN_VALUE)],
                )

    def test_legacy_startup_entry_is_migrated_to_the_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            launcher = Path(temp_dir) / "LocalVoiceBridge.exe"
            launcher.write_bytes(b"launcher")
            legacy_entry = Path(temp_dir) / "ChatGPT Local Voice Bridge.vbs"
            legacy_entry.write_text("legacy", encoding="utf-8")
            with (
                mock.patch.object(tray, "LAUNCHER_EXE", launcher),
                mock.patch.object(tray, "legacy_startup_entry_paths", return_value=(legacy_entry,)),
                mock.patch.object(tray, "_write_startup_command") as write_startup,
            ):
                self.assertTrue(tray.migrate_legacy_startup())

            write_startup.assert_called_once_with(f'"{launcher}"')
            self.assertFalse(legacy_entry.exists())

    def test_startup_folder_falls_back_when_appdata_is_missing(self) -> None:
        home = Path("voice-test-home")
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(tray, "IS_WINDOWS", True),
            mock.patch.object(tray.Path, "home", return_value=home),
        ):
            self.assertEqual(
                tray.startup_folder(),
                home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup",
            )

    def test_exe_launcher_uses_pythonw_and_checks_qt_dependencies(self) -> None:
        launcher = (ROOT / "scripts" / "launcher" / "VoiceBridgeLauncher.cs").read_text(encoding="utf-8")
        build_script = (ROOT / "scripts" / "build-launcher.ps1").read_text(encoding="utf-8")
        shortcut_script_path = ROOT / "scripts" / "install-start-menu-shortcut.ps1"
        setup_script = (ROOT / "setup-voice-env.cmd").read_text(encoding="utf-8")
        setup_engine = (ROOT / "scripts" / "setup" / "setup-engine.ps1").read_text(encoding="utf-8")
        setup_gui = (ROOT / "scripts" / "setup" / "setup-gui.ps1").read_text(encoding="utf-8")

        self.assertIn("pythonw.exe", launcher)
        self.assertIn("from PySide6 import QtWidgets, QtSvg", launcher)
        self.assertIn("--self-test", launcher)
        self.assertIn("--setup", launcher)
        self.assertIn("WindowsApplication", build_script)
        self.assertIn("LocalVoiceBridge.exe", build_script)
        self.assertTrue(shortcut_script_path.is_file())
        shortcut_script = shortcut_script_path.read_text(encoding="utf-8")
        self.assertIn("SpecialFolder]::Programs", shortcut_script)
        self.assertIn("Local Voice Bridge.lnk", shortcut_script)
        self.assertIn("CreateShortcut", shortcut_script)
        self.assertIn("TargetPath", shortcut_script)
        self.assertIn("WorkingDirectory", shortcut_script)
        self.assertIn("IconLocation", shortcut_script)
        self.assertIn("LocalVoiceBridge.exe", setup_script)
        self.assertIn("--setup", setup_script)
        self.assertIn("requirements-core.txt", setup_engine)
        self.assertIn("requirements-stt.txt", setup_engine)
        self.assertIn("runtime\\setup", setup_engine)
        self.assertIn("LVB_PROGRESS", setup_engine)
        self.assertIn("build-launcher.ps1", setup_engine)
        self.assertIn("install-start-menu-shortcut.ps1", setup_engine)
        self.assertIn("開発者向け（通常は不要）", setup_gui)
        self.assertIn("開発者向けの項目を表示", setup_gui)
        self.assertIn('$visibleProfileKeys = @("reading", "stt")', setup_gui)
        self.assertIn('if ($advancedCheck.Checked)', setup_gui)
        self.assertIn("失敗内容をコピー", setup_gui)

    def test_public_tree_has_no_vbs_launcher(self) -> None:
        self.assertFalse((ROOT / "start-voice-bridge.vbs").exists())

    def test_public_contributor_contract_requires_agent_entrypoint(self) -> None:
        self.assertTrue((ROOT / "CONTRIBUTING.md").is_file())
        agents_path = ROOT / "AGENTS.md"
        self.assertTrue(agents_path.is_file())
        agents = agents_path.read_text(encoding="utf-8")
        self.assertIn("README.md", agents)
        self.assertIn("CONTRIBUTING.md", agents)
        self.assertIn("拡張機能を再読み込み", agents)

    def test_windows_directories_open_with_explicit_explorer_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir) / "generated-audio"
            with (
                mock.patch.object(tray, "IS_WINDOWS", True),
                mock.patch.object(tray.subprocess, "Popen") as popen,
            ):
                tray.open_path(directory)
            popen.assert_called_once_with(
                ["explorer.exe", str(directory)],
                creationflags=tray.CREATE_NO_WINDOW,
            )

    def test_server_disables_only_implicit_hugging_face_tokens(self) -> None:
        source = (ROOT / "local-api" / "server.py").read_text(encoding="utf-8")
        setting = 'os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")'
        engine_import = "from irodori_engine import IrodoriError"
        self.assertIn(setting, source)
        self.assertLess(source.index(setting), source.index(engine_import))

    def test_controller_has_no_autohotkey_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("autohotkey", source)
        self.assertNotIn(".ahk", source)

    def test_qt_application_keeps_running_when_pet_is_hidden(self) -> None:
        app = tray.create_qt_application([])
        self.assertFalse(app.quitOnLastWindowClosed())

    def test_status_updates_are_delivered_through_callback_without_pystray(self) -> None:
        controller = tray.VoiceBridgeController()
        statuses: list[str] = []
        controller.set_status_callback(statuses.append)
        controller.set_status("Ready")
        self.assertEqual(statuses[-1], "Ready")

    def test_controller_shutdown_is_idempotent(self) -> None:
        controller = tray.VoiceBridgeController()
        with mock.patch.object(controller, "stop_owned_server") as stop_owned_server:
            controller.shutdown()
            controller.shutdown()
        self.assertTrue(controller.stop_requested)
        stop_owned_server.assert_called_once_with()

    def test_controller_uses_qsystemtrayicon_and_has_no_second_gui_loop(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("QSystemTrayIcon", source)
        self.assertIn("app.exec()", source)
        self.assertNotIn("pystray", source)
        self.assertNotIn("icon.run(", source)

    def test_requirements_pin_pyside_and_remove_pystray(self) -> None:
        core = (ROOT / "local-api" / "requirements-core.txt").read_text(encoding="utf-8")
        stt = (ROOT / "local-api" / "requirements-stt.txt").read_text(encoding="utf-8")
        bundle = (ROOT / "local-api" / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("PySide6==", core)
        self.assertIn("hf-xet==1.5.1", core)
        self.assertNotIn("faster-whisper", core)
        self.assertIn("faster-whisper", stt)
        self.assertIn("requirements-core.txt", bundle)
        self.assertIn("requirements-stt.txt", bundle)
        self.assertNotIn("pystray", core + stt + bundle)


if __name__ == "__main__":
    unittest.main()
