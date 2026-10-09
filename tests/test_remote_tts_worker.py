from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import wave
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

import voice_service  # noqa: E402


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes((b"\x00\x00" * 2400))
    return buffer.getvalue()


class _WorkerHandler(BaseHTTPRequestHandler):
    audio = wav_bytes()
    audio_url = "/audio/test.wav"
    last_speak: dict[str, object] = {}
    get_paths: list[str] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        type(self).get_paths.append(self.path)
        if self.path == "/health":
            body = json.dumps({"ok": True, "role": "worker"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/http-error":
            self.send_response(500, r"C:\worker\private\api.log")
            self.end_headers()
            return
        if self.path == "/audio/redirect":
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://localhost:{self.server.server_address[1]}/audio/test.wav",
            )
            self.end_headers()
            return
        if self.path == "/audio/test.wav":
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(self.audio)))
            self.end_headers()
            self.wfile.write(self.audio)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/v1/speak":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        type(self).last_speak = json.loads(self.rfile.read(length).decode("utf-8"))
        body = json.dumps(
            {
                "ok": True,
                "result": {
                    "ok": True,
                    "audioUrl": type(self).audio_url,
                },
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RemoteTtsWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        _WorkerHandler.get_paths = []
        _WorkerHandler.last_speak = {}
        _WorkerHandler.audio_url = "/audio/test.wav"
        _WorkerHandler.audio = wav_bytes()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _WorkerHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def test_prepare_and_synthesize_through_worker_gateway(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "audioOutputDir": temp_dir,
                "remoteTts": {
                    "enabled": True,
                    "baseUrl": self.base_url,
                    "healthPath": "/health",
                    "speakPath": "/v1/speak",
                    "model": "irodori_v3_low_latency",
                },
            }
            readiness = voice_service.prepare_remote_tts(config)
            self.assertEqual(readiness["runtime"], "remote_worker")
            path, used_reference = voice_service.synthesize_remote_tts(
                config=config,
                payload={
                    "requestId": "remote-test-1",
                    "text": "テストです",
                    "speedScale": 1.05,
                    "voiceVolume": 0.4,
                    "seed": 123,
                },
                reference_voice="suguha",
                profile_name="speed",
            )
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 256)
            self.assertEqual(used_reference, "suguha")
            self.assertEqual(_WorkerHandler.last_speak["model"], "irodori_v3_low_latency")
            self.assertEqual(_WorkerHandler.last_speak["referenceVoice"], "suguha")
            self.assertEqual(_WorkerHandler.last_speak["seed"], 123)
            self.assertEqual(_WorkerHandler.last_speak["format"], "wav")

    def test_build_runtime_does_not_create_local_gpu_arbiter_in_remote_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = voice_service.build_voice_runtime(
                {
                    "audioOutputDir": temp_dir,
                    "remoteTts": {"enabled": True, "baseUrl": self.base_url},
                },
                instance_id="remote-test",
            )
            self.assertIsNone(runtime._gpu_arbiter)

    def test_remote_tts_maps_absolute_worker_audio_url_to_loopback_tunnel(self) -> None:
        self.assertEqual(
            voice_service._resolve_remote_audio_url(
                "http://127.0.0.1:18730",
                "http://127.0.0.1:8730/audio/generated.wav",
            ),
            "http://127.0.0.1:18730/audio/generated.wav",
        )

    def test_remote_mode_can_expose_voice_metadata_without_local_audio_files(self) -> None:
        voices = voice_service.reference_voice_list(
            {
                "remoteTts": {"enabled": True, "baseUrl": self.base_url},
                "referenceVoices": {
                    "sakura_01": {"label": "sakura_01"},
                    "asuka": {"label": "asuka"},
                },
            }
        )
        self.assertIn({"id": "sakura_01", "label": "sakura_01"}, voices)
        self.assertIn({"id": "asuka", "label": "asuka"}, voices)

    def test_remote_worker_health_details_are_not_exposed(self) -> None:
        private_path = r"C:\worker\runtime\audio"
        with patch.object(
            voice_service,
            "_read_json_response",
            return_value={"ok": True, "audioOutputDir": private_path, "status": "healthy"},
        ):
            readiness = voice_service.prepare_remote_tts(
                {"remoteTts": {"enabled": True, "baseUrl": self.base_url}}
            )

        self.assertEqual(readiness["health"], {"ok": True})
        self.assertNotIn(private_path, repr(readiness))

    def test_remote_worker_requests_bypass_environment_proxies(self) -> None:
        proxy = "http://127.0.0.1:1"
        config = {"remoteTts": {"enabled": True, "baseUrl": self.base_url}}
        with patch.dict(
            os.environ,
            {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""},
        ):
            readiness = voice_service.prepare_remote_tts(config)

        self.assertEqual(readiness["health"]["ok"], True)

    def test_remote_worker_errors_do_not_expose_worker_details(self) -> None:
        private_path = r"C:\worker\runtime\audio"
        config = {"remoteTts": {"enabled": True, "baseUrl": self.base_url}}

        with patch.object(
            voice_service,
            "_read_json_response",
            return_value={"ok": False, "audioOutputDir": private_path},
        ):
            with self.assertRaises(voice_service.VoiceServiceError) as error:
                voice_service.prepare_remote_tts(config)

        self.assertEqual(str(error.exception), "remote TTS worker is not ready")
        self.assertNotIn(private_path, str(error.exception))

    def test_remote_worker_http_error_reason_is_not_exposed(self) -> None:
        private_path = r"C:\worker\private\api.log"
        with self.assertRaises(voice_service.VoiceServiceError) as error:
            voice_service.prepare_remote_tts(
                {"remoteTts": {"enabled": True, "baseUrl": self.base_url, "healthPath": "/http-error"}}
            )

        self.assertEqual(str(error.exception), "remote TTS worker returned HTTP 500")
        self.assertNotIn(private_path, str(error.exception))

    def test_remote_worker_response_read_error_is_not_exposed(self) -> None:
        private_path = r"C:\worker\private\broken-response"

        class _UnreadableResponse:
            headers = {}

            def read(self, _limit: int) -> bytes:
                raise HTTPException(private_path)

        with self.assertRaisesRegex(voice_service.VoiceServiceError, "response could not be read") as error:
            voice_service._read_bounded_response(_UnreadableResponse(), max_bytes=1024, label="JSON")

        self.assertNotIn(private_path, str(error.exception))

    def test_remote_generation_errors_do_not_expose_worker_details(self) -> None:
        private_path = r"C:\worker\runtime\audio\failed.wav"
        config = {
            "audioOutputDir": tempfile.gettempdir(),
            "remoteTts": {"enabled": True, "baseUrl": self.base_url},
        }

        with patch.object(
            voice_service,
            "_read_json_response",
            return_value={"ok": False, "errorMessage": private_path},
        ):
            with self.assertRaises(voice_service.VoiceServiceError) as error:
                voice_service.synthesize_remote_tts(
                    config=config,
                    payload={"requestId": "error-test", "text": "worker error"},
                    reference_voice="suguha",
                    profile_name="speed",
                )

        self.assertEqual(str(error.exception), "remote TTS generation failed")
        self.assertNotIn(private_path, str(error.exception))

    def test_remote_generation_missing_audio_url_does_not_expose_worker_details(self) -> None:
        private_path = r"C:\worker\runtime\audio\failed.wav"
        config = {
            "remoteTts": {"enabled": True, "baseUrl": self.base_url},
        }

        with patch.object(
            voice_service,
            "_read_json_response",
            return_value={"ok": True, "result": {"ok": True, "audioOutputDir": private_path}},
        ):
            with self.assertRaises(voice_service.VoiceServiceError) as error:
                voice_service.synthesize_remote_tts(
                    config=config,
                    payload={"requestId": "missing-audio-url", "text": "worker response"},
                    reference_voice="suguha",
                    profile_name="speed",
                )

        self.assertEqual(str(error.exception), "remote TTS response has no audioUrl")
        self.assertNotIn(private_path, str(error.exception))

    def test_remote_tts_rejects_non_loopback_base_url(self) -> None:
        with self.assertRaisesRegex(voice_service.VoiceServiceError, "loopback"):
            voice_service.remote_tts_settings(
                {
                    "remoteTts": {"enabled": True, "baseUrl": "http://192.168.0.20:8730"},
                }
            )

    def test_remote_tts_rejects_audio_urls_outside_worker_origin(self) -> None:
        for audio_url in (
            "https://example.com/audio/test.wav",
            "//example.com/audio/test.wav",
        ):
            with self.subTest(audio_url=audio_url):
                with self.assertRaisesRegex(voice_service.VoiceServiceError, "loopback|origin"):
                    voice_service._resolve_remote_audio_url(self.base_url, audio_url)

    def test_remote_tts_rejects_absolute_api_paths(self) -> None:
        with self.assertRaisesRegex(voice_service.VoiceServiceError, "relative"):
            voice_service._remote_url(self.base_url, "https://example.com/health")

    def test_remote_tts_rejects_requests_outside_worker_origin(self) -> None:
        with self.assertRaisesRegex(voice_service.VoiceServiceError, "worker loopback origin"):
            voice_service._open_remote(
                "https://example.com/health",
                base_url=self.base_url,
                timeout=1,
            )

    def test_remote_tts_bounds_json_response_size(self) -> None:
        with patch.object(voice_service, "MAX_REMOTE_JSON_BYTES", 8, create=True):
            with self.assertRaisesRegex(voice_service.VoiceServiceError, "too large"):
                voice_service.prepare_remote_tts(
                    {"remoteTts": {"enabled": True, "baseUrl": self.base_url}}
                )

    def test_remote_tts_bounds_audio_response_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "audioOutputDir": temp_dir,
                "remoteTts": {"enabled": True, "baseUrl": self.base_url},
            }
            with patch.object(voice_service, "MAX_REMOTE_AUDIO_BYTES", 1024, create=True):
                with self.assertRaisesRegex(voice_service.VoiceServiceError, "too large"):
                    voice_service.synthesize_remote_tts(
                        config=config,
                        payload={"requestId": "size-test", "text": "bounded response"},
                        reference_voice="suguha",
                        profile_name="speed",
                    )

    def test_remote_tts_rejects_cross_origin_audio_redirect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "audioOutputDir": temp_dir,
                "remoteTts": {"enabled": True, "baseUrl": self.base_url},
            }
            with patch.object(_WorkerHandler, "audio_url", "/audio/redirect"):
                with self.assertRaisesRegex(voice_service.VoiceServiceError, "cross-origin redirect"):
                    voice_service.synthesize_remote_tts(
                        config=config,
                        payload={"requestId": "redirect-test", "text": "same origin only"},
                        reference_voice="suguha",
                        profile_name="speed",
                    )
            self.assertNotIn("/audio/test.wav", _WorkerHandler.get_paths)

    def test_remote_tts_rejects_non_wav_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "audioOutputDir": temp_dir,
                "remoteTts": {"enabled": True, "baseUrl": self.base_url},
            }
            with patch.object(_WorkerHandler, "audio", b"not a wave file" * 40):
                with self.assertRaisesRegex(voice_service.VoiceServiceError, "WAV"):
                    voice_service.synthesize_remote_tts(
                        config=config,
                        payload={"requestId": "invalid-audio", "text": "validate output"},
                        reference_voice="suguha",
                        profile_name="speed",
                    )

    def test_remote_generation_modes_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(voice_service.VoiceServiceError, "mutually exclusive"):
            voice_service.build_voice_runtime(
                {
                    "generationBackend": "remote_ssh",
                    "remoteGeneration": {"sshAlias": "worker", "remoteRepoRoot": "C:/worker"},
                    "remoteTts": {"enabled": True, "baseUrl": self.base_url},
                },
                instance_id="remote-conflict",
            )


if __name__ == "__main__":
    unittest.main()
