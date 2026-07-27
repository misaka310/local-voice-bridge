from __future__ import annotations

import importlib.util
import sys
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
PREFLIGHT_PATH = LOCAL_API / "scripts" / "preflight_irodori.py"


def load_preflight_module():
    if str(LOCAL_API) not in sys.path:
        sys.path.insert(0, str(LOCAL_API))
    spec = importlib.util.spec_from_file_location("irodori_preflight_for_test", PREFLIGHT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load preflight_irodori.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load_preflight_module()


class SecurityBaselineTests(unittest.TestCase):
    def test_accepts_patched_security_baseline(self):
        installed = {
            "transformers": "5.5.0",
            "huggingface-hub": "1.5.0",
            "sentencepiece": "0.2.1",
        }
        with patch.object(preflight, "package_version", side_effect=installed.__getitem__):
            self.assertTrue(preflight.security_baselines_ok())

    def test_rejects_vulnerable_transformers(self):
        installed = {
            "transformers": "4.57.3",
            "huggingface-hub": "1.23.0",
            "sentencepiece": "0.2.1",
        }
        with patch.object(preflight, "package_version", side_effect=installed.__getitem__):
            self.assertFalse(preflight.security_baselines_ok())

    def test_rejects_vulnerable_sentencepiece(self):
        installed = {
            "transformers": "5.5.0",
            "huggingface-hub": "1.5.0",
            "sentencepiece": "0.1.99",
        }
        with patch.object(preflight, "package_version", side_effect=installed.__getitem__):
            self.assertFalse(preflight.security_baselines_ok())

    def test_rejects_missing_required_package(self):
        with patch.object(preflight, "package_version", side_effect=PackageNotFoundError):
            self.assertFalse(preflight.security_baselines_ok())


if __name__ == "__main__":
    unittest.main()
