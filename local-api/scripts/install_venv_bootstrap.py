from __future__ import annotations

import site
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_NAME = "local_voice_ffmpeg_bootstrap.pth"


def write_bootstrap() -> Path:
    site_packages = next(Path(path) for path in site.getsitepackages() if path.endswith("site-packages"))
    bootstrap = site_packages / BOOTSTRAP_NAME
    bootstrap.write_text(
        str(ROOT) + "\n"
        + "import ffmpeg_env; ffmpeg_env.configure_ffmpeg_dll_path()\n",
        encoding="utf-8",
    )
    return bootstrap


def main() -> int:
    print(write_bootstrap())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
