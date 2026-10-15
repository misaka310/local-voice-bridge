from __future__ import annotations

import sys
import unittest
from pathlib import Path


WINDOWS_TESTS = Path(__file__).resolve().parent / "windows"
if str(WINDOWS_TESTS) not in sys.path:
    sys.path.insert(0, str(WINDOWS_TESTS))

from process_identity import logical_leaf_pids  # noqa: E402


class WindowsProcessIdentityTests(unittest.TestCase):
    def test_virtualenv_redirector_parent_is_not_a_second_instance(self) -> None:
        self.assertEqual(logical_leaf_pids([(2944, 8352), (3644, 2944)]), {3644})

    def test_independent_controller_processes_remain_independent(self) -> None:
        self.assertEqual(logical_leaf_pids([(101, 1), (202, 1)]), {101, 202})

    def test_nested_redirector_chain_resolves_to_the_leaf(self) -> None:
        self.assertEqual(logical_leaf_pids([(10, 1), (20, 10), (30, 20)]), {30})

    def test_empty_process_set_stays_empty(self) -> None:
        self.assertEqual(logical_leaf_pids([]), set())


if __name__ == "__main__":
    unittest.main()
