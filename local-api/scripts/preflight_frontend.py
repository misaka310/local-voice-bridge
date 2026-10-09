from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


LOCAL_API = Path(__file__).resolve().parents[1]
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))


REQUIRED_MODULES = ("PySide6", "soundfile", "sounddevice", "numpy", "paramiko")


def main() -> int:
    missing = [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        print("frontend dependencies missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        from server import load_config

        config = load_config()
    except Exception as exc:
        print(f"frontend configuration invalid: {exc}", file=sys.stderr)
        return 1
    if str(config.get("generationBackend") or "local") != "remote_ssh":
        print("frontend preflight requires generationBackend=remote_ssh", file=sys.stderr)
        return 1
    print("frontend dependencies: ready")
    print("generationBackend=remote_ssh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
