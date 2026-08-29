# UX Responsibility Finalization Design

## Goal

Close the remaining whole-product UX and responsibility gaps without undoing the existing module split. The user should experience Local Voice Bridge as one Windows application: daily controls and runtime recovery stay in Windows, browser-specific behavior stays in the extension, and each visible control maps to one independent state.

## Decisions

### 1. Auto and microphone conversation are independent

`enabled` continues to exist for backward compatibility but is defined only as Auto speech enabled. `micConversationEnabled` is independent. Runtime conflict suppression during microphone phases remains temporary and does not mutate either stored setting.

### 2. Windows/runtime settings are Local API-owned

Local API persists and normalizes `referenceVoice`, `voiceVolume`, `micConversationEnabled`, `sttModel`, `cancelGraceMs`, and new `liveTtsProfile`. The extension keeps mirrored copies only because content scripts and the MV3 worker need them locally. On synchronization, Local API wins for these keys after initialization.

The browser options page owns only `previewMaxLines` and `previewMaxChars`. Windows advanced settings owns STT model, send-cancel grace, and Live TTS profile. The Windows advanced settings surface may navigate to browser preview settings, but it does not duplicate preview controls.

### 3. Windows advanced settings remains a focused local UI

Add a small Qt `AdvancedSettingsDialog` in a focused module rather than growing `control_panel.py`. It edits the three Local API-owned advanced settings through the existing control-panel client. The main panel `詳細設定` opens this dialog locally. A secondary `ブラウザの読み上げ範囲設定` action sends `open_options` through the existing durable extension command only when the extension is connected.

### 4. Recovery states are truthful

The panel treats these as distinct states:

- local API unavailable: application starting/unavailable; mutating controls disabled.
- extension disconnected: `ChatGPTとの接続待ち`; no claim that self-reload is running.
- extension connected, update required, self-reload supported: show `拡張機能を再読み込み`.
- extension connected, update required, old extension cannot self-reload: show the existing manual browser fallback copy.
- runtime repair required: show `環境を修復`; emit a local UI intent signal. `tray_controller.py` maps that signal to the existing no-console `LocalVoiceBridge.exe --setup` path.

No new browser automation or hidden public API is introduced.

### 5. Multi-tab target information is surfaced from existing state

The extension already knows registered tab titles, selected/manual target, current playback item, and last played item. `externalStateSnapshot()` adds compact fields derived from those existing objects:

- `autoScopeTabs`: registered tab count.
- `manualTargetTabId` / `manualTargetTitle`: the target `Next` / `Regen` would use without a sender tab.
- `playbackSourceTabId` / `playbackSourceTitle`: current item, otherwise last played item.

The Windows panel renders one compact context line. No additional polling cadence or all-tab broadcast is introduced.

## Responsibility boundaries

### `local-api/state_normalization.py`
Owns normalization/defaults for Local API-owned settings, including `liveTtsProfile`.

### `local-api/control_state.py`
Persists the Local API-owned values; remains a coordinator.

### `local-api/advanced_settings_dialog.py`
Owns the Windows advanced-settings widgets and user input validation only. It calls the existing control-panel client; it does not own persistence, browser messaging, setup launching, or server supervision.

### `local-api/control_panel.py`
Owns daily controls, status/context display, and high-level local UI signals (`repair_requested`). It opens the advanced dialog but does not duplicate its fields.

### `local-api/tray_controller.py`
Wires `repair_requested` to the existing `exit_and_run_setup` composition action. It remains the composition root and does not regain raw process-launch responsibility.

### `extension/options.*`
Own only preview line/character limits and save those to Chrome storage.

### `extension/background-settings-core.js`
Keeps a mirror of Local API-owned settings for browser runtime use. `planExternalSettings` accepts the Local API values as authoritative.

### `extension/background-control-sync.js`
Pulls Local API-owned settings into the browser mirror. It no longer pushes STT/cancel/live settings from browser options into Local API.

### `extension/background.js`
Adds derived target labels to the existing external snapshot only.

## Migration

No key is deleted. Existing users may have `sttModel`, `cancelGraceMs`, and `liveTtsProfile` in Chrome storage. On first synchronization after this change:

1. If Local API state is already initialized, Local API wins.
2. If Local API state is not initialized, the first initialization payload may adopt the existing browser values once, preserving current user choices.
3. From then on, changes to these settings occur through the Windows advanced dialog and are mirrored back to Chrome by normal external synchronization.

This preserves compatibility while establishing one owner.

## Error handling

- A failed advanced-settings save leaves the dialog open and shows a local error; it does not optimistically close.
- A disconnected extension disables the browser-preview navigation action but does not disable Windows runtime settings.
- A failed setup-repair launch follows existing `exit_and_run_setup` error handling.
- Missing/invalid `liveTtsProfile` normalizes to `speed` for live conversation.

## Verification

1. RED/GREEN Qt tests prove Auto/mic independence, repair signal, advanced-settings save, and disconnected/update-state behavior.
2. RED/GREEN Python state tests prove `liveTtsProfile` persistence and normalization.
3. RED/GREEN background tests prove Local API authoritative sync and target/source labels.
4. Options tests prove STT/cancel/live fields are absent and preview settings still persist.
5. Architecture checks keep the new dialog focused and prevent responsibility from returning to `control_panel.py` or `tray_controller.py`.
6. Full `npm run test:ci` must pass on the PR.
7. After merge, the normal Windows application path must be refreshed and the existing extension reload/version check used if extension sources changed.
