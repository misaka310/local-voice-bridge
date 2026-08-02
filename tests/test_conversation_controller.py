from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from conversation_controller import (  # noqa: E402
    VK_LCONTROL,
    VK_OEM_102,
    VK_RCONTROL,
    FasterWhisperTranscriber,
    VoiceConversationController,
    YouTubePauseNotifier,
)


class InlineExecutor:
    def submit(self, function, *args):
        function(*args)
        return None

    def shutdown(self, **_kwargs):
        return None


class DelayedExecutor:
    def __init__(self) -> None:
        self.items = []

    def submit(self, function, *args):
        self.items.append((function, args))
        return None

    def run_all(self) -> None:
        while self.items:
            function, args = self.items.pop(0)
            function(*args)

    def shutdown(self, **_kwargs):
        self.items.clear()


class ImmediateLease:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeGpuArbiter:
    def __init__(self) -> None:
        self.stt_calls = 0

    def acquire_stt(self, **_kwargs):
        self.stt_calls += 1
        return ImmediateLease()


class FakeClient:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.events: list[tuple[str, dict[str, object]]] = []
        self.states: list[dict[str, object]] = []
        self.playing_states: list[bool] = []
        self.playback_phases: list[str] = []

    def get_snapshot(self):
        playing = self.playing_states.pop(0) if self.playing_states else False
        phase = self.playback_phases.pop(0) if self.playback_phases else ("playing" if playing else "idle")
        return {"extension": {"isPlaying": playing, "playbackPhase": phase}}

    def send_command(self, command):
        self.commands.append(command)
        return {"ok": True}

    def send_conversation_event(self, event_type, payload):
        self.events.append((event_type, dict(payload)))
        return {"ok": True}

    def update_conversation_state(self, payload):
        self.states.append(dict(payload))
        return {"ok": True}


class FakeRecorder:
    sample_rate = 16000

    def __init__(self, samples: np.ndarray | None = None) -> None:
        self.samples = samples if samples is not None else np.full(16000, 0.02, dtype=np.float32)
        self.started = 0
        self.stopped = 0
        self.discarded = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return self.samples.copy()

    def discard(self):
        self.discarded += 1


class FakeTranscriber:
    def __init__(self, text: str = "日本語のテストです") -> None:
        self.text = text
        self.calls = 0
        self.prepared: list[str] = []

    def prepare(self, model_name):
        self.prepared.append(str(model_name))
        return "cuda"

    def transcribe(self, _audio, _model):
        self.calls += 1
        return self.text, "cuda"


class FakeHttpResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


class CapturingOpener:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[object, float]] = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, float(timeout)))
        if self.fail:
            raise OSError("unavailable")
        return FakeHttpResponse()


class FakePauseNotifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[bool] = []

    def set_active(self, active: bool) -> bool:
        self.calls.append(bool(active))
        if self.fail:
            raise RuntimeError("pause bridge unavailable")
        return True


class FailingPrepareTranscriber(FakeTranscriber):
    def prepare(self, model_name):
        self.prepared.append(str(model_name))
        raise RuntimeError("model download failed")


