# アーキテクチャ

このリポジトリは、ChatGPTの新しいassistant返答の完了を検知し、PC上のIrodori v3 direct APIで冒頭プレビューを一度だけ読み上げるChrome / Brave拡張とWindows常駐アプリです。通信は127.0.0.1のローカルAPIだけを使用します。

## 役割

- `LocalVoiceBridge.exe`: 既存の`local-api/.venv`と`pythonw.exe`を使い、通知領域アプリを起動する小さなランチャー。`--setup`は通常利用者向けの正式セットアップ/修復入口
- `local-api/tray_controller.py`: Qt通知領域、Windows小窓、デスクトップペット、マイクControllerを組み立てるUI composition層。小窓の`repair_requested`を既存のsetup-after-exit経路へ接続する
- `local-api/server_supervisor.py`: API health、CUDA preflight、所有server process、restart/shutdown、controller/server log・生成音声メンテナンスを担当
- `local-api/windows_integration.py`: Windows自動起動、legacy移行、single-instance mutex、Explorer/MessageBox、再起動・再セットアップ・uninstallのno-window process launchを担当
- `local-api/control_panel.py`: `キャラクター`、`音量`、`マイク会話`、`自動読み上げ`、`次へ`、`再生成`、`停止`、`もう一度`、`詳細設定`、状態、現在文章、キュー数、複数タブcontextを表示する常時最前面の小窓
- `local-api/advanced_settings_dialog.py`: Local APIが所有するSTTモデル、送信前猶予、Live TTSプロファイルを編集し、ブラウザ固有の読み上げ範囲設定への明示的な遷移を提供するWindows詳細設定UI
- `local-api/control_state.py`: 設定、ブラウザ状態、配信outboxを組み合わせて保存する薄い調整層。Windows/runtime設定の正本を保持する
- `local-api/state_normalization.py`: 設定・拡張状態・マイク状態の入力境界と既定値。`liveTtsProfile`を含むruntime設定を正規化する
- `local-api/browser_runtime_state.py`: タブ・最新返答・キュー・録音開始時送信先の永続スキーマ
- `local-api/durable_outbox.py`: コマンドと文字起こしイベントの非破壊poll、consumer別ACK、再配信、容量制限、consumerの7日失効・32件上限・旧形式移行
- `local-api/http_io.py`: JSON入出力と切断済みsocketの扱い
- `local-api/runtime_readiness.py`: process、依存関係、拡張機能、タブ、モデル状態を分けたReady判定
- `local-api/desktop_pet.py`: Windowsデスクトップ上のペット1体の表示、左ドラッグ移動、ダブルクリック通知を担当
- `extension/content.js`: 各ChatGPTタブの専用Controllerを生成し、起動・停止とページイベントだけを接続する調整層
- `extension/content-settings.js`: content側設定の既定値、移行、正規化
- `extension/content-dom-observer.js`: assistant DOM監視、生成中・完了判定、Auto controllerへの通知
- `extension/content-completion-marker.js`: 生成中・完了未確認・再生中・エラー停止を示す静的favicon状態機械
- `extension/content-conversation-bridge.js`: Composer状態、文字起こし配送、送信・取消、Live所有権の接続
- `extension/content-audio-player.js`: ブラウザ側音声再生、Object URL、再生開始・完了通知
- `extension/content-message-router.js`: content scriptのChrome message振り分け
- `extension/assistant-source-filter.js`: ChatGPTの出典・citation UIを周辺証拠付きで判定し、本文を含まない最小コンテナだけを除外する境界
- `extension/assistant-text-extractor.js`: assistant DOMから本文だけを抽出し、コード、操作ボタン、途中状態を除外したうえで出典フィルタへ委譲する境界
- `extension/auto-speech-controller.js`: 既存返答の基線、新規返答の生成中・完了候補・完了確定、可逆な完了証拠、Auto一回送信、完了通知をタブ単位で管理
- `extension/prompt-input-core.js`: 使用可能なChatGPT Composerの選択、ProseMirrorへのネイティブ挿入・削除、送信ボタン範囲、送信前ACK後のクリックを担当
- `extension/background.js`: background各Controllerの共有状態と依存関係を組み立てる調整層
- `extension/background-local-api-client.js`: loopback APIへのHTTP要求、音声取得、Ref・ペット・会話状態同期
- `extension/background-runtime-store.js`: Service Worker起動時の復元、永続化予約、API復旧時の再水和
- `extension/background-tab-registry.js`: ChatGPTタブ登録、所有タブ、選択タブ、reload・close処理
- `extension/background-conversation-target.js`: 録音開始時送信先の選択・固定・文字起こし配送
- `extension/background-playback-queue.js`: 共通ローカル再生、回答元タブへの状態通知、watchdog、Stop / Next / Regen / Replay
- `extension/background-message-router.js`: runtime messageの検証と専用Controllerへの振り分け。Options更新時はブラウザ固有設定だけを再取得・配信し、runtime設定をLocal APIへ逆流させない
- `extension/background-settings-core.js`: Chrome設定の既定値、移行、入力正規化、Refの明示的`none`と旧設定の区別、Local API→Chrome mirror計画を副作用なしで担当。legacy Local API snapshotは新owner契約が存在するまでruntime mirrorを上書きしない
- `extension/background-runtime-core.js`: Service Worker再起動時の状態シリアライズ・復元・キュー重複排除を副作用なしで担当
- `extension/background-queue-core.js`: Auto許可判定、streaming時の既読境界維持、Auto重複排除、Next / Regen選択、キュー項目正規化を副作用なしで担当
- `extension/background-external-state.js`: 既存のtabs、手動対象、current/last playback itemからAuto scope・操作対象・再生元の外部表示contextを副作用なしで導出する
- `extension/background-control-sync.js`: 安定consumer ID、control-panel poll / ACK、再配信カーソル、Local API→Chrome mirrorの外部設定同期、録音開始時の送信先への文字起こし配送を担当
- `extension/live-browser-core.js`: assistant基線・一意bind、文境界、prefix整合、429 bounded retryを副作用なしで担当
- `extension/live-content-controller.js`: `pageInstanceId`、`submissionId`、assistant bind、Liveチャンク送信、入力・送信・Regen・遷移による失効を担当
- `extension/background-live-client.js`: content scriptから受けたLive要求へ送信元tab IDを上書きし、loopback Live APIへ転送
- `local-api/server.py`: サーバー起動、依存サービス生成、RequestHandlerとrouter contextの組み立て
- `local-api/api_router.py`: 永続状態、音声、Live、Ref、外部パネル、ペットAPIのGET / POSTルーティング
- `local-api/voice_runtime.py`: 起動時モデル準備と、独立した生成ワーカー・再生ワーカー、Replay、世代付きStopを担当
- `local-api/conversation_submission.py`: 送信前`arm`、送信後`commit`、assistant `bind`、失効・完了を永続管理
- `local-api/live_conversation.py`: bind済み所有権、最大2チャンク先読み、重複排除、Live完了・失敗を管理
- `local-api/gpu_arbiter.py`: Windows名前付きGate/GPU MutexでSTTを次のTTSより優先
- `local-api/runtime_events.py`: 本文・絶対パスを含めないLive JSONLイベントを記録
- `local-api/conversation_controller.py`: 録音・モデル準備・文字起こし・ChatGPT送信の状態遷移を調整
- `local-api/audio_recorder.py`: sounddevice録音と音声バッファ管理
- `local-api/stt_runtime.py`: faster-whisperモデル準備とCUDA文字起こし
- `local-api/windows_push_to_talk.py`: 右Ctrl＋`＼ / _`のWindows低レベルキーボードフック
- `local-api/dictation_pause_notifier.py`: YouTube Dictation Pause Controlへの任意状態通知
- `local-api/control_panel_client.py`: Windows小窓・Windows詳細設定・マイクControllerから使うloopback API client
- `local-api/panel_window_state.py`: 小窓位置の保存・画面内復元

