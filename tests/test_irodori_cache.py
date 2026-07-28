from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "local-api" / "irodori_engine.py"


def load_engine_module():
    fake_ffmpeg_env = types.ModuleType("ffmpeg_env")
    fake_ffmpeg_env.configure_ffmpeg_dll_path = lambda: None

    spec = importlib.util.spec_from_file_location("irodori_engine_for_test", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load irodori_engine.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"ffmpeg_env": fake_ffmpeg_env}):
        spec.loader.exec_module(module)
    return module


engine = load_engine_module()


class FakeCuda:
    def __init__(self) -> None:
        self.empty_cache_calls = 0

    def is_available(self) -> bool:
        return True

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1


class IrodoriCudaCacheTests(unittest.TestCase):
    def test_sampling_seed_is_stable_by_default(self):
        self.assertEqual(engine._sampling_seed(None), 10)
        self.assertEqual(engine._sampling_seed("invalid"), 10)
        self.assertEqual(engine._sampling_seed(42), 42)

    def test_sampling_seed_is_random_only_when_explicitly_requested(self):
        self.assertIsNone(engine._sampling_seed("random"))

    def test_releases_unused_cache_for_cuda_runtime(self):
        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = FakeCuda()
        runtime = types.SimpleNamespace(model_device="cuda", codec_device="cuda")

        with patch.dict(sys.modules, {"torch": fake_torch}):
            engine._release_unused_cuda_cache(runtime)

        self.assertEqual(fake_torch.cuda.empty_cache_calls, 1)

    def test_skips_cache_release_for_cpu_runtime(self):
        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = FakeCuda()
        runtime = types.SimpleNamespace(model_device="cpu", codec_device="cpu")

        with patch.dict(sys.modules, {"torch": fake_torch}):
            engine._release_unused_cuda_cache(runtime)

        self.assertEqual(fake_torch.cuda.empty_cache_calls, 0)

    def test_rejects_a_reference_when_the_runtime_cannot_apply_speaker_conditioning(self):
        runtime = types.SimpleNamespace(
            model_cfg=types.SimpleNamespace(use_speaker_condition_resolved=False)
        )

        with self.assertRaisesRegex(engine.IrodoriError, "cannot apply the selected reference voice"):
            engine._require_reference_condition(runtime, "reference.wav")

    def test_accepts_a_reference_when_speaker_conditioning_is_enabled(self):
        runtime = types.SimpleNamespace(
            model_cfg=types.SimpleNamespace(use_speaker_condition_resolved=True)
        )

        self.assertTrue(engine._require_reference_condition(runtime, "reference.wav"))
        self.assertFalse(engine._require_reference_condition(runtime, None))


    def test_reference_requests_use_the_quality_sampling_profile(self):
        self.assertEqual(engine._sampling_quality({}, use_reference=False), (16, 5.0))
        self.assertEqual(engine._sampling_quality({}, use_reference=True), (32, 6.0))

    def test_reference_sampling_profile_can_be_overridden(self):
        config = {"numSteps": 20, "referenceNumSteps": 40, "cfgScaleSpeaker": 7.0}
        self.assertEqual(engine._sampling_quality(config, use_reference=False), (20, 7.0))
        self.assertEqual(engine._sampling_quality(config, use_reference=True), (40, 7.0))


if __name__ == "__main__":
    unittest.main()
