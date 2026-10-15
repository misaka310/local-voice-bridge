from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from installation_identity import gpu_mutex_names, installation_id  # noqa: E402


class InstallationIdentityTests(unittest.TestCase):
    def test_identity_preserves_existing_server_algorithm(self) -> None:
        app_root = ROOT.resolve()
        expected = hashlib.sha256(str(app_root).casefold().encode("utf-8")).hexdigest()[:20]
        self.assertEqual(installation_id(app_root), expected)

    def test_different_installations_get_different_identity(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            self.assertNotEqual(installation_id(first), installation_id(second))

    def test_gpu_mutex_names_are_instance_scoped(self) -> None:
        gate, gpu = gpu_mutex_names("abc123")
        self.assertEqual(gate, "Local\\LocalVoiceBridgeGpuSttGate-abc123")
        self.assertEqual(gpu, "Local\\LocalVoiceBridgeGpu-abc123")
        self.assertNotEqual(gpu_mutex_names("abc123"), gpu_mutex_names("other"))


if __name__ == "__main__":
    unittest.main()
