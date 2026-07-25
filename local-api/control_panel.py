from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
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


class ControlPanelClient(Protocol):
    def get_snapshot(self) -> dict[str, Any]: ...

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def send_command(self, command: str) -> dict[str, Any]: ...

    def send_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def update_conversation_state(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class ControlPanelApiClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8717", *, timeout: float = 0.4) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.timeout = max(0.1, float(timeout))

    def _request(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise RuntimeError(str(body.get("error") if isinstance(body, dict) else "invalid response"))
        return body

    def get_snapshot(self) -> dict[str, Any]:
        return self._request("/v1/control-panel")

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v1/control-panel/settings", method="POST", payload=payload)

    def send_command(self, command: str) -> dict[str, Any]:
        return self._request("/v1/control-panel/command", method="POST", payload={"command": command})

    def send_conversation_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "/v1/conversation/event",
            method="POST",
            payload={"type": event_type, "payload": payload},
        )

    def update_conversation_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/v1/conversation/state", method="POST", payload=payload)


class PanelWindowStateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load_position(self) -> QPoint | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            return QPoint(int(payload["x"]), int(payload["y"]))
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def save_position(self, position: QPoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "x": int(position.x()), "y": int(position.y())}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)


def clamp_window_position(
    position: QPoint,
    window_size: QSize,
    screen_geometries: list[QRect],
    *,
    margin: int = 8,
) -> QPoint:
    """Keep a window fully reachable after monitor topology changes."""
    if not screen_geometries:
        return QPoint(position)

    width = max(1, int(window_size.width()))
    height = max(1, int(window_size.height()))
    requested = QRect(position, QSize(width, height))
    safe_geometries: list[QRect] = []
    for geometry in screen_geometries:
        safe = geometry.adjusted(margin, margin, -margin, -margin)
        if safe.width() < 1 or safe.height() < 1:
            safe = QRect(geometry)
        safe_geometries.append(safe)
        if safe.contains(requested):
            return QPoint(position)

    requested_center = requested.center()

    def target_score(geometry: QRect) -> tuple[int, int]:
        intersection = geometry.intersected(requested)
        intersection_area = max(0, intersection.width()) * max(0, intersection.height())
        dx = 0
        if requested_center.x() < geometry.left():
            dx = geometry.left() - requested_center.x()
        elif requested_center.x() > geometry.right():
            dx = requested_center.x() - geometry.right()
        dy = 0
        if requested_center.y() < geometry.top():
            dy = geometry.top() - requested_center.y()
        elif requested_center.y() > geometry.bottom():
            dy = requested_center.y() - geometry.bottom()
        return (-intersection_area, dx * dx + dy * dy)

    target = min(safe_geometries, key=target_score)
    max_x = target.x() + max(0, target.width() - width)
    max_y = target.y() + max(0, target.height() - height)
    return QPoint(
        min(max(position.x(), target.x()), max_x),
        min(max(position.y(), target.y()), max_y),
    )


