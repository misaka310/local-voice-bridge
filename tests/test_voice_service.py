from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from voice_service import VoiceServiceError, build_voice_runtime, resolve_reference_asset  # noqa: E402


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


class VoiceServiceRemoteBackendTests(unittest.TestCase):
    def test_remote_backend_sends_frontend_reference_asset_to_remote_client(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            voices = root / "voices"
            voice = voices / "asuka"
            voice.mkdir(parents=True)
            reference = voice / "voice.wav"
            reference.write_bytes(b"RIFF-asuka")
            (voice / "voice.txt").write_text("reference transcript", encoding="utf-8")
            output = root / "audio"
            generated = output / "remote.wav"
            calls: dict[str, object] = {}

            class FakeRemoteClient:
                def prepare(self):
                    calls["prepared"] = True
                    return {"backend": "remote_ssh", "sshAlias": "desk"}

                def synthesize(self, payload, *, reference_audio, reference_text=""):
                    calls["payload"] = dict(payload)
                    calls["reference_audio"] = reference_audio
                    calls["reference_text"] = reference_text
                    output.mkdir(parents=True, exist_ok=True)
                    generated.write_bytes(b"RIFF-generated")
                    return generated, "remote"

            fake_client = FakeRemoteClient()
            config = {
                "generationBackend": "remote_ssh",
                "remoteGeneration": {
                    "sshAlias": "desk",
                    "remoteRepoRoot": "C:/00_dev/17_chatgpt-local-voice-bridge",
                },
                "referenceVoicesDir": str(voices),
                "audioOutputDir": str(output),
                "irodori": {},
            }
            with (
                mock.patch("remote_generation.RemoteGenerationClient", return_value=fake_client) as client_type,
                mock.patch("voice_service.synthesize_irodori_direct") as local_synthesize,
            ):
                runtime = build_voice_runtime(config, instance_id="test-instance")
                detail = runtime._prepare_fn()
                source, used_reference = runtime._synthesize_fn(
                    {
                        "text": "hello",
                        "requestId": "job-remote",
                        "referenceVoice": "asuka",
                        "ttsProfile": "balanced",
                    }
                )

            self.assertEqual(detail["backend"], "remote_ssh")
            self.assertEqual(source, generated)
            self.assertEqual(used_reference, "remote")
            self.assertIsNone(runtime._gpu_arbiter)
            self.assertEqual(calls["reference_audio"], reference)
            self.assertEqual(calls["reference_text"], "reference transcript")
            self.assertEqual(calls["payload"]["resolvedTtsProfile"], "balanced")
            client_type.assert_called_once()
            local_synthesize.assert_not_called()

    def test_remote_backend_missing_reference_fails_before_ssh_client_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = {
                "generationBackend": "remote_ssh",
                "remoteGeneration": {
                    "sshAlias": "desk",
                    "remoteRepoRoot": "C:/00_dev/17_chatgpt-local-voice-bridge",
                },
                "referenceVoicesDir": str(root / "voices"),
                "audioOutputDir": str(root / "audio"),
                "irodori": {},
            }
            fake_client = mock.Mock()
            with mock.patch("remote_generation.RemoteGenerationClient", return_value=fake_client):
                runtime = build_voice_runtime(config, instance_id="test-instance")
                with self.assertRaisesRegex(VoiceServiceError, "reference voice not found"):
                    runtime._synthesize_fn({"text": "hello", "referenceVoice": "missing"})
            fake_client.synthesize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
