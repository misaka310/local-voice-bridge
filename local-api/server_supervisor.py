from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from maintenance import clear_generated_audio
from windows_integration import open_path

LOCAL_API_DIR = Path(__file__).resolve().parent
APP_ROOT = LOCAL_API_DIR.parent
VENV_SCRIPTS = LOCAL_API_DIR / ".venv" / "Scripts"
SERVER_PYTHON = VENV_SCRIPTS / "python.exe"
SERVER_SCRIPT = LOCAL_API_DIR / "server.py"
PREFLIGHT_SCRIPT = LOCAL_API_DIR / "scripts" / "preflight_irodori.py"
RUNTIME_DIR = LOCAL_API_DIR / "runtime"
LOG_DIR = LOCAL_API_DIR / "logs"
CONTROLLER_LOG = LOG_DIR / "controller.log"
SERVER_LOG = LOG_DIR / "server.log"
AUDIO_DIR = RUNTIME_DIR / "audio"
INSTANCE_STATE_PATH = RUNTIME_DIR / "server-instance.json"
INSTALLATION_ID = hashlib.sha256(str(APP_ROOT).casefold().encode("utf-8")).hexdigest()[:20]
REFERENCE_DIR = LOCAL_API_DIR / "reference" / "voices"
HEALTH_URL = "http://127.0.0.1:8717/health"
PORT = 8717
HEALTH_INTERVAL_SECONDS = 5.0
RESTART_MIN_INTERVAL_SECONDS = 10.0
PREFLIGHT_TIMEOUT_SECONDS = 180.0
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
LOGGER = logging.getLogger("local-voice-bridge-tray")


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
