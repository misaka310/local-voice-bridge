from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

import server_supervisor as supervisor  # noqa: E402
import tray_controller as tray  # noqa: E402
import windows_integration as windows  # noqa: E402


class TrayControllerProcessTests(unittest.TestCase):
    def test_windows_mutex_rejects_second_instance(self) -> None:
        with (
            mock.patch.object(windows, "IS_WINDOWS", True),
            mock.patch.object(
                windows,
                "_open_named_mutex",
                return_value=(123, windows.ERROR_ALREADY_EXISTS),
            ) as open_mutex,
            mock.patch.object(windows, "_close_windows_handle") as close_handle,
        ):
            self.assertFalse(windows.acquire_single_instance())

        open_mutex.assert_called_once_with(windows.MUTEX_NAME)
        close_handle.assert_called_once_with(123)

    def test_windows_mutex_keeps_unique_instance_handle_open(self) -> None:
        with (
            mock.patch.object(windows, "IS_WINDOWS", True),
            mock.patch.object(windows, "_open_named_mutex", return_value=(456, 0)),
            mock.patch.object(windows, "_close_windows_handle") as close_handle,
        ):
            self.assertTrue(windows.acquire_single_instance())

        self.assertEqual(windows._MUTEX_HANDLE, 456)
        close_handle.assert_not_called()

    def test_windows_mutex_release_closes_handle_once(self) -> None:
        windows._MUTEX_HANDLE = 789
        with (
            mock.patch.object(windows, "IS_WINDOWS", True),
            mock.patch.object(windows, "_close_windows_handle") as close_handle,
        ):
            windows.release_single_instance()
            windows.release_single_instance()

        self.assertIsNone(windows._MUTEX_HANDLE)
        close_handle.assert_called_once_with(789)

    def test_duplicate_launch_exits_without_blocking_message(self) -> None:
        with (
            mock.patch.object(tray, "IS_WINDOWS", True),
            mock.patch.object(tray, "configure_logging"),
            mock.patch.object(tray, "migrate_legacy_startup"),
            mock.patch.object(tray, "acquire_single_instance", return_value=False),
            mock.patch.object(tray, "show_message") as show_message,
        ):
            self.assertEqual(tray.main(), 0)

        show_message.assert_not_called()

    def test_compatible_existing_server_is_not_owned_or_terminated(self) -> None:
        controller = supervisor.VoiceBridgeController()
        with (
            mock.patch.object(supervisor, "probe_health", return_value=(True, {"ok": True})),
            mock.patch.object(supervisor.subprocess, "Popen") as popen,
        ):
            controller._ensure_running()
            controller.shutdown()

        self.assertEqual(controller.status, "Stopping")
        self.assertIsNone(controller._process)
        popen.assert_not_called()

    def test_owned_server_is_stopped_once_on_shutdown(self) -> None:
        process = mock.Mock()
        process.pid = 321
        process.poll.return_value = None
        controller = supervisor.VoiceBridgeController()
        controller._process = process

        controller.shutdown()
        controller.shutdown()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    def test_restart_stops_only_a_same_installation_existing_server(self) -> None:
        controller = supervisor.VoiceBridgeController()
        payload = {"ok": True, "instanceId": supervisor.INSTALLATION_ID}
        with (
            mock.patch.object(supervisor, "probe_health", return_value=(True, payload)),
            mock.patch.object(supervisor, "request_same_installation_shutdown", return_value=True) as shutdown,
        ):
            self.assertTrue(controller.prepare_application_restart())
        shutdown.assert_called_once_with(payload)
        self.assertEqual(controller.status, "Stopping existing service")

    def test_restart_refuses_a_different_installation(self) -> None:
        controller = supervisor.VoiceBridgeController()
        payload = {"ok": True, "instanceId": "another-installation"}
        with (
            mock.patch.object(supervisor, "probe_health", return_value=(True, payload)),
            mock.patch.object(supervisor, "request_same_installation_shutdown") as shutdown,
        ):
            self.assertFalse(controller.prepare_application_restart())
        shutdown.assert_not_called()


if __name__ == "__main__":
    unittest.main()
