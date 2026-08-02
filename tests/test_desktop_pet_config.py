from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from desktop_pet_config import (  # noqa: E402
    DesktopPetSettings,
    DesktopPetSettingsStore,
    ScreenGeometry,
    build_pet_config_candidates,
    capture_position,
    discover_available_pets,
    load_pet_definition,
    resolve_selected_pet,
    restore_position,
)


class DesktopPetSettingsTests(unittest.TestCase):
    def test_missing_settings_file_uses_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DesktopPetSettingsStore(Path(temp_dir) / "desktop-pet-settings.json")
            settings = store.load()

        self.assertEqual(settings.version, 1)
        self.assertTrue(settings.visible)
        self.assertTrue(settings.always_on_top)
        self.assertEqual(settings.selected_pet_id, "placeholder")

    def test_corrupt_settings_file_recovers_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "desktop-pet-settings.json"
            path.write_text("{broken", encoding="utf-8")
            settings = DesktopPetSettingsStore(path).load()

        self.assertEqual(settings, DesktopPetSettings())

    def test_settings_round_trip_preserves_all_user_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "desktop-pet-settings.json"
            store = DesktopPetSettingsStore(path)
            expected = DesktopPetSettings(
                screen_name="DISPLAY2",
                x=2100,
                y=220,
                relative_x=0.25,
                relative_y=0.5,
                saved_available_geometry=ScreenGeometry("DISPLAY2", 1920, 0, 1920, 1080),
                visible=False,
                always_on_top=False,
                selected_pet_id="misaka",
            )
            store.save(expected)
            actual = store.load()

        self.assertEqual(actual, expected)


class PetDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _write_pet(path: Path, *, pet_id: str, display_name: str, sheet: str = "sprite.png", extra: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": pet_id,
            "displayName": display_name,
            "spritesheetPath": sheet,
            "columns": 2,
            "rows": 2,
            "frameWidth": 16,
            "frameHeight": 16,
        }
        payload.update(extra or {})
        path.write_text(json.dumps(payload), encoding="utf-8")
        (path.parent / sheet).write_bytes(b"asset")

    def test_candidate_order_matches_browser_pet_fallback_order(self) -> None:
        root = Path("extension/assets/pet")
        self.assertEqual(
            build_pet_config_candidates(root, "misaka"),
            [
                root / "local" / "voices" / "misaka" / "pet.json",
                root / "local" / "voices" / "placeholder" / "pet.json",
                root / "local" / "pet.json",
                root / "pet.json",
            ],
        )

    def test_discovery_lists_local_pets_and_public_placeholder_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_pet(root / "local" / "voices" / "misaka" / "pet.json", pet_id="misaka", display_name="Misaka")
            self._write_pet(root / "local" / "pet.json", pet_id="local-default", display_name="Local Default")
            self._write_pet(root / "pet.json", pet_id="local-voice-placeholder", display_name="Public Placeholder", sheet="placeholder.svg")

            choices = discover_available_pets(root)

        self.assertEqual([choice.selection_id for choice in choices], ["misaka", "local-default", "placeholder"])
        self.assertEqual(choices[-1].display_name, "Public Placeholder")

    def test_first_run_selects_only_local_pet_otherwise_placeholder(self) -> None:
        placeholder = type("Choice", (), {"selection_id": "placeholder", "is_local": False})()
        misaka = type("Choice", (), {"selection_id": "misaka", "is_local": True})()
        asuka = type("Choice", (), {"selection_id": "asuka", "is_local": True})()

        self.assertEqual(resolve_selected_pet("", [misaka, placeholder]), "misaka")
        self.assertEqual(resolve_selected_pet("", [misaka, asuka, placeholder]), "placeholder")
        self.assertEqual(resolve_selected_pet("asuka", [misaka, asuka, placeholder]), "asuka")
        self.assertEqual(resolve_selected_pet("deleted", [misaka, asuka, placeholder]), "placeholder")

    def test_pet_definition_normalizes_animations_display_scale_and_bad_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "local" / "voices" / "misaka" / "pet.json"
            self._write_pet(
                config_path,
                pet_id="misaka",
                display_name="Misaka",
                extra={
                    "displayScale": 0.5,
                    "animations": {
                        "idle": {"frames": [-1, 0, 99], "speed": 250},
                        "walk": {"frames": [3, 2], "speed": 175},
                        "talking": [1, 2],
                    },
                },
            )

            pet = load_pet_definition(config_path, selection_id="misaka")

        self.assertEqual(pet.display_scale, 0.5)
        self.assertEqual(pet.animations["idle"].frames, (0, 0, 3))
        self.assertEqual(pet.animations["idle"].speed_ms, 250)
        self.assertEqual(pet.animations["walking"].frames, (3, 2))
        self.assertEqual(pet.animations["walking"].speed_ms, 175)
        self.assertEqual(pet.animations["talking"].frames, (1, 2))
        self.assertEqual(pet.animations["happy"].frames, (0,))

    def test_missing_display_scale_matches_existing_browser_placeholder_width(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "pet.json"
            self._write_pet(
                config_path,
                pet_id="placeholder",
                display_name="Placeholder",
                extra={"frameWidth": 192, "frameHeight": 208},
            )

            pet = load_pet_definition(config_path, selection_id="placeholder")

        self.assertAlmostEqual(pet.display_scale, 88 / 192)
        self.assertEqual(pet.display_width, 88)
        self.assertEqual(pet.display_height, 95)


    def test_assets_pet_path_is_resolved_from_pet_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pet_root = Path(temp_dir) / "extension" / "assets" / "pet"
            config_path = pet_root / "local" / "voices" / "misaka" / "pet.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            shared = pet_root / "shared.png"
            shared.write_bytes(b"asset")
            config_path.write_text(
                json.dumps(
                    {
                        "id": "misaka",
                        "displayName": "Misaka",
                        "spritesheetPath": "assets/pet/shared.png",
                        "columns": 1,
                        "rows": 1,
                        "frameWidth": 16,
                        "frameHeight": 16,
                    }
                ),
                encoding="utf-8",
            )

            pet = load_pet_definition(config_path, selection_id="misaka")

        self.assertEqual(pet.spritesheet_path, shared.resolve())


class PositionRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.primary = ScreenGeometry("DISPLAY1", 0, 0, 1920, 1040)
        self.secondary = ScreenGeometry("DISPLAY2", 1920, 0, 2560, 1400)

    def test_saved_screen_uses_relative_position_after_resolution_or_dpi_change(self) -> None:
        settings = DesktopPetSettings(
            screen_name="DISPLAY2",
            x=2200,
            y=300,
            relative_x=0.5,
            relative_y=0.25,
            saved_available_geometry=ScreenGeometry("DISPLAY2", 1920, 0, 1920, 1040),
        )

        restored = restore_position(settings, [self.primary, self.secondary], 100, 100)

        self.assertEqual(restored.screen_name, "DISPLAY2")
        self.assertEqual(restored.x, 1920 + round((2560 - 100) * 0.5))
        self.assertEqual(restored.y, round((1400 - 100) * 0.25))
        self.assertFalse(restored.temporarily_adjusted)

    def test_fully_offscreen_position_is_temporarily_clamped_without_mutating_saved_position(self) -> None:
        settings = DesktopPetSettings(screen_name="REMOVED", x=8000, y=5000, relative_x=0.9, relative_y=0.9)
        original = replace(settings)

        restored = restore_position(settings, [self.primary], 100, 100)

        self.assertTrue(restored.temporarily_adjusted)
        self.assertGreaterEqual(restored.x, self.primary.x)
        self.assertLessEqual(restored.x, self.primary.right - 24)
        self.assertEqual(settings, original)

    def test_capture_position_saves_logical_coordinates_and_relative_position(self) -> None:
        settings = DesktopPetSettings(selected_pet_id="misaka")

        captured = capture_position(settings, 2400, 350, [self.primary, self.secondary], 100, 100)

        self.assertEqual(captured.screen_name, "DISPLAY2")
        self.assertEqual(captured.x, 2400)
        self.assertEqual(captured.y, 350)
        self.assertAlmostEqual(captured.relative_x, (2400 - 1920) / (2560 - 100))
        self.assertAlmostEqual(captured.relative_y, 350 / (1400 - 100))
        self.assertEqual(captured.selected_pet_id, "misaka")


if __name__ == "__main__":
    unittest.main()
