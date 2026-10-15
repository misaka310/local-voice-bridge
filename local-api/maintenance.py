from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
DEFAULT_AUDIO_MAX_FILES = 1000
DEFAULT_AUDIO_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_AUDIO_MAX_AGE_DAYS = 14
_CLEANUP_LOCK = threading.Lock()


@dataclass(frozen=True)
class CleanupResult:
    scanned_files: int
    scanned_bytes: int
    deleted_files: int
    deleted_bytes: int
    remaining_files: int
    remaining_bytes: int
    failed_files: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))


def audio_retention_policy(config: dict[str, Any] | None) -> dict[str, int]:
    raw = config.get("audioRetention") if isinstance(config, dict) else None
    values = raw if isinstance(raw, dict) else {}
    return {
        "maxFiles": _bounded_int(values.get("maxFiles"), DEFAULT_AUDIO_MAX_FILES, 1, 100_000),
        "maxBytes": _bounded_int(values.get("maxBytes"), DEFAULT_AUDIO_MAX_BYTES, 10 * 1024 * 1024, 100 * 1024 * 1024 * 1024),
        "maxAgeDays": _bounded_int(values.get("maxAgeDays"), DEFAULT_AUDIO_MAX_AGE_DAYS, 1, 3650),
    }


def _audio_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    rows: list[Path] = []
    try:
        candidates = directory.iterdir()
    except OSError:
        return []
    for path in candidates:
        try:
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                rows.append(path)
        except OSError:
            continue
    return rows


def prune_generated_audio(
    directory: Path,
    *,
    max_files: int = DEFAULT_AUDIO_MAX_FILES,
    max_bytes: int = DEFAULT_AUDIO_MAX_BYTES,
    max_age_days: int = DEFAULT_AUDIO_MAX_AGE_DAYS,
    preserve: Iterable[Path] = (),
    now: float | None = None,
) -> CleanupResult:
    directory = Path(directory)
    current_time = time.time() if now is None else float(now)
    protected = {Path(path).resolve() for path in preserve}
    max_files = max(1, int(max_files))
    max_bytes = max(1, int(max_bytes))
    max_age_seconds = max(1, int(max_age_days)) * 24 * 60 * 60

    with _CLEANUP_LOCK:
        rows: list[tuple[Path, float, int]] = []
        failed = 0
        for path in _audio_files(directory):
            try:
                stat = path.stat()
                rows.append((path, float(stat.st_mtime), int(stat.st_size)))
            except OSError:
                failed += 1

        rows.sort(key=lambda item: (item[1], item[0].name.casefold()))
        scanned_files = len(rows)
        scanned_bytes = sum(size for _path, _mtime, size in rows)
        remaining_files = scanned_files
        remaining_bytes = scanned_bytes
        deleted_files = 0
        deleted_bytes = 0

        for path, modified, size in rows:
            try:
                is_protected = path.resolve() in protected
            except OSError:
                is_protected = False
            expired = current_time - modified > max_age_seconds
            over_count = remaining_files > max_files
            over_bytes = remaining_bytes > max_bytes
            if is_protected or not (expired or over_count or over_bytes):
                continue
            try:
                path.unlink()
            except OSError:
                failed += 1
                continue
            remaining_files -= 1
            remaining_bytes -= size
            deleted_files += 1
            deleted_bytes += size

        return CleanupResult(
            scanned_files=scanned_files,
            scanned_bytes=scanned_bytes,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            remaining_files=remaining_files,
            remaining_bytes=remaining_bytes,
            failed_files=failed,
        )


def clear_generated_audio(directory: Path) -> CleanupResult:
    directory = Path(directory)
    with _CLEANUP_LOCK:
        rows: list[tuple[Path, int]] = []
        failed = 0
        for path in _audio_files(directory):
            try:
                rows.append((path, int(path.stat().st_size)))
            except OSError:
                failed += 1
        scanned_bytes = sum(size for _path, size in rows)
        deleted_files = 0
        deleted_bytes = 0
        for path, size in rows:
            try:
                path.unlink()
            except OSError:
                failed += 1
                continue
            deleted_files += 1
            deleted_bytes += size
        remaining = _audio_files(directory)
        remaining_bytes = 0
        for path in remaining:
            try:
                remaining_bytes += int(path.stat().st_size)
            except OSError:
                failed += 1
        return CleanupResult(
            scanned_files=len(rows),
            scanned_bytes=scanned_bytes,
            deleted_files=deleted_files,
            deleted_bytes=deleted_bytes,
            remaining_files=len(remaining),
            remaining_bytes=remaining_bytes,
            failed_files=failed,
        )


def format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{int(value)} B"


def trim_file_to_tail(path: Path, max_bytes: int) -> None:
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.trim.tmp")
    try:
        with path.open("rb") as source:
            source.seek(-max_bytes, os.SEEK_END)
            tail = source.read(max_bytes)
        temporary.write_bytes(tail)
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