## Windows Local Voice小窓

小窓に表示する日常操作は次だけです。

- `キャラクター`
- `音量`
- `マイク会話`
- `自動読み上げ`
- `次へ`
- `再生成`
- `停止`
- `もう一度`
- `詳細設定`

`キャラクター`は内部では既存の参照音声設定`referenceVoice`を使い、空IDは`標準`として表示します。同じIDのペット素材がある場合はデスクトップペットも連動します。TTS runtime/modelはIrodori v3 direct固定です。

`自動読み上げ`の永続値`enabled`と`micConversationEnabled`は独立しています。一方のボタン操作で他方を書き換えません。マイク会話中にAutoを一時抑制する場合はruntime phaseで行います。

`詳細設定`は`advanced_settings_dialog.py`を開き、Local APIが正本として持つ`sttModel`、`cancelGraceMs`、`liveTtsProfile`を編集します。ブラウザ固有の`previewMaxLines` / `previewMaxChars`へはWindows詳細設定の`ブラウザの読み上げ範囲設定`から拡張Optionsへ明示的に遷移します。拡張Optionsはruntime設定の正本になりません。

小窓はデスクトップペットのダブルクリック、または通知領域の`Show Local Voice panel`から表示・非表示を切り替えます。×は終了ではなく非表示です。位置は`local-api/runtime/control-panel-window.json`へ保存します。初回オンボーディング未完了時だけ自動表示し、導入→接続→テスト音声成功後に完了を永続化します。通常起動は非表示です。

