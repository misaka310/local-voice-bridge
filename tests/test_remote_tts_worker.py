from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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
    last_speak: dict[str, object] = {}

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            body = json.dumps({"ok": True, "role": "worker"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
                    "audioUrl": "http://127.0.0.1:8730/audio/test.wav",
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

    def test_remote_tts_rejects_non_loopback_base_url(self) -> None:
        with self.assertRaisesRegex(voice_service.VoiceServiceError, "loopback"):
            voice_service.remote_tts_settings(
                {
                    "remoteTts": {"enabled": True, "baseUrl": "http://192.168.0.20:8730"},
                }
            )


if __name__ == "__main__":
    unittest.main()