class LocalVoiceControlPanel(QWidget):
    visibility_changed = Signal(bool)

    def __init__(
        self,
        client: ControlPanelClient,
        *,
        state_path: Path,
        start_polling: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.state_store = PanelWindowStateStore(state_path)
        self._updating_controls = False
        self._shutting_down = False
        self._poll_when_visible = bool(start_polling)
        self._current_text_full = "No assistant response yet"
        self._drag_offset: QPoint | None = None

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
        self.refresh_timer.setInterval(750)
        self.refresh_timer.timeout.connect(self.refresh_now)

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

        self.queue_label = QLabel("Queue 0 · 0 tabs", card)
        self.queue_label.setObjectName("panel-queue")
        card_layout.addWidget(self.queue_label)

        ref_row = QHBoxLayout()
        ref_label = QLabel("Ref", card)
        ref_label.setFixedWidth(46)
        self.reference_combo = QComboBox(card)
        self.reference_combo.setObjectName("panel-reference")
        self.reference_combo.currentIndexChanged.connect(self._on_reference_changed)
        ref_row.addWidget(ref_label)
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
        self.replay_button = QPushButton("Replay", card)
        self.replay_button.clicked.connect(lambda: self._send_command("replay"))
        for button in (self.auto_button, self.next_button, self.regen_button, self.replay_button):
            button.setMinimumHeight(30)
            controls.addWidget(button)
        card_layout.addLayout(controls)

        self.setStyleSheet(
            """
            QFrame#panel-card {
                background: rgba(10, 12, 18, 238);
                border: 1px solid rgba(120, 180, 255, 70);
                border-radius: 14px;
            }
            QLabel { color: #f5f7ff; font: 12px 'Segoe UI'; }
            QLabel#panel-title { font-size: 14px; font-weight: 700; }
            QLabel#panel-status { color: #9fd0ff; font-weight: 600; }
            QLabel#panel-current-text { color: #c8d2e8; }
            QLabel#panel-queue { color: #8792a8; font-size: 11px; }
            QPushButton, QComboBox {
                color: #f5f7ff;
                background: rgba(255, 255, 255, 18);
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 8px;
                padding: 5px 8px;
            }
            QPushButton:hover, QComboBox:hover { background: rgba(255, 255, 255, 30); }
            QPushButton:checked { background: rgba(73, 168, 113, 80); border-color: rgba(73, 168, 113, 150); }
            QPushButton:disabled { color: #667085; background: rgba(255, 255, 255, 8); }
            QPushButton#panel-hide { padding: 0; font-size: 17px; }
            QComboBox QAbstractItemView { background: #171b25; color: #f5f7ff; selection-background-color: #2f6feb; }
            QSlider::groove:horizontal { height: 4px; background: #3a4252; border-radius: 2px; }
            QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px; background: #8fc7ff; }
            """
        )

    def refresh_now(self) -> None:
        try:
            snapshot = self.client.get_snapshot()
        except (OSError, RuntimeError, ValueError, urllib.error.URLError):
            self._apply_disconnected_state()
            return
        self.apply_snapshot(snapshot)

    def _apply_disconnected_state(self) -> None:
        self.status_label.setText("Voice Bridge starting")
        self._set_current_text("Waiting for local API", tooltip="")
        self.queue_label.setText("Queue 0 · 0 tabs")
        for button in (self.next_button, self.regen_button, self.replay_button):
            button.setEnabled(False)

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
        if connected:
            status = str(extension.get("statusText") or "Ready")
            phase = str(extension.get("playbackPhase") or "idle")
            if phase == "generating":
                status = "Generating"
            elif phase == "playing":
                status = "Playing"
            self.status_label.setText(status)
            current_text = str(extension.get("currentText") or "No assistant response yet")
            self._set_current_text(current_text)
        else:
            self.status_label.setText("拡張機能を再読み込みしてください")
            self._set_current_text(
                "Chrome / Braveの拡張機能画面でLocal Voice Bridgeを再読み込みしてください。ChatGPTタブの再読み込みは不要です。"
            )

        mic_enabled = bool(settings.get("micConversationEnabled"))
        stt_installed = bool(components.get("sttInstalled"))
        self.mic_button.setEnabled(stt_installed)
        if not stt_installed:
            self._set_mic_button_text(
                "マイク会話（追加セットアップ）",
                "読み上げ + マイク会話を追加してください",
            )
        elif mic_enabled:
            if connected:
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

        queue_size = max(0, int(extension.get("queueSize") or 0))
        tabs_count = max(0, int(extension.get("tabsCount") or 0))
        self.queue_label.setText(f"Queue {queue_size} · {tabs_count} tabs")
        self.next_button.setEnabled(connected)
        self.regen_button.setEnabled(connected)
        self.replay_button.setEnabled(connected and bool(extension.get("replayAvailable")))

        if bool(extension.get("updateRequired")):
            loaded = str(extension.get("loadedVersion") or "旧版")
            expected = str(extension.get("expectedVersion") or "最新版")
            self.status_label.setText("拡張機能の再読み込みが必要")
            self._set_current_text(
                f"Chrome / Braveの拡張機能画面でLocal Voice Bridgeを再読み込みしてください（{loaded} → {expected}）"
            )

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
        self._update_settings({"voiceVolume": round(int(value) / 100.0, 2)})

    def _on_reference_changed(self, _index: int) -> None:
        if self._updating_controls:
            return
        self._update_settings({"referenceVoice": str(self.reference_combo.currentData() or "")})

    def _update_settings(self, payload: dict[str, Any]) -> None:
        try:
            self.client.update_settings(payload)
        except (OSError, RuntimeError, ValueError, urllib.error.URLError):
            self.status_label.setText("Control update failed")

    def _send_command(self, command: str) -> None:
        try:
            self.client.send_command(command)
        except (OSError, RuntimeError, ValueError, urllib.error.URLError):
            self.status_label.setText("Command failed")

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
        if self._poll_when_visible and not self.refresh_timer.isActive():
            self.refresh_timer.start()
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
        self.state_store.save_position(self.pos())
        self.hide()
        self.close()
        self.deleteLater()
