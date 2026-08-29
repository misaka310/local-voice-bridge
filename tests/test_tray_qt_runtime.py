from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

import tray_controller as tray  # noqa: E402


class FakeController:
    def __init__(self, status: str = "Ready") -> None:
        self.status = status
        self.callback: Callable[[str], None] | None = None
        self.start_count = 0
        self.shutdown_count = 0
        self.restart_count = 0

    def set_status_callback(self, callback: Callable[[str], None] | None) -> None:
        self.callback = callback
        if callback is not None:
            callback(self.status)

    def start_monitor(self) -> None:
        self.start_count += 1

    def shutdown(self) -> None:
        self.shutdown_count += 1

    def restart_async(self, *_: object) -> None:
        self.restart_count += 1

    def prepare_application_restart(self) -> bool:
        return True

    def clear_generated_audio_files(self):
        return SimpleNamespace(deleted_files=0, deleted_bytes=0, failed_files=0)

    def open_controller_log(self, *_: object) -> None:
        return None

    def open_audio_folder(self, *_: object) -> None:
        return None

    def open_reference_folder(self, *_: object) -> None:
        return None

    def publish_from_worker(self, status: str) -> None:
        def worker() -> None:
            self.status = status
            if self.callback is not None:
                self.callback(status)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)


class FakeControlClient:
    def __init__(self, *, cancel_grace_ms: int = 700) -> None:
        self.cancel_grace_ms = cancel_grace_ms
        self.playback_phase = "idle"
        self.voice_runtime_phase = "idle"
        self.is_playing = False
        self.settings_calls: list[dict[str, object]] = []
        self.commands: list[str] = []

    def get_snapshot(self) -> dict[str, object]:
        return {
            "ok": True,
            "initialized": True,
            "settings": {
                "enabled": True,
                "voiceVolume": 0.6,
                "referenceVoice": "",
                "micConversationEnabled": True,
                "sttModel": "small",
                "cancelGraceMs": self.cancel_grace_ms,
            },
            "referenceVoices": [{"id": "", "label": "none"}],
            "extension": {
                "connected": False,
                "statusText": "Waiting for ChatGPT",
                "statusLevel": "info",
                "currentText": "",
                "queueSize": 0,
                "isPlaying": self.is_playing,
                "playbackPhase": self.playback_phase,
                "replayAvailable": False,
                "tabsCount": 0,
            },
            "voiceRuntime": {
                "phase": self.voice_runtime_phase,
            },
        }

    def update_settings(self, payload: dict[str, object]) -> dict[str, object]:
        self.settings_calls.append(dict(payload))
        return {"ok": True}

    def send_command(self, command: str) -> dict[str, object]:
        self.commands.append(command)
        return {"ok": True}

    def send_conversation_event(self, event_type: str, payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "type": event_type, "payload": payload}

    def update_conversation_state(self, payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "conversation": payload}


class FakeConversationController:
    def __init__(self) -> None:
        self.configurations: list[dict[str, object]] = []
        self.key_events: list[tuple[int, bool]] = []
        self.shutdown_count = 0

    def configure(self, *, enabled: bool, stt_model: str, cancel_grace_ms: int) -> None:
        self.configurations.append(
            {"enabled": enabled, "sttModel": stt_model, "cancelGraceMs": cancel_grace_ms}
        )

    def handle_key_event(self, vk_code: int, *, is_down: bool) -> bool:
        self.key_events.append((vk_code, is_down))
        return True

    def shutdown(self) -> None:
        self.shutdown_count += 1


class FakeKeyboardHook:
    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> None:
        self.start_count += 1

    def stop(self) -> None:
        self.stop_count += 1


class TrayQtRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = tray.create_qt_application([])

    @staticmethod
    def _create_pet_root(temp_dir: str, *, local_pets: tuple[str, ...] = ()) -> Path:
        root = Path(temp_dir) / "pet"
        root.mkdir(parents=True, exist_ok=True)
        (root / "placeholder.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">'
            '<circle cx="16" cy="16" r="12" fill="#4488ff"/>'
            "</svg>",
            encoding="utf-8",
        )
        (root / "pet.json").write_text(
            json.dumps(
                {
                    "id": "local-voice-placeholder",
                    "displayName": "Placeholder",
                    "spritesheetPath": "placeholder.svg",
                    "columns": 1,
                    "rows": 1,
                    "frameWidth": 32,
                    "frameHeight": 32,
                    "displayScale": 1,
                }
            ),
            encoding="utf-8",
        )
        for pet_id in local_pets:
            pet_dir = root / "local" / "voices" / pet_id
            pet_dir.mkdir(parents=True, exist_ok=True)
            (pet_dir / "sprite.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">'
                '<rect x="4" y="4" width="24" height="24" fill="#44aa88"/>'
                "</svg>",
                encoding="utf-8",
            )
            (pet_dir / "pet.json").write_text(
                json.dumps(
                    {
                        "id": pet_id,
                        "displayName": pet_id,
                        "spritesheetPath": "sprite.svg",
                        "columns": 1,
                        "rows": 1,
                        "frameWidth": 32,
                        "frameHeight": 32,
                        "displayScale": 1,
                    }
                ),
                encoding="utf-8",
            )
        return root

    def _create_runtime(
        self,
        temp_dir: str,
        controller: FakeController | None = None,
        *,
        conversation_controller: FakeConversationController | None = None,
        keyboard_hook: FakeKeyboardHook | None = None,
        control_panel_client: FakeControlClient | None = None,
    ) -> tray.VoiceBridgeQtRuntime:
        runtime = tray.VoiceBridgeQtRuntime(
            self.app,
            controller=controller or FakeController(),
            pet_root=self._create_pet_root(temp_dir),
            settings_path=Path(temp_dir) / "settings.json",
            control_panel_client=control_panel_client or FakeControlClient(),
            panel_state_path=Path(temp_dir) / "panel-window.json",
            conversation_controller=conversation_controller or FakeConversationController(),
            keyboard_hook=keyboard_hook or FakeKeyboardHook(),
            start_panel_polling=False,
            start_monitor=True,
            show_tray=False,
        )
        runtime.app = SimpleNamespace(quit=lambda: None)
        return runtime

    def test_runtime_initializes_status_after_actions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._create_runtime(temp_dir, FakeController("Ready"))
            self.app.processEvents()

            self.assertEqual(runtime.status_action.text(), "Status: Ready")
            self.assertEqual(runtime.pet.current_state, "idle")
            runtime.shutdown()

    def test_runtime_owns_one_pet_and_status_changes_do_not_duplicate_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = FakeController("Ready")
            runtime = self._create_runtime(temp_dir, controller)
            pet_identity = id(runtime.pet)

            controller.publish_from_worker("Unhealthy")
            self.app.processEvents()
            controller.publish_from_worker("Ready")
            self.app.processEvents()

            self.assertEqual(id(runtime.pet), pet_identity)
            self.assertEqual(runtime.pet.current_state, "idle")
            runtime.shutdown()

    def test_pet_follows_voice_generation_and_playback_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = FakeControlClient()
            runtime = self._create_runtime(temp_dir, control_panel_client=client)

            runtime.pet.set_state = mock.Mock(wraps=runtime.pet.set_state)  # type: ignore[method-assign]
            client.playback_phase = "generating"
            runtime.sync_conversation_settings()
            runtime.sync_conversation_settings()
            self.assertEqual(runtime.pet.current_state, "thinking")
            runtime.pet.set_state.assert_called_once_with("thinking")

            client.playback_phase = "playing"
            client.is_playing = True
            runtime.sync_conversation_settings()
            self.assertEqual(runtime.pet.current_state, "talking")

            client.playback_phase = "idle"
            client.is_playing = False
            runtime.sync_conversation_settings()
            self.assertEqual(runtime.pet.current_state, "idle")
            runtime.shutdown()

    def test_tray_menu_contains_external_panel_and_service_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._create_runtime(temp_dir)
            action_texts = [action.text() for action in runtime.menu.actions()]

            self.assertIn("Show Local Voice panel", action_texts)
            self.assertIn("Bring Desktop Pet Back", action_texts)
            self.assertIn("Restart Voice Bridge", action_texts)
            self.assertIn("Exit", action_texts)
            self.assertNotIn("デスクトップペットを表示", action_texts)
            self.assertNotIn("使用するペット", action_texts)
            self.assertNotIn("ペットを常に手前に表示", action_texts)
            runtime.shutdown()

    def test_restart_action_relaunches_the_entire_tray_application(self) -> None:
        runtime = tray.VoiceBridgeQtRuntime.__new__(tray.VoiceBridgeQtRuntime)
        runtime.controller = SimpleNamespace(prepare_application_restart=lambda: True)
        runtime._restart_after_exit = False
        shutdown_calls: list[bool] = []
        runtime.shutdown = lambda: shutdown_calls.append(True)  # type: ignore[method-assign]

        with (
            mock.patch.object(tray, "IS_WINDOWS", True),
            mock.patch.object(tray, "LAUNCHER_EXE", SimpleNamespace(is_file=lambda: True)),
        ):
            runtime.restart_application()

        self.assertTrue(runtime._restart_after_exit)
        self.assertEqual(shutdown_calls, [True])

    def test_restart_launch_uses_the_supported_launcher_after_releasing_mutex(self) -> None:
        runtime = tray.VoiceBridgeQtRuntime.__new__(tray.VoiceBridgeQtRuntime)
        events: list[str] = []

        with (
            mock.patch.object(tray, "release_single_instance", side_effect=lambda: events.append("release")),
            mock.patch.object(tray, "launch_application", side_effect=lambda exe, cwd: events.append("launch")) as launch,
        ):
            runtime._launch_application_after_exit()

        self.assertEqual(events, ["release", "launch"])
        launch.assert_called_once_with(
            tray.LAUNCHER_EXE,
            tray.APP_ROOT,

        )

    def test_pet_return_action_restores_and_shows_the_desktop_pet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            runtime = self._create_runtime(temp_dir)
            runtime.pet.move(-10000, -10000)
            runtime.pet.hide_pet()

            runtime.pet_return_action.trigger()
            self.app.processEvents()

            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertTrue(runtime.pet.isVisible())
            self.assertNotEqual((runtime.pet.x(), runtime.pet.y()), (-10000, -10000))
            self.assertTrue(saved["visible"])
            self.assertEqual(saved["x"], runtime.pet.x())
            self.assertEqual(saved["y"], runtime.pet.y())
            runtime.shutdown()

    def test_runtime_owns_hidden_panel_and_pet_double_click_toggles_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = self._create_runtime(temp_dir)
            self.app.processEvents()

            self.assertFalse(runtime.control_panel.isVisible())
            self.assertEqual(runtime.panel_action.text(), "Show Local Voice panel")

            runtime.pet.panel_toggle_requested.emit()
            self.app.processEvents()
            self.assertTrue(runtime.control_panel.isVisible())
            self.assertEqual(runtime.panel_action.text(), "Hide Local Voice panel")

            runtime.pet.panel_toggle_requested.emit()
            self.app.processEvents()
            self.assertFalse(runtime.control_panel.isVisible())
            self.assertEqual(runtime.panel_action.text(), "Show Local Voice panel")
            runtime.shutdown()

    def test_monitor_starts_while_pet_stays_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = FakeController()
            runtime = self._create_runtime(temp_dir, controller)
            self.app.processEvents()
            self.assertEqual(controller.start_count, 1)
            self.assertTrue(runtime.pet.isVisible())
            self.assertEqual(controller.start_count, 1)
            self.assertEqual(controller.shutdown_count, 0)
            runtime.shutdown()

    def test_external_settings_change_switches_the_running_desktop_pet(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            runtime = tray.VoiceBridgeQtRuntime(
                self.app,
                controller=FakeController(),
                pet_root=self._create_pet_root(temp_dir, local_pets=("misaka", "asuka")),
                settings_path=settings_path,
                start_monitor=False,
                show_tray=False,
            )
            runtime.app = SimpleNamespace(quit=lambda: None)
            self.assertEqual(runtime.pet.current_pet.selection_id, "placeholder")
            settings_path.write_text(
                json.dumps({"version": 1, "selectedPetId": "misaka", "visible": True}),
                encoding="utf-8",
            )

            runtime.sync_pet_settings_from_disk()
            self.app.processEvents()

            self.assertEqual(runtime.pet.current_pet.selection_id, "misaka")
            self.assertTrue(runtime.pet.isVisible())
            runtime.shutdown()

    def test_runtime_starts_hook_syncs_mic_settings_and_stops_both_on_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conversation = FakeConversationController()
            hook = FakeKeyboardHook()
            runtime = self._create_runtime(
                temp_dir,
                conversation_controller=conversation,
                keyboard_hook=hook,
            )
            runtime.sync_conversation_settings()

            self.assertEqual(hook.start_count, 1)
            self.assertIn(
                {"enabled": True, "sttModel": "small", "cancelGraceMs": 700},
                conversation.configurations,
            )
            runtime.shutdown()
            self.assertEqual(hook.stop_count, 1)
            self.assertEqual(conversation.shutdown_count, 1)

    def test_zero_cancel_grace_is_not_replaced_by_the_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            conversation = FakeConversationController()
            runtime = self._create_runtime(
                temp_dir,
                conversation_controller=conversation,
                control_panel_client=FakeControlClient(cancel_grace_ms=0),
            )
            runtime.sync_conversation_settings()

            self.assertIn(
                {"enabled": True, "sttModel": "small", "cancelGraceMs": 0},
                conversation.configurations,
            )
            runtime.shutdown()

    def test_shutdown_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = FakeController()
            runtime = self._create_runtime(temp_dir, controller)
            runtime.shutdown()
            runtime.shutdown()

            self.assertEqual(controller.shutdown_count, 1)
            self.assertTrue(runtime._shutdown_started)
            self.assertTrue(runtime.control_panel._shutting_down)


if __name__ == "__main__":
    unittest.main()
