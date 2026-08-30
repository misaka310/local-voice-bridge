# UX Responsibility Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining Local Voice Bridge UX/responsibility gaps so Windows is the coherent daily/recovery surface, Auto and microphone states are independent, runtime settings have one owner, and multi-tab actions are understandable.

**Architecture:** Keep the current Local API / tray / extension controller split. Add one focused Windows advanced-settings dialog, make Local API authoritative for runtime settings, shrink browser options to browser-only preview settings, and expose existing tab-target metadata through the current external snapshot without increasing polling.

**Tech Stack:** Python 3.11, PySide6, Chrome/Brave Manifest V3 JavaScript, Node.js test runners, GitHub Actions Windows runner.

**Spec:** `docs/superpowers/specs/2026-08-30-ux-responsibility-finalization-design.md`

## Global Constraints

- Normal-user entry remains `LocalVoiceBridge.exe`.
- Normal recovery must not display a console or expose internal scripts.
- `enabled` remains persisted but means Auto only.
- `micConversationEnabled` is independent from Auto.
- Existing Auto/Next/Regen/Replay/Stop behavior and common-queue semantics remain unchanged.
- Existing loopback-only security boundaries remain unchanged.
- No new high-frequency tab polling or broadcast path may be added.
- Extension source updates still use the existing formal reload/version verification path.
- Every behavioral change starts with a failing regression test.

---

### Task 1: Independent Auto and microphone state

**Files:**
- Modify: `tests/test_control_panel.py`
- Modify: `local-api/control_panel.py`

**Interfaces:**
- Consumes: existing `ControlPanelClient.update_settings(payload)`.
- Produces: independent payloads `{enabled: bool}` and `{micConversationEnabled: bool}`.

- [ ] **Step 1: Write failing Qt tests**
  - Clicking Auto OFF while mic is ON must send only `{enabled: false}` and leave the mic button checked.
  - Clicking mic OFF/ON must send only `micConversationEnabled` and leave Auto unchanged.

- [ ] **Step 2: Run `python -m unittest tests.test_control_panel -v` and verify the new tests fail because the existing handlers cross-write the other setting.**

- [ ] **Step 3: Change `_on_auto_clicked` to update only `enabled`; change `_on_mic_clicked` to update only `micConversationEnabled`.**

- [ ] **Step 4: Re-run the focused tests and require PASS.**

---

### Task 2: Local API owns advanced runtime settings

**Files:**
- Modify: `tests/test_control_state.py`
- Modify: `local-api/state_normalization.py`
- Modify: `local-api/control_state.py`
- Modify: `tests/background-settings-core.test.js`
- Modify: `extension/background-settings-core.js`
- Modify: `tests/background-external-panel.test.js`
- Modify: `extension/background-control-sync.js`

**Interfaces:**
- Produces Local API setting `liveTtsProfile: "speed" | "balanced" | "bridge"`.
- `planExternalSettings()` mirrors `sttModel`, `cancelGraceMs`, and `liveTtsProfile` from Local API into Chrome runtime settings.
- First uninitialized Local API bootstrap may adopt existing browser runtime values once.

- [ ] **Step 1: Add failing Python tests for `liveTtsProfile` default, validation, update, persistence, and reload.**
- [ ] **Step 2: Add failing JS tests that authoritative external settings overwrite browser mirrors for STT/cancel/live profile.**
- [ ] **Step 3: Run the focused Python/Node tests and verify RED.**
- [ ] **Step 4: Add `normalize_live_tts_profile`, include the key in Local API defaults/normalization/update persistence.**
- [ ] **Step 5: Extend `planExternalSettings` and external synchronization so Local API-owned fields flow API -> browser after initialization. Remove the steady-state browser-options -> API push behavior.**
- [ ] **Step 6: Re-run focused tests and require GREEN.**

---

### Task 3: Windows advanced settings UI and browser-preview navigation

**Files:**
- Create: `local-api/advanced_settings_dialog.py`
- Create/Modify: `tests/test_advanced_settings_dialog.py`
- Modify: `local-api/control_panel.py`
- Modify: `tests/test_control_panel.py`
- Modify: `scripts/run-python-tests.js`
- Modify: `scripts/check-architecture.js`

**Interfaces:**
- `AdvancedSettingsDialog(client, open_browser_settings, parent=None)`.
- Edits `sttModel`, `cancelGraceMs`, `liveTtsProfile` via `client.update_settings`.
- Calls `open_browser_settings()` for browser preview settings.

