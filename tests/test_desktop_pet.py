from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QImage
from PySide6.QtWidgets import QApplication

class FakeMouseEvent:
    def __init__(
        self,
        button: Qt.MouseButton,
        *,
        buttons: Qt.MouseButton = Qt.MouseButton.NoButton,
        global_position: QPoint | None = None,
    ) -> None:
        self._button = button
        self._buttons = buttons
        self._global_position = QPointF(global_position or QPoint())
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return self._button

    def buttons(self) -> Qt.MouseButton:
        return self._buttons

    def globalPosition(self) -> QPointF:  # noqa: N802
        return self._global_position

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from desktop_pet import DesktopPetWindow, load_sprite_sheet  # noqa: E402
from desktop_pet_config import DesktopPetSettings, DesktopPetSettingsStore, load_pet_definition  # noqa: E402


class DesktopPetQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @staticmethod
    def _write_raster(path: Path, image_format: str) -> None:
        image = QImage(32, 16, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        image.setPixelColor(2, 2, QColor(255, 0, 0, 255))
        if not image.save(str(path), image_format):
            raise unittest.SkipTest(f"Qt image plugin cannot save {image_format}")

    @staticmethod
    def _write_svg(path: Path) -> None:
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="16" viewBox="0 0 32 16">'
            '<rect x="2" y="2" width="8" height="8" fill="#ff0000"/>'
            "</svg>",
            encoding="utf-8",
        )

    @staticmethod
    def _write_config(path: Path, *, pet_id: str, sheet_name: str, display_name: str | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "id": pet_id,
                    "displayName": display_name or pet_id.title(),
                    "spritesheetPath": sheet_name,
                    "columns": 2,
                    "rows": 1,
                    "frameWidth": 16,
                    "frameHeight": 16,
                    "displayScale": 1,
                    "animations": {
                        "idle": {"frames": [0, 1], "speed": 1000},
                        "walking": {"frames": [0, 1, 0], "speed": 10},
                        "error": {"frames": [1], "speed": 1000},
                    },
                }
            ),
            encoding="utf-8",
        )

    def _create_pet_root(self, temp_dir: str, *, local_pets: tuple[str, ...] = ()) -> Path:
        root = Path(temp_dir) / "pet"
        root.mkdir(parents=True, exist_ok=True)
        self._write_svg(root / "placeholder.svg")
        self._write_config(root / "pet.json", pet_id="local-voice-placeholder", sheet_name="placeholder.svg", display_name="Placeholder")
        for pet_id in local_pets:
            pet_dir = root / "local" / "voices" / pet_id
            pet_dir.mkdir(parents=True, exist_ok=True)
            self._write_raster(pet_dir / "spritesheet.png", "PNG")
            self._write_config(pet_dir / "pet.json", pet_id=pet_id, sheet_name="spritesheet.png")
        return root

    def test_png_webp_and_svg_load_with_alpha_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for extension, image_format in (("png", "PNG"), ("webp", "WEBP")):
                image_path = root / f"sprite.{extension}"
                self._write_raster(image_path, image_format)
                config_path = root / f"pet-{extension}.json"
                self._write_config(config_path, pet_id=extension, sheet_name=image_path.name)
                pet = load_pet_definition(config_path, selection_id=extension)
                pixmap = load_sprite_sheet(pet)
                image = pixmap.toImage()
                self.assertTrue(image.hasAlphaChannel())
                self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                self.assertEqual(image.pixelColor(2, 2).alpha(), 255)

            svg_path = root / "sprite.svg"
            self._write_svg(svg_path)
            svg_config = root / "pet-svg.json"
            self._write_config(svg_config, pet_id="svg", sheet_name=svg_path.name)
            svg_pet = load_pet_definition(svg_config, selection_id="svg")
            svg_image = load_sprite_sheet(svg_pet).toImage()
            self.assertTrue(svg_image.hasAlphaChannel())
            self.assertEqual(svg_image.pixelColor(0, 0).alpha(), 0)
            self.assertGreater(svg_image.pixelColor(3, 3).alpha(), 0)

    def test_window_is_frameless_tool_translucent_and_not_normal_app_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            flags = window.windowFlags()

            self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
            self.assertTrue(flags & Qt.WindowType.Tool)
            self.assertTrue(flags & Qt.WindowType.NoDropShadowWindowHint)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertFalse(window.autoFillBackground())
            window.shutdown()

    def test_close_keeps_the_only_pet_visible_without_quitting_qapplication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            store = DesktopPetSettingsStore(Path(temp_dir) / "settings.json")
            window = DesktopPetWindow(root, store)
            window.show_pet()
            self.app.processEvents()
            self.assertTrue(window.isVisible())

            window.close()
            self.app.processEvents()

            self.assertTrue(window.isVisible())
            self.assertTrue(store.load().visible)
            self.assertFalse(self.app.quitOnLastWindowClosed())
            window.shutdown()

    def test_saved_hidden_state_is_migrated_to_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            store = DesktopPetSettingsStore(Path(temp_dir) / "settings.json")
            store.save(DesktopPetSettings(visible=False))

            window = DesktopPetWindow(root, store)
            self.app.processEvents()

            self.assertTrue(window.isVisible())
            self.assertTrue(store.load().visible)
            window.shutdown()

    def test_plain_left_click_does_nothing_and_double_click_requests_panel_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            persisted: list[bool] = []
            toggles: list[bool] = []
            window.persist_settings = lambda: persisted.append(True)
            window.panel_toggle_requested.connect(lambda: toggles.append(True))
            origin = window.pos()
            global_position = window.frameGeometry().topLeft() + QPoint(4, 4)

            press = FakeMouseEvent(Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton, global_position=global_position)
            release = FakeMouseEvent(Qt.MouseButton.LeftButton, global_position=global_position)
            double_click = FakeMouseEvent(Qt.MouseButton.LeftButton, global_position=global_position)
            window.mousePressEvent(press)
            window.mouseReleaseEvent(release)
            window.mouseDoubleClickEvent(double_click)

            self.assertEqual(window.pos(), origin)
            self.assertEqual(persisted, [])
            self.assertEqual(toggles, [True])
            self.assertTrue(press.accepted)
            self.assertTrue(release.accepted)
            self.assertTrue(double_click.accepted)
            self.assertTrue(window.isVisible())
            window.shutdown()

    def test_left_drag_moves_and_persists_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            persisted: list[bool] = []
            window.persist_settings = lambda: persisted.append(True)
            origin = window.pos()
            start = window.frameGeometry().topLeft() + QPoint(4, 4)
            target = start + QPoint(25, 30)

            window.mousePressEvent(FakeMouseEvent(Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton, global_position=start))
            window.mouseMoveEvent(FakeMouseEvent(Qt.MouseButton.NoButton, buttons=Qt.MouseButton.LeftButton, global_position=target))
            window.mouseReleaseEvent(FakeMouseEvent(Qt.MouseButton.LeftButton, global_position=target))

            self.assertEqual(window.pos(), origin + QPoint(25, 30))
            self.assertEqual(persisted, [True])
            self.assertTrue(window._position_dirty)
            suppressed_double_click = FakeMouseEvent(Qt.MouseButton.LeftButton, global_position=target)
            toggles: list[bool] = []
            window.panel_toggle_requested.connect(lambda: toggles.append(True))
            window.mouseDoubleClickEvent(suppressed_double_click)
            self.assertEqual(toggles, [])
            self.assertFalse(suppressed_double_click.accepted)
            window.shutdown()
    def test_right_click_has_no_pet_menu_or_visibility_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse,
                QPoint(1, 1),
                QPoint(1, 1),
            )

            window.contextMenuEvent(event)
            self.app.processEvents()

            self.assertFalse(event.isAccepted())
            self.assertTrue(window.isVisible())
            window.shutdown()

    def test_always_on_top_toggle_preserves_position_and_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            window.show_pet()
            window.move(31, 47)
            self.app.processEvents()

            window.set_always_on_top(False)
            self.app.processEvents()

            self.assertEqual((window.x(), window.y()), (31, 47))
            self.assertTrue(window.isVisible())
            self.assertFalse(window.settings.always_on_top)
            self.assertFalse(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            window.shutdown()

    def test_pet_selection_is_saved_and_selected_asset_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir, local_pets=("misaka", "asuka"))
            store = DesktopPetSettingsStore(Path(temp_dir) / "settings.json")
            store.save(DesktopPetSettings(selected_pet_id="placeholder"))
            window = DesktopPetWindow(root, store)

            window.select_pet("misaka")

            self.assertEqual(window.current_pet.selection_id, "misaka")
            self.assertEqual(store.load().selected_pet_id, "misaka")
            window.shutdown()

    def test_first_launch_with_one_local_pet_selects_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir, local_pets=("misaka",))
            store = DesktopPetSettingsStore(Path(temp_dir) / "settings.json")

            window = DesktopPetWindow(root, store)

            self.assertEqual(window.current_pet.selection_id, "misaka")
            self.assertEqual(store.load().selected_pet_id, "misaka")
            window.shutdown()

    def test_deleted_saved_pet_falls_back_to_public_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir, local_pets=("misaka", "asuka"))
            store = DesktopPetSettingsStore(Path(temp_dir) / "settings.json")
            store.save(DesktopPetSettings(selected_pet_id="deleted"))

            window = DesktopPetWindow(root, store)

            self.assertEqual(window.current_pet.selection_id, "placeholder")
            self.assertEqual(store.load().selected_pet_id, "placeholder")
            window.shutdown()

    def test_position_save_does_not_overwrite_external_pet_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir, local_pets=("misaka", "asuka"))
            store = DesktopPetSettingsStore(Path(temp_dir) / "settings.json")
            window = DesktopPetWindow(root, store)
            store.save(DesktopPetSettings(selected_pet_id="misaka"))
            window.move(42, 58)
            window._position_dirty = True

            window.persist_settings()

            saved = store.load()
            self.assertEqual(saved.selected_pet_id, "misaka")
            self.assertEqual(window.current_pet.selection_id, "misaka")
            self.assertEqual((saved.x, saved.y), (42, 58))
            window.shutdown()

    def test_idle_activity_uses_walking_frames_without_moving_the_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            origin = window.pos()
            window._idle_activity_timer.stop()

            window._play_idle_activity()

            self.assertEqual(window.current_state, "idle")
            self.assertEqual(window._active_animation_state, "walking")
            self.assertEqual(window.pos(), origin)

            window._finish_idle_activity()

            self.assertEqual(window.current_state, "idle")
            self.assertEqual(window._active_animation_state, "idle")
            self.assertEqual(window.pos(), origin)
            window.shutdown()

    def test_same_state_does_not_restart_the_current_animation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))
            window.set_state("error")
            window._frame_index = 1

            window.set_state("error")

            self.assertEqual(window.current_state, "error")
            self.assertEqual(window._frame_index, 1)
            window.shutdown()

    def test_voice_bridge_status_only_uses_idle_and_error_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._create_pet_root(temp_dir)
            window = DesktopPetWindow(root, DesktopPetSettingsStore(Path(temp_dir) / "settings.json"))

            window.set_voice_bridge_status("Ready")
            self.assertEqual(window.current_state, "idle")
            window.set_voice_bridge_status("Ready (existing)")
            self.assertEqual(window.current_state, "idle")
            window.set_voice_bridge_status("Unhealthy")
            self.assertEqual(window.current_state, "error")
            window.shutdown()


if __name__ == "__main__":
    unittest.main()
