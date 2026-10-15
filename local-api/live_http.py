from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from conversation_submission import (
    SubmissionConflict,
    SubmissionNotFound,
    SubmissionValidationError,
)
from http_io import ResponseWriteError, json_response, request_json
from live_conversation import (
    LiveBackpressure,
    LiveChunkConflict,
    LiveConversationError,
    LiveConversationService,
)


def get_live_state(handler: Any, service: LiveConversationService) -> None:
    try:
        json_response(handler, HTTPStatus.OK, service.snapshot())
    except ResponseWriteError:
        return
    except Exception as exc:
        json_response(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def post_submission(handler: Any, service: LiveConversationService) -> None:
    try:
        payload = request_json(handler)
        action = str(payload.get("action") or "")
        submission = service.apply_submission(action, payload)
        json_response(
            handler,
            HTTPStatus.OK,
            {
                "ok": True,
                "action": action,
                "submission": submission,
                "sendAllowed": action.strip().lower() == "arm" and submission.get("phase") == "armed",
            },
        )
    except (json.JSONDecodeError, SubmissionValidationError, LiveConversationError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except (SubmissionConflict, SubmissionNotFound) as exc:
        json_response(handler, HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    except (OSError, RuntimeError) as exc:
        json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})


def post_live_chunk(handler: Any, service: LiveConversationService) -> None:
    try:
        payload = request_json(handler)
        accepted = service.enqueue_chunk(payload)
        json_response(handler, HTTPStatus.ACCEPTED, {"ok": True, **accepted})
    except json.JSONDecodeError as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except LiveBackpressure as exc:
        json_response(
            handler,
            HTTPStatus.TOO_MANY_REQUESTS,
            {"ok": False, "error": str(exc), "retryAfterMs": exc.retry_after_ms},
        )
    except (LiveChunkConflict, SubmissionConflict) as exc:
        json_response(handler, HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    except (LiveConversationError, SubmissionValidationError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except (OSError, RuntimeError) as exc:
        json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})


def post_interrupt(handler: Any, service: LiveConversationService) -> None:
    try:
        payload = request_json(handler)
        result = service.interrupt(payload)
        json_response(handler, HTTPStatus.OK, {"ok": True, **result})
    except (json.JSONDecodeError, LiveConversationError, ValueError) as exc:
        json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except (OSError, RuntimeError) as exc:
        json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
