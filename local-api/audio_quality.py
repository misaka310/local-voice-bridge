from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np


DEFAULT_THRESHOLDS: dict[str, float] = {
    "minRms": 0.005,
    "maxClipFraction": 0.002,
    "maxAbsDcOffset": 0.03,
    "maxDiffSpikeFraction": 0.002,
    "maxHighBandRatio": 0.08,
    "maxSpectralFlatness": 0.35,
    "minDurationSeconds": 0.05,
    "maxDurationSeconds": 120.0,
}


@dataclass(frozen=True)
class AudioQualityResult:
    passed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def _read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as handle:
            channels = int(handle.getnchannels())
            sample_width = int(handle.getsampwidth())
            sample_rate = int(handle.getframerate())
            frame_count = int(handle.getnframes())
            raw = handle.readframes(frame_count)
    except (OSError, EOFError, wave.Error) as exc:
        raise ValueError(f"invalid WAV: {exc}") from exc
    if channels <= 0 or sample_rate <= 0 or frame_count <= 0:
        raise ValueError("invalid WAV metadata")
    dtype_map = {1: np.uint8, 2: np.int16, 3: None, 4: np.int32}
    if sample_width not in dtype_map:
        raise ValueError(f"unsupported WAV sample width: {sample_width}")
    if sample_width == 3:
        bytes_array = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        signed = (
            bytes_array[:, 0].astype(np.int32)
            | (bytes_array[:, 1].astype(np.int32) << 8)
            | (bytes_array[:, 2].astype(np.int32) << 16)
        )
        signed = np.where(signed & 0x800000, signed - 0x1000000, signed)
        samples = signed.astype(np.float32) / 8388608.0
    else:
        samples = np.frombuffer(raw, dtype=dtype_map[sample_width])
        if sample_width == 1:
            samples = (samples.astype(np.float32) - 128.0) / 128.0
        elif sample_width == 2:
            samples = samples.astype(np.float32) / 32768.0
        else:
            samples = samples.astype(np.float32) / 2147483648.0
    if samples.size % channels:
        raise ValueError("WAV sample count is not divisible by channel count")
    samples = samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)
    if not samples.size or not np.all(np.isfinite(samples)):
        raise ValueError("WAV contains no finite samples")
    return samples.astype(np.float32, copy=False), sample_rate


def _spectral_metrics(samples: np.ndarray, sample_rate: int) -> tuple[float, float]:
    if samples.size < 16:
        return 0.0, 0.0
    centered = samples.astype(np.float64) - float(np.mean(samples))
    windowed = centered * np.hanning(centered.size)
    power = np.square(np.abs(np.fft.rfft(windowed)))
    if power.size <= 1 or float(np.sum(power)) <= 1e-20:
        return 0.0, 0.0
    frequencies = np.fft.rfftfreq(windowed.size, d=1.0 / float(sample_rate))
    total = float(np.sum(power[1:]))
    high = float(np.sum(power[frequencies >= 12000.0])) if sample_rate > 24000 else 0.0
    positive = power[1:][power[1:] > 1e-20]
    flatness = 0.0
    if positive.size:
        flatness = float(math.exp(float(np.mean(np.log(positive)))) / float(np.mean(positive)))
    return high / total if total > 0 else 0.0, flatness


def inspect_wav(
    path: Path | str,
    thresholds: Mapping[str, Any] | None = None,
) -> AudioQualityResult:
    source = Path(path)
    configured = dict(DEFAULT_THRESHOLDS)
    for key, value in dict(thresholds or {}).items():
        if key in configured:
            configured[key] = float(value)
    reasons: list[str] = []
    try:
        samples, sample_rate = _read_pcm_wav(source)
    except ValueError as exc:
        return AudioQualityResult(False, (str(exc),), {})

    duration = float(samples.size) / float(sample_rate)
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    clip_fraction = float(np.mean(np.abs(samples) >= 0.999))
    dc_offset = float(np.mean(samples, dtype=np.float64))
    differences = np.abs(np.diff(samples.astype(np.float64)))
    if differences.size:
        median_diff = float(np.median(differences))
        spike_threshold = max(0.25, median_diff * 12.0)
        diff_spike_fraction = float(np.mean(differences >= spike_threshold))
    else:
        diff_spike_fraction = 0.0
    high_band_ratio, spectral_flatness = _spectral_metrics(samples, sample_rate)
    metrics = {
        "durationSeconds": duration,
        "sampleRate": float(sample_rate),
        "rms": rms,
        "clipFraction": clip_fraction,
        "dcOffset": dc_offset,
        "diffSpikeFraction": diff_spike_fraction,
        "highBandRatio": high_band_ratio,
        "spectralFlatness": spectral_flatness,
    }
    if duration < configured["minDurationSeconds"] or duration > configured["maxDurationSeconds"]:
        reasons.append("duration_out_of_range")
    if rms < configured["minRms"]:
        reasons.append("rms_too_low")
    if clip_fraction > configured["maxClipFraction"]:
        reasons.append("clipping")
    if abs(dc_offset) > configured["maxAbsDcOffset"]:
        reasons.append("dc_offset")
    if diff_spike_fraction > configured["maxDiffSpikeFraction"]:
        reasons.append("diff_spikes")
    if high_band_ratio > configured["maxHighBandRatio"]:
        reasons.append("high_band_noise")
    if spectral_flatness > configured["maxSpectralFlatness"]:
        reasons.append("spectral_flatness")
    return AudioQualityResult(not reasons, tuple(reasons), metrics)


def inspect_wav_dict(path: Path | str, thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return inspect_wav(path, thresholds).as_dict()
