from __future__ import annotations

import subprocess
import time

from pywinauto import Desktop

import tray_uia_smoke as smoke


def find_qt_popup(_pid: int) -> smoke.WindowInfo | None:
    desktop = Desktop(backend="uia")
    for row in smoke.enum_top_windows():
        if row.title != smoke.APP_NAME or "QWindowPopup" not in row.class_name:
            continue
        if not smoke.USER32.IsWindowVisible(row.hwnd):
            continue
        try:
            wrapper = desktop.window(handle=row.hwnd)
            names = {
                item.window_text().strip()
                for item in wrapper.descendants(control_type="MenuItem")
            }
        except Exception:
            continue
        if "Exit" in names and "Restart Voice Bridge" in names:
            return row
    return None


def panel_window(_pid: int) -> smoke.WindowInfo | None:
    return next(
        (
            row
            for row in smoke.enum_top_windows()
            if row.title == smoke.APP_NAME
            and "QWindowPopup" not in row.class_name
            and row.class_name != "#32770"
            and smoke.USER32.IsWindowVisible(row.hwnd)
        ),
        None,
    )


def pet_window(_pid: int) -> smoke.WindowInfo | None:
    return next(
        (
            row
            for row in smoke.enum_top_windows()
            if row.title == smoke.PET_WINDOW_TITLE
            and smoke.USER32.IsWindowVisible(row.hwnd)
        ),
        None,
    )


def controller_process_details() -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    for process in smoke.controller_processes():
        try:
            details.append(
                {
                    "pid": process.pid,
                    "ppid": process.ppid(),
                    "exe": process.exe(),
                    "cmdline": process.cmdline(),
                    "createTime": process.create_time(),
                    "status": process.status(),
                }
            )
        except Exception as exc:
            details.append({"pid": process.pid, "error": f"{type(exc).__name__}: {exc}"})
    return details


def stable_controller_pids(timeout: float = 15.0, stable_for: float = 1.0) -> tuple[int, ...]:
    deadline = time.monotonic() + timeout
    last: tuple[int, ...] = ()
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        current = tuple(sorted(process.pid for process in smoke.controller_processes()))
        if current and current == last:
            if time.monotonic() - stable_since >= stable_for:
                return current
        else:
            last = current
            stable_since = time.monotonic()
        time.sleep(0.25)
    raise AssertionError(f"controller process set did not stabilize: {controller_process_details()}")


def assert_single_instance(original_pid: int) -> None:
    baseline = stable_controller_pids()
    if original_pid not in baseline:
        raise AssertionError(f"original controller PID {original_pid} not in baseline {baseline}")

    completed = subprocess.run(
        [str(smoke.EXE)],
        cwd=smoke.ROOT,
        timeout=15,
        check=False,
        creationflags=smoke.CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"second launcher returned {completed.returncode}")

    deadline = time.monotonic() + 30
    baseline_since: float | None = None
    while time.monotonic() < deadline:
        current = tuple(sorted(process.pid for process in smoke.controller_processes()))
        if current == baseline:
            if baseline_since is None:
                baseline_since = time.monotonic()
            elif time.monotonic() - baseline_since >= 5:
                smoke.assert_menu_contract(original_pid)
                return
        else:
            baseline_since = None
        time.sleep(0.25)

    raise AssertionError(
        f"controller process tree changed after duplicate launch: baseline={baseline}; "
        f"current={controller_process_details()}"
    )


def main() -> int:
    smoke.find_qt_popup = find_qt_popup
    smoke.panel_window = panel_window
    smoke.pet_window = pet_window
    smoke.assert_single_instance = assert_single_instance
    return smoke.main()


if __name__ == "__main__":
    raise SystemExit(main())
