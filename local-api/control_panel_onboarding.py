from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from control_panel import LocalVoiceControlPanel

BACKGROUND_REFRESH_MS = 1000


class OnboardingStateStore:
    """Persists only whether the one-time Windows onboarding has completed."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def is_complete(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return False
        return bool(payload.get("completed")) if isinstance(payload, dict) else False

    def mark_complete(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"completed": True}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


class FirstRunOnboardingWidget(QWidget):
    test_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel-onboarding")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(6)

        title = QLabel("初回セットアップ", self)
        title.setObjectName("panel-onboarding-title")
        layout.addWidget(title)

        self.instructions_label = QLabel(
            "1. Chrome / Brave の拡張機能画面でデベロッパーモードを有効にし、"
            "このアプリの extension フォルダを読み込んでください。",
            self,
        )
        self.instructions_label.setWordWrap(True)
        layout.addWidget(self.instructions_label)

        self.connection_label = QLabel("2. 拡張機能の接続を待っています。", self)
        self.connection_label.setWordWrap(True)
        layout.addWidget(self.connection_label)

        self.test_button = QPushButton("3. テスト音声", self)
        self.test_button.setEnabled(False)
        self.test_button.clicked.connect(self.test_requested.emit)
        layout.addWidget(self.test_button)

        self.result_label = QLabel("", self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

    def set_connected(self, connected: bool) -> None:
        if connected:
            self.connection_label.setText("2. 拡張機能に接続しました。テスト音声を確認してください。")
            self.test_button.setEnabled(True)
        else:
            self.connection_label.setText("2. 拡張機能の接続を待っています。")
            self.test_button.setEnabled(False)

    def set_testing(self) -> None:
        self.test_button.setEnabled(False)
        self.result_label.setText("テスト音声を再生しています…")

    def set_failure(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.result_label.setText(f"テスト音声を確認できませんでした: {message}")


class OnboardingTestRunner(QObject):
    finished = Signal(object, object)

    def __init__(self, owner: QWidget, client: Any, *, async_requests: bool) -> None:
        super().__init__(owner)
        self.client = client
        self._closed = False
        self._executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-voice-onboarding")
            if async_requests
            else None
        )

    def start(self, *, reference_voice: str, voice_volume: float) -> bool:
        if self._closed:
            return False
        if self._executor is None:
            try:
                result = self.client.test_speech(
                    reference_voice=reference_voice,
                    voice_volume=voice_volume,
                )
            except Exception as exc:
                self.finished.emit(None, exc)
            else:
                self.finished.emit(result, None)
            return True
        try:
            future = self._executor.submit(
                self.client.test_speech,
                reference_voice=reference_voice,
                voice_volume=voice_volume,
            )
        except RuntimeError:
            return False
        future.add_done_callback(self._finish)
        return True

    def _finish(self, future: Future[Any]) -> None:
        if self._closed:
            return
        try:
            result = future.result()
        except Exception as exc:
            self.finished.emit(None, exc)
        else:
            self.finished.emit(result, None)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._executor is None:
            return
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            self._executor.shutdown(wait=False)


class FirstRunControlPanel(LocalVoiceControlPanel):
    """Adds one-time onboarding and one shared snapshot stream for visible and hidden states."""

    snapshot_applied = Signal(object)

    def __init__(
        self,
        client: Any,
        *,
        state_path: Path,
        start_polling: bool = True,
        async_requests: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            client,
            state_path=state_path,
            start_polling=start_polling,
            async_requests=async_requests,
            parent=parent,
        )
        self._background_sync_enabled = bool(start_polling)
        self.background_refresh_timer = QTimer(self)
        self.background_refresh_timer.setInterval(BACKGROUND_REFRESH_MS)
        self.background_refresh_timer.timeout.connect(self._refresh_while_hidden)
        if self._background_sync_enabled:
            self.background_refresh_timer.start()

        self.onboarding_store = OnboardingStateStore(
            Path(state_path).with_name("control-panel-onboarding.json")
        )
        self.onboarding_widget = FirstRunOnboardingWidget(self)
        root_layout = self.layout()
        if root_layout is not None:
            root_layout.insertWidget(0, self.onboarding_widget)
        if self.onboarding_store.is_complete():
            self.onboarding_widget.hide()
        else:
            self.onboarding_widget.show()
        self._onboarding_runner = OnboardingTestRunner(
            self,
            client,
            async_requests=async_requests,
        )
        self.onboarding_widget.test_requested.connect(self._start_onboarding_test)
        self._onboarding_runner.finished.connect(self._finish_onboarding_test)

    def needs_onboarding(self) -> bool:
        return not self.onboarding_store.is_complete()

    def _refresh_while_hidden(self) -> None:
        if self._background_sync_enabled and not self.isVisible():
            self.refresh_now()

    def show_panel(self) -> None:
        self.background_refresh_timer.stop()
        super().show_panel()

    def hide_panel(self) -> None:
        super().hide_panel()
        if self._background_sync_enabled and not self._shutting_down:
            self.background_refresh_timer.start()

    def apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        super().apply_snapshot(snapshot)
        extension = snapshot.get("extension") if isinstance(snapshot.get("extension"), dict) else {}
        if self.needs_onboarding():
            self.onboarding_widget.set_connected(bool(extension.get("connected")))
        self.snapshot_applied.emit(dict(snapshot))

    def _start_onboarding_test(self) -> None:
        if not self.needs_onboarding():
            return
        extension = self._last_snapshot.get("extension") if isinstance(self._last_snapshot.get("extension"), dict) else {}
        if not bool(extension.get("connected")):
            self.onboarding_widget.set_connected(False)
            return
        settings = self._last_snapshot.get("settings") if isinstance(self._last_snapshot.get("settings"), dict) else {}
        self.onboarding_widget.set_testing()
        if not self._onboarding_runner.start(
            reference_voice=str(settings.get("referenceVoice") or ""),
            voice_volume=float(settings.get("voiceVolume", 0.6)),
        ):
            self.onboarding_widget.set_failure("テストを開始できませんでした")

    def _finish_onboarding_test(self, result: Any, error: Any) -> None:
        if error is not None:
            self.onboarding_widget.set_failure(str(error))
            return
        if not isinstance(result, dict) or result.get("ok") is not True:
            self.onboarding_widget.set_failure("音声APIから成功応答を確認できませんでした")
            return
        try:
            self.onboarding_store.mark_complete()
        except OSError as exc:
            self.onboarding_widget.set_failure(f"完了状態を保存できませんでした: {exc}")
            return
        self.onboarding_widget.result_label.setText("テスト音声を確認しました。初回セットアップは完了です。")
        self.onboarding_widget.hide()

    def shutdown(self) -> None:
        self.background_refresh_timer.stop()
        self._onboarding_runner.shutdown()
        super().shutdown()
