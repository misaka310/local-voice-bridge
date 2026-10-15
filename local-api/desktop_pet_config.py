from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

SETTINGS_VERSION = 1
DEFAULT_PET_ID = "placeholder"
DEFAULT_BROWSER_PET_WIDTH = 88
DEFAULT_RIGHT_MARGIN = 24
DEFAULT_BOTTOM_MARGIN = 140
MIN_VISIBLE_PIXELS = 24
SUPPORTED_ASSET_SUFFIXES = {".png", ".webp", ".svg"}
ANIMATION_NAMES = ("idle", "walking", "thinking", "talking", "happy", "error")
ANIMATION_ALIASES = {
    "idle": ("idle", "waiting"),
    "walking": ("walking", "walk", "pacing"),
    "thinking": ("thinking", "working"),
    "talking": ("talking", "speaking", "talk"),
    "happy": ("happy", "success", "celebrate"),
    "error": ("error", "sad", "angry", "confused"),
}
DEFAULT_ANIMATION_SPEEDS = {
    "idle": 400,
    "walking": 180,
    "thinking": 300,
    "talking": 150,
    "happy": 220,
    "error": 260,
}


@dataclass(frozen=True)
class ScreenGeometry:
    name: str
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def to_json(self) -> dict[str, Any]:
        return {
            "screenName": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_json(cls, value: Any) -> ScreenGeometry | None:
        if not isinstance(value, dict):
            return None
        try:
            width = int(value.get("width"))
            height = int(value.get("height"))
            if width <= 0 or height <= 0:
                return None
            return cls(
                name=str(value.get("screenName") or value.get("name") or ""),
                x=int(value.get("x", 0)),
                y=int(value.get("y", 0)),
                width=width,
                height=height,
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class DesktopPetSettings:
    version: int = SETTINGS_VERSION
    screen_name: str = ""
    x: int | None = None
    y: int | None = None
    relative_x: float | None = None
    relative_y: float | None = None
    saved_available_geometry: ScreenGeometry | None = None
    visible: bool = True
    always_on_top: bool = True
    selected_pet_id: str = DEFAULT_PET_ID

    def to_json(self) -> dict[str, Any]:
        return {
            "version": SETTINGS_VERSION,
            "screenName": self.screen_name,
            "x": self.x,
            "y": self.y,
            "relativeX": self.relative_x,
            "relativeY": self.relative_y,
            "savedAvailableGeometry": (
                self.saved_available_geometry.to_json() if self.saved_available_geometry else None
            ),
            "visible": self.visible,
            "alwaysOnTop": self.always_on_top,
            "selectedPetId": self.selected_pet_id,
        }

    @classmethod
    def from_json(cls, value: Any) -> DesktopPetSettings:
        if not isinstance(value, dict):
            return cls()

        def optional_int(raw: Any) -> int | None:
            if raw is None or isinstance(raw, bool):
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        def optional_ratio(raw: Any) -> float | None:
            try:
                result = float(raw)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(result):
                return None
            return min(1.0, max(0.0, result))

        selected = normalize_selection_id(value.get("selectedPetId"))
        return cls(
            version=SETTINGS_VERSION,
            screen_name=str(value.get("screenName") or ""),
            x=optional_int(value.get("x")),
            y=optional_int(value.get("y")),
            relative_x=optional_ratio(value.get("relativeX")),
            relative_y=optional_ratio(value.get("relativeY")),
            saved_available_geometry=ScreenGeometry.from_json(value.get("savedAvailableGeometry")),
            visible=value.get("visible") is not False,
            always_on_top=value.get("alwaysOnTop") is not False,
            selected_pet_id=selected or DEFAULT_PET_ID,
        )


class DesktopPetSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> DesktopPetSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return DesktopPetSettings()
        return DesktopPetSettings.from_json(raw)

    def save(self, settings: DesktopPetSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = json.dumps(settings.to_json(), ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)


@dataclass(frozen=True)
class AnimationDefinition:
    frames: tuple[int, ...]
    speed_ms: int


@dataclass(frozen=True)
class PetDefinition:
    selection_id: str
    id: str
    display_name: str
    config_path: Path
    spritesheet_path: Path
    columns: int
    rows: int
    frame_width: int
    frame_height: int
    display_scale: float
    animations: dict[str, AnimationDefinition]

    @property
    def total_frames(self) -> int:
        return self.columns * self.rows

    @property
    def display_width(self) -> int:
        return max(1, round(self.frame_width * self.display_scale))

    @property
    def display_height(self) -> int:
        return max(1, round(self.frame_height * self.display_scale))


@dataclass(frozen=True)
class PetChoice:
    selection_id: str
    display_name: str
    config_path: Path
    is_local: bool


@dataclass(frozen=True)
class RestoredPosition:
    x: int
    y: int
    screen_name: str
    temporarily_adjusted: bool


def normalize_selection_id(value: Any) -> str:
    result = str(value or "").strip().lower()
    if not result or result in {"none", ".", ".."} or "/" in result or "\\" in result:
        return ""
    return result


def _positive_int(value: Any, fallback: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return fallback
    return result if result > 0 else fallback


def _positive_float(value: Any, fallback: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(result) or result <= 0:
        return fallback
    return result


def build_pet_config_candidates(pet_root: Path, selected_pet_id: str) -> list[Path]:
    root = Path(pet_root)
    selected = normalize_selection_id(selected_pet_id) or DEFAULT_PET_ID
    result = [root / "local" / "voices" / selected / "pet.json"]
    placeholder = root / "local" / "voices" / DEFAULT_PET_ID / "pet.json"
    if placeholder not in result:
        result.append(placeholder)
    result.extend([root / "local" / "pet.json", root / "pet.json"])
    return result


def _read_pet_metadata(config_path: Path) -> tuple[str, str] | None:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("spritesheetPath"):
        return None
    pet_id = normalize_selection_id(payload.get("id")) or config_path.parent.name.lower()
    display_name = str(payload.get("displayName") or payload.get("id") or config_path.parent.name).strip()
    return pet_id, display_name or pet_id


def discover_available_pets(pet_root: Path) -> list[PetChoice]:
    root = Path(pet_root)
    result: list[PetChoice] = []
    seen: set[str] = set()

    voices_dir = root / "local" / "voices"
    if voices_dir.is_dir():
        for config_path in sorted(voices_dir.glob("*/pet.json"), key=lambda path: path.parent.name.lower()):
            metadata = _read_pet_metadata(config_path)
            if metadata is None:
                continue
            pet_id, display_name = metadata
            selection_id = normalize_selection_id(config_path.parent.name) or pet_id
            if selection_id == DEFAULT_PET_ID or selection_id in seen:
                continue
            seen.add(selection_id)
            result.append(PetChoice(selection_id, display_name, config_path, True))

    local_default = root / "local" / "pet.json"
    metadata = _read_pet_metadata(local_default) if local_default.is_file() else None
    if metadata is not None:
        pet_id, display_name = metadata
        selection_id = pet_id or "local-default"
        if selection_id not in seen and selection_id != DEFAULT_PET_ID:
            seen.add(selection_id)
            result.append(PetChoice(selection_id, display_name, local_default, True))

    public_config = root / "pet.json"
    metadata = _read_pet_metadata(public_config) if public_config.is_file() else None
    if metadata is not None:
        _pet_id, display_name = metadata
        result.append(PetChoice(DEFAULT_PET_ID, display_name, public_config, False))
        seen.add(DEFAULT_PET_ID)

    return result


def resolve_selected_pet(saved_pet_id: str, choices: Sequence[PetChoice]) -> str:
    available = {choice.selection_id for choice in choices}
    saved = normalize_selection_id(saved_pet_id)
    if saved and saved in available:
        return saved
    local_ids = [choice.selection_id for choice in choices if choice.is_local]
    if not saved and len(local_ids) == 1:
        return local_ids[0]
    if DEFAULT_PET_ID in available:
        return DEFAULT_PET_ID
    return choices[0].selection_id if choices else DEFAULT_PET_ID


def _resolve_asset_path(config_path: Path, raw_path: Any) -> Path:
    value = str(raw_path or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("spritesheetPath is required")
    pet_root = next(
        (parent for parent in config_path.parents if parent.name.lower() == "pet"),
        config_path.parent,
    )
    normalized = value.lstrip("/")
    if normalized.startswith("assets/pet/"):
        candidate = pet_root / normalized.removeprefix("assets/pet/")
    elif value.startswith("/"):
        candidate = pet_root / normalized
    else:
        candidate = config_path.parent / value
    resolved = candidate.resolve()
    if resolved.suffix.lower() not in SUPPORTED_ASSET_SUFFIXES:
        raise ValueError(f"Unsupported desktop pet asset format: {resolved.suffix}")
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def _animation_payload(raw_animations: dict[str, Any], name: str) -> Any:
    for alias in ANIMATION_ALIASES[name]:
        if alias in raw_animations:
            return raw_animations[alias]
    return None


def _normalize_animation(raw: Any, *, total_frames: int, speed_ms: int) -> AnimationDefinition:
    if isinstance(raw, list):
        source_frames = raw
        raw_speed = speed_ms
    elif isinstance(raw, dict):
        source_frames = raw.get("frames") if isinstance(raw.get("frames"), list) else [0]
        raw_speed = raw.get("speed", speed_ms)
    else:
        source_frames = [0]
        raw_speed = speed_ms

    frames: list[int] = []
    max_frame = max(0, total_frames - 1)
    for value in source_frames:
        try:
            frame = math.floor(float(value))
        except (TypeError, ValueError, OverflowError):
            continue
        frames.append(min(max_frame, max(0, frame)))
    if not frames:
        frames = [0]
    return AnimationDefinition(tuple(frames), _positive_int(raw_speed, speed_ms))


def load_pet_definition(config_path: Path, *, selection_id: str | None = None) -> PetDefinition:
    path = Path(config_path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid pet.json: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"pet.json must contain an object: {path}")

    columns = _positive_int(payload.get("columns"), 1)
    rows = _positive_int(payload.get("rows"), 1)
    frame_width = _positive_int(payload.get("frameWidth"), DEFAULT_BROWSER_PET_WIDTH)
    frame_height = _positive_int(payload.get("frameHeight"), frame_width)
    total_frames = columns * rows
    default_scale = DEFAULT_BROWSER_PET_WIDTH / frame_width
    display_scale = _positive_float(payload.get("displayScale"), default_scale)
    raw_animations = payload.get("animations") if isinstance(payload.get("animations"), dict) else {}
    animations = {
        name: _normalize_animation(
            _animation_payload(raw_animations, name),
            total_frames=total_frames,
            speed_ms=DEFAULT_ANIMATION_SPEEDS[name],
        )
        for name in ANIMATION_NAMES
    }
    pet_id = normalize_selection_id(payload.get("id")) or normalize_selection_id(selection_id) or "custom-pet"
    normalized_selection = normalize_selection_id(selection_id) or pet_id
    display_name = str(payload.get("displayName") or pet_id).strip() or pet_id
    return PetDefinition(
        selection_id=normalized_selection,
        id=pet_id,
        display_name=display_name,
        config_path=path,
        spritesheet_path=_resolve_asset_path(path, payload.get("spritesheetPath")),
        columns=columns,
        rows=rows,
        frame_width=frame_width,
        frame_height=frame_height,
        display_scale=display_scale,
        animations=animations,
    )


def resolve_pet_definition(pet_root: Path, selected_pet_id: str) -> PetDefinition:
    errors: list[Exception] = []
    for config_path in build_pet_config_candidates(pet_root, selected_pet_id):
        if not config_path.is_file():
            continue
        try:
            selection = (
                DEFAULT_PET_ID
                if config_path == Path(pet_root) / "pet.json"
                else normalize_selection_id(selected_pet_id) or DEFAULT_PET_ID
            )
            return load_pet_definition(config_path, selection_id=selection)
        except (OSError, ValueError) as exc:
            errors.append(exc)
    if errors:
        raise ValueError(f"No usable desktop pet asset was found: {errors[-1]}") from errors[-1]
    raise FileNotFoundError("No desktop pet pet.json was found")


def _clamp_ratio(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return min(1.0, max(0.0, value))


def _intersection_area(x: int, y: int, width: int, height: int, screen: ScreenGeometry) -> int:
    left = max(x, screen.x)
    top = max(y, screen.y)
    right = min(x + width, screen.right)
    bottom = min(y + height, screen.bottom)
    return max(0, right - left) * max(0, bottom - top)


def _screen_for_window(
    x: int,
    y: int,
    width: int,
    height: int,
    screens: Sequence[ScreenGeometry],
) -> ScreenGeometry | None:
    if not screens:
        return None
    return max(screens, key=lambda screen: _intersection_area(x, y, width, height, screen))


def _clamp_to_screen(
    x: int,
    y: int,
    screen: ScreenGeometry,
    width: int,
    height: int,
) -> tuple[int, int]:
    max_x = max(screen.x, screen.right - MIN_VISIBLE_PIXELS)
    max_y = max(screen.y, screen.bottom - MIN_VISIBLE_PIXELS)
    min_x = min(screen.right - MIN_VISIBLE_PIXELS, screen.x)
    min_y = min(screen.bottom - MIN_VISIBLE_PIXELS, screen.y)
    clamped_x = min(max(x, min_x), max_x)
    clamped_y = min(max(y, min_y), max_y)
    return round(clamped_x), round(clamped_y)


def _default_position(screen: ScreenGeometry, width: int, height: int) -> tuple[int, int]:
    x = max(screen.x, screen.right - width - DEFAULT_RIGHT_MARGIN)
    y = max(screen.y, screen.bottom - height - DEFAULT_BOTTOM_MARGIN)
    return x, y


def restore_position(
    settings: DesktopPetSettings,
    screens: Sequence[ScreenGeometry],
    window_width: int,
    window_height: int,
) -> RestoredPosition:
    if not screens:
        return RestoredPosition(settings.x or 0, settings.y or 0, settings.screen_name, False)

    matching = next((screen for screen in screens if screen.name == settings.screen_name), None)
    if matching is not None:
        relative_x = _clamp_ratio(settings.relative_x)
        relative_y = _clamp_ratio(settings.relative_y)
        if relative_x is not None and relative_y is not None:
            x_span = max(0, matching.width - window_width)
            y_span = max(0, matching.height - window_height)
            x = matching.x + round(x_span * relative_x)
            y = matching.y + round(y_span * relative_y)
        elif settings.x is not None and settings.y is not None:
            x, y = settings.x, settings.y
        else:
            x, y = _default_position(matching, window_width, window_height)
        x, y = _clamp_to_screen(x, y, matching, window_width, window_height)
        return RestoredPosition(x, y, matching.name, False)

    if settings.x is not None and settings.y is not None:
        best = _screen_for_window(settings.x, settings.y, window_width, window_height, screens)
        if best is not None and _intersection_area(settings.x, settings.y, window_width, window_height, best) > 0:
            x, y = _clamp_to_screen(settings.x, settings.y, best, window_width, window_height)
            return RestoredPosition(x, y, best.name, (x, y) != (settings.x, settings.y))

    primary = screens[0]
    x, y = _default_position(primary, window_width, window_height)
    x, y = _clamp_to_screen(x, y, primary, window_width, window_height)
    return RestoredPosition(x, y, primary.name, True)


def capture_position(
    settings: DesktopPetSettings,
    x: int,
    y: int,
    screens: Sequence[ScreenGeometry],
    window_width: int,
    window_height: int,
) -> DesktopPetSettings:
    screen = _screen_for_window(x, y, window_width, window_height, screens)
    if screen is None:
        return replace(settings, x=round(x), y=round(y))
    x_span = max(1, screen.width - window_width)
    y_span = max(1, screen.height - window_height)
    relative_x = min(1.0, max(0.0, (x - screen.x) / x_span))
    relative_y = min(1.0, max(0.0, (y - screen.y) / y_span))
    return replace(
        settings,
        version=SETTINGS_VERSION,
        screen_name=screen.name,
        x=round(x),
        y=round(y),
        relative_x=relative_x,
        relative_y=relative_y,
        saved_available_geometry=screen,
    )
