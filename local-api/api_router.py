from __future__ import annotations

import copy
import json
import mimetypes
import secrets
import sys
import threading
from http import HTTPStatus
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, unquote

from http_io import ResponseWriteError, is_normal_client_disconnect, json_response, request_json
from live_http import get_live_state, post_interrupt, post_live_chunk, post_submission
from tts_profiles import TtsProfileError, profile_from_payload
from voice_runtime import VoiceRuntimeError
from voice_service import VoiceServiceError


def route_get(handler, parsed, app: ModuleType) -> None:
    if parsed.path == "/v1/control-panel":
        try:
            payload = app.enrich_runtime_snapshot(app.CONTROL_STATE.snapshot(), handler)
            payload["referenceVoices"] = app.reference_voice_list(app.load_config())
            json_response(handler, HTTPStatus.OK, payload)
        except ResponseWriteError:
            return
        except Exception as exc:
            json_response(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
        return
    if parsed.path == "/v1/control-panel/poll":
        try:
            query = parse_qs(parsed.query)
            after_command_id = int(query.get("after", ["0"])[0] or 0)
            after_event_id = int(query.get("afterEvent", ["0"])[0] or 0)
            consumer_id = query.get("consumer", [None])[0]
            replay_existing = query.get("replayExisting", [""])[0] == "1"
            json_response(
                handler,
                HTTPStatus.OK,
                app.enrich_runtime_snapshot(
                    app.CONTROL_STATE.poll(
                        after_command_id,
                        after_event_id=after_event_id,
                        consumer_id=consumer_id,
                        replay_existing=replay_existing,
                    ),
                    handler,
                ),
            )
        except (TypeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        return
    if parsed.path == "/v1/browser-runtime":
        json_response(
            handler,
            HTTPStatus.OK,
            {"ok": True, "browserRuntime": app.CONTROL_STATE.browser_runtime_snapshot()},
        )
        return
    if parsed.path == "/v1/live/state":
        service = app.live_conversation_for(handler)
        if service is None:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "live conversation is not started"})
        else:
            get_live_state(handler, service)
        return
    if parsed.path == "/v1/desktop-pet":
        try:
            settings = app.load_desktop_pet_settings()
            json_response(
                handler,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "selectedPetId": app.normalize_desktop_pet_id(settings.get("selectedPetId")),
                    "visible": True,
                    "pets": app.desktop_pet_list(),
                },
            )
        except ResponseWriteError:
            return
        except Exception as exc:
            json_response(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
        return
    try:
        config = app.load_config()
        if parsed.path == "/health":
            payload = {
                "ok": True,
                "engine": "irodori_direct",
                "runtime": "irodori_direct",
                "defaultModel": "irodori-v3",
                "models": app.model_list(config),
                "referenceVoices": app.reference_voice_list(config),
                "availableVoiceProfiles": app.model_list(config),
                "availableReferenceVoices": app.reference_voice_list(config),
                "audioOutputDir": "local-api/runtime/audio",
                "cacheHint": app.cache_hint(),
                "instanceId": app.INSTANCE_ID,
                "audioRetention": app.audio_retention_policy(config),
                "pathsExposed": False,
            }
            runtime = app.voice_runtime_snapshot(handler)
            payload["voiceRuntime"] = runtime
            control_snapshot = app.enrich_control_snapshot(app.CONTROL_STATE.snapshot())
            payload["readiness"] = app.structured_readiness(control_snapshot.get("extension"), runtime)
            json_response(handler, HTTPStatus.OK, payload)
            return
        if parsed.path == "/v1/models":
            json_response(handler, HTTPStatus.OK, {"ok": True, "models": app.model_list(config)})
            return
        if parsed.path == "/v1/reference-voices":
            json_response(handler, HTTPStatus.OK, {"ok": True, "voices": app.reference_voice_list(config)})
            return
        if parsed.path.startswith("/audio/"):
            serve_audio(handler, config, parsed.path[len("/audio/"):], app)
            return
        json_response(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
    except ResponseWriteError:
        return
    except Exception as exc:
        json_response(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def route_post(handler, path: str, app: ModuleType) -> None:
    if path == "/v1/admin/shutdown":
        expected = str(getattr(handler.server, "shutdown_token", "") or "")
        supplied = str(handler.headers.get("X-Local-Voice-Token") or "")
        if not expected or not secrets.compare_digest(supplied, expected):
            json_response(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error": "forbidden"})
            return
        json_response(handler, HTTPStatus.OK, {"ok": True, "stopping": True})
        threading.Thread(target=handler.server.shutdown, name="local-voice-http-shutdown", daemon=True).start()
        return
    if path == "/v1/control-panel/settings":
        try:
            payload = request_json(handler)
            payload["initialized"] = True
            json_response(handler, HTTPStatus.OK, app.CONTROL_STATE.update_settings(payload))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/control-panel/command":
        try:
            payload = request_json(handler)
            command = app.CONTROL_STATE.enqueue_command(str(payload.get("command") or ""))
            json_response(handler, HTTPStatus.OK, {"ok": True, "command": command})
        except (json.JSONDecodeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except (OSError, RuntimeError) as exc:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/control-panel/ack":
        try:
            payload = request_json(handler)
            has_command = "commandId" in payload
            has_event = "conversationEventId" in payload
            if not has_command and not has_event:
                raise ValueError("commandId or conversationEventId is required")
            consumer_id = payload.get("consumerId")
            result = {"ok": True, "consumerId": str(consumer_id or "legacy")}
            if has_command:
                result["commandId"] = app.CONTROL_STATE.acknowledge_commands(
                    payload.get("commandId"), consumer_id=consumer_id
                )
            if has_event:
                result["conversationEventId"] = app.CONTROL_STATE.acknowledge_conversation_events(
                    payload.get("conversationEventId"), consumer_id=consumer_id
                )
            json_response(handler, HTTPStatus.OK, result)
        except (json.JSONDecodeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except (OSError, RuntimeError) as exc:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/browser-runtime":
        try:
            payload = request_json(handler)
            browser_runtime = app.CONTROL_STATE.update_browser_runtime(payload)
            json_response(handler, HTTPStatus.OK, {"ok": True, "browserRuntime": browser_runtime})
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except (OSError, RuntimeError) as exc:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/control-panel/state":
        try:
            payload = request_json(handler)
            extension = app.CONTROL_STATE.update_extension_state(payload)
            json_response(handler, HTTPStatus.OK, {"ok": True, "extension": extension})
        except (json.JSONDecodeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/conversation/state":
        try:
            payload = request_json(handler)
            json_response(handler, HTTPStatus.OK, app.CONTROL_STATE.update_conversation_state(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/conversation/event":
        try:
            payload = request_json(handler)
            event = app.CONTROL_STATE.enqueue_conversation_event(
                str(payload.get("type") or ""),
                payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
            )
            json_response(handler, HTTPStatus.OK, {"ok": True, "event": event})
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except (OSError, RuntimeError) as exc:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        return
    if path in {"/v1/conversation/submission", "/v1/live/chunks", "/v1/interrupt"}:
        service = app.live_conversation_for(handler)
        if service is None:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "live conversation is not started"})
            return
        if path == "/v1/conversation/submission":
            post_submission(handler, service)
        elif path == "/v1/live/chunks":
            post_live_chunk(handler, service)
        else:
            post_interrupt(handler, service)
        return
    if path == "/v1/desktop-pet":
        try:
            payload = request_json(handler)
            settings = app.update_desktop_pet_settings(payload.get("petId"))
            json_response(
                handler,
                HTTPStatus.OK,
                {"ok": True, "selectedPetId": settings["selectedPetId"], "visible": True},
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        return
    if path == "/v1/playback/stop":
        runtime = app.voice_runtime_for(handler)
        if runtime is None:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "voice runtime is not started"})
            return
        json_response(handler, HTTPStatus.OK, runtime.stop_playback())
        return
    if path == "/v1/playback/replay":
        try:
            payload = request_json(handler)
            runtime = app.voice_runtime_for(handler)
            if runtime is None:
                raise VoiceRuntimeError("voice runtime is not started")
            volume = min(1.0, max(0.0, float(payload.get("voiceVolume", 0.6))))
            result = runtime.replay(volume=volume, text=str(payload.get("text") or ""))
            source_file = Path(result["path"])
            config = app.load_config()
            result_payload = {
                "ok": True,
                "audioUrl": f"{str(config.get('publicBaseUrl')).rstrip('/')}/audio/{source_file.name}",
                **{key: value for key, value in result.items() if key != "path"},
            }
            json_response(handler, HTTPStatus.OK, result_payload)
        except (ValueError, json.JSONDecodeError, VoiceRuntimeError) as exc:
            json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        return
    if path != "/v1/speak":
        json_response(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        return
    post_speak(handler, app)


def post_speak(handler, app: ModuleType) -> None:
    try:
        config = app.load_config()
        payload = request_json(handler)
        text = app.sanitize_text(payload.get("text"))
        request_id = str(payload.get("requestId") or "") or None
        model = "irodori-v3"
        voice_id = app.normalize_reference_id(payload.get("voiceId") or payload.get("referenceVoice") or "")
        voice_prompt = str(payload.get("voicePrompt") or payload.get("instruct") or "").strip()
        profile = profile_from_payload(
            payload,
            live=False,
            use_reference=bool(voice_id),
            legacy_settings=config.get("irodori"),
        )
        payload["ttsProfile"] = profile.name
        runtime = app.voice_runtime_for(handler)
        if runtime is not None:
            play_local = bool(payload.get("playLocal"))
            try:
                voice_volume = min(1.0, max(0.0, float(payload.get("voiceVolume", 0.6))))
            except (TypeError, ValueError):
                voice_volume = 0.6
            runtime_result = runtime.synthesize(
                {
                    **payload,
                    "text": text,
                    "requestId": request_id,
                    "voiceId": voice_id,
                    "referenceVoice": voice_id,
                    "voicePrompt": voice_prompt,
                },
                text=text,
                volume=voice_volume,
                play_local=play_local,
            )
            source_file = Path(runtime_result["path"])
            audio_url = f"{str(config.get('publicBaseUrl')).rstrip('/')}/audio/{source_file.name}"
            json_response(
                handler,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "engine": "irodori_direct",
                    "runtime": "irodori_direct",
                    "model": model,
                    "voiceId": voice_id,
                    "voiceProfile": model,
                    "referenceVoice": voice_id,
                    "usedReferenceAudio": str(runtime_result.get("usedReferenceAudio") or ""),
                    "ttsProfile": str(runtime_result.get("ttsProfile") or profile.name),
                    "requestId": request_id,
                    "audioUrl": audio_url,
                    "textLength": len(text),
                    "playedLocally": bool(runtime_result.get("playedLocally")),
                    "playbackCompleted": bool(runtime_result.get("playbackCompleted")),
                    "stopped": bool(runtime_result.get("stopped")),
                },
            )
            return
        runtime_config = copy.deepcopy(config)
        runtime_config["referenceVoices"] = app.scan_reference_voices(config)
        source_file, used_reference_audio = app.synthesize_irodori_direct(
            raw_config=runtime_config,
            model_config=app.model_config(config, model),
            output_dir=app.output_dir(config),
            text=text,
            request_id=request_id,
            reference_voice=voice_id or None,
            voice_prompt=voice_prompt,
            profile_name=profile.name,
            live=False,
        )
        cleanup = app.prune_audio(config, preserve=(source_file,))
        if cleanup.deleted_files:
            print(
                f"[maintenance] removed {cleanup.deleted_files} generated audio files "
                f"({cleanup.deleted_bytes} bytes); remaining={cleanup.remaining_files} files/{cleanup.remaining_bytes} bytes"
            )
        audio_url = f"{str(config.get('publicBaseUrl')).rstrip('/')}/audio/{source_file.name}"
        json_response(handler, HTTPStatus.OK, {
            "ok": True,
            "engine": "irodori_direct",
            "runtime": "irodori_direct",
            "model": model,
            "voiceId": voice_id,
            "voiceProfile": model,
            "referenceVoice": voice_id,
            "usedReferenceAudio": used_reference_audio,
            "ttsProfile": profile.name,
            "requestId": request_id,
            "audioUrl": audio_url,
            "textLength": len(text),
        })
    except VoiceRuntimeError as exc:
        print(f"[TTS ERROR] {exc}", file=sys.stderr)
        json_response(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
    except (app.BridgeError, VoiceServiceError, app.IrodoriError, TtsProfileError) as exc:
        print(f"[TTS ERROR] {exc}", file=sys.stderr)
        json_response(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except ResponseWriteError:
        return
    except Exception as exc:
        print(f"[TTS ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        json_response(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def serve_audio(handler, config: dict, name: str, app: ModuleType) -> bool:
    path = app.output_dir(config) / Path(unquote(name)).name
    if not path.exists() or not path.is_file() or path.suffix.lower() not in app.AUDIO_EXTENSIONS:
        json_response(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "audio not found"})
        return True
    data = path.read_bytes()
    try:
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)
    except OSError as exc:
        if is_normal_client_disconnect(exc):
            return False
        raise ResponseWriteError("audio response write failed") from exc
    return True
