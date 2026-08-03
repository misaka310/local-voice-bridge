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

from gpu_arbiter import (  # noqa: E402
    GpuArbiter,
    GpuArbiterCancelled,
    InProcessMutexBackend,
)


class ScriptedBackend:
    def __init__(self, waits: list[str]) -> None:
        self.waits = list(waits)
        self.released: list[str] = []
        self.closed: list[str] = []

    def create(self, name: str) -> str:
        return name

    def wait(self, _handle: str, _timeout_ms: int) -> str:
        return self.waits.pop(0)

    def release(self, handle: str) -> None:
        self.released.append(handle)

    def close(self, handle: str) -> None:
        self.closed.append(handle)


class GpuArbiterTests(unittest.TestCase):
    def test_stt_waiting_at_gate_prevents_new_tts_overtake(self) -> None:
        instance = f"priority-{time.time_ns()}"
        backend = InProcessMutexBackend()
        owner = GpuArbiter(instance, backend=backend)
        stt_arbiter = GpuArbiter(instance, backend=backend)
        next_tts_arbiter = GpuArbiter(instance, backend=backend)
        first_tts = owner.acquire_tts(timeout=1)
        order: list[str] = []
        stt_acquired = threading.Event()

        def run_stt() -> None:
            with stt_arbiter.acquire_stt(timeout=2):
                order.append("stt")
                stt_acquired.set()
                time.sleep(0.03)

        def run_tts() -> None:
            with next_tts_arbiter.acquire_tts(timeout=2):
                order.append("tts")

        stt_thread = threading.Thread(target=run_stt, daemon=True)
        stt_thread.start()
        time.sleep(0.05)
        tts_thread = threading.Thread(target=run_tts, daemon=True)
        tts_thread.start()
        time.sleep(0.02)
        first_tts.release()

        self.assertTrue(stt_acquired.wait(1))
        stt_thread.join(2)
        tts_thread.join(2)
        self.assertEqual(order, ["stt", "tts"])
        owner.close()
        stt_arbiter.close()
        next_tts_arbiter.close()

    def test_tts_wait_is_cancel_aware(self) -> None:
        instance = f"cancel-{time.time_ns()}"
        backend = InProcessMutexBackend()
        owner = GpuArbiter(instance, backend=backend)
        waiter = GpuArbiter(instance, backend=backend)
        lease = owner.acquire_tts(timeout=1)
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(GpuArbiterCancelled):
            waiter.acquire_tts(timeout=1, cancel_event=cancel)

        lease.release()
        owner.close()
        waiter.close()

    def test_abandoned_mutex_is_recovered_and_reported(self) -> None:
        backend = ScriptedBackend(["abandoned", "acquired"])
        arbiter = GpuArbiter("abandoned", backend=backend)

        lease = arbiter.acquire_stt(timeout=1)

        self.assertTrue(lease.abandoned)
        self.assertEqual(arbiter.snapshot()["abandonedRecoveries"], 1)
        lease.release()
        self.assertEqual(backend.released.count(arbiter.gate_name), 1)
        self.assertEqual(backend.released.count(arbiter.gpu_name), 1)
        arbiter.close()

    def test_lease_release_is_idempotent(self) -> None:
        backend = ScriptedBackend(["acquired", "acquired"])
        arbiter = GpuArbiter("idempotent", backend=backend)
        lease = arbiter.acquire_stt(timeout=1)

        lease.release()
        lease.release()

        self.assertEqual(backend.released.count(arbiter.gpu_name), 1)
        arbiter.close()


if __name__ == "__main__":
    unittest.main()
