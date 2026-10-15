from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME_FFMPEG_DIR = ROOT / "runtime" / "ffmpeg-shared"
_CONFIGURED_BIN: Path | None = None
_DLL_DIRECTORY_HANDLES: dict[str, object] = {}


def _has_shared_dlls(bin_dir: Path) -> bool:
    return (
        bin_dir.is_dir()
        and any(bin_dir.glob("avutil-*.dll"))
        and any(bin_dir.glob("avcodec-*.dll"))
        and any(bin_dir.glob("avformat-*.dll"))
    )


def find_shared_ffmpeg_bin() -> Path | None:
    env_bin = os.environ.get("LOCAL_VOICE_FFMPEG_BIN")
    if env_bin:
        candidate = Path(env_bin).expanduser()
        if _has_shared_dlls(candidate):
            return candidate

    direct = RUNTIME_FFMPEG_DIR / "bin"
    if _has_shared_dlls(direct):
        return direct

    if RUNTIME_FFMPEG_DIR.is_dir():
        for child in sorted(RUNTIME_FFMPEG_DIR.iterdir()):
            candidate = child / "bin"
            if _has_shared_dlls(candidate):
                return candidate

    return None


def configure_ffmpeg_dll_path() -> Path | None:
    global _CONFIGURED_BIN
    bin_dir = find_shared_ffmpeg_bin()
    if not bin_dir:
        return None

    bin_dir = bin_dir.resolve()
    bin_text = str(bin_dir)
    normalized_bin = os.path.normcase(os.path.normpath(bin_text))
    path_value = os.environ.get("PATH", "")
    path_parts = path_value.split(os.pathsep) if path_value else []
    deduplicated_parts: list[str] = []
    seen: set[str] = set()
    for part in path_parts:
        if not part:
            continue
        normalized_part = os.path.normcase(os.path.normpath(part))
        if normalized_part == normalized_bin or normalized_part in seen:
            continue
        seen.add(normalized_part)
        deduplicated_parts.append(part)
    os.environ["PATH"] = os.pathsep.join([bin_text, *deduplicated_parts])

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if callable(add_dll_directory) and normalized_bin not in _DLL_DIRECTORY_HANDLES:
        _DLL_DIRECTORY_HANDLES[normalized_bin] = add_dll_directory(bin_text)
    _CONFIGURED_BIN = bin_dir
    return bin_dir
