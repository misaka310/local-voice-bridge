from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from conversation_turn import TurnIdentity  # noqa: E402
from runtime_events import RuntimeEventLogger, event_fields  # noqa: E402


class RuntimeEventLoggerTests(unittest.TestCase):
    def test_writes_structured_clocked_event_without_text_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            logger = RuntimeEventLogger(path)
            logger.emit(
                "tts_generation_completed",
                **event_fields(
                    TurnIdentity(
                        turn_id="turn-1",
                        cancel_epoch=2,
                        submission_id="submission-1",
                        tab_id=7,
                        page_instance_id="page-1",
                        conversation_key="conversation-1",
                        generation_id="generation-1",
                    ),
                    ttsProfile="speed",
                    textLength=22,
                    textHash="abc123",
                    durationSeconds=0.9,
                    text="secret body",
                    path="C:/private/voice.wav",
                ),
            )

            record = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(record["event"], "tts_generation_completed")
            self.assertEqual(record["turnId"], "turn-1")
            self.assertEqual(record["ttsProfile"], "speed")
            self.assertIsInstance(record["wallTimeNs"], int)
            self.assertIsInstance(record["monotonicNs"], int)
            self.assertNotIn("text", record)
            self.assertNotIn("path", record)
            self.assertNotIn("secret body", path.read_text(encoding="utf-8"))
            self.assertNotIn("C:/private", path.read_text(encoding="utf-8"))

    def test_log_io_failure_never_breaks_runtime_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent_file = Path(temp_dir) / "not-a-directory"
            parent_file.write_text("occupied", encoding="utf-8")
            logger = RuntimeEventLogger(parent_file / "events.jsonl")

            logger.emit("recording_started", sessionId="session-1")

            self.assertEqual(parent_file.read_text(encoding="utf-8"), "occupied")

    def test_concurrent_emits_leave_valid_json_lines(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            logger = RuntimeEventLogger(path)
            threads = [
                threading.Thread(
                    target=lambda index=index: logger.emit(
                        "recording_started",
                        sessionId=f"session-{index}",
                        turnId=f"turn-{index}",
                    )
                )
                for index in range(20)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 20)
            self.assertEqual({record["event"] for record in records}, {"recording_started"})


if __name__ == "__main__":
    unittest.main()
