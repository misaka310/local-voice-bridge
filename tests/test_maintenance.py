from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from maintenance import clear_generated_audio, prune_generated_audio, trim_file_to_tail  # noqa: E402


class AudioMaintenanceTests(unittest.TestCase):
    def _audio(self, root: Path, name: str, size: int, modified: float) -> Path:
        path = root / name
        path.write_bytes(b"x" * size)
        os.utime(path, (modified, modified))
        return path

    def test_prune_enforces_count_and_bytes_while_preserving_current_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = time.time()
            files = [self._audio(root, f"{index}.wav", 40, now - (10 - index)) for index in range(5)]
            result = prune_generated_audio(
                root,
                max_files=3,
                max_bytes=100,
                max_age_days=365,
                preserve=(files[-1],),
                now=now,
            )
            self.assertEqual(result.remaining_files, 2)
            self.assertEqual(result.remaining_bytes, 80)
            self.assertTrue(files[-1].exists())
            self.assertFalse(files[0].exists())
            self.assertFalse(files[1].exists())
            self.assertFalse(files[2].exists())

    def test_prune_removes_expired_audio_but_not_non_audio_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            now = time.time()
            expired = self._audio(root, "expired.flac", 10, now - 3 * 24 * 60 * 60)
            current = self._audio(root, "current.flac", 10, now)
            note = root / "keep.txt"
            note.write_text("keep", encoding="utf-8")
            result = prune_generated_audio(root, max_files=10, max_bytes=1000, max_age_days=1, now=now)
            self.assertEqual(result.deleted_files, 1)
            self.assertFalse(expired.exists())
            self.assertTrue(current.exists())
            self.assertTrue(note.exists())

    def test_clear_generated_audio_leaves_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._audio(root, "one.wav", 10, time.time())
            self._audio(root, "two.mp3", 20, time.time())
            marker = root / "marker.json"
            marker.write_text("{}", encoding="utf-8")
            result = clear_generated_audio(root)
            self.assertEqual(result.deleted_files, 2)
            self.assertEqual(result.remaining_files, 0)
            self.assertTrue(marker.exists())

    def test_trim_file_to_tail_reclaims_oversized_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "server.log"
            path.write_bytes(b"a" * 100 + b"tail")
            trim_file_to_tail(path, 16)
            self.assertEqual(path.stat().st_size, 16)
            self.assertTrue(path.read_bytes().endswith(b"tail"))


if __name__ == "__main__":
    unittest.main()
