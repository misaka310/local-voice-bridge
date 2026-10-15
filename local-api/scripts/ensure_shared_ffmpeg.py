from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "runtime" / "ffmpeg-shared"
LATEST_RELEASE_API = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"


def has_shared_ffmpeg(target: Path) -> bool:
    bin_dir = target / "bin"
    return (
        bin_dir.is_dir()
        and any(bin_dir.glob("avutil-*.dll"))
        and any(bin_dir.glob("avcodec-*.dll"))
        and any(bin_dir.glob("avformat-*.dll"))
    )


def find_asset_url() -> tuple[str, str]:
    with urllib.request.urlopen(LATEST_RELEASE_API) as response:
        payload = json.load(response)
    for asset in payload.get("assets", []):
        name = str(asset.get("name") or "")
        if name.endswith("-full_build-shared.zip"):
            return name, str(asset["browser_download_url"])
    raise RuntimeError("shared FFmpeg zip asset not found in latest Gyan release")


def ensure_shared_ffmpeg(force: bool = False) -> Path:
    if has_shared_ffmpeg(TARGET_DIR) and not force:
        return TARGET_DIR

    asset_name, asset_url = find_asset_url()
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        archive_path = tmp_root / asset_name
        urllib.request.urlretrieve(asset_url, archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(TARGET_DIR)

    extracted_root = next((child for child in TARGET_DIR.iterdir() if child.is_dir() and has_shared_ffmpeg(child)), None)
    if extracted_root is None:
        raise RuntimeError("downloaded FFmpeg archive did not contain shared DLLs")

    final_bin = TARGET_DIR / "bin"
    if final_bin.exists():
        shutil.rmtree(final_bin)
    shutil.move(str(extracted_root / "bin"), str(final_bin))
    for child in list(TARGET_DIR.iterdir()):
        if child == final_bin:
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return TARGET_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure a local shared-FFmpeg runtime for torchcodec.")
    parser.add_argument("--force", action="store_true", help="Re-download even when a shared runtime already exists.")
    args = parser.parse_args()
    target = ensure_shared_ffmpeg(force=args.force)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
