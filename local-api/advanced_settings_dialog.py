from __future__ import annotations

from typing import Any, Callable, Protocol

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class AdvancedSettingsClient(Protocol):
    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class AdvancedSettingsDialog(QDialog):
    """Windows-owned runtime settings; browser-only preview settings stay in the extension."""

    def __init__(
        self,
        client: AdvancedSettingsClient,
        *,
        open_browser_settings: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self._open_browser_settings = open_browser_settings
        self.setWindowTitle("Local Voice Bridge 詳細設定")
        self.setModal(False)
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        intro = QLabel(
            "音声ランタイムとマイク会話の詳細設定です。"
            "ChatGPT返答の読み上げ範囲だけはブラウザ設定で管理します。",
            self,
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.stt_model_combo = QComboBox(self)
        self.stt_model_combo.addItem("Small — 軽量", "small")
        self.stt_model_combo.addItem("Medium — 精度重視", "medium")
        self.stt_model_combo.addItem("Large v3 Turbo — 高精度・高速", "large-v3-turbo")
        form.addRow("文字起こしモデル", self.stt_model_combo)

        self.cancel_grace_spin = QDoubleSpinBox(self)
        self.cancel_grace_spin.setRange(0.0, 5.0)
        self.cancel_grace_spin.setDecimals(1)
        self.cancel_grace_spin.setSingleStep(0.1)
        self.cancel_grace_spin.setSuffix(" 秒")
        form.addRow("送信前の猶予", self.cancel_grace_spin)

        self.live_profile_combo = QComboBox(self)
        self.live_profile_combo.addItem("Speed — 低遅延", "speed")
        self.live_profile_combo.addItem("Balanced — バランス", "balanced")
        self.live_profile_combo.addItem("Bridge — 品質重視", "bridge")
        form.addRow("Live TTSプロファイル", self.live_profile_combo)
        root.addLayout(form)

        self.browser_settings_button = QPushButton("ブラウザの読み上げ範囲設定", self)
        self.browser_settings_button.clicked.connect(self._open_browser_settings)
        root.addWidget(self.browser_settings_button)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("閉じる", self)
        cancel_button.clicked.connect(self.close)
        self.save_button = QPushButton("保存", self)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(cancel_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
        extension = snapshot.get("extension") if isinstance(snapshot.get("extension"), dict) else {}

        stt_model = str(settings.get("sttModel") or "small")
        stt_index = self.stt_model_combo.findData(stt_model)
        self.stt_model_combo.setCurrentIndex(max(0, stt_index))

        try:
            grace_ms = int(settings.get("cancelGraceMs", 700))
        except (TypeError, ValueError):
            grace_ms = 700
        self.cancel_grace_spin.setValue(max(0.0, min(5.0, grace_ms / 1000.0)))

        live_profile = str(settings.get("liveTtsProfile") or "speed")
        profile_index = self.live_profile_combo.findData(live_profile)
        self.live_profile_combo.setCurrentIndex(max(0, profile_index))

        browser_settings_available = bool(extension.get("connected")) and not bool(extension.get("updateRequired"))
        self.browser_settings_button.setEnabled(browser_settings_available)
        if not browser_settings_available:
            self.browser_settings_button.setToolTip("拡張機能が接続すると利用できます。")
        else:
            self.browser_settings_button.setToolTip("")

    def _save(self) -> None:
        payload = {
            "sttModel": str(self.stt_model_combo.currentData() or "small"),
            "cancelGraceMs": round(self.cancel_grace_spin.value() * 1000),
            "liveTtsProfile": str(self.live_profile_combo.currentData() or "speed"),
        }
        self.save_button.setEnabled(False)
        try:
            self.client.update_settings(payload)
        except Exception as exc:
            self.status_label.setText(f"保存できませんでした: {exc}")
            return
        finally:
            self.save_button.setEnabled(True)
        self.status_label.setText("設定を保存しました")