小窓と拡張機能は次のloopback APIで同期します。

```text
GET  /v1/control-panel
GET  /v1/control-panel/poll?consumer=<id>&after=<command-id>&afterEvent=<event-id>
POST /v1/control-panel/ack
GET  /v1/browser-runtime
POST /v1/browser-runtime
POST /v1/control-panel/settings
POST /v1/control-panel/command
POST /v1/control-panel/state
```

設定・未処理コマンド・文字起こしイベント・ブラウザ共通キューは永続化されます。pollでは削除せず、安定consumer IDから処理成功後のACKを受けて初めて配信済みになります。ACK失敗やService Worker終了時は同じIDで再配信され、文字起こしは`deliveryId`で二重挿入を防ぎます。

外部state snapshotには表示専用の`autoScopeTabs`、`manualTargetTabId` / `manualTargetTitle`、`playbackSourceTabId` / `playbackSourceTitle`を含めます。これらは既存状態から導出し、新しい高頻度pollや全タブbroadcastを追加しません。

## 返答検知と全タブAuto

1. 開いている各ChatGPTタブが`background-message-router.js`経由で`background-tab-registry.js`へ登録されます。
2. `assistant-source-filter.js`が出典UIだけを除外し、`assistant-text-extractor.js`がassistant本文を取り出し、`auto-speech-controller.js`が既存返答を基準として記録します。
3. 外部小窓で`自動読み上げ`をオンにすると、すべての登録済みChatGPTタブが基準を作り直します。
4. その後で各タブへ新しく表示されたassistant返答をAuto状態機械が検知・安定判定します。
5. 最大2行・80文字の冒頭プレビューを`background-queue-core.js`が重複排除し、`background-playback-queue.js`が共通キューへ追加します。
6. `background-playback-queue.js`が`background-local-api-client.js`で音声を生成・ローカル再生し、回答元`tabId`には開始・完了・停止・エラーの状態だけを通知します。

Autoの対象は、最後に触った1タブだけではありません。開いている全ChatGPTタブです。`思考中`、`考え中`、`Thinking`、`画像を分析しています`だけの途中状態と、Autoをオンにする前から表示されていた返答は読みません。

## 音声生成と再生

```text
各ChatGPT content-dom-observer.js
  -> content-message-router.js / background-message-router.js
  -> background-runtime-store.js がローカル永続キューへ同期
  -> background-playback-queue.js / background-local-api-client.js
  -> POST http://127.0.0.1:8717/v1/speak { playLocal: true }
  -> voice_runtime.py の生成ワーカーで生成
  -> 独立した再生ワーカーがPCの音声デバイスで再生
  -> backgroundがitem.tabIdへ状態だけを通知
  -> 次のキュー項目へ進む
```

