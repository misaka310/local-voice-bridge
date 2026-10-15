from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np


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
