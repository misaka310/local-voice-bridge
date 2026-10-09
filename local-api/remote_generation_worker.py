from __future__ import annotations

import argparse
import copy
import io
import os
import ctypes
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO, Callable

from irodori_engine import prepare_irodori_direct, synthesize_irodori_direct
from remote_generation_protocol import (
    PROTOCOL_VERSION,
    RemoteGenerationProtocolError,
    read_frame,
    write_frame,
)


MAX_REFERENCE_WAV_BYTES = 32 * 1024 * 1024
MAX_GENERATED_WAV_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_TEXT_CHARS = 64 * 1024
REMOTE_REFERENCE_ID = "remote-request"
DEFAULT_PREFERRED_GPU_MIN_FREE_MIB = 8192
_SELECTED_GPU: dict[str, Any] = {}


def query_nvidia_gpus() -> list[dict[str, Any]]:
    class MemoryInfo(ctypes.Structure):
        _fields_ = [
            ("total", ctypes.c_ulonglong),
            ("free", ctypes.c_ulonglong),
            ("used", ctypes.c_ulonglong),
        ]

    try:
        nvml = ctypes.WinDLL("nvml.dll")
        nvml.nvmlInit_v2.restype = ctypes.c_int
        nvml.nvmlShutdown.restype = ctypes.c_int
        nvml.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        nvml.nvmlDeviceGetCount_v2.restype = ctypes.c_int
        nvml.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        nvml.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
        nvml.nvmlDeviceGetUUID.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        nvml.nvmlDeviceGetUUID.restype = ctypes.c_int
        nvml.nvmlDeviceGetName.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        nvml.nvmlDeviceGetName.restype = ctypes.c_int
        nvml.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryInfo)]
        nvml.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
    except (AttributeError, OSError):
        return []

    if nvml.nvmlInit_v2() != 0:
        return []
    try:
        count = ctypes.c_uint(0)
        if nvml.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
            return []
        gpus: list[dict[str, Any]] = []
        for index in range(int(count.value)):
            handle = ctypes.c_void_p()
            if nvml.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) != 0:
                continue
            uuid_buffer = ctypes.create_string_buffer(96)
            name_buffer = ctypes.create_string_buffer(96)
            memory = MemoryInfo()
            if nvml.nvmlDeviceGetUUID(handle, uuid_buffer, len(uuid_buffer)) != 0:
                continue
            if nvml.nvmlDeviceGetName(handle, name_buffer, len(name_buffer)) != 0:
                continue
            if nvml.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) != 0:
                continue
            uuid = uuid_buffer.value.decode("utf-8", errors="replace").strip()
            if not uuid:
                continue
            gpus.append(
                {
                    "index": str(index),
                    "uuid": uuid,
                    "name": name_buffer.value.decode("utf-8", errors="replace").strip(),
                    "freeMiB": max(0, int(memory.free // (1024 * 1024))),
                }
            )
        return gpus
    finally:
        nvml.nvmlShutdown()


def select_cuda_device(
    gpus: list[dict[str, Any]],
    *,
    preferred_device: str = "",
    preferred_min_free_mib: int = DEFAULT_PREFERRED_GPU_MIN_FREE_MIB,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for gpu in gpus:
        uuid = str(gpu.get("uuid") or "").strip()
        index = str(gpu.get("index") or "").strip()
        name = str(gpu.get("name") or "").strip()
        try:
            free_mib = max(0, int(gpu.get("freeMiB", 0)))
        except (TypeError, ValueError):
            continue
        if not uuid:
            continue
        candidates.append({"index": index, "uuid": uuid, "name": name, "freeMiB": free_mib})
    if not candidates:
        return {}

    preferred = str(preferred_device or "").strip()
    minimum = max(0, int(preferred_min_free_mib))
    if preferred:
        preferred_lower = preferred.lower()
        for candidate in candidates:
            if candidate["index"] == preferred or candidate["uuid"].lower() == preferred_lower:
                if candidate["freeMiB"] >= minimum:
                    return candidate
                break

    return max(candidates, key=lambda item: item["freeMiB"])


def configure_cuda_visibility(
    *,
    preferred_device: str = "",
    preferred_min_free_mib: int = DEFAULT_PREFERRED_GPU_MIN_FREE_MIB,
    gpu_query: Callable[[], list[dict[str, Any]]] = query_nvidia_gpus,
) -> dict[str, Any]:
    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
    selected = select_cuda_device(
        gpu_query(),
        preferred_device=preferred_device,
        preferred_min_free_mib=preferred_min_free_mib,
    )
    if selected:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(selected["uuid"])
    global _SELECTED_GPU
    _SELECTED_GPU = dict(selected)
    return selected


class _PrefixedStream:
    def __init__(self, prefix: bytes, stream: BinaryIO) -> None:
        self._prefix = bytearray(prefix)
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if size < 0:
            prefix = bytes(self._prefix)
            self._prefix.clear()
            return prefix + self._stream.read()
        if self._prefix:
            take = min(size, len(self._prefix))
            head = bytes(self._prefix[:take])
            del self._prefix[:take]
            if take == size:
                return head
            return head + self._stream.read(size - take)
        return self._stream.read(size)


def _model_config(config: dict[str, Any]) -> dict[str, Any]:
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    value = models.get("irodori-v3") if isinstance(models, dict) else None
    return value if isinstance(value, dict) else {}


def _safe_error(_exc: BaseException, *, message: str) -> str:
    # Backend exceptions may contain absolute cache/repository paths. Never send
    # those details across the machine boundary.
    return message[:512]


def _response_base(request: dict[str, Any], *, ok: bool, op: str) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "ok": ok,
        "op": op,
        "requestId": str(request.get("requestId") or "")[:128],
    }


def _handle_prepare(
    request: dict[str, Any],
    stdout: BinaryIO,
    *,
    config_loader: Callable[[], dict[str, Any]],
) -> None:
    try:
        config = config_loader()
        with redirect_stdout(sys.stderr):
            detail = prepare_irodori_direct(raw_config=config, model_config=_model_config(config))
        header = _response_base(request, ok=True, op="prepare")
        runtime_detail = dict(detail) if isinstance(detail, dict) else {}
        if _SELECTED_GPU:
            runtime_detail.update(
                selectedCudaDevice=str(_SELECTED_GPU.get("uuid") or ""),
                selectedGpuName=str(_SELECTED_GPU.get("name") or ""),
                selectedGpuFreeMiBAtLaunch=int(_SELECTED_GPU.get("freeMiB") or 0),
            )
        header["runtime"] = runtime_detail
        write_frame(stdout, header)
    except BaseException as exc:
        header = _response_base(request, ok=False, op="prepare")
        header.update(
            errorCode="prepare_failed",
            error=_safe_error(exc, message="Irodori generation backend preparation failed"),
        )
        write_frame(stdout, header)


def _validate_synthesis_request(request: dict[str, Any], payload: bytes) -> None:
    text = str(request.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > 1600:
        raise ValueError("text is too long")
    has_reference = bool(request.get("hasReference"))
    if has_reference and not payload:
        raise ValueError("reference audio is required")
    if not has_reference and payload:
        raise ValueError("unexpected reference audio payload")
    reference_text = str(request.get("referenceText") or "")
    if len(reference_text) > MAX_REFERENCE_TEXT_CHARS:
        raise ValueError("reference text is too long")


def _handle_synthesize(
    request: dict[str, Any],
    payload: bytes,
    stdout: BinaryIO,
    *,
    config_loader: Callable[[], dict[str, Any]],
) -> None:
    try:
        _validate_synthesis_request(request, payload)
        config = copy.deepcopy(config_loader())
        with TemporaryDirectory(prefix="lvb-remote-generation-") as temp_dir:
            temp_root = Path(temp_dir)
            output_root = temp_root / "output"
            output_root.mkdir(parents=True, exist_ok=True)
            config["referenceVoices"] = {}
            reference_voice: str | None = None
            if payload:
                reference_path = temp_root / "reference.wav"
                reference_path.write_bytes(payload)
                reference_item: dict[str, Any] = {
                    "label": REMOTE_REFERENCE_ID,
                    "referenceAudioPath": str(reference_path),
                    "language": "Japanese",
                    "source": "remote-request",
                }
                reference_text = str(request.get("referenceText") or "")
                if reference_text:
                    reference_text_path = temp_root / "reference.txt"
                    reference_text_path.write_text(reference_text, encoding="utf-8")
                    reference_item["referenceTextPath"] = str(reference_text_path)
                config["referenceVoices"] = {REMOTE_REFERENCE_ID: reference_item}
                reference_voice = REMOTE_REFERENCE_ID

            with redirect_stdout(sys.stderr):
                source_file, used_reference_audio = synthesize_irodori_direct(
                    raw_config=config,
                    model_config=_model_config(config),
                    output_dir=output_root,
                    text=str(request.get("text") or "").strip(),
                    request_id=str(request.get("requestId") or "") or None,
                    reference_voice=reference_voice,
                    voice_prompt=str(request.get("voicePrompt") or "").strip(),
                    profile_name=str(request.get("ttsProfile") or "").strip() or None,
                    live=bool(request.get("live")),
                )
            source_path = Path(source_file)
            wav_bytes = source_path.read_bytes()
            if len(wav_bytes) > MAX_GENERATED_WAV_BYTES:
                raise ValueError("generated audio exceeds limit")
            header = _response_base(request, ok=True, op="synthesize")
            header.update(
                usedReferenceAudio=bool(used_reference_audio),
                ttsProfile=str(request.get("ttsProfile") or "").strip(),
            )
            write_frame(stdout, header, wav_bytes)
    except BaseException as exc:
        header = _response_base(request, ok=False, op="synthesize")
        header.update(
            errorCode="generation_failed",
            error=_safe_error(exc, message="Irodori generation failed"),
        )
        write_frame(stdout, header)


def run_worker(
    stdin: BinaryIO,
    stdout: BinaryIO,
    *,
    config_loader: Callable[[], dict[str, Any]],
) -> int:
    while True:
        first = stdin.read(1)
        if not first:
            return 0
        try:
            request, payload = read_frame(
                _PrefixedStream(first, stdin),
                max_payload_bytes=MAX_REFERENCE_WAV_BYTES,
            )
        except RemoteGenerationProtocolError as exc:
            write_frame(
                stdout,
                {
                    "version": PROTOCOL_VERSION,
                    "ok": False,
                    "op": "protocol",
                    "requestId": "",
                    "errorCode": "protocol_error",
                    "error": _safe_error(exc, message="Invalid remote generation frame"),
                },
            )
            return 2

        op = str(request.get("op") or "")
        if op == "prepare":
            _handle_prepare(request, stdout, config_loader=config_loader)
        elif op == "synthesize":
            _handle_synthesize(request, payload, stdout, config_loader=config_loader)
        else:
            header = _response_base(request, ok=False, op=op)
            header.update(errorCode="unsupported_operation", error="Unsupported remote generation operation")
            write_frame(stdout, header)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preferred-cuda-device", default="")
    parser.add_argument(
        "--preferred-gpu-min-free-mib",
        type=int,
        default=DEFAULT_PREFERRED_GPU_MIN_FREE_MIB,
    )
    args = parser.parse_args()
    configure_cuda_visibility(
        preferred_device=str(args.preferred_cuda_device or ""),
        preferred_min_free_mib=max(0, int(args.preferred_gpu_min_free_mib)),
    )
    # Importing the normal server config loader is safe here: it does not start
    # the HTTP server or tray. Runtime diagnostics are redirected away from the
    # binary stdout protocol by the operation handlers above.
    from server import load_config

    return run_worker(sys.stdin.buffer, sys.stdout.buffer, config_loader=load_config)


if __name__ == "__main__":
    raise SystemExit(main())
