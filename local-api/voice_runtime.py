from __future__ import annotations

import hashlib
import importlib.util
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from conversation_turn import ConversationTurn, TurnIdentity
from gpu_arbiter import GpuArbiter
from runtime_events import NullRuntimeEventLogger, event_fields
from voice_job_queue import VoiceJobQueue, VoiceJobQueueClosed, VoiceJobQueueFull


class VoiceRuntimeError(RuntimeError):
    pass


class SoundDevicePlayer:
    """Blocking WAV player. It runs only on the dedicated playback worker."""

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
    identity: TurnIdentity
    text: str = ""
    volume: float = 0.6
    play_local: bool = True
    existing_path: Path | None = None
    generated_path: Path | None = None
    used_reference_audio: str = ""
    quality_result: dict[str, Any] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None
    _completion_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class VoiceRuntime:
    """Owns model readiness, serialized GPU synthesis and independent playback."""

    def __init__(
        self,
        *,
        prepare_fn: Callable[[], dict[str, Any]],
        synthesize_fn: Callable[[dict[str, Any]], tuple[Path, str]],
        player: Any | None = None,
        name: str = "local-voice-runtime",
        turn_state: ConversationTurn | None = None,
        gpu_arbiter: GpuArbiter | None = None,
        quality_check_fn: Callable[[Path], dict[str, Any]] | None = None,
        event_logger: Any | None = None,
    ) -> None:
        self._prepare_fn = prepare_fn
        self._synthesize_fn = synthesize_fn
        self._quality_check_fn = quality_check_fn
        self._events = event_logger or NullRuntimeEventLogger()
        self._player = player or SoundDevicePlayer()
        self._name = name
        self._gpu_arbiter = gpu_arbiter
        self._generation_queue: VoiceJobQueue[_Job] = VoiceJobQueue(maxsize=256)
        self._playback_queue: VoiceJobQueue[_Job] = VoiceJobQueue(maxsize=256)
        self._turn_state = turn_state or ConversationTurn()
        self._lock = threading.RLock()
        self._ready_event = threading.Event()
        self._generation_worker: threading.Thread | None = None
        self._playback_worker: threading.Thread | None = None
        self._preparer: threading.Thread | None = None
        self._closed = False
        self._readiness = "not_started"
        self._readiness_detail: dict[str, Any] = {}
        self._readiness_error = ""
        self._generation_phase = "idle"
        self._playback_phase = "idle"
        self._current_generation: _Job | None = None
        self._current_playback: _Job | None = None
        self._last_operation = ""
        self._last_error = ""
        self._last_audio_path: Path | None = None
        self._started_at = 0.0

    def start(self) -> None:
        with self._lock:
            if self._closed:
                raise VoiceRuntimeError("voice runtime is closed")
            if self._generation_worker and self._generation_worker.is_alive():
                return
            self._started_at = time.time()
            self._readiness = "loading"
            self._generation_worker = threading.Thread(
                target=self._run_generation_worker,
                name=f"{self._name}-generation",
                daemon=True,
            )
            self._playback_worker = threading.Thread(
                target=self._run_playback_worker,
                name=f"{self._name}-playback",
                daemon=True,
            )
            self._preparer = threading.Thread(target=self._run_prepare, name=f"{self._name}-prepare", daemon=True)
            self._generation_worker.start()
            self._playback_worker.start()
            self._preparer.start()

    def _run_prepare(self) -> None:
        try:
            detail = self._prepare_fn() or {}
        except BaseException as exc:
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

    def _current_identity(self) -> TurnIdentity:
        return self._turn_state.snapshot().identity

    def _emit(self, event: str, job: _Job | None = None, **extra: Any) -> None:
        identity = job.identity if job is not None else self._current_identity()
        if job is not None:
            extra.setdefault("textLength", len(job.text))
            extra.setdefault("textHash", hashlib.sha256(job.text.encode("utf-8")).hexdigest() if job.text else "")
            extra.setdefault(
                "ttsProfile",
                str(job.payload.get("resolvedTtsProfile") or job.payload.get("ttsProfile") or ""),
            )
        try:
            self._events.emit(event, **event_fields(identity, **extra))
        except Exception:
            return

    def adopt_live_turn(self, payload: dict[str, Any]) -> TurnIdentity:
        return self._turn_state.adopt_turn(
            str(payload.get("turnId") or ""),
            int(payload.get("cancelEpoch", 0)),
            {
                "submissionId": payload.get("submissionId"),
                "tabId": payload.get("tabId"),
                "pageInstanceId": payload.get("pageInstanceId"),
                "conversationKey": payload.get("conversationKey"),
            },
        )

    def bind_live_submission(self, payload: dict[str, Any]) -> TurnIdentity:
        return self._turn_state.bind_submission(
            str(payload.get("submissionId") or ""),
            owner={
                "submissionId": payload.get("submissionId"),
                "tabId": payload.get("tabId"),
                "pageInstanceId": payload.get("pageInstanceId"),
                "conversationKey": payload.get("conversationKey"),
            },
        )

    def live_identity(self) -> TurnIdentity:
        return self._current_identity()

    def _is_current(self, job: _Job) -> bool:
        return self._turn_state.is_current(job.identity)

    @staticmethod
    def _safe_delete(path: Path | None) -> None:
        if path is None:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            return

    def _complete_job(
        self,
        job: _Job,
        *,
        stopped: bool,
        played: bool = False,
        error: BaseException | None = None,
        update_replay: bool = False,
    ) -> None:
        with job._completion_lock:
            if job.done.is_set():
                return
            path = job.generated_path or job.existing_path
            if error is not None:
                job.error = error
            else:
                job.result = {
                    "path": path,
                    "usedReferenceAudio": job.used_reference_audio,
                    "ttsProfile": str(
                        job.payload.get("resolvedTtsProfile") or job.payload.get("ttsProfile") or ""
                    ),
                    "audioQuality": dict(job.quality_result),
                    "playedLocally": bool(job.play_local),
                    "playbackCompleted": bool(played),
                    "stopped": bool(stopped),
                }
            current = self._is_current(job)
            with self._lock:
                if current and error is None:
                    if update_replay and path is not None:
                        self._last_audio_path = Path(path)
                    self._last_operation = f"{job.kind}:{'stopped' if stopped else 'complete'}"
                    self._last_error = ""
                elif current and error is not None:
                    self._last_error = str(error)
                    self._last_operation = f"{job.kind}:failed"
            job.done.set()

    def _discard_stale(self, job: _Job) -> None:
        self._emit("stale_output_discarded", job, reason="turn_or_epoch_changed")
        if job.kind == "synthesize":
            self._safe_delete(job.generated_path)
        self._complete_job(job, stopped=True, played=False, update_replay=False)

    def _synthesize_with_gpu(self, job: _Job) -> tuple[Path, str]:
        if self._gpu_arbiter is None:
            return self._synthesize_fn(dict(job.payload))
        with self._gpu_arbiter.acquire_tts(cancel_event=job.stop_event):
            return self._synthesize_fn(dict(job.payload))

    def _check_audio_quality(self, job: _Job) -> None:
        if self._quality_check_fn is None or job.generated_path is None:
            job.quality_result = {"passed": True, "reasons": [], "metrics": {}}
            return
        try:
            result = dict(self._quality_check_fn(job.generated_path) or {})
        except BaseException as exc:
            raise VoiceRuntimeError(f"audio quality inspection failed: {exc}") from exc
        passed = bool(result.get("passed"))
        reasons = [str(reason) for reason in result.get("reasons", [])]
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        job.quality_result = {
            "passed": passed,
            "reasons": reasons,
            "metrics": dict(metrics),
        }
        if not passed:
            detail = ", ".join(reasons) or "unknown_quality_failure"
            raise VoiceRuntimeError(f"audio quality gate rejected: {detail}")

    def _run_generation_worker(self) -> None:
        while True:
            try:
                job = self._generation_queue.take()
            except VoiceJobQueueClosed:
                return
            if job is None:
                continue
            if not self._is_current(job) or job.stop_event.is_set():
                self._discard_stale(job)
                continue
            with self._lock:
                self._current_generation = job
                self._generation_phase = "generating"
                self._last_error = ""
            generation_started = time.perf_counter()
            self._emit("tts_generation_started", job, gpuOwner="tts")
            try:
                self._wait_until_ready()
                identity = self._turn_state.begin_generation()
                job.identity = replace(job.identity, generation_id=identity.generation_id)
                source_path, used_reference_audio = self._synthesize_with_gpu(job)
                job.generated_path = Path(source_path)
                job.used_reference_audio = str(used_reference_audio or "")
                self._emit(
                    "tts_generation_completed",
                    job,
                    durationSeconds=time.perf_counter() - generation_started,
                    result="success",
                )
                self._check_audio_quality(job)
                self._emit(
                    "quality_gate_completed",
                    job,
                    passed=bool(job.quality_result.get("passed")),
                    reasons=job.quality_result.get("reasons", []),
                )
                if not self._is_current(job) or job.stop_event.is_set():
                    self._discard_stale(job)
                elif job.play_local:
                    try:
                        self._playback_queue.enqueue(job, timeout=1.0)
                    except (VoiceJobQueueClosed, VoiceJobQueueFull) as exc:
                        self._safe_delete(job.generated_path)
                        self._complete_job(job, stopped=False, error=VoiceRuntimeError(str(exc)))
                else:
                    self._complete_job(job, stopped=False, played=False, update_replay=True)
            except BaseException as exc:
                if not self._is_current(job) or job.stop_event.is_set():
                    self._discard_stale(job)
                else:
                    if job.kind == "synthesize":
                        self._safe_delete(job.generated_path)
                    error = exc if isinstance(exc, VoiceRuntimeError) else VoiceRuntimeError(str(exc))
                    self._complete_job(job, stopped=False, error=error)
            finally:
                with self._lock:
                    if self._current_generation is job:
                        self._current_generation = None
                    self._generation_phase = "idle"

    def _run_playback_worker(self) -> None:
        while True:
            try:
                job = self._playback_queue.take()
            except VoiceJobQueueClosed:
                return
            if job is None:
                continue
            if not self._is_current(job) or job.stop_event.is_set():
                self._discard_stale(job)
                continue
            source_path = job.generated_path or job.existing_path
            if source_path is None or not Path(source_path).is_file():
                self._complete_job(job, stopped=False, error=VoiceRuntimeError("replay audio file no longer exists"))
                continue
            identity = self._turn_state.begin_playback()
            job.identity = replace(job.identity, playback_id=identity.playback_id)
            with self._lock:
                self._current_playback = job
                self._playback_phase = "playing"
                self._last_error = ""
            playback_started = time.perf_counter()
            self._emit("playback_started", job)
            try:
                played = bool(self._player.play(Path(source_path), job.volume, job.stop_event))
                self._emit(
                    "playback_completed",
                    job,
                    durationSeconds=time.perf_counter() - playback_started,
                    result="success" if played else "stopped",
                )
                if not self._is_current(job) or job.stop_event.is_set() or not played:
                    if job.kind == "synthesize" and not self._is_current(job):
                        self._safe_delete(job.generated_path)
                    self._complete_job(job, stopped=True, played=False, update_replay=False)
                else:
                    self._complete_job(job, stopped=False, played=True, update_replay=True)
            except BaseException as exc:
                if not self._is_current(job) or job.stop_event.is_set():
                    self._discard_stale(job)
                else:
                    error = exc if isinstance(exc, VoiceRuntimeError) else VoiceRuntimeError(str(exc))
                    self._complete_job(job, stopped=False, error=error)
            finally:
                with self._lock:
                    if self._current_playback is job:
                        self._current_playback = None
                    self._playback_phase = "idle"

    def _new_job(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        text: str,
        volume: float,
        play_local: bool,
        existing_path: Path | None = None,
    ) -> _Job:
        return _Job(
            kind=kind,
            payload=dict(payload),
            identity=self._current_identity(),
            text=str(text or ""),
            volume=float(volume),
            play_local=bool(play_local),
            existing_path=Path(existing_path) if existing_path is not None else None,
        )

    def _wait_for_job(self, job: _Job, *, timeout: float | None = None) -> dict[str, Any]:
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
        self.start()
        job = self._new_job(
            kind="synthesize",
            payload=payload,
            text=text,
            volume=volume,
            play_local=play_local,
        )
        try:
            self._generation_queue.enqueue(job, timeout=1.0)
            self._emit("tts_enqueued", job, capacity=256 - self._generation_queue.qsize())
        except (VoiceJobQueueClosed, VoiceJobQueueFull) as exc:
            raise VoiceRuntimeError(str(exc)) from exc
        return self._wait_for_job(job, timeout=timeout)

    def enqueue_live(
        self,
        payload: dict[str, Any],
        *,
        text: str,
        volume: float,
        identity: TurnIdentity,
    ) -> _Job:
        """Accept a Live chunk without waiting for generation or playback completion."""

        self.start()
        job = _Job(
            kind="synthesize",
            payload=dict(payload),
            identity=identity,
            text=str(text or ""),
            volume=float(volume),
            play_local=True,
        )
        try:
            self._generation_queue.enqueue(job, timeout=0.0)
            self._emit("tts_enqueued", job, capacity=256 - self._generation_queue.qsize())
        except (VoiceJobQueueClosed, VoiceJobQueueFull) as exc:
            raise VoiceRuntimeError(str(exc)) from exc
        return job

    def replay(
        self,
        path: Path | None = None,
        *,
        volume: float = 0.6,
        text: str = "",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.start()
        with self._lock:
            source = Path(path) if path is not None else self._last_audio_path
        if source is None:
            raise VoiceRuntimeError("no replay audio is available")
        job = self._new_job(
            kind="replay",
            payload={},
            text=text,
            volume=volume,
            play_local=True,
            existing_path=source,
        )
        try:
            self._playback_queue.enqueue(job, timeout=1.0)
        except (VoiceJobQueueClosed, VoiceJobQueueFull) as exc:
            raise VoiceRuntimeError(str(exc)) from exc
        return self._wait_for_job(job, timeout=timeout)

    def interrupt(self, reason: str = "stop", *, requested_epoch: int | None = None) -> dict[str, Any]:
        snapshot = self._turn_state.interrupt(reason, requested_epoch=requested_epoch)
        try:
            self._events.emit("stop_command_sent", **event_fields(snapshot.identity, reason=reason))
        except Exception:
            pass
        with self._lock:
            current_generation = self._current_generation
            current_playback = self._current_playback
        if current_generation is not None:
            current_generation.stop_event.set()
        if current_playback is not None:
            current_playback.stop_event.set()
        self._player.stop()

        def stale(job: _Job) -> bool:
            return job.identity.cancel_epoch < snapshot.identity.cancel_epoch

        removed = self._generation_queue.invalidate(stale) + self._playback_queue.invalidate(stale)
        for job in removed:
            job.stop_event.set()
            self._discard_stale(job)
        with self._lock:
            self._last_operation = "stop:requested"
        return {
            "ok": True,
            "stopping": True,
            "cancelEpoch": snapshot.identity.cancel_epoch,
            "invalidatedJobs": len(removed),
        }

    def stop_playback(self) -> dict[str, Any]:
        return self.interrupt("stop")

    def snapshot(self) -> dict[str, Any]:
        dependency_status = getattr(self._player, "dependency_status", SoundDevicePlayer.dependency_status)
        dependencies = dict(dependency_status())
        turn = self._turn_state.snapshot()
        with self._lock:
            readiness = self._readiness
            error = self._readiness_error or self._last_error
            generation_text = self._current_generation.text if self._current_generation is not None else ""
            playback_text = self._current_playback.text if self._current_playback is not None else ""
            if self._playback_phase == "playing":
                phase = "playing"
            elif self._generation_phase == "generating":
                phase = "generating"
            elif error:
                phase = "error"
            else:
                phase = "idle"
            return {
                "readiness": readiness,
                "ready": readiness == "ready" and all(dependencies.values()),
                "detail": dict(self._readiness_detail),
                "error": error,
                "repairRequired": bool(readiness == "failed" or not all(dependencies.values())),
                "dependencies": dependencies,
                "phase": phase,
                "generationPhase": self._generation_phase,
                "playbackPhase": self._playback_phase,
                "queueSize": self._generation_queue.qsize() + self._playback_queue.qsize(),
                "generationQueueSize": self._generation_queue.qsize(),
                "playbackQueueSize": self._playback_queue.qsize(),
                "currentText": playback_text or generation_text,
                "lastOperation": self._last_operation,
                "replayAvailable": bool(self._last_audio_path and self._last_audio_path.is_file()),
                "startedAt": self._started_at,
                "turnId": turn.identity.turn_id,
                "cancelEpoch": turn.identity.cancel_epoch,
                "gpuArbiter": self._gpu_arbiter.snapshot() if self._gpu_arbiter is not None else {},
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.interrupt("close")
        removed = self._generation_queue.close() + self._playback_queue.close()
        for job in removed:
            self._discard_stale(job)
