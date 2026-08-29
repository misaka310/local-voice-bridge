# Windows Shell UX and Responsibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Local Voice Bridge present one coherent Windows-first setup, recovery, settings-navigation, and runtime surface while splitting Windows/server responsibilities out of `tray_controller.py`.

**Architecture:** Keep the existing local API and persisted setting schema stable. Improve the shell at its current boundaries: setup remains WinForms + PowerShell, advanced settings remain extension-owned but are opened from the Windows panel, server supervision moves to `server_supervisor.py`, Windows OS integration moves to `windows_integration.py`, and `tray_controller.py` becomes composition/UI wiring.

**Tech Stack:** Python 3.11, PySide6, PowerShell 5.1/7-compatible WinForms, Chrome/Brave Manifest V3 JavaScript, Node.js test runners.

**Spec:** `docs/superpowers/specs/2026-08-29-windows-shell-ux-responsibility-design.md`

## Global Constraints

- Normal-user entry is `LocalVoiceBridge.exe`; do not add another normal startup path.
- Normal setup/recovery/restart flows must not display a console or terminal.
- Persisted/API keys such as `referenceVoice` remain unchanged.
- Auto/Next/Regen/Replay/Stop behavior and common-queue semantics remain unchanged.
- First unpacked-extension installation may require the browser extensions page; source-update reload must prefer the control-panel reload path.
- Existing loopback-only API/security boundaries remain unchanged.
- Do not modify or depend on canonical uncommitted `docs/SPEC.md` work.
- Every behavioral change starts with a failing regression test and ends with relevant tests green.

---

### Task 1: Setup preflight and cancellation UX

**Files:**
- Modify: `scripts/setup/setup-engine.ps1`
- Modify: `scripts/setup/setup-gui.ps1`
- Modify: `tests/test_tray_controller.py`
- Create/Modify: `tests/test_setup_user_flow.py`
- Modify: `scripts/run-python-tests.js`

**Interfaces:**
- Produces: `Test-EarlyNvidiaCapability -> bool` in `setup-engine.ps1`.
- Produces: `Stop-SetupProcessTree([System.Diagnostics.Process])` in `setup-gui.ps1`.
- Consumes: existing `Invoke-SetupStage`, setup progress JSONL, resumable stage state.

- [ ] **Step 1: Write failing tests for early NVIDIA preflight**

Add Python source-contract tests that run the setup engine in a temporary fixture with package/model stages stubbed. The failure case must make NVIDIA capability unavailable and assert exit is non-zero at `LVB-SETUP-001` before any `torch` stage event. The success case stubs an NVIDIA GPU name and asserts execution reaches the next stage.

- [ ] **Step 2: Run the focused setup tests and confirm RED**

Run:

```bat
python -m unittest tests.test_setup_user_flow -v
```

Expected: failures because no early NVIDIA check exists.

- [ ] **Step 3: Implement lightweight NVIDIA capability check**

In `setup-engine.ps1`, add:

