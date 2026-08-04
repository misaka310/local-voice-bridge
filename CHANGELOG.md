# Changelog

## Unreleased

- Changed normal Auto from stable streaming-preview playback to completion-gated playback, requiring stable final Copy controls for the same response turn, revoking transient completion candidates when generation resumes, and persisting the completion reason for diagnosis.

- Added GPT Live-style microphone conversation with persisted pre-send submission ownership, fail-closed assistant binding, stable-sentence streaming, local playback, input/Enter/Regen/navigation interruption, and restart invalidation without changing normal Auto, Next, Regen, or Replay recovery.
- Split TTS generation and playback workers, added `speed`/`balanced`/`bridge` Irodori profiles, Suguha reference-latent reuse, cross-process Windows GPU arbitration with STT priority, and strict CUDA STT without automatic CPU fallback.
- Added generated-WAV quality gates, privacy-safe structured Live event logs, a 17-item Live verification gate, and real GPU/STT/Suguha performance evidence kept outside the public tree.

- Added a safe Windows-panel action that acknowledges a pending extension update before calling `chrome.runtime.reload()`, preventing reload loops and keeping the first unsupported upgrade as an explicit one-time manual step.

- Pinned the required `hf-xet==1.5.1` runtime dependency so strict startup preflight succeeds after a clean environment setup.
- Allowed the documented Irodori `sentencepiece<0.2` metadata conflict while still enforcing the security-fixed `sentencepiece==0.2.1` runtime baseline.
- Fixed background-tab completion markers to use the live Chrome tab state and react immediately when response generation ends.
- Isolated real Irodori E2E runs with per-test state files, authenticated shutdown, and ownership checks so tests cannot stop or overwrite the normal Local Voice Bridge runtime.
- Split persistent settings, browser runtime state, durable ACK delivery, HTTP I/O, runtime readiness, and browser restore rules into focused modules, and added a CI architecture gate that blocks duplicated responsibilities and monolith growth.
- Preserved the reference voice selected when each playback item was queued, distinguished an explicit `Ref=none` choice from a legacy empty value, rejected output when the local API could not prove that the selected reference voice was applied, and exposed the applied reference in playback status.
- Made control commands and pending microphone transcript delivery durable across API and extension service-worker restarts, using per-consumer acknowledgements, stable delivery IDs, bounded local persistence, and duplicate-insertion protection.
- Moved synthesis and playback into one local runtime worker, prepared Irodori during server startup, persisted tab/latest-response/queue state, and exposed structured loading, ready, failed, and repair-required status.
- Updated SentencePiece to 0.2.1 to resolve the reported heap-overflow vulnerability.
- Reconnected already-open ChatGPT tabs automatically after the local Voice Bridge API or unpacked extension restarts, injecting the current content scripts when needed and restoring tab and latest-response state without auto-reading old replies.
- Moved the microphone shortcut and STT status into the microphone button to reduce the Windows panel height.
- Bounded generated audio to the newest 1,000 files, 1 GiB, and 14 days, pruning the oldest files at startup and after synthesis while preserving the current response.
- Rotated both controller and server logs at 2 MiB with two backups and suppressed routine successful HTTP access logs.
- Made full application restart safely stop a residual API only when it belongs to the same repository installation, using a local per-process control token.
- Added tray actions to clear generated audio and uninstall Windows startup/shortcuts while preserving reference voices, settings, models, and the repository.
- Added a strict runtime dependency audit with verified versions and pinned Irodori, DAC-VAE, and SilentCipher Git commits.
- Fixed the Windows GUI smoke runner and verified launcher, tray menu, desktop pet recovery, single-instance behavior, panel responsiveness, full restart, exit, and relaunch.

- Added the standard Chrome / Brave options page for configurable reading limits, STT model, and pre-send cancellation grace, while keeping microphone enablement and playback controls in the compact Windows panel.
- Changed `Restart Voice Bridge` to restart the tray application itself, so updated panel code is reloaded instead of leaving the old single-instance process in memory.
- Pinned `transformers` to the security-fixed 5.5.0 release, pinned `huggingface-hub` to the verified 1.23.0 release, and pinned the verified Irodori source commit.
- Made Irodori v3 direct the supported local TTS path while preserving the preview-only Auto UX.
- Kept Auto from reading replies that were already visible before it was enabled.
- Preserved the `Next`, `Regen`, and `Replay` controls and added mock E2E coverage for their network behavior.
- Added a GPU-free Chromium demo that uses the real extension code and a shared mock voice API.
- Added loopback-only API enforcement, automated boundary tests, and `SECURITY.md`.
- Added a concise public README, an explicit environment matrix, limitations, and a lightweight visual demo.
- Added a reproducible public-tree check for private files, generated files, broken documentation links, and media limits.
- Fixed repeated FFmpeg path registration and synchronized the PowerShell startup and smoke scripts with Irodori v3.
- Replaced the normal VBS startup path with a small Windows launcher EXE while keeping the old VBS file only as a compatibility forwarder.
- Consolidated daily operation into the Chrome / Brave Local Voice panel and removed the redundant Voice, Tab, and Pet fields.
- Linked the desktop pet directly to Ref, including safe migration of legacy browser pet settings and placeholder fallback.
- Removed the in-page Chrome pet implementation and limited the single Windows desktop pet to display and left-drag movement.
- Reduced the tray to service management and added regression coverage for panel collapse, active-tab ownership, pet interactions, launcher self-test, and loopback-only operation.
- Fixed Auto so complete short replies, including replies under 20 characters, are read after the stability delay.
- Stopped real E2E startup failures from leaving orphaned local API processes on port 8717.
- Registered `Local Voice Bridge` in the current user's Windows Start menu during setup so it can be launched from search.
- Kept periodic split-view tab heartbeats from moving the Local Voice panel back and forth between panes.
- Excluded transient assistant statuses such as `思考中` and `Thinking` from Auto speech.
- Moved Local Voice controls from the ChatGPT page to one Windows always-on-top panel while preserving the global all-tab Auto queue.
- Added desktop-pet double-click and tray actions to show or hide the Windows Local Voice panel.
- Added optional local microphone conversation mode with model preparation before recording, Esc cancellation, and memory-only audio capture.
- Changed push-to-talk from right Ctrl alone to right Ctrl plus the `＼ / _` key left of right Shift, without stealing right Ctrl by itself.
- Added optional direct recording-state notifications to source-aware YouTube Dictation Pause Control instances, without making YouTube availability a requirement for microphone recording.
- Fixed microphone transcripts to stay on the ChatGPT composer focused when recording began and prevented unrelated tab replies from interrupting active transcription.
- Fixed `Next` so it follows the completed streaming reply instead of reusing the short Auto preview captured at the beginning.
- Excluded image-analysis progress text such as `画像を分析しています` from speech.
- Renamed the public product, extension, launcher, and Start menu entry to `Local Voice Bridge`, with migration from the previous launcher and startup names.
