#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import mimetypes
import os
import secrets
import sys
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# Public model downloads must not be broken by a stale token saved by another
# Hugging Face login. Explicit environment settings still take precedence.
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

from control_state import ControlStateStore
from desktop_pet_config import discover_available_pets
from http_io import ResponseWriteError, is_normal_client_disconnect, json_response, request_json
from irodori_engine import IrodoriError, cache_hint, prepare_irodori_direct, synthesize_irodori_direct
from maintenance import audio_retention_policy, prune_generated_audio
from runtime_readiness import enrich_snapshot, runtime_snapshot, structured_readiness
from server_logging import configure_server_process_logging
from voice_runtime import VoiceRuntime, VoiceRuntimeError

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
INSTANCE_ID = hashlib.sha256(str(APP_ROOT).casefold().encode("utf-8")).hexdigest()[:20]
INSTANCE_STATE_PATH = Path(
    os.environ.get("LOCAL_VOICE_INSTANCE_STATE") or ROOT / "runtime" / "server-instance.json"
).expanduser().resolve()
DESKTOP_PET_SETTINGS_PATH = Path(
    os.environ.get("LOCAL_VOICE_DESKTOP_PET_SETTINGS") or ROOT / "runtime" / "desktop-pet-settings.json"
).expanduser().resolve()
CONTROL_PANEL_STATE_PATH = ROOT / "runtime" / "control-panel-state.json"
DESKTOP_PET_ROOT = ROOT.parent / "extension" / "assets" / "pet"
DESKTOP_PET_SETTINGS_LOCK = threading.Lock()
EXTENSION_MANIFEST_PATH = ROOT.parent / "extension" / "manifest.json"
CONTROL_STATE = ControlStateStore(
    Path(os.environ.get("LOCAL_VOICE_CONTROL_STATE") or CONTROL_PANEL_STATE_PATH).expanduser().resolve()
)
VOICE_RUNTIME: VoiceRuntime | None = None
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
TEXT_FILES = ("voice.txt", "text.txt", "transcript.txt")
DEFAULT_CONFIG: dict[str, Any] = {
    "engine": "irodori_direct",
    "host": "127.0.0.1",
    "port": 8717,
    "publicBaseUrl": "",
    "audioOutputDir": "./runtime/audio",
    "audioRetention": {
        "maxFiles": 1000,
        "maxBytes": 1073741824,
        "maxAgeDays": 14,
    },
    "defaultModel": "irodori-v3",
    "models": {"irodori-v3": {"label": "Irodori v3 direct", "runtime": "irodori_direct", "hfCheckpoint": "Aratako/Irodori-TTS-500M-v3"}},
    "referenceVoicesDir": "./reference/voices",
    "irodori": {
        "hfCheckpoint": "Aratako/Irodori-TTS-500M-v3",
        "codecRepo": "Aratako/Semantic-DACVAE-Japanese-32dim",
        "modelDevice": "auto",
        "codecDevice": "auto",
        "modelPrecision": "auto",
        "codecPrecision": "auto",
        "requireCuda": True,
        "numSteps": 16,
        "tScheduleMode": "sway",
        "swayCoeff": -1.0,
        "durationScale": 1.0,
        "decodeMode": "sequential",
        "contextKvCache": True,
        "releaseUnusedCudaCache": True,
        "seed": 10,
    },
}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class BridgeError(RuntimeError):
    pass


def deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def extension_package_version(path: Path = EXTENSION_MANIFEST_PATH) -> str:
    try:
        payload = load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("version") or "").strip()[:32]


