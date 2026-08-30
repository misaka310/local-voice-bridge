from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepairSignalWiringTests(unittest.TestCase):
    def test_panel_repair_signal_routes_to_existing_tray_setup_action(self) -> None:
        source = (ROOT / "local-api" / "tray_controller.py").read_text(encoding="utf-8")
        self.assertIn(
            "self.control_panel.repair_requested.connect(self.exit_and_run_setup)",
            source,
            "runtime repair must reuse the existing no-console setup-after-exit path",
        )
        self.assertNotIn("setup-voice-env.cmd", source)


if __name__ == "__main__":
    unittest.main()
