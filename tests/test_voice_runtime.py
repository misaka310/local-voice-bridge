from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from voice_runtime import VoiceRuntime, VoiceRuntimeError  # noqa: E402


class FakePlayer:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, float]] = []
        self.stopped = False

    @staticmethod
    def dependency_status() -> dict[str, bool]:
        return {"sounddevice": True, "soundfile": True}

    def play(self, path: Path, volume: float, stop_event: threading.Event) -> bool:
        self.calls.append((Path(path), volume))
        return not stop_event.is_set()

    def stop(self) -> None:
        self.stopped = True


class VoiceRuntimeTests(unittest.TestCase):
    def test_request_waits_for_startup_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "voice.wav"
            output.write_bytes(b"RIFF")
            prepare_release = threading.Event()
            synthesized = threading.Event()

            def prepare():
                prepare_release.wait(2)
                return {"device": "cuda", "precision": "bf16"}

            def synthesize(_payload):
                synthesized.set()
                return output, ""

            runtime = VoiceRuntime(prepare_fn=prepare, synthesize_fn=synthesize, player=FakePlayer())
            result: dict[str, object] = {}
            thread = threading.Thread(
                target=lambda: result.update(runtime.synthesize({}, text="hello", volume=0, play_local=False)),
                daemon=True,
            )
            thread.start()
            time.sleep(0.05)

            self.assertEqual(runtime.snapshot()["readiness"], "loading")
            self.assertFalse(synthesized.is_set())
            prepare_release.set()
            thread.join(2)

            self.assertFalse(thread.is_alive())
            self.assertTrue(synthesized.is_set())
            self.assertEqual(result["path"], output)
            self.assertEqual(runtime.snapshot()["readiness"], "ready")
            runtime.close()

    def test_synthesis_is_serialized_across_concurrent_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            active = 0
            maximum = 0
            lock = threading.Lock()

            def synthesize(payload):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.05)
                path = Path(temp_dir) / f"{payload['id']}.wav"
                path.write_bytes(b"RIFF")
                with lock:
                    active -= 1
                return path, ""

            runtime = VoiceRuntime(
                prepare_fn=lambda: {"device": "cuda"},
                synthesize_fn=synthesize,
                player=FakePlayer(),
            )
            results: list[dict[str, object]] = []
            threads = [
                threading.Thread(
                    target=lambda item=index: results.append(
                        runtime.synthesize({"id": item}, text=str(item), volume=0, play_local=False)
                    )
                )
                for index in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(2)

            self.assertEqual(maximum, 1)
            self.assertEqual(len(results), 2)
            runtime.close()

    def test_local_playback_and_replay_use_the_same_generated_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "voice.wav"
            output.write_bytes(b"RIFF")
            player = FakePlayer()
            runtime = VoiceRuntime(
                prepare_fn=lambda: {"device": "cuda"},
                synthesize_fn=lambda _payload: (output, "reference.wav"),
                player=player,
            )

            first = runtime.synthesize({}, text="hello", volume=0.25, play_local=True)
            replay = runtime.replay(volume=0.75, text="hello")

            self.assertTrue(first["playedLocally"])
            self.assertEqual(first["usedReferenceAudio"], "reference.wav")
            self.assertTrue(replay["playedLocally"])
            self.assertEqual(player.calls, [(output, 0.25), (output, 0.75)])
            runtime.close()

    def test_prepare_failure_is_exposed_and_requests_fail_stably(self) -> None:
        runtime = VoiceRuntime(
            prepare_fn=lambda: (_ for _ in ()).throw(RuntimeError("missing transformers")),
            synthesize_fn=lambda _payload: (_ for _ in ()).throw(AssertionError("not called")),
            player=FakePlayer(),
        )
        runtime.start()
        deadline = time.time() + 2
        while runtime.snapshot()["readiness"] == "loading" and time.time() < deadline:
            time.sleep(0.01)

        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["readiness"], "failed")
        self.assertTrue(snapshot["repairRequired"])
        self.assertIn("missing transformers", snapshot["error"])
        with self.assertRaisesRegex(VoiceRuntimeError, "missing transformers"):
            runtime.synthesize({}, text="hello", volume=0, play_local=False)
        runtime.close()

    def test_stop_during_generation_prevents_late_playback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "voice.wav"
            output.write_bytes(b"RIFF")
            synthesizing = threading.Event()
            release = threading.Event()
            player = FakePlayer()

            def synthesize(_payload):
                synthesizing.set()
                release.wait(2)
                return output, ""

            runtime = VoiceRuntime(
                prepare_fn=lambda: {},
                synthesize_fn=synthesize,
                player=player,
            )
            result: dict[str, object] = {}
            thread = threading.Thread(
                target=lambda: result.update(
                    runtime.synthesize({}, text="hello", volume=0.5, play_local=True)
                ),
                daemon=True,
            )
            thread.start()
            self.assertTrue(synthesizing.wait(1))
            runtime.stop_playback()
            release.set()
            thread.join(2)

            self.assertFalse(thread.is_alive())
            self.assertEqual(player.calls, [])
            self.assertTrue(result["stopped"])
            runtime.close()

    def test_stop_requests_local_player_stop(self) -> None:
        player = FakePlayer()
        runtime = VoiceRuntime(
            prepare_fn=lambda: {},
            synthesize_fn=lambda _payload: (_ for _ in ()).throw(AssertionError("not called")),
            player=player,
        )

        result = runtime.stop_playback()

        self.assertTrue(result["stopping"])
        self.assertTrue(player.stopped)
        runtime.close()


if __name__ == "__main__":
    unittest.main()
