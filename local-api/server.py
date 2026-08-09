#!/usr/bin/env python3
from __future__ import annotations

import copy
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

import api_router
from control_state import ControlStateStore
from desktop_pet_config import discover_available_pets
from http_io import ResponseWriteError, is_normal_client_disconnect, json_response, request_json
from http_io import MAX_POST_BODY_BYTES, browser_origin_allowed, validate_post_request
from installation_identity import installation_id
from irodori_engine import IrodoriError, cache_hint, synthesize_irodori_direct
from live_conversation import LiveConversationService
from live_http import get_live_state, post_interrupt, post_live_chunk, post_submission
from maintenance import audio_retention_policy
from runtime_events import RuntimeEventLogger, default_event_log_path
from runtime_readiness import enrich_snapshot, runtime_snapshot, structured_readiness
from server_logging import configure_server_process_logging
from tts_profiles import TtsProfileError, profile_from_payload
from voice_runtime import VoiceRuntime, VoiceRuntimeError
from voice_service import (
    VoiceServiceError,
    build_voice_runtime as build_voice_runtime_service,
    model_config,
    model_list,
    normalize_reference_id,
    output_dir,
    prune_audio,
    reference_voice_list,
    reference_voices_dir,
    sanitize_text,
    scan_reference_voices,
)

ROOT = Path(__file__).resolve().parent
APP_ROOT = ROOT.parent
INSTANCE_ID = installation_id(APP_ROOT)
EVENT_LOGGER = RuntimeEventLogger(default_event_log_path(APP_ROOT))
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
LIVE_CONVERSATION: LiveConversationService | None = None
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
    "audioQuality": {
        "minRms": 0.005,
        "maxClipFraction": 0.002,
        "maxAbsDcOffset": 0.03,
        "maxDiffSpikeFraction": 0.002,
        "maxHighBandRatio": 0.08,
        "maxSpectralFlatness": 0.35,
        "minDurationSeconds": 0.05,
        "maxDurationSeconds": 120.0,
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
        "referenceNumSteps": 32,
        "cfgScaleSpeaker": 6.0,
        "tScheduleMode": "sway",
        "swayCoeff": -1.0,
        "durationScale": 1.0,
        "decodeMode": "sequential",
        "contextKvCache": True,
        "releaseUnusedCudaCache": True,
        "referenceLatentCacheDir": "./runtime/reference-latents",
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









def build_voice_runtime(config: dict[str, Any]) -> VoiceRuntime:
    return build_voice_runtime_service(config, instance_id=INSTANCE_ID, event_logger=EVENT_LOGGER)


def voice_runtime_for(handler: BaseHTTPRequestHandler | None = None) -> VoiceRuntime | None:
    if handler is not None:
        runtime = getattr(getattr(handler, "server", None), "voice_runtime", None)
        if runtime is not None:
            return runtime
    return VOICE_RUNTIME


def voice_runtime_snapshot(handler: BaseHTTPRequestHandler | None = None) -> dict[str, Any]:
    return runtime_snapshot(voice_runtime_for(handler))


def live_conversation_for(handler: BaseHTTPRequestHandler | None = None) -> LiveConversationService | None:
    if handler is not None:
        service = getattr(getattr(handler, "server", None), "live_conversation", None)
        if service is not None:
            return service
    return LIVE_CONVERSATION


def enrich_runtime_snapshot(payload: dict[str, Any], handler: BaseHTTPRequestHandler | None = None) -> dict[str, Any]:
    return enrich_snapshot(enrich_control_snapshot(payload), voice_runtime_snapshot(handler))


def normalize_reference_id(value: Any) -> str:
    voice_id = str(value or "").strip()
    if voice_id.lower() in {"none", "qwen3", "qwen"}:
        return ""
    return voice_id


class _RouterContext:
    def __getattr__(self, name: str) -> Any:
        return globals()[name]


ROUTER_CONTEXT = _RouterContext()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalVoiceBridge/1.0"

    def do_OPTIONS(self) -> None:
        origin = self.headers.get("Origin")
        if origin and not browser_origin_allowed(origin):
            json_response(self, HTTPStatus.FORBIDDEN, {"ok": False, "error": "browser origin is not allowed"})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:
        api_router.route_get(self, urlparse(self.path), ROUTER_CONTEXT)

    def do_POST(self) -> None:
        problem = validate_post_request(self)
        if problem is not None:
            status, error = problem
            json_response(self, status, {"ok": False, "error": error})
            return
        api_router.route_post(self, urlparse(self.path).path, ROUTER_CONTEXT)

    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            status = int(args[1]) if len(args) > 1 else 0
        except (TypeError, ValueError):
            status = 0
        if status >= 400:
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main() -> int:
    global VOICE_RUNTIME, LIVE_CONVERSATION
    configure_server_process_logging()
    control_nonce = uuid.uuid4().hex
    try:
        config = load_config()
        output_dir(config).mkdir(parents=True, exist_ok=True)
        reference_voices_dir(config).mkdir(parents=True, exist_ok=True)
        cleanup = prune_audio(config)
        VOICE_RUNTIME = build_voice_runtime(config)
        VOICE_RUNTIME.start()
        LIVE_CONVERSATION = LiveConversationService(
            runtime=VOICE_RUNTIME,
            state_path=ROOT / "runtime" / "live-conversation-state.json",
            max_pending_chunks=2,
            event_logger=EVENT_LOGGER,
        )
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
    setattr(httpd, "live_conversation", LIVE_CONVERSATION)
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
