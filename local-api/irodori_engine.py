from __future__ import annotations

import gc
import hashlib
import inspect
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ffmpeg_env import configure_ffmpeg_dll_path
from tts_profiles import TtsProfile, TtsProfileError, apply_profile, resolve_tts_profile

_MODEL_CACHE: dict[tuple[Any, ...], Any] = {}
_MODEL_CACHE_LOCK = threading.RLock()
_SYNTHESIS_SETTINGS_LOCK = threading.RLock()
_REFERENCE_LATENT_LOCK = threading.RLock()


class IrodoriError(RuntimeError):
    pass


DEFAULT_SAMPLING_SEED = 10


def _release_unused_cuda_cache(runtime: Any) -> None:
    devices = (
        getattr(runtime, "model_device", ""),
        getattr(runtime, "codec_device", ""),
    )
    if not any(str(device).startswith("cuda") for device in devices):
        return

    try:
        import torch

        if not torch.cuda.is_available():
            return
        gc.collect()
        torch.cuda.empty_cache()
    except Exception:
        # Cache cleanup must never turn a successful synthesis into an API error.
        return


def _safe_name(text: str, request_id: str | None) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    prefix = request_id or f"irodori-{int(time.time())}"
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in prefix)[:48]
    return f"chatgpt-{safe}-{digest}.wav"


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sampling_seed(value: Any) -> int | None:
    if isinstance(value, str) and value.strip().lower() == "random":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_SAMPLING_SEED


def _pick_precision(torch: Any, device: str, configured: str) -> str:
    value = str(configured or "auto").strip().lower()
    if value in {"fp32", "bf16"}:
        return value
    if str(device).startswith("cuda"):
        try:
            if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
                return "bf16"
        except Exception:
            pass
    return "fp32"


def _resolve_device(torch: Any, value: str, *, require_cuda: bool) -> str:
    device = str(value or "auto").strip().lower()
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if require_cuda and not device.startswith("cuda"):
        raise IrodoriError("CUDA GPU is required for the public Irodori direct path. Run setup again after installing an NVIDIA CUDA-capable torch build, or set irodori.requireCuda=false for slow CPU testing.")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise IrodoriError("CUDA was selected but torch.cuda.is_available() is False. Re-run setup-voice-env.cmd and confirm the NVIDIA driver is installed.")
    return device


def _reference_audio_for(reference_voice: str | None, config: dict[str, Any]) -> str | None:
    voice_id = str(reference_voice or "").strip()
    if not voice_id or voice_id.lower() == "none":
        return None
    voices = config.get("referenceVoices") if isinstance(config.get("referenceVoices"), dict) else {}
    item = voices.get(voice_id) if isinstance(voices, dict) else None
    if not isinstance(item, dict):
        raise IrodoriError(f"reference voice not found: {voice_id}. Use Ref=none for the first public run.")
    audio = str(item.get("referenceAudioPath") or "").strip()
    if not audio:
        raise IrodoriError(f"reference voice has no referenceAudioPath: {voice_id}")
    path = Path(audio).expanduser()
    if not path.is_file():
        raise IrodoriError(f"reference voice audio file not found: {path}")
    return str(path)


def _get_runtime(*, model_cfg: dict[str, Any]) -> Any:
    configure_ffmpeg_dll_path()
    with _MODEL_CACHE_LOCK:
        import torch
        from huggingface_hub import hf_hub_download
        from irodori_tts.inference_runtime import InferenceRuntime, RuntimeKey

        repo_id = str(model_cfg.get("hfCheckpoint") or model_cfg.get("model") or "Aratako/Irodori-TTS-500M-v3")
        codec_repo = str(model_cfg.get("codecRepo") or "Aratako/Semantic-DACVAE-Japanese-32dim")
        checkpoint = hf_hub_download(
            repo_id=repo_id,
            filename=str(model_cfg.get("checkpointFile") or "model.safetensors"),
        )
        require_cuda = _bool(model_cfg.get("requireCuda"), True)
        model_device = _resolve_device(
            torch,
            str(model_cfg.get("modelDevice") or "auto"),
            require_cuda=require_cuda,
        )
        codec_device = _resolve_device(
            torch,
            str(model_cfg.get("codecDevice") or model_device),
            require_cuda=require_cuda,
        )
        model_precision = _pick_precision(torch, model_device, str(model_cfg.get("modelPrecision") or "auto"))
        codec_precision = _pick_precision(torch, codec_device, str(model_cfg.get("codecPrecision") or model_precision))
        key = (
            checkpoint,
            model_device,
            codec_repo,
            model_precision,
            codec_device,
            codec_precision,
            bool(model_cfg.get("compileModel", False)),
            bool(model_cfg.get("compileDynamic", False)),
        )
        if key not in _MODEL_CACHE:
            runtime_key = RuntimeKey(
                checkpoint=checkpoint,
                model_device=model_device,
                codec_repo=codec_repo,
                model_precision=model_precision,
                codec_device=codec_device,
                codec_precision=codec_precision,
                compile_model=bool(model_cfg.get("compileModel", False)),
                compile_dynamic=bool(model_cfg.get("compileDynamic", False)),
            )
            _MODEL_CACHE[key] = InferenceRuntime.from_key(runtime_key)
        return _MODEL_CACHE[key]


