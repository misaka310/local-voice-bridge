from __future__ import annotations

from collections.abc import Iterable


def logical_leaf_pids(processes: Iterable[tuple[int, int]]) -> set[int]:
    """Return controller PIDs that are not interpreter-wrapper parents.

    Windows virtual environments may keep a small ``pythonw.exe`` redirector
    alive as the direct parent of the real base-interpreter process. Both
    processes carry the same script command line, but only the leaf process is
    an application instance. Independent leaves remain independent.
    """

    rows = tuple(processes)
    candidate_pids = {pid for pid, _ppid in rows}
    wrapper_parent_pids = {
        ppid
        for _pid, ppid in rows
        if ppid in candidate_pids
    }
    return candidate_pids - wrapper_parent_pids
