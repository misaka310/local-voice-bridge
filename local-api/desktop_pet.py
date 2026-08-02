from __future__ import annotations

import ctypes
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QContextMenuEvent, QImage, QImageReader, QMouseEvent, QPaintEvent, QPainter, QPixmap, QShowEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget

from desktop_pet_config import (
    DEFAULT_PET_ID,
    DesktopPetSettingsStore,
    PetDefinition,
    ScreenGeometry,
    capture_position,
    discover_available_pets,
    load_pet_definition,
    resolve_pet_definition,
    resolve_selected_pet,
    restore_position,
)

LOGGER = logging.getLogger("local-voice-bridge-tray")
IS_WINDOWS = os.name == "nt"
IDLE_ACTIVITY_DELAY_MS = 8000

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
DWMWA_NCRENDERING_POLICY = 2
DWMNCRP_DISABLED = 1


def load_sprite_sheet(pet: PetDefinition) -> QPixmap:
    expected_width = max(1, pet.frame_width * pet.columns)
    expected_height = max(1, pet.frame_height * pet.rows)
    suffix = pet.spritesheet_path.suffix.lower()

    if suffix == ".svg":
        renderer = QSvgRenderer(str(pet.spritesheet_path))
        if not renderer.isValid():
            raise ValueError(f"SVGを読み込めません: {pet.spritesheet_path}")
        image = QImage(expected_width, expected_height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            renderer.render(painter, QRectF(0, 0, expected_width, expected_height))
        finally:
            painter.end()
        return QPixmap.fromImage(image)

    reader = QImageReader(str(pet.spritesheet_path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise ValueError(f"画像を読み込めません: {pet.spritesheet_path}: {reader.errorString()}")
    if image.format() not in (
        QImage.Format.Format_ARGB32,
        QImage.Format.Format_ARGB32_Premultiplied,
        QImage.Format.Format_RGBA8888,
        QImage.Format.Format_RGBA8888_Premultiplied,
    ):
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    return QPixmap.fromImage(image)


def qt_screen_geometries() -> list[ScreenGeometry]:
    app = QApplication.instance()
    if app is None:
        return []
    result: list[ScreenGeometry] = []
    for screen in app.screens():
        geometry = screen.availableGeometry()
        result.append(
            ScreenGeometry(
                name=screen.name(),
                x=geometry.x(),
                y=geometry.y(),
                width=geometry.width(),
                height=geometry.height(),
            )
        )
    return result


def apply_windows_toolwindow_style(widget: QWidget) -> None:
    if not IS_WINDOWS:
        return
    try:
        hwnd = int(widget.winId())
        user32 = ctypes.windll.user32
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(hwnd, GWL_EXSTYLE))
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        set_style(hwnd, GWL_EXSTYLE, style)
        user32.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        try:
            policy = ctypes.c_int(DWMNCRP_DISABLED)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_NCRENDERING_POLICY,
                ctypes.byref(policy),
                ctypes.sizeof(policy),
            )
        except (AttributeError, OSError):
            LOGGER.debug("DWM non-client rendering policy was not applied", exc_info=True)
    except (AttributeError, OSError, TypeError, ValueError):
        LOGGER.debug("Windows tool-window style was not applied", exc_info=True)


class DesktopPetWindow(QWidget):
    visibility_changed = Signal(bool)
    always_on_top_changed = Signal(bool)
    pet_selection_changed = Signal(str)
    panel_toggle_requested = Signal()
    exit_requested = Signal()

    def __init__(
        self,
        pet_root: Path,
        settings_store: DesktopPetSettingsStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.pet_root = Path(pet_root)
        self.settings_store = settings_store
        had_saved_settings = settings_store.path.is_file()
        self.settings = settings_store.load()
        if not self.settings.visible:
            self.settings = replace(self.settings, visible=True)
            self.settings_store.save(self.settings)
        self.available_pet_choices = discover_available_pets(self.pet_root)
        saved_pet_id = self.settings.selected_pet_id if had_saved_settings else ""
        selected = resolve_selected_pet(saved_pet_id, self.available_pet_choices)
        if selected != self.settings.selected_pet_id:
            self.settings = replace(self.settings, selected_pet_id=selected)
            self.settings_store.save(self.settings)

        self.current_pet = self._load_selected_pet(selected)
        self._sprite_sheet = QPixmap()
        self._frames: list[QPixmap] = []
        self._frame_index = 0
        self.current_state = "idle"
        self._active_animation_state = "idle"
        self._drag_offset: QPoint | None = None
        self._drag_origin: QPoint | None = None
        self._position_dirty = False
        self._last_drag_completed_at = 0.0
        self._temporarily_adjusted = False
        self._shutting_down = False

        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._advance_frame)
        self._idle_activity_timer = QTimer(self)
        self._idle_activity_timer.setSingleShot(True)
        self._idle_activity_timer.timeout.connect(self._play_idle_activity)
        self._idle_activity_end_timer = QTimer(self)
        self._idle_activity_end_timer.setSingleShot(True)
        self._idle_activity_end_timer.timeout.connect(self._finish_idle_activity)

        self.setObjectName("desktop-pet-window")
        self.setWindowTitle("Local Voice Bridge Desktop Pet")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._apply_window_flags()
        self._apply_pet(self.current_pet)
        self._restore_saved_position()
        self.set_state("idle")

        self.show()
        apply_windows_toolwindow_style(self)

    def _base_window_flags(self) -> Qt.WindowType:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if self.settings.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def _apply_window_flags(self) -> None:
        self.setWindowFlags(self._base_window_flags())

    def _load_selected_pet(self, selected_pet_id: str) -> PetDefinition:
        choice = next(
            (item for item in self.available_pet_choices if item.selection_id == selected_pet_id),
            None,
        )
        if choice is not None:
            try:
                return load_pet_definition(choice.config_path, selection_id=choice.selection_id)
            except (OSError, ValueError):
                LOGGER.warning("Desktop pet '%s' could not be loaded; falling back", selected_pet_id, exc_info=True)
        return resolve_pet_definition(self.pet_root, DEFAULT_PET_ID)

    def _apply_pet(self, pet: PetDefinition) -> None:
        old_position = self.pos()
        was_visible = self.isVisible()
        self.current_pet = pet
        self._sprite_sheet = load_sprite_sheet(pet)
        self._frames = []
        transform = (
            Qt.TransformationMode.SmoothTransformation
            if pet.spritesheet_path.suffix.lower() == ".svg"
            else Qt.TransformationMode.FastTransformation
        )
        for frame_id in range(pet.total_frames):
            column = frame_id % pet.columns
            row = frame_id // pet.columns
            frame = self._sprite_sheet.copy(
                column * pet.frame_width,
                row * pet.frame_height,
                pet.frame_width,
                pet.frame_height,
            )
            if frame.isNull():
                frame = QPixmap(pet.frame_width, pet.frame_height)
                frame.fill(Qt.GlobalColor.transparent)
            self._frames.append(
                frame.scaled(
                    pet.display_width,
                    pet.display_height,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    transform,
                )
            )
        if not self._frames:
            empty = QPixmap(pet.display_width, pet.display_height)
            empty.fill(Qt.GlobalColor.transparent)
            self._frames = [empty]
        self.resize(pet.display_width, pet.display_height)
        if not old_position.isNull():
            self.move(old_position)
        if was_visible:
            self.show()
            apply_windows_toolwindow_style(self)
        self._idle_activity_timer.stop()
        self._idle_activity_end_timer.stop()
        self._start_animation(self.current_state)
        self._schedule_idle_activity()
        self.update()

    def _restore_saved_position(self) -> None:
        restored = restore_position(
            self.settings,
            qt_screen_geometries(),
            self.width(),
            self.height(),
        )
        self._temporarily_adjusted = restored.temporarily_adjusted
        self.move(restored.x, restored.y)

    def _capture_current_position(self) -> None:
        self.settings = capture_position(
            self.settings,
            self.x(),
            self.y(),
            qt_screen_geometries(),
            self.width(),
            self.height(),
        )
        self._position_dirty = False
        self._temporarily_adjusted = False

    def persist_settings(self) -> None:
        if self.settings_store.path.is_file():
            self.sync_settings_from_disk()
        if self._position_dirty:
            self._capture_current_position()
        self.settings_store.save(self.settings)

    def show_pet(self) -> None:
        self.settings = replace(self.settings, visible=True)
        self.settings_store.save(self.settings)
        self.show()
        self.raise_()
        apply_windows_toolwindow_style(self)
        self.visibility_changed.emit(True)

    def hide_pet(self) -> None:
        self.settings = replace(self.settings, visible=False)
        self.settings_store.save(self.settings)
        self.hide()
        self.visibility_changed.emit(False)

    def set_always_on_top(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.settings.always_on_top:
            return
        was_visible = self.isVisible()
        position = self.pos()
        self.settings = replace(self.settings, always_on_top=enabled)
        self._apply_window_flags()
        self.move(position)
        if was_visible:
            self.show()
            self.raise_()
            apply_windows_toolwindow_style(self)
        self.settings_store.save(self.settings)
        self.always_on_top_changed.emit(enabled)

    def reset_position(self) -> None:
        screens = qt_screen_geometries()
        reset_settings = replace(
            self.settings,
            screen_name="",
            x=None,
            y=None,
            relative_x=None,
            relative_y=None,
            saved_available_geometry=None,
        )
        restored = restore_position(reset_settings, screens, self.width(), self.height())
        self.move(restored.x, restored.y)
        self._position_dirty = True
        self.persist_settings()

    def select_pet(self, selected_pet_id: str) -> None:
        selected = resolve_selected_pet(selected_pet_id, self.available_pet_choices)
        try:
            pet = self._load_selected_pet(selected)
        except (OSError, ValueError):
            LOGGER.error("No usable desktop pet could be loaded", exc_info=True)
            return
        if pet.selection_id != selected and selected != DEFAULT_PET_ID:
            selected = DEFAULT_PET_ID
            pet = self._load_selected_pet(DEFAULT_PET_ID)
        self.settings = replace(self.settings, selected_pet_id=selected)
        self._apply_pet(pet)
        self.settings_store.save(self.settings)
        self.pet_selection_changed.emit(selected)

    def sync_settings_from_disk(self) -> bool:
        latest = self.settings_store.load()
        selected = resolve_selected_pet(latest.selected_pet_id, self.available_pet_choices)
        selection_changed = selected != self.current_pet.selection_id
        needs_save = not latest.visible or selected != latest.selected_pet_id
        self.settings = replace(latest, visible=True, selected_pet_id=selected)
        if selection_changed:
            pet = self._load_selected_pet(selected)
            self._apply_pet(pet)
            LOGGER.info("Desktop pet selection changed to %s", selected)
        if not self.isVisible():
            self.show()
            self.raise_()
            apply_windows_toolwindow_style(self)
        if needs_save:
            self.settings_store.save(self.settings)
        return selection_changed

    def set_voice_bridge_status(self, status: str) -> None:
        normalized = str(status or "")
        if normalized.startswith("Ready"):
            self.set_state("idle")
            return
        if normalized in {"Starting", "Checking environment", "Waiting to retry"}:
            self.set_state("idle")
            return
        self.set_state("error")

    def set_state(self, state: str) -> None:
        normalized = state if state in self.current_pet.animations else "idle"
        if normalized == self.current_state:
            if normalized == "idle" and not self._idle_activity_timer.isActive():
                self._schedule_idle_activity()
            return
        self._idle_activity_timer.stop()
        self._idle_activity_end_timer.stop()
        self.current_state = normalized
        self._schedule_idle_activity()
        self._start_animation(self.current_state)

    def _start_animation(self, state: str) -> None:
        self._animation_timer.stop()
        self._active_animation_state = state if state in self.current_pet.animations else "idle"
        animation = self.current_pet.animations.get(self._active_animation_state) or self.current_pet.animations["idle"]
        self._frame_index = 0
        self._show_animation_frame(animation.frames[0])
        if len(animation.frames) > 1:
            self._animation_timer.start(animation.speed_ms)

    def _advance_frame(self) -> None:
        animation = self.current_pet.animations.get(self._active_animation_state) or self.current_pet.animations["idle"]
        if not animation.frames:
            return
        self._frame_index = (self._frame_index + 1) % len(animation.frames)
        self._show_animation_frame(animation.frames[self._frame_index])

    def _schedule_idle_activity(self) -> None:
        if self._shutting_down or self.current_state != "idle":
            return
        walking = self.current_pet.animations.get("walking")
        idle = self.current_pet.animations.get("idle")
        if walking is None or idle is None or len(walking.frames) < 2 or walking.frames == idle.frames:
            return
        self._idle_activity_timer.start(IDLE_ACTIVITY_DELAY_MS)

    def _play_idle_activity(self) -> None:
        if self._shutting_down or self.current_state != "idle":
            return
        walking = self.current_pet.animations.get("walking")
        if walking is None or len(walking.frames) < 2:
            return
        self._start_animation("walking")
        self._idle_activity_end_timer.start(max(1, len(walking.frames) * walking.speed_ms))

    def _finish_idle_activity(self) -> None:
        if self._shutting_down or self.current_state != "idle":
            return
        self._start_animation("idle")
        self._schedule_idle_activity()

    def _show_animation_frame(self, frame_id: int) -> None:
        if not self._frames:
            return
        safe_frame = min(len(self._frames) - 1, max(0, int(frame_id)))
        self._paint_frame = self._frames[safe_frame]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        frame = getattr(self, "_paint_frame", None)
        if isinstance(frame, QPixmap) and not frame.isNull():
            painter.drawPixmap(0, 0, frame)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = self.pos()
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
            moved = self._drag_origin is not None and self.pos() != self._drag_origin
            self._drag_offset = None
            self._drag_origin = None
            if moved:
                self._position_dirty = True
                self._last_drag_completed_at = time.monotonic()
                self.persist_settings()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if time.monotonic() - self._last_drag_completed_at < 0.45:
                event.ignore()
                return
            self.panel_toggle_requested.emit()
            event.accept()
            return
        event.ignore()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        event.ignore()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        apply_windows_toolwindow_style(self)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._shutting_down:
            event.accept()
            return
        event.ignore()
        self.show_pet()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._animation_timer.stop()
        self._idle_activity_timer.stop()
        self._idle_activity_end_timer.stop()
        self.persist_settings()
        self.hide()
        self.close()
        self.deleteLater()
