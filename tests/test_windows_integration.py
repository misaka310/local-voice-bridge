from __future__ import annotations

import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))


class WindowsIntegrationTests(unittest.TestCase):
    def _module(self):
        try:
            return importlib.import_module("windows_integration")
        except ModuleNotFoundError as exc:
            self.fail(f"windows_integration module is required: {exc}")

    def test_launch_setup_uses_launcher_setup_mode_without_console(self) -> None:
        integration = self._module()
        launcher = Path(r"C:\fixture\LocalVoiceBridge.exe")
        app_root = Path(r"C:\fixture")
        expected_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )

        with mock.patch.object(integration.subprocess, "Popen") as popen:
            integration.launch_setup(launcher, app_root)

        popen.assert_called_once_with(
            [str(launcher), "--setup"],
            cwd=app_root,
            creationflags=expected_flags,
        )

    def test_launch_application_uses_no_console_process_group(self) -> None:
        integration = self._module()
        launcher = Path(r"C:\fixture\LocalVoiceBridge.exe")
        app_root = Path(r"C:\fixture")
        expected_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )

        with mock.patch.object(integration.subprocess, "Popen") as popen:
            integration.launch_application(launcher, app_root)

        popen.assert_called_once_with(
            [str(launcher)],
            cwd=app_root,
            creationflags=expected_flags,
        )

    def test_launch_uninstall_keeps_powershell_hidden_without_console(self) -> None:
        integration = self._module()
        script = Path(r"C:\fixture\uninstall-local-voice-bridge.ps1")
        app_root = Path(r"C:\fixture")
        expected_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )

        with mock.patch.object(integration.subprocess, "Popen") as popen:
            integration.launch_uninstall(script, app_root)

        popen.assert_called_once_with(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(script),
                "-RemoveGeneratedData",
            ],
            cwd=app_root,
            creationflags=expected_flags,
        )


if __name__ == "__main__":
    unittest.main()
