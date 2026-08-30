# アーキテクチャ

Local Voice Bridgeは、ChatGPTの返答検知を担当するChrome / Brave拡張と、音声生成・再生・設定・Windows UIを担当するWindows常駐アプリで構成します。通信は127.0.0.1のLocal APIだけを使用します。

設計上の中心原則は、**利用者からは1つのWindowsアプリに見せ、ブラウザ拡張はChatGPTとのアダプターへ限定すること**です。

## 利用者向け責務

- `LocalVoiceBridge.exe`: Windowsアプリの正式入口。手動起動は小窓を表示し、`--background`はWindowsログイン時の静かな常駐、`--setup`は正式なセットアップ/環境修復入口
- Windows Local Voice小窓: 日常操作、現在状態、複数タブcontext、復旧操作の正本
- Windows詳細設定: STTモデル、送信前猶予、Live TTSプロファイルなどローカルruntime設定の編集
- Chrome / Brave拡張: ChatGPT DOM・タブ・Service WorkerとLocal APIの接続
- 拡張Options: `previewMaxLines` / `previewMaxChars`などブラウザ固有設定だけを編集
- デスクトップペット: 状態表示と小窓への補助入口

日常操作をブラウザとWindowsへ二重配置しません。拡張アイコン左クリックはブラウザ固有Optionsを開くだけです。

## Windowsアプリ側

- `scripts/launcher/VoiceBridgeLauncher.cs`: `LocalVoiceBridge.exe`の小型launcher。`--background`を`pythonw.exe`へ転送し、通常利用でコンソールを出さない
- `local-api/tray_controller.py`: QApplication、通知領域、小窓、ペット、マイクControllerを組み立てるcomposition層。OS機能やserver process詳細を所有しない
- `local-api/windows_integration.py`: Windows自動起動、legacy移行、single-instance mutex、既存instance activation event、Explorer/MessageBox、no-window process launch
- `local-api/server_supervisor.py`: API health、CUDA preflight、server process lifecycle、restart/shutdown、log・生成音声maintenance
- `local-api/control_panel.py`: `キャラクター`、`音量`、`マイク会話`、`自動読み上げ`、`次へ`、`再生成`、`停止`、`もう一度`、`詳細設定`と状態表示
- `local-api/control_panel_onboarding.py`: 初回オンボーディングと、visible/hiddenを通して共通のsnapshot streamを提供するfocused wrapper
- `local-api/control_panel_async.py`: loopback API呼び出しをQt UI thread外で実行
- `local-api/advanced_settings_dialog.py`: STTモデル、送信前猶予、`liveTtsProfile`のWindows設定UI。ブラウザ設定への明示リンクを持つ
- `local-api/control_panel_client.py`: Windows UI・マイクController用loopback client
- `local-api/panel_window_state.py`: 小窓位置の保存と画面内復元
- `local-api/desktop_pet.py`: ペット表示、左ドラッグ、ダブルクリック通知
- `local-api/desktop_pet_config.py`: ペット定義・選択・位置などの永続状態
- `local-api/conversation_controller.py`: マイク会話の録音、STT準備、文字起こし、送信phaseを調整
- `local-api/audio_recorder.py`: sounddevice録音とメモリ内音声バッファ
- `local-api/stt_runtime.py`: faster-whisper CUDA runtime
- `local-api/windows_push_to_talk.py`: 右Ctrl＋`＼ / _`のWindows低レベルフック
- `local-api/dictation_pause_notifier.py`: YouTube Dictation Pause Controlへの任意loopback通知

### 起動導線

手動で`LocalVoiceBridge.exe`を起動すると小窓を表示します。Windowsログイン時はRun registryから`LocalVoiceBridge.exe --background`を起動し、オンボーディング完了済みなら小窓を前面へ出しません。

single-instance mutexで二重常駐を防止します。すでに起動中の状態で手動起動された場合は、Windows named activation eventで既存processへ小窓表示を要求します。通知領域アイコンのダブルクリックも同じ`show_control_panel`経路へ集約します。

通知領域メニューは日本語で、`小窓を表示` / `小窓を隠す`は実際の表示状態に追従します。

### 初回オンボーディング

`control_panel_onboarding.py`は`control-panel-onboarding.json`だけを所有し、次を案内します。

1. 拡張機能導入
2. Local APIへの拡張接続
3. 既存`/v1/speak`によるテスト音声
4. 成功後の完了状態永続化

専用の別音声APIは追加しません。

### 小窓のsnapshot同期

表示中は`control_panel.py`のactive/idle refresh policyを使います。非表示中も`FirstRunControlPanel`が同じ`refresh_now()`経路を低頻度で継続し、`snapshot_applied`をtray compositionへ渡します。

tray側に会話設定用・ペット用の別API polling timerは持ちません。これにより、設定・再生状態・ペット状態は1本のcontrol-panel snapshot pipelineから同期されます。

## Local APIと永続状態

- `local-api/server.py`: サーバー起動と依存サービスの組み立て
- `local-api/api_router.py`: control panel、browser runtime、voice、Live、ペットAPIのGET/POST routing
- `local-api/http_io.py`: JSON I/O、body境界、通常client disconnect
- `local-api/control_state.py`: 設定、browser state、durable outboxを組み合わせる調整層
- `local-api/state_normalization.py`: 設定・拡張状態・マイク状態の入力境界
- `local-api/browser_runtime_state.py`: タブ、最新返答、queue、録音開始時送信先の永続schema
- `local-api/durable_outbox.py`: command / transcript eventのconsumer別poll・ACK・再配信
- `local-api/runtime_readiness.py`: process、依存、拡張、タブ、model readinessを分離
- `local-api/voice_runtime.py`: Irodori model準備、生成worker、再生worker、Replay、generation-aware Stop
- `local-api/conversation_submission.py`: send arm / commit / assistant bind / invalidation
- `local-api/live_conversation.py`: bind済み所有権、Live chunk queue、完了・失敗
- `local-api/gpu_arbiter.py`: STTとTTSのGPU arbitration
- `local-api/runtime_events.py`: 本文・絶対パスを含めないstructured event log

