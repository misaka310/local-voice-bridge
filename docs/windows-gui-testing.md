# Windows GUI smoke testing

The normal CI workflow verifies source behavior with headless and offscreen tests. It does not prove that a user can open the packaged Windows tray menu or activate its controls.

`npm run test:gui:windows` builds and launches the real `LocalVoiceBridge.exe` entrypoint. The smoke test uses Windows UI Automation selectors and accessible control names. It does not use fixed screen coordinates.

## What it verifies

- the packaged launcher starts and exposes one operable notification-area UI;
- the expected tray menu items exist and are enabled;
- the desktop pet is visible, responsive, and returns from a moved position through `Bring Desktop Pet Back`;
- a second launch exits without creating another controller while the original tray remains operable;
- the Local Voice panel can be shown, remains responsive, and can be hidden;
- Restart dispatches and the tray remains operable;
- Exit removes the controller UI;
- the application can start and exit again after the first shutdown.

The menu contract also checks the presence of Logs, generated-audio, reference-voice, Windows-startup, and setup actions. Destructive setup execution and Windows startup registry changes are intentionally outside this smoke test.

## GitHub Actions

The public repository runs `.github/workflows/windows-gui-smoke.yml` on GitHub-hosted `windows-latest` for pull requests and manual `workflow_dispatch` runs. No self-hosted runner, central VM workflow, repository variable, or runner label is required.

The workflow checks out a fresh Windows runner, installs Node.js and Python, builds the packaged launcher, and operates the real notification-area UI. Hosted-Windows compatibility handling uses accessible Qt controls and verifies that duplicate launch processes exit while the original controller remains the only instance.

## Local execution

The same contract can be run manually from an interactive Windows desktop:

```powershell
npm run test:gui:windows
```

Do not run it while another copy from the same checkout is already running. The test opens the tray menu, desktop pet, and Local Voice panel while it runs.

## Output

Successful runs print one compact `PASS` line per scenario. A failure exits non-zero and stores the JSON result plus a desktop screenshot under:

```text
test-results/windows-gui-smoke/
```

GitHub Actions uploads that directory only when the GUI smoke fails.