```powershell
function Test-EarlyNvidiaCapability {
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($null -eq $nvidiaSmi) { return $false }
    try {
        $output = & $nvidiaSmi.Source --query-gpu=name --format=csv,noheader 2>$null
        return ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($output -join "`n")))
    } catch {
        return $false
    }
}
```

Call it inside the existing `preflight` stage after disk/Python checks. On failure throw:

```text
NVIDIA GPUまたはNVIDIAドライバーを確認できません。Local Voice Bridgeの実音声機能にはNVIDIA GPU/CUDAが必要です。
```

Keep the later strict Torch/Irodori CUDA preflight unchanged.

- [ ] **Step 4: Write failing tests for setup cancellation**

Add tests asserting `setup-gui.ps1` contains a `キャンセル` button, enables it only while a child is active, terminates the process tree through a no-window path, restores Start/profile/advanced/Close controls, reports resumability, and contains no `タスクマネージャーで中止` instruction.

- [ ] **Step 5: Run cancellation tests and confirm RED**

Run:

```bat
python -m unittest tests.test_setup_user_flow -v
```

Expected: cancellation assertions fail against the current GUI.

- [ ] **Step 6: Implement setup cancellation**

Add `$cancelButton` beside the setup/start controls. Add:

```powershell
function Stop-SetupProcessTree {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process -or $Process.HasExited) { return }
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = "taskkill.exe"
    $startInfo.Arguments = "/PID $($Process.Id) /T /F"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $killer = [System.Diagnostics.Process]::Start($startInfo)
    $killer.WaitForExit(10000) | Out-Null
}
```

When Cancel is clicked, stop the tree, stop the timer, dispose the process after it exits, restore the controls, set status to:

```text
セットアップをキャンセルしました。再実行すると完了済み工程を再確認して続きから再開します。
```

The FormClosing handler while active must cancel closing and tell the user `キャンセル`を押してください, not Task Manager.

- [ ] **Step 7: Run focused tests GREEN**

```bat
python -m unittest tests.test_setup_user_flow tests.test_tray_controller -v
```

Expected: PASS.

- [ ] **Step 8: Register the new Python test module in CI**

Add `tests.test_setup_user_flow` to `scripts/run-python-tests.js` and run:

```bat
npm run test:python
```

Expected: all Python CI modules PASS.

- [ ] **Step 9: Commit Task 1**

```bat
git add scripts/setup/setup-engine.ps1 scripts/setup/setup-gui.ps1 tests/test_setup_user_flow.py tests/test_tray_controller.py scripts/run-python-tests.js
git commit -m "fix: improve setup recovery ux"
```

---

### Task 2: Make Windows panel the advanced-settings entry point

**Files:**
- Modify: `local-api/control_panel.py`
- Modify: `extension/background-control-sync.js`
- Modify: `tests/test_control_panel.py`
- Modify: `tests/background-external-panel.test.js`
- Modify: `tests/background-control-sync.test.js` if present; otherwise extend `tests/background-external-panel.test.js`

**Interfaces:**
- Produces: control-panel command string `open_options`.
- Consumes: existing `/v1/control-panel/command` durable outbox.
- Extension action: `chrome.runtime.openOptionsPage()`.

- [ ] **Step 1: Write failing Windows-panel tests**

Assert the panel has visible `Voice`, `マイク会話`, `Auto`, `Next`, `Regen`, `Stop`, `Replay`, and `詳細設定`. Assert the reference combo tooltip explains that the matching desktop pet follows the selected Voice. Assert clicking `詳細設定` sends command `open_options` through the existing client.

- [ ] **Step 2: Run focused panel tests RED**

```bat
python -m unittest tests.test_control_panel -v
```

Expected: missing Voice label/details button assertions fail.

- [ ] **Step 3: Implement panel navigation**

In `LocalVoiceControlPanel._build_ui`:

- change the visible `Ref` label text to `Voice`; keep object names and stored values unchanged.
- set a tooltip such as `参照音声を選択します。同じIDのペット素材がある場合はペットも連動します。` on label/combo.
- add a `詳細設定` button below/beside daily controls.
- connect it to `self._send_command("open_options")`.
- disable it when extension settings are unavailable, using the same connectivity/update gating as Auto/Voice/Volume.

- [ ] **Step 4: Write failing background command test**

Build a control-sync fixture whose `chrome.runtime.openOptionsPage` records calls. Feed one `open_options` durable command. Assert one options-page call, one ACK, `lastCommandId` advancement, and no call to `executeUiCommand`. Add a rejection case where `openOptionsPage()` rejects and assert no ACK occurs.

- [ ] **Step 5: Run background test RED**

```bat
node --test tests/background-external-panel.test.js
```

Expected: `open_options` falls through to `executeUiCommand` and fails.

- [ ] **Step 6: Implement `open_options` in `background-control-sync.js`**

Before the generic UI-command branch:

```javascript
if (command === 'open_options') {
  await chrome.runtime.openOptionsPage();
  await deps.flushBrowserRuntimeState();
  await acknowledge(effectiveSettings, consumerId, { commandId });
  lastCommandId = commandId;
  return;
}
```

Do not ACK if `openOptionsPage()` throws.

- [ ] **Step 7: Run Task 2 tests GREEN**

```bat
python -m unittest tests.test_control_panel -v
node --test tests/background-external-panel.test.js
npm run test:background
```

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bat
git add local-api/control_panel.py extension/background-control-sync.js tests/test_control_panel.py tests/background-external-panel.test.js
git commit -m "feat: open advanced settings from windows panel"
```

---

### Task 3: Extract local server supervision from `tray_controller.py`

**Files:**
- Create: `local-api/server_supervisor.py`
- Modify: `local-api/tray_controller.py`
- Modify: `tests/test_tray_controller_processes.py`
- Modify: `tests/test_tray_controller.py`
- Modify: `scripts/check-architecture.js`

