from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "run-windows-gui-smoke.ps1"
SMOKE_WORKFLOW = ROOT / ".github" / "workflows" / "windows-gui-smoke.yml"
HOSTED_ONLY_MESSAGE = "Windows GUI smoke must run only on GitHub-hosted windows-latest."


class WindowsGuiSmokeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SMOKE_SCRIPT.read_text(encoding="utf-8-sig")
        cls.workflow = SMOKE_WORKFLOW.read_text(encoding="utf-8")

    def test_prefers_actions_configured_python_for_venv_creation(self) -> None:
        self.assertIn("$env:pythonLocation", self.script)
        self.assertIn("Join-Path $env:pythonLocation 'python.exe'", self.script)
        self.assertIn("-FilePath $configuredPython", self.script)

    def test_never_uses_unqualified_latest_python_launcher(self) -> None:
        self.assertNotIn("@('-3', '-m', 'venv'", self.script)
        self.assertIn("@('-3.11', '-m', 'venv'", self.script)

    def test_fails_when_actions_python_version_drifts(self) -> None:
        self.assertIn("$venvVersion.StartsWith('3.11.')", self.script)
        self.assertIn("GitHub Actions configured Python 3.11", self.script)

    def test_refuses_gui_execution_outside_github_hosted_windows(self) -> None:
        self.assertIn("$env:GITHUB_ACTIONS -ne 'true'", self.script)
        self.assertIn("$env:RUNNER_OS -ne 'Windows'", self.script)
        self.assertIn("$env:LOCAL_VOICE_GUI_RUNNER -ne 'github-hosted-windows-latest'", self.script)
        self.assertIn("must run only on GitHub-hosted windows-latest", self.script)

    def test_local_invocation_stops_before_gui_setup(self) -> None:
        environment = os.environ.copy()
        environment.pop("GITHUB_ACTIONS", None)
        environment.pop("RUNNER_OS", None)
        environment.pop("LOCAL_VOICE_GUI_RUNNER", None)
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "exit 0",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.skipTest("powershell.exe itself did not start within five seconds")
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SMOKE_SCRIPT),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(HOSTED_ONLY_MESSAGE, output)
        self.assertNotIn("[gui-smoke] Python", output)

    def test_workflow_sets_the_hosted_runner_contract(self) -> None:
        self.assertIn("runs-on: windows-latest", self.workflow)
        self.assertIn("LOCAL_VOICE_GUI_RUNNER: github-hosted-windows-latest", self.workflow)


if __name__ == "__main__":
    unittest.main()