def prepare_irodori_direct(*, raw_config: dict[str, Any], model_config: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(raw_config.get("irodori") or {})
    cfg.update(model_config or {})
    runtime = _get_runtime(model_cfg=cfg)
    return {
        "modelDevice": str(getattr(runtime, "model_device", "")),
        "codecDevice": str(getattr(runtime, "codec_device", "")),
        "modelPrecision": str(getattr(runtime, "model_precision", "")),
        "codecPrecision": str(getattr(runtime, "codec_precision", "")),
        "speakerConditioning": bool(
            getattr(getattr(runtime, "model_cfg", None), "use_speaker_condition_resolved", False)
        ),
    }


def _require_reference_condition(runtime: Any, ref_wav: str | None) -> bool:
    if not ref_wav:
        return False
    model_cfg = getattr(runtime, "model_cfg", None)
    if not bool(getattr(model_cfg, "use_speaker_condition_resolved", False)):
        raise IrodoriError("the active Irodori runtime cannot apply the selected reference voice")
    return True


def _sampling_quality(
    cfg: dict[str, Any],
    *,
    use_reference: bool,
    profile_name: str | None = None,
    live: bool = False,
) -> tuple[int, float]:
    try:
        profile = resolve_tts_profile(
            profile_name or cfg.get("ttsProfile"),
            live=live,
            use_reference=use_reference,
            legacy_settings=cfg,
        )
    except TtsProfileError as exc:
        raise IrodoriError(str(exc)) from exc
    return profile.num_steps, profile.cfg_scale_speaker


def _resolve_profile(
    cfg: dict[str, Any],
    *,
    profile_name: str | None,
    live: bool,
    use_reference: bool,
) -> TtsProfile:
    try:
        return resolve_tts_profile(
            profile_name or cfg.get("ttsProfile"),
            live=live,
            use_reference=use_reference,
            legacy_settings=cfg,
        )
    except TtsProfileError as exc:
        raise IrodoriError(str(exc)) from exc


def _reference_latent_cache(
    *,
    runtime: Any,
    inference_runtime_module: Any,
    reference_audio: str,
    cfg: dict[str, Any],
    output_dir: Path,
) -> tuple[str, bool]:
    source = Path(reference_audio).expanduser().resolve()
    if not source.is_file():
        raise IrodoriError(f"reference voice audio file not found: {source}")
    configured_cache = str(cfg.get("referenceLatentCacheDir") or "").strip()
    if configured_cache:
        cache_dir = Path(configured_cache).expanduser()
        if not cache_dir.is_absolute():
            cache_dir = output_dir.resolve().parents[1] / cache_dir
    else:
        cache_dir = output_dir.resolve().parent / "reference-latents"
    cache_dir = cache_dir.resolve()
    stat = source.stat()
    fingerprint = "\n".join(
        (
            str(source),
            str(stat.st_size),
            str(stat.st_mtime_ns),
            str(cfg.get("codecRepo") or ""),
            str(cfg.get("codecPrecision") or ""),
            "normalize_db=-16.0",
            "ensure_max=true",
        )
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
    cache_path = cache_dir / f"{source.stem}-{digest}.pt"
    with _REFERENCE_LATENT_LOCK:
        import torch

        if cache_path.is_file():
            try:
                try:
                    torch.load(cache_path, map_location="cpu", weights_only=True)
                except TypeError:
                    torch.load(cache_path, map_location="cpu")
                return str(cache_path), True
            except Exception:
                cache_path.unlink(missing_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        wav, sample_rate = inference_runtime_module._load_audio(str(source))
        latent = runtime.codec.encode_waveform(
            wav.unsqueeze(0),
            sample_rate=int(sample_rate),
            normalize_db=-16.0,
            ensure_max=True,
        ).cpu()
        temporary = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            torch.save(latent, temporary)
            temporary.replace(cache_path)
        finally:
            temporary.unlink(missing_ok=True)
    return str(cache_path), False


@contextmanager
def _runtime_profile_overrides(
    runtime: Any,
    inference_runtime_module: Any,
    profile: TtsProfile,
) -> Iterator[None]:
    """Apply unavoidable runtime-global settings under one generation lock."""

    with _SYNTHESIS_SETTINGS_LOCK:
        watermarker = getattr(runtime, "watermarker", None)
        had_watermark_model = bool(watermarker is not None and hasattr(watermarker, "model"))
        watermark_model = getattr(watermarker, "model", None) if had_watermark_model else None
        measure_start = getattr(inference_runtime_module, "_measure_start", None)
        measure_end = getattr(inference_runtime_module, "_measure_end", None)
        if not profile.sync_stages and (measure_start is None or measure_end is None):
            raise IrodoriError("the active Irodori runtime cannot disable synchronized stage timing safely")
        try:
            if not profile.watermark and had_watermark_model:
                watermarker.model = None
            if not profile.sync_stages:
                inference_runtime_module._measure_start = lambda _device, *_extra: time.perf_counter()
                inference_runtime_module._measure_end = (
                    lambda _device, started, *_extra: time.perf_counter() - started
                )
            yield
        finally:
            if measure_start is not None:
                inference_runtime_module._measure_start = measure_start
            if measure_end is not None:
                inference_runtime_module._measure_end = measure_end
            if had_watermark_model:
                watermarker.model = watermark_model


def synthesize_irodori_direct(
    *,
    raw_config: dict[str, Any],
    model_config: dict[str, Any],
    output_dir: Path,
    text: str,
    request_id: str | None,
    reference_voice: str | None = None,
    voice_prompt: str | None = None,
    profile_name: str | None = None,
    live: bool = False,
) -> tuple[Path, str]:
    configure_ffmpeg_dll_path()
    import irodori_tts.inference_runtime as inference_runtime_module
    from irodori_tts.inference_runtime import SamplingRequest, resolve_cfg_scales, save_wav

    cfg = dict(raw_config.get("irodori") or {})
    cfg.update(model_config or {})
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / _safe_name(text, request_id)
    ref_wav = _reference_audio_for(reference_voice, raw_config)
    profile = _resolve_profile(
        cfg,
        profile_name=profile_name,
        live=live,
        use_reference=bool(ref_wav),
    )
    cfg = apply_profile(cfg, profile)
    caption = str(voice_prompt or cfg.get("caption") or "").strip() or None
    runtime = _get_runtime(model_cfg=cfg)
    use_speaker = _require_reference_condition(runtime, ref_wav)
    use_caption = bool(runtime.model_cfg.use_caption_condition and caption)
    cfg_scale_text, cfg_scale_caption, cfg_scale_speaker, _messages = resolve_cfg_scales(
        cfg_guidance_mode=str(cfg.get("cfgGuidanceMode") or "independent"),
        cfg_scale_text=float(cfg.get("cfgScaleText", profile.cfg_scale_text)),
        cfg_scale_caption=float(cfg.get("cfgScaleCaption", 3.0)),
        cfg_scale_speaker=float(cfg.get("cfgScaleSpeaker", profile.cfg_scale_speaker)),
        cfg_scale=None,
        use_caption_condition=use_caption,
        use_speaker_condition=use_speaker,
    )
    ref_latent: str | None = None
    reference_cache_hit = False
    if ref_wav and profile.reference_mode == "latent":
        ref_latent, reference_cache_hit = _reference_latent_cache(
            runtime=runtime,
            inference_runtime_module=inference_runtime_module,
            reference_audio=ref_wav,
            cfg=cfg,
            output_dir=output_dir,
        )
    request_kwargs: dict[str, Any] = {
        "text": text,
        "caption": caption,
        "ref_wav": None if ref_latent else ref_wav,
        "no_ref": not bool(ref_wav or ref_latent),
        "num_steps": profile.num_steps,
        "t_schedule_mode": profile.t_schedule_mode,
        "sway_coeff": profile.sway_coeff,
        "duration_scale": float(cfg.get("durationScale", 1.0)),
        "num_candidates": 1,
        "decode_mode": profile.decode_mode,
        "cfg_scale_text": cfg_scale_text,
        "cfg_scale_caption": cfg_scale_caption,
        "cfg_scale_speaker": cfg_scale_speaker,
        "cfg_guidance_mode": str(cfg.get("cfgGuidanceMode") or "independent"),
        "cfg_min_t": float(cfg.get("cfgMinT", 0.5)),
        "cfg_max_t": float(cfg.get("cfgMaxT", 1.0)),
        "context_kv_cache": profile.context_kv_cache,
        "trim_tail": profile.trim_tail,
        "seed": _sampling_seed(cfg.get("seed", profile.seed)),
    }
    if ref_latent:
        parameters = inspect.signature(SamplingRequest).parameters
        if "ref_latent" not in parameters:
            raise IrodoriError("the active Irodori runtime cannot consume a cached reference latent")
        request_kwargs["ref_latent"] = ref_latent
    request = SamplingRequest(**request_kwargs)
    result = None
    succeeded = False
    try:
        with _runtime_profile_overrides(runtime, inference_runtime_module, profile):
            result = runtime.synthesize(request, log_fn=None)
        save_wav(out_file, result.audio, result.sample_rate)
        succeeded = True
    finally:
        result = None
        if not succeeded:
            out_file.unlink(missing_ok=True)
        if profile.release_unused_cuda_cache:
            _release_unused_cuda_cache(runtime)
    _ = reference_cache_hit
    return out_file, str(ref_wav or "")


def cache_hint() -> str:
    return os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