**Interfaces:**
- Produces class `VoiceBridgeController` with the same public methods currently consumed by `VoiceBridgeQtRuntime`: `status`, `stop_requested`, `set_status_callback`, `start_monitor`, `restart_async`, `prepare_application_restart`, `clear_generated_audio_files`, `open_controller_log`, `open_audio_folder`, `open_reference_folder`, `shutdown`.
- Produces helpers `compatible_health_payload`, `probe_health`, `port_is_open`, `same_installation_health`, `request_same_installation_shutdown` in `server_supervisor.py`.
- `tray_controller.py` imports the class/helpers instead of owning their implementation.

- [ ] **Step 1: Write architecture tests that require `server_supervisor.py`**

Extend `scripts/check-architecture.js` to require the file and forbid `class VoiceBridgeController` plus raw server-health probing in `tray_controller.py`.

- [ ] **Step 2: Run architecture check RED**

```bat
npm run check:architecture
```

Expected: missing module / leakage failures.

- [ ] **Step 3: Move server-supervision code without changing behavior**

Move health probing, owned-server lifecycle, preflight/server command construction, controller/server log maintenance, and `VoiceBridgeController` into `local-api/server_supervisor.py`. Pass path/constants explicitly where needed or keep shared immutable path constants in the new module. Do not duplicate implementations.

Keep `tray_controller.py` importing `VoiceBridgeController` and only wiring it to Qt/runtime actions.

- [ ] **Step 4: Preserve process tests against the new module**

Update imports in `tests/test_tray_controller_processes.py` and relevant `tests/test_tray_controller.py` patches so the same scenarios still test ownership, health compatibility, restart and shutdown.

- [ ] **Step 5: Run focused process/architecture tests GREEN**

```bat
python -m unittest tests.test_tray_controller_processes tests.test_tray_controller -v
npm run check:architecture
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bat
git add local-api/server_supervisor.py local-api/tray_controller.py tests/test_tray_controller_processes.py tests/test_tray_controller.py scripts/check-architecture.js
git commit -m "refactor: extract voice server supervisor"
```

---

### Task 4: Extract Windows integration and fix re-setup launching

**Files:**
- Create: `local-api/windows_integration.py`
- Modify: `local-api/tray_controller.py`
- Modify: `tests/test_tray_controller.py`
- Modify: `tests/test_windows_gui_smoke_script.py`
- Modify: `scripts/check-architecture.js`

**Interfaces:**
- Produces startup APIs: `is_startup_enabled()`, `set_startup_enabled(enabled: bool)`, `migrate_legacy_startup()`.
- Produces single-instance APIs: `acquire_single_instance()`, `release_single_instance()`.
- Produces shell helpers: `open_path(path)`, `show_message(title, message, error=False)`.
- Produces no-window post-exit launch helpers: `launch_application(exe, cwd)`, `launch_setup(exe, cwd)`, `launch_uninstall(script, cwd)`.

- [ ] **Step 1: Write failing tests for no-console re-setup and module boundary**