class FasterWhisperTranscriberTests(unittest.TestCase):
    def test_cuda_prepare_failure_does_not_fall_back_to_cpu_in_live_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcriber = FasterWhisperTranscriber(download_root=Path(temp_dir))
            calls: list[tuple[str, str]] = []

            def model(_name, device, compute_type):
                calls.append((device, compute_type))
                raise RuntimeError("cuda unavailable")

            transcriber._model = model
            with self.assertRaisesRegex(RuntimeError, "CPUへの自動フォールバックは無効"):
                transcriber.prepare("small")
            self.assertEqual(calls, [("cuda", "float16")])

    def test_cuda_transcribe_failure_does_not_call_cpu_in_live_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcriber = FasterWhisperTranscriber(download_root=Path(temp_dir))
            calls: list[tuple[str, str]] = []

            def model(_name, device, compute_type):
                calls.append((device, compute_type))
                raise RuntimeError("cuda inference failed")

            transcriber._model = model
            with self.assertRaisesRegex(RuntimeError, "CPUへの自動フォールバックは無効"):
                transcriber.transcribe(np.ones(16000, dtype=np.float32), "small")
            self.assertEqual(calls, [("cuda", "float16")])

    def test_explicit_diagnostic_mode_may_use_cpu(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            transcriber = FasterWhisperTranscriber(
                download_root=Path(temp_dir),
                allow_cpu_diagnostic=True,
            )
            cpu_model = object()

            def model(_name, device, _compute_type):
                if device == "cuda":
                    raise RuntimeError("cuda unavailable")
                return cpu_model

            transcriber._model = model
            transcriber._run = lambda selected, _audio: "診断" if selected is cpu_model else ""
            self.assertEqual(transcriber.prepare("small"), "cpu")
            self.assertEqual(
                transcriber.transcribe(np.ones(16000, dtype=np.float32), "small"),
                ("診断", "cpu"),
            )


class YouTubePauseNotifierTests(unittest.TestCase):
    def test_posts_local_voice_bridge_source_state(self):
        opener = CapturingOpener()
        notifier = YouTubePauseNotifier(
            state_url="http://127.0.0.1:17654/state",
            timeout_seconds=0.25,
            opener=opener,
        )

        self.assertTrue(notifier.set_active(True))
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:17654/state")
        self.assertEqual(timeout, 0.25)
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"active": True, "source": "local-voice-bridge"},
        )

    def test_returns_false_when_pause_service_is_unavailable(self):
        notifier = YouTubePauseNotifier(opener=CapturingOpener(fail=True))
        self.assertFalse(notifier.set_active(True))

    def test_normalizes_documented_loopback_hosts(self):
        for url in (
            "http://127.0.0.1:27654/state",
            "http://localhost:27654/state",
            "http://[::1]:27654/state",
        ):
            with self.subTest(url=url):
                notifier = YouTubePauseNotifier(state_url=url)
                self.assertEqual(notifier.state_url, "http://127.0.0.1:27654/state")

    def test_rejects_non_loopback_or_malformed_state_urls(self):
        for url in (
            "https://127.0.0.1:17654/state",
            "http://192.168.1.20:17654/state",
            "http://example.com:17654/state",
            "http://127.0.0.1:17654/reset",
            "http://127.0.0.1:17654/state?token=value",
            "not-a-url",
        ):
            with self.subTest(url=url):
                notifier = YouTubePauseNotifier(state_url=url)
                self.assertEqual(notifier.state_url, YouTubePauseNotifier.DEFAULT_STATE_URL)


