from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from voice_job_queue import (  # noqa: E402
    VoiceJobQueue,
    VoiceJobQueueClosed,
    VoiceJobQueueFull,
)


class VoiceJobQueueTests(unittest.TestCase):
    def test_fifo_and_invalidate(self) -> None:
        queue = VoiceJobQueue[dict[str, int]](maxsize=4)
        queue.enqueue({"id": 1})
        queue.enqueue({"id": 2})
        queue.enqueue({"id": 3})

        removed = queue.invalidate(lambda item: item["id"] == 2)

        self.assertEqual(removed, [{"id": 2}])
        self.assertEqual(queue.take(timeout=0.1), {"id": 1})
        self.assertEqual(queue.take(timeout=0.1), {"id": 3})

    def test_full_queue_times_out(self) -> None:
        queue = VoiceJobQueue[int](maxsize=1)
        queue.enqueue(1)

        with self.assertRaises(VoiceJobQueueFull):
            queue.enqueue(2, timeout=0.01)

    def test_wake_releases_waiter_without_item(self) -> None:
        queue = VoiceJobQueue[int]()
        result: list[int | None] = []
        started = threading.Event()

        def waiter() -> None:
            started.set()
            result.append(queue.take(timeout=1))

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(started.wait(0.5))
        time.sleep(0.01)
        queue.wake()
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [None])

    def test_close_discards_and_unblocks(self) -> None:
        queue = VoiceJobQueue[int]()
        queue.enqueue(1)
        removed = queue.close()

        self.assertEqual(removed, [1])
        self.assertTrue(queue.closed)
        with self.assertRaises(VoiceJobQueueClosed):
            queue.take(timeout=0.01)
        with self.assertRaises(VoiceJobQueueClosed):
            queue.enqueue(2, timeout=0.01)

    def test_close_is_idempotent(self) -> None:
        queue = VoiceJobQueue[int]()
        self.assertEqual(queue.close(), [])
        self.assertEqual(queue.close(), [])


if __name__ == "__main__":
    unittest.main()
