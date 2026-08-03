# Responsibility-boundary refactor

## Goal

Preserve the documented Auto / Next / Regen / Replay / microphone UX while moving ChatGPT DOM parsing, Auto response lifecycle, and background queue rules out of the large coordinator files.

## Non-goals

- No change to preview limits, queue order, reference voice behavior, microphone hotkey, or Live ownership semantics.
- No rewrite of the local Python runtime.
- No mixing with the separate desktop-pet working-tree changes.

## Phases

### 1. Assistant text boundary

Input: one ChatGPT assistant message DOM node.

Output: normalized speakable assistant text with transient statuses, code blocks, controls, citation markers, and decorative source labels removed.

Owner: `extension/assistant-text-extractor.js`.

### 2. Auto response lifecycle

Input: extracted text plus generation/completion observations.

Output: one preview queue request per new response, stable update handling, and one completion notification.

Owner: `extension/auto-speech-controller.js`.

### 3. Background queue boundary

Input: normalized queue items and playback events.

Output: deterministic queue state transitions, current-item cleanup, read-boundary preservation, and selected target resolution.

Owner: `extension/background-queue-core.js`.

### 4. Coordinators

`content.js` keeps browser event wiring, settings, local playback, and Live-controller wiring. `background.js` keeps Chrome APIs, HTTP calls, persistence scheduling, and message dispatch. They delegate pure/state rules to the focused modules.

### 5. Verification and review

- Direct unit tests for each focused module.
- Existing mock browser E2E remains behaviorally unchanged.
- `npm run check:architecture` must require the new modules and lower coordinator line caps.
- Two consecutive full CI passes.
- Final diff review for responsibility leakage, accidental UX change, stale duplicated code, and unrelated-file contamination.
