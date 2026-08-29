from __future__ import annotations

import sys
import urllib.error
from pathlib import Path
from typing import Any, Sequence

LOCAL_API_DIR = Path(__file__).resolve().parent
if str(LOCAL_API_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_API_DIR))

from windows_integration import show_message  # noqa: E402

try:
    from PySide6.QtCore import QObject, QTimer, Qt, Signal
    from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon
except ImportError as exc:
    message = "PySide6 が見つかりません。Local Voice Bridge の環境修復を実行してください。"
    show_message("Local Voice Bridge", message, error=True)
    raise SystemExit(2) from exc

from control_panel import ControlPanelApiClient, LocalVoiceControlPanel  # noqa: E402
from conversation_controller import GlobalRightCtrlHook, VoiceConversationController  # noqa: E402
from desktop_pet import DesktopPetWindow  # noqa: E402
from desktop_pet_config import DesktopPetSettingsStore  # noqa: E402
from maintenance import format_bytes  # noqa: E402
from runtime_events import RuntimeEventLogger, default_event_log_path  # noqa: E402
from server_supervisor import APP_ROOT, LOGGER, VoiceBridgeController, configure_logging  # noqa: E402
from windows_integration import (  # noqa: E402
    APP_NAME,
    IS_WINDOWS,
    LAUNCHER_EXE,
    UNINSTALL_SCRIPT,
    acquire_single_instance,
    is_startup_enabled,
    launch_application,
    launch_setup,
    launch_uninstall,
    migrate_legacy_startup,
    release_single_instance,
    set_startup_enabled,
)

PET_ROOT = APP_ROOT / "extension" / "assets" / "pet"
RUNTIME_DIR = LOCAL_API_DIR / "runtime"
DESKTOP_PET_SETTINGS = RUNTIME_DIR / "desktop-pet-settings.json"
CONTROL_PANEL_WINDOW_SETTINGS = RUNTIME_DIR / "control-panel-window.json"


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
        self.control_panel.repair_requested.connect(self.exit_and_run_setup)
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
        if not IS_WINDOWS or not LAUNCHER_EXE.is_file():
            show_message(APP_NAME, "LocalVoiceBridge.exe が見つかりません。", error=True)
            return
        self._setup_after_exit = True
        self.shutdown()

    def restart_application(self, *_: Any) -> None:
        if not IS_WINDOWS or not LAUNCHER_EXE.is_file():
            show_message(APP_NAME, "LocalVoiceBridge.exe が見つかりません。", error=True)
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
        launch_application(LAUNCHER_EXE, APP_ROOT)

    def _launch_uninstall(self) -> None:
        launch_uninstall(UNINSTALL_SCRIPT, APP_ROOT)

    def _launch_setup(self) -> None:
        launch_setup(LAUNCHER_EXE, APP_ROOT)

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
            "起動に失敗しました。controller.log を確認し、Local Voice Bridge の環境修復を実行してください。",
            error=True,
        )
        return 2
    finally:
        if runtime is not None:
            runtime.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())