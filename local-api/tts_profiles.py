from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ALLOWED_PROFILE_NAMES = ("speed", "balanced", "bridge")
ALLOWED_STEP_COUNTS = (12, 16, 32)


class TtsProfileError(ValueError):
    pass


@dataclass(frozen=True)
class TtsProfile:
    name: str
    num_steps: int
    reference_mode: str
    sync_stages: bool
    watermark: bool
    release_unused_cuda_cache: bool
    t_schedule_mode: str = "sway"
    sway_coeff: float = -1.0
    context_kv_cache: bool = True
    model_precision: str = "bf16"
    codec_precision: str = "bf16"
    cfg_scale_text: float = 3.0
    cfg_scale_speaker: float = 6.0
    decode_mode: str = "sequential"
    trim_tail: bool = True
    seed: int = 10

    def as_config(self) -> dict[str, Any]:
        return {
            "ttsProfile": self.name,
            "numSteps": self.num_steps,
            "referenceMode": self.reference_mode,
            "syncStages": self.sync_stages,
            "watermark": self.watermark,
            "releaseUnusedCudaCache": self.release_unused_cuda_cache,
            "tScheduleMode": self.t_schedule_mode,
            "swayCoeff": self.sway_coeff,
            "contextKvCache": self.context_kv_cache,
            "modelPrecision": self.model_precision,
            "codecPrecision": self.codec_precision,
            "cfgScaleText": self.cfg_scale_text,
            "cfgScaleSpeaker": self.cfg_scale_speaker,
            "decodeMode": self.decode_mode,
            "trimTail": self.trim_tail,
            "seed": self.seed,
        }


_PROFILES = {
    "speed": TtsProfile(
        name="speed",
        num_steps=12,
        reference_mode="latent",
        sync_stages=False,
        watermark=False,
        release_unused_cuda_cache=False,
    ),
    "balanced": TtsProfile(
        name="balanced",
        num_steps=16,
        reference_mode="latent",
        sync_stages=False,
        watermark=False,
        release_unused_cuda_cache=False,
    ),
    "bridge": TtsProfile(
        name="bridge",
        num_steps=32,
        reference_mode="wav",
        sync_stages=True,
        watermark=True,
        release_unused_cuda_cache=True,
        model_precision="auto",
        codec_precision="auto",
    ),
}


def _normalize_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _validate_legacy_steps(settings: Mapping[str, Any] | None) -> None:
    source = dict(settings or {})
    for key in ("numSteps", "referenceNumSteps"):
        if key not in source or source.get(key) in (None, ""):
            continue
        try:
            steps = int(source[key])
        except (TypeError, ValueError) as exc:
            raise TtsProfileError(f"{key} must be one of {ALLOWED_STEP_COUNTS}") from exc
        if steps not in ALLOWED_STEP_COUNTS:
            raise TtsProfileError(f"unsupported Irodori step count: {steps}")


def resolve_tts_profile(
    value: Any = None,
    *,
    live: bool = False,
    use_reference: bool = False,
    legacy_settings: Mapping[str, Any] | None = None,
) -> TtsProfile:
    """Resolve the only supported optimization profiles.

    Live defaults to speed. Legacy requests preserve the existing quality contract:
    selected reference voices use bridge, while Ref=none uses balanced.
    """

    _validate_legacy_steps(legacy_settings)
    name = _normalize_name(value)
    if not name:
        name = "speed" if live else ("bridge" if use_reference else "balanced")
    if name not in _PROFILES:
        raise TtsProfileError(
            f"unsupported TTS profile: {name or value!s}; expected one of {', '.join(ALLOWED_PROFILE_NAMES)}"
        )
    return _PROFILES[name]


def profile_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    live: bool,
    use_reference: bool,
    legacy_settings: Mapping[str, Any] | None = None,
) -> TtsProfile:
    source = dict(payload or {})
    value = source.get("ttsProfile") or source.get("optimizationProfile") or source.get("profile")
    return resolve_tts_profile(
        value,
        live=live,
        use_reference=use_reference,
        legacy_settings=legacy_settings,
    )


def apply_profile(settings: Mapping[str, Any] | None, profile: TtsProfile) -> dict[str, Any]:
    merged = dict(settings or {})
    merged.update(profile.as_config())
    # The old referenceNumSteps branch must never override the resolved profile.
    merged["referenceNumSteps"] = profile.num_steps
    return merged


def normalize_persisted_profile(value: Any, *, use_reference: bool) -> str:
    name = _normalize_name(value)
    if name in _PROFILES:
        return name
    return "bridge" if use_reference else "balanced"
