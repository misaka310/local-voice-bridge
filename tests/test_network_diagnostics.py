import json
import tempfile
import unittest
from pathlib import Path

from network_diagnostics import ChatgptNetworkEventLogger, sanitize_network_event


class NetworkDiagnosticsTests(unittest.TestCase):
    def test_sanitize_network_event_strips_query_fragment_and_rejects_other_hosts(self):
        event = sanitize_network_event(
            {
                "observedAt": "2026-08-13T06:00:00.000Z",
                "method": "get",
                "statusCode": 429,
                "type": "xmlhttprequest",
                "tabId": 8,
                "host": "CHATGPT.COM",
                "path": "/backend-api/conversations?offset=20#private",
            }
        )
        self.assertEqual(event["host"], "chatgpt.com")
        self.assertEqual(event["path"], "/backend-api/conversations")
        self.assertEqual(event["method"], "GET")
        self.assertEqual(event["statusCode"], 429)
        with self.assertRaises(ValueError):
            sanitize_network_event({"host": "example.com", "path": "/", "statusCode": 200})

    def test_logger_writes_privacy_minimized_jsonl_and_rotates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "chatgpt-network-events.jsonl"
            logger = ChatgptNetworkEventLogger(path, max_bytes=1024, backup_count=1)
            for index in range(30):
                logger.record(
                    {
                        "observedAt": "2026-08-13T06:00:00.000Z",
                        "method": "GET",
                        "statusCode": 429,
                        "type": "xmlhttprequest",
                        "tabId": index,
                        "host": "chatgpt.com",
                        "path": f"/backend-api/conversations/{index}?sample=redacted",
                        "synthetic": True,
                    }
                )
            self.assertTrue(path.exists())
            self.assertTrue(path.with_name(path.name + ".1").exists())
            latest = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            self.assertTrue(latest)
            self.assertTrue(all("redacted" not in record["path"] for record in latest))
            self.assertTrue(all(record["synthetic"] is True for record in latest))


if __name__ == "__main__":
    unittest.main()
