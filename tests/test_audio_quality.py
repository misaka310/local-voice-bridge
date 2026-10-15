from __future__ import annotations

import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from audio_quality import inspect_wav  # noqa: E402


def write_pcm16(path: Path, samples: np.ndarray, sample_rate: int = 48000) -> None:
    normalized = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    pcm = np.round(normalized * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


class AudioQualityTests(unittest.TestCase):
    def test_normal_speech_like_wave_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "normal.wav"
            sample_rate = 48000
            seconds = 0.8
            time_axis = np.arange(int(sample_rate * seconds), dtype=np.float64) / sample_rate
            envelope = np.minimum(1.0, time_axis * 8.0) * np.minimum(1.0, (seconds - time_axis) * 8.0)
            samples = envelope * (
                0.09 * np.sin(2.0 * np.pi * 220.0 * time_axis)
                + 0.04 * np.sin(2.0 * np.pi * 440.0 * time_axis)
                + 0.02 * np.sin(2.0 * np.pi * 880.0 * time_axis)
            )
            write_pcm16(path, samples, sample_rate)

            result = inspect_wav(path)

            self.assertTrue(result.passed, result.reasons)
            self.assertGreater(result.metrics["rms"], 0.005)
            self.assertLessEqual(result.metrics["clipFraction"], 0.002)

    def test_sustained_sharp_transient_is_not_misclassified_as_impulse_spikes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sharp-transient.wav"
            sample_rate = 48000
            time_axis = np.arange(sample_rate, dtype=np.float64) / sample_rate
            samples = 0.08 * np.sin(2.0 * np.pi * 220.0 * time_axis)
            burst = (time_axis >= 0.40) & (time_axis < 0.46)
            samples[burst] += 0.16 * np.sin(2.0 * np.pi * 8000.0 * time_axis[burst])
            samples[burst] += 0.112 * np.sin(2.0 * np.pi * 10000.0 * time_axis[burst])
            write_pcm16(path, samples, sample_rate)

            result = inspect_wav(path)

            self.assertTrue(result.passed, result.reasons)

    def test_silence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "silence.wav"
            write_pcm16(path, np.zeros(48000, dtype=np.float32))

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertIn("rms_too_low", result.reasons)

    def test_clipping_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "clipping.wav"
            samples = np.zeros(48000, dtype=np.float32)
            samples[:4000] = 1.0
            write_pcm16(path, samples)

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertIn("clipping", result.reasons)

    def test_dc_offset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dc.wav"
            time_axis = np.arange(48000, dtype=np.float64) / 48000.0
            samples = 0.10 + 0.03 * np.sin(2.0 * np.pi * 220.0 * time_axis)
            write_pcm16(path, samples)

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertIn("dc_offset", result.reasons)

    def test_artificial_spikes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "spikes.wav"
            time_axis = np.arange(48000, dtype=np.float64) / 48000.0
            samples = 0.05 * np.sin(2.0 * np.pi * 220.0 * time_axis)
            samples[::200] = 0.8
            write_pcm16(path, samples)

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertIn("diff_spikes", result.reasons)

    def test_repeated_step_clicks_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "step-clicks.wav"
            sample_rate = 48000
            time_axis = np.arange(sample_rate, dtype=np.float64) / sample_rate
            samples = 0.05 * np.sin(2.0 * np.pi * 220.0 * time_axis)
            blocks = (np.arange(sample_rate) // 200) % 2
            samples += np.where(blocks == 0, 0.30, -0.30)
            write_pcm16(path, samples, sample_rate)

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertIn("diff_spikes", result.reasons)

    def test_short_audio_counts_boundary_clicks_toward_diff_spike_fraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "boundary-clicks.wav"
            sample_rate = 48000
            sample_count = int(sample_rate * 0.05)
            time_axis = np.arange(sample_count, dtype=np.float64) / sample_rate
            samples = 0.05 * np.sin(2.0 * np.pi * 220.0 * time_axis)
            samples[0] = 0.8
            samples[600:1200] += 0.30
            samples[1200:1800] -= 0.30
            samples[-1] = 0.8
            write_pcm16(path, samples, sample_rate)

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertIn("diff_spikes", result.reasons)

    def test_invalid_wav_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.wav"
            path.write_bytes(b"not-a-wave")

            result = inspect_wav(path)

            self.assertFalse(result.passed)
            self.assertTrue(any("invalid WAV" in reason for reason in result.reasons))

    def test_thresholds_can_be_calibrated_without_disabling_other_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quiet.wav"
            time_axis = np.arange(48000, dtype=np.float64) / 48000.0
            samples = 0.002 * np.sin(2.0 * np.pi * 220.0 * time_axis)
            write_pcm16(path, samples)

            default_result = inspect_wav(path)
            calibrated_result = inspect_wav(path, {"minRms": 0.001})

            self.assertIn("rms_too_low", default_result.reasons)
            self.assertTrue(calibrated_result.passed, calibrated_result.reasons)


if __name__ == "__main__":
    unittest.main()
