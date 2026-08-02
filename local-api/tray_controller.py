from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    import winreg
except ImportError:
    winreg = None  # type: ignore[assignment]

try:
    from PySide6.QtCore import QObject, QTimer, Qt, Signal
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
except ImportError as exc:
    message = "PySide6 が見つかりません。setup-voice-env.cmd をもう一度実行してください。"
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "Local Voice Bridge", 0x10)
    else:
        print(f"{message}\nImportError: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

LOCAL_API_DIR = Path(__file__).resolve().parent
APP_ROOT = LOCAL_API_DIR.parent
if str(LOCAL_API_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_API_DIR))

from control_panel import ControlPanelApiClient, LocalVoiceControlPanel  # noqa: E402
from conversation_controller import GlobalRightCtrlHook, VoiceConversationController  # noqa: E402
from desktop_pet import DesktopPetWindow  # noqa: E402
from desktop_pet_config import DesktopPetSettingsStore  # noqa: E402
from maintenance import clear_generated_audio, format_bytes  # noqa: E402
from runtime_events import RuntimeEventLogger, default_event_log_path  # noqa: E402

VENV_SCRIPTS = LOCAL_API_DIR / ".venv" / "Scripts"
SERVER_PYTHON = VENV_SCRIPTS / "python.exe"
SERVER_SCRIPT = LOCAL_API_DIR / "server.py"
PREFLIGHT_SCRIPT = LOCAL_API_DIR / "scripts" / "preflight_irodori.py"
SETUP_SCRIPT = APP_ROOT / "setup-voice-env.cmd"
UNINSTALL_SCRIPT = APP_ROOT / "scripts" / "uninstall-local-voice-bridge.ps1"
APP_NAME = "Local Voice Bridge"
LEGACY_APP_NAME = "ChatGPT Local Voice Bridge"
LAUNCHER_EXE = APP_ROOT / "LocalVoiceBridge.exe"
LEGACY_LAUNCHER_EXE = APP_ROOT / "ChatGPTLocalVoiceBridge.exe"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = APP_NAME
LEGACY_WINDOWS_RUN_VALUE = LEGACY_APP_NAME
RUNTIME_DIR = LOCAL_API_DIR / "runtime"
LOG_DIR = LOCAL_API_DIR / "logs"
CONTROLLER_LOG = LOG_DIR / "controller.log"
SERVER_LOG = LOG_DIR / "server.log"
AUDIO_DIR = RUNTIME_DIR / "audio"
INSTANCE_STATE_PATH = RUNTIME_DIR / "server-instance.json"
INSTALLATION_ID = hashlib.sha256(str(APP_ROOT).casefold().encode("utf-8")).hexdigest()[:20]
REFERENCE_DIR = LOCAL_API_DIR / "reference" / "voices"
PET_ROOT = APP_ROOT / "extension" / "assets" / "pet"
DESKTOP_PET_SETTINGS = RUNTIME_DIR / "desktop-pet-settings.json"
CONTROL_PANEL_WINDOW_SETTINGS = RUNTIME_DIR / "control-panel-window.json"
HEALTH_URL = "http://127.0.0.1:8717/health"
PORT = 8717
HEALTH_INTERVAL_SECONDS = 5.0
RESTART_MIN_INTERVAL_SECONDS = 10.0
PREFLIGHT_TIMEOUT_SECONDS = 180.0
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
MUTEX_NAME = "Local\\ChatGPTLocalVoiceBridgeTray"
ERROR_ALREADY_EXISTS = 183
IS_WINDOWS = os.name == "nt"

LOGGER = logging.getLogger("local-voice-bridge-tray")
_MUTEX_HANDLE: int | None = None


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if LOGGER.handlers:
        return
    LOGGER.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        CONTROLLER_LOG,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [tray] %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)


def compatible_health_payload(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("runtime") == "irodori_direct"
        and payload.get("defaultModel") == "irodori-v3"
    )


