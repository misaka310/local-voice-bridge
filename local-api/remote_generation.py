from __future__ import annotations

import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remote_generation_protocol import (
    PROTOCOL_VERSION,
    RemoteGenerationProtocolError,
    read_frame,
    write_frame,
)


MAX_REFERENCE_WAV_BYTES = 32 * 1024 * 1024
MAX_GENERATED_WAV_BYTES = 64 * 1024 * 1024
_SAFE_REMOTE_ROOT = re.compile(r"^[A-Za-z]:[\\/][A-Za-z0-9 _./\\-]+$")
_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9_.-]+$")


class RemoteGenerationError(RuntimeError):
    pass


class _BackendResponseError(RemoteGenerationError):
    pass


@dataclass(frozen=True)
class RemoteGenerationConfig:
    ssh_alias: str
    remote_repo_root: str
    connect_timeout_seconds: float = 7.0


class RemoteGenerationClient:
    def __init__(
        self,
        config: RemoteGenerationConfig,
        *,
        output_dir: Path,
        paramiko_module: Any | None = None,
        ssh_config_path: Path | None = None,
        known_hosts_path: Path | None = None,
    ) -> None:
        self.config = config
        self.output_dir = Path(output_dir)
        self._paramiko_module = paramiko_module
        self._ssh_config_path = Path(ssh_config_path or (Path.home() / ".ssh" / "config"))
        self._known_hosts_path = Path(known_hosts_path or (Path.home() / ".ssh" / "known_hosts"))
        self._lock = threading.RLock()
        self._ssh_client: Any | None = None
        self._channel: Any | None = None
        self._stdin: Any | None = None
        self._stdout: Any | None = None
        self._prepared = False

    def _paramiko(self) -> Any:
        if self._paramiko_module is not None:
            return self._paramiko_module
        try:
            import paramiko
        except ImportError as exc:
            raise RemoteGenerationError("Remote generation SSH support is not installed") from exc
        self._paramiko_module = paramiko
        return paramiko

    def _normalized_remote_root(self) -> str:
        value = str(self.config.remote_repo_root or "").strip()
        if not _SAFE_REMOTE_ROOT.fullmatch(value) or ".." in Path(value.replace("\\", "/")).parts:
            raise RemoteGenerationError("Remote generation repository path is invalid")
        return value.replace("\\", "/").rstrip("/")

    def _ssh_options(self) -> dict[str, Any]:
        alias = str(self.config.ssh_alias or "").strip()
        if not alias or not _SAFE_ALIAS.fullmatch(alias):
            raise RemoteGenerationError("Remote generation SSH alias is invalid")
        if not self._ssh_config_path.is_file():
            raise RemoteGenerationError("Remote generation SSH config is missing")
        if not self._known_hosts_path.is_file():
            raise RemoteGenerationError("Remote generation known_hosts is missing")
        paramiko = self._paramiko()
        parser = paramiko.SSHConfig()
        try:
            with self._ssh_config_path.open("r", encoding="utf-8") as handle:
                parser.parse(handle)
            raw = parser.lookup(alias)
        except (OSError, UnicodeError, ValueError) as exc:
            raise RemoteGenerationError("Remote generation SSH config could not be read") from exc
        hostname = str(raw.get("hostname") or alias).strip()
        username = str(raw.get("user") or os.environ.get("USERNAME") or "").strip()
        try:
            port = int(raw.get("port") or 22)
        except (TypeError, ValueError) as exc:
            raise RemoteGenerationError("Remote generation SSH port is invalid") from exc
        identity_values = raw.get("identityfile") or []
        if isinstance(identity_values, str):
            identity_values = [identity_values]
        identities = [
            str(Path(os.path.expandvars(os.path.expanduser(str(value)))))
            for value in identity_values
            if str(value).strip()
        ]
        return {
            "alias": alias,
            "hostname": hostname,
            "username": username or None,
            "port": port,
            "key_filename": identities or None,
        }

    def prepare(self) -> dict[str, Any]:
        self._normalized_remote_root()
        options = self._ssh_options()
        self._prepared = True
        return {
            "backend": "remote_ssh",
            "sshAlias": options["alias"],
            "transport": "ssh-stdio",
        }

    def _worker_command(self) -> str:
        root = self._normalized_remote_root()
        python = f"{root}/local-api/.venv/Scripts/python.exe"
        worker = f"{root}/local-api/remote_generation_worker.py"
        return f'"{python}" -u "{worker}"'

    def _close_connection(self) -> None:
        channel = self._channel
        client = self._ssh_client
        self._stdin = None
        self._stdout = None
        self._channel = None
        self._ssh_client = None
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self._close_connection()

    def _connection_is_active(self) -> bool:
        client = self._ssh_client
        channel = self._channel
        if client is None or channel is None or bool(getattr(channel, "closed", False)):
            return False
        try:
            transport = client.get_transport()
            return bool(transport and transport.is_active() and transport.is_authenticated())
        except Exception:
            return False

    def _connect(self) -> None:
        if self._connection_is_active():
            return
        self._close_connection()
        options = self._ssh_options()
        paramiko = self._paramiko()
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.load_host_keys(str(self._known_hosts_path))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        timeout = max(1.0, float(self.config.connect_timeout_seconds))
        client.connect(
            hostname=options["hostname"],
            port=options["port"],
            username=options["username"],
            key_filename=options["key_filename"],
            allow_agent=True,
            look_for_keys=True,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        transport = client.get_transport()
        if transport is None or not transport.is_active() or not transport.is_authenticated():
            client.close()
            raise RemoteGenerationError("Remote generation SSH transport did not become active")
        transport.set_keepalive(15)
        channel = transport.open_session(timeout=10)
        channel.exec_command(self._worker_command())
        self._ssh_client = client
        self._channel = channel
        self._stdin = channel.makefile("wb")
        self._stdout = channel.makefile("rb")

    def _exchange_once(self, header: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        self._connect()
        assert self._stdin is not None
        assert self._stdout is not None
        write_frame(self._stdin, header, payload)
        response, response_payload = read_frame(self._stdout, max_payload_bytes=MAX_GENERATED_WAV_BYTES)
        if str(response.get("requestId") or "") != str(header.get("requestId") or ""):
            raise RemoteGenerationProtocolError("response request id mismatch")
        if str(response.get("op") or "") != str(header.get("op") or ""):
            raise RemoteGenerationProtocolError("response operation mismatch")
        if not bool(response.get("ok")):
            code = str(response.get("errorCode") or "backend_error")[:80]
            raise _BackendResponseError(f"Remote generation failed ({code})")
        return response, response_payload

    def _exchange(self, header: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        for attempt in range(2):
            try:
                return self._exchange_once(header, payload)
            except _BackendResponseError:
                raise
            except Exception as exc:
                self._close_connection()
                if attempt == 0:
                    continue
                raise RemoteGenerationError("Remote generation backend unavailable") from exc
        raise RemoteGenerationError("Remote generation backend unavailable")

    @staticmethod
    def _safe_filename_component(value: Any) -> str:
        raw = str(value or "").strip()
        cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)[:48]
        return cleaned or "request"

    def synthesize(
        self,
        payload: dict[str, Any],
        *,
        reference_audio: Path | None,
        reference_text: str = "",
    ) -> tuple[Path, str]:
        if not self._prepared:
            self.prepare()
        reference_bytes = b""
        if reference_audio is not None:
            reference_path = Path(reference_audio)
            if not reference_path.is_file():
                raise RemoteGenerationError("Selected reference audio is missing")
            if reference_path.stat().st_size > MAX_REFERENCE_WAV_BYTES:
                raise RemoteGenerationError("Selected reference audio exceeds remote generation limit")
            reference_bytes = reference_path.read_bytes()
        request_id = str(payload.get("requestId") or "") or uuid.uuid4().hex
        request = {
            "version": PROTOCOL_VERSION,
            "op": "synthesize",
            "requestId": request_id,
            "text": str(payload.get("text") or ""),
            "ttsProfile": str(payload.get("resolvedTtsProfile") or payload.get("ttsProfile") or ""),
            "live": bool(payload.get("live")),
            "voicePrompt": str(payload.get("voicePrompt") or payload.get("instruct") or ""),
            "referenceVoice": str(payload.get("referenceVoice") or payload.get("voiceId") or ""),
            "referenceText": str(reference_text or ""),
            "hasReference": bool(reference_bytes),
        }
        with self._lock:
            response, wav_bytes = self._exchange(request, reference_bytes)
        if not wav_bytes:
            raise RemoteGenerationError("Remote generation returned empty audio")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        token = self._safe_filename_component(request_id)
        destination = self.output_dir / f"chatgpt-remote-{token}-{uuid.uuid4().hex[:8]}.wav"
        temporary = destination.with_name(f".{destination.name}.tmp")
        try:
            temporary.write_bytes(wav_bytes)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        used_reference = "remote" if bool(response.get("usedReferenceAudio")) else ""
        return destination, used_reference
