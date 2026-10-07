# Remote Generation Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Local Voice Bridge UI, settings, character/reference assets, tab state, favicon and playback on DESK2 while running only Irodori GPU synthesis on DESK over the existing restricted SSH path.

**Architecture:** DESK2 serves its own loopback `127.0.0.1:8717` and keeps the existing `VoiceRuntime` playback/queue behavior. In `remote_ssh` mode, `VoiceRuntime.synthesize_fn` uses an in-process Paramiko SSH client to a stdio generation worker on DESK; each job sends only the selected reference WAV/text and returns generated WAV bytes. DESK never starts the Local Voice Bridge tray or port 8717.

**Tech Stack:** Python 3.11, Paramiko 3.5.1, PySide6, sounddevice/soundfile, existing Irodori v3 runtime, Windows OpenSSH/SSH config, unittest, Node/Playwright existing CI.

**Spec:** `docs/plans/2026-10-08-desk2-frontend-desk-generation-backend.md`

## Global Constraints

- DESK2 owns UI, settings, character/reference assets, browser integration, generated-audio storage and playback.
- DESK owns NVIDIA/CUDA, Irodori runtime/model/cache and generation worker only.
- DESK must not run Local Voice Bridge Startup, tray, or a port-8717 listener.
- Do not persist the character/reference library on DESK; send only the selected reference asset per job.
- DESK2 must not silently fall back to AMD/iGPU/local Irodori generation.
- No new LAN/Tailscale/HTTP listening port; transport is the existing SSH trust only.
- DESK firewall remains TCP/22 restricted to DESK2 `192.168.0.12`; do not broaden it.
- Do not use VBS, keyboard/mouse injection, or a hidden `ssh.exe` child process; SSH runs in-process via Paramiko.
- Unknown SSH host keys are rejected.
- Existing single-PC local-generation mode remains supported.

## Review Focus

- DESK offline at frontend startup: DESK2 API/UI must stay usable and the next generation request must fail as backend-unavailable, not kill 8717.
- SSH drops during a synthesis: current job fails cleanly; the next job reconnects without extension reload.
- Malformed/oversized worker frame: reject it without unbounded allocation or writes outside bounded temp/output directories.
- Reference voice exists only on DESK2: generation must use it successfully without leaving a character directory or reference WAV on DESK.
- Remote mode on a machine with no CUDA: frontend preflight must pass without importing/installing local Irodori/PyTorch, while local mode still requires its current CUDA preflight.

---

### Task 1: Add a bounded stdio generation protocol

**Files:**
- Create: `local-api/remote_generation_protocol.py`
- Create: `tests/test_remote_generation_protocol.py`

**Interfaces:**
- Produces: `PROTOCOL_VERSION = 1`
- Produces: `write_frame(stream: BinaryIO, header: dict[str, Any], payload: bytes = b"") -> None`
- Produces: `read_frame(stream: BinaryIO, *, max_payload_bytes: int) -> tuple[dict[str, Any], bytes]`
- Limits: header <= 1 MiB; reference payload <= 32 MiB; generated WAV <= 64 MiB.
- Frame format: 4-byte big-endian JSON-header length, UTF-8 JSON header, then exactly `payloadBytes` raw bytes.

- [ ] **Step 1: Write failing protocol tests**

Cover round-trip framing, truncated header/payload, invalid JSON/version, 1 MiB header limit, 32 MiB request payload limit and 64 MiB response payload limit.

- [ ] **Step 2: Run the protocol tests and confirm RED**

Run: `C:/00_dev/17_chatgpt-local-voice-bridge/local-api/.venv/Scripts/python.exe -m unittest tests.test_remote_generation_protocol`
Expected: FAIL because `remote_generation_protocol` does not exist.

- [ ] **Step 3: Implement the protocol module**

Use only the Python standard library. Never trust `payloadBytes` before checking the caller-supplied maximum. Require `version == 1` on every frame.

- [ ] **Step 4: Run the protocol tests and confirm GREEN**

