from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    if str(config.get("generationBackend") or "local").strip().lower() == "remote_ssh":
        return _build_remote_voice_runtime(config, event_logger=event_logger)
    runtime_config = copy.deepcopy(config)
    runtime_config["referenceVoices"] = scan_reference_voices(config)
    selected_model = "irodori-v3"

    def prepare() -> dict[str, Any]:
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
        gpu_arbiter=GpuArbiter(instance_id, event_logger=event_logger),
        quality_check_fn=lambda path: inspect_wav_dict(path, config.get("audioQuality")),
        event_logger=event_logger,
    )