音声はWindows側の共通再生ワーカーから出力し、回答元`tabId`にはfavicon更新用の状態通知だけを送ります。回答元タブが閉じられてもローカル再生は継続します。`uiOwnerTabId`と`selectedTabId`は手動操作の対象返答を決め、Autoの検出対象は制限しません。APIまたはService Workerの再起動後は永続状態からタブ・最新返答・待機キューを復元します。

### 30タブ向け負荷境界

通常検知はDOM・タブイベント駆動です。各タブ5秒heartbeatは使わず、MV3休止対策は全体で60秒に1回のChrome Alarm、サーバー側の接続有効期間は90秒とします。Service Worker起動時の制御同期は全タブ再接続の完了を待たずに開始し、各タブの再接続は2.5秒で打ち切るため、無応答タブ1件で全体接続を止めません。生成中タブだけ30秒の保険確認、全タブ回復sweepは既定60秒・下限20秒、完了候補の短い再確認は対象タブ1個だけに限定します。faviconは静的SVGで、定期描画しません。数値と回帰条件は[タブ状態と30タブ向け低負荷設計](docs/tab-status-and-resource-design.md)に固定します。

Windows trayのConversation/Pet同期も500ms固定pollを使わず、小窓のsnapshot signalを契機にevent-drivenで反映します。architecture gateは固定pollの再導入を禁止します。

## マイクLive経路

```text
録音終了
  -> CUDA faster-whisper（CPU自動fallbackなし）
  -> content.js が submissionId とassistant基線を生成
  -> POST /v1/conversation/submission action=arm
  -> 永続化ACK後だけChatGPT送信ボタンをクリック
  -> action=commit
  -> 同じtab/page/conversationの新規assistant候補が1件だけならaction=bind
  -> 確定文をPOST /v1/live/chunksへ最大2件先読み
  -> TTS生成ワーカーと再生ワーカーを重ねる
```

入力、Enter、送信ボタン、Regen、会話遷移、reload、次の録音は`cancelEpoch`を進め、生成済み・待機中・再生中の古いチャンクを失効します。Service WorkerまたはAPI再起動時、未完了Liveは自動復元せず`invalidated`になります。通常Auto、Next、Regen、Replayの永続キューは従来契約を維持します。

GPUは`Local\\LocalVoiceBridgeGpuSttGate-*`と`Local\\LocalVoiceBridgeGpu-*`の2つのWindows名前付きMutexで調停します。STTがGateを取得すると、新しいTTSはGPU Mutexへ割り込めません。すでに実行中のTTSは安全に完了させ、録音自体はTTS生成完了を待たず開始します。

## YouTube停止状態の直接通知

マイク会話モードの低レベルキーボードフックは、録音開始・終了を最初に確定できる唯一の経路です。別プロセスで同じキーを再監視せず、次の通知を専用executorから送ります。

```text
right Ctrl + VK_OEM_102 start/stop
  -> conversation_controller.py
  -> POST http://127.0.0.1:17654/state
     {"active": true|false, "source": "local-voice-bridge"}
  -> YouTube Dictation Pause Controlが他の入力元とOR集約
```

通知は任意連携です。接続失敗やタイムアウトは録音・文字起こし・送信を失敗させません。無効化と正常終了時は、残留activeを避けるため`false`を送信します。

## キャラクター（referenceVoice）とデスクトップペット

外部小窓で`キャラクター`を変更すると、内部保存キー`referenceVoice`の同じIDを次へ送ります。

```text
POST http://127.0.0.1:8717/v1/desktop-pet
```

空IDは通常UIでは`標準`として表示します。内部互換では空、`none`、旧`qwen`系値、不正なパス形式を安全な既定値へ正規化し、指定IDの素材がない場合はデスクトップ側が利用可能な既定素材へフォールバックします。

ペットの操作は次だけです。

- 左ドラッグ: 位置を移動・保存
- 左ダブルクリック: Windows Local Voice小窓を表示・非表示
- シングルクリック、右クリック: 何もしない
- ドラッグ直後のダブルクリック: 誤操作防止のため無効

ペットの位置と選択IDは`local-api/runtime/desktop-pet-settings.json`へ保存します。

## 通知領域・起動・復旧

通常入口は`LocalVoiceBridge.exe`です。EXEは`pythonw.exe local-api/tray_controller.py`を非表示で起動します。通常利用者向けのセットアップ/修復入口は`LocalVoiceBridge.exe --setup`です。

