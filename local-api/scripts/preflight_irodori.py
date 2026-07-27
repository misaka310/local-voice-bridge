from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ffmpeg_env import configure_ffmpeg_dll_path
from scripts.audit_runtime_dependencies import audit_pip_check, audit_versions

DEFAULT_MODEL = "Aratako/Irodori-TTS-500M-v3"
DEFAULT_CODEC = "Aratako/Semantic-DACVAE-Japanese-32dim"
SECURITY_BASELINES = {
    "transformers": (Version("5.5.0"), Version("6")),
    "huggingface-hub": (Version("1.5.0"), Version("2")),
    "sentencepiece": (Version("0.2.1"), Version("0.3")),
}


def cache_dir() -> str:
    return os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")


def load_config() -> dict:
    out = {}
    for name in ("config.example.json", "config.json", "config.local.json"):
        path = ROOT / name
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            out.update(data)
    return out


def security_baselines_ok() -> bool:
    for package_name, (minimum, maximum) in SECURITY_BASELINES.items():
        try:
            installed = Version(package_version(package_name))
        except PackageNotFoundError:
            print(f"[ng] {package_name} is not installed.", file=sys.stderr)
            return False
        if installed < minimum or installed >= maximum:
            print(
                f"[ng] {package_name}={installed}; required >= {minimum}, < {maximum}.",
                file=sys.stderr,
            )
            return False
        print(f"[ok] {package_name}={installed}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight for Irodori direct runtime.")
    parser.add_argument("--quick", action="store_true", help="Do not download; only check local cache presence.")
    parser.add_argument("--strict-cuda", action="store_true", help="Fail when CUDA is unavailable.")
    args = parser.parse_args()

    try:
        import torch
    except Exception as exc:
        print(f"[ng] torch import failed: {exc}", file=sys.stderr)
        return 2

    print(f"[ok] python={sys.executable}")
    print(f"[ok] torch={torch.__version__}")
    print(f"[ok] torch.cuda.is_available={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"[ok] torch.version.cuda={torch.version.cuda}")
        print(f"[ok] gpu={torch.cuda.get_device_name(0)}")
    elif args.strict_cuda:
        print("[ng] CUDA is not available. Install NVIDIA driver and rerun setup-voice-env.cmd.", file=sys.stderr)
        return 3
    else:
        print("[warn] CUDA is not available. CPU fallback is very slow.")

    if not security_baselines_ok():
        return 6

    dependency_errors = audit_versions() + audit_pip_check()
    if dependency_errors:
        for error in dependency_errors:
            print(f"[ng] dependency audit: {error}", file=sys.stderr)
        return 7
    print("[ok] dependency audit complete")

    ffmpeg_bin = configure_ffmpeg_dll_path()
    if ffmpeg_bin:
        print(f"[ok] ffmpeg_bin={ffmpeg_bin}")
    else:
        print("[warn] shared FFmpeg DLLs not found under local-api/runtime/ffmpeg-shared/bin")

    for module_name in ("torchaudio", "torchcodec", "irodori_tts", "dacvae", "silentcipher", "audiotools"):
        try:
            __import__(module_name)
        except Exception as exc:
            print(f"[ng] {module_name} import failed: {exc}", file=sys.stderr)
            return 4
        print(f"[ok] {module_name} import")

    config = load_config()
    irodori = config.get("irodori") if isinstance(config.get("irodori"), dict) else {}
    model = str(irodori.get("hfCheckpoint") or DEFAULT_MODEL)
    codec = str(irodori.get("codecRepo") or DEFAULT_CODEC)
    print(f"[ok] hf cache={cache_dir()}")
    print(f"[ok] model={model}")
    print(f"[ok] codec={codec}")

    try:
        from huggingface_hub import hf_hub_download
        local_only = bool(args.quick)
        model_path = hf_hub_download(repo_id=model, filename="model.safetensors", local_files_only=local_only)
        codec_path = hf_hub_download(repo_id=codec, filename="weights.pth", local_files_only=local_only)
        print(f"[ok] model cache file={model_path}")
        print(f"[ok] codec cache file={codec_path}")
    except Exception as exc:
        mode = "local cache check" if args.quick else "download"
        print(f"[ng] Hugging Face {mode} failed: {exc}", file=sys.stderr)
        return 5

    print("[ok] Irodori direct preflight complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
