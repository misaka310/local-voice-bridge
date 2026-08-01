from __future__ import annotations

import ctypes
import ctypes.wintypes
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from gpu_arbiter import GpuArbiter
from installation_identity import installation_id
from runtime_events import NullRuntimeEventLogger

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_OEM_102 = 0xE2
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012


class ConversationApiClient(Protocol):
    def get_snapshot(self) -> dict[str, Any]: ...

    def send_command(self, command: str) -> dict[str, Any]: ...

    def send_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_conversation_state(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class DictationPauseNotifier(Protocol):
    def set_active(self, active: bool) -> bool: ...


class YouTubePauseNotifier:
    DEFAULT_STATE_URL = "http://127.0.0.1:17654/state"
    SOURCE = "local-voice-bridge"

    def __init__(
        self,
        *,
        state_url: str | None = None,
        timeout_seconds: float = 0.5,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        configured_url = state_url or os.environ.get("YOUTUBE_DICTATION_PAUSE_STATE_URL")
        self.state_url = self._normalize_state_url(configured_url)
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self._opener = opener or urllib.request.urlopen

    @classmethod
    def _normalize_state_url(cls, value: str | None) -> str:
        candidate = str(value or cls.DEFAULT_STATE_URL).strip()
        try:
            parsed = urllib.parse.urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return cls.DEFAULT_STATE_URL
        if (
            parsed.scheme.lower() != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or port is None
            or parsed.path != "/state"
            or parsed.query
            or parsed.fragment
        ):
            return cls.DEFAULT_STATE_URL
        return f"http://127.0.0.1:{port}/state"

    def set_active(self, active: bool) -> bool:
        payload = json.dumps(
            {"active": bool(active), "source": self.SOURCE},
            ensure_ascii=True,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.state_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status_value = getattr(response, "status", None)
                if status_value is None:
                    status_value = response.getcode()
                return 200 <= int(status_value) < 300
        except (OSError, ValueError, urllib.error.URLError):
            return False


class SoundDeviceRecorder:
    def __init__(self, *, sample_rate: int = 16000) -> None:
        self.sample_rate = int(sample_rate)
        self._stream: Any | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.RLock()

    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self._stream is not None:
                raise RuntimeError("録音はすでに開始されています。")
            default_input = int(sd.default.device[0])
            if default_input < 0:
                raise RuntimeError(
                    "Windowsの既定マイクが見つかりません。入力デバイスとデスクトップアプリのマイク権限を確認してください。"
                )
            self._chunks = []

            def callback(indata: np.ndarray, _frames: int, _time_info: Any, status: Any) -> None:
                if status:
                    # PortAudioの一時的なstatusは本文ログへ残さず、取得済み音声を優先する。
                    pass
                with self._lock:
                    if self._stream is not None:
                        self._chunks.append(np.asarray(indata[:, 0], dtype=np.float32).copy())

            stream = sd.InputStream(
                device=default_input,
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
                blocksize=0,
            )
            stream.start()
            self._stream = stream

    def stop(self) -> np.ndarray:
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is None:
            return np.empty(0, dtype=np.float32)
        try:
            stream.stop()
        finally:
            stream.close()
        with self._lock:
            chunks = self._chunks
            self._chunks = []
        if not chunks:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def discard(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._chunks = []
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()


class FasterWhisperTranscriber:
    def __init__(self, *, download_root: Path, allow_cpu_diagnostic: bool = False) -> None:
        self.download_root = Path(download_root)
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.allow_cpu_diagnostic = bool(allow_cpu_diagnostic)
        self._models: dict[tuple[str, str, str], Any] = {}
        self._lock = threading.RLock()

    def _model(self, model_name: str, device: str, compute_type: str) -> Any:
        from faster_whisper import WhisperModel

        key = (model_name, device, compute_type)
        with self._lock:
            model = self._models.get(key)
            if model is None:
                model = WhisperModel(
                    model_name,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(self.download_root),
                )
                self._models[key] = model
            return model

    def prepare(self, model_name: str) -> str:
        try:
            self._model(model_name, "cuda", "float16")
            return "cuda"
        except Exception as cuda_error:
            if not self.allow_cpu_diagnostic:
                raise RuntimeError(
                    f"faster-whisper CUDAモデルを準備できませんでした。CPUへの自動フォールバックは無効です: {cuda_error}"
                ) from cuda_error
        try:
            self._model(model_name, "cpu", "int8")
            return "cpu"
        except Exception as cpu_error:
            raise RuntimeError(
                f"診断用faster-whisper CPUモデルも準備できませんでした: {cpu_error}"
            ) from cpu_error

    @staticmethod
    def _run(model: Any, audio: np.ndarray) -> str:
        segments, _info = model.transcribe(
            audio,
            language="ja",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return "".join(str(segment.text or "") for segment in segments).strip()

    def transcribe(self, audio: np.ndarray, model_name: str) -> tuple[str, str]:
        try:
            return self._run(self._model(model_name, "cuda", "float16"), audio), "cuda"
        except Exception as cuda_error:
            if not self.allow_cpu_diagnostic:
                raise RuntimeError(
                    f"faster-whisper CUDA推論に失敗しました。CPUへの自動フォールバックは無効です: {cuda_error}"
                ) from cuda_error
        try:
            return self._run(self._model(model_name, "cpu", "int8"), audio), "cpu"
        except Exception as cpu_error:
            raise RuntimeError(
                f"診断用faster-whisper CPU推論にも失敗しました: {cpu_error}"
            ) from cpu_error


class VoiceConversationController:
    MIN_DURATION_SECONDS = 0.20
    MIN_RMS = 0.0005

    def __init__(
        self,
        client: ConversationApiClient,
        *,
        recorder: SoundDeviceRecorder | Any | None = None,
        transcriber: FasterWhisperTranscriber | Any | None = None,
        gpu_arbiter: GpuArbiter | Any | None = None,
        event_logger: Any | None = None,
        executor: Any | None = None,
        control_executor: Any | None = None,
        pause_notifier: DictationPauseNotifier | Any | None = None,
        pause_executor: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        stop_poll_interval_seconds: float = 0.02,
        stop_wait_seconds: float = 0.5,
    ) -> None:
        root = Path(__file__).resolve().parent
        self.client = client
        self.recorder = recorder or SoundDeviceRecorder()
        self.transcriber = transcriber or FasterWhisperTranscriber(
            download_root=root / "runtime" / "stt-models"
        )
        self._events = event_logger or NullRuntimeEventLogger()
        self.gpu_arbiter = gpu_arbiter or GpuArbiter(installation_id(root.parent), event_logger=self._events)
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-voice-stt")
        self.control_executor = control_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="local-voice-control"
        )
        self.pause_notifier = pause_notifier or YouTubePauseNotifier()
        self.pause_executor = pause_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="local-voice-youtube-pause"
        )
        self.sleep = sleep
        self.stop_poll_interval_seconds = max(0.005, float(stop_poll_interval_seconds))
        self.stop_wait_seconds = max(self.stop_poll_interval_seconds, float(stop_wait_seconds))
        self._lock = threading.RLock()
        self._enabled = False
        self._configured = False
        self._stt_model = "small"
        self._cancel_grace_ms = 700
        self._pressed = False
        self._right_ctrl_down = False
        self._trigger_down = False
        self._recording = False
        self._pause_source_active = False
        self._session_id = 0
        self._model_ready_for = ""
        self._model_device = ""
        self._model_preparing_for = ""
        self._model_failed_for = ""
        self._shutdown = False
        self._phase = "off"
        self._stt_cancel_event = threading.Event()

    def _send_pause_source(self, active: bool) -> None:
        try:
            self.pause_notifier.set_active(bool(active))
        except Exception:
            pass

    def _set_pause_source(self, active: bool) -> None:
        normalized = bool(active)
        with self._lock:
            if self._pause_source_active == normalized:
                return
            self._pause_source_active = normalized
        try:
            self.pause_executor.submit(self._send_pause_source, normalized)
        except Exception:
            pass

    def configure(self, *, enabled: bool, stt_model: str, cancel_grace_ms: int) -> None:
        normalized_model = stt_model if stt_model in {"small", "medium", "large-v3-turbo"} else "small"
        normalized_grace = min(5000, max(0, int(cancel_grace_ms)))
        with self._lock:
            previous_enabled = self._enabled
            previous_model = self._stt_model
            self._enabled = bool(enabled)
            self._stt_model = normalized_model
            self._cancel_grace_ms = normalized_grace
            if previous_enabled != self._enabled or previous_model != normalized_model:
                self._model_failed_for = ""
            first_configuration = not self._configured
            self._configured = True
            if previous_enabled and not self._enabled:
                self._session_id += 1
                self._stt_cancel_event.set()
                self._pressed = False
                self._right_ctrl_down = False
                self._trigger_down = False
                was_recording = self._recording
                self._recording = False
            else:
                was_recording = False
            should_prepare = (
                self._enabled
                and self._model_ready_for != normalized_model
                and self._model_preparing_for != normalized_model
                and self._model_failed_for != normalized_model
            )
            if should_prepare:
                self._model_preparing_for = normalized_model
        if previous_enabled and not self._enabled:
            self._set_pause_source(False)
        if was_recording:
            self.recorder.discard()
        if not self._enabled:
            if first_configuration or previous_enabled != self._enabled:
                self._update_state("off", "マイク会話オフ", error="")
            return
        if should_prepare:
            self._update_state(
                "preparing_model",
                f"STT {normalized_model}を準備中（初回のみダウンロード）",
                stt_model=normalized_model,
                error="",
            )
            self.executor.submit(self._prepare_model, normalized_model)
        elif first_configuration or previous_enabled != self._enabled or previous_model != normalized_model:
            self._update_state(
                "idle",
                "待機中（右Ctrl＋＼ 長押し）",
                stt_device=self._model_device,
                stt_model=normalized_model,
                error="",
            )

    def _prepare_model(self, model_name: str) -> None:
        try:
            prepare = getattr(self.transcriber, "prepare", None)
            with self.gpu_arbiter.acquire_stt(timeout=120.0):
                device = str(prepare(model_name) if callable(prepare) else "cuda")
        except Exception as exc:
            with self._lock:
                if self._model_preparing_for == model_name:
                    self._model_preparing_for = ""
                stale = self._shutdown or not self._enabled or self._stt_model != model_name
                if not stale:
                    self._model_failed_for = model_name
            if not stale:
                self._update_state(
                    "error",
                    f"STT {model_name}を準備できませんでした",
                    stt_model=model_name,
                    error=self._friendly_error(exc),
                )
            return
        with self._lock:
            if self._model_preparing_for == model_name:
                self._model_preparing_for = ""
            stale = self._shutdown or not self._enabled or self._stt_model != model_name
            if not stale:
                self._model_ready_for = model_name
                self._model_device = device
                self._model_failed_for = ""
        if not stale:
            self._update_state(
                "idle",
                "待機中（右Ctrl＋＼ 長押し）",
                stt_device=device,
                stt_model=model_name,
                error="",
            )

    def handle_key_event(self, vk_code: int, is_down: bool) -> bool:
        key = int(vk_code)
        if key not in {VK_RCONTROL, VK_OEM_102}:
            return False
        with self._lock:
            if self._shutdown or not self._enabled:
                return False
            was_pressed = self._pressed
            if key == VK_RCONTROL:
                self._right_ctrl_down = bool(is_down)
            else:
                self._trigger_down = bool(is_down)
            chord_down = self._right_ctrl_down and self._trigger_down
            if chord_down and not was_pressed:
                self._pressed = True
                self._session_id += 1
                self._stt_cancel_event.set()
                self._stt_cancel_event = threading.Event()
                session_id = self._session_id
                action = "start"
            elif was_pressed and not chord_down:
                self._pressed = False
                session_id = self._session_id
                action = "stop"
            else:
                action = "none"
                session_id = self._session_id
            suppress_trigger = key == VK_OEM_102 and (was_pressed or chord_down)
        if action == "start":
            self._set_pause_source(True)
            self.control_executor.submit(self._begin_recording, session_id)
        elif action == "stop":
            self._set_pause_source(False)
            self.control_executor.submit(self._finish_recording, session_id)
        return suppress_trigger

    def _wait_for_tts_stop(self) -> bool:
        attempts = max(1, int(math.ceil(self.stop_wait_seconds / self.stop_poll_interval_seconds)) + 1)
        for attempt in range(attempts):
            try:
                snapshot = self.client.get_snapshot()
                extension = snapshot.get("extension") if isinstance(snapshot, dict) else {}
                if not isinstance(extension, dict):
                    extension = {}
                phase = str(extension.get("playbackPhase") or "idle").strip().lower()
                active = bool(extension.get("isPlaying")) or phase in {"playing", "stopping"}
                if not active:
                    return True
            except Exception:
                # 拡張機能がまだ未接続でも、停止命令後の録音自体は使えるようにする。
                return True
            if attempt + 1 < attempts:
                self.sleep(self.stop_poll_interval_seconds)
        return False

    def _begin_recording(self, session_id: int) -> None:
        try:
            with self._lock:
                model_name = self._stt_model
                model_ready = self._model_ready_for == model_name
            if not model_ready:
                self._update_state(
                    "preparing_model",
                    f"STT {model_name}を準備中です。完了後にもう一度押してください",
                    stt_model=model_name,
                    error="",
                )
                return
            self.client.send_command("stop")
            self.client.send_conversation_event("cancel_pending", {"sessionId": session_id})
            if not self._wait_for_tts_stop():
                self._update_state(
                    "error",
                    "読み上げ停止を確認できないため録音を開始しませんでした",
                    error="Chrome版ChatGPTと拡張機能の接続を確認してください。",
                )
                return
            with self._lock:
                if (
                    self._shutdown
                    or not self._enabled
                    or not self._pressed
                    or session_id != self._session_id
                ):
                    return
            self.recorder.start()
            self._events.emit("recording_started", sessionId=session_id, sttDevice="cuda")
            with self._lock:
                if self._shutdown or session_id != self._session_id:
                    self.recorder.discard()
                    return
                self._recording = True
            self._update_state("recording", "録音中（右Ctrlまたは＼を離すと文字起こし）", error="")
        except Exception as exc:
            with self._lock:
                self._recording = False
            self.recorder.discard()
            self._update_state("error", "録音を開始できませんでした", error=self._friendly_error(exc))

    def _finish_recording(self, session_id: int) -> None:
        with self._lock:
            if self._shutdown or session_id != self._session_id or not self._recording:
                return
            self._recording = False
            model_name = self._stt_model
            cancel_grace_ms = self._cancel_grace_ms
            cancel_event = self._stt_cancel_event
        try:
            audio = np.asarray(self.recorder.stop(), dtype=np.float32).reshape(-1)
            self._events.emit("recording_stopped", sessionId=session_id)
        except Exception as exc:
            self._update_state("error", "録音を終了できませんでした", error=self._friendly_error(exc))
            return
        duration = float(audio.size) / float(getattr(self.recorder, "sample_rate", 16000) or 16000)
        rms = math.sqrt(float(np.mean(np.square(audio)))) if audio.size else 0.0
        if duration < self.MIN_DURATION_SECONDS or rms < self.MIN_RMS:
            self._update_state("idle", "音声が短いか無音のため送信しませんでした", error="")
            return
        self._update_state("transcribing", "文字起こし中", error="", stt_model=model_name)
        self._events.emit("transcription_started", sessionId=session_id, sttDevice="cuda")
        self.executor.submit(self._transcribe, session_id, audio, model_name, cancel_grace_ms, cancel_event)

    def _transcribe(
        self,
        session_id: int,
        audio: np.ndarray,
        model_name: str,
        cancel_grace_ms: int,
        cancel_event: threading.Event,
    ) -> None:
        try:
            with self.gpu_arbiter.acquire_stt(timeout=30.0, cancel_event=cancel_event):
                text, device = self.transcriber.transcribe(audio, model_name)
            text = str(text or "").strip()
            self._events.emit(
                "transcription_completed",
                sessionId=session_id,
                sttDevice=device,
                textLength=len(text),
                textHash=hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
                result="success",
            )
            self._events.emit("stt_device_selected", sessionId=session_id, sttDevice=device)
            with self._lock:
                stale = self._shutdown or session_id != self._session_id or not self._enabled
            if stale:
                return
            if not text:
                self._update_state(
                    "idle",
                    "文字起こし結果が空のため送信しませんでした",
                    stt_device=device,
                    stt_model=model_name,
                    error="",
                )
                return
            self._update_state(
                "pending_send",
                "ChatGPT入力欄へ反映中",
                stt_device=device,
                stt_model=model_name,
                error="",
            )
            self.client.send_conversation_event(
                "transcript",
                {
                    "sessionId": session_id,
                    "text": text,
                    "cancelGraceMs": cancel_grace_ms,
                },
            )
        except Exception as exc:
            with self._lock:
                if self._shutdown or session_id != self._session_id:
                    return
            self._update_state(
                "error",
                "文字起こしに失敗しました",
                stt_model=model_name,
                error=self._friendly_error(exc),
            )

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        message = str(exc or "").strip()
        lowered = message.lower()
        if "permission" in lowered or "access" in lowered:
            return "Windows設定でデスクトップアプリのマイクアクセスを許可してください。"
        if "device" in lowered or "portaudio" in lowered:
            return "Windowsの既定入力デバイスを選択し、マイクが他アプリで占有されていないか確認してください。"
        if "faster-whisper" in lowered or "ctranslate" in lowered or "cuda" in lowered:
            return message[:300] or "faster-whisperまたはCUDAを確認してください。"
        return message[:300] or "不明なエラー"

    def _update_state(
        self,
        phase: str,
        status_text: str,
        *,
        stt_device: str = "",
        stt_model: str | None = None,
        error: str = "",
    ) -> None:
        with self._lock:
            self._phase = phase
        try:
            self.client.update_conversation_state(
                {
                    "phase": phase,
                    "statusText": status_text,
                    "sttDevice": stt_device,
                    "sttModel": stt_model or self._stt_model,
                    "error": error,
                }
            )
        except Exception:
            pass

    def reconcile_reported_state(self, reported: dict[str, Any] | None) -> None:
        current = reported if isinstance(reported, dict) else {}
        if str(current.get("phase") or "").lower() != "idle":
            return
        with self._lock:
            if (
                self._shutdown
                or not self._enabled
                or self._phase != "idle"
                or self._model_ready_for != self._stt_model
            ):
                return
            model_name = self._stt_model
            device = self._model_device
        expected_status = "待機中（右Ctrl＋＼ 長押し）"
        if (
            str(current.get("statusText") or "") == expected_status
            and str(current.get("sttDevice") or "") == device
            and str(current.get("sttModel") or "") == model_name
        ):
            return
        self._update_state(
            "idle",
            expected_status,
            stt_device=device,
            stt_model=model_name,
            error="",
        )

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            self._enabled = False
            self._session_id += 1
            self._stt_cancel_event.set()
            was_recording = self._recording
            pause_was_active = self._pause_source_active
            self._pause_source_active = False
            self._recording = False
            self._pressed = False
            self._right_ctrl_down = False
            self._trigger_down = False
        if was_recording:
            self.recorder.discard()
        try:
            self.pause_executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            self.pause_executor.shutdown(wait=True)
        if pause_was_active:
            self._send_pause_source(False)
        for executor in (self.control_executor, self.executor):
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)


class GlobalRightCtrlHook:
    def __init__(self, callback: Callable[[int, bool], bool]) -> None:
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook: Any = None
        self._proc: Any = None
        self._started = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        if os.name != "nt" or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="local-voice-push-to-talk", daemon=True)
        self._thread.start()
        self._started.wait(timeout=3)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        low_level_proc = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p
        )
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, low_level_proc, ctypes.c_void_p, ctypes.c_uint32]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = ctypes.c_int

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_uint32),
                ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        @low_level_proc
        def hook_proc(code: int, wparam: int, lparam: int) -> int:
            if code >= 0 and lparam:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                key = int(data.vkCode)
                if key in {VK_RCONTROL, VK_OEM_102}:
                    try:
                        if int(wparam) in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                            if self.callback(key, True):
                                return 1
                        elif int(wparam) in {WM_KEYUP, WM_SYSKEYUP}:
                            if self.callback(key, False):
                                return 1
                    except Exception:
                        pass
            # 右Ctrl単独は通常操作へ流し、録音トリガーとして使った＼だけを抑止する。
            return int(user32.CallNextHookEx(self._hook, code, wparam, lparam))

        self._proc = hook_proc
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, kernel32.GetModuleHandleW(None), 0)
        self._started.set()
        if not self._hook:
            return
        message = ctypes.wintypes.MSG()
        while not self._stop.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
        self._thread_id = 0
