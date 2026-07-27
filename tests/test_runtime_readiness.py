from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from runtime_readiness import enrich_snapshot, runtime_snapshot, structured_readiness  # noqa: E402


class FakeRuntime:
    def snapshot(self):
        return {
            "readiness": "ready",
            "ready": True,
            "dependencies": {"sounddevice": True, "soundfile": True},
            "repairRequired": False,
            "lastOperation": "synthesize:complete",
        }


class RuntimeReadinessTests(unittest.TestCase):
    def test_missing_runtime_is_not_reported_ready(self) -> None:
        snapshot = runtime_snapshot(None)
        self.assertEqual(snapshot["readiness"], "not_started")
        self.assertFalse(snapshot["ready"])
        self.assertEqual(snapshot["queueSize"], 0)

    def test_readiness_requires_runtime_dependencies_and_a_connected_tab(self) -> None:
        runtime = runtime_snapshot(FakeRuntime())
        waiting = structured_readiness({"connected": True, "tabsCount": 0}, runtime)
        ready = structured_readiness({"connected": True, "tabsCount": 2}, runtime)

        self.assertFalse(waiting["ready"])
        self.assertEqual(waiting["browserExtension"], "waiting")
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["dependencies"], "ready")

    def test_enrichment_keeps_control_payload_and_adds_structured_runtime(self) -> None:
        runtime = runtime_snapshot(FakeRuntime())
        result = enrich_snapshot(
            {"settings": {"enabled": True}, "extension": {"connected": True, "tabsCount": 1}},
            runtime,
        )

        self.assertTrue(result["settings"]["enabled"])
        self.assertEqual(result["voiceRuntime"]["readiness"], "ready")
        self.assertTrue(result["readiness"]["ready"])

    def test_invalid_tab_count_degrades_to_waiting_instead_of_crashing(self) -> None:
        result = structured_readiness({"connected": True, "tabsCount": "bad"}, runtime_snapshot(FakeRuntime()))
        self.assertFalse(result["ready"])
        self.assertEqual(result["tabs"], 0)


if __name__ == "__main__":
    unittest.main()
