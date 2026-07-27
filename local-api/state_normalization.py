from __future__ import annotations

from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "voiceVolume": 0.6,
    "referenceVoice": "",
    "referenceVoiceExplicit": False,
    "micConversationEnabled": False,
    "sttModel": "small",
    "cancelGraceMs": 700,
}

DEFAULT_CONVERSATION_STATE: dict[str, Any] = {
    "phase": "off",
    "statusText": "マイク会話オフ",
    "sttDevice": "",
    "sttModel": "small",
    "error": "",
    "updatedAt": 0.0,
}

DEFAULT_EXTENSION_STATE: dict[str, Any] = {
    "connected": False,
    "statusText": "Waiting for ChatGPT",
    "statusLevel": "info",
    "currentText": "",
    "queueSize": 0,
    "isPlaying": False,
    "playbackPhase": "idle",
    "replayAvailable": False,
    "tabsCount": 0,
    "loadedVersion": "",
    "updatedAt": 0.0,
}

ALLOWED_CONVERSATION_PHASES = {
    "off",
    "idle",
    "recording",
    "preparing_model",
    "transcribing",
    "pending_send",
    "sending",
    "waiting_response",
    "speaking",
    "error",
}


def clamp_volume(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_SETTINGS["voiceVolume"])
    return min(1.0, max(0.0, number))


def normalize_reference_voice(value: Any) -> str:
    normalized = str(value or "").strip()
    if normalized.lower() in {"none", "qwen", "qwen3"}:
        return ""
    if "/" in normalized or "\\" in normalized:
        return ""
    return normalized


def normalize_stt_model(value: Any) -> str:
    normalized = str(value or DEFAULT_SETTINGS["sttModel"]).strip()
    return normalized if normalized in {"small", "medium", "large-v3-turbo"} else str(DEFAULT_SETTINGS["sttModel"])


def normalize_cancel_grace_ms(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return int(DEFAULT_SETTINGS["cancelGraceMs"])
    return min(5000, max(0, number))


def normalize_settings(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    reference_voice = normalize_reference_voice(raw.get("referenceVoice", raw.get("voiceId", "")))
    reference_voice_explicit = (
        bool(raw.get("referenceVoiceExplicit"))
        if "referenceVoiceExplicit" in raw
        else bool(reference_voice)
    )
    return {
        "enabled": bool(raw.get("enabled", DEFAULT_SETTINGS["enabled"])),
        "voiceVolume": clamp_volume(raw.get("voiceVolume", DEFAULT_SETTINGS["voiceVolume"])),
        "referenceVoice": reference_voice,
        "referenceVoiceExplicit": reference_voice_explicit,
        "micConversationEnabled": bool(raw.get("micConversationEnabled", DEFAULT_SETTINGS["micConversationEnabled"])),
        "sttModel": normalize_stt_model(raw.get("sttModel")),
        "cancelGraceMs": normalize_cancel_grace_ms(raw.get("cancelGraceMs")),
    }


def normalize_conversation_state(value: Any, *, now: float) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    phase = str(raw.get("phase") or "idle").strip().lower()
    if phase not in ALLOWED_CONVERSATION_PHASES:
        phase = "error"
    return {
        "phase": phase,
        "statusText": str(raw.get("statusText") or "待機中"),
        "sttDevice": str(raw.get("sttDevice") or ""),
        "sttModel": normalize_stt_model(raw.get("sttModel")),
        "error": str(raw.get("error") or ""),
        "updatedAt": float(now),
    }


def normalize_extension_state(value: Any, *, now: float) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    try:
        queue_size = max(0, int(raw.get("queueSize") or 0))
    except (TypeError, ValueError):
        queue_size = 0
    try:
        tabs_count = max(0, int(raw.get("tabsCount") or 0))
    except (TypeError, ValueError):
        tabs_count = 0
    return {
        "connected": True,
        "statusText": str(raw.get("statusText") or "Ready"),
        "statusLevel": str(raw.get("statusLevel") or "info"),
        "currentText": str(raw.get("currentText") or ""),
        "queueSize": queue_size,
        "isPlaying": bool(raw.get("isPlaying")),
        "playbackPhase": str(raw.get("playbackPhase") or "idle"),
        "replayAvailable": bool(raw.get("replayAvailable")),
        "tabsCount": tabs_count,
        "loadedVersion": str(raw.get("loadedVersion") or "").strip()[:32],
        "updatedAt": float(now),
    }
