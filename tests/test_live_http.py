from __future__ import annotations

import importlib.util
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
SERVER_PATH = LOCAL_API / "server.py"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from live_conversation import LiveBackpressure, LiveChunkConflict  # noqa: E402


def load_server_module():
    spec = importlib.util.spec_from_file_location("local_voice_live_http_server_test", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load_server_module()


class FakeLiveService:
    def __init__(self) -> None:
        self.mode = "ok"
        self.calls: list[tuple[str, dict[str, object]]] = []

    def snapshot(self):
        return {"ok": True, "submission": {"phase": "bound"}, "capacity": 2}

    def apply_submission(self, action, payload):
        self.calls.append((f"submission:{action}", dict(payload)))
        if self.mode == "conflict":
            from conversation_submission import SubmissionConflict

            raise SubmissionConflict("conflict")
        return {"phase": "armed", "submissionId": payload.get("submissionId")}

    def enqueue_chunk(self, payload):
        self.calls.append(("chunk", dict(payload)))
        if self.mode == "backpressure":
            raise LiveBackpressure(125)
        if self.mode == "conflict":
            raise LiveChunkConflict("chunk conflict")
        return {"accepted": True, "duplicate": False, "capacity": 1}

    def interrupt(self, payload):
        self.calls.append(("interrupt", dict(payload)))
        return {"stopping": True, "cancelEpoch": int(payload.get("cancelEpoch", 0)) + 1}


class LiveHttpTests(unittest.TestCase):
    def start_server(self, service: FakeLiveService | None):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        if service is not None:
            setattr(httpd, "live_conversation", service)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread, f"http://127.0.0.1:{httpd.server_port}"

    @staticmethod
    def post(url: str, payload: dict[str, object]):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_live_state_and_submission_ack_routes(self) -> None:
        service = FakeLiveService()
        httpd, thread, base = self.start_server(service)
        try:
            with urllib.request.urlopen(f"{base}/v1/live/state", timeout=5) as response:
                state = json.loads(response.read().decode("utf-8"))
            self.assertEqual(state["submission"]["phase"], "bound")

            payload = {"action": "arm", "submissionId": "submission-1"}
            with self.post(f"{base}/v1/conversation/submission", payload) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertTrue(result["sendAllowed"])
            self.assertEqual(service.calls[-1][0], "submission:arm")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)

    def test_live_chunk_returns_202_and_backpressure_returns_429(self) -> None:
        service = FakeLiveService()
        httpd, thread, base = self.start_server(service)
        try:
            with self.post(f"{base}/v1/live/chunks", {"text": "hello"}) as response:
                self.assertEqual(response.status, 202)
                accepted = json.loads(response.read().decode("utf-8"))
            self.assertTrue(accepted["accepted"])

            service.mode = "backpressure"
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.post(f"{base}/v1/live/chunks", {"text": "next"})
            self.assertEqual(raised.exception.code, 429)
            payload = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(payload["retryAfterMs"], 125)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)

    def test_conflicts_return_409_and_interrupt_advances_epoch(self) -> None:
        service = FakeLiveService()
        httpd, thread, base = self.start_server(service)
        try:
            service.mode = "conflict"
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.post(f"{base}/v1/conversation/submission", {"action": "arm"})
            self.assertEqual(raised.exception.code, 409)

            service.mode = "ok"
            with self.post(f"{base}/v1/interrupt", {"cancelEpoch": 5, "reason": "input"}) as response:
                result = json.loads(response.read().decode("utf-8"))
            self.assertEqual(result["cancelEpoch"], 6)
            self.assertTrue(result["stopping"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)

    def test_live_routes_return_503_when_service_is_not_attached(self) -> None:
        httpd, thread, base = self.start_server(None)
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(f"{base}/v1/live/state", timeout=5)
            self.assertEqual(raised.exception.code, 503)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
