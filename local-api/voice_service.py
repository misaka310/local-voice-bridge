from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from audio_quality import inspect_wav_dict
from gpu_arbiter import GpuArbiter
from irodori_engine import prepare_irodori_direct, synthesize_irodori_direct
from maintenance import audio_retention_policy, prune_generated_audio
from tts_profiles import profile_from_payload
from voice_runtime import VoiceRuntime


ROOT = Path(__file__).resolve().parent
TEXT_FILES = ("voice.txt", "text.txt", "transcript.txt")


class VoiceServiceError(ValueError):
    pass


def remote_tts_settings(config: dict[str, Any]) -> dict[str, Any] | None:
    raw = config.get("remoteTts")
    if not isinstance(raw, dict) or not bool(raw.get("enabled")):
        return None
    base_url = str(raw.get("baseUrl") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise VoiceServiceError("remoteTts.baseUrl must be an http(s) URL")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise VoiceServiceError("remoteTts.baseUrl must use a loopback host")
    return {
        "baseUrl": base_url,
        "healthPath": str(raw.get("healthPath") or "/health"),
        "speakPath": str(raw.get("speakPath") or "/v1/speak"),
        "model": str(raw.get("model") or "irodori_v3_low_latency"),
        "timeoutSeconds": max(5.0, float(raw.get("timeoutSeconds") or 180.0)),
    }


def _remote_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", str(path or "").lstrip("/"))


def _read_json_response(request: Request | str, *, timeout: float) -> dict[str, Any]:
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise VoiceServiceError("remote TTS returned a non-object JSON response")
    return payload


def prepare_remote_tts(config: dict[str, Any]) -> dict[str, Any]:
    remote = remote_tts_settings(config)
    if remote is None:
        raise VoiceServiceError("remoteTts is not enabled")
    payload = _read_json_response(
        _remote_url(remote["baseUrl"], remote["healthPath"]),
        timeout=min(15.0, float(remote["timeoutSeconds"])),
    )
    if payload.get("ok") is not True:
        raise VoiceServiceError(f"remote TTS worker is not ready: {payload!r}")
    return {
        "runtime": "remote_worker",
        "baseUrl": remote["baseUrl"],
        "model": remote["model"],
        "health": payload,
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
    }
    if payload.get("seed") is not None:
        body["seed"] = payload.get("seed")
    request = Request(
        _remote_url(remote["baseUrl"], remote["speakPath"]),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    response = _read_json_response(request, timeout=float(remote["timeoutSeconds"]))
    result = response.get("result") if isinstance(response.get("result"), dict) else response
    if response.get("ok") is False or result.get("ok") is False:
        raise VoiceServiceError(
            str(result.get("errorMessage") or result.get("error") or response.get("error") or "remote TTS generation failed")
        )
    audio_url = str(result.get("audioUrl") or "").strip()
    if not audio_url:
        raise VoiceServiceError(f"remote TTS response has no audioUrl: {response!r}")

    parsed_audio = urlparse(audio_url)
    if parsed_audio.hostname in {"127.0.0.1", "localhost", "::1"} and parsed_audio.path.startswith("/audio/"):
        audio_url = _remote_url(
            remote["baseUrl"],
            parsed_audio.path + (f"?{parsed_audio.query}" if parsed_audio.query else ""),
        )
    else:
        audio_url = urljoin(remote["baseUrl"].rstrip("/") + "/", audio_url)

    output = output_dir(config)
    output.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(f"{request_id}\n{text}".encode("utf-8")).hexdigest()[:20]
    target = output / f"remote-{token}.wav"
    with urlopen(audio_url, timeout=min(60.0, float(remote["timeoutSeconds"]))) as audio_response:
        target.write_bytes(audio_response.read())
    if not target.is_file() or target.stat().st_size < 256:
        target.unlink(missing_ok=True)
        raise VoiceServiceError("remote TTS returned an unexpectedly small audio file")
    return target, reference_voice


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


def build_voice_runtime(
    config: dict[str, Any],
    *,
    instance_id: str,
    event_logger: Any | None = None,
) -> VoiceRuntime:
    runtime_config = copy.deepcopy(config)
    runtime_config["referenceVoices"] = scan_reference_voices(config)
    selected_model = "irodori-v3"
    remote = remote_tts_settings(runtime_config)

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
