from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from remote_generation_protocol import PROTOCOL_VERSION, read_frame, write_frame  # noqa: E402
from remote_generation_worker import run_worker  # noqa: E402


class RemoteGenerationWorkerTests(unittest.TestCase):
    def _request_stream(self, header: dict, payload: bytes = b"") -> io.BytesIO:
        stream = io.BytesIO()
        write_frame(stream, {"version": PROTOCOL_VERSION, **header}, payload)
        stream.seek(0)
        return stream

    def _read_response(self, stream: io.BytesIO) -> tuple[dict, bytes]:
        stream.seek(0)
        return read_frame(stream, max_payload_bytes=64 * 1024 * 1024)

    def test_prepare_returns_runtime_metadata_without_starting_server(self) -> None:
        stdin = self._request_stream({"op": "prepare", "requestId": "prep-1"})
        stdout = io.BytesIO()
        config = {"models": {"irodori-v3": {"runtime": "irodori_direct"}}, "irodori": {}}

        with patch(
            "remote_generation_worker.prepare_irodori_direct",
            return_value={"modelDevice": "cuda:0", "modelPrecision": "bf16"},
        ) as prepare:
            result = run_worker(stdin, stdout, config_loader=lambda: config)

        header, payload = self._read_response(stdout)
        self.assertEqual(result, 0)
        self.assertTrue(header["ok"])
        self.assertEqual(header["op"], "prepare")
        self.assertEqual(header["requestId"], "prep-1")
        self.assertEqual(header["runtime"]["modelDevice"], "cuda:0")
        self.assertEqual(payload, b"")
        prepare.assert_called_once()

    def test_synthesize_uses_request_scoped_reference_asset_and_cleans_it(self) -> None:
        reference = b"RIFF-reference"
        stdin = self._request_stream(
            {
                "op": "synthesize",
                "requestId": "job-1",
                "text": "hello",
                "ttsProfile": "balanced",
                "live": False,
                "voicePrompt": "calm",
                "referenceText": "reference transcript",
                "hasReference": True,
            },
            reference,
        )
        stdout = io.BytesIO()
        config = {"models": {"irodori-v3": {}}, "irodori": {}}
        observed: dict[str, object] = {}

        def fake_synthesize(**kwargs):
            raw_config = kwargs["raw_config"]
            voice = raw_config["referenceVoices"]["remote-request"]
            reference_path = Path(voice["referenceAudioPath"])
            reference_text_path = Path(voice["referenceTextPath"])
            observed["temp_root"] = reference_path.parent
            observed["reference_bytes"] = reference_path.read_bytes()
            observed["reference_text"] = reference_text_path.read_text(encoding="utf-8")
            observed["reference_voice"] = kwargs["reference_voice"]
            observed["profile_name"] = kwargs["profile_name"]
            observed["live"] = kwargs["live"]
            observed["voice_prompt"] = kwargs["voice_prompt"]
            output = Path(kwargs["output_dir"]) / "generated.wav"
            output.write_bytes(b"RIFF-generated")
            return output, str(reference_path)

        with patch("remote_generation_worker.synthesize_irodori_direct", side_effect=fake_synthesize):
            result = run_worker(stdin, stdout, config_loader=lambda: config)

        header, payload = self._read_response(stdout)
        self.assertEqual(result, 0)
        self.assertTrue(header["ok"])
        self.assertEqual(header["requestId"], "job-1")
        self.assertTrue(header["usedReferenceAudio"])
        self.assertEqual(header["ttsProfile"], "balanced")
        self.assertEqual(payload, b"RIFF-generated")
        self.assertEqual(observed["reference_bytes"], reference)
        self.assertEqual(observed["reference_text"], "reference transcript")
        self.assertEqual(observed["reference_voice"], "remote-request")
        self.assertEqual(observed["profile_name"], "balanced")
        self.assertFalse(observed["live"])
        self.assertEqual(observed["voice_prompt"], "calm")
        self.assertFalse(Path(observed["temp_root"]).exists())

    def test_synthesize_without_reference_does_not_create_reference_entry(self) -> None:
        stdin = self._request_stream(
            {"op": "synthesize", "requestId": "job-2", "text": "hello", "hasReference": False}
        )
        stdout = io.BytesIO()
        config = {"models": {"irodori-v3": {}}, "irodori": {}}

        def fake_synthesize(**kwargs):
            self.assertNotIn("remote-request", kwargs["raw_config"].get("referenceVoices", {}))
            self.assertIsNone(kwargs["reference_voice"])
            output = Path(kwargs["output_dir"]) / "generated.wav"
            output.write_bytes(b"RIFF-no-ref")
            return output, ""

        with patch("remote_generation_worker.synthesize_irodori_direct", side_effect=fake_synthesize):
            result = run_worker(stdin, stdout, config_loader=lambda: config)

        header, payload = self._read_response(stdout)
        self.assertEqual(result, 0)
        self.assertTrue(header["ok"])
        self.assertFalse(header["usedReferenceAudio"])
        self.assertEqual(payload, b"RIFF-no-ref")

    def test_generation_failure_returns_bounded_error_and_cleans_temp_files(self) -> None:
        stdin = self._request_stream(
            {
                "op": "synthesize",
                "requestId": "job-fail",
                "text": "hello",
                "hasReference": True,
            },
            b"RIFF-reference",
        )
        stdout = io.BytesIO()
        config = {"models": {"irodori-v3": {}}, "irodori": {}}
        observed: dict[str, Path] = {}

        def fake_synthesize(**kwargs):
            reference_path = Path(kwargs["raw_config"]["referenceVoices"]["remote-request"]["referenceAudioPath"])
            observed["temp_root"] = reference_path.parent
            raise RuntimeError("C:/secret/backend/path/model.safetensors exploded")

        with patch("remote_generation_worker.synthesize_irodori_direct", side_effect=fake_synthesize):
            result = run_worker(stdin, stdout, config_loader=lambda: config)

        header, payload = self._read_response(stdout)
        self.assertEqual(result, 0)
        self.assertFalse(header["ok"])
        self.assertEqual(header["errorCode"], "generation_failed")
        self.assertLessEqual(len(header["error"]), 512)
        self.assertNotIn("C:/secret", header["error"])
        self.assertEqual(payload, b"")
        self.assertFalse(observed["temp_root"].exists())

    def test_unknown_operation_returns_protocol_error_and_worker_continues_to_eof(self) -> None:
        stdin = self._request_stream({"op": "delete-everything", "requestId": "bad-1"})
        stdout = io.BytesIO()

        result = run_worker(stdin, stdout, config_loader=lambda: {"models": {}, "irodori": {}})

        header, payload = self._read_response(stdout)
        self.assertEqual(result, 0)
        self.assertFalse(header["ok"])
        self.assertEqual(header["errorCode"], "unsupported_operation")
        self.assertEqual(payload, b"")


if __name__ == "__main__":
    unittest.main()
