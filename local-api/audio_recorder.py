from __future__ import annotations

import threading
from typing import Any

import numpy as np


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
