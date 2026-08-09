from __future__ import annotations

import copy
import errno
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_API = ROOT / "local-api"
SERVER_PATH = LOCAL_API / "server.py"
if str(LOCAL_API) not in sys.path:
    sys.path.insert(0, str(LOCAL_API))


def load_server_module():
    fake_engine = types.ModuleType("irodori_engine")

    class FakeIrodoriError(RuntimeError):
        pass

    fake_engine.IrodoriError = FakeIrodoriError
    fake_engine.cache_hint = lambda: "test"
    fake_engine.prepare_irodori_direct = lambda **_: {"modelDevice": "test"}
    fake_engine.synthesize_irodori_direct = lambda **_: (_ for _ in ()).throw(AssertionError("not called"))
    sys.modules["irodori_engine"] = fake_engine

    spec = importlib.util.spec_from_file_location("local_voice_server_for_test", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load_server_module()


class FakeVoiceRuntime:
    def __init__(self, audio_path: Path) -> None:
        self.audio_path = Path(audio_path)
        self.synthesize_calls: list[dict[str, object]] = []
        self.replay_calls: list[dict[str, object]] = []
        self.stop_calls = 0

    def synthesize(self, payload, *, text, volume, play_local, timeout=None):
        self.synthesize_calls.append(
            {
                "payload": dict(payload),
                "text": text,
                "volume": volume,
                "playLocal": play_local,
                "timeout": timeout,
            }
        )
        return {
            "path": self.audio_path,
            "usedReferenceAudio": str(self.audio_path.parent / "private-reference.wav"),
            "playedLocally": bool(play_local),
            "playbackCompleted": bool(play_local),
            "stopped": False,
        }

    def replay(self, path=None, *, volume=0.6, text="", timeout=None):
        self.replay_calls.append({"path": path, "volume": volume, "text": text, "timeout": timeout})
        return {
            "path": self.audio_path,
            "usedReferenceAudio": str(self.audio_path.parent / "private-reference.wav"),
            "playedLocally": True,
            "playbackCompleted": True,
            "stopped": False,
        }

    def stop_playback(self):
        self.stop_calls += 1
        return {"ok": True, "stopping": True}

    def snapshot(self):
        return {
            "readiness": "ready",
            "ready": True,
            "detail": {"modelDevice": "cuda", "modelPrecision": "bf16"},
            "error": "",
            "repairRequired": False,
            "dependencies": {"sounddevice": True, "soundfile": True},
            "phase": "idle",
            "queueSize": 0,
            "currentText": "",
            "lastOperation": "synthesize:complete",
            "replayAvailable": True,
            "startedAt": 1.0,
        }


class LoopbackConfigTests(unittest.TestCase):
    def config(self, **changes):
        value = copy.deepcopy(server.DEFAULT_CONFIG)
        value.update(changes)
        return value

    def test_accepts_only_documented_loopback_hosts(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                normalized = server.normalize_config(self.config(host=host))
                self.assertEqual(normalized["host"], "127.0.0.1")

    def test_rejects_wildcard_lan_and_external_hosts(self):
        for host in ("0.0.0.0", "192.168.1.20", "10.0.0.5", "example.com"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(server.BridgeError, "ローカル専用"):
                    server.normalize_config(self.config(host=host))

    def test_accepts_loopback_public_base_url(self):
        normalized = server.normalize_config(
            self.config(publicBaseUrl="http://localhost:8717")
        )
        self.assertEqual(normalized["publicBaseUrl"], "http://127.0.0.1:8717")

    def test_rejects_non_loopback_public_base_url(self):
        for url in (
            "http://0.0.0.0:8717",
            "http://192.168.1.20:8717",
            "https://example.com",
            "http://example.com:8717",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(server.BridgeError, "ローカル専用"):
                    server.normalize_config(self.config(publicBaseUrl=url))

    def test_rejects_path_query_or_fragment(self):
        for url in (
            "http://127.0.0.1:8717/api",
            "http://127.0.0.1:8717/?token=value",
            "http://127.0.0.1:8717/#fragment",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(server.BridgeError, "ローカル専用"):
                    server.normalize_config(self.config(publicBaseUrl=url))

    def test_extension_package_version_reads_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "manifest.json"
            manifest.write_text(json.dumps({"version": "1.2.3"}), encoding="utf-8")
            self.assertEqual(server.extension_package_version(manifest), "1.2.3")

    def test_control_snapshot_requires_reload_for_old_or_unknown_extension(self):
        original = server.extension_package_version
        server.extension_package_version = lambda: "0.2.0"
        try:
            matching = server.enrich_control_snapshot(
                {"extension": {"connected": True, "loadedVersion": "0.2.0"}}
            )
            old = server.enrich_control_snapshot(
                {"extension": {"connected": True, "loadedVersion": "0.1.0"}}
            )
            unknown = server.enrich_control_snapshot(
                {"extension": {"connected": True, "loadedVersion": ""}}
            )
            disconnected = server.enrich_control_snapshot(
                {"extension": {"connected": False, "loadedVersion": ""}}
            )
        finally:
            server.extension_package_version = original

        self.assertFalse(matching["extension"]["updateRequired"])
        self.assertTrue(old["extension"]["updateRequired"])
        self.assertTrue(unknown["extension"]["updateRequired"])
        self.assertFalse(disconnected["extension"]["updateRequired"])
        self.assertEqual(old["extension"]["expectedVersion"], "0.2.0")

    def test_desktop_pet_id_rejects_path_like_values(self):
        for value in ("", "none", ".", "..", "../misaka", "voices/misaka", r"voices\misaka"):
            with self.subTest(value=value):
                self.assertEqual(server.normalize_desktop_pet_id(value), "placeholder")
        self.assertEqual(server.normalize_desktop_pet_id(" Misaka "), "misaka")

    def test_desktop_pet_list_includes_local_pets_without_reference_voices(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "pet"
            root.mkdir(parents=True)
            (root / "placeholder.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"/>',
                encoding="utf-8",
            )
            (root / "pet.json").write_text(
                json.dumps(
                    {
                        "id": "placeholder",
                        "displayName": "Placeholder",
                        "spritesheetPath": "placeholder.svg",
                        "columns": 1,
                        "rows": 1,
                        "frameWidth": 32,
                        "frameHeight": 32,
                    }
                ),
                encoding="utf-8",
            )
            local = root / "local" / "voices" / "standalone"
            local.mkdir(parents=True)
            (local / "sprite.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"/>',
                encoding="utf-8",
            )
            (local / "pet.json").write_text(
                json.dumps(
                    {
                        "id": "standalone",
                        "displayName": "Standalone Pet",
                        "spritesheetPath": "sprite.svg",
                        "columns": 1,
                        "rows": 1,
                        "frameWidth": 32,
                        "frameHeight": 32,
                    }
                ),
                encoding="utf-8",
            )

            pets = server.desktop_pet_list(root)

            self.assertIn({"id": "placeholder", "label": "Placeholder"}, pets)
            self.assertIn({"id": "standalone", "label": "Standalone Pet"}, pets)

    def test_desktop_pet_update_preserves_position_and_forces_visible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "desktop-pet-settings.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "selectedPetId": "placeholder",
                        "visible": False,
                        "x": 123,
                        "y": 456,
                        "alwaysOnTop": True,
                    }
                ),
                encoding="utf-8",
            )

            result = server.update_desktop_pet_settings("misaka", path)

            self.assertEqual(result["selectedPetId"], "misaka")
            self.assertTrue(result["visible"])
            self.assertEqual((result["x"], result["y"]), (123, 456))
            self.assertTrue(result["alwaysOnTop"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)

    def test_desktop_pet_update_recovers_from_corrupt_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "desktop-pet-settings.json"
            path.write_text("not-json", encoding="utf-8")

            result = server.update_desktop_pet_settings("asuka", path)

            self.assertEqual(result["version"], 1)
            self.assertEqual(result["selectedPetId"], "asuka")
            self.assertTrue(result["visible"])


    def test_concurrent_desktop_pet_updates_leave_valid_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "desktop-pet-settings.json"
            path.write_text(
                json.dumps({"version": 1, "x": 123, "y": 456, "alwaysOnTop": True}),
                encoding="utf-8",
            )
            pet_ids = ["misaka", "asuka", "placeholder"] * 8

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda pet_id: server.update_desktop_pet_settings(pet_id, path), pet_ids))

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(saved["selectedPetId"], set(pet_ids))
            self.assertTrue(saved["visible"])
            self.assertEqual((saved["x"], saved["y"]), (123, 456))
            self.assertTrue(saved["alwaysOnTop"])
            self.assertEqual(len(results), len(pet_ids))
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_desktop_pet_endpoint_updates_the_runtime_settings_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "desktop-pet-settings.json"
            previous_path = os.environ.get("LOCAL_VOICE_DESKTOP_PET_SETTINGS")
            original_load_config = server.load_config
            os.environ["LOCAL_VOICE_DESKTOP_PET_SETTINGS"] = str(settings_path)
            server.load_config = lambda: (_ for _ in ()).throw(AssertionError("not called"))
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{httpd.server_port}/v1/desktop-pet",
                    data=json.dumps({"petId": "misaka"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertTrue(payload["ok"])
                self.assertEqual(payload["selectedPetId"], "misaka")
                self.assertTrue(payload["visible"])
                saved = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["selectedPetId"], "misaka")
                self.assertTrue(saved["visible"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                server.load_config = original_load_config
                if previous_path is None:
                    os.environ.pop("LOCAL_VOICE_DESKTOP_PET_SETTINGS", None)
                else:
                    os.environ["LOCAL_VOICE_DESKTOP_PET_SETTINGS"] = previous_path


    def test_admin_shutdown_requires_the_per_process_nonce(self):
        control_nonce = server.uuid.uuid4().hex
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        setattr(httpd, "shutdown_token", control_nonce)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            unauthorized = urllib.request.Request(
                f"{base}/v1/admin/shutdown",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(unauthorized, timeout=5)
            self.assertEqual(error.exception.code, 403)
            self.assertTrue(thread.is_alive())

            authorized = urllib.request.Request(
                f"{base}/v1/admin/shutdown",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "X-Local-Voice-Token": control_nonce,
                },
                method="POST",
            )
            with urllib.request.urlopen(authorized, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["ok"])
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        finally:
            httpd.server_close()

    def test_post_boundary_rejects_normal_web_origins_non_json_and_oversized_bodies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = server.CONTROL_STATE
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                blocked_origin = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=json.dumps({"enabled": True}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://example.com",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(blocked_origin, timeout=5)
                self.assertEqual(error.exception.code, 403)
                self.assertFalse(server.CONTROL_STATE.snapshot()["settings"]["enabled"])

                non_json = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=b'{"enabled":true}',
                    headers={"Content-Type": "text/plain"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(non_json, timeout=5)
                self.assertEqual(error.exception.code, 415)

                oversized = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(server.MAX_POST_BODY_BYTES + 1),
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(oversized, timeout=5)
                self.assertEqual(error.exception.code, 413)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                server.CONTROL_STATE = original_store

    def test_request_host_boundary_rejects_non_loopback_host_for_all_methods(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = server.CONTROL_STATE
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                get_request = urllib.request.Request(
                    f"{base}/v1/browser-runtime",
                    headers={"Host": "review-rebind.example"},
                    method="GET",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(get_request, timeout=5)
                self.assertEqual(error.exception.code, 421)

                post_request = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=json.dumps({"enabled": True}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Host": "review-rebind.example",
                    },
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(post_request, timeout=5)
                self.assertEqual(error.exception.code, 421)
                self.assertFalse(server.CONTROL_STATE.snapshot()["settings"]["enabled"])

                options_request = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    headers={
                        "Host": "review-rebind.example",
                        "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                    },
                    method="OPTIONS",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(options_request, timeout=5)
                self.assertEqual(error.exception.code, 421)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                server.CONTROL_STATE = original_store

    def test_post_boundary_keeps_extension_and_native_clients_working(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = server.CONTROL_STATE
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                extension_request = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=json.dumps({"enabled": True}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(extension_request, timeout=5) as response:
                    extension_payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(extension_payload["settings"]["enabled"])

                native_request = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=json.dumps({"enabled": False}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(native_request, timeout=5) as response:
                    native_payload = json.loads(response.read().decode("utf-8"))
                self.assertFalse(native_payload["settings"]["enabled"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                server.CONTROL_STATE = original_store

    def test_control_panel_endpoints_share_settings_commands_and_extension_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = getattr(server, "CONTROL_STATE", None)
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                settings_request = urllib.request.Request(
                    f"{base}/v1/control-panel/settings",
                    data=json.dumps(
                        {
                            "enabled": True,
                            "voiceVolume": 0.25,
                            "referenceVoice": "asuka",
                            "initialized": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(settings_request, timeout=5) as response:
                    settings_payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(settings_payload["ok"])
                self.assertTrue(settings_payload["settings"]["enabled"])
                self.assertEqual(settings_payload["settings"]["referenceVoice"], "asuka")

                command_request = urllib.request.Request(
                    f"{base}/v1/control-panel/command",
                    data=json.dumps({"command": "next"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(command_request, timeout=5) as response:
                    command_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(command_payload["command"]["id"], 1)

                extension_request = urllib.request.Request(
                    f"{base}/v1/control-panel/state",
                    data=json.dumps(
                        {
                            "statusText": "Ready",
                            "currentText": "全タブから届いた返答です。",
                            "queueSize": 1,
                            "tabsCount": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(extension_request, timeout=5) as response:
                    extension_payload = json.loads(response.read().decode("utf-8"))
                self.assertTrue(extension_payload["ok"])

                with urllib.request.urlopen(f"{base}/v1/control-panel/poll?after=0", timeout=5) as response:
                    poll_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual([item["command"] for item in poll_payload["commands"]], ["next"])
                self.assertEqual(poll_payload["settings"]["voiceVolume"], 0.25)

                with urllib.request.urlopen(f"{base}/v1/control-panel", timeout=5) as response:
                    snapshot = json.loads(response.read().decode("utf-8"))
                self.assertEqual(snapshot["extension"]["currentText"], "全タブから届いた返答です。")
                self.assertEqual(snapshot["extension"]["tabsCount"], 2)
                self.assertIn("referenceVoices", snapshot)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                if original_store is not None:
                    server.CONTROL_STATE = original_store

    def test_browser_runtime_endpoint_persists_authoritative_tab_and_queue_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = getattr(server, "CONTROL_STATE", None)
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                payload = {
                    "tabs": [
                        {
                            "id": 101,
                            "title": "Tab A",
                            "url": "https://chatgpt.com/c/101",
                            "lastReadIndex": 0,
                            "lastAutoQueueSignature": "reply-1\u0000preview",
                            "lastAssistantMessage": {
                                "messageKey": "reply-1",
                                "chunks": ["最初です。", "続きです。"],
                                "capturedAt": 10,
                            },
                        }
                    ],
                    "selectedTabId": 101,
                    "uiOwnerTabId": 101,
                    "queue": [
                        {
                            "id": 4,
                            "mode": "next",
                            "tabId": 101,
                            "messageKey": "reply-1",
                            "chunkIndex": 1,
                            "chunkCount": 2,
                            "text": "続きです。",
                            "referenceVoice": "asuka",
                        }
                    ],
                    "seq": 5,
                }
                request = urllib.request.Request(
                    f"{base}/v1/browser-runtime",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    saved = json.loads(response.read().decode("utf-8"))
                self.assertEqual(saved["browserRuntime"]["queue"][0]["text"], "続きです。")

                with urllib.request.urlopen(f"{base}/v1/browser-runtime", timeout=5) as response:
                    restored = json.loads(response.read().decode("utf-8"))
                self.assertEqual(restored["browserRuntime"]["tabs"][0]["lastAssistantMessage"]["messageKey"], "reply-1")
                self.assertEqual(restored["browserRuntime"]["selectedTabId"], 101)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                if original_store is not None:
                    server.CONTROL_STATE = original_store

    def test_health_marks_outdated_connected_extension_reload_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "local-runtime.wav"
            audio_path.write_bytes(b"RIFF")
            runtime = FakeVoiceRuntime(audio_path)
            original_store = server.CONTROL_STATE
            original_extension_package_version = server.extension_package_version
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            server.CONTROL_STATE.update_extension_state(
                {"tabsCount": 2, "loadedVersion": "0.1.0"}
            )
            server.extension_package_version = lambda: "0.2.0"
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            setattr(httpd, "voice_runtime", runtime)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{httpd.server_port}/health", timeout=5
                ) as response:
                    health = json.loads(response.read().decode("utf-8"))

                self.assertFalse(health["readiness"]["ready"])
                self.assertEqual(
                    health["readiness"]["browserExtension"], "reload_required"
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                server.CONTROL_STATE = original_store
                server.extension_package_version = original_extension_package_version

    def test_local_voice_runtime_handles_speak_replay_stop_and_health(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "local-runtime.wav"
            audio_path.write_bytes(b"RIFF")
            runtime = FakeVoiceRuntime(audio_path)
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            setattr(httpd, "voice_runtime", runtime)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                speak = urllib.request.Request(
                    f"{base}/v1/speak",
                    data=json.dumps(
                        {
                            "text": "ローカル再生テスト",
                            "requestId": "runtime-test",
                            "playLocal": True,
                            "voiceVolume": 0.25,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(speak, timeout=5) as response:
                    spoken = json.loads(response.read().decode("utf-8"))
                self.assertTrue(spoken["playedLocally"])
                self.assertTrue(spoken["playbackCompleted"])
                self.assertEqual(spoken["usedReferenceAudio"], "applied")
                self.assertNotIn(temp_dir, json.dumps(spoken))
                self.assertEqual(runtime.synthesize_calls[0]["volume"], 0.25)
                self.assertTrue(runtime.synthesize_calls[0]["playLocal"])

                compatibility_speak = urllib.request.Request(
                    f"{base}/v1/speak",
                    data=json.dumps({"text": "互換クライアント"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(compatibility_speak, timeout=5) as response:
                    compatibility = json.loads(response.read().decode("utf-8"))
                self.assertFalse(compatibility["playedLocally"])
                self.assertFalse(runtime.synthesize_calls[1]["playLocal"])

                replay = urllib.request.Request(
                    f"{base}/v1/playback/replay",
                    data=json.dumps({"voiceVolume": 0.75, "text": "再生"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(replay, timeout=5) as response:
                    replayed = json.loads(response.read().decode("utf-8"))
                self.assertTrue(replayed["playedLocally"])
                self.assertEqual(replayed["usedReferenceAudio"], "applied")
                self.assertNotIn(temp_dir, json.dumps(replayed))
                self.assertEqual(runtime.replay_calls[0]["volume"], 0.75)

                stop = urllib.request.Request(
                    f"{base}/v1/playback/stop",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(stop, timeout=5) as response:
                    stopped = json.loads(response.read().decode("utf-8"))
                self.assertTrue(stopped["stopping"])
                self.assertEqual(runtime.stop_calls, 1)

                with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
                    health = json.loads(response.read().decode("utf-8"))
                self.assertEqual(health["voiceRuntime"]["readiness"], "ready")
                self.assertEqual(health["readiness"]["deviceOrModel"], "ready")
                self.assertEqual(health["readiness"]["dependencies"], "ready")
                self.assertFalse(health["pathsExposed"])
                self.assertNotIn("cacheHint", health)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)

    def test_control_panel_poll_and_ack_are_non_destructive_and_use_independent_cursors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = getattr(server, "CONTROL_STATE", None)
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                command = server.CONTROL_STATE.enqueue_command("next")
                event = server.CONTROL_STATE.enqueue_conversation_event("cancel_pending", {"sessionId": 2})
                poll_url = f"{base}/v1/control-panel/poll?consumer=panel&after=0&afterEvent=0&replayExisting=1"
                with urllib.request.urlopen(poll_url, timeout=5) as response:
                    first = json.loads(response.read().decode("utf-8"))
                with urllib.request.urlopen(poll_url, timeout=5) as response:
                    second = json.loads(response.read().decode("utf-8"))
                self.assertEqual(first["commands"], second["commands"])
                self.assertEqual(first["conversationEvents"], second["conversationEvents"])
                self.assertEqual(first["commands"][0]["id"], command["id"])
                self.assertEqual(first["conversationEvents"][0]["id"], event["id"])

                ack = urllib.request.Request(
                    f"{base}/v1/control-panel/ack",
                    data=json.dumps({"consumerId": "panel", "commandId": command["id"]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(ack, timeout=5) as response:
                    ack_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(ack_payload["commandId"], command["id"])
                self.assertNotIn("conversationEventId", ack_payload)

                with urllib.request.urlopen(poll_url, timeout=5) as response:
                    after_command_ack = json.loads(response.read().decode("utf-8"))
                self.assertEqual(after_command_ack["commands"], [])
                self.assertEqual([item["id"] for item in after_command_ack["conversationEvents"]], [event["id"]])

                bad_ack = urllib.request.Request(
                    f"{base}/v1/control-panel/ack",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(bad_ack, timeout=5)
                self.assertEqual(error.exception.code, 400)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                if original_store is not None:
                    server.CONTROL_STATE = original_store

    def test_json_response_swallows_normal_client_disconnect_without_retrying(self):
        class BrokenWriter:
            def write(self, _data):
                raise BrokenPipeError(errno.EPIPE, "broken pipe")

        class FakeHandler:
            def __init__(self):
                self.wfile = BrokenWriter()
                self.responses = 0

            def send_response(self, _status):
                self.responses += 1

            def send_header(self, _key, _value):
                pass

            def end_headers(self):
                pass

        handler = FakeHandler()
        self.assertFalse(server.json_response(handler, 200, {"ok": True}))
        self.assertEqual(handler.responses, 1)

    def test_conversation_state_and_transcript_event_endpoints_remain_pending_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_store = getattr(server, "CONTROL_STATE", None)
            server.CONTROL_STATE = server.ControlStateStore(Path(temp_dir) / "control-state.json")
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{httpd.server_port}"
            try:
                state_request = urllib.request.Request(
                    f"{base}/v1/conversation/state",
                    data=json.dumps(
                        {"phase": "recording", "statusText": "録音中", "sttDevice": "cuda", "sttModel": "small"}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(state_request, timeout=5) as response:
                    state_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(state_payload["conversation"]["phase"], "recording")

                event_request = urllib.request.Request(
                    f"{base}/v1/conversation/event",
                    data=json.dumps(
                        {
                            "type": "transcript",
                            "payload": {"sessionId": 3, "text": "テスト送信", "cancelGraceMs": 700},
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(event_request, timeout=5) as response:
                    event_payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(event_payload["event"]["type"], "transcript")

                poll_url = f"{base}/v1/control-panel/poll?consumer=legacy&after=0&afterEvent=0"
                with urllib.request.urlopen(poll_url, timeout=5) as response:
                    first_poll = json.loads(response.read().decode("utf-8"))
                self.assertEqual(first_poll["conversationEvents"][0]["payload"]["text"], "テスト送信")
                with urllib.request.urlopen(poll_url, timeout=5) as response:
                    second_poll = json.loads(response.read().decode("utf-8"))
                self.assertEqual(second_poll["conversationEvents"], first_poll["conversationEvents"])

                ack_request = urllib.request.Request(
                    f"{base}/v1/control-panel/ack",
                    data=json.dumps({"consumerId": "legacy", "conversationEventId": first_poll["conversationEvents"][0]["id"]}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(ack_request, timeout=5) as response:
                    self.assertEqual(json.loads(response.read().decode("utf-8"))["conversationEventId"], 1)
                with urllib.request.urlopen(poll_url, timeout=5) as response:
                    acknowledged_poll = json.loads(response.read().decode("utf-8"))
                self.assertEqual(acknowledged_poll["conversationEvents"], [])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                if original_store is not None:
                    server.CONTROL_STATE = original_store


    def test_full_outboxes_return_service_unavailable_json(self):
        class FullOutboxStore:
            def enqueue_command(self, _command):
                raise RuntimeError("command outbox is full")

            def enqueue_conversation_event(self, _event_type, _payload):
                raise RuntimeError("conversation event outbox is full")

            def acknowledge_commands(self, _command_id, *, consumer_id=None):
                raise OSError("ack state file unavailable")

            def update_browser_runtime(self, _payload):
                raise OSError("browser runtime state file unavailable")

        original_store = server.CONTROL_STATE
        server.CONTROL_STATE = FullOutboxStore()
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            requests = [
                (
                    urllib.request.Request(
                        f"{base}/v1/control-panel/command",
                        data=json.dumps({"command": "next"}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    "outbox is full",
                ),
                (
                    urllib.request.Request(
                        f"{base}/v1/conversation/event",
                        data=json.dumps({"type": "cancel_pending", "payload": {"sessionId": 1}}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    "outbox is full",
                ),
                (
                    urllib.request.Request(
                        f"{base}/v1/control-panel/ack",
                        data=json.dumps({"consumerId": "extension", "commandId": 1}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    "ack state file unavailable",
                ),
                (
                    urllib.request.Request(
                        f"{base}/v1/browser-runtime",
                        data=json.dumps({"tabs": []}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    "browser runtime state file unavailable",
                ),
            ]
            for request, expected_error in requests:
                with self.subTest(url=request.full_url):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, 503)
                    payload = json.loads(error.exception.read().decode("utf-8"))
                    self.assertFalse(payload["ok"])
                    self.assertIn(expected_error, payload["error"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            server.CONTROL_STATE = original_store


if __name__ == "__main__":
    unittest.main()
