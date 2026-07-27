from __future__ import annotations

import importlib.util
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class VoiceRuntimeError(RuntimeError):
    pass


class SoundDevicePlayer:
    """Small blocking WAV player used only by the single voice worker."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @staticmethod
    def dependency_status() -> dict[str, bool]:
        return {
            "sounddevice": importlib.util.find_spec("sounddevice") is not None,
            "soundfile": importlib.util.find_spec("soundfile") is not None,
        }

    def play(self, path: Path, volume: float, stop_event: threading.Event) -> bool:
        try:
            import sounddevice as sd
            import soundfile as sf
        except ImportError as exc:
            raise VoiceRuntimeError(
                "Local audio playback requires sounddevice and soundfile. Run setup-voice-env.cmd again."
            ) from exc

        data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
        safe_volume = min(1.0, max(0.0, float(volume)))
        data = data * safe_volume
        with self._lock:
            sd.play(data, int(sample_rate), blocking=False)
        while True:
            if stop_event.wait(0.05):
                with self._lock:
                    sd.stop()
                return False
            try:
                stream = sd.get_stream()
                if not bool(getattr(stream, "active", False)):
                    return True
            except Exception:
                # PortAudio may dispose the completed stream before get_stream.
                return True

    def stop(self) -> None:
        try:
            import sounddevice as sd

            with self._lock:
                sd.stop()
        except Exception:
            return


@dataclass
class _Job:
    kind: str
    payload: dict[str, Any]
    text: str = ""
    volume: float = 0.6
    play_local: bool = True
    existing_path: Path | None = None
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


class VoiceRuntime:
    """Owns model readiness plus one serialized synthesis/playback queue."""

    def __init__(
        self,
        *,
        prepare_fn: Callable[[], dict[str, Any]],
        synthesize_fn: Callable[[dict[str, Any]], tuple[Path, str]],
        player: Any | None = None,
        name: str = "local-voice-runtime",
    ) -> None:
        self._prepare_fn = prepare_fn
        self._synthesize_fn = synthesize_fn
        self._player = player or SoundDevicePlayer()
        self._name = name
        self._queue: queue.Queue[_Job | None] = queue.Queue(maxsize=256)
        self._lock = threading.RLock()
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._preparer: threading.Thread | None = None
        self._closed = False
        self._readiness = "not_started"
        self._readiness_detail: dict[str, Any] = {}
        self._readiness_error = ""
        self._phase = "idle"
        self._current_text = ""
        self._last_operation = ""
        self._last_error = ""
        self._last_audio_path: Path | None = None
        self._started_at = 0.0

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise VoiceRuntimeError("voice runtime is closed")
            if self._worker and self._worker.is_alive():
                return
            self._started_at = time.time()
            self._readiness = "loading"
            self._worker = threading.Thread(target=self._run_worker, name=f"{self._name}-worker", daemon=True)
            self._preparer = threading.Thread(target=self._run_prepare, name=f"{self._name}-prepare", daemon=True)
            self._worker.start()
            self._preparer.start()

    def _run_prepare(self) -> None:
        try:
            detail = self._prepare_fn() or {}
        except BaseException as exc:  # readiness must preserve the actionable original error
            with self._lock:
                self._readiness = "failed"
                self._readiness_error = str(exc)
                self._last_error = str(exc)
            self._ready_event.set()
            return
        with self._lock:
            self._readiness = "ready"
            self._readiness_detail = dict(detail)
            self._readiness_error = ""
        self._ready_event.set()

    def _wait_until_ready(self) -> None:
        self._ready_event.wait()
        with self._lock:
            if self._readiness != "ready":
                raise VoiceRuntimeError(self._readiness_error or "Irodori runtime is not ready")

    def _run_worker(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                self._run_job(job)
            except BaseException as exc:
                job.error = exc
                with self._lock:
                    self._phase = "error"
                    self._last_error = str(exc)
                    self._last_operation = f"{job.kind}:failed"
            finally:
                with self._lock:
                    if self._phase != "error":
                        self._phase = "idle"
                    self._current_text = ""
                job.done.set()
                self._queue.task_done()

    def _run_job(self, job: _Job) -> None:
        self._stop_event.clear()
        source_path: Path
        used_reference_audio = ""
        if job.kind == "synthesize":
            self._wait_until_ready()
            with self._lock:
                self._phase = "generating"
                self._current_text = job.text
                self._last_error = ""
            source_path, used_reference_audio = self._synthesize_fn(dict(job.payload))
        elif job.kind == "replay" and job.existing_path is not None:
            source_path = Path(job.existing_path)
            if not source_path.is_file():
                raise VoiceRuntimeError("replay audio file no longer exists")
        else:
            raise VoiceRuntimeError(f"unsupported voice job: {job.kind}")

        played = False
        stopped = self._stop_event.is_set()
        if job.play_local and not stopped:
            with self._lock:
                self._phase = "playing"
                self._current_text = job.text
            played = bool(self._player.play(source_path, job.volume, self._stop_event))
            stopped = not played

        with self._lock:
            self._last_audio_path = source_path
            self._last_operation = f"{job.kind}:{'stopped' if stopped else 'complete'}"
            self._last_error = ""
        job.result = {
            "path": source_path,
            "usedReferenceAudio": used_reference_audio,
            "playedLocally": bool(job.play_local),
            "playbackCompleted": played,
            "stopped": stopped,
        }

    def _submit(self, job: _Job, *, timeout: float | None = None) -> dict[str, Any]:
        self.start()
        try:
            self._queue.put(job, timeout=1.0)
        except queue.Full as exc:
            raise VoiceRuntimeError("voice runtime queue is full") from exc
        if not job.done.wait(timeout):
            raise VoiceRuntimeError("voice runtime request timed out")
        if job.error is not None:
            if isinstance(job.error, VoiceRuntimeError):
                raise job.error
            raise VoiceRuntimeError(str(job.error)) from job.error
        return dict(job.result or {})

    def synthesize(
        self,
        payload: dict[str, Any],
        *,
        text: str,
        volume: float,
        play_local: bool,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._submit(
            _Job(
                kind="synthesize",
                payload=dict(payload),
                text=str(text or ""),
                volume=float(volume),
                play_local=bool(play_local),
            ),
            timeout=timeout,
        )

    def replay(
        self,
        path: Path | None = None,
        *,
        volume: float = 0.6,
        text: str = "",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            source = Path(path) if path is not None else self._last_audio_path
        if source is None:
            raise VoiceRuntimeError("no replay audio is available")
        return self._submit(
            _Job(kind="replay", payload={}, text=text, volume=volume, play_local=True, existing_path=source),
            timeout=timeout,
        )

    def stop_playback(self) -> dict[str, Any]:
        self._stop_event.set()
        self._player.stop()
        with self._lock:
            self._last_operation = "stop:requested"
        return {"ok": True, "stopping": True}

    def snapshot(self) -> dict[str, Any]:
        dependency_status = getattr(self._player, "dependency_status", SoundDevicePlayer.dependency_status)
        dependencies = dict(dependency_status())
        with self._lock:
            readiness = self._readiness
            error = self._readiness_error or self._last_error
            return {
                "readiness": readiness,
                "ready": readiness == "ready" and all(dependencies.values()),
                "detail": dict(self._readiness_detail),
                "error": error,
                "repairRequired": bool(readiness == "failed" or not all(dependencies.values())),
                "dependencies": dependencies,
                "phase": self._phase,
                "queueSize": self._queue.qsize(),
                "currentText": self._current_text,
                "lastOperation": self._last_operation,
                "replayAvailable": bool(self._last_audio_path and self._last_audio_path.is_file()),
                "startedAt": self._started_at,
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.stop_playback()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            return
