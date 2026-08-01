from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from installation_identity import gpu_mutex_names
from runtime_events import NullRuntimeEventLogger


class GpuArbiterError(RuntimeError):
    pass


class GpuArbiterTimeout(GpuArbiterError):
    pass


class GpuArbiterCancelled(GpuArbiterError):
    pass


class MutexBackend(Protocol):
    def create(self, name: str) -> Any: ...

    def wait(self, handle: Any, timeout_ms: int) -> str: ...

    def release(self, handle: Any) -> None: ...

    def close(self, handle: Any) -> None: ...


_IN_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_IN_PROCESS_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class _InProcessHandle:
    name: str
    lock: threading.Lock


class InProcessMutexBackend:
    """Injectable contract-compatible backend used by non-Windows tests."""

    def create(self, name: str) -> _InProcessHandle:
        with _IN_PROCESS_LOCKS_GUARD:
            lock = _IN_PROCESS_LOCKS.setdefault(name, threading.Lock())
        return _InProcessHandle(name=name, lock=lock)

    def wait(self, handle: _InProcessHandle, timeout_ms: int) -> str:
        timeout_ms = int(timeout_ms)
        if timeout_ms < 0:
            acquired = handle.lock.acquire()
        elif timeout_ms == 0:
            acquired = handle.lock.acquire(blocking=False)
        else:
            acquired = handle.lock.acquire(timeout=timeout_ms / 1000.0)
        return "acquired" if acquired else "timeout"

    def release(self, handle: _InProcessHandle) -> None:
        try:
            handle.lock.release()
        except RuntimeError as exc:
            raise GpuArbiterError(f"mutex is not owned: {handle.name}") from exc

    def close(self, _handle: _InProcessHandle) -> None:
        return


class WindowsMutexBackend:
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    INFINITE = 0xFFFFFFFF

    def __init__(self) -> None:
        if os.name != "nt":
            raise GpuArbiterError("Windows mutex backend requires Windows")
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        self._kernel32.ReleaseMutex.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def create(self, name: str) -> Any:
        handle = self._kernel32.CreateMutexW(None, False, str(name))
        if not handle:
            error = self._ctypes.get_last_error()
            raise GpuArbiterError(f"CreateMutexW failed for {name}: winerror={error}")
        return handle

    def wait(self, handle: Any, timeout_ms: int) -> str:
        milliseconds = self.INFINITE if int(timeout_ms) < 0 else max(0, int(timeout_ms))
        result = int(self._kernel32.WaitForSingleObject(handle, milliseconds))
        if result == self.WAIT_OBJECT_0:
            return "acquired"
        if result == self.WAIT_ABANDONED:
            return "abandoned"
        if result == self.WAIT_TIMEOUT:
            return "timeout"
        error = self._ctypes.get_last_error()
        raise GpuArbiterError(f"WaitForSingleObject failed: result={result} winerror={error}")

    def release(self, handle: Any) -> None:
        if not self._kernel32.ReleaseMutex(handle):
            error = self._ctypes.get_last_error()
            raise GpuArbiterError(f"ReleaseMutex failed: winerror={error}")

    def close(self, handle: Any) -> None:
        if handle and not self._kernel32.CloseHandle(handle):
            error = self._ctypes.get_last_error()
            raise GpuArbiterError(f"CloseHandle failed: winerror={error}")


class GpuLease:
    def __init__(
        self,
        *,
        backend: MutexBackend,
        handle: Any,
        role: str,
        abandoned: bool,
        waited_seconds: float,
        on_release: Any,
    ) -> None:
        self.role = role
        self.abandoned = bool(abandoned)
        self.waited_seconds = float(waited_seconds)
        self._backend = backend
        self._handle = handle
        self._on_release = on_release
        self._released = False
        self._lock = threading.Lock()

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._backend.release(self._handle)
            self._released = True
        self._on_release(self)

    def __enter__(self) -> GpuLease:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.release()