Assert the tray path contains no `cmd.exe`, `/k`, or `setup-voice-env.cmd` post-exit command construction. Assert `windows_integration.launch_setup` launches `[LocalVoiceBridge.exe, "--setup"]` with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`.

Extend architecture checks to require `windows_integration.py`, cap `tray_controller.py` at 650 lines, and forbid `winreg`, `CreateMutex`, `subprocess.Popen(` and `cmd.exe` in `tray_controller.py`.

- [ ] **Step 2: Run focused tests/architecture RED**

```bat
python -m unittest tests.test_tray_controller tests.test_windows_gui_smoke_script -v
npm run check:architecture
```

Expected: current tray implementation violates the new boundary and console-free setup requirement.

- [ ] **Step 3: Move Windows integration code**

Move startup registry/legacy migration, mutex ownership, path opening, native message-box helper and post-exit process spawning to `windows_integration.py`.

Implement setup launch as:

```python
subprocess.Popen(
    [str(launcher_exe), "--setup"],
    cwd=app_root,
    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
)
```

Keep uninstall launching PowerShell hidden/no-window. Keep restart launching the EXE directly.

- [ ] **Step 4: Rewire `tray_controller.py` as composition**

Import the focused functions. Replace `_launch_application_after_exit`, `_launch_setup`, `_launch_uninstall` raw process code with focused helper calls. Keep menu construction and post-exit intent selection in the Qt runtime.

- [ ] **Step 5: Run focused tests and architecture GREEN**

```bat
python -m unittest tests.test_tray_controller tests.test_windows_gui_smoke_script tests.test_windows_process_identity -v
npm run check:architecture
```

Expected: PASS and `tray_controller.py <= 650` lines.

- [ ] **Step 6: Commit Task 4**

```bat
git add local-api/windows_integration.py local-api/tray_controller.py tests/test_tray_controller.py tests/test_windows_gui_smoke_script.py scripts/check-architecture.js
git commit -m "refactor: isolate windows shell integration"
```

---

### Task 5: Synchronize user documentation and control vocabulary

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/setup.md`
- Modify: `docs/startup.md`
- Modify: `docs/operation.md`
- Modify: `extension/INSTALL.md`
- Modify: `tests/test_tray_controller.py`
- Modify: `tests/options-page.test.js` only if copy assertions require it

**Interfaces:**
- No runtime interface changes. Documentation must describe the already-implemented UI.

- [ ] **Step 1: Add failing documentation/source-contract assertions**

Assert public docs list the actual daily controls `Voice`, `Volume`, `マイク会話`, `Auto`, `Next`, `Regen`, `Stop`, `Replay`, `詳細設定`. Assert `extension/INSTALL.md` says source-update reload should use the Windows panel when available and browser extension-page reload is fallback only. Assert setup docs mention early NVIDIA preflight and Cancel.

- [ ] **Step 2: Run documentation contract tests RED**

```bat
python -m unittest tests.test_tray_controller -v
```

Expected: stale documentation assertions fail.

- [ ] **Step 3: Update docs**

Keep first unpacked-extension installation instructions intact. For updates, document:

1. use `拡張機能を再読み込み` in the Windows panel when shown;
2. wait for tab-count/reconnection recovery;
3. use `chrome://extensions` / `brave://extensions` only when the panel explicitly says the loaded old extension cannot self-reload.

Update setup/startup/operation/architecture control lists and describe `Voice` as the visible name of the existing reference-voice selection with matching pet linkage. Document `詳細設定` as opening extension-owned advanced settings from the Windows panel.

- [ ] **Step 4: Run focused docs/public tests GREEN**

```bat
python -m unittest tests.test_tray_controller -v
npm run check:public
npm run check:architecture
```

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bat
git add README.md ARCHITECTURE.md docs/setup.md docs/startup.md docs/operation.md extension/INSTALL.md tests/test_tray_controller.py
git commit -m "docs: align windows voice bridge user flow"
```

---

### Task 6: Whole-branch verification, review and delivery

**Files:**
- No new feature files expected; only review fixes if findings are confirmed.

**Interfaces:**
- Consumes all Task 1-5 behavior and documentation.

- [ ] **Step 1: Run full local verification**

```bat
npm run test:python
npm run test:background
npm run test:e2e:mock
npm run check:public
npm run check:architecture
npm run test:launcher
python -m unittest discover -s tests -p "test_*.py"
```

On Windows, also run the existing GUI smoke command used by `.github/workflows/windows-gui-smoke.yml` if dependencies are installed.

Expected: all PASS.

- [ ] **Step 2: Run no-visible-terminal/pre-commit policy and diff check**

Run the repository/shared terminal audit used by pre-commit and `git diff --check`. Expected: PASS.

- [ ] **Step 3: Request an independent whole-branch code review**

Review `origin/main..HEAD` with the most capable independent reviewer available. The reviewer must check setup cancellation/process-tree behavior, early preflight ordering, durable `open_options` ACK semantics, Windows process visibility, tray responsibility leakage, and documentation/runtime consistency.

- [ ] **Step 4: Fix only confirmed findings and rerun affected + full gates**

Use the receiving-code-review workflow: reproduce/inspect each finding before changing code. Rerun the focused tests for every accepted fix, then repeat Step 1 gates.

- [ ] **Step 5: Push feature branch and open PR**

Push the isolated branch, open a PR targeting `main`, and wait for public readiness, mock E2E, and Windows GUI smoke checks. Fix any CI-only failure by reproducing its actual cause; do not weaken tests.

- [ ] **Step 6: Merge after all required checks are green**

Merge through the repository's normal PR workflow. Record the merge SHA.

- [ ] **Step 7: Synchronize canonical checkout without touching unrelated `docs/SPEC.md` local work**

Only fast-forward canonical `main` if the merge does not touch `docs/SPEC.md`. Verify the unrelated canonical change remains present and unstaged.

- [ ] **Step 8: Final Windows user-path verification**

Rebuild `LocalVoiceBridge.exe`, run `--self-test`, launch the EXE through the no-window detached path, confirm `/health` reports `ready=true`, confirm the panel opens and exposes the intended control set, and send one real `/v1/speak` request with `playLocal=false`. Confirm the returned WAV URL is HTTP 200 and non-empty.

- [ ] **Step 9: Final completion gate**

Run the workspace completion policy when available. Completion requires merged main CI green plus the local Windows user-path evidence above.