def probe_health(timeout: float = 1.5) -> tuple[bool, dict[str, Any] | None]:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            if response.status != 200:
                return False, None
            payload = json.loads(response.read().decode("utf-8"))
            return compatible_health_payload(payload), payload if isinstance(payload, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return False, None


def port_is_open(timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


def load_server_instance_state(path: Path = INSTANCE_STATE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def same_installation_health(payload: Any) -> bool:
    return isinstance(payload, dict) and str(payload.get("instanceId") or "") == INSTALLATION_ID


def request_same_installation_shutdown(payload: Any, timeout: float = 8.0) -> bool:
    if not same_installation_health(payload):
        return False
    state = load_server_instance_state()
    if str(state.get("instanceId") or "") != INSTALLATION_ID:
        return False
    control_nonce = str(state.get("shutdownToken") or "").strip()
    if not control_nonce:
        return False
    request = urllib.request.Request(
        "http://127.0.0.1:8717/v1/admin/shutdown",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-Local-Voice-Token": control_nonce,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            if response.status != 200:
                return False
    except (OSError, urllib.error.URLError):
        return False
    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        if not port_is_open():
            return True
        time.sleep(0.1)
    return not port_is_open()


def server_command(python_executable: Path = SERVER_PYTHON) -> list[str]:
    return [str(python_executable), str(SERVER_SCRIPT)]


def preflight_command(python_executable: Path = SERVER_PYTHON) -> list[str]:
    return [str(python_executable), str(PREFLIGHT_SCRIPT), "--strict-cuda", "--quick"]


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


def startup_command(launcher: Path | None = None) -> str:
    target = launcher if launcher is not None else LAUNCHER_EXE
    return f'"{target}"'


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
        if _read_startup_command() == startup_command():
            return True
        legacy_command = _read_startup_command(LEGACY_WINDOWS_RUN_VALUE)
        if legacy_command in {startup_command(), startup_command(LEGACY_LAUNCHER_EXE)}:
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
        or current_command == startup_command(LEGACY_LAUNCHER_EXE)
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
            raise RuntimeError("LocalVoiceBridge.exe が見つかりません。setup-voice-env.cmd を再実行してください。")
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



def create_qt_application(argv: Sequence[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        app = existing
    else:
        app = QApplication(list(argv or []))
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    return app


def create_tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(36, 99, 235))
    painter.drawRoundedRect(8, 8, 48, 48, 14, 14)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawRoundedRect(25, 15, 14, 23, 7, 7)
    pen = QPen(QColor(255, 255, 255), 4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(19, 23, 26, 25, 0, -180 * 16)
    painter.drawLine(32, 47, 32, 54)
    painter.drawLine(25, 54, 39, 54)
    painter.end()
    return QIcon(pixmap)


class VoiceBridgeController:
    def __init__(self) -> None:
        self._status = "Starting"
        self._status_lock = threading.Lock()
        self._operation_lock = threading.RLock()
        self._shutdown_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._shutdown_started = False
        self._monitor_thread: threading.Thread | None = None
        self._process: subprocess.Popen[Any] | None = None
        self._server_log_handle: Any = None
        self._status_callback: Callable[[str], None] | None = None
        self._last_start_attempt = 0.0
        self._health_failures = 0

    @property
    def status(self) -> str:
        with self._status_lock:
            return self._status

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def set_status_callback(self, callback: Callable[[str], None] | None) -> None:
        self._status_callback = callback
        if callback is not None:
            callback(self.status)

    def set_status(self, value: str) -> None:
        with self._status_lock:
            changed = value != self._status
            self._status = value
        if changed:
            LOGGER.info("Status: %s", value)
        callback = self._status_callback
        if callback is not None:
            try:
                callback(value)
            except Exception:
                LOGGER.debug("Status callback failed", exc_info=True)

    def start_monitor(self) -> None:
        if self._stop_event.is_set():
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="voice-bridge-health-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor_loop(self) -> None:
        self._ensure_running()
        while not self._stop_event.wait(HEALTH_INTERVAL_SECONDS):
            process = self._process
            if process is not None and process.poll() is not None:
                LOGGER.warning("Owned voice bridge exited with code %s", process.returncode)
                self._close_server_log()
                self._process = None

            healthy, _ = probe_health()
            if healthy:
                self._health_failures = 0
                self.set_status("Ready" if self._process is not None else "Ready (existing)")
                continue

            self._health_failures += 1
            if self._process is not None and self._process.poll() is None:
                self.set_status("Unhealthy")
                if self._health_failures >= 2:
                    self._restart_owned_server()
                continue

            self._ensure_running()

    def _run_preflight(self) -> bool:
        if self._stop_event.is_set():
            return False
        self.set_status("Checking environment")
        try:
            process = subprocess.Popen(
                preflight_command(),
                cwd=LOCAL_API_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,

                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            LOGGER.error("Preflight could not run: %s", exc)
            self.set_status("Environment check failed")
            return False

        deadline = time.monotonic() + PREFLIGHT_TIMEOUT_SECONDS
        output = ""
        while True:
            try:
                output, _ = process.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if self._stop_event.is_set() or time.monotonic() >= deadline:
                    process.terminate()
                    try:
                        output, _ = process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        output, _ = process.communicate(timeout=5)
                    if not self._stop_event.is_set():
                        LOGGER.error("Preflight timed out")
                        self.set_status("Environment check failed")
                    return False

        if output:
            for line in output.splitlines():
                LOGGER.info("[preflight] %s", line)
        if process.returncode != 0:
            self.set_status("CUDA or model unavailable")
            return False
        return not self._stop_event.is_set()

    def _ensure_running(self) -> None:
        with self._operation_lock:
            self._ensure_running_locked()

    def _ensure_running_locked(self) -> None:
        if self._stop_event.is_set():
            return
        healthy, _ = probe_health()
        if healthy:
            self._health_failures = 0
            self.set_status("Ready" if self._process is not None else "Ready (existing)")
            return

        if port_is_open():
            self.set_status(f"Port {PORT} in use")
            return

        if not SERVER_PYTHON.is_file() or not SERVER_SCRIPT.is_file():
            self.set_status("Environment missing")
            return

        now = time.monotonic()
        if now - self._last_start_attempt < RESTART_MIN_INTERVAL_SECONDS:
            self.set_status("Waiting to retry")
            return

        self._last_start_attempt = now
        if not self._run_preflight():
            return
        self._start_owned_server()

    def _start_owned_server(self) -> None:
        if self._stop_event.is_set():
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["LOCAL_VOICE_SERVER_LOG"] = str(SERVER_LOG)
        try:
            self._process = subprocess.Popen(
                server_command(),
                cwd=LOCAL_API_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,

                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            LOGGER.error("Failed to start local voice bridge: %s", exc)
            self._process = None
            self._close_server_log()
            self.set_status("Start failed")
            return

        LOGGER.info("Started owned local voice bridge PID %s", self._process.pid)
        self.set_status("Starting")
        for _ in range(20):
            if self._stop_event.wait(0.25):
                return
            if self._process.poll() is not None:
                LOGGER.error("Local voice bridge exited during startup with code %s", self._process.returncode)
                self._process = None
                self._close_server_log()
                self.set_status("Start failed")
                return
            healthy, _ = probe_health(timeout=0.5)
            if healthy:
                self._health_failures = 0
                self.set_status("Ready")
                return
        self.set_status("Unhealthy")

    def _close_server_log(self) -> None:
        if self._server_log_handle is not None:
            try:
                self._server_log_handle.close()
            except OSError:
                pass
            self._server_log_handle = None

    def stop_owned_server(self) -> None:
        with self._operation_lock:
            self._stop_owned_server_locked()

    def _stop_owned_server_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            self._close_server_log()
            return
        if process.poll() is None:
            LOGGER.info("Stopping owned local voice bridge PID %s", process.pid)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                LOGGER.warning("Force-killing owned local voice bridge PID %s", process.pid)
                process.kill()
                process.wait(timeout=5)
        self._close_server_log()

    def _restart_owned_server(self) -> None:
        with self._operation_lock:
            if self._stop_event.is_set():
                return
            self.set_status("Restarting")
            self._stop_owned_server_locked()
            self._last_start_attempt = 0.0
            self._health_failures = 0
            self._ensure_running_locked()

    def restart_async(self, *_: Any) -> None:
        if self._stop_event.is_set():
            return
        threading.Thread(target=self._restart_owned_server, name="voice-bridge-restart", daemon=True).start()

    def prepare_application_restart(self) -> bool:
        with self._operation_lock:
            if self._process is not None:
                return True
            healthy, payload = probe_health()
            if not healthy:
                return True
            if not same_installation_health(payload):
                LOGGER.warning("Refusing to stop a compatible server from another installation")
                return False
            self.set_status("Stopping existing service")
            stopped = request_same_installation_shutdown(payload)
            if not stopped:
                self.set_status("Restart blocked")
                LOGGER.error("Could not stop the existing same-installation server")
                return False
            self._last_start_attempt = 0.0
            return True

    def clear_generated_audio_files(self):
        result = clear_generated_audio(AUDIO_DIR)
        LOGGER.info(
            "Cleared generated audio: deleted=%s bytes=%s failed=%s",
            result.deleted_files,
            result.deleted_bytes,
            result.failed_files,
        )
        return result

    def open_controller_log(self, *_: Any) -> None:
        configure_logging()
        open_path(CONTROLLER_LOG)

    def open_audio_folder(self, *_: Any) -> None:
        open_path(AUDIO_DIR)

    def open_reference_folder(self, *_: Any) -> None:
        open_path(REFERENCE_DIR)

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_started:
                return
            self._shutdown_started = True
        self._stop_event.set()
        self.set_status("Stopping")
        monitor = self._monitor_thread
        if monitor is not None and monitor.is_alive() and monitor is not threading.current_thread():
            monitor.join(timeout=10)
            if monitor.is_alive():
                LOGGER.warning("Voice bridge monitor did not stop within the shutdown timeout")
        self.stop_owned_server()
        self.set_status_callback(None)


class StatusRelay(QObject):
    status_changed = Signal(str)


class VoiceBridgeQtRuntime(QObject):
    def __init__(
        self,
        app: QApplication,
        controller: VoiceBridgeController | None = None,
        *,
        pet_root: Path = PET_ROOT,
        settings_path: Path = DESKTOP_PET_SETTINGS,
        control_panel_client: Any | None = None,
        panel_state_path: Path = CONTROL_PANEL_WINDOW_SETTINGS,
        conversation_controller: Any | None = None,
        keyboard_hook: Any | None = None,
        start_panel_polling: bool = True,
        start_monitor: bool = True,
        show_tray: bool = True,
    ) -> None:
        super().__init__()
        self.app = app
        self.controller = controller or VoiceBridgeController()
        self._shutdown_started = False
        self._setup_after_exit = False
        self._restart_after_exit = False
        self._uninstall_after_exit = False
        self._voice_bridge_status = "Starting"
        self.status_relay = StatusRelay(self)
        self.status_relay.status_changed.connect(self._apply_status)

        self.pet = DesktopPetWindow(pet_root, DesktopPetSettingsStore(settings_path))
        self.control_panel_client = control_panel_client or ControlPanelApiClient()
        self.control_panel = LocalVoiceControlPanel(
            self.control_panel_client,
            state_path=panel_state_path,
            start_polling=start_panel_polling,
        )
        self.voice_conversation = conversation_controller or VoiceConversationController(
            self.control_panel_client,
            event_logger=RuntimeEventLogger(default_event_log_path(APP_ROOT)),
        )
        self.right_ctrl_hook = keyboard_hook or GlobalRightCtrlHook(self.voice_conversation.handle_key_event)
        self.conversation_settings_timer = QTimer(self)
        self.conversation_settings_timer.setInterval(500)
        self.conversation_settings_timer.timeout.connect(self.sync_conversation_settings)
        self.conversation_settings_timer.start()
        self.sync_conversation_settings()
        self.right_ctrl_hook.start()
        self.pet.panel_toggle_requested.connect(self.toggle_control_panel)
        self.control_panel.visibility_changed.connect(self._sync_panel_action)
        self.pet_settings_timer = QTimer(self)
        self.pet_settings_timer.setInterval(500)
        self.pet_settings_timer.timeout.connect(self.sync_pet_settings_from_disk)
        self.pet_settings_timer.start()

        self.tray_icon = QSystemTrayIcon(create_tray_icon(), self)
        self.tray_icon.setToolTip(APP_NAME)
        self.menu = QMenu()
        self._build_menu()
        self.tray_icon.setContextMenu(self.menu)
        self.controller.set_status_callback(self.status_relay.status_changed.emit)
        self._sync_all_actions()
        if show_tray:
            self.tray_icon.show()
        if start_monitor:
            QTimer.singleShot(0, self.controller.start_monitor)

    def _build_menu(self) -> None:
        self.status_action = QAction("Status: Starting", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        self.panel_action = self.menu.addAction("Show Local Voice panel")
        self.panel_action.triggered.connect(self.toggle_control_panel)
        self.pet_return_action = self.menu.addAction("Bring Desktop Pet Back")
        self.pet_return_action.triggered.connect(self.bring_desktop_pet_back)
        self.menu.addSeparator()

        restart_action = self.menu.addAction("Restart Voice Bridge")
        restart_action.triggered.connect(self.restart_application)
        controller_log_action = self.menu.addAction("Open controller log")
        controller_log_action.triggered.connect(self.controller.open_controller_log)
        audio_action = self.menu.addAction("Open generated audio folder")
        audio_action.triggered.connect(self.controller.open_audio_folder)
        clear_audio_action = self.menu.addAction("Clear generated audio...")
        clear_audio_action.triggered.connect(self.clear_generated_audio)
        reference_action = self.menu.addAction("Open reference voices folder")
        reference_action.triggered.connect(self.controller.open_reference_folder)

        self.menu.addSeparator()
        self.startup_action = QAction("Start with Windows", self.menu)
        self.startup_action.setCheckable(True)
        self.startup_action.toggled.connect(self._set_startup_enabled)
        self.menu.addAction(self.startup_action)
        setup_action = self.menu.addAction("Exit and run environment setup")
        setup_action.triggered.connect(self.exit_and_run_setup)
        uninstall_action = self.menu.addAction("Uninstall Local Voice Bridge...")
        uninstall_action.triggered.connect(self.uninstall_application)
        self.menu.addSeparator()
        exit_action = self.menu.addAction("Exit")
        exit_action.triggered.connect(self.shutdown)

    def _sync_all_actions(self) -> None:
        self.startup_action.blockSignals(True)
        self.startup_action.setChecked(is_startup_enabled())
        self.startup_action.blockSignals(False)
        self._sync_panel_action(self.control_panel.isVisible())

    def _sync_panel_action(self, visible: bool) -> None:
        if hasattr(self, "panel_action"):
            self.panel_action.setText("Hide Local Voice panel" if visible else "Show Local Voice panel")

    def toggle_control_panel(self, *_: Any) -> None:
        self.control_panel.toggle_visibility()
        self._sync_panel_action(self.control_panel.isVisible())

    def bring_desktop_pet_back(self, *_: Any) -> None:
        self.pet.reset_position()
        self.pet.show_pet()

    def _apply_status(self, status: str) -> None:
        self._voice_bridge_status = str(status or "")
        self.status_action.setText(f"Status: {status}")
        self.tray_icon.setToolTip(f"{APP_NAME}\n{status}")
        self.pet.set_voice_bridge_status(status)

    def sync_pet_settings_from_disk(self) -> None:
        try:
            self.pet.sync_settings_from_disk()
        except (OSError, ValueError):
            LOGGER.warning("Desktop pet settings could not be synchronized", exc_info=True)

    def _sync_pet_playback_state(self, snapshot: Any) -> None:
        if not self._voice_bridge_status.startswith("Ready"):
            return
        data = snapshot if isinstance(snapshot, dict) else {}
        extension = data.get("extension") if isinstance(data.get("extension"), dict) else {}
        voice_runtime = data.get("voiceRuntime") if isinstance(data.get("voiceRuntime"), dict) else {}
        phases = {
            str(extension.get("playbackPhase") or "idle").strip().lower(),
            str(voice_runtime.get("phase") or "idle").strip().lower(),
        }
        if bool(extension.get("isPlaying")) or "playing" in phases:
            state = "talking"
        elif "generating" in phases:
            state = "thinking"
        elif "error" in phases or str(extension.get("statusLevel") or "").strip().lower() == "error":
            state = "error"
        else:
            state = "idle"
        if self.pet.current_state != state:
            self.pet.set_state(state)

    def sync_conversation_settings(self) -> None:
        try:
            snapshot = self.control_panel_client.get_snapshot()
            settings = snapshot.get("settings") if isinstance(snapshot, dict) else {}
            conversation = snapshot.get("conversation") if isinstance(snapshot, dict) else {}
            if not isinstance(settings, dict):
                settings = {}
            if not isinstance(conversation, dict):
                conversation = {}
            self.voice_conversation.configure(
                enabled=bool(settings.get("micConversationEnabled")),
                stt_model=str(settings.get("sttModel") or "small"),
                cancel_grace_ms=int(settings.get("cancelGraceMs", 700)),
            )
            reconcile = getattr(self.voice_conversation, "reconcile_reported_state", None)
            if callable(reconcile):
                reconcile(conversation)
            self._sync_pet_playback_state(snapshot)
        except (OSError, RuntimeError, ValueError, TypeError, urllib.error.URLError):
            return

    def _set_startup_enabled(self, enabled: bool) -> None:
        try:
            set_startup_enabled(bool(enabled))
        except (OSError, RuntimeError) as exc:
            LOGGER.error("Could not update Windows startup entry: %s", exc)
            show_message(
                APP_NAME,
                f"自動起動を変更できませんでした。\n\n{exc}",
                error=True,
            )
        finally:
            self.startup_action.blockSignals(True)
            self.startup_action.setChecked(is_startup_enabled())
            self.startup_action.blockSignals(False)

    def clear_generated_audio(self, *_: Any) -> None:
        answer = QMessageBox.question(
            None,
            APP_NAME,
            "生成済み音声を削除します。参照音声と設定は削除されません。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.controller.clear_generated_audio_files()
        show_message(
            APP_NAME,
            f"生成音声を {result.deleted_files} 件、{format_bytes(result.deleted_bytes)} 削除しました。"
            + (f"\n削除できなかったファイル: {result.failed_files} 件" if result.failed_files else ""),
        )

    def uninstall_application(self, *_: Any) -> None:
        if not IS_WINDOWS or not UNINSTALL_SCRIPT.is_file():
            show_message(APP_NAME, "アンインストールスクリプトが見つかりません。", error=True)
            return
        answer = QMessageBox.question(
            None,
            APP_NAME,
            "自動起動とスタートメニュー登録を解除し、生成音声とログを削除します。\n"
            "参照音声、設定、モデル、リポジトリ本体は残ります。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._uninstall_after_exit = True
        self.shutdown()

    def exit_and_run_setup(self) -> None:
        if not IS_WINDOWS or not SETUP_SCRIPT.is_file():
            show_message(
                APP_NAME,
                "setup-voice-env.cmd が見つかりません。",
                error=True,
            )
            return
        self._setup_after_exit = True
        self.shutdown()

    def restart_application(self, *_: Any) -> None:
        if not IS_WINDOWS or not LAUNCHER_EXE.is_file():
            show_message(
                APP_NAME,
                "LocalVoiceBridge.exe が見つかりません。",
                error=True,
            )
            return
        if not self.controller.prepare_application_restart():
            show_message(
                APP_NAME,
                "既存の音声APIを安全に停止できなかったため、再起動を中止しました。controller.logを確認してください。",
                error=True,
            )
            return
        self._restart_after_exit = True
        self.shutdown()

    def _launch_application_after_exit(self) -> None:
        release_single_instance()
        creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [str(LAUNCHER_EXE)],
            cwd=APP_ROOT,
            creationflags=creationflags,
        )

    def _launch_uninstall(self) -> None:
        creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(UNINSTALL_SCRIPT),
                "-RemoveGeneratedData",
            ],
            cwd=APP_ROOT,
            creationflags=creationflags,
        )

    def _launch_setup(self) -> None:
        command = f'timeout /t 2 /nobreak >nul & call "{SETUP_SCRIPT}"'
        subprocess.Popen(
            ["cmd.exe", "/c", "start", f"{APP_NAME} setup", "cmd.exe", "/k", command],
            cwd=APP_ROOT,
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )

    def shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.conversation_settings_timer.stop()
        self.right_ctrl_hook.stop()
        self.voice_conversation.shutdown()
        self.pet_settings_timer.stop()
        self.pet.persist_settings()
        self.controller.shutdown()
        self.control_panel.shutdown()
        self.pet.shutdown()
        self.tray_icon.hide()
        self.tray_icon.setContextMenu(None)
        self.menu.close()
        self.tray_icon.deleteLater()
        if self._uninstall_after_exit:
            self._launch_uninstall()
        elif self._setup_after_exit:
            self._launch_setup()
        elif self._restart_after_exit:
            self._launch_application_after_exit()
        self.app.quit()


def main() -> int:
    configure_logging()
    if IS_WINDOWS:
        try:
            migrate_legacy_startup()
        except OSError:
            LOGGER.warning("Could not migrate the legacy VBS startup entry", exc_info=True)
    if not IS_WINDOWS:
        LOGGER.error("Tray mode is supported only on Windows")
        return 2
    if not acquire_single_instance():
        LOGGER.info("Tray application is already running; duplicate launch ignored")
        return 0

    app = create_qt_application(sys.argv)
    runtime: VoiceBridgeQtRuntime | None = None
    try:
        runtime = VoiceBridgeQtRuntime(app)
        return app.exec()
    except Exception:
        LOGGER.exception("Tray application failed")
        show_message(
            APP_NAME,
            "起動に失敗しました。controller.log を確認し、setup-voice-env.cmd を再実行してください。",
            error=True,
        )
        return 2
    finally:
        if runtime is not None:
            runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