Local runtime設定の正本はLocal APIです。`referenceVoice`、音量、`micConversationEnabled`、STTモデル、送信前猶予、`liveTtsProfile`をChrome storageへ逆流させて正本化しません。

## ブラウザ拡張側

### content

- `extension/content.js`: content側Controllerのcomposition
- `extension/content-settings.js`: browser/content設定の正規化
- `extension/content-dom-observer.js`: assistant DOM監視
- `extension/content-completion-marker.js`: favicon state machine
- `extension/content-conversation-bridge.js`: Composer、transcript、Live接続
- `extension/content-audio-player.js`: browser側audio lifecycle
- `extension/content-message-router.js`: content message routing
- `extension/assistant-source-filter.js`: citation/source UI除外境界
- `extension/assistant-text-extractor.js`: assistant本文抽出
- `extension/auto-speech-controller.js`: 新規返答の生成中・完了候補・完了確定・Auto一回送信
- `extension/prompt-input-core.js`: ChatGPT Composerへの入力・送信境界

### background

- `extension/background.js`: background各Controllerと共有状態のcomposition
- `extension/background-action-navigation.js`: 拡張toolbar左クリックからbrowser Optionsを開くfocused UI navigation
- `extension/background-local-api-client.js`: Local API client
- `extension/background-runtime-store.js`: Service Worker runtime復元・永続化
- `extension/background-tab-registry.js`: ChatGPT tab registry、owner/selected tab
- `extension/background-conversation-target.js`: 録音開始時送信先の固定
- `extension/background-playback-queue.js`: 共通queue、ローカル再生、Stop / Next / Regen / Replay
- `extension/background-message-router.js`: runtime message validationとrouting
- `extension/background-settings-core.js`: Chrome/browser設定の正規化とLocal API→Chrome mirror plan
- `extension/background-runtime-core.js`: Service Worker runtime serialization
- `extension/background-queue-core.js`: Auto重複排除、queue項目、manual controls
- `extension/background-external-state.js`: Auto scope、手動対象、再生元の表示contextを導出
- `extension/background-control-sync.js`: durable command/event syncとACK
- `extension/background-control-heartbeat.js`: control state heartbeat
- `extension/background-live-client.js`: contentからのLive requestをLocal APIへ転送

## 設定所有権

### Local APIが正本

- `referenceVoice`
- `voiceVolume`
- `micConversationEnabled`
- STT model
- cancel grace
- `liveTtsProfile`

Windows小窓とWindows詳細設定はこの正本を編集します。Chrome側にmirrorが必要な場合もLocal APIの値を優先します。

### ブラウザ拡張が正本

- `previewMaxLines`
- `previewMaxChars`
- ChatGPT DOM/ブラウザ挙動だけに関係する設定

Windows詳細設定の`ブラウザの読み上げ範囲設定`からOptionsへ明示的に遷移します。

## Autoとマイク会話

`enabled`（自動読み上げ）と`micConversationEnabled`は独立した永続設定です。マイク会話をオンにしてもAutoをオンにせず、Autoをオフにしてもマイク会話をオフにしません。

競合回避のための一時停止はruntime phaseで扱い、利用者設定を書き換えません。

## 複数タブAuto

1. 各ChatGPTタブを`background-tab-registry.js`へ登録します。
2. content側がassistant本文と完了状態を判定します。
3. Auto有効後に新しく完了した返答だけをqueueへ送ります。
4. `background-queue-core.js`が重複排除します。
5. `background-playback-queue.js`が1つの共通queueで生成・再生します。
6. 回答元tabにはfavicon/playback stateだけを返します。

外部snapshotには`autoScopeTabs`、`manualTargetTabId` / `manualTargetTitle`、`playbackSourceTabId` / `playbackSourceTitle`を含め、小窓で操作対象を可視化します。

## 復旧導線

- 拡張**切断**: 自己reload成功を装わず、接続確認と自動復帰を待つ
- 接続済み**更新待ち**: 対応版がself-reload capabilityを示す場合だけ`拡張機能を再読み込み`を表示
- voice runtime failure: Windows小窓の`環境を修復`から`LocalVoiceBridge.exe --setup`へ遷移
- launcher environment failure: setup/repair UIを提示

通常runtimeエラーから`setup-voice-env.cmd`など内部scriptを利用者へ直接案内しません。

## Architecture Gate

`scripts/check-architecture.js`は大きなorchestratorへの責務逆流を防ぎます。`tray_controller.py`へserver health/process/registry/mutex詳細、`background.js`へqueue/settings/delivery core logic、`content.js`へDOM/audio/Auto core logicを戻さないことをCIで検証します。

`scripts/check-tray-snapshot-sync.js`は、tray独自の固定500ms API pollingを戻さず、`FirstRunControlPanel`の`snapshot_applied`を共通state streamとして使うことを検証します。

この分離を保ったまま、利用者の導線は次の1本にします。

```text
LocalVoiceBridge.exe
  → Windows Local Voice小窓
  → 日常操作 / 状態 / 復旧
  → Windows詳細設定
  → 必要な場合だけbrowser Options

Chrome / Brave拡張
  → ChatGPTとのadapterとして裏側で動作
```