通知領域は、外部小窓の表示・非表示、状態確認、再起動、フォルダ表示、自動起動、再セットアップ、終了を担当します。ペット専用の表示・種類・位置メニューは持ちません。Windowsログイン時の自動起動もEXEを直接指定します。

拡張機能の`connected=false`と`updateRequired=true`は別状態です。切断中は自己reloadが成功したような文言を表示せず再接続を待ちます。接続済みかつ対応版が更新待ちの場合だけ`reload_extension`を通常の自己reload導線として使用します。runtime修復は小窓の`環境を修復`→`repair_requested`→trayの既存setup-after-exit→`LocalVoiceBridge.exe --setup`で処理し、通常利用者へ内部cmdを露出しません。

## 設定所有権と保存

Local APIがWindows/runtime設定の正本です。

- `referenceVoice`
- `voiceVolume`
- `micConversationEnabled`
- `sttModel`
- `cancelGraceMs`
- `liveTtsProfile`
- `enabled`（AutoのON/OFF。マイク会話のmaster switchではない）

Chrome storageは拡張実行に必要なmirrorを持ちますが、Local API初期化後にruntime設定を逆方向へ上書きする正本にはなりません。初回の未初期化Local APIだけは既存browser runtime値を一度bootstrapできます。legacy Local API snapshotは新owner契約を示す`liveTtsProfile`が存在するまでSTT/cancel/live mirrorを上書きしません。

拡張Optionsが直接所有するブラウザ固有設定は次だけです。

- `previewMaxLines`
- `previewMaxChars`

Windows側にはさらに次を保存します。

- 外部小窓の位置・初回オンボーディング完了状態
- ペットの選択IDと位置
- 拡張機能から受け取った直近状態
- 登録タブ、選択タブ、最新返答、Auto / Next境界、待機キュー、Replay対象、録音開始時の送信先セッション
- 未ACKコマンド・文字起こしイベントとconsumer別ACK位置

`referenceVoice`はキャラクター表示とペット連動で共有しますが、別の独立した`Voice`/`Pet`選択正本は作りません。

## 変更時の設計ゲート

`npm run check:architecture`は、責務分離に必要なController / routerモジュール、主要import、禁止された重複実装、固定poll再導入、オーケストレータの行数上限を確認します。`server.py`、`control_panel.py`、`conversation_controller.py`、`content.js`、`background.js`へHTTP routing、録音・STT・Windows hook、設定、DOM監視、メッセージ分岐、再生キューなどの責務を戻す変更や、上限を超える変更はCIで失敗します。詳細設定は`advanced_settings_dialog.py`、外部tab context導出は`background-external-state.js`へ分離し、上限を緩めて責務増大を隠しません。

## テスト

- Pythonテスト: loopback境界、外部状態ストア、外部Qt小窓、Windows詳細設定、独立Auto/mic、復旧表示、通知領域、ペットのドラッグ・ダブルクリック、ランチャー
- extension単体テスト: assistant本文抽出、Auto状態機械、Composer操作、全タブ共通キュー、Next / Regen境界、Local API→browser mirror、Optionsのbrowser-only ownership、ACK再配信、Service Worker / API復旧、delivery ID、ローカル再生、Ref・ペット同期、外部tab context
- mock E2E: Chrome内パネルなし、外部Auto、Next / Regen / Replay、短文、途中状態除外、複数タブ共通キュー、マイク送信前ACK、assistant bind、Liveチャンク、出典UI除外と通常リンク・本文保持のDOMマトリクス
- Live 17項目ゲート: 入力・送信・Regen・遷移の割り込み、stale排除、再起動失効、通常キュー互換、曖昧bind拒否、STT優先、CPU fallback 0
- real E2E: 専用loopbackポートでIrodori v3 direct、Next、実参照音声・ペット同期、複数タブ共通キュー
- 実機測定: Suguhaの3プロファイル、CUDA STT、別プロセスGPU競合、録音終了から再生開始までの全経路

エージェント実行のブラウザ検証では`voiceVolume=0`と`--mute-audio`を使用します。
