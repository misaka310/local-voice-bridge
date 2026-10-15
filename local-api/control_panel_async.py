from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot


class AsyncControlPanelDispatcher(QObject):
    """Runs loopback control calls off the Qt UI thread and returns on it."""

    completed = Signal(str, object, object)

    def __init__(self, owner: Any, client: Any, *, async_requests: bool = True) -> None:
        super().__init__(owner)
        self.owner = owner
        self.client = client
        self._executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-voice-panel")
            if async_requests
            else None
        )
        self._refresh_pending = False
        self._settings_error_pending = False
        self._closed = False
        self.completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def _submit(self, operation: str, call: Any, *args: Any) -> bool:
        if self._closed:
            return False
        if self._executor is None:
            try:
                result = call(*args)
            except Exception as exc:
                self._deliver(operation, None, exc)
            else:
                self._deliver(operation, result, None)
            return True
        try:
            future = self._executor.submit(call, *args)
        except RuntimeError:
            return False
        future.add_done_callback(lambda item: self._finish(operation, item))
        return True

    def _finish(self, operation: str, future: Future[Any]) -> None:
        try:
            result = future.result()
        except Exception as exc:
            if self._closed:
                return
            self.completed.emit(operation, None, exc)
            return
        if self._closed:
            return
        self.completed.emit(operation, result, None)

    def refresh(self) -> None:
        if self._refresh_pending or self._closed:
            return
        self._refresh_pending = True
        if not self._submit("snapshot", self.client.get_snapshot):
            self._refresh_pending = False

    def update_settings(self, payload: dict[str, Any]) -> None:
        self._submit("settings", self.client.update_settings, payload)

    def send_command(self, command: str) -> None:
        self._submit("command", self.client.send_command, command)

    def reload_extension(self) -> None:
        self._submit("reload", self.client.send_command, "reload_extension")

    @Slot(str, object, object)
    def _deliver(self, operation: str, result: Any, error: Any) -> None:
        if self._closed:
            return
        if operation == "snapshot":
            self._refresh_pending = False
            if error is not None:
                self._settings_error_pending = False
                self.owner._apply_disconnected_state()
            else:
                self.owner.apply_snapshot(result if isinstance(result, dict) else {})
                if self._settings_error_pending:
                    self._settings_error_pending = False
                    self.owner.status_label.setText("設定を保存できませんでした")
            return
        if operation == "settings":
            if error is not None:
                self._settings_error_pending = True
                self.refresh()
            return
        if operation == "command":
            if error is not None:
                self.owner.status_label.setText("操作を送信できませんでした")
            return
        if operation == "reload":
            if error is not None:
                self.owner._reload_extension_requested = False
                self.owner.reload_extension_button.setEnabled(True)
                self.owner.status_label.setText("拡張機能の再読み込み要求に失敗しました")
                return
            self.owner.status_label.setText("拡張機能を再読み込みしています")
            self.owner._set_current_text("再接続を待っています。ChatGPTタブの再読み込みは不要です。")

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
