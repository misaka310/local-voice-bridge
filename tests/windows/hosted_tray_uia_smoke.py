from __future__ import annotations

import subprocess
import time

from pywinauto import Desktop

import tray_uia_smoke as smoke


EXPECTED_ACTIONS = (
    "小窓を表示",
    "デスクトップペットを戻す",
    "Local Voice Bridge を再起動",
    "コントローラーログを開く",
    "生成音声フォルダーを開く",
    "生成音声を削除...",
    "参照音声フォルダーを開く",
    "Windows起動時に開始",
    "終了して環境を修復",
    "Local Voice Bridge をアンインストール...",
    "終了",
)

ACTION_TRANSLATIONS = {
    "Show Local Voice panel": "小窓を表示",
    "Hide Local Voice panel": "小窓を隠す",
    "Bring Desktop Pet Back": "デスクトップペットを戻す",
    "Restart Voice Bridge": "Local Voice Bridge を再起動",
    "Open controller log": "コントローラーログを開く",
    "Open generated audio folder": "生成音声フォルダーを開く",
    "Clear generated audio...": "生成音声を削除...",
    "Open reference voices folder": "参照音声フォルダーを開く",
    "Start with Windows": "Windows起動時に開始",
    "Exit and run environment setup": "終了して環境を修復",
    "Uninstall Local Voice Bridge...": "Local Voice Bridge をアンインストール...",
    "Exit": "終了",
}


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
        if "終了" in names and "Local Voice Bridge を再起動" in names:
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


def launch_app():
    completed = subprocess.run(
        [str(smoke.EXE), "--background"],
        cwd=smoke.ROOT,
        timeout=15,
        check=False,
        creationflags=smoke.CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{smoke.EXE.name} returned {completed.returncode}")
    return smoke.wait_for_stable_single_controller(timeout=15)


def open_menu(pid: int):
    smoke.find_tray_button().click_input(button="right")
    popup = smoke.wait_until("Qt tray menu", lambda: find_qt_popup(pid), timeout=10)
    wrapper = Desktop(backend="uia").window(handle=popup.hwnd)
    smoke.wait_until("tray menu items", lambda: wrapper.descendants(control_type="MenuItem"), timeout=5)
    return popup, wrapper


def assert_menu_contract(pid: int) -> None:
    popup, wrapper = open_menu(pid)
    try:
        items = smoke.menu_items(wrapper)
        if not any(title.startswith("状態: ") for title in items):
            raise AssertionError(f"status item missing: {sorted(items)}")
        missing = [title for title in EXPECTED_ACTIONS if title not in items]
        if missing:
            raise AssertionError(f"missing menu actions: {missing}; actual={sorted(items)}")
        disabled = [title for title in EXPECTED_ACTIONS if not items[title].is_enabled()]
        if disabled:
            raise AssertionError(f"unexpected disabled menu actions: {disabled}")
    finally:
        smoke.close_popup(popup.hwnd)


def click_menu_item(pid: int, title: str) -> None:
    translated = ACTION_TRANSLATIONS.get(title, title)
    popup, wrapper = open_menu(pid)
    items = smoke.menu_items(wrapper)
    item = items.get(translated)
    if item is None:
        smoke.close_popup(popup.hwnd)
        raise AssertionError(f"menu item not found: {translated}; actual={sorted(items)}")
    if not item.is_enabled():
        smoke.close_popup(popup.hwnd)
        raise AssertionError(f"menu item is disabled: {translated}")
    item.click_input()
    smoke.wait_until(
        "tray menu to close",
        lambda: not smoke.USER32.IsWindow(popup.hwnd),
        timeout=5,
    )


def assert_single_instance(original_pid: int) -> None:
    baseline = stable_controller_pids()
    if original_pid not in baseline:
        raise AssertionError(f"original controller PID {original_pid} not in baseline {baseline}")

    completed = subprocess.run(
        [str(smoke.EXE), "--background"],
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
                assert_menu_contract(original_pid)
                return
        else:
            baseline_since = None
        time.sleep(0.25)

    raise AssertionError(
        f"controller process tree changed after duplicate launch: baseline={baseline}; "
        f"current={controller_process_details()}"
    )


def main() -> int:
    smoke.EXPECTED_ACTIONS = EXPECTED_ACTIONS
    smoke.find_qt_popup = find_qt_popup
    smoke.panel_window = panel_window
    smoke.pet_window = pet_window
    smoke.launch_app = launch_app
    smoke.open_menu = open_menu
    smoke.assert_menu_contract = assert_menu_contract
    smoke.click_menu_item = click_menu_item
    smoke.assert_single_instance = assert_single_instance
    return smoke.main()


if __name__ == "__main__":
    raise SystemExit(main())