def enrich_control_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    extension = dict(result.get("extension") or {})
    expected = extension_package_version()
    loaded = str(extension.get("loadedVersion") or "").strip()
    connected = bool(extension.get("connected"))
    extension["expectedVersion"] = expected
    extension["updateRequired"] = bool(connected and expected and loaded != expected)
    result["extension"] = extension
    result["components"] = {
        "sttInstalled": importlib.util.find_spec("faster_whisper") is not None
        and importlib.util.find_spec("sounddevice") is not None,
    }
    return result


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    # This API accepts text and can serve locally generated voice audio.  It is
    # intentionally a same-PC service, never a LAN or public endpoint.
    host = str(config.get("host") or "127.0.0.1").strip().lower()
    if host not in LOOPBACK_HOSTS:
        raise BridgeError("このAPIはローカル専用です。host は 127.0.0.1、localhost、::1 のいずれかにしてください")
    config["host"] = "127.0.0.1" if host in {"localhost", "::1"} else host
    public_base_url = str(config.get("publicBaseUrl") or "").strip()
    if public_base_url:
        parsed = urlparse(public_base_url)
        if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
            raise BridgeError("このAPIはローカル専用です。publicBaseUrl は loopback の http URL にしてください")
        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            raise BridgeError("このAPIはローカル専用です。publicBaseUrl に path、query、fragment は指定できません")
        config["publicBaseUrl"] = f"http://127.0.0.1:{parsed.port or config.get('port', 8717)}"
    config["engine"] = "irodori_direct"
    existing = config.get("models") if isinstance(config.get("models"), dict) else {}
    irodori_model = copy.deepcopy(DEFAULT_CONFIG["models"]["irodori-v3"])
    if isinstance(existing.get("irodori-v3"), dict):
        irodori_model = deep_merge(irodori_model, existing["irodori-v3"])
    irodori_model["runtime"] = "irodori_direct"
    config["models"] = {"irodori-v3": irodori_model}
    config["defaultModel"] = "irodori-v3"
    config.setdefault("referenceVoicesDir", "./reference/voices")
    return config


def load_config() -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_CONFIG)
    for name in ("config.example.json", "config.json", "config.local.json"):
        path = ROOT / name
        if path.exists():
            loaded = load_json(path)
            if not isinstance(loaded, dict):
                raise BridgeError(f"config must be JSON object: {path.name}")
            merged = deep_merge(merged, loaded)
    if os.environ.get("LOCAL_VOICE_PORT"):
        merged["port"] = int(os.environ["LOCAL_VOICE_PORT"])
    if os.environ.get("LOCAL_VOICE_PUBLIC_BASE_URL"):
        merged["publicBaseUrl"] = os.environ["LOCAL_VOICE_PUBLIC_BASE_URL"]
    merged = normalize_config(merged)
    if not merged.get("publicBaseUrl"):
        merged["publicBaseUrl"] = f"http://{merged.get('host', '127.0.0.1')}:{int(merged.get('port', 8717))}"
    return merged


def resolve_path(value: Any) -> Path:
    path = Path(str(value or "")).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def output_dir(config: dict[str, Any]) -> Path:
    return resolve_path(config.get("audioOutputDir", "./runtime/audio"))


def prune_audio(config: dict[str, Any], preserve: tuple[Path, ...] = ()):
    policy = audio_retention_policy(config)
    return prune_generated_audio(
        output_dir(config),
        max_files=policy["maxFiles"],
        max_bytes=policy["maxBytes"],
        max_age_days=policy["maxAgeDays"],
        preserve=preserve,
    )