- [ ] **Step 1: Write failing dialog tests for snapshot rendering, validated save payload, save failure staying open, and browser-preview navigation.**
- [ ] **Step 2: Run the focused dialog tests and verify RED because the module does not exist.**
- [ ] **Step 3: Implement the minimal dialog with STT model, cancel grace seconds, Live profile, Save/Cancel, and `ブラウザの読み上げ範囲設定`.**
- [ ] **Step 4: Change the panel `詳細設定` button to open the local dialog. The dialog's browser-preview action sends existing `open_options` only when the extension is connected.**
- [ ] **Step 5: Register the test module and add an architecture cap for the dialog; keep `control_panel.py` under its existing cap.**
- [ ] **Step 6: Re-run focused Python tests and architecture check; require GREEN.**

---

### Task 4: Shrink browser Options to browser-only settings

**Files:**
- Modify: `extension/options.html`
- Modify: `extension/options.js`
- Modify: `tests/options-settings.test.js` or the existing options test module that imports `options.js`.
- Modify: `extension/background-message-router.js`
- Modify: `extension/background.js`
- Modify: affected background router tests.

**Interfaces:**
- Options persists only `previewMaxLines` and `previewMaxChars` plus `settingsVersion` as needed for compatibility.
- `options-settings-updated` refreshes/broadcasts browser-local preview settings; it does not push Local API-owned runtime settings back to Local API.

- [ ] **Step 1: Add failing tests that options normalization/output does not own STT/cancel/live fields and HTML omits those controls.**
- [ ] **Step 2: Run options/background router tests and verify RED.**
- [ ] **Step 3: Remove the three runtime-owned controls from options HTML/JS while preserving preview settings and save feedback.**
- [ ] **Step 4: Replace `pushOptionSettings` message handling with browser-settings refresh/broadcast only.**
- [ ] **Step 5: Re-run focused tests and `npm run test:background`; require GREEN.**

---

### Task 5: Truthful recovery and no-script repair UX

**Files:**
- Modify: `tests/test_control_panel.py`
- Modify: `local-api/control_panel.py`
- Modify: `tests/test_tray_controller.py`
- Modify: `local-api/tray_controller.py`

**Interfaces:**
- `LocalVoiceControlPanel.repair_requested = Signal()`.
- `VoiceBridgeQtRuntime` connects the signal to `exit_and_run_setup`.

- [ ] **Step 1: Add failing tests proving disconnected extension does not offer/claim active self-reload, while connected update-required + support does.**
- [ ] **Step 2: Add failing tests proving runtime failure shows `環境を修復`, emits `repair_requested`, and does not mention `setup-voice-env.cmd`.**
- [ ] **Step 3: Add tray test proving the signal maps to the existing setup-after-exit path.**
- [ ] **Step 4: Run focused tests and verify RED.**
- [ ] **Step 5: Implement the distinct states and signal wiring without adding process code to the panel.**
- [ ] **Step 6: Re-run focused tests and require GREEN.**

---

### Task 6: Surface multi-tab scope and action target

**Files:**
- Modify: `tests/background-external-panel.test.js`
- Modify: `extension/background.js`
- Modify: `tests/test_control_panel.py`
- Modify: `local-api/control_panel.py`

**Interfaces:**
- External state adds `autoScopeTabs`, `manualTargetTabId`, `manualTargetTitle`, `playbackSourceTabId`, `playbackSourceTitle`.

- [ ] **Step 1: Add failing background tests for selected/manual target and current/last playback source titles.**
- [ ] **Step 2: Add failing panel test for a compact line such as `Auto: 全3タブ · 操作対象: Tab B · 再生元: Tab A`.**
- [ ] **Step 3: Run focused tests and verify RED.**
- [ ] **Step 4: Derive labels from existing `tabs`, `selectedTabId`, `currentItem`, and `lastPlayedItem` only.**
- [ ] **Step 5: Render the context line without changing poll intervals.**
- [ ] **Step 6: Re-run focused tests and require GREEN.**

---

### Task 7: Documentation, full CI, merge, and activation

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/operation.md`
- Modify: `docs/startup.md`
- Modify: `extension/INSTALL.md`
- Modify: `docs/SPEC.md` acceptance checkboxes only after evidence exists.

- [ ] **Step 1: Synchronize user-facing copy with independent Auto/mic states, Windows advanced settings, recovery states, and multi-tab context.**
- [ ] **Step 2: Run/observe the PR `npm run test:ci` equivalent in GitHub Actions and require success on the exact head SHA.**
- [ ] **Step 3: Review the PR diff for scope and responsibility leakage; fix any findings with new regression tests.**
- [ ] **Step 4: Merge only after checks pass.**
- [ ] **Step 5: Verify the default branch contains the merge and its post-merge CI is green.**
- [ ] **Step 6: Refresh the Windows checkout to final main; because extension files changed, run the repository formal extension reload path and verify loaded/expected version equality and reconnection.**
- [ ] **Step 7: Run the existing isolated Windows/browser smoke path for the changed user flow, then run the Git Completion Gate/finalize path.**
