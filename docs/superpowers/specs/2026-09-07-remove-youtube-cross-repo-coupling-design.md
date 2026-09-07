# Remove YouTube Cross-Repository Coupling Design

## Purpose

Restore Local Voice Bridge to its intended responsibility: ChatGPT reply detection, local TTS playback, optional local microphone/STT input, Windows UI, and the browser adapter needed for those flows. Remove the unrequested runtime coupling to `youtube-dictation-pause-control`.

## Root cause

PR #18 added a `YouTubePauseNotifier` because Local Voice Bridge already had the definitive push-to-talk key state and the separate YouTube tool's own key observation was timing-sensitive. That solved a problem owned by another product by expanding Local Voice Bridge's runtime responsibility. The result conflicts with the repository's existing one-app UX and responsibility contract.

## Desired architecture

Local Voice Bridge must not control, coordinate, or publish runtime state for YouTube. `VoiceConversationController` owns only the microphone conversation lifecycle: push-to-talk state, STT preparation, recording, transcription, ChatGPT target delivery, cancellation, and state reporting.

The only runtime loopback service used by normal Local Voice Bridge operation is its own Local API (default `127.0.0.1:8717`). External repositories, separate resident applications, new localhost ports, or external runtime services are not added merely for code reuse or convenience.

## Removal scope

- Delete `local-api/dictation_pause_notifier.py`.
- Remove `YouTubePauseNotifier`, `DictationPauseNotifier`, pause executors/state, and all start/stop/shutdown notifications from `local-api/conversation_controller.py`.
- Remove YouTube-specific controller/notifier tests.
- Remove current-user documentation that describes YouTube integration from README, operation, troubleshooting, and architecture docs.
- Preserve historical changelog information, but add an explicit removal entry so history remains truthful.
- Remove the notifier module from architecture required-file checks.

## Recurrence prevention

### Human/agent rule

A change must stop before implementation and obtain explicit user approval if it would introduce any of the following to normal runtime or setup:

- a dependency on another repository's application or runtime,
- another resident process/application,
- another localhost service or port,
- another installation/startup/monitoring step,
- a new external service or runtime dependency,
- ownership of a feature whose user-facing purpose is outside Local Voice Bridge.

"Existing code can be reused", "cleaner responsibility separation", or "it fixes another tool" are not sufficient reasons to cross this boundary.

### CI rule

`scripts/check-architecture.js` will enforce the concrete Local Voice Bridge boundary by failing if the removed YouTube bridge module, symbols, port, environment variable, or current-user documentation reappear. This gate supplements, rather than replaces, the explicit approval rule above.

## Responsibility audit result

Repository-wide search of current default-branch runtime/documentation found no second equivalent runtime cross-repository coupling:

- `repo-launch-doctor` is a CI-only checkout and is not a normal-runtime dependency.
- `127.0.0.1:8717` is Local Voice Bridge's own Local API.
- references to `73_chatgpt-tab-memo` describe an ownership boundary and do not create runtime coupling.
- setup-time dependency acquisition such as FFmpeg is infrastructure required by Local Voice Bridge itself, not delegation of an unrelated user feature to another resident application.

No unrelated refactor is included in this change.

## Acceptance criteria

- No Local Voice Bridge runtime code sends YouTube-related state or contacts port `17654`.
- `local-api/dictation_pause_notifier.py` no longer exists.
- Microphone conversation start/stop/disable/shutdown behavior continues without pause-notifier state or executors.
- README/operation/troubleshooting/architecture no longer present YouTube control as a Local Voice Bridge capability.
- `AGENTS.md`, `CONTRIBUTING.md`, and `docs/SPEC.md` state the external-runtime/responsibility approval boundary.
- Architecture CI detects reintroduction of the removed bridge markers.
- Existing CI, including public-tree, architecture, Python/controller, background, and mock E2E checks, passes on the final branch.
