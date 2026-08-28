# Windows Shell UX and Responsibility Design

## Goal

Make Local Voice Bridge behave like one Windows application rather than a collection of a launcher, scripts, browser-extension internals, and diagnostic routes. A normal user should start from `LocalVoiceBridge.exe`, understand readiness/recovery from the Windows UI, and never be told to use Task Manager or a visible terminal for ordinary recovery.

## Scope

This design implements the findings from the 2026-08-29 whole-product UX/responsibility review without changing the persisted control schema or the Auto/Next/Regen/Replay/Stop semantics.

### In scope

1. Remove the visible-console re-setup path from the tray application.
2. Add a normal Cancel path to the long-running setup GUI.
3. Fail early, before large package downloads, when the machine cannot satisfy the public NVIDIA/CUDA prerequisite.
4. Make the Windows panel the entry point for advanced settings by adding a `詳細設定` action that asks the connected extension to open its options page.
5. Clarify the visible `Ref` concept as `Voice`; the same stored reference ID may continue to select the matching desktop-pet asset. No API/storage migration is introduced.
6. Split Windows server supervision and Windows OS integration out of `tray_controller.py`, then add architecture gates so those responsibilities cannot drift back.
7. Synchronize README, startup/operation/architecture docs, and extension installation/update guidance with the implemented UI.

### Out of scope

- Moving advanced-setting storage from Chrome storage into the local API.
- Renaming the persisted `referenceVoice` / `Ref` API schema.
- Packaging the extension in the Chrome Web Store or bypassing Chrome's unpacked-extension first-install requirement.
- Rewriting the Qt control panel or the setup GUI into another UI framework.
- Changing TTS/STT models or preview/chunk semantics.

## User flow

### First setup

1. User starts `setup-voice-env.cmd` or `LocalVoiceBridge.exe --setup`.
2. Setup performs a lightweight preflight before large downloads: Python availability/version, free disk, Windows platform, NVIDIA GPU/driver visibility.
3. If preflight fails, setup stops before Torch/Irodori downloads and gives an actionable `LVB-SETUP-001` reason.
4. During setup, the user can press `キャンセル`. Cancellation terminates the setup process tree without opening a console and leaves resumable setup state/logs intact.
5. A later setup run revalidates completed stages and resumes safely.
6. Successful setup keeps `拡張機能の導入手順` available. First extension installation remains manual because the extension is unpacked.

### Normal use and recovery

1. User starts `LocalVoiceBridge.exe` / Windows Search shortcut.
2. The tray application, control panel, pet, local API and extension connectivity form one product surface.
3. `Exit and run environment setup` shuts the running app down and starts `LocalVoiceBridge.exe --setup` directly with `CREATE_NO_WINDOW`; it never opens `cmd.exe /k`.
4. The control panel exposes daily controls plus `詳細設定`. Clicking it sends an outbox command to the connected extension; the service worker acknowledges the command and calls `chrome.runtime.openOptionsPage()`.
5. Extension source updates use the control-panel reload route when supported. `chrome://extensions` / `brave://extensions` manual reload is documented only as the fallback when the running old extension explicitly cannot self-reload.

## UI decisions

### Windows control panel

Visible daily controls remain compact:

- `Voice` — backed by the existing `referenceVoice` value. Tooltip/help text explains that a matching desktop-pet asset follows the same selection.
- `Volume`
- `マイク会話`
- `Auto`
- `Next`
- `Regen`
- `Stop`
- `Replay`
- `詳細設定`

`詳細設定` is navigation, not duplicated configuration. It keeps one user entry point while allowing the extension options page to remain the owner of preview limits, STT model, cancel grace, and Live TTS profile.

### Setup cancellation

A dedicated `キャンセル` button is enabled only while a setup child process is active. It uses a hidden process-tree termination path. The UI reports `セットアップをキャンセルしました。再実行すると完了済み工程を再確認して続きから再開します。` and restores profile/start/close controls.

Closing the setup window while setup is active no longer tells the user to use Task Manager. It asks the user to use the Cancel button instead.

## Responsibility boundaries

### `local-api/server_supervisor.py`

Owns local server lifecycle and compatibility probing:

- `/health` probing and installation compatibility
- server/preflight command construction
- owned server process start/stop/restart
- controller/server logs needed for supervision
- generated-audio maintenance methods that are server-runtime maintenance

It does not own Qt widgets, registry/startup configuration, mutexes, setup/uninstall launching, or pet/mic UI wiring.

### `local-api/windows_integration.py`

Owns Windows application integration:

- per-user startup registration and legacy migration
- single-instance mutex
- opening local paths / native message boxes when needed by the shell
- no-window process spawning helpers used for restart/setup/uninstall

It does not own server-health state machines or Qt menu construction.

### `local-api/tray_controller.py`

Becomes the Windows composition/root UI layer:

- creates QApplication, tray menu, control panel, pet and microphone controller
- maps supervisor status into tray/pet presentation
- wires menu actions to focused services
- chooses post-exit action, delegating actual Windows process creation to `windows_integration.py`

Architecture CI caps this file and forbids reintroducing server probing, registry APIs, or raw Windows process-launch logic.

## Extension command boundary

`background-control-sync.js` owns external control-panel command delivery. Add `open_options` as a dedicated command alongside `reload_extension`:

1. receive `open_options`
2. call `chrome.runtime.openOptionsPage()`
3. flush any pending browser runtime state
4. acknowledge it through the same durable command cursor
5. update `lastCommandId`

If `openOptionsPage` fails, do not ACK the command; the normal durable retry contract remains in force.

## Early hardware preflight

The public runtime already requires Windows + NVIDIA GPU/CUDA. Before installing multi-GB CUDA packages, setup checks:

- current platform is Windows
- Python 3.10+ is resolvable
- required free disk is available
- `nvidia-smi.exe` is resolvable and `nvidia-smi --query-gpu=name --format=csv,noheader` returns at least one GPU

This is an early capability check, not a replacement for the later strict Torch/CUDA/Irodori runtime preflight. Driver/runtime incompatibilities still fail at `runtime-check` after dependencies exist.

## Error handling

- Every new normal-user launch remains console-free.
- Setup cancellation is an expected state, not a failure-code dialog.
- Failed `詳細設定` delivery leaves the durable command unacknowledged and the panel can surface normal extension-disconnected status.
- Manual extension-page instructions are fallback only.
- Existing setup stage codes remain stable; the early NVIDIA failure uses the existing `LVB-SETUP-001` preflight stage.

## Verification

Required tests:

1. tray re-setup uses the launcher `--setup` path and contains no `cmd.exe /k` path.
2. setup GUI source/behavior has a Cancel button, process-tree cancellation, state restoration, and no Task Manager instruction.
3. setup early preflight fails before package stages when NVIDIA capability is absent and passes a mocked NVIDIA capability check.
4. Windows panel sends `open_options`; background control sync ACKs then calls `chrome.runtime.openOptionsPage()` and does not fall through to playback commands.
5. control panel displays `Voice`, `Stop`, `マイク会話`, and `詳細設定`; docs list the same controls.
6. `tray_controller.py` imports focused Windows/server modules, stays below its new line cap, and architecture checks forbid responsibility leakage.
7. existing Python, background, mock E2E, public-tree, architecture and Windows GUI smoke gates remain green.
8. final Windows verification launches `LocalVoiceBridge.exe` without a terminal, confirms `/health` ready, opens the panel, and verifies a real `/v1/speak` result.
