from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore[assignment]

LOCAL_API_DIR = Path(__file__).resolve().parent
APP_ROOT = LOCAL_API_DIR.parent
APP_NAME = "Local Voice Bridge"
LEGACY_APP_NAME = "ChatGPT Local Voice Bridge"
LAUNCHER_EXE = APP_ROOT / "LocalVoiceBridge.exe"
LEGACY_LAUNCHER_EXE = APP_ROOT / "ChatGPTLocalVoiceBridge.exe"
UNINSTALL_SCRIPT = APP_ROOT / "scripts" / "uninstall-local-voice-bridge.ps1"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = APP_NAME
LEGACY_WINDOWS_RUN_VALUE = LEGACY_APP_NAME
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
MUTEX_NAME = "Local\\ChatGPTLocalVoiceBridgeTray"
ACTIVATION_EVENT_NAME = "Local\\ChatGPTLocalVoiceBridgeActivate"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x00000000
IS_WINDOWS = os.name == "nt"
LOGGER = logging.getLogger("local-voice-bridge-tray")
_MUTEX_HANDLE: int | None = None
_ACTIVATION_EVENT_HANDLE: int | None = None


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata and IS_WINDOWS:
        appdata = str(Path.home() / "AppData" / "Roaming")
    if not appdata:
        raise RuntimeError("APPDATA is not available")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def legacy_startup_entry_paths() -> tuple[Path, ...]:
    folder = startup_folder()
    return (
        folder / "ChatGPT Local Voice Bridge.vbs",
        folder / "Local Voice Bridge.vbs",
    )


def legacy_startup_entry_path() -> Path:
    return legacy_startup_entry_paths()[0]


def legacy_startup_command(launcher: Path | None = None) -> str:
    target = launcher if launcher is not None else LAUNCHER_EXE
    return f'"{target}"'


def startup_command(launcher: Path | None = None) -> str:
    return f"{legacy_startup_command(launcher)} --background"


def _require_winreg() -> Any:
    if not IS_WINDOWS or winreg is None:
        raise RuntimeError("Windows startup registry is not available")
    return winreg


def _read_startup_command(value_name: str = WINDOWS_RUN_VALUE) -> str | None:
    try:
        registry = _require_winreg()
        with registry.OpenKey(registry.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, registry.KEY_READ) as key:
            value, _value_type = registry.QueryValueEx(key, value_name)
    except (OSError, RuntimeError):
        return None
    return str(value)


