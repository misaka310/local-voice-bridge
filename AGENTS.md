# Repository instructions for Local Voice Bridge

## Preserve the existing public UX

Do not change the ChatGPT reading UX unless the user explicitly asks for a UX change.

The `main` branch README is the source of truth for the public UX. Before changing `README.md`, `extension/content.js`, `extension/background.js`, or E2E expectations, first read the main README and identify the existing UX contract.

Current public UX contract:

- Detect a new assistant response in ChatGPT.
- Send only the beginning preview to the local TTS API.
- Auto playback must not read the full assistant response.
- Auto playback preview limit defaults to max 2 lines / 80 characters and may be changed only through the extension options page.
- Auto should normally call `/v1/speak` once per new assistant response.
- Auto must not split the full assistant response into chunks and play all chunks.
- `Next`, `Replay`, and `Regen` behavior must follow the existing behavior unless the user explicitly requests a change.

## Engine changes are not UX changes

Replacing Qwen or ComfyUI with Irodori direct is an engine/runtime change. It must not change ChatGPT DOM detection, preview extraction, Auto playback scope, or user-visible playback semantics.

Allowed for Irodori direct work:

- Change the local TTS runtime/API implementation.
- Change setup/run scripts for Irodori direct.
- Update docs to explain the Irodori direct setup path.
- Keep `Ref=none` as the initial public path.
- Keep reference voice support.

Not allowed without explicit user approval:

- Changing Auto from preview-only playback to full-response playback.
- Reinterpreting preview limits as chunk-splitting rules.
- Updating E2E expectations to match accidental UX changes.
- Rewriting README with internal implementation history or developer-only wording.

## Required safety checks before behavior changes

Before modifying behavior in `extension/content.js`, `extension/background.js`, or E2E tests:

1. Read the main README.
2. State the existing UX contract being preserved.
3. State the exact behavior being changed.
4. If the change affects user-visible behavior, stop and ask the user first.
5. Add or update tests that preserve the documented behavior.

## Architecture boundaries for AI changes

The focused modules and `npm run check:architecture` are part of the product safety contract, not optional cleanup.

- Keep `local-api/control_state.py`, `local-api/server.py`, and `extension/background.js` as coordinators. Do not add a new persistence schema, delivery cursor, retry loop, HTTP serializer, settings normalizer, or model lifecycle directly to them.
- Put settings and input normalization in `state_normalization.py` or `background-settings-core.js`.
- Put durable command/event delivery and ACK behavior in `durable_outbox.py` or `background-control-sync.js`.
- Put browser persisted-state schemas and merge/restore rules in `browser_runtime_state.py` or `background-runtime-core.js`.
- Put HTTP JSON/socket behavior in `http_io.py`, readiness composition in `runtime_readiness.py`, and synthesis/playback serialization in `voice_runtime.py`.
- Do not weaken line caps, remove required-module checks, or change tests to accept an accidental behavior merely to make CI pass. A required new responsibility must be extracted into a focused module with direct tests.
- Run `npm run check:architecture` and `npm run test:ci` before claiming completion. For changes to persistence, ACK/retry, Service Worker recovery, queue restoration, or transcript routing, require two consecutive full CI passes; a rerun-only pass after an unexplained failure is not completion.

## Existing accident-prevention behavior to keep

These protections are allowed and should be preserved unless the user asks otherwise:

- Auto ON establishes a baseline and must not read assistant messages already visible before Auto was enabled.
- Auto OFF -> ON establishes a new baseline.
- The same assistant message or normalized text must not be queued twice.
- Stale reference voices such as `qwen3`, `qwen`, `none`, and empty strings normalize to `referenceVoice=""` unless the user selects an existing reference voice.
- Empty Ref must not fall back to stale storage values.
- `run-voice-stack.cmd` should detect an existing `8717` listener and stop with a clear PID/tasklist message rather than silently starting against the wrong process.
- Generated audio must remain bounded. Keep the default retention at newest 1,000 files, 1 GiB, and 14 days unless the user explicitly requests a product change.
- Controller and server logs must use bounded rotation. Do not restore unbounded append-only server logging.
- Full restart may stop an existing API only after proving it belongs to the same repository installation; never kill an arbitrary process merely because it owns port 8717.

## Public release tree rule

When rebuilding history or publishing `main`, create the public tree from Git-tracked source files only, for example with `git archive` or a clean clone. Do not copy the working directory wholesale. Local runtime audio, `.venv`, E2E profiles, test results, npm caches, AI handoff files, and local reference voices must stay out of the public tree.

## README rules

README is for normal users. It should explain:

- What the extension does.
- How to install/setup/run it.
- What success looks like.
- How to add reference voice files.
- Symptom-based troubleshooting.

Do not put internal comparisons, postmortem text, or developer-only reasoning in README. Symptoms are okay; examples: `8288` appears, `reference voice not found` appears, or Auto reads an old response.

## Verification audio rule

When running E2E, Playwright, or manual browser verification in this repository, set `voiceVolume=0` first and launch the browser with `--mute-audio` unless the user explicitly asks to verify audible playback volume.

Keep the public default volume in product docs unchanged unless the user asks for a product default change. This rule is only for agent-driven verification so local checks do not blast audio unexpectedly.

## Windows launcher rule

Use a small Windows EXE as the primary user-facing launcher. Do not introduce VBS as the normal startup path. VBS may remain only as a temporary backward-compatibility forwarder for existing users. Setup, documentation, and Windows login startup must point to `LocalVoiceBridge.exe`.
