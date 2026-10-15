from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
PREFLIGHT_PATH = LOCAL_API / "scripts" / "preflight_irodori.py"
AUDIT_PATH = LOCAL_API / "scripts" / "audit_runtime_dependencies.py"
FRONTEND_PREFLIGHT_PATH = LOCAL_API / "scripts" / "preflight_frontend.py"
FRONTEND_REQUIREMENTS = LOCAL_API / "requirements-frontend.txt"


def load_module(name: str, path: Path):
    if str(LOCAL_API) not in sys.path:
        sys.path.insert(0, str(LOCAL_API))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load_module("irodori_preflight_for_test", PREFLIGHT_PATH)
audit = load_module("runtime_dependency_audit_for_test", AUDIT_PATH)
frontend_preflight = load_module("frontend_preflight_for_test", FRONTEND_PREFLIGHT_PATH)


class SecurityBaselineTests(unittest.TestCase):
    def test_accepts_patched_security_baseline(self):
        installed = {
            "transformers": "5.18.0",
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
            "transformers": "5.10.0",
            "huggingface-hub": "1.5.0",
            "sentencepiece": "0.1.99",
        }
        with patch.object(preflight, "package_version", side_effect=installed.__getitem__):
            self.assertFalse(preflight.security_baselines_ok())

    def test_rejects_missing_required_package(self):
        with patch.object(preflight, "package_version", side_effect=PackageNotFoundError):
            self.assertFalse(preflight.security_baselines_ok())


class FrontendDependencyTests(unittest.TestCase):
    def test_frontend_requirements_are_minimal_and_pinned(self):
        requirements = {
            line.strip()
            for line in FRONTEND_REQUIREMENTS.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(
            requirements,
            {
                "PySide6==6.11.2",
                "soundfile==0.14.0",
                "sounddevice>=0.5.6,<0.6",
                "numpy==2.4.6",
                "paramiko==5.0.0",
            },
        )

    def test_frontend_preflight_checks_only_frontend_modules(self):
        self.assertEqual(
            frontend_preflight.REQUIRED_MODULES,
            ("PySide6", "soundfile", "sounddevice", "numpy", "paramiko"),
        )


class DependencyAuditTests(unittest.TestCase):
    def test_allows_documented_sentencepiece_metadata_override(self):
        warning = (
            "irodori-tts 0.1.0 has requirement sentencepiece<0.2,>=0.1.99, "
            "but you have sentencepiece 0.2.1."
        )
        completed = subprocess.CompletedProcess([], 1, stdout=warning, stderr="")
        with patch.object(audit.subprocess, "run", return_value=completed):
            self.assertEqual(audit.audit_pip_check(), [])

    def test_rejects_missing_hf_xet_in_standalone_audit(self):
        installed = dict(audit.EXPECTED_EXACT_VERSIONS)
        installed.pop("hf-xet")
        for package_name, (minimum, _maximum) in audit.EXPECTED_VERSION_RANGES.items():
            installed[package_name] = str(minimum)

        def installed_version(package_name: str) -> str:
            if package_name == "hf-xet":
                raise PackageNotFoundError(package_name)
            return installed[package_name]

        with (
            patch.object(audit, "version", side_effect=installed_version),
            patch.object(
                audit,
                "direct_url_commit",
                side_effect=audit.EXPECTED_VCS_COMMITS.__getitem__,
            ),
        ):
            self.assertIn("missing package: hf-xet", audit.audit_versions())

    def test_rejects_vulnerable_datasets_in_standalone_audit(self):
        installed = dict(audit.EXPECTED_EXACT_VERSIONS)
        installed["datasets"] = "5.0.0"
        for package_name, (minimum, _maximum) in audit.EXPECTED_VERSION_RANGES.items():
            installed[package_name] = str(minimum)
        with (
            patch.object(audit, "version", side_effect=installed.__getitem__),
            patch.object(
                audit,
                "direct_url_commit",
                side_effect=audit.EXPECTED_VCS_COMMITS.__getitem__,
            ),
        ):
            self.assertIn(
                "datasets=5.0.0; expected 5.0.1",
                audit.audit_versions(),
            )

    def test_rejects_vulnerable_sentencepiece_in_standalone_audit(self):
        installed = dict(audit.EXPECTED_EXACT_VERSIONS)
        installed["sentencepiece"] = "0.1.99"
        for package_name, (minimum, _maximum) in audit.EXPECTED_VERSION_RANGES.items():
            installed[package_name] = str(minimum)
        with (
            patch.object(audit, "version", side_effect=installed.__getitem__),
            patch.object(
                audit,
                "direct_url_commit",
                side_effect=audit.EXPECTED_VCS_COMMITS.__getitem__,
            ),
        ):
            self.assertIn(
                "sentencepiece=0.1.99; expected 0.2.2",
                audit.audit_versions(),
            )

    def test_rejects_unknown_dependency_conflict(self):
        warning = "example-package 1.0 requires missing-package, which is not installed."
        completed = subprocess.CompletedProcess([], 1, stdout=warning, stderr="")
        with patch.object(audit.subprocess, "run", return_value=completed):
            self.assertEqual(audit.audit_pip_check(), [warning])


if __name__ == "__main__":
    unittest.main()
