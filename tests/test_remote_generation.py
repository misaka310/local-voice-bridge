from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))

from remote_generation import (  # noqa: E402
    RemoteGenerationClient,
    RemoteGenerationConfig,
    RemoteGenerationError,
)
from remote_generation_protocol import PROTOCOL_VERSION, read_frame, write_frame  # noqa: E402


class FakeSSHException(Exception):
    pass


class FakeSSHConfig:
    def __init__(self, options: dict[str, object]) -> None:
        self.options = options

    def parse(self, _handle) -> None:
        return None

    def lookup(self, _alias: str) -> dict[str, object]:
        return dict(self.options)


class FakeChannel:
    def __init__(self, response: bytes) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(response)
        self.command = ""
        self.closed = False

    def exec_command(self, command: str) -> None:
        self.command = command

    def makefile(self, mode: str):
        if "w" in mode:
            return self.stdin
        return self.stdout

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, channel: FakeChannel, *, active: bool = True) -> None:
        self.channel = channel
        self.active = active
        self.authenticated = active
        self.keepalive = None

    def is_active(self) -> bool:
        return self.active

    def is_authenticated(self) -> bool:
        return self.authenticated

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive = seconds

    def open_session(self, timeout: float | None = None) -> FakeChannel:
        if not self.active:
            raise FakeSSHException("transport closed")
        return self.channel


class FakeSSHClient:
    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport
        self.policy = None
        self.connect_calls: list[dict[str, object]] = []
        self.loaded_host_files: list[str] = []
        self.system_hosts_loaded = False
        self.closed = False

    def load_system_host_keys(self) -> None:
        self.system_hosts_loaded = True

    def load_host_keys(self, path: str) -> None:
        self.loaded_host_files.append(path)

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy

    def connect(self, **kwargs) -> None:
        self.connect_calls.append(dict(kwargs))

    def get_transport(self) -> FakeTransport:
        return self.transport

    def close(self) -> None:
        self.closed = True
        self.transport.active = False


class FakeRejectPolicy:
    pass


class FakeParamiko:
    SSHException = FakeSSHException
    RejectPolicy = FakeRejectPolicy

    def __init__(self, channels: list[FakeChannel], options: dict[str, object] | None = None) -> None:
        self.channels = list(channels)
        self.options = options or {"hostname": "192.168.0.66", "user": "organ", "port": "22"}
        self.clients: list[FakeSSHClient] = []

    def SSHConfig(self):
        return FakeSSHConfig(self.options)

    def SSHClient(self):
        if not self.channels:
            raise AssertionError("no fake channel left")
        client = FakeSSHClient(FakeTransport(self.channels.pop(0)))
        self.clients.append(client)
        return client


def framed_response(header: dict, payload: bytes = b"") -> bytes:
    stream = io.BytesIO()
    write_frame(stream, {"version": PROTOCOL_VERSION, **header}, payload)
    return stream.getvalue()


class RemoteGenerationClientTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path, Path]:
        ssh_config = root / "config"
        known_hosts = root / "known_hosts"
        output = root / "audio"
        ssh_config.write_text("Host desk\n", encoding="utf-8")
        known_hosts.write_text("desk ssh-ed25519 AAAATEST\n", encoding="utf-8")
        return ssh_config, known_hosts, output

    def test_prepare_validates_local_ssh_material_without_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ssh_config, known_hosts, output = self._paths(root)
            fake_paramiko = FakeParamiko([])
            client = RemoteGenerationClient(
                RemoteGenerationConfig("desk", "C:/00_dev/17_chatgpt-local-voice-bridge"),
                output_dir=output,
                paramiko_module=fake_paramiko,
                ssh_config_path=ssh_config,
                known_hosts_path=known_hosts,
            )

            detail = client.prepare()

            self.assertEqual(detail["backend"], "remote_ssh")
            self.assertEqual(detail["sshAlias"], "desk")
            self.assertEqual(fake_paramiko.clients, [])

    def test_synthesize_uses_reject_policy_and_returns_wav_to_local_output(self) -> None:
        response = framed_response(
            {
                "ok": True,
                "op": "synthesize",
                "requestId": "job-1",
                "usedReferenceAudio": True,
                "ttsProfile": "balanced",
            },
            b"RIFF-generated",
        )
        channel = FakeChannel(response)
        fake_paramiko = FakeParamiko([channel])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ssh_config, known_hosts, output = self._paths(root)
            reference = root / "voice.wav"
            reference.write_bytes(b"RIFF-reference")
            client = RemoteGenerationClient(
                RemoteGenerationConfig("desk", "C:/00_dev/17_chatgpt-local-voice-bridge"),
                output_dir=output,
                paramiko_module=fake_paramiko,
                ssh_config_path=ssh_config,
                known_hosts_path=known_hosts,
            )

            path, used_reference = client.synthesize(
                {
                    "requestId": "job-1",
                    "text": "hello",
                    "ttsProfile": "balanced",
                    "voicePrompt": "calm",
                    "live": False,
                    "referenceVoice": "asuka",
                },
                reference_audio=reference,
                reference_text="transcript",
            )

            self.assertEqual(path.read_bytes(), b"RIFF-generated")
            self.assertEqual(path.parent, output)
            self.assertTrue(used_reference)
            self.assertEqual(len(fake_paramiko.clients), 1)
            ssh_client = fake_paramiko.clients[0]
            self.assertIsInstance(ssh_client.policy, FakeRejectPolicy)
            self.assertTrue(ssh_client.system_hosts_loaded)
            self.assertEqual(ssh_client.loaded_host_files, [str(known_hosts)])
            self.assertEqual(ssh_client.connect_calls[0]["hostname"], "192.168.0.66")
            self.assertIn("/local-api/.venv/Scripts/python.exe", channel.command)
            self.assertIn("remote_generation_worker.py", channel.command)
            self.assertNotIn("py -3.11", channel.command)
            self.assertNotIn("8717", channel.command)
            channel.stdin.seek(0)
            request, request_payload = read_frame(channel.stdin, max_payload_bytes=32 * 1024 * 1024)
            self.assertEqual(request["op"], "synthesize")
            self.assertEqual(request["referenceVoice"], "asuka")
            self.assertEqual(request["referenceText"], "transcript")
            self.assertTrue(request["hasReference"])
            self.assertEqual(request_payload, b"RIFF-reference")

    def test_worker_command_requests_preferred_cuda_device_with_automatic_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ssh_config, known_hosts, output = self._paths(root)
            client = RemoteGenerationClient(
                RemoteGenerationConfig(
                    "desk",
                    "C:/00_dev/17_chatgpt-local-voice-bridge",
                    preferred_cuda_device="GPU-df2abfc7-2b62-e5ce-2738-f7b68d784457",
                    preferred_gpu_min_free_mib=8192,
                ),
                output_dir=output,
                paramiko_module=FakeParamiko([]),
                ssh_config_path=ssh_config,
                known_hosts_path=known_hosts,
            )

            command = client._worker_command()

            self.assertIn(
                "--preferred-cuda-device GPU-df2abfc7-2b62-e5ce-2738-f7b68d784457",
                command,
            )
            self.assertIn("--preferred-gpu-min-free-mib 8192", command)
            self.assertNotIn("CUDA_VISIBLE_DEVICES", command)

    def test_channel_loss_reconnects_once_and_next_response_succeeds(self) -> None:
        first = FakeChannel(b"")
        second = FakeChannel(
            framed_response(
                {"ok": True, "op": "synthesize", "requestId": "job-2", "usedReferenceAudio": False},
                b"RIFF-after-reconnect",
            )
        )
        fake_paramiko = FakeParamiko([first, second])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ssh_config, known_hosts, output = self._paths(root)
            client = RemoteGenerationClient(
                RemoteGenerationConfig("desk", "C:/00_dev/17_chatgpt-local-voice-bridge"),
                output_dir=output,
                paramiko_module=fake_paramiko,
                ssh_config_path=ssh_config,
                known_hosts_path=known_hosts,
            )

            path, used_reference = client.synthesize(
                {"requestId": "job-2", "text": "hello"},
                reference_audio=None,
            )

            self.assertEqual(path.read_bytes(), b"RIFF-after-reconnect")
            self.assertFalse(used_reference)
            self.assertEqual(len(fake_paramiko.clients), 2)
            self.assertTrue(fake_paramiko.clients[0].closed)

    def test_backend_error_does_not_leak_remote_paths(self) -> None:
        response = framed_response(
            {
                "ok": False,
                "op": "synthesize",
                "requestId": "job-3",
                "errorCode": "generation_failed",
                "error": "C:/00_dev/private/model.safetensors failed",
            }
        )
        fake_paramiko = FakeParamiko([FakeChannel(response)])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ssh_config, known_hosts, output = self._paths(root)
            client = RemoteGenerationClient(
                RemoteGenerationConfig("desk", "C:/00_dev/17_chatgpt-local-voice-bridge"),
                output_dir=output,
                paramiko_module=fake_paramiko,
                ssh_config_path=ssh_config,
                known_hosts_path=known_hosts,
            )

            with self.assertRaises(RemoteGenerationError) as raised:
                client.synthesize({"requestId": "job-3", "text": "hello"}, reference_audio=None)

            self.assertIn("generation_failed", str(raised.exception))
            self.assertNotIn("C:/00_dev", str(raised.exception))

    def test_rejects_unsafe_remote_repo_root_before_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ssh_config, known_hosts, output = self._paths(root)
            client = RemoteGenerationClient(
                RemoteGenerationConfig("desk", 'C:/safe" & whoami'),
                output_dir=output,
                paramiko_module=FakeParamiko([]),
                ssh_config_path=ssh_config,
                known_hosts_path=known_hosts,
            )

            with self.assertRaises(RemoteGenerationError):
                client.prepare()


if __name__ == "__main__":
    unittest.main()
