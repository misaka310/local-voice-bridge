from __future__ import annotations

import ast
import os
import subprocess
import time
import unittest
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = ROOT / "scripts" / "run-windows-gui-smoke.ps1"
SMOKE_WORKFLOW = ROOT / ".github" / "workflows" / "windows-gui-smoke.yml"
TRAY_UIA_SMOKE = ROOT / "tests" / "windows" / "tray_uia_smoke.py"
HOSTED_TRAY_UIA_SMOKE = ROOT / "tests" / "windows" / "hosted_tray_uia_smoke.py"
HOSTED_ONLY_MESSAGE = "Windows GUI smoke must run only on GitHub-hosted windows-latest."


class WindowsGuiSmokeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SMOKE_SCRIPT.read_text(encoding="utf-8-sig")
        cls.workflow = SMOKE_WORKFLOW.read_text(encoding="utf-8")
        cls.tray_uia_smoke = TRAY_UIA_SMOKE.read_text(encoding="utf-8")
        cls.hosted_tray_uia_smoke = HOSTED_TRAY_UIA_SMOKE.read_text(encoding="utf-8")

        module = ast.parse(cls.tray_uia_smoke)
        cls.tray_uia_module = module
        wait_until = next(
            node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "wait_until"
        )
        namespace = {"time": time, "Callable": Callable}
        exec(compile(ast.Module(body=[wait_until], type_ignores=[]), str(TRAY_UIA_SMOKE), "exec"), namespace)
        cls.wait_until = staticmethod(namespace["wait_until"])

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

    def test_panel_responsiveness_waits_for_bounded_recovery(self) -> None:
        verify_panel_toggle = next(
            node
            for node in self.tray_uia_module.body
            if isinstance(node, ast.FunctionDef) and node.name == "verify_panel_toggle"
        )
        responsiveness_calls = [
            node
            for node in ast.walk(verify_panel_toggle)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "wait_until"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "Local Voice panel responsiveness"
        ]
        self.assertEqual(len(responsiveness_calls), 1)
        responsiveness_call = responsiveness_calls[0]
        self.assertGreaterEqual(len(responsiveness_call.args), 2)
        predicate = responsiveness_call.args[1]
        self.assertIsInstance(predicate, ast.Lambda)
        self.assertIsInstance(predicate.body, ast.Call)
        self.assertIsInstance(predicate.body.func, ast.Name)
        self.assertEqual(predicate.body.func.id, "window_is_responsive")
        predicate_keywords = {keyword.arg: keyword.value for keyword in predicate.body.keywords}
        self.assertEqual(ast.literal_eval(predicate_keywords["timeout_ms"]), 500)
        wait_keywords = {keyword.arg: keyword.value for keyword in responsiveness_call.keywords}
        self.assertEqual(ast.literal_eval(wait_keywords["timeout"]), 10)
        self.assertEqual(ast.literal_eval(wait_keywords["interval"]), 0.2)

        attempts = iter((False, False, True))
        self.assertTrue(self.wait_until("transient responsiveness", lambda: next(attempts), timeout=0.1, interval=0))
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.wait_until("permanent hang", lambda: False, timeout=0.01, interval=0.001)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_packaged_launcher_smoke_never_opens_a_console(self) -> None:
        self.assertIn('CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)', self.tray_uia_smoke)
        self.assertGreaterEqual(self.tray_uia_smoke.count("creationflags=CREATE_NO_WINDOW"), 2)
        self.assertIn("creationflags=smoke.CREATE_NO_WINDOW", self.hosted_tray_uia_smoke)


if __name__ == "__main__":
    unittest.main()
