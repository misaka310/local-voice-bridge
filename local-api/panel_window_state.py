from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize


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
