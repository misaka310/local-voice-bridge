# Remove YouTube Cross-Repository Coupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unrequested YouTube runtime coupling from Local Voice Bridge, audit adjacent responsibility boundaries, and add enforceable recurrence prevention.

**Architecture:** Keep Local Voice Bridge self-contained around its Windows app, ChatGPT browser adapter, Local API, local TTS, and optional microphone/STT flow. Remove the notifier path entirely rather than moving YouTube control elsewhere inside this repository. Add explicit approval rules in project policy/specification and a static architecture gate for concrete reintroduction markers.

**Tech Stack:** Python 3, Node.js architecture checks, Chrome/Brave extension, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-07-remove-youtube-cross-repo-coupling-design.md`

## Global Constraints

- Do not add another repository, resident process/application, localhost service/port, startup step, or external runtime dependency without explicit user approval.
- Preserve Local Voice Bridge's existing ChatGPT/TTS/microphone behavior.
- Do not turn YouTube control into an internal Local Voice Bridge feature.
- Do not remove unrelated functionality while cleaning the coupling.
- Final verification must include the repository's normal CI gates.

---

### Task 1: Add a failing architecture boundary gate

**Files:**
- Modify: `scripts/check-architecture.js`

**Interfaces:**
- Consumes: existing `requireFile`, `requireText`, and `forbidText` architecture helpers.
- Produces: a CI failure while the current YouTube bridge remains, and a permanent regression gate after removal.

- [ ] **Step 1: Add architecture checks for the forbidden bridge**

Add a helper that can fail when a forbidden file exists, then forbid:

```text
local-api/dictation_pause_notifier.py
YouTubePauseNotifier
DictationPauseNotifier
YOUTUBE_DICTATION_PAUSE_STATE_URL
127.0.0.1:17654
youtube-dictation-pause-control
```

Apply source checks to `local-api/conversation_controller.py` and current-user docs (`README.md`, `ARCHITECTURE.md`, `docs/operation.md`, `docs/troubleshooting.md`). Remove `local-api/dictation_pause_notifier.py` from the required architecture-module list.

- [ ] **Step 2: Run architecture/CI and confirm RED**

Expected: architecture check fails because the existing notifier file and current references are still present.

- [ ] **Step 3: Commit the failing gate**

Commit message: `test: forbid cross-repo YouTube runtime coupling`

### Task 2: Remove runtime coupling

**Files:**
- Modify: `local-api/conversation_controller.py`
- Delete: `local-api/dictation_pause_notifier.py`
- Modify: `tests/test_conversation_controller.py`

**Interfaces:**
- Consumes: existing microphone conversation controller API.
- Produces: the same `VoiceConversationController` public behavior without pause-notifier dependencies or side effects.

- [ ] **Step 1: Remove notifier-specific tests**

Delete tests and test helpers whose only contract is YouTube source-state notification, including fake pause notifier/executor plumbing. Keep all recording/STT/ChatGPT-delivery tests.

- [ ] **Step 2: Remove notifier code from `VoiceConversationController`**

Remove:

```text
from dictation_pause_notifier import YouTubePauseNotifier
DictationPauseNotifier
pause_notifier
pause_executor
_pause_source_active
_send_pause_source
_set_pause_source
```

Also remove notification calls from enable/disable, key start/stop, and shutdown. Shutdown should only discard active recording and stop the controller/STT executors.

- [ ] **Step 3: Delete `local-api/dictation_pause_notifier.py`**

No compatibility shim remains because the integration itself is out of scope.

- [ ] **Step 4: Run Python/controller tests**

Expected: microphone conversation tests pass without YouTube state side effects.

- [ ] **Step 5: Commit**

Commit message: `fix: remove YouTube coupling from microphone runtime`

### Task 3: Align public docs and ownership contract

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `docs/operation.md`
- Modify: `docs/troubleshooting.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/SPEC.md`
- Modify: `CONTRIBUTING.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Produces: one consistent user/developer/agent contract for Local Voice Bridge boundaries.

- [ ] **Step 1: Remove current YouTube capability documentation**

Delete the README feature bullet, operation paragraph, troubleshooting section, and architecture module entry that describe the separate YouTube tool.

- [ ] **Step 2: Record truthful removal history**

Add a current changelog entry stating that the previously added optional YouTube recording-state bridge was removed because it crossed the product responsibility boundary. Do not rewrite historical releases as if the feature never existed.

- [ ] **Step 3: Add the approval boundary to the specification**

Add a project constraint and acceptance criterion requiring explicit user approval before adding any separate repository/runtime app/process, localhost service/port, setup/startup step, external runtime dependency, or unrelated feature ownership.

- [ ] **Step 4: Add contributor/agent prevention rules**

In both `CONTRIBUTING.md` and `AGENTS.md`, state that implementation must stop for explicit user approval before crossing that boundary; code reuse or fixing another tool is not sufficient justification.

- [ ] **Step 5: Commit**

Commit message: `docs: enforce Local Voice Bridge responsibility boundary`

### Task 4: Final responsibility audit and verification

**Files:**
- Review only unless a directly related stale reference is found.

**Interfaces:**
- Consumes: final branch content.
- Produces: evidence that the requested coupling is gone and no equivalent runtime coupling remains.

- [ ] **Step 1: Search final tree for removed markers**

Search for `youtube-dictation-pause-control`, `YouTubePauseNotifier`, `DictationPauseNotifier`, `YOUTUBE_DICTATION_PAUSE_STATE_URL`, and `17654`. Only historical changelog/design/plan references are allowed.

- [ ] **Step 2: Re-check cross-repository references**

Classify every remaining repository reference as self-link, CI/development-only tooling, or a documented ownership boundary. Any normal-runtime coupling is a failure.

- [ ] **Step 3: Run full CI**

Require public-tree, architecture, Python, background, and mock E2E gates to pass. If Windows GUI smoke is triggered for the change, require it to pass as well.

- [ ] **Step 4: Review the PR diff for unrelated changes**

Ensure the diff contains only removal, directly related tests/docs, and recurrence prevention.

- [ ] **Step 5: Merge only after all required checks pass**

Use the repository's normal PR path; do not write directly to `main`.