def write_instance_state(token: str, path: Path = INSTANCE_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = {
        "version": 1,
        "pid": os.getpid(),
        "instanceId": INSTANCE_ID,
        "shutdownToken": token,
    }
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def remove_instance_state(token: str, path: Path = INSTANCE_STATE_PATH) -> None:
    try:
        payload = load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if isinstance(payload, dict) and secrets.compare_digest(str(payload.get("shutdownToken") or ""), token):
        path.unlink(missing_ok=True)


def reference_voices_dir(config: dict[str, Any]) -> Path:
    return resolve_path(config.get("referenceVoicesDir", "./reference/voices"))



def normalize_desktop_pet_id(value: Any) -> str:
    pet_id = str(value or "").strip().lower()
    if not pet_id or pet_id in {"none", ".", ".."} or "/" in pet_id or "\\" in pet_id:
        return "placeholder"
    return pet_id


def desktop_pet_list(pet_root: Path | None = None) -> list[dict[str, str]]:
    root = Path(pet_root) if pet_root is not None else DESKTOP_PET_ROOT
    return [
        {"id": choice.selection_id, "label": choice.display_name}
        for choice in discover_available_pets(root)
    ]


def desktop_pet_settings_path() -> Path:
    override = str(os.environ.get("LOCAL_VOICE_DESKTOP_PET_SETTINGS") or "").strip()
    return Path(override).expanduser().resolve() if override else DESKTOP_PET_SETTINGS_PATH


def load_desktop_pet_settings(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else desktop_pet_settings_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def update_desktop_pet_settings(value: Any, path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else desktop_pet_settings_path()
    with DESKTOP_PET_SETTINGS_LOCK:
        settings = load_desktop_pet_settings(target)
        settings.setdefault("version", 1)
        settings["selectedPetId"] = normalize_desktop_pet_id(value)
        settings["visible"] = True
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return settings


def sanitize_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise BridgeError("text is required")
    if len(text) > 1600:
        raise BridgeError("text is too long")
    return text


def model_config(config: dict[str, Any], model: str) -> dict[str, Any]:
    item = config.get("models", {}).get(model)
    return item if isinstance(item, dict) else {}


def model_list(config: dict[str, Any]) -> list[dict[str, str]]:
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    return [{"id": str(k), "label": str(v.get("label") or k), "runtime": str(v.get("runtime") or "")} for k, v in models.items() if isinstance(v, dict)]


def find_text_file(folder: Path) -> Path | None:
    for name in TEXT_FILES:
        path = folder / name
        if path.is_file():
            return path
    return None


def scan_reference_voices(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    base = reference_voices_dir(config)
    if base.is_dir():
        for folder in sorted([p for p in base.iterdir() if p.is_dir()]):
            voice_wav = folder / "voice.wav"
            if not voice_wav.is_file():
                continue
            text_file = find_text_file(folder)
            result[folder.name] = {
                "label": folder.name,
                "referenceAudioPath": str(voice_wav),
                "referenceTextPath": str(text_file) if text_file else "",
                "language": "Japanese",
                "source": "reference/voices",
            }
    configured = config.get("referenceVoices") if isinstance(config.get("referenceVoices"), dict) else {}
    for key, value in configured.items():
        if isinstance(value, dict):
            result[str(key)] = value
    return result


def reference_voice_list(config: dict[str, Any]) -> list[dict[str, str]]:
    voices = scan_reference_voices(config)
    return [{"id": "", "label": "none"}] + [{"id": str(k), "label": str(v.get("label") or k)} for k, v in voices.items()]


def build_voice_runtime(config: dict[str, Any]) -> VoiceRuntime:
    runtime_config = copy.deepcopy(config)
    runtime_config["referenceVoices"] = scan_reference_voices(config)
    selected_model = "irodori-v3"

    def prepare() -> dict[str, Any]:
        return prepare_irodori_direct(
            raw_config=runtime_config,
            model_config=model_config(config, selected_model),
        )

    def synthesize(payload: dict[str, Any]) -> tuple[Path, str]:
        source_file, used_reference_audio = synthesize_irodori_direct(
            raw_config=runtime_config,
            model_config=model_config(config, selected_model),
            output_dir=output_dir(config),
            text=sanitize_text(payload.get("text")),
            request_id=str(payload.get("requestId") or "") or None,
            reference_voice=normalize_reference_id(
                payload.get("voiceId") or payload.get("referenceVoice") or ""
            )
            or None,
            voice_prompt=str(payload.get("voicePrompt") or payload.get("instruct") or "").strip(),
        )
        cleanup = prune_audio(config, preserve=(source_file,))
        if cleanup.deleted_files:
            print(
                f"[maintenance] removed {cleanup.deleted_files} generated audio files "
                f"({cleanup.deleted_bytes} bytes); remaining={cleanup.remaining_files} files/{cleanup.remaining_bytes} bytes"
            )
        return source_file, used_reference_audio

    return VoiceRuntime(prepare_fn=prepare, synthesize_fn=synthesize)


def voice_runtime_for(handler: BaseHTTPRequestHandler | None = None) -> VoiceRuntime | None:
    if handler is not None:
        runtime = getattr(getattr(handler, "server", None), "voice_runtime", None)
        if runtime is not None:
            return runtime
    return VOICE_RUNTIME


def voice_runtime_snapshot(handler: BaseHTTPRequestHandler | None = None) -> dict[str, Any]:
    return runtime_snapshot(voice_runtime_for(handler))


def enrich_runtime_snapshot(payload: dict[str, Any], handler: BaseHTTPRequestHandler | None = None) -> dict[str, Any]:
    return enrich_snapshot(enrich_control_snapshot(payload), voice_runtime_snapshot(handler))


def normalize_reference_id(value: Any) -> str:
    voice_id = str(value or "").strip()
    if voice_id.lower() in {"none", "qwen3", "qwen"}:
        return ""
    return voice_id


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalVoiceBridge/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/control-panel":
            try:
                payload = enrich_runtime_snapshot(CONTROL_STATE.snapshot(), self)
                payload["referenceVoices"] = reference_voice_list(load_config())
                json_response(self, HTTPStatus.OK, payload)
            except ResponseWriteError:
                return
            except Exception as exc:
                json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/v1/control-panel/poll":
            try:
                query = parse_qs(parsed.query)
                after_command_id = int(query.get("after", ["0"])[0] or 0)
                after_event_id = int(query.get("afterEvent", ["0"])[0] or 0)
                consumer_id = query.get("consumer", [None])[0]
                replay_existing = query.get("replayExisting", [""])[0] == "1"
                json_response(
                    self,
                    HTTPStatus.OK,
                    enrich_runtime_snapshot(
                        CONTROL_STATE.poll(
                            after_command_id,
                            after_event_id=after_event_id,
                            consumer_id=consumer_id,
                            replay_existing=replay_existing,
                        ),
                        self,
                    ),
                )
            except (TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/v1/browser-runtime":
            json_response(
                self,
                HTTPStatus.OK,
                {"ok": True, "browserRuntime": CONTROL_STATE.browser_runtime_snapshot()},
            )
            return
        if parsed.path == "/v1/desktop-pet":
            try:
                settings = load_desktop_pet_settings()
                json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "selectedPetId": normalize_desktop_pet_id(settings.get("selectedPetId")),
                        "visible": True,
                        "pets": desktop_pet_list(),
                    },
                )
            except ResponseWriteError:
                return
            except Exception as exc:
                json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        try:
            config = load_config()
            if parsed.path == "/health":
                payload = {
                    "ok": True,
                    "engine": "irodori_direct",
                    "runtime": "irodori_direct",
                    "defaultModel": "irodori-v3",
                    "models": model_list(config),
                    "referenceVoices": reference_voice_list(config),
                    "availableVoiceProfiles": model_list(config),
                    "availableReferenceVoices": reference_voice_list(config),
                    "audioOutputDir": "local-api/runtime/audio",
                    "cacheHint": cache_hint(),
                    "instanceId": INSTANCE_ID,
                    "audioRetention": audio_retention_policy(config),
                    "pathsExposed": False,
                }
                runtime = voice_runtime_snapshot(self)
                payload["voiceRuntime"] = runtime
                payload["readiness"] = structured_readiness(CONTROL_STATE.snapshot().get("extension"), runtime)
                json_response(self, HTTPStatus.OK, payload)
                return
            if parsed.path == "/v1/models":
                json_response(self, HTTPStatus.OK, {"ok": True, "models": model_list(config)})
                return
            if parsed.path == "/v1/reference-voices":
                json_response(self, HTTPStatus.OK, {"ok": True, "voices": reference_voice_list(config)})
                return
            if parsed.path.startswith("/audio/"):
                self.serve_audio(config, parsed.path[len("/audio/"):])
                return
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        except ResponseWriteError:
            return
        except Exception as exc:
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/v1/admin/shutdown":
            expected = str(getattr(self.server, "shutdown_token", "") or "")
            supplied = str(self.headers.get("X-Local-Voice-Token") or "")
            if not expected or not secrets.compare_digest(supplied, expected):
                json_response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "forbidden"})
                return
            json_response(self, HTTPStatus.OK, {"ok": True, "stopping": True})
            threading.Thread(target=self.server.shutdown, name="local-voice-http-shutdown", daemon=True).start()
            return
        if path == "/v1/control-panel/settings":
            try:
                payload = request_json(self)
                payload["initialized"] = True
                json_response(self, HTTPStatus.OK, CONTROL_STATE.update_settings(payload))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/control-panel/command":
            try:
                payload = request_json(self)
                command = CONTROL_STATE.enqueue_command(str(payload.get("command") or ""))
                json_response(self, HTTPStatus.OK, {"ok": True, "command": command})
            except (json.JSONDecodeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except (OSError, RuntimeError) as exc:
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/control-panel/ack":
            try:
                payload = request_json(self)
                has_command = "commandId" in payload
                has_event = "conversationEventId" in payload
                if not has_command and not has_event:
                    raise ValueError("commandId or conversationEventId is required")
                consumer_id = payload.get("consumerId")
                result: dict[str, Any] = {"ok": True, "consumerId": str(consumer_id or "legacy")}
                if has_command:
                    result["commandId"] = CONTROL_STATE.acknowledge_commands(
                        payload.get("commandId"), consumer_id=consumer_id
                    )
                if has_event:
                    result["conversationEventId"] = CONTROL_STATE.acknowledge_conversation_events(
                        payload.get("conversationEventId"), consumer_id=consumer_id
                    )
                json_response(self, HTTPStatus.OK, result)
            except (json.JSONDecodeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except (OSError, RuntimeError) as exc:
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/browser-runtime":
            try:
                payload = request_json(self)
                browser_runtime = CONTROL_STATE.update_browser_runtime(payload)
                json_response(self, HTTPStatus.OK, {"ok": True, "browserRuntime": browser_runtime})
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except (OSError, RuntimeError) as exc:
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/control-panel/state":
            try:
                payload = request_json(self)
                extension = CONTROL_STATE.update_extension_state(payload)
                json_response(self, HTTPStatus.OK, {"ok": True, "extension": extension})
            except (json.JSONDecodeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/conversation/state":
            try:
                payload = request_json(self)
                json_response(self, HTTPStatus.OK, CONTROL_STATE.update_conversation_state(payload))
            except (json.JSONDecodeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/conversation/event":
            try:
                payload = request_json(self)
                event = CONTROL_STATE.enqueue_conversation_event(
                    str(payload.get("type") or ""),
                    payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
                )
                json_response(self, HTTPStatus.OK, {"ok": True, "event": event})
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except (OSError, RuntimeError) as exc:
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/desktop-pet":
            try:
                payload = request_json(self)
                settings = update_desktop_pet_settings(payload.get("petId"))
                json_response(
                    self,
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "selectedPetId": settings["selectedPetId"],
                        "visible": True,
                    },
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        if path == "/v1/playback/stop":
            runtime = voice_runtime_for(self)
            if runtime is None:
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "voice runtime is not started"})
                return
            json_response(self, HTTPStatus.OK, runtime.stop_playback())
            return
        if path == "/v1/playback/replay":
            try:
                payload = request_json(self)
                runtime = voice_runtime_for(self)
                if runtime is None:
                    raise VoiceRuntimeError("voice runtime is not started")
                volume = min(1.0, max(0.0, float(payload.get("voiceVolume", 0.6))))
                result = runtime.replay(volume=volume, text=str(payload.get("text") or ""))
                source_file = Path(result["path"])
                config = load_config()
                result_payload = {
                    "ok": True,
                    "audioUrl": f"{str(config.get('publicBaseUrl')).rstrip('/')}/audio/{source_file.name}",
                    **{key: value for key, value in result.items() if key != "path"},
                }
                json_response(self, HTTPStatus.OK, result_payload)
            except (ValueError, json.JSONDecodeError, VoiceRuntimeError) as exc:
                json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
            return
        if path != "/v1/speak":
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        try:
            config = load_config()
            payload = request_json(self)
            text = sanitize_text(payload.get("text"))
            request_id = str(payload.get("requestId") or "") or None
            model = "irodori-v3"
            voice_id = normalize_reference_id(payload.get("voiceId") or payload.get("referenceVoice") or "")
            voice_prompt = str(payload.get("voicePrompt") or payload.get("instruct") or "").strip()
            runtime = voice_runtime_for(self)
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
                    self,
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
            runtime_config["referenceVoices"] = scan_reference_voices(config)
            source_file, used_reference_audio = synthesize_irodori_direct(
                raw_config=runtime_config,
                model_config=model_config(config, model),
                output_dir=output_dir(config),
                text=text,
                request_id=request_id,
                reference_voice=voice_id or None,
                voice_prompt=voice_prompt,
            )
            cleanup = prune_audio(config, preserve=(source_file,))
            if cleanup.deleted_files:
                print(
                    f"[maintenance] removed {cleanup.deleted_files} generated audio files "
                    f"({cleanup.deleted_bytes} bytes); remaining={cleanup.remaining_files} files/{cleanup.remaining_bytes} bytes"
                )
            audio_url = f"{str(config.get('publicBaseUrl')).rstrip('/')}/audio/{source_file.name}"
            json_response(self, HTTPStatus.OK, {"ok": True, "engine": "irodori_direct", "runtime": "irodori_direct", "model": model, "voiceId": voice_id, "voiceProfile": model, "referenceVoice": voice_id, "usedReferenceAudio": used_reference_audio, "requestId": request_id, "audioUrl": audio_url, "textLength": len(text)})
        except VoiceRuntimeError as exc:
            print(f"[TTS ERROR] {exc}", file=sys.stderr)
            json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        except (BridgeError, IrodoriError) as exc:
            print(f"[TTS ERROR] {exc}", file=sys.stderr)
            json_response(self, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except ResponseWriteError:
            return
        except Exception as exc:
            print(f"[TTS ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
            json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def serve_audio(self, config: dict[str, Any], name: str) -> bool:
        path = output_dir(config) / Path(unquote(name)).name
        if not path.exists() or not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            json_response(self, HTTPStatus.NOT_FOUND, {"ok": False, "error": "audio not found"})
            return True
        data = path.read_bytes()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except OSError as exc:
            if is_normal_client_disconnect(exc):
                return False
            raise ResponseWriteError("audio response write failed") from exc
        return True

    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            status = int(args[1]) if len(args) > 1 else 0
        except (TypeError, ValueError):
            status = 0
        if status >= 400:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> int:
    global VOICE_RUNTIME
    configure_server_process_logging()
    control_nonce = uuid.uuid4().hex
    try:
        config = load_config()
        output_dir(config).mkdir(parents=True, exist_ok=True)
        reference_voices_dir(config).mkdir(parents=True, exist_ok=True)
        cleanup = prune_audio(config)
        VOICE_RUNTIME = build_voice_runtime(config)
        VOICE_RUNTIME.start()
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    if cleanup.deleted_files:
        print(
            f"[maintenance] removed {cleanup.deleted_files} generated audio files "
            f"({cleanup.deleted_bytes} bytes); remaining={cleanup.remaining_files} files/{cleanup.remaining_bytes} bytes"
        )
    host = str(config.get("host", "127.0.0.1"))
    port = int(config.get("port", 8717))
    print(f"Local Voice Bridge listening on http://{host}:{port}")
    print("runtime=irodori_direct")
    print("model=irodori-v3")
    print(f"cacheHint={cache_hint()}")
    httpd = ThreadingHTTPServer((host, port), Handler)
    setattr(httpd, "shutdown_token", control_nonce)
    setattr(httpd, "voice_runtime", VOICE_RUNTIME)
    try:
        write_instance_state(control_nonce)
    except Exception as exc:
        httpd.server_close()
        if VOICE_RUNTIME is not None:
            VOICE_RUNTIME.close()
        print(f"[FATAL] could not write server instance state: {exc}", file=sys.stderr)
        return 2
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        pass
    finally:
        httpd.server_close()
        if VOICE_RUNTIME is not None:
            VOICE_RUNTIME.close()
        remove_instance_state(control_nonce)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
