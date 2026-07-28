from __future__ import annotations

import gc
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

from ffmpeg_env import configure_ffmpeg_dll_path

_MODEL_CACHE: dict[tuple[Any, ...], Any] = {}
_MODEL_CACHE_LOCK = threading.RLock()


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


def _require_reference_condition(runtime: Any, ref_wav: str | None) -> bool:
    if not ref_wav:
        return False
    model_cfg = getattr(runtime, "model_cfg", None)
    if not bool(getattr(model_cfg, "use_speaker_condition_resolved", False)):
        raise IrodoriError("the active Irodori runtime cannot apply the selected reference voice")
    return True


def _sampling_quality(cfg: dict[str, Any], *, use_reference: bool) -> tuple[int, float]:
    default_steps = int(cfg.get("numSteps", 16))
    steps = int(cfg.get("referenceNumSteps", 32)) if use_reference else default_steps
    speaker_scale = float(cfg.get("cfgScaleSpeaker", 6.0 if use_reference else 5.0))
    return steps, speaker_scale


def synthesize_irodori_direct(
    *,
    raw_config: dict[str, Any],
    model_config: dict[str, Any],
    output_dir: Path,
    text: str,
    request_id: str | None,
    reference_voice: str | None = None,
    voice_prompt: str | None = None,
) -> tuple[Path, str]:
    configure_ffmpeg_dll_path()
    from irodori_tts.inference_runtime import SamplingRequest, resolve_cfg_scales, save_wav

    cfg = dict(raw_config.get("irodori") or {})
    cfg.update(model_config or {})
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / _safe_name(text, request_id)
    ref_wav = _reference_audio_for(reference_voice, raw_config)
    caption = str(voice_prompt or cfg.get("caption") or "").strip() or None
    runtime = _get_runtime(model_cfg=cfg)
    use_speaker = _require_reference_condition(runtime, ref_wav)
    num_steps, cfg_scale_speaker_requested = _sampling_quality(cfg, use_reference=use_speaker)
    use_caption = bool(runtime.model_cfg.use_caption_condition and caption)
    cfg_scale_text, cfg_scale_caption, cfg_scale_speaker, _messages = resolve_cfg_scales(
        cfg_guidance_mode=str(cfg.get("cfgGuidanceMode") or "independent"),
        cfg_scale_text=float(cfg.get("cfgScaleText", 3.0)),
        cfg_scale_caption=float(cfg.get("cfgScaleCaption", 3.0)),
        cfg_scale_speaker=cfg_scale_speaker_requested,
        cfg_scale=None,
        use_caption_condition=use_caption,
        use_speaker_condition=use_speaker,
    )
    request = SamplingRequest(
        text=text,
        caption=caption,
        ref_wav=ref_wav,
        no_ref=not bool(ref_wav),
        num_steps=num_steps,
        t_schedule_mode=str(cfg.get("tScheduleMode") or "sway"),
        sway_coeff=float(cfg.get("swayCoeff", -1.0)),
        duration_scale=float(cfg.get("durationScale", 1.0)),
        num_candidates=1,
        decode_mode=str(cfg.get("decodeMode") or "sequential"),
        cfg_scale_text=cfg_scale_text,
        cfg_scale_caption=cfg_scale_caption,
        cfg_scale_speaker=cfg_scale_speaker,
        cfg_guidance_mode=str(cfg.get("cfgGuidanceMode") or "independent"),
        cfg_min_t=float(cfg.get("cfgMinT", 0.5)),
        cfg_max_t=float(cfg.get("cfgMaxT", 1.0)),
        context_kv_cache=_bool(cfg.get("contextKvCache"), True),
        trim_tail=_bool(cfg.get("trimTail"), True),
        seed=_sampling_seed(cfg.get("seed", DEFAULT_SAMPLING_SEED)),
    )
    result = None
    try:
        result = runtime.synthesize(request, log_fn=None)
        save_wav(out_file, result.audio, result.sample_rate)
    finally:
        result = None
        if _bool(cfg.get("releaseUnusedCudaCache"), True):
            _release_unused_cuda_cache(runtime)
    return out_file, str(ref_wav or "")


def cache_hint() -> str:
    return os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
