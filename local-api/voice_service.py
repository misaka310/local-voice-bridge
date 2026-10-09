from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from audio_quality import inspect_wav_dict
from gpu_arbiter import GpuArbiter
from irodori_engine import prepare_irodori_direct, synthesize_irodori_direct
from maintenance import audio_retention_policy, prune_generated_audio
from tts_profiles import profile_from_payload
from voice_runtime import VoiceRuntime


ROOT = Path(__file__).resolve().parent
TEXT_FILES = ("voice.txt", "text.txt", "transcript.txt")
MAX_REMOTE_JSON_BYTES = 1024 * 1024
MAX_REMOTE_AUDIO_BYTES = 64 * 1024 * 1024


class VoiceServiceError(ValueError):
    pass


def remote_tts_settings(config: dict[str, Any]) -> dict[str, Any] | None:
    raw = config.get("remoteTts")
    if not isinstance(raw, dict) or not bool(raw.get("enabled")):
        return None
    if str(config.get("generationBackend") or "local").strip().lower() == "remote_ssh":
        raise VoiceServiceError("remoteTts and generationBackend=remote_ssh are mutually exclusive")
    base_url = str(raw.get("baseUrl") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise VoiceServiceError("remoteTts.baseUrl must be an http(s) URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise VoiceServiceError("remoteTts.baseUrl must use a loopback host")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise VoiceServiceError("remoteTts.baseUrl must not contain credentials, a query, or a fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise VoiceServiceError("remoteTts.baseUrl contains an invalid port") from exc
    return {
        "baseUrl": base_url,
        "healthPath": str(raw.get("healthPath") or "/health"),
        "speakPath": str(raw.get("speakPath") or "/v1/speak"),
        "model": str(raw.get("model") or "irodori_v3_low_latency"),
        "timeoutSeconds": max(5.0, float(raw.get("timeoutSeconds") or 180.0)),
    }


def _remote_url(base_url: str, path: str) -> str:
    path = str(path or "").strip()
    parsed_path = urlparse(path)
    if parsed_path.scheme or parsed_path.netloc or parsed_path.fragment:
        raise VoiceServiceError("remote TTS API paths must be relative paths without a fragment")
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def _url_origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise VoiceServiceError("remote TTS URL contains an invalid port") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise VoiceServiceError("remote TTS URL must use an http(s) host")
    return scheme, parsed.hostname.lower(), port if port is not None else (443 if scheme == "https" else 80)


def _resolve_remote_audio_url(base_url: str, audio_url: str) -> str:
    audio_url = str(audio_url or "").strip()
    parsed_audio = urlparse(audio_url)
    if parsed_audio.fragment:
        raise VoiceServiceError("remote TTS audio URL must not contain a fragment")
    if parsed_audio.username is not None or parsed_audio.password is not None:
        raise VoiceServiceError("remote TTS audio URL must not contain credentials")

    if parsed_audio.scheme or parsed_audio.netloc:
        if parsed_audio.scheme and parsed_audio.scheme.lower() not in {"http", "https"}:
            raise VoiceServiceError("remote TTS audio URL must use HTTP(S)")
        if not parsed_audio.hostname or parsed_audio.hostname.lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise VoiceServiceError("remote TTS audio URL must use the worker loopback origin")
        if parsed_audio.path.startswith("/audio/"):
            mapped_path = parsed_audio.path
            if parsed_audio.query:
                mapped_path += "?" + parsed_audio.query
            return _remote_url(base_url, mapped_path)
        if _url_origin(audio_url) != _url_origin(base_url):
            raise VoiceServiceError("remote TTS audio URL must use the worker loopback origin")
        return audio_url

    return _remote_url(base_url, audio_url)


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._origin = _url_origin(base_url)

    def redirect_request(
        self,
        request: Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        if _url_origin(new_url) != self._origin:
            response.close()
            raise VoiceServiceError("remote TTS cross-origin redirect blocked")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _open_remote(request: Request | str, *, base_url: str, timeout: float):
    request_url = request.full_url if isinstance(request, Request) else str(request)
    if _url_origin(request_url) != _url_origin(base_url):
        raise VoiceServiceError("remote TTS requests must use the worker loopback origin")
    opener = build_opener(_SameOriginRedirectHandler(base_url))
    try:
        return opener.open(request, timeout=timeout)
    except HTTPError as exc:
        exc.close()
        raise VoiceServiceError(f"remote TTS worker returned HTTP {exc.code}") from None
    except (URLError, HTTPException, OSError, TimeoutError):
        raise VoiceServiceError("remote TTS worker request failed") from None


def _read_bounded_response(response: Any, *, max_bytes: int, label: str) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError) as exc:
            raise VoiceServiceError(f"remote TTS {label} response has an invalid Content-Length") from exc
        if declared_length < 0 or declared_length > max_bytes:
            raise VoiceServiceError(f"remote TTS {label} response is too large")
    try:
        data = response.read(max_bytes + 1)
    except (HTTPException, OSError, TimeoutError):
        raise VoiceServiceError(f"remote TTS {label} response could not be read") from None
    if len(data) > max_bytes:
        raise VoiceServiceError(f"remote TTS {label} response is too large")
    return data


def _read_json_response(
    request: Request | str,
    *,
    base_url: str,
    timeout: float,
) -> dict[str, Any]:
    with _open_remote(request, base_url=base_url, timeout=timeout) as response:
        raw_payload = _read_bounded_response(response, max_bytes=MAX_REMOTE_JSON_BYTES, label="JSON")
    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VoiceServiceError("remote TTS returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise VoiceServiceError("remote TTS returned a non-object JSON response")
    return payload


def prepare_remote_tts(config: dict[str, Any]) -> dict[str, Any]:
    remote = remote_tts_settings(config)
    if remote is None:
        raise VoiceServiceError("remoteTts is not enabled")
    payload = _read_json_response(
        _remote_url(remote["baseUrl"], remote["healthPath"]),
        base_url=remote["baseUrl"],
        timeout=min(15.0, float(remote["timeoutSeconds"])),
    )
    if payload.get("ok") is not True:
        raise VoiceServiceError("remote TTS worker is not ready")
    return {
        "runtime": "remote_worker",
        "baseUrl": remote["baseUrl"],
        "model": remote["model"],
        "health": {"ok": True},
    }


def synthesize_remote_tts(
    *,
    config: dict[str, Any],
    payload: dict[str, Any],
    reference_voice: str,
    profile_name: str,
) -> tuple[Path, str]:
    remote = remote_tts_settings(config)
    if remote is None:
        raise VoiceServiceError("remoteTts is not enabled")
    text = sanitize_text(payload.get("text"))
    request_id = str(payload.get("requestId") or "").strip()
    body = {
        "requestId": request_id,
        "text": text,
        "model": remote["model"],
        "voiceId": reference_voice,
        "referenceVoice": reference_voice,
        "ttsProfile": profile_name,
        "speedScale": float(payload.get("speedScale") or 1.0),
        "voiceVolume": float(payload.get("voiceVolume") or 1.0),
        "playLocal": False,
        "format": "wav",
    }
    if payload.get("seed") is not None:
        body["seed"] = payload.get("seed")
    request = Request(
        _remote_url(remote["baseUrl"], remote["speakPath"]),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    response = _read_json_response(
        request,
        base_url=remote["baseUrl"],
        timeout=float(remote["timeoutSeconds"]),
    )
    result = response.get("result") if isinstance(response.get("result"), dict) else response
    if response.get("ok") is False or result.get("ok") is False:
        raise VoiceServiceError("remote TTS generation failed")
    audio_url = str(result.get("audioUrl") or "").strip()
    if not audio_url:
        raise VoiceServiceError("remote TTS response has no audioUrl")

    audio_url = _resolve_remote_audio_url(remote["baseUrl"], audio_url)

    output = output_dir(config)
    output.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(f"{request_id}\n{text}".encode("utf-8")).hexdigest()[:20]
    target = output / f"remote-{token}.wav"
    with _open_remote(
        audio_url,
        base_url=remote["baseUrl"],
        timeout=min(60.0, float(remote["timeoutSeconds"])),
    ) as audio_response:
        audio_data = _read_bounded_response(
            audio_response,
            max_bytes=MAX_REMOTE_AUDIO_BYTES,
            label="audio",
        )
    if len(audio_data) < 256:
        raise VoiceServiceError("remote TTS returned an unexpectedly small audio file")
    if audio_data[:4] not in {b"RIFF", b"RF64"} or audio_data[8:12] != b"WAVE":
        raise VoiceServiceError("remote TTS returned invalid WAV audio")
    target.write_bytes(audio_data)
    return target, reference_voice


@dataclass(frozen=True)
class ReferenceAsset:
    voice_id: str
    audio_path: Path
    reference_text: str = ""


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


def reference_voices_dir(config: dict[str, Any]) -> Path:
    return resolve_path(config.get("referenceVoicesDir", "./reference/voices"))


def sanitize_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise VoiceServiceError("text is required")
    if len(text) > 1600:
        raise VoiceServiceError("text is too long")
    return text


def model_config(config: dict[str, Any], model: str) -> dict[str, Any]:
    item = config.get("models", {}).get(model)
    return item if isinstance(item, dict) else {}


def model_list(config: dict[str, Any]) -> list[dict[str, str]]:
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    return [
        {"id": str(key), "label": str(value.get("label") or key), "runtime": str(value.get("runtime") or "")}
        for key, value in models.items()
        if isinstance(value, dict)
    ]


def _find_text_file(folder: Path) -> Path | None:
    for name in TEXT_FILES:
        path = folder / name
        if path.is_file():
            return path
    return None


def scan_reference_voices(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    base = reference_voices_dir(config)
    if base.is_dir():
        for folder in sorted(path for path in base.iterdir() if path.is_dir()):
            voice_wav = folder / "voice.wav"
            if not voice_wav.is_file():
                continue
            text_file = _find_text_file(folder)
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
    return [{"id": "", "label": "none"}] + [
        {"id": str(key), "label": str(value.get("label") or key)} for key, value in voices.items()
    ]


def normalize_reference_id(value: Any) -> str:
    voice_id = str(value or "").strip()
    if voice_id.lower() in {"none", "qwen3", "qwen"}:
        return ""
    return voice_id


def resolve_reference_asset(config: dict[str, Any], reference_voice: Any) -> ReferenceAsset | None:
    voice_id = normalize_reference_id(reference_voice)
    if not voice_id:
        return None
    item = scan_reference_voices(config).get(voice_id)
    if not isinstance(item, dict):
        raise VoiceServiceError(f"reference voice not found: {voice_id}")
    audio_value = str(item.get("referenceAudioPath") or "").strip()
    if not audio_value:
        raise VoiceServiceError(f"reference voice has no reference audio: {voice_id}")
    audio_path = resolve_path(audio_value)
    if not audio_path.is_file():
        raise VoiceServiceError(f"reference voice audio file not found: {voice_id}")
    text_value = str(item.get("referenceTextPath") or "").strip()
    reference_text = ""
    if text_value:
        text_path = resolve_path(text_value)
        if text_path.is_file():
            try:
                reference_text = text_path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                raise VoiceServiceError(f"reference voice text could not be read: {voice_id}") from exc
    return ReferenceAsset(voice_id=voice_id, audio_path=audio_path, reference_text=reference_text)


def _build_remote_voice_runtime(
    config: dict[str, Any],
    *,
    event_logger: Any | None = None,
) -> VoiceRuntime:
    from remote_generation import RemoteGenerationClient, RemoteGenerationConfig

    remote = config.get("remoteGeneration") if isinstance(config.get("remoteGeneration"), dict) else {}
    client = RemoteGenerationClient(
        RemoteGenerationConfig(
            ssh_alias=str(remote.get("sshAlias") or ""),
            remote_repo_root=str(remote.get("remoteRepoRoot") or ""),
            connect_timeout_seconds=float(remote.get("connectTimeoutSeconds", 7.0)),
            preferred_cuda_device=str(remote.get("preferredCudaDevice") or ""),
            preferred_gpu_min_free_mib=int(remote.get("preferredGpuMinFreeMiB", 8192)),
        ),
        output_dir=output_dir(config),
    )

    def prepare() -> dict[str, Any]:
        return client.prepare()

    def synthesize(payload: dict[str, Any]) -> tuple[Path, str]:
        reference_voice = normalize_reference_id(
            payload.get("voiceId") or payload.get("referenceVoice") or ""
        )
        live = bool(payload.get("live"))
        profile = profile_from_payload(
            payload,
            live=live,
            use_reference=bool(reference_voice),
            legacy_settings=config.get("irodori"),
        )
        outbound = dict(payload)
        outbound["text"] = sanitize_text(payload.get("text"))
        outbound["resolvedTtsProfile"] = profile.name
        outbound["referenceVoice"] = reference_voice
        asset = resolve_reference_asset(config, reference_voice)
        source_file, used_reference_audio = client.synthesize(
            outbound,
            reference_audio=asset.audio_path if asset is not None else None,
            reference_text=asset.reference_text if asset is not None else "",
        )
        cleanup = prune_audio(config, preserve=(source_file,))
        if cleanup.deleted_files:
            print(
                f"[maintenance] removed {cleanup.deleted_files} generated audio files "
                f"({cleanup.deleted_bytes} bytes); remaining={cleanup.remaining_files} files/{cleanup.remaining_bytes} bytes"
            )
        return source_file, used_reference_audio

    return VoiceRuntime(
        prepare_fn=prepare,
        synthesize_fn=synthesize,
        quality_check_fn=lambda path: inspect_wav_dict(path, config.get("audioQuality")),
        event_logger=event_logger,
    )


def build_voice_runtime(
    config: dict[str, Any],
    *,
    instance_id: str,
    event_logger: Any | None = None,
) -> VoiceRuntime:
    backend = str(config.get("generationBackend") or "local").strip().lower()
    remote = remote_tts_settings(config)
    if backend == "remote_ssh":
        return _build_remote_voice_runtime(config, event_logger=event_logger)
    runtime_config = copy.deepcopy(config)
    runtime_config["referenceVoices"] = scan_reference_voices(config)
    selected_model = "irodori-v3"

    def prepare() -> dict[str, Any]:
        if remote is not None:
            return prepare_remote_tts(runtime_config)
        return prepare_irodori_direct(
            raw_config=runtime_config,
            model_config=model_config(config, selected_model),
        )

    def synthesize(payload: dict[str, Any]) -> tuple[Path, str]:
        reference_voice = normalize_reference_id(
            payload.get("voiceId") or payload.get("referenceVoice") or ""
        )
        live = bool(payload.get("live"))
        profile = profile_from_payload(
            payload,
            live=live,
            use_reference=bool(reference_voice),
            legacy_settings=runtime_config.get("irodori"),
        )
        payload["resolvedTtsProfile"] = profile.name
        if remote is not None:
            source_file, used_reference_audio = synthesize_remote_tts(
                config=runtime_config,
                payload=payload,
                reference_voice=reference_voice,
                profile_name=profile.name,
            )
        else:
            source_file, used_reference_audio = synthesize_irodori_direct(
                raw_config=runtime_config,
                model_config=model_config(config, selected_model),
                output_dir=output_dir(config),
                text=sanitize_text(payload.get("text")),
                request_id=str(payload.get("requestId") or "") or None,
                reference_voice=reference_voice or None,
                voice_prompt=str(payload.get("voicePrompt") or payload.get("instruct") or "").strip(),
                profile_name=profile.name,
                live=live,
            )
        cleanup = prune_audio(config, preserve=(source_file,))
        if cleanup.deleted_files:
            print(
                f"[maintenance] removed {cleanup.deleted_files} generated audio files "
                f"({cleanup.deleted_bytes} bytes); remaining={cleanup.remaining_files} files/{cleanup.remaining_bytes} bytes"
            )
        return source_file, used_reference_audio

    return VoiceRuntime(
        prepare_fn=prepare,
        synthesize_fn=synthesize,
        gpu_arbiter=None if remote is not None else GpuArbiter(instance_id, event_logger=event_logger),
        quality_check_fn=lambda path: inspect_wav_dict(path, config.get("audioQuality")),
        event_logger=event_logger,
    )
