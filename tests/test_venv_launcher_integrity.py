from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_LAUNCHER = ROOT / "scripts" / "build-launcher.ps1"
SETUP_ENGINE = ROOT / "scripts" / "setup" / "setup-engine.ps1"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@unittest.skipUnless(os.name == "nt", "Windows launcher integrity tests require Windows")
class VenvLauncherIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._build_dir = tempfile.TemporaryDirectory()
        cls.built_launcher = Path(cls._build_dir.name) / "LocalVoiceBridge.exe"
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(BUILD_LAUNCHER),
                "-OutputPath",
                str(cls.built_launcher),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"launcher build failed: {completed.stdout}\n{completed.stderr}")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._build_dir.cleanup()

    def create_test_app(self, root: Path) -> tuple[Path, Path, Path]:
        local_api = root / "local-api"
        venv = local_api / ".venv"
        (root / "scripts" / "setup").mkdir(parents=True)
        local_api.mkdir(parents=True)
        shutil.copy2(self.built_launcher, root / "LocalVoiceBridge.exe")
        (root / "scripts" / "setup" / "setup-gui.ps1").write_text("# test setup\n", encoding="utf-8")
        (local_api / "tray_controller.py").write_text("# test controller\n", encoding="utf-8")

        completed = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        fake_pyside = venv / "Lib" / "site-packages" / "PySide6"
        fake_pyside.mkdir(parents=True)
        (fake_pyside / "__init__.py").write_text(
            "QtWidgets = object()\nQtSvg = object()\n",
            encoding="utf-8",
        )

        base_home = self.read_base_home(venv)
        return venv, base_home / "python.exe", base_home / "pythonw.exe"

    @staticmethod
    def read_base_home(venv: Path) -> Path:
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("home = "):
                return Path(line.split("=", 1)[1].strip())
        raise AssertionError("temporary venv did not contain a home entry")

    @staticmethod
    def replace_with_base_interpreter(target: Path, base: Path, *, make_hash_different: bool = False) -> None:
        target.unlink()
        shutil.copy2(base, target)
        if make_hash_different:
            with target.open("ab") as handle:
                handle.write(b"\0")

    @staticmethod
    def system_only_path() -> str:
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        return os.pathsep.join((str(windows / "System32"), str(windows)))

    def run_launcher_self_test(self, app_root: Path, *, path_value: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PATH"] = path_value
        return subprocess.run(
            [str(app_root / "LocalVoiceBridge.exe"), "--self-test"],
            cwd=app_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )

    def run_setup_detect_and_repair(self, venv: Path, *, path_value: str) -> subprocess.CompletedProcess[str]:
        harness = r'''
$source = Get-Content -LiteralPath $env:LVB_SETUP_ENGINE -Raw -Encoding UTF8
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { exit 30 }
$functions = $ast.EndBlock.Statements | Where-Object { $_ -is [System.Management.Automation.Language.FunctionDefinitionAst] }
foreach ($function in $functions) { Invoke-Expression $function.Extent.Text }
function Get-FileHash { throw 'Get-FileHash is intentionally unavailable in this regression harness.' }
$venv = $env:LVB_TEST_VENV
$python = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'
$logPath = $env:LVB_TEST_LOG
if (Test-VenvPython) { exit 31 }
try {
    Repair-VoiceVenv
} catch {
    Write-Error $_
    exit 32
}
if (-not (Test-VenvPython)) {
    Write-Host "POST_REPAIR_UNHEALTHY"
    $cfgPath = Join-Path $venv 'pyvenv.cfg'
    if (Test-Path -LiteralPath $cfgPath) { Write-Host (Get-Content -LiteralPath $cfgPath -Raw) }
    $basePython = Get-VenvBasePython
    Write-Host ("BASE_PYTHON={0}" -f $basePython)
    if ($null -ne $basePython) {
        $baseHome = Split-Path -Parent $basePython
        $templateRoot = Join-Path $baseHome 'Lib\venv\scripts\nt'
        foreach ($name in @('python.exe', 'pythonw.exe')) {
            $launcher = Join-Path (Join-Path $venv 'Scripts') $name
            $template = Join-Path $templateRoot $name
            $launcherHash = if (Test-Path -LiteralPath $launcher) { Get-FileSha256Hex -Path $launcher } else { 'MISSING' }
            $templateHash = if (Test-Path -LiteralPath $template) { Get-FileSha256Hex -Path $template } else { 'MISSING' }
            Write-Host ("{0}: launcher={1} template={2}" -f $name, $launcherHash, $templateHash)
        }
    }
    exit 33
}
exit 0
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            harness_path = Path(temp_dir) / "venv-probe.ps1"
            log_path = Path(temp_dir) / "setup.log"
            harness_path.write_text(harness, encoding="utf-8")
            environment = os.environ.copy()
            environment["PATH"] = path_value
            environment["LVB_SETUP_ENGINE"] = str(SETUP_ENGINE)
            environment["LVB_TEST_VENV"] = str(venv)
            environment["LVB_TEST_LOG"] = str(log_path)
            return subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                creationflags=CREATE_NO_WINDOW,
            )

    def test_launcher_rejects_base_pythonw_copied_over_venv_redirector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, _base_python, base_pythonw = self.create_test_app(app)
            self.replace_with_base_interpreter(venv / "Scripts" / "pythonw.exe", base_pythonw)

            completed = self.run_launcher_self_test(app, path_value=self.system_only_path())

            self.assertEqual(completed.returncode, 2, completed.stderr)

    def test_launcher_rejects_stale_base_python_copy_even_when_base_home_is_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, base_python, _base_pythonw = self.create_test_app(app)
            self.replace_with_base_interpreter(
                venv / "Scripts" / "python.exe",
                base_python,
                make_hash_different=True,
            )
            contaminated_path = os.pathsep.join((str(base_python.parent), self.system_only_path()))

            completed = self.run_launcher_self_test(app, path_value=contaminated_path)

            self.assertEqual(completed.returncode, 2, completed.stderr)

    def test_launcher_rejects_missing_pyvenv_cfg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, _base_python, _base_pythonw = self.create_test_app(app)
            (venv / "pyvenv.cfg").unlink()

            completed = self.run_launcher_self_test(app, path_value=self.system_only_path())

            self.assertEqual(completed.returncode, 2, completed.stderr)

    def test_launcher_rejects_pyvenv_cfg_without_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, _base_python, _base_pythonw = self.create_test_app(app)
            (venv / "pyvenv.cfg").write_text(
                "include-system-site-packages = false\nversion = 3.11.9\n",
                encoding="utf-8",
            )

            completed = self.run_launcher_self_test(app, path_value=self.system_only_path())

            self.assertEqual(completed.returncode, 2, completed.stderr)

    def test_setup_fails_closed_when_pyvenv_cfg_has_no_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, _base_python, _base_pythonw = self.create_test_app(app)
            (venv / "pyvenv.cfg").write_text(
                "include-system-site-packages = false\nversion = 3.11.9\n",
                encoding="utf-8",
            )

            completed = self.run_setup_detect_and_repair(venv, path_value=self.system_only_path())

            self.assertEqual(completed.returncode, 32, f"{completed.stdout}\n{completed.stderr}")

    def test_setup_detects_and_repairs_base_pythonw_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, _base_python, base_pythonw = self.create_test_app(app)
            self.replace_with_base_interpreter(venv / "Scripts" / "pythonw.exe", base_pythonw)

            completed = self.run_setup_detect_and_repair(venv, path_value=self.system_only_path())

            self.assertEqual(completed.returncode, 0, f"{completed.stdout}\n{completed.stderr}")

    def test_setup_detects_and_repairs_stale_base_python_copy_with_contaminated_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir)
            venv, base_python, _base_pythonw = self.create_test_app(app)
            self.replace_with_base_interpreter(
                venv / "Scripts" / "python.exe",
                base_python,
                make_hash_different=True,
            )
            contaminated_path = os.pathsep.join((str(base_python.parent), self.system_only_path()))

            completed = self.run_setup_detect_and_repair(venv, path_value=contaminated_path)

            self.assertEqual(completed.returncode, 0, f"{completed.stdout}\n{completed.stderr}")


if __name__ == "__main__":
    unittest.main()
