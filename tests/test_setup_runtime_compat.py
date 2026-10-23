"""Regression tests for a clean, CUDA-capable Irodori setup on Windows."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup" / "setup-engine.ps1"
FFMPEG = ROOT / "local-api" / "scripts" / "ensure_shared_ffmpeg.py"
CORE = ROOT / "local-api" / "requirements-core.txt"
AUDIT = ROOT / "local-api" / "scripts" / "audit_runtime_dependencies.py"


class SetupRuntimeCompatTests(unittest.TestCase):
    def test_pinned_ffmpeg_does_not_accept_ffmpeg9(self) -> None:
        spec = importlib.util.spec_from_file_location("local_voice_ffmpeg_installer", FFMPEG)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn("/tags/8.1.2", module.COMPATIBLE_RELEASE_API)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td)
            bin_dir = target / "bin"
            bin_dir.mkdir()
            for name in ("avutil-61.dll", "avcodec-63.dll", "avformat-63.dll"):
                (bin_dir / name).touch()
            self.assertFalse(module.has_shared_ffmpeg(target))
            for name in ("avutil-60.dll", "avcodec-62.dll", "avformat-62.dll"):
                (bin_dir / name).touch()
            self.assertTrue(module.has_shared_ffmpeg(target))

    def test_install_order_and_dependency_pins(self) -> None:
        source = SETUP.read_text(encoding="utf-8-sig")
        at = lambda stage: source.index(f'Invoke-SetupStage -Id "{stage}"')
        self.assertLess(at("torch"), at("ffmpeg"))
        self.assertLess(at("ffmpeg"), at("venv-bootstrap"))
        self.assertLess(at("venv-bootstrap"), at("torchcodec"))
        self.assertLess(at("torchcodec"), at("core-dependencies"))
        self.assertLess(at("irodori"), at("model-cache"))
        self.assertLess(at("model-cache"), at("runtime-check"))
        self.assertIn('Test-PythonImport "import irodori_tts"', source)
        self.assertIn('numpy==2.4.6', source)
        requirements = CORE.read_text(encoding="utf-8")
        self.assertIn("Pillow==12.0.0", requirements)
        self.assertNotIn("\nwandb==0.28.0\n", "\n" + requirements + "\n")
        audit = AUDIT.read_text(encoding="utf-8")
        self.assertIn('"transformers": "5.20.1"', audit)
        self.assertIn('"PySide6": "6.12.0"', audit)


if __name__ == "__main__":
    unittest.main()
