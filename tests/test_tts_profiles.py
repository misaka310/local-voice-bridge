from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from tts_profiles import (  # noqa: E402
    TtsProfileError,
    apply_profile,
    normalize_persisted_profile,
    resolve_tts_profile,
)


class TtsProfileTests(unittest.TestCase):
    def test_only_documented_profiles_resolve(self) -> None:
        expected = {"speed": 12, "balanced": 16, "bridge": 32}
        for name, steps in expected.items():
            with self.subTest(name=name):
                profile = resolve_tts_profile(name)
                self.assertEqual(profile.num_steps, steps)

    def test_live_defaults_to_speed_and_legacy_preserves_reference_quality(self) -> None:
        self.assertEqual(resolve_tts_profile(live=True).name, "speed")
        self.assertEqual(resolve_tts_profile(use_reference=True).name, "bridge")
        self.assertEqual(resolve_tts_profile(use_reference=False).name, "balanced")

    def test_unsupported_profile_and_step_counts_are_rejected(self) -> None:
        with self.assertRaises(TtsProfileError):
            resolve_tts_profile("candidate8")
        for steps in (2, 3, 4, 6, 8, 20, 40):
            with self.subTest(steps=steps):
                with self.assertRaises(TtsProfileError):
                    resolve_tts_profile("speed", legacy_settings={"numSteps": steps})

    def test_profile_application_overrides_reference_step_branch(self) -> None:
        config = apply_profile({"numSteps": 16, "referenceNumSteps": 32}, resolve_tts_profile("speed"))
        self.assertEqual(config["numSteps"], 12)
        self.assertEqual(config["referenceNumSteps"], 12)
        self.assertEqual(config["referenceMode"], "latent")
        self.assertFalse(config["syncStages"])
        self.assertFalse(config["watermark"])
        self.assertFalse(config["releaseUnusedCudaCache"])

    def test_invalid_persisted_profile_normalizes_without_arbitrary_steps(self) -> None:
        self.assertEqual(normalize_persisted_profile("candidate6", use_reference=True), "bridge")
        self.assertEqual(normalize_persisted_profile("candidate6", use_reference=False), "balanced")


if __name__ == "__main__":
    unittest.main()