class GpuArbiter:
    """Cross-process STT-priority GPU turnstile.

    STT holds the Gate while waiting for the GPU mutex, preventing a new TTS job
    from overtaking it. TTS holds the Gate only while attempting a non-blocking
    GPU acquisition, then releases the Gate before backing off.
    """

    def __init__(
        self,
        instance_id: str,
        *,
        backend: MutexBackend | None = None,
        backoff_seconds: float = 0.01,
        event_logger: Any | None = None,
    ) -> None:
        self.gate_name, self.gpu_name = gpu_mutex_names(instance_id)
        self._backend: MutexBackend = backend or (
            WindowsMutexBackend() if os.name == "nt" else InProcessMutexBackend()
        )
        self._gate_handle = self._backend.create(self.gate_name)
        self._gpu_handle = self._backend.create(self.gpu_name)
        self._backoff_seconds = max(0.001, float(backoff_seconds))
        self._events = event_logger or NullRuntimeEventLogger()
        self._metrics_lock = threading.Lock()
        self._closed = False
        self._active_role = ""
        self._last_wait_seconds = 0.0
        self._abandoned_recoveries = 0
        self._timeouts = 0
        self._cancellations = 0

    @staticmethod
    def _deadline(timeout: float | None) -> float | None:
        return None if timeout is None else time.monotonic() + max(0.0, float(timeout))

    @staticmethod
    def _remaining_ms(deadline: float | None) -> int:
        if deadline is None:
            return -1
        return max(0, int((deadline - time.monotonic()) * 1000))

    @staticmethod
    def _cancelled(cancel_event: threading.Event | None) -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    def _ensure_open(self) -> None:
        if self._closed:
            raise GpuArbiterError("GPU arbiter is closed")

    def _record_acquire(self, role: str, waited_seconds: float, abandoned: bool) -> None:
        self._events.emit(
            "gpu_arbiter_acquired",
            gpuOwner=role,
            waitSeconds=waited_seconds,
            result="abandoned_recovered" if abandoned else "acquired",
        )
        if abandoned:
            self._events.emit("gpu_arbiter_abandoned_recovered", gpuOwner=role)
        with self._metrics_lock:
            self._active_role = role
            self._last_wait_seconds = waited_seconds
            if abandoned:
                self._abandoned_recoveries += 1

    def _record_release(self, lease: GpuLease) -> None:
        self._events.emit("gpu_arbiter_released", gpuOwner=lease.role, waitSeconds=lease.waited_seconds)
        with self._metrics_lock:
            if self._active_role == lease.role:
                self._active_role = ""

    def _raise_timeout(self, role: str) -> None:
        with self._metrics_lock:
            self._timeouts += 1
        raise GpuArbiterTimeout(f"timed out waiting for GPU as {role}")

    def _raise_cancelled(self, role: str) -> None:
        with self._metrics_lock:
            self._cancellations += 1
        raise GpuArbiterCancelled(f"GPU wait cancelled for {role}")

    def acquire_stt(
        self,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> GpuLease:
        self._ensure_open()
        started = time.monotonic()
        self._events.emit("gpu_arbiter_wait_started", gpuOwner="stt")
        deadline = self._deadline(timeout)
        if self._cancelled(cancel_event):
            self._raise_cancelled("stt")
        gate_result = self._backend.wait(self._gate_handle, self._remaining_ms(deadline))
        if gate_result == "timeout":
            self._raise_timeout("stt-gate")
        gate_owned = True
        abandoned = gate_result == "abandoned"
        try:
            if self._cancelled(cancel_event):
                self._raise_cancelled("stt")
            gpu_result = self._backend.wait(self._gpu_handle, self._remaining_ms(deadline))
            if gpu_result == "timeout":
                self._raise_timeout("stt-gpu")
            abandoned = abandoned or gpu_result == "abandoned"
        except BaseException:
            if gate_owned:
                self._backend.release(self._gate_handle)
            raise
        self._backend.release(self._gate_handle)
        waited = time.monotonic() - started
        self._record_acquire("stt", waited, abandoned)
        return GpuLease(
            backend=self._backend,
            handle=self._gpu_handle,
            role="stt",
            abandoned=abandoned,
            waited_seconds=waited,
            on_release=self._record_release,
        )

    def acquire_tts(
        self,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> GpuLease:
        self._ensure_open()
        started = time.monotonic()
        self._events.emit("gpu_arbiter_wait_started", gpuOwner="tts")
        deadline = self._deadline(timeout)
        abandoned = False
        while True:
            if self._cancelled(cancel_event):
                self._raise_cancelled("tts")
            remaining_ms = self._remaining_ms(deadline)
            if deadline is not None and remaining_ms <= 0:
                self._raise_timeout("tts")
            gate_slice_ms = 50 if remaining_ms < 0 else min(50, remaining_ms)
            gate_result = self._backend.wait(self._gate_handle, gate_slice_ms)
            if gate_result == "timeout":
                if cancel_event is not None:
                    cancel_event.wait(min(self._backoff_seconds, 0.05))
                else:
                    time.sleep(self._backoff_seconds)
                continue
            abandoned = abandoned or gate_result == "abandoned"
            gpu_result = "timeout"
            try:
                gpu_result = self._backend.wait(self._gpu_handle, 0)
                abandoned = abandoned or gpu_result == "abandoned"
            finally:
                self._backend.release(self._gate_handle)
            if gpu_result in {"acquired", "abandoned"}:
                waited = time.monotonic() - started
                self._record_acquire("tts", waited, abandoned)
                return GpuLease(
                    backend=self._backend,
                    handle=self._gpu_handle,
                    role="tts",
                    abandoned=abandoned,
                    waited_seconds=waited,
                    on_release=self._record_release,
                )
            if cancel_event is not None:
                cancel_event.wait(self._backoff_seconds)
            else:
                time.sleep(self._backoff_seconds)

    def snapshot(self) -> dict[str, Any]:
        with self._metrics_lock:
            return {
                "gateName": self.gate_name,
                "gpuName": self.gpu_name,
                "activeRole": self._active_role,
                "lastWaitSeconds": self._last_wait_seconds,
                "abandonedRecoveries": self._abandoned_recoveries,
                "timeouts": self._timeouts,
                "cancellations": self._cancellations,
                "closed": self._closed,
            }

    def close(self) -> None:
        with self._metrics_lock:
            if self._closed:
                return
            if self._active_role:
                raise GpuArbiterError("cannot close GPU arbiter while a lease is active")
            self._closed = True
        self._backend.close(self._gpu_handle)
        self._backend.close(self._gate_handle)
