from __future__ import annotations

import json
import time
from typing import Any

from state_normalization import normalize_reference_voice

DEFAULT_BROWSER_RUNTIME_STATE: dict[str, Any] = {
    "tabs": [],
    "selectedTabId": 0,
    "uiOwnerTabId": 0,
    "lastComposerFocusedTabId": 0,
    "activeConversationTargetTabId": 0,
    "conversationSessions": [],
    "queue": [],
    "currentItem": None,
    "lastPlayedItem": None,
    "seq": 1,
    "updatedAt": 0.0,
}


def safe_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def normalize_browser_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    text = str(value.get("text") or "").strip()[:1600]
    if not text:
        return None
    return {
        "id": str(value.get("id") or "").strip()[:128],
        "mode": str(value.get("mode") or "auto").strip()[:32],
        "reason": str(value.get("reason") or "").strip()[:64],
        "tabId": safe_nonnegative_int(value.get("tabId")),
        "tabTitle": str(value.get("tabTitle") or "")[:200],
        "messageKey": str(value.get("messageKey") or "")[:256],
        "chunkIndex": safe_nonnegative_int(value.get("chunkIndex")),
        "chunkCount": safe_nonnegative_int(value.get("chunkCount")),
        "text": text,
        "voiceProfile": str(value.get("voiceProfile") or "irodori-v3")[:64],
        "referenceVoice": normalize_reference_voice(value.get("referenceVoice")),
        "voicePrompt": "",
        "audioUrl": str(value.get("audioUrl") or "")[:1000] or None,
        "usedReferenceAudio": str(value.get("usedReferenceAudio") or "")[:1000],
    }


def normalize_browser_runtime(value: Any, *, now: float | None = None) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    tabs: list[dict[str, Any]] = []
    for item in raw.get("tabs") if isinstance(raw.get("tabs"), list) else []:
        if not isinstance(item, dict):
            continue
        tab_id = safe_nonnegative_int(item.get("id"))
        if not tab_id:
            continue
        message = item.get("lastAssistantMessage") if isinstance(item.get("lastAssistantMessage"), dict) else None
        normalized_message = None
        if message:
            chunks = [
                str(chunk or "").strip()[:1600]
                for chunk in (message.get("chunks") if isinstance(message.get("chunks"), list) else [])[:128]
                if str(chunk or "").strip()
            ]
            message_key = str(message.get("messageKey") or "").strip()[:256]
            if message_key and chunks:
                normalized_message = {
                    "messageKey": message_key,
                    "chunks": chunks,
                    "completionReason": str(message.get("completionReason") or "")[:128],
                    "completionObservedAt": float(message.get("completionObservedAt") or 0.0),
                    "capturedAt": float(message.get("capturedAt") or 0.0),
                }
        tabs.append(
            {
                "id": tab_id,
                "title": str(item.get("title") or "ChatGPT")[:200],
                "url": str(item.get("url") or "")[:1000],
                "lastReadIndex": max(-1, int(item.get("lastReadIndex") or -1)),
                "lastAutoQueueSignature": str(item.get("lastAutoQueueSignature") or "")[:2000],
                "lastAssistantMessage": normalized_message,
            }
        )
        if len(tabs) >= 32:
            break

    queue_items: list[dict[str, Any]] = []
    for item in raw.get("queue") if isinstance(raw.get("queue"), list) else []:
        normalized = normalize_browser_item(item)
        if normalized:
            queue_items.append(normalized)
        if len(queue_items) >= 256:
            break

    conversation_sessions: list[dict[str, Any]] = []
    for item in raw.get("conversationSessions") if isinstance(raw.get("conversationSessions"), list) else []:
        if not isinstance(item, dict):
            continue
        session_id = safe_nonnegative_int(item.get("sessionId"))
        if not session_id:
            continue
        conversation_sessions.append(
            {
                "sessionId": session_id,
                "tabId": safe_nonnegative_int(item.get("tabId")),
                "location": str(item.get("location") or "")[:1000],
            }
        )
        if len(conversation_sessions) >= 128:
            break

    return {
        "tabs": tabs,
        "selectedTabId": safe_nonnegative_int(raw.get("selectedTabId")),
        "uiOwnerTabId": safe_nonnegative_int(raw.get("uiOwnerTabId")),
        "lastComposerFocusedTabId": safe_nonnegative_int(raw.get("lastComposerFocusedTabId")),
        "activeConversationTargetTabId": safe_nonnegative_int(raw.get("activeConversationTargetTabId")),
        "conversationSessions": conversation_sessions,
        "queue": queue_items,
        "currentItem": normalize_browser_item(raw.get("currentItem")),
        "lastPlayedItem": normalize_browser_item(raw.get("lastPlayedItem")),
        "seq": max(1, safe_nonnegative_int(raw.get("seq"), 1)),
        "updatedAt": float(time.time() if now is None else now),
    }


def clone_browser_runtime(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False))
