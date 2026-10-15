from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reload-extension.ps1"


class ExtensionReloadScriptTests(unittest.TestCase):
    def test_agent_reload_uses_loopback_command_and_post_reload_version_check(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("http://127.0.0.1:8717", text)
        self.assertIn("/v1/control-panel/command", text)
        self.assertIn("reload_extension", text)
        self.assertIn("expectedVersion", text)
        self.assertIn("loadedVersion", text)
        self.assertIn("updateRequired", text)

    def test_agent_reload_refreshes_same_version_content_scripts_by_default(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("$sameVersionRefresh = $extension.updateRequired -ne $true", text)
        self.assertIn("sameVersionRefresh = [bool]$sameVersionRefresh", text)
        self.assertNotIn("result = 'already_current'", text)

    def test_agent_reload_never_drives_the_browser_ui(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8-sig").casefold()
        self.assertNotIn("chrome://extensions", text)
        self.assertNotIn("setforegroundwindow", text)
        self.assertNotIn("sendkeys", text)
        self.assertNotIn("start-process", text)


if __name__ == "__main__":
    unittest.main()