def _write_startup_command(command: str) -> None:
    registry = _require_winreg()
    with registry.CreateKey(registry.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
        registry.SetValueEx(key, WINDOWS_RUN_VALUE, 0, registry.REG_SZ, command)


def _delete_startup_command(value_name: str = WINDOWS_RUN_VALUE) -> None:
    try:
        registry = _require_winreg()
        with registry.OpenKey(registry.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
            registry.DeleteValue(key, value_name)
    except (OSError, RuntimeError):
        pass


def _remove_legacy_startup_entry() -> None:
    try:
        entries = legacy_startup_entry_paths()
    except RuntimeError:
        return
    for entry in entries:
        if entry.exists():
            entry.unlink()


def is_startup_enabled() -> bool:
    try:
        current_command = _read_startup_command()
        if current_command in {startup_command(), legacy_startup_command()}:
            return True
        legacy_command = _read_startup_command(LEGACY_WINDOWS_RUN_VALUE)
        if legacy_command in {
            startup_command(),
            legacy_startup_command(),
            startup_command(LEGACY_LAUNCHER_EXE),
            legacy_startup_command(LEGACY_LAUNCHER_EXE),
        }:
            return True
        return any(entry.is_file() for entry in legacy_startup_entry_paths())
    except RuntimeError:
        return False


def migrate_legacy_startup() -> bool:
    if not LAUNCHER_EXE.is_file():
        return False
    try:
        entries = legacy_startup_entry_paths()
        current_command = _read_startup_command()
        legacy_command = _read_startup_command(LEGACY_WINDOWS_RUN_VALUE)
    except RuntimeError:
        return False
    needs_migration = (
        any(entry.is_file() for entry in entries)
        or current_command in {
            legacy_startup_command(),
            startup_command(LEGACY_LAUNCHER_EXE),
            legacy_startup_command(LEGACY_LAUNCHER_EXE),
        }
        or legacy_command is not None
    )
    if not needs_migration:
        return False
    _write_startup_command(startup_command())
    _delete_startup_command(LEGACY_WINDOWS_RUN_VALUE)
    _remove_legacy_startup_entry()
    LOGGER.info("Migrated Windows startup to %s: %s", APP_NAME, LAUNCHER_EXE)
    return True


def set_startup_enabled(enabled: bool) -> None:
    if enabled:
        if not LAUNCHER_EXE.is_file():
            raise RuntimeError("LocalVoiceBridge.exe が見つかりません。アプリの環境修復を実行してください。")
        _write_startup_command(startup_command())
        _delete_startup_command(LEGACY_WINDOWS_RUN_VALUE)
        _remove_legacy_startup_entry()
        LOGGER.info("Enabled current-user Windows startup: %s", LAUNCHER_EXE)
        return
    _delete_startup_command()
    _delete_startup_command(LEGACY_WINDOWS_RUN_VALUE)
    _remove_legacy_startup_entry()
    LOGGER.info("Disabled current-user Windows startup")


def open_path(path: Path) -> None:
    is_directory = path.suffix == ""
    path.mkdir(parents=True, exist_ok=True) if is_directory else path.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS and is_directory:
        subprocess.Popen(["explorer.exe", str(path)], creationflags=CREATE_NO_WINDOW)
    elif IS_WINDOWS:
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def show_message(title: str, message: str, error: bool = False) -> None:
    if IS_WINDOWS:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    else:
        print(f"{title}: {message}", file=sys.stderr if error else sys.stdout)


def _open_named_mutex(name: str) -> tuple[int, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = create_mutex(None, False, name)
    return int(handle or 0), int(ctypes.get_last_error())


def _create_named_event(name: str) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_event = kernel32.CreateEventW
    create_event.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
    create_event.restype = ctypes.c_void_p
    handle = create_event(None, False, False, name)
    return int(handle or 0)


def _open_named_event(name: str, access: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_event = kernel32.OpenEventW
    open_event.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    open_event.restype = ctypes.c_void_p
    handle = open_event(access, False, name)
    return int(handle or 0)


def _set_windows_event(handle: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_event = kernel32.SetEvent
    set_event.argtypes = [ctypes.c_void_p]
    set_event.restype = ctypes.c_bool
    return bool(set_event(ctypes.c_void_p(handle)))


def _wait_windows_event(handle: int, timeout_ms: int = 0) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wait_for_single = kernel32.WaitForSingleObject
    wait_for_single.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single.restype = ctypes.c_uint32
    return int(wait_for_single(ctypes.c_void_p(handle), max(0, int(timeout_ms))))


def _close_windows_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_bool
    close_handle(ctypes.c_void_p(handle))


def acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if not IS_WINDOWS:
        return True
    handle, last_error = _open_named_mutex(MUTEX_NAME)
    if not handle:
        return False
    if last_error == ERROR_ALREADY_EXISTS:
        _close_windows_handle(handle)
        return False
    _MUTEX_HANDLE = handle
    return True


def release_single_instance() -> None:
    global _MUTEX_HANDLE
    handle = _MUTEX_HANDLE
    _MUTEX_HANDLE = None
    if IS_WINDOWS and handle:
        _close_windows_handle(handle)


def create_activation_event() -> bool:
    global _ACTIVATION_EVENT_HANDLE
    if not IS_WINDOWS:
        return True
    if _ACTIVATION_EVENT_HANDLE:
        return True
    handle = _create_named_event(ACTIVATION_EVENT_NAME)
    if not handle:
        return False
    _ACTIVATION_EVENT_HANDLE = handle
    return True


def request_existing_instance_activation() -> bool:
    if not IS_WINDOWS:
        return False
    handle = _open_named_event(ACTIVATION_EVENT_NAME, EVENT_MODIFY_STATE)
    if not handle:
        return False
    try:
        return _set_windows_event(handle)
    finally:
        _close_windows_handle(handle)


def consume_activation_request() -> bool:
    handle = _ACTIVATION_EVENT_HANDLE
    if not IS_WINDOWS or not handle:
        return False
    return _wait_windows_event(handle, 0) == WAIT_OBJECT_0


def release_activation_event() -> None:
    global _ACTIVATION_EVENT_HANDLE
    handle = _ACTIVATION_EVENT_HANDLE
    _ACTIVATION_EVENT_HANDLE = None
    if IS_WINDOWS and handle:
        _close_windows_handle(handle)


def launch_application(exe: Path = LAUNCHER_EXE, cwd: Path = APP_ROOT) -> None:
    subprocess.Popen(
        [str(exe)],
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )


def launch_setup(exe: Path = LAUNCHER_EXE, cwd: Path = APP_ROOT) -> None:
    subprocess.Popen(
        [str(exe), "--setup"],
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )


def launch_uninstall(script: Path = UNINSTALL_SCRIPT, cwd: Path = APP_ROOT) -> None:
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
            "-RemoveGeneratedData",
        ],
        cwd=cwd,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
    )
