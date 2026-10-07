from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from voice_service import VoiceServiceError, resolve_reference_asset  # noqa: E402


class VoiceServiceReferenceAssetTests(unittest.TestCase):
    def test_resolves_reference_audio_and_text_from_frontend_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            voices = Path(temp_dir) / "voices"
            voice = voices / "asuka"
            voice.mkdir(parents=True)
            audio = voice / "voice.wav"
            audio.write_bytes(b"RIFF-asuka")
            (voice / "voice.txt").write_text("reference transcript", encoding="utf-8")

            asset = resolve_reference_asset({"referenceVoicesDir": str(voices)}, "asuka")

            self.assertIsNotNone(asset)
            assert asset is not None
            self.assertEqual(asset.voice_id, "asuka")
            self.assertEqual(asset.audio_path, audio)
            self.assertEqual(asset.reference_text, "reference transcript")

    def test_empty_reference_returns_none(self) -> None:
        self.assertIsNone(resolve_reference_asset({}, ""))

    def test_missing_reference_fails_before_remote_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(VoiceServiceError, "reference voice not found"):
                resolve_reference_asset({"referenceVoicesDir": str(Path(temp_dir) / "voices")}, "missing")

    def test_configured_reference_audio_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "custom.wav"
            text = root / "custom.txt"
            audio.write_bytes(b"RIFF-custom")
            text.write_text("custom transcript", encoding="utf-8")
            config = {
                "referenceVoices": {
                    "custom": {
                        "label": "custom",
                        "referenceAudioPath": str(audio),
                        "referenceTextPath": str(text),
                    }
                }
            }

            asset = resolve_reference_asset(config, "custom")

            self.assertIsNotNone(asset)
            assert asset is not None
            self.assertEqual(asset.audio_path, audio)
            self.assertEqual(asset.reference_text, "custom transcript")


if __name__ == "__main__":
    unittest.main()
