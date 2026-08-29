from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_ENGINE = ROOT / "scripts" / "setup" / "setup-engine.ps1"
SETUP_GUI = ROOT / "scripts" / "setup" / "setup-gui.ps1"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _run_powershell(script: str, *, env: dict[str, str] | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        script_path = Path(temp_dir) / "probe.ps1"
        script_path.write_text(script, encoding="utf-8")
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )


def _process_exists(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


@unittest.skipUnless(os.name == "nt", "Windows setup UX tests require Windows")
class SetupUserFlowTests(unittest.TestCase):
    def _nvidia_probe(self, *, fake_gpu: bool) -> subprocess.CompletedProcess[str]:
        harness = r'''
$source = Get-Content -LiteralPath $env:LVB_SETUP_ENGINE -Raw -Encoding UTF8
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { exit 40 }
$function = $ast.EndBlock.Statements | Where-Object {
    $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq 'Test-EarlyNvidiaCapability'
} | Select-Object -First 1
if ($null -eq $function) { exit 41 }
Invoke-Expression $function.Extent.Text
if ($env:LVB_FAKE_NVIDIA -eq '1') {
    function global:nvidia-smi.exe { 'Fake NVIDIA GPU' }
}
if (Test-EarlyNvidiaCapability) { exit 0 }
exit 42
'''
        environment = os.environ.copy()
        environment["LVB_SETUP_ENGINE"] = str(SETUP_ENGINE)
        environment["LVB_FAKE_NVIDIA"] = "1" if fake_gpu else "0"
        if not fake_gpu:
            environment["PATH"] = tempfile.gettempdir()
        return _run_powershell(harness, env=environment)

    def test_early_nvidia_capability_fails_when_nvidia_command_is_unavailable(self) -> None:
        completed = self._nvidia_probe(fake_gpu=False)
        self.assertEqual(completed.returncode, 42, completed.stdout + completed.stderr)

    def test_early_nvidia_capability_accepts_a_resolvable_gpu_command(self) -> None:
        completed = self._nvidia_probe(fake_gpu=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_setup_stops_at_preflight_before_package_downloads_without_nvidia(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            setup_dir = root / "scripts" / "setup"
            setup_dir.mkdir(parents=True)
            (root / "local-api").mkdir()
            shutil.copy2(SETUP_ENGINE, setup_dir / "setup-engine.ps1")
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            shutil.copy2(sys.executable, fake_bin / "python.exe")
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin)
            environment["ProgramFiles"] = str(root / "empty-program-files")
            completed = subprocess.run(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(setup_dir / "setup-engine.ps1"),
                    "-Profile",
                    "reading",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )
            self.assertNotEqual(completed.returncode, 0)
            progress = root / "local-api" / "runtime" / "setup" / "progress.jsonl"
            log_path = root / "local-api" / "runtime" / "setup" / "setup.log"
            log_text = log_path.read_text(encoding="utf-8-sig") if log_path.is_file() else ""
            self.assertTrue(progress.is_file(), completed.stdout + completed.stderr + "\n" + log_text)
            events = progress.read_text(encoding="utf-8-sig")
            self.assertIn('"id":"preflight"', events)
            self.assertIn('"status":"failed"', events)
            self.assertIn('"code":"LVB-SETUP-001"', events)
            self.assertNotIn('"id":"torch"', events)
            self.assertFalse((root / "local-api" / ".venv").exists())

    def test_setup_cancel_stops_the_setup_process_tree(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            temp = Path(temp_dir)
            child_pid_path = temp / "child.pid"
            child_pid: int | None = None
            parent_helper = temp / "parent_helper.py"
            parent_helper.write_text(
                textwrap.dedent(
                    f"""
                    import subprocess
                    import sys
                    import time
                    from pathlib import Path

                    CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    child = subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(120)"],
                        creationflags=CREATE_NO_WINDOW,
                    )
                    Path({str(child_pid_path)!r}).write_text(str(child.pid), encoding="utf-8")
                    time.sleep(120)
                    """
                ),
                encoding="utf-8",
            )
            parent = subprocess.Popen(
                [sys.executable, str(parent_helper)],
                cwd=temp,
                creationflags=CREATE_NO_WINDOW,
            )
            try:
                deadline = time.monotonic() + 10
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(child_pid_path.exists(), "child pid was not published")
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                self.assertTrue(_process_exists(parent.pid))
                self.assertTrue(_process_exists(child_pid))

                harness = r'''
$source = Get-Content -LiteralPath $env:LVB_SETUP_GUI -Raw -Encoding UTF8
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { exit 50 }
$function = $ast.EndBlock.Statements | Where-Object {
    $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq 'Stop-SetupProcessTree'
} | Select-Object -First 1
if ($null -eq $function) { exit 51 }
Invoke-Expression $function.Extent.Text
$process = [System.Diagnostics.Process]::GetProcessById([int]$env:LVB_PARENT_PID)
Stop-SetupProcessTree -Process $process
exit 0
'''
                environment = os.environ.copy()
                environment["LVB_SETUP_GUI"] = str(SETUP_GUI)
                environment["LVB_PARENT_PID"] = str(parent.pid)
                completed = _run_powershell(harness, env=environment)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

                parent.wait(timeout=10)
                deadline = time.monotonic() + 5
                while _process_exists(child_pid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(_process_exists(child_pid), "setup cancellation left a child process running")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
                if child_pid is not None and _process_exists(child_pid):
                    subprocess.run(
                        ["taskkill.exe", "/PID", str(child_pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                        creationflags=CREATE_NO_WINDOW,
                    )

    def test_setup_gui_exposes_cancel_and_never_tells_users_to_use_task_manager(self) -> None:
        source = SETUP_GUI.read_text(encoding="utf-8-sig")
        self.assertIn('Text = "キャンセル"', source)
        self.assertIn("Stop-SetupProcessTree", source)
        self.assertIn("セットアップをキャンセルしました", source)
        self.assertNotIn("タスクマネージャーで中止", source)

    def test_setup_engine_process_is_started_with_create_no_window(self) -> None:
        source = SETUP_GUI.read_text(encoding="utf-8-sig")
        self.assertIn("function Start-SetupProcessNoWindow", source)
        self.assertIn("$startInfo.CreateNoWindow = $true", source)
        self.assertNotIn('Start-Process -FilePath "powershell.exe"', source)
        self.assertNotIn("-WindowStyle Hidden", source)


if __name__ == "__main__":
    unittest.main()