Run the same command; expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat(remote-generation): add bounded stdio protocol`

---

### Task 2: Add the DESK Irodori generation worker

**Files:**
- Create: `local-api/remote_generation_worker.py`
- Create: `tests/test_remote_generation_worker.py`
- Modify: `local-api/irodori_engine.py` only if a small public helper is needed to avoid depending on a private symbol.

**Interfaces:**
- Consumes: Task 1 framing.
- Produces: `run_worker(stdin: BinaryIO, stdout: BinaryIO, *, config_loader: Callable[[], dict[str, Any]]) -> int`.
- Request ops: `prepare`, `synthesize`.
- `prepare` loads/validates the existing DESK Irodori runtime and returns device/precision metadata.
- `synthesize` accepts text/profile/live/voicePrompt/reference metadata plus optional reference WAV payload.
- Worker loops until EOF so one SSH channel can reuse the loaded Irodori model.

- [ ] **Step 1: Write failing worker tests**

Mock `prepare_irodori_direct` / `synthesize_irodori_direct`. Assert `prepare` returns bounded metadata; reference-WAV synthesis creates a per-request temp asset, injects only a synthetic `remote-request` reference entry, returns WAV bytes, and removes all temp reference/output files on success and failure.

- [ ] **Step 2: Run worker tests and confirm RED**

Run: `...python.exe -m unittest tests.test_remote_generation_worker`
Expected: FAIL because the worker does not exist.

- [ ] **Step 3: Implement the worker**

Use `TemporaryDirectory(prefix="lvb-remote-generation-")`. Never use a character ID as a filesystem path. Pass the temp reference WAV to existing Irodori via a request-local `referenceVoices` entry. Do not start `server.py`, tray code or any listener.

- [ ] **Step 4: Run worker tests and confirm GREEN**

Expected: PASS and temp directories empty after every case.

- [ ] **Step 5: Commit**

Commit message: `feat(remote-generation): add headless Irodori worker`

---

### Task 3: Add the DESK2 in-process SSH generation client

**Files:**
- Create: `local-api/remote_generation.py`
- Create: `tests/test_remote_generation.py`
- Modify: `local-api/voice_service.py`

**Interfaces:**
- Consumes: Task 1 protocol and Task 2 worker.
- Produces: `RemoteGenerationConfig` with `ssh_alias: str`, `remote_repo_root: str`, `connect_timeout_seconds: float = 7.0`.
- Produces: `RemoteGenerationClient.prepare() -> dict[str, Any]` that validates local SSH config/host-key material but does not require DESK to be online.
- Produces: `RemoteGenerationClient.synthesize(payload: dict[str, Any], *, reference_audio: Path | None, reference_text: str = "") -> tuple[Path, str]`.
- Client uses Paramiko `SSHConfig`, system/user known_hosts and `RejectPolicy`; it reuses one authenticated transport/channel and reconnects once after transport failure.
- Worker command is built from configured `remoteRepoRoot` plus fixed repo-relative `local-api/remote_generation_worker.py`; arbitrary command text is not accepted from config.

- [ ] **Step 1: Write failing SSH-client tests**

Use fake Paramiko transport/channel objects. Cover unknown-host rejection configuration, prepare without live connection, successful synthesis, local output-file creation, one reconnect after channel loss, bounded backend-unavailable error, and no secret/path leakage in errors.

- [ ] **Step 2: Add a reference-asset resolver test in `tests/test_voice_runtime.py` or a focused `tests/test_voice_service.py`**

Assert a selected `referenceVoice` is resolved from DESK2 `reference/voices/<id>/voice.wav` before crossing the remote boundary, and a missing asset fails locally.

- [ ] **Step 3: Implement `remote_generation.py` and the reference resolver**

Returned WAV bytes are written only under the existing DESK2 `audioOutputDir`; validate response size before writing. `usedReferenceAudio` is true only when a supplied reference WAV was actually sent and acknowledged.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `...python.exe -m unittest tests.test_remote_generation tests.test_voice_runtime`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat(remote-generation): add DESK2 SSH client`

