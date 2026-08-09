# Security policy

## Local-only boundary

This project is designed for use on one PC. The local API has no general-purpose user authentication and is not intended for the internet, a LAN, port forwarding, reverse proxies, or tunnels. In addition to loopback binding, browser-origin POST requests are accepted only from a Chrome extension origin; native same-PC clients may omit `Origin`. POST bodies must use `application/json` and are limited to 32 MiB.

- The default bind address is `127.0.0.1`.
- Only `127.0.0.1`, `localhost`, and `::1` are accepted as configured hosts.
- `0.0.0.0`, LAN addresses, and external addresses are rejected before the server starts.
- `publicBaseUrl` must also be a loopback HTTP URL. There is no remote-access override.
- Normal web-page origins such as `https://example.com`, `https://chatgpt.com`, `null`, and loopback web pages cannot invoke POST mutation endpoints.
- Cross-origin preflight requests from normal web pages are rejected and the server does not emit permissive CORS headers.

The extension sends only the ChatGPT response selected by the current user action: a new response preview detected after Auto was enabled, or the chunk selected by Next, Regen, or Replay. It does not upload the conversation to an external service.

## Local files and retention

Generated audio is stored under `local-api/runtime/audio/` and may remain there until retention cleanup or the user deletes it. Reference audio, its transcription text, local configuration, logs, models, and caches are local files. For crash/reconnect recovery, the local runtime state can also contain recent assistant-response chunks, playback queue items, and transcript delivery events until they are acknowledged. Raw microphone recordings and a long-term STT transcript history are not stored. These local files are excluded from the public repository, but users remain responsible for deleting their own runtime data when it is no longer needed.

## Extension permissions and destinations

`extension/manifest.json` requests:

- `storage`: save extension settings and recover browser-side playback state after a Manifest V3 service-worker restart.
- `scripting`: restore the content scripts in already-open ChatGPT tabs when the service worker or local API reconnects.
- `alarms`: wake the Manifest V3 service worker at a low-frequency recovery interval.
- Page access to `https://chatgpt.com/*` and `https://chat.openai.com/*`: detect assistant responses selected for reading, update tab status, and deliver an approved local transcript to the ChatGPT composer.
- Host access to `http://127.0.0.1:8717/*` and `http://localhost:8717/*`: call the local health, speech, control-panel, reference-voice, and generated-audio endpoints.

No cloud TTS endpoint is configured by this project.

## Reporting a vulnerability

Do not publish vulnerability details, API keys, reference audio, ChatGPT exports, local configuration, or logs in a public Issue. Use GitHub Security Advisories for this repository and include a minimal reproduction and the affected revision.
