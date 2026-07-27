from __future__ import annotations

from typing import Any

EMPTY_RUNTIME_SNAPSHOT: dict[str, Any] = {
    "readiness": "not_started",
    "ready": False,
    "detail": {},
    "error": "",
    "repairRequired": False,
    "dependencies": {},
    "phase": "idle",
    "queueSize": 0,
    "currentText": "",
    "lastOperation": "",
    "replayAvailable": False,
    "startedAt": 0.0,
}


def runtime_snapshot(runtime: Any | None) -> dict[str, Any]:
    if runtime is None:
        return dict(EMPTY_RUNTIME_SNAPSHOT)
    value = runtime.snapshot()
    return dict(value) if isinstance(value, dict) else dict(EMPTY_RUNTIME_SNAPSHOT)


def structured_readiness(extension: dict[str, Any] | None, runtime: dict[str, Any]) -> dict[str, Any]:
    extension_state = extension if isinstance(extension, dict) else {}
    connected = bool(extension_state.get("connected"))
    try:
        tabs_count = max(0, int(extension_state.get("tabsCount") or 0))
    except (TypeError, ValueError):
        tabs_count = 0
    dependencies = runtime.get("dependencies") if isinstance(runtime.get("dependencies"), dict) else {}
    dependencies_ready = bool(dependencies) and all(bool(value) for value in dependencies.values())
    browser_state = "ready" if connected and tabs_count > 0 else "waiting"
    return {
        "process": "ready",
        "dependencies": "ready" if dependencies_ready else "failed",
        "browserExtension": browser_state,
        "tabs": tabs_count,
        "deviceOrModel": str(runtime.get("readiness") or "not_started"),
        "lastOperation": str(runtime.get("lastOperation") or ""),
        "repairRequired": bool(runtime.get("repairRequired")),
        "ready": bool(runtime.get("ready") and browser_state == "ready"),
    }


def enrich_snapshot(payload: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["voiceRuntime"] = dict(runtime)
    result["readiness"] = structured_readiness(result.get("extension"), runtime)
    return result
