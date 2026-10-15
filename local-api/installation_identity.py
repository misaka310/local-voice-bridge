from __future__ import annotations

import hashlib
from pathlib import Path


def installation_id(app_root: Path | str) -> str:
    """Return the existing installation identity without changing its public value."""

    root = Path(app_root).expanduser().resolve()
    return hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:20]


def gpu_mutex_names(instance_id: str) -> tuple[str, str]:
    normalized = "".join(ch for ch in str(instance_id or "") if ch.isalnum() or ch in "-_")[:64]
    if not normalized:
        raise ValueError("instance_id is required")
    return (
        f"Local\\LocalVoiceBridgeGpuSttGate-{normalized}",
        f"Local\\LocalVoiceBridgeGpu-{normalized}",
    )
