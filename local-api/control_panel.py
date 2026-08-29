from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from control_panel_async import AsyncControlPanelDispatcher
from control_panel_client import ControlPanelApiClient
from control_panel_style import PANEL_STYLE
from panel_window_state import PanelWindowStateStore, clamp_window_position

ACTIVE_REFRESH_MS = 750
IDLE_REFRESH_MS = 5000


class ControlPanelClient(Protocol):
    def get_snapshot(self) -> dict[str, Any]: ...

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def send_command(self, command: str) -> dict[str, Any]: ...

    def send_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_conversation_state(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class LocalVoiceControlPanel(QWidget):
    visibility_changed = Signal(bool)

    def __init__(
        self,
        client: ControlPanelClient,
        *,
        state_path: Path,
        start_polling: bool = True,
        async_requests: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.state_store = PanelWindowStateStore(state_path)
        self._updating_controls = False
        self._shutting_down = False
        self._poll_when_visible = bool(start_polling)
        self._current_text_full = "No assistant response yet"
        self._reload_extension_requested = False
        self._drag_offset: QPoint | None = None
        self._request_dispatcher = AsyncControlPanelDispatcher(self, client, async_requests=async_requests)

        self.setObjectName("local-voice-control-panel")
        self.setWindowTitle("Local Voice Bridge")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(320)
        self.setMaximumWidth(360)
        self._build_ui()

        saved_position = self.state_store.load_position()
        if saved_position is not None:
            self.move(saved_position)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self._next_refresh_ms = IDLE_REFRESH_MS
        self.refresh_timer.timeout.connect(self.refresh_now)
        self.volume_update_timer = QTimer(self)
        self.volume_update_timer.setSingleShot(True)
        self.volume_update_timer.timeout.connect(self._flush_volume_change)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("panel-card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)
        root.addWidget(card)

        header = QHBoxLayout()
        self.title_label = QLabel("Local Voice", card)
        self.title_label.setObjectName("panel-title")
        header.addWidget(self.title_label)
        header.addStretch(1)
        self.hide_button = QPushButton("×", card)
        self.hide_button.setObjectName("panel-hide")
        self.hide_button.setFixedSize(26, 26)
        self.hide_button.clicked.connect(self.hide_panel)
        header.addWidget(self.hide_button)
        card_layout.addLayout(header)

        self.status_label = QLabel("Waiting for ChatGPT", card)
        self.status_label.setObjectName("panel-status")
        card_layout.addWidget(self.status_label)

        self.current_text_label = QLabel(self._current_text_full, card)
        self.current_text_label.setObjectName("panel-current-text")
        self.current_text_label.setWordWrap(False)
        self.current_text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(self.current_text_label)

        self.reload_extension_button = QPushButton("拡張機能を再読み込み", card)
        self.reload_extension_button.setObjectName("panel-reload-extension")
        self.reload_extension_button.setMinimumHeight(30)
        self.reload_extension_button.clicked.connect(self._reload_extension)
        self.reload_extension_button.hide()
        card_layout.addWidget(self.reload_extension_button)

        self.queue_label = QLabel("Queue 0 · 0 tabs", card)
        self.queue_label.setObjectName("panel-queue")
        card_layout.addWidget(self.queue_label)

        ref_row = QHBoxLayout()
        self.reference_label = QLabel("Voice", card)
        self.reference_label.setFixedWidth(46)
        self.reference_label.setToolTip("参照音声を選択します。同じIDのペット素材がある場合はペットも連動します。")
        self.reference_combo = QComboBox(card)
        self.reference_combo.setObjectName("panel-reference")
        self.reference_combo.setToolTip(self.reference_label.toolTip())
        self.reference_combo.currentIndexChanged.connect(self._on_reference_changed)
        ref_row.addWidget(self.reference_label)
        ref_row.addWidget(self.reference_combo, 1)
        card_layout.addLayout(ref_row)

        volume_row = QHBoxLayout()
        volume_label = QLabel("Volume", card)
        volume_label.setFixedWidth(46)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal, card)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_value = QLabel("60%", card)
        self.volume_value.setFixedWidth(38)
        self.volume_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        volume_row.addWidget(volume_label)
        volume_row.addWidget(self.volume_slider, 1)
        volume_row.addWidget(self.volume_value)
        card_layout.addLayout(volume_row)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(6)
        self.mic_button = QPushButton("マイク会話　右Ctrl＋＼ 長押し", card)
        self.mic_button.setObjectName("panel-mic")
        self.mic_button.setCheckable(True)
        self.mic_button.setMinimumHeight(30)
        self.mic_button.clicked.connect(self._on_mic_clicked)
        mic_row.addWidget(self.mic_button, 1)
        card_layout.addLayout(mic_row)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.auto_button = QPushButton("Auto", card)
        self.auto_button.setCheckable(True)
        self.auto_button.clicked.connect(self._on_auto_clicked)
        self.next_button = QPushButton("Next", card)
        self.next_button.clicked.connect(lambda: self._send_command("next"))
        self.regen_button = QPushButton("Regen", card)
        self.regen_button.clicked.connect(lambda: self._send_command("regen"))
        self.stop_button = QPushButton("Stop", card)
        self.stop_button.clicked.connect(lambda: self._send_command("stop"))
        self.replay_button = QPushButton("Replay", card)
        self.replay_button.clicked.connect(lambda: self._send_command("replay"))
        for button in (self.auto_button, self.next_button, self.regen_button, self.stop_button, self.replay_button):
            button.setMinimumHeight(30)
            controls.addWidget(button)
        card_layout.addLayout(controls)

        self.details_button = QPushButton("詳細設定", card)
        self.details_button.setObjectName("panel-details")
        self.details_button.setMinimumHeight(30)
        self.details_button.clicked.connect(lambda: self._send_command("open_options"))
        card_layout.addWidget(self.details_button)
        self.setStyleSheet(PANEL_STYLE)

    def refresh_now(self) -> None:
        self._request_dispatcher.refresh()

    def _schedule_refresh(self, delay_ms: int) -> None:
        self._next_refresh_ms = max(1, int(delay_ms))
        if self._poll_when_visible and self.isVisible():
            self.refresh_timer.start(self._next_refresh_ms)

    def _apply_disconnected_state(self) -> None:
        self.status_label.setText("Voice Bridge starting")
        self._set_current_text("Waiting for local API", tooltip="")
        self.queue_label.setText("Queue 0 · 0 tabs")
        self._reload_extension_requested = False
        self.reload_extension_button.hide()
        self.reload_extension_button.setEnabled(False)
        for control in (self.auto_button, self.mic_button, self.reference_combo, self.volume_slider, self.details_button):
            control.setEnabled(False)
        for button in (self.next_button, self.regen_button, self.stop_button, self.replay_button):
            button.setEnabled(False)
        self._schedule_refresh(ACTIVE_REFRESH_MS)

    def _set_mic_button_text(self, title: str, detail: str, *, visible_detail: str = "") -> None:
        safe_title = str(title or "マイク会話")
        safe_detail = str(detail or "")
        safe_visible_detail = str(visible_detail or "")
        self.mic_button.setText(
            f"{safe_title}　{safe_visible_detail}" if safe_visible_detail else safe_title
        )
        self.mic_button.setToolTip(safe_detail)

    def apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
        extension = snapshot.get("extension") if isinstance(snapshot.get("extension"), dict) else {}
        conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
        components = snapshot.get("components") if isinstance(snapshot.get("components"), dict) else {}
        readiness = snapshot.get("readiness") if isinstance(snapshot.get("readiness"), dict) else {}
        voice_runtime = snapshot.get("voiceRuntime") if isinstance(snapshot.get("voiceRuntime"), dict) else {}
        voices = snapshot.get("referenceVoices") if isinstance(snapshot.get("referenceVoices"), list) else []
        reference_voice = str(settings.get("referenceVoice") or "")

        self._updating_controls = True
        try:
            self._sync_reference_voices(voices, reference_voice)
            self.volume_slider.setValue(round(float(settings.get("voiceVolume", 0.6)) * 100))
            self.volume_value.setText(f"{self.volume_slider.value()}%")
            self.auto_button.setChecked(bool(settings.get("enabled")))
            self.mic_button.setChecked(bool(settings.get("micConversationEnabled")))
        finally:
            self._updating_controls = False

        connected = bool(extension.get("connected"))
        update_required = bool(extension.get("updateRequired"))
        reload_supported = bool(extension.get("supportsExtensionReload"))
        settings_available = connected and not update_required
        self.auto_button.setEnabled(settings_available)
        self.reference_combo.setEnabled(settings_available)
        self.volume_slider.setEnabled(settings_available)
        self.details_button.setEnabled(settings_available)
        if connected and not update_required:
            self._reload_extension_requested = False
        show_reload_button = (not connected) or (update_required and reload_supported)
        self.reload_extension_button.setVisible(show_reload_button)
        self.reload_extension_button.setEnabled(show_reload_button and not self._reload_extension_requested)
        model_state = str(readiness.get("deviceOrModel") or voice_runtime.get("readiness") or "").strip().lower()
        repair_required = bool(readiness.get("repairRequired") or voice_runtime.get("repairRequired"))
        runtime_error = str(voice_runtime.get("error") or "").strip()
        runtime_blocked = repair_required or model_state == "failed"
        runtime_loading = model_state in {"loading", "not_started"}

        if runtime_blocked:
            self.status_label.setText("音声ランタイムの修復が必要")
            self._set_current_text(
                runtime_error
                or "setup-voice-env.cmd を再実行し、依存関係と音声モデルを修復してください。"
            )
        elif runtime_loading:
            self.status_label.setText("音声モデルを準備中")
            self._set_current_text("音声モデルをGPUへ読み込み中です。保存済みモデルは再利用し、完了後に自動で利用可能になります。")
        elif connected:
            status = str(extension.get("statusText") or "").strip()
            phase = str(extension.get("playbackPhase") or voice_runtime.get("phase") or "idle")
            if not status:
                if phase == "generating":
                    status = "Generating"
                elif phase == "playing":
                    status = "Playing"
                else:
                    status = "Ready"
            self.status_label.setText(status)
            current_text = str(
                extension.get("currentText")
                or voice_runtime.get("currentText")
                or "No assistant response yet"
            )
            self._set_current_text(current_text)
        else:
            self.status_label.setText("拡張機能を再読み込みしてください")
            self._set_current_text(
                "下のボタンでLocal Voice Bridge拡張機能を再読み込みしてください。ChatGPTタブの再読み込みは不要です。"
            )

        mic_enabled = bool(settings.get("micConversationEnabled"))
        stt_installed = bool(components.get("sttInstalled"))
        self.mic_button.setEnabled(settings_available and stt_installed)
        if not stt_installed:
            self._set_mic_button_text(
                "マイク会話（追加セットアップ）",
                "読み上げ + マイク会話を追加してください",
            )
        elif mic_enabled:
            if connected and not runtime_blocked and not runtime_loading:
                self.status_label.setText(str(conversation.get("statusText") or "待機中（右Ctrl＋＼ 長押し）"))
            device = str(conversation.get("sttDevice") or "未ロード")
            device_label = "CUDA" if device.lower() == "cuda" else "CPU fallback" if device.lower() == "cpu" else device
            model_label = str(conversation.get("sttModel") or settings.get("sttModel") or "small")
            error = str(conversation.get("error") or "")
            self._set_mic_button_text(
                "マイク会話",
                error or f"右Ctrl＋＼（右Shift左） · STT {model_label} · {device_label}",
                visible_detail="エラー" if error else "右Ctrl＋＼ 長押し",
            )
        else:
            self._set_mic_button_text(
                "マイク会話",
                "オフ（右Ctrlは通常どおり使用できます）",
                visible_detail="右Ctrl＋＼ 長押し",
            )

        queue_size = max(
            0,
            int(extension.get("queueSize") or 0),
            int(voice_runtime.get("queueSize") or 0),
        )
        tabs_count = max(0, int(extension.get("tabsCount") or readiness.get("tabs") or 0))
        replay_available = bool(extension.get("replayAvailable") or voice_runtime.get("replayAvailable"))
        self.queue_label.setText(f"Queue {queue_size} · {tabs_count} tabs")
        controls_available = connected and not runtime_blocked and not update_required
        self.next_button.setEnabled(controls_available)
        self.regen_button.setEnabled(controls_available)
        self.stop_button.setEnabled(controls_available)
        self.replay_button.setEnabled(controls_available and not runtime_loading and replay_available)

        if update_required:
            loaded = str(extension.get("loadedVersion") or "旧版")
            expected = str(extension.get("expectedVersion") or "最新版")
            self.status_label.setText("拡張機能の再読み込みが必要")
            self._set_current_text(
                f"{'下のボタンでLocal Voice Bridgeを再読み込みできます' if reload_supported else 'この更新だけはChrome / Braveの拡張機能画面で手動再読み込みしてください'}（{loaded} → {expected}）"
            )

        if self._reload_extension_requested and show_reload_button:
            self.status_label.setText("拡張機能を再読み込みしています")
            self._set_current_text("再接続を待っています。ChatGPTタブの再読み込みは不要です。")

        conversation_phase = str(conversation.get("phase") or "idle").strip().lower()
        playback_phase = str(extension.get("playbackPhase") or voice_runtime.get("phase") or "idle").strip().lower()
        active = (
            runtime_loading
            or queue_size > 0
            or playback_phase in {"generating", "playing", "stopping"}
            or conversation_phase not in {"", "idle", "ready", "disabled", "off"}
        )
        self._schedule_refresh(ACTIVE_REFRESH_MS if active else IDLE_REFRESH_MS)

    def _set_current_text(self, text: str, *, tooltip: str | None = None) -> None:
        self._current_text_full = str(text or "")
        self.current_text_label.setToolTip(self._current_text_full if tooltip is None else tooltip)
        width = max(40, self.current_text_label.width())
        rendered = self.current_text_label.fontMetrics().elidedText(
            self._current_text_full,
            Qt.TextElideMode.ElideRight,
            width,
        )
        self.current_text_label.setText(rendered)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "current_text_label"):
            self._set_current_text(self._current_text_full)

    def _sync_reference_voices(self, voices: list[Any], selected: str) -> None:
        normalized: list[tuple[str, str]] = [("", "none")]
        seen = {""}
        for item in voices:
            if not isinstance(item, dict):
                continue
            voice_id = str(item.get("id") or "").strip()
            if voice_id in seen:
                continue
            seen.add(voice_id)
            normalized.append((voice_id, str(item.get("label") or voice_id)))
        if selected and selected not in seen:
            normalized.append((selected, selected))

        current_items = [str(self.reference_combo.itemData(index) or "") for index in range(self.reference_combo.count())]
        desired_items = [voice_id for voice_id, _label in normalized]
        if current_items != desired_items:
            self.reference_combo.clear()
            for voice_id, label in normalized:
                self.reference_combo.addItem(label, voice_id)
        target_index = self.reference_combo.findData(selected)
        self.reference_combo.setCurrentIndex(max(0, target_index))

    def _on_auto_clicked(self, checked: bool) -> None:
        if self._updating_controls:
            return
        payload: dict[str, Any] = {"enabled": bool(checked)}
        if not checked and self.mic_button.isChecked():
            self._updating_controls = True
            try:
                self.mic_button.setChecked(False)
            finally:
                self._updating_controls = False
            payload["micConversationEnabled"] = False
        self._update_settings(payload)

    def _on_mic_clicked(self, checked: bool) -> None:
        if self._updating_controls:
            return
        payload: dict[str, Any] = {"micConversationEnabled": bool(checked)}
        if checked:
            payload["enabled"] = True
        self._update_settings(payload)

    def _on_volume_changed(self, value: int) -> None:
        self.volume_value.setText(f"{int(value)}%")
        if self._updating_controls:
            return
        self.volume_update_timer.start(150)

    def _flush_volume_change(self) -> None:
        self._update_settings({"voiceVolume": round(self.volume_slider.value() / 100.0, 2)})

    def _on_reference_changed(self, _index: int) -> None:
        if self._updating_controls:
            return
        self._update_settings({"referenceVoice": str(self.reference_combo.currentData() or "")})

    def _update_settings(self, payload: dict[str, Any]) -> None:
        self._request_dispatcher.update_settings(payload)

    def _reload_extension(self) -> None:
        if self._reload_extension_requested:
            return
        self._reload_extension_requested = True
        self.reload_extension_button.setEnabled(False)
        self._request_dispatcher.reload_extension()

    def _send_command(self, command: str) -> None:
        self._request_dispatcher.send_command(command)

    def show_panel(self) -> None:
        self.refresh_now()
        self.show()
        corrected_position = clamp_window_position(
            self.pos(),
            self.frameGeometry().size(),
            [screen.availableGeometry() for screen in QApplication.screens()],
        )
        if corrected_position != self.pos():
            self.move(corrected_position)
            self.state_store.save_position(corrected_position)
        self.raise_()
        self.activateWindow()
        self._schedule_refresh(ACTIVE_REFRESH_MS)
        self.visibility_changed.emit(True)

    def hide_panel(self) -> None:
        was_visible = self.isVisible()
        if was_visible:
            self.state_store.save_position(self.pos())
        self.refresh_timer.stop()
        self.hide()
        if was_visible:
            self.visibility_changed.emit(False)

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide_panel()
        else:
            self.show_panel()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 42:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.state_store.save_position(self.pos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._shutting_down:
            event.accept()
            return
        event.ignore()
        self.hide_panel()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.refresh_timer.stop()
        self.volume_update_timer.stop()
        self._request_dispatcher.shutdown()
        self.state_store.save_position(self.pos())
        self.hide()
        self.close()
        self.deleteLater()