class VoiceConversationControllerTests(unittest.TestCase):
    def make_controller(
        self,
        *,
        samples=None,
        text="日本語のテストです",
        control_executor=None,
        stt_executor=None,
        pause_notifier=None,
        pause_executor=None,
    ):
        client = FakeClient()
        recorder = FakeRecorder(samples)
        transcriber = FakeTranscriber(text)
        controller = VoiceConversationController(
            client,
            recorder=recorder,
            transcriber=transcriber,
            gpu_arbiter=FakeGpuArbiter(),
            control_executor=control_executor or InlineExecutor(),
            executor=stt_executor or InlineExecutor(),
            pause_notifier=pause_notifier or FakePauseNotifier(),
            pause_executor=pause_executor or InlineExecutor(),
            sleep=lambda _seconds: None,
        )
        controller.configure(enabled=True, stt_model="small", cancel_grace_ms=700)
        return controller, client, recorder, transcriber

    def test_held_chord_starts_recording_automatically_after_model_preparation(self):
        stt_executor = DelayedExecutor()
        control_executor = DelayedExecutor()
        controller, client, recorder, _transcriber = self.make_controller(
            stt_executor=stt_executor,
            control_executor=control_executor,
        )

        self.press_chord(controller)
        control_executor.run_all()
        self.assertEqual(recorder.started, 0)
        self.assertEqual(client.states[-1]["phase"], "preparing_model")
        self.assertNotIn("もう一度押してください", str(client.states[-1]["statusText"]))
        self.assertIn("押し続けると準備完了後に録音", str(client.states[-1]["statusText"]))
        self.assertIn("保存済みモデルは再利用", str(client.states[0]["statusText"]))

        stt_executor.run_all()
        control_executor.run_all()

        self.assertEqual(recorder.started, 1)
        self.assertEqual(client.states[-1]["phase"], "recording")

    def test_releasing_chord_while_model_prepares_does_not_start_recording_later(self):
        stt_executor = DelayedExecutor()
        control_executor = DelayedExecutor()
        controller, _client, recorder, _transcriber = self.make_controller(
            stt_executor=stt_executor,
            control_executor=control_executor,
        )

        self.press_chord(controller)
        control_executor.run_all()
        controller.handle_key_event(VK_OEM_102, False)
        control_executor.run_all()
        stt_executor.run_all()
        control_executor.run_all()

        self.assertEqual(recorder.started, 0)

    def test_model_is_prepared_when_enabled_or_changed_not_on_first_utterance(self):
        controller, _client, _recorder, transcriber = self.make_controller()
        self.assertEqual(transcriber.prepared, ["small"])
        controller.configure(enabled=True, stt_model="medium", cancel_grace_ms=700)
        self.assertEqual(transcriber.prepared, ["small", "medium"])

    def test_failed_model_prepare_is_not_retried_on_every_settings_poll(self):
        client = FakeClient()
        transcriber = FailingPrepareTranscriber()
        controller = VoiceConversationController(
            client,
            recorder=FakeRecorder(),
            transcriber=transcriber,
            gpu_arbiter=FakeGpuArbiter(),
            control_executor=InlineExecutor(),
            executor=InlineExecutor(),
            sleep=lambda _seconds: None,
        )
        controller.configure(enabled=True, stt_model="small", cancel_grace_ms=700)
        controller.configure(enabled=True, stt_model="small", cancel_grace_ms=700)
        self.assertEqual(transcriber.prepared, ["small"])
        self.assertEqual(client.states[-1]["phase"], "error")

        controller.configure(enabled=False, stt_model="small", cancel_grace_ms=700)
        controller.configure(enabled=True, stt_model="small", cancel_grace_ms=700)
        self.assertEqual(transcriber.prepared, ["small", "small"])

    def test_idle_state_is_reconciled_after_browser_reports_stale_hotkey_text(self):
        controller, client, _recorder, _transcriber = self.make_controller()
        controller.reconcile_reported_state({
            "phase": "idle",
            "statusText": "待機中（右Ctrl長押し）",
            "sttDevice": "",
            "sttModel": "small",
        })
        self.assertEqual(client.states[-1]["statusText"], "待機中（右Ctrl＋＼ 長押し）")
        self.assertEqual(client.states[-1]["sttDevice"], "cuda")

    def press_chord(self, controller):
        self.assertFalse(controller.handle_key_event(VK_RCONTROL, True))
        self.assertTrue(controller.handle_key_event(VK_OEM_102, True))

    def release_chord(self, controller):
        self.assertTrue(controller.handle_key_event(VK_OEM_102, False))
        self.assertFalse(controller.handle_key_event(VK_RCONTROL, False))

    def test_right_ctrl_plus_oem_102_starts_recording_after_stop_and_cancel(self):
        controller, client, recorder, _ = self.make_controller()
        self.press_chord(controller)
        self.assertEqual(client.commands, ["stop"])
        self.assertEqual(client.events[0][0], "cancel_pending")
        self.assertEqual(recorder.started, 1)
        self.assertEqual(client.states[-1]["phase"], "recording")

    def test_tts_generation_without_audio_does_not_block_recording_start(self):
        controller, client, recorder, _ = self.make_controller()
        client.playback_phases = ["generating"]

        self.press_chord(controller)

        self.assertEqual(recorder.started, 1)
        self.assertEqual(client.states[-1]["phase"], "recording")

    def test_left_ctrl_does_not_start_recording(self):
        controller, client, recorder, _ = self.make_controller()
        self.assertFalse(controller.handle_key_event(VK_LCONTROL, True))
        self.assertEqual(client.commands, [])
        self.assertEqual(recorder.started, 0)

    def test_right_ctrl_alone_does_not_start_or_get_suppressed(self):
        controller, client, recorder, _ = self.make_controller()
        self.assertFalse(controller.handle_key_event(VK_RCONTROL, True))
        self.assertFalse(controller.handle_key_event(VK_RCONTROL, False))
        self.assertEqual(client.commands, [])
        self.assertEqual(recorder.started, 0)

    def test_oem_102_alone_does_not_start_or_get_suppressed(self):
        controller, client, recorder, _ = self.make_controller()
        self.assertFalse(controller.handle_key_event(VK_OEM_102, True))
        self.assertFalse(controller.handle_key_event(VK_OEM_102, False))
        self.assertEqual(client.commands, [])
        self.assertEqual(recorder.started, 0)

    def test_key_repeat_does_not_start_twice(self):
        controller, _client, recorder, _ = self.make_controller()
        self.press_chord(controller)
        self.assertTrue(controller.handle_key_event(VK_OEM_102, True))
        self.assertEqual(recorder.started, 1)

    def test_release_transcribes_and_emits_one_transcript(self):
        controller, client, recorder, transcriber = self.make_controller()
        self.press_chord(controller)
        self.release_chord(controller)
        self.assertEqual(recorder.stopped, 1)
        self.assertEqual(transcriber.calls, 1)
        transcripts = [item for item in client.events if item[0] == "transcript"]
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0][1]["cancelGraceMs"], 700)

    def test_empty_transcript_is_not_emitted(self):
        controller, client, _recorder, _ = self.make_controller(text="   ")
        self.press_chord(controller)
        self.release_chord(controller)
        self.assertFalse(any(event_type == "transcript" for event_type, _payload in client.events))

    def test_short_or_silent_audio_is_not_transcribed(self):
        samples = np.zeros(1000, dtype=np.float32)
        controller, client, _recorder, transcriber = self.make_controller(samples=samples)
        self.press_chord(controller)
        self.release_chord(controller)
        self.assertEqual(transcriber.calls, 0)
        self.assertFalse(any(event_type == "transcript" for event_type, _payload in client.events))

    def test_disabled_mode_does_not_capture_right_ctrl(self):
        controller, client, recorder, _ = self.make_controller()
        controller.configure(enabled=False, stt_model="small", cancel_grace_ms=700)
        self.assertFalse(controller.handle_key_event(VK_RCONTROL, True))
        self.assertFalse(controller.handle_key_event(VK_OEM_102, True))
        self.assertEqual(recorder.started, 0)
        self.assertEqual(client.states[-1]["phase"], "off")

    def test_hook_callback_schedules_work_off_callback_thread(self):
        delayed = DelayedExecutor()
        controller, client, recorder, _ = self.make_controller(control_executor=delayed)
        self.press_chord(controller)
        self.assertEqual(client.commands, [])
        self.assertEqual(recorder.started, 0)
        delayed.run_all()
        self.assertEqual(client.commands, ["stop"])
        self.assertEqual(recorder.started, 1)

    def test_hold_chord_notifies_youtube_source_active_then_inactive(self):
        notifier = FakePauseNotifier()
        controller, _client, _recorder, _transcriber = self.make_controller(pause_notifier=notifier)
        self.press_chord(controller)
        self.assertEqual(notifier.calls, [True])
        self.release_chord(controller)
        self.assertEqual(notifier.calls, [True, False])

    def test_pause_notification_runs_off_hook_callback_thread(self):
        notifier = FakePauseNotifier()
        delayed = DelayedExecutor()
        controller, _client, _recorder, _transcriber = self.make_controller(
            pause_notifier=notifier,
            pause_executor=delayed,
        )
        self.press_chord(controller)
        self.assertEqual(notifier.calls, [])
        delayed.run_all()
        self.assertEqual(notifier.calls, [True])
        self.release_chord(controller)
        self.assertEqual(notifier.calls, [True])
        delayed.run_all()
        self.assertEqual(notifier.calls, [True, False])

    def test_pause_notification_failure_does_not_block_recording(self):
        notifier = FakePauseNotifier(fail=True)
        controller, _client, recorder, _transcriber = self.make_controller(pause_notifier=notifier)
        self.press_chord(controller)
        self.assertEqual(notifier.calls, [True])
        self.assertEqual(recorder.started, 1)

    def test_disabling_mode_releases_youtube_source(self):
        notifier = FakePauseNotifier()
        controller, _client, _recorder, _transcriber = self.make_controller(pause_notifier=notifier)
        self.press_chord(controller)
        controller.configure(enabled=False, stt_model="small", cancel_grace_ms=700)
        self.assertEqual(notifier.calls, [True, False])

    def test_shutdown_cancels_queued_start_before_sending_final_inactive(self):
        notifier = FakePauseNotifier()
        delayed = DelayedExecutor()
        controller, _client, _recorder, _transcriber = self.make_controller(
            pause_notifier=notifier,
            pause_executor=delayed,
        )
        self.press_chord(controller)
        self.assertEqual(notifier.calls, [])
        controller.shutdown()
        self.assertEqual(notifier.calls, [False])

    def test_shutdown_releases_youtube_source_before_executor_cleanup(self):
        notifier = FakePauseNotifier()
        delayed = DelayedExecutor()
        controller, _client, _recorder, _transcriber = self.make_controller(
            pause_notifier=notifier,
            pause_executor=delayed,
        )
        self.press_chord(controller)
        delayed.run_all()
        self.assertEqual(notifier.calls, [True])
        controller.shutdown()
        self.assertEqual(notifier.calls, [True, False])


if __name__ == "__main__":
    unittest.main()