---

### Task 4: Select local vs remote generation inside the existing VoiceRuntime

**Files:**
- Modify: `local-api/server.py`
- Modify: `local-api/voice_service.py`
- Modify: `local-api/server_supervisor.py`
- Create: `local-api/scripts/preflight_frontend.py`
- Modify: `local-api/config.example.json`
- Modify: `local-api/config.local.example.json`
- Test: `tests/test_server_loopback.py`
- Test: `tests/test_tray_controller.py`
- Test: `tests/test_runtime_readiness.py`

**Interfaces:**
- Config: `generationBackend` is exactly `local` or `remote_ssh`; default is `local`.
- Config: `remoteGeneration.sshAlias` and `remoteGeneration.remoteRepoRoot` are required only for `remote_ssh`.
- `build_voice_runtime()` preserves the existing local Irodori path for `local` and injects the Task 3 remote synthesize function for `remote_ssh`.
- Remote `prepare_fn` performs local frontend validation only; backend network connection remains lazy so DESK offline cannot prevent 8717 from starting.
- `server_supervisor.preflight_command()` selects `preflight_irodori.py --strict-cuda --quick` for local mode and `preflight_frontend.py` for remote mode.

- [ ] **Step 1: Write failing backend-selection tests**

Assert local remains the default; remote mode starts without CUDA/model availability; malformed remote config fails with a clear configuration error; DESK offline does not make supervisor startup fail.

- [ ] **Step 2: Write failing readiness tests**

After a remote synthesis failure, API health/control state remains available and generation reports a backend-specific error. A later successful request clears the active generation error without extension reload.

- [ ] **Step 3: Implement backend selection and frontend preflight**

Do not change loopback host validation. Do not add a listener. Do not silently fall back from `remote_ssh` to `local`.

- [ ] **Step 4: Run focused tests**

