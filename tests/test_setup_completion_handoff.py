from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_GUI = ROOT / "scripts" / "setup" / "setup-gui.ps1"


class SetupCompletionHandoffTests(unittest.TestCase):
    def test_success_handoff_leads_from_extension_install_to_windows_onboarding(self) -> None:
        source = SETUP_GUI.read_text(encoding="utf-8-sig")

        self.assertIn('$launcher = Join-Path $repoRoot "LocalVoiceBridge.exe"', source)
        self.assertIn('次に「拡張機能の導入手順」で導入または再読み込みし', source)
        self.assertIn('「Local Voice Bridge を開く」', source)
        self.assertIn('小窓で接続を待って「テスト音声」', source)
        self.assertIn('$startButton.Text = "Local Voice Bridge を開く"', source)
        self.assertIn('$cancelButton.Visible = $false', source)
        self.assertIn('$script:setupComplete = $true', source)
        self.assertIn('Start-Process -FilePath $launcher', source)


if __name__ == "__main__":
    unittest.main()