Run: `...python.exe -m unittest tests.test_server_loopback tests.test_tray_controller tests.test_runtime_readiness`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat(remote-generation): route VoiceRuntime generation to SSH backend`

---

### Task 5: Add a frontend-only setup profile for DESK2

**Files:**
- Create: `local-api/requirements-frontend.txt`
- Modify: `scripts/setup/setup-engine.ps1`
- Modify: `scripts/setup/setup-gui.ps1`
- Modify: `tests/test_setup_user_flow.py`
- Modify: `tests/test_preflight_versions.py`

**Interfaces:**
- New setup profile id: `frontend`.
- Frontend requirements contain exactly: `PySide6==6.11.2`, `soundfile==0.14.0`, `sounddevice>=0.5.6,<0.6`, `numpy==2.4.6`, `paramiko==3.5.1` plus their normal transitive dependencies.
- `frontend` skips CUDA PyTorch, TorchAudio, TorchCodec, Irodori runtime and model download stages.
- Existing `reading`, `stt`, and `dev` profiles keep their current behavior.

- [ ] **Step 1: Write failing setup-profile tests**

Assert frontend does not execute torch/Irodori/model-cache stages, installs frontend requirements, still builds `LocalVoiceBridge.exe`/Start Menu integration, and local reading setup remains unchanged.

- [ ] **Step 2: Run setup tests and confirm RED**

Run: `...python.exe -m unittest tests.test_setup_user_flow tests.test_preflight_versions`
Expected: FAIL because `frontend` is unknown.

- [ ] **Step 3: Implement frontend profile and requirements**

Expose it as a remote-generation frontend option without DESK/DESK2-specific wording in public product UI.

- [ ] **Step 4: Run setup tests and confirm GREEN**

Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `feat(setup): add remote-generation frontend profile`

---

### Task 6: Update architecture/docs and protect the no-new-port boundary

**Files:**
- Modify: `docs/SPEC.md`
- Modify: `ARCHITECTURE.md`
- Modify: `README.md`
- Modify: `docs/setup.md`
- Modify: `docs/startup.md`
- Modify: `docs/operation.md`
- Modify: `scripts/check-runtime-boundaries.js`
- Modify: `tests/test_tray_controller.py`

**Interfaces:**
- Public docs describe generic frontend/remote-generation roles; environment-specific DESK/DESK2 values remain in the design/deployment evidence, not general user instructions.
- Runtime-boundary check permits the SSH client/worker modules but still rejects any new production listener/localhost port.

- [ ] **Step 1: Add/adjust contract tests for docs and runtime boundaries**

Assert remote mode keeps loopback 8717 frontend-owned, worker owns no listener, and docs state reference assets remain frontend-owned.

- [ ] **Step 2: Update spec/architecture/user docs**

Document local and remote modes, offline-backend behavior, frontend-only setup, and no-LAN-port rule.

- [ ] **Step 3: Run architecture/public checks**

Run: `npm run check:architecture`
Run: `npm run check:public`
Expected: PASS.

- [ ] **Step 4: Commit**

Commit message: `docs(remote-generation): document frontend/backend split`

---

### Task 7: Full verification, merge, and two-PC migration

**Files / state:**
- Repository delivery through feature branch + PR; no direct public-main push.
- DESK2 local config: `generationBackend=remote_ssh`, SSH alias `desk`, remote repo root matching the installed DESK checkout.
- DESK2 old `LocalVoiceBridgeRemoteClient` process/startup registration removed only after new path passes functional verification.
- DESK Local Voice Bridge Startup/tray/8717 remain absent.

**Interfaces:**
- Any persistent Startup/Run/task/configuration mutation uses the configuration-change audit workflow and records its `change_id`.
- Firewall should require no mutation; verify the existing `OpenSSH-Server-In-TCP` remote address remains `192.168.0.12`.

- [ ] **Step 1: Run full repository verification**

Run: `npm run test:ci`
Expected: all Python, Node, architecture, runtime-boundary and mock E2E checks PASS.

- [ ] **Step 2: Push a feature branch, open PR, wait for GitHub CI / Windows GUI smoke / Repo Launch Doctor**

Expected: all required checks success before merge.

- [ ] **Step 3: Fast-forward canonical DESK and DESK2 checkouts to merged `origin/main`**

Verify both are clean.

- [ ] **Step 4: Install/repair DESK2 frontend profile and configure remote mode**

Use the audited configuration-change path for persistent config. Do not install Irodori/CUDA/model assets on DESK2.

- [ ] **Step 5: Start DESK2 Local Voice Bridge and verify frontend ownership**

Assert DESK2 `127.0.0.1:8717/health` responds when DESK Local Voice Bridge tray/server is stopped. Verify extension reconnect/version match, tab registry and favicon path remain functional.

- [ ] **Step 6: Run a real reference-voice generation**

Choose a reference voice that exists on DESK2. Before generation assert the corresponding character/reference directory does not exist on DESK. Generate through DESK GPU, receive WAV on DESK2, pass quality checks and play locally. Recheck DESK has no persistent copy afterward.

- [ ] **Step 7: Verify failure/recovery**

Temporarily make the generation backend unavailable without stopping DESK2 Local API; assert generation-specific failure. Restore backend and generate again without extension reload.

- [ ] **Step 8: Retire the old whole-API proxy**

After the new path passes, remove/disable the old `LocalVoiceBridgeRemoteClient` auto-start/process through the audited configuration workflow. Verify port 8717 on DESK2 is owned by the DESK2 Local Voice Bridge process.

- [ ] **Step 9: Final machine-state verification**

DESK: no Local Voice Bridge Startup link, no tray process, no 8717 listener; sshd remains TCP/22 with firewall remote address `192.168.0.12` only.
DESK2: frontend Local Voice Bridge starts normally; no old RemoteClient listener/process; repo clean and synced.

- [ ] **Step 10: Finalize workspace and report evidence**

Run the repository completion gate and report merged SHA, CI status, machine-state checks, configuration change IDs, and whether rollback material is recovery-ready.
