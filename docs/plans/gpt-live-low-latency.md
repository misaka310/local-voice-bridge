# GPT Live風・低遅延会話 改修計画

## 1. 目的と完了条件

### 元の目的

Local Voice Bridgeで、Suguhaの声を維持しながら、GPT Liveに近いテンポの低遅延・割り込み可能な音声会話を実現する。

### 最終完了条件

次のすべてが成立した場合だけ、実装作業を`completed`と判定する。

1. 専用worktree内だけで実装されている。
2. 通常作業ツリーを変更していない。
3. `C:\00_dev\30_local-tts-service`と`C:\00_dev\_inbox\irodori-fast-lab`を変更していない。
4. TTSプロファイルとして12、16、32-stepだけを利用できる。
5. 2、3、4、6、8-stepなど未承認の設定をUI、設定、HTTP APIから選択できない。
6. 生成音声ごとに再生前品質ゲートがある。
7. 文字入力、貼り付け、送信、ChatGPT生成停止、録音開始、既存Stopで音声が停止する。
8. 割り込み後の古い生成音声・待機音声が再生されない。
9. マイク会話の返答で、文単位の先行生成・順次再生が動く。
10. 既存のAuto、Next、Regen、Replay、マイク会話を壊さない。
11. 単体テスト、既存回帰テスト、要求されたE2Eが通る。
12. 実測遅延を構造化ログと最終報告に残す。
13. `git diff`をレビューし、関係ない変更がない。
14. 実装コードのcommit、push、PRは、ユーザーの追加指示があるまで行わない。

## 2. 計画基点と安全境界

### 基点

- 通常作業ツリー: `C:\00_dev\17_chatgpt-local-voice-bridge`
- 計画・実装worktree: `C:\00_dev\_worktrees\17_chatgpt-local-voice-bridge\gpt-live-low-latency-plan`
- 作業ブランチ: `plan/gpt-live-low-latency`
- 基点HEAD: `a42cd61f3d5f052e7a69406fce598719e0a9e406`
- `origin/main`: `432d68943e6f61027a76626158ef9149cc8f8ca9`
- 基点HEADは`origin/main`より2コミット先行している。

### 開始時の通常作業ツリー

開始時点で通常作業ツリーには未コミット変更がある。これらは本計画worktreeへ取り込まず、上書き、移動、stash、reset、cleanを行わない。

確認された変更対象:

- `extension/content-text-core.js`
- `extension/content.js`
- `extension/manifest.json`
- `local-api/desktop_pet.py`
- `local-api/desktop_pet_config.py`
- `local-api/tray_controller.py`
- `tests/content-text-core.test.js`
- `tests/e2e/extension-mock-ci.spec.js`
- `tests/test_desktop_pet.py`
- `tests/test_desktop_pet_config.py`
- `tests/test_tray_qt_runtime.py`

### 読み取り専用対象

- `C:\00_dev\30_local-tts-service`
- `C:\00_dev\_inbox\irodori-fast-lab`

上記へ設定、キャッシュ、テスト結果、生成音声、ログなどを一切追加しない。本番コードから実験フォルダへ依存しない。必要な知見だけを読み取り、本リポジトリ内へ独立実装する。

### 変更禁止・非対象

- デスクトップペット
- 通知領域の構成
- Windows小窓の一般UIデザイン
- ランチャーの通常起動経路
- 無関係な一括リファクタ
- 公開リポジトリ衛生チェックの弱体化
- `=\npm-cache`など誤ったキャッシュパスの追加

## 3. 現行契約と今回のUX変更

`AGENTS.md`、`README.md`、`ARCHITECTURE.md`の現行契約は次のとおり。

- 通常Autoは、新しいassistant返答の冒頭プレビューを最大2行・80文字で1回だけ読む。
- 通常Autoは全文を分割して全チャンク再生しない。
- `Next`、`Regen`、`Replay`の既存挙動を維持する。
- 複数ChatGPTタブのAutoは1つの共通キューで直列再生する。
- `voice_runtime.py`は生成とローカル再生を単一ワーカーで直列化する。
- Service WorkerとAPIの再起動後、永続状態から復旧する。

今回、ユーザーはGPT Live風の低遅延会話を明示的に要求している。ただし互換性条件を守るため、最初の導入は次のように分離する。

### 導入方針

- **マイク会話の現在ターン**: 文単位ストリーミングTTSを使用する。
- **通常Auto**: 初期導入では従来の冒頭プレビュー1回再生を維持する。
- **Next / Regen / Replay**: 従来モードを維持しつつ、割り込み・世代検証を共通化する。
- 通常Autoを文単位の全文読み上げへ変更する場合は、測定結果と回帰影響を提示し、別途ユーザー判断を得る。

この分離により、既存利用者のAuto体験を急に変えず、マイク会話だけをGPT Liveに近づける。

## 4. 設計原則

1. ローカルAPIを会話ターン、キャンセル世代、TTSランタイム状態の正本とする。
2. 拡張機能は正本をミラーしつつ、ブラウザ音声停止だけはユーザー操作と同じイベント内で即時実行する。
3. 新しい明示的会話操作は音声所有権を取得し、古い所有者の未再生音声を無効化する。
4. TTS処理は強制スレッド停止せず、世代IDで論理キャンセルする。
5. `voice_runtime.py`の単一生成ワーカーを維持する。
6. 状態・世代・所有権の純粋ロジックは専用モジュールへ分離する。
7. `background.js`、`server.py`、`control_state.py`は調整層のまま保つ。
8. 生成結果は、生成開始前、生成完了後、保存後、品質ゲート後、再生直前に有効性を確認する。
9. 古い非同期完了は、新しい状態やReplay対象を上書きできない。
10. 割り込みは何度呼ばれても安全な冪等操作にする。
11. ログに本文全文や音声内容を残さない。文字数、ハッシュ、ID、時刻、結果だけを記録する。

## 5. 予定する責務分割

ファイル名は既存命名と設計ゲートに合わせて実装時に最終決定する。次は責務の予定であり、単一巨大モジュールにはまとめない。

### 拡張機能側

- `extension/conversation-turn-core.js`
  - `turnId`、`generationId`、`playbackId`、`cancelEpoch`
  - イベント受理可否
  - stale判定
  - 冪等interrupt
  - 状態遷移の純粋関数
- `extension/streaming-chunk-core.js`
  - assistantメッセージIDと文字範囲の管理
  - 文境界抽出
  - Markdown、URL、コードブロック、数値途中の除外
  - 安定判定
  - 重複排除
  - 最終残片の確定
- `extension/playback-owner-core.js`
  - 拡張機能全体で1つの再生所有権
  - tab、conversation、assistant message、turn単位の所有者判定
  - Service Worker復旧時の所有権正規化
- `extension/content.js`
  - DOM監視とユーザー入力イベント接続のみ
  - `beforeinput`、`paste`、通常文字キー、送信・生成停止ボタン`pointerdown`の即時interrupt通知
  - HTMLAudioElementが存在する場合の即時停止・Object URL解放
- `extension/background.js`
  - 専用coreの接続、API呼び出し、永続化のみ

### ローカルAPI側

- `local-api/conversation_turn.py`
  - 会話ターン、キャンセル世代、再生所有権、interrupt reason
  - API側の正本
  - stale完了の拒否
- `local-api/conversation_state_machine.py`
  - 明示的状態遷移
  - 状態更新の世代検証
  - 永続状態復旧時の正規化
- `local-api/voice_job_queue.py`
  - `deque`と`Condition`を使う専用キュー
  - enqueue、take、invalidate、clear、close、wake
  - `queue.Queue`の内部属性を直接操作しない
  - 単一ワーカーを維持する
- `local-api/tts_profiles.py`
  - `speed`、`balanced`、`bridge`の解決
  - 旧設定の安全な正規化
  - 未承認stepの拒否
- `local-api/audio_quality.py`
  - WAV読込と品質指標
  - 判定結果と理由
  - 安全な削除
- `local-api/voice_metrics.py`
  - 構造化イベント
  - turn、generation、playback、chunk、traceの相関
  - 本文を保存しない
- `local-api/voice_runtime.py`
  - 単一ワーカー
  - 生成、品質ゲート、再生の直列実行
  - 各境界で世代確認
  - interrupt時の即時`stop_event`と`sounddevice.stop()`
- `local-api/server.py`
  - `/v1/interrupt`などのHTTP接続だけを追加
  - 状態、キュー、品質ロジックを直接持たない

### 既存永続モジュール

- `browser_runtime_state.py`と`background-runtime-core.js`は、復旧に必要な最小限のターン・所有権・世代スナップショットだけを扱う。
- 古い`currentItem`を復元して再生する現在の経路は、新しいcancel epochと所有権を満たす場合だけ許可する。
- Service Worker再起動時に「再生中だった」という永続値だけを根拠に音声を再開しない。

## 6. IDと所有権モデル

各処理は最低限、次を持つ。

- `traceId`: ブラウザからAPI、生成、再生までを相関するランダムID
- `tabId`: ChatGPTタブ
- `conversationKey`: ChatGPT会話を識別できる範囲の安定キー
- `assistantMessageKey`: assistantメッセージ単位のキー
- `turnId`: 新しい明示的会話操作ごとに更新
- `generationId`: TTS生成ジョブごとに更新
- `playbackId`: 再生ごとに更新
- `chunkIndex`: ターン内の順序
- `cancelEpoch`: interruptごとに単調増加
- `interruptReason`: 入力、貼り付け、送信、生成停止、録音開始、Stop、Next、Regenなど

### 再生所有権

- 音声出力は拡張機能全体で1所有者とする。
- 新しい明示的会話操作は所有権を奪取する。
- マイク会話の現在ターンは通常Autoより優先する。
- 非所有タブの古い`play-audio`通知は拒否する。
- 所有権は`tabId + conversationKey + turnId + cancelEpoch`で検証する。
- タブを閉じた場合、所有権を失効させる。失効後のAPI生成結果は破棄する。

## 7. 共通interruptの意味

Local Voice Bridgeの`Stop`は、ChatGPTのテキスト生成ではなく、音声系を停止する。

共通interruptは次を行う。

1. ブラウザ側HTMLAudioElementを同じユーザーイベント内で停止する。
2. Object URLを解放する。
3. 拡張機能内の未再生チャンクを無効化する。
4. APIへ`/v1/interrupt`を送る。
5. APIの`cancelEpoch`を増加させる。
6. 現在の`stop_event`を発火する。
7. `sounddevice.stop()`を即時呼び出す。
8. 待機ジョブを無効化・破棄する。
9. 旧世代の完了通知が状態、`lastOperation`、`lastAudioPath`、現在文章を変更することを拒否する。

ChatGPT画面の生成停止ボタンを押した場合だけ、ChatGPT自身のテキスト生成停止と音声interruptが同時に起きる。

### interrupt対象

- マイク録音ホットキー押下の瞬間
- ChatGPT入力欄の`beforeinput`
- 通常文字キー入力
- 貼り付け
- 送信ボタンの`pointerdown`
- ChatGPT生成停止ボタンの`pointerdown`
- 既存Stopコマンド
- 新しい会話送信開始
- NextまたはRegenで音声を置き換える時

### interruptしない操作

- 修飾キーだけ
- Tab移動だけ
- 音量キー
- 矢印移動や選択移動だけ
- IME確定前の無意味なイベント
- 音声に影響しないUI操作

## 8. 状態機械

API側の状態を正本とし、次を最低限持つ。

- `idle`
- `listening`
- `recording`
- `transcribing`
- `submitting`
- `waiting_for_assistant`
- `generating_tts`
- `playing`
- `interrupted`
- `error`

### 主要遷移

- `idle -> listening -> recording`
- `recording -> transcribing -> submitting -> waiting_for_assistant`
- `waiting_for_assistant -> generating_tts -> playing`
- `playing -> interrupted -> recording`
- `generating_tts -> interrupted`
- `error -> idle`または新しい録音開始

### 状態更新規則

- すべての非同期更新に`turnId`と`cancelEpoch`を要求する。
- 現在値と一致しない更新を無視する。
- 古い完了通知は`idle`や`complete`へ戻せない。
- `error`は次ターン開始を妨げない。
- 再起動復旧時、実再生を確認できなければ`playing`を維持しない。
- UI表示用状態は正本から投影し、別の独立状態機械を作らない。

## 9. TTSプロファイル

### `speed`

- `numSteps: 12`
- `tScheduleMode: sway`
- `swayCoeff: -1.0`
- `contextKvCache: true`
- `modelPrecision: bf16`（GPU対応時。未対応時は安全にfp32）
- `codecPrecision: bf16`（GPU対応時。未対応時は安全にfp32）
- `cfgScaleText: 3.0`
- `cfgScaleSpeaker: 6.0`
- `decodeMode: sequential`
- `trimTail: true`
- `seed: 10`
- Suguha参照latentの安全な再利用を許可

### `balanced`

- `numSteps: 16`
- その他は`speed`と同条件

### `bridge`

- `referenceNumSteps: 32`
- `tScheduleMode: sway`
- `swayCoeff: -1.0`
- `cfgScaleText: 3.0`
- `cfgScaleSpeaker: 6.0`
- `decodeMode: sequential`
- `contextKvCache: true`
- Suguha WAV参照
- `seed: 10`
- 既存ウォーターマーク動作を維持

### 既定値と移行

- マイク会話の新しいLive経路だけ`speed`を既定候補とする。
- 通常Autoは既存動作と既存設定を維持する。
- 既存設定に未承認stepがある場合は`balanced`または`bridge`へ安全に正規化し、警告を残す。
- HTTP APIはプロファイル名を主入力とし、任意step数の直接指定を拒否する。
- 12、16、32以外のstepをテストで明示的に拒否する。

## 10. 品質ゲート

生成後かつ再生前に、専用モジュールでWAVを検査する。

### 初期指標

- WAVが正常に読める
- 音声長が0または異常値でない
- NaN、Infがない
- `rms >= 0.005`
- `clipFraction <= 0.002`
- `abs(dcOffset) <= 0.03`
- `diffSpikeFraction <= 0.002`
- `highBandRatio <= 0.08`（12kHz以上）
- `spectralFlatness <= 0.35`

### 不合格時

- WAVを冪等に削除する。
- 再生しない。
- Replay対象にしない。
- `lastAudioPath`を更新しない。
- 無限再試行しない。
- turn、chunk、失敗指標を状態と構造化ログに残す。
- 後続会話を開始できる状態へ戻す。

### 閾値検証

- `speed`、`balanced`、`bridge`で複数文章を生成する。
- 短文、長文、数字、英字混在を含める。
- 指標分布を記録する。
- 閾値変更時は旧値、新値、理由、正常音声・異常音声への影響を報告する。
- 主観試聴だけを理由に緩和しない。
- 生成物は専用worktree内の一時領域へ置き、検証後に削除する。

## 11. 文単位ストリーミングTTS

### 確定境界

優先順:

1. `。`
2. `！`
3. `？`
4. 改行
5. 閉じ括弧後の区切り
6. 一定文字数を超えた場合の自然な読点境界

### 確定禁止

- 語の途中
- Markdown記号の途中
- URLの途中
- コードブロックの途中
- インラインコードの途中
- 数値、小数点、日付の途中
- DOM上で変化している末尾
- 同じメッセージの同じ文字範囲

### 安定判定

- 文末が確認できた文は即時確定する。
- 文末のない長文は短い安定待ちを置き、最後に変化した位置より前だけを確定する。
- assistant生成完了時、安定した残りを最終チャンクとして確定する。
- Markdown記号や空白だけの残片は破棄する。
- 極端に短い最終残片は直前未再生チャンクへ結合できる。
- ChatGPT生成停止時は、すでに安定確定した範囲だけを使用する。
- Regen開始時は旧assistantメッセージの未再生範囲を破棄する。

### パイプライン

- チャンク1を生成する。
- チャンク1が品質ゲートを通過したら再生する。
- チャンク1再生中にチャンク2を生成する。
- チャンク1終了直後、所有権と世代を再確認してチャンク2を再生する。
- GPU生成は単一ワーカーで直列化する。
- 最大先読み数を設定し、初期値は2チャンクとする。
- 音声再生と次チャンク生成の重なりがGPUまたは音声品質を悪化させる場合は、計測に基づいて先読み数を1へ落とす。

## 12. フェーズとゲート

前フェーズのテストが通る前に次へ進まない。

### フェーズ0: 基線固定

- 本計画書と基点SHAを確認する。
- 通常ツリーが未変更のままであることを再確認する。
- worktree内で既存テストを実行し、開始時点の失敗を記録する。
- `30`と実験フォルダの変更前スナップショットを読み取り専用で記録する。

ゲート:

- `npm run check:architecture`
- `npm run test:ci`
- 基線失敗がある場合、実装由来と混同しないよう記録する。

### フェーズ1: ターン制御・状態機械・共通interrupt

- 会話ターン、cancel epoch、所有権、状態遷移のcoreを追加する。
- ブラウザ即時停止とAPI interruptを接続する。
- `sounddevice.stop()`とstop eventを即時発火する。
- Stop、入力、貼り付け、送信、生成停止、録音開始、Next、Regenを接続する。

ゲート:

- PythonとJavaScriptの新規単体テスト
- `npm run check:architecture`
- 該当既存テスト

### フェーズ2: 論理キャンセルと安全なキュー

- 生成ジョブへturn、generation、cancel epochを付与する。
- 専用キューのinvalidate、clear、closeを実装する。
- stale WAV削除、Replay除外、状態上書き防止を実装する。

ゲート:

- 生成中interrupt
- 待機キュー破棄
- stale結果破棄
- close競合・連続interrupt
- 該当既存テスト

### フェーズ3: TTSプロファイル

- `speed`、`balanced`、`bridge`を実装する。
- 旧設定移行とHTTP拒否を実装する。
- Suguha参照を維持する。

ゲート:

- 12、16、32の解決テスト
- 2、3、4、6、8の拒否テスト
- Irodori既存テスト

### フェーズ4: 品質ゲート

- WAV解析、判定、削除、エラー状態を実装する。
- 合成結果とReplay更新の間に品質ゲートを置く。

ゲート:

- 正常WAV通過
- 各異常指標の拒否
- 不合格時の削除・Replay除外
- 後続会話継続

### フェーズ5: 文単位ストリーミング

- assistant DOMの安定チャンク抽出を実装する。
- turn内順序、重複排除、先読み上限を実装する。
- マイク会話の現在ターンだけLive経路へ接続する。

ゲート:

- 句点・改行・括弧境界
- URL、コード、数値途中の除外
- 未完成末尾の未送信
- 最終残片
- 重複排除
- turn変更時のstale拒否

### フェーズ6: 復旧・複数タブ・既存機能統合

- Service Worker再起動
- API再起動
- 複数タブ所有権
- Auto、Next、Regen、Replayとの統合

ゲート:

- 復旧後の古い音声再生0件
- 非所有タブの再生拒否
- 通常Autoのプレビュー契約維持
- Next、Regen、Replay回帰
- `npm run test:ci`を2回連続成功

### フェーズ7: E2Eと性能計測

- mock E2Eをミュートで実行する。
- BraveまたはChromeの専用テストプロファイルを使う。
- 最終実機確認だけ短いSuguha音声を使う。
- 実測値とボトルネックを報告する。

ゲート:

- 要求E2E 10項目
- 実測ログ
- 公開衛生チェック
- 最終diffレビュー

## 13. テスト計画

### Python単体テスト

1. 再生中interruptでstop eventが発火する。
2. 待機キューが破棄または即時無効化される。
3. 生成完了後に世代が古ければ再生されない。
4. 古い生成結果が`lastAudioPath`を上書きしない。
5. キャンセル済みWAVが削除される。
6. 品質ゲート不合格WAVが再生されない。
7. 12、16、32-stepが正しい設定へ解決される。
8. 2、3、4、6、8-stepが拒否される。
9. 連続interruptが冪等である。
10. close中のinterruptでデッドロックしない。
11. stale完了が`phase=idle`へ戻さない。
12. 品質ゲート失敗後に次ジョブを処理できる。

### JavaScript単体テスト

1. `beforeinput`でinterrupt要求する。
2. 貼り付けでinterrupt要求する。
3. 送信ボタン`pointerdown`でinterrupt要求する。
4. 生成停止ボタン`pointerdown`でinterrupt要求する。
5. 通常文字キーでinterrupt要求する。
6. 修飾キーのみでは停止しない。
7. 確定済み文チャンクを重複送信しない。
8. ストリーミング中の未完成文を送信しない。
9. URL、コード、小数途中を分割しない。
10. 会話ターン変更後の古い再生を拒否する。
11. Service Worker再起動後も古い音声を再生しない。
12. 複数タブで再生所有権が壊れない。
13. 通常Autoは冒頭プレビュー1回のままである。

### E2E

1. 音声再生中に入力欄へ文字入力し、停止する。
2. 音声再生中に送信ボタンを押し、停止する。
3. 音声再生中に録音ホットキーを押し、停止後に録音開始する。
4. 長文返答で最初の確定文から読み上げを開始する。
5. 再生中に次チャンクを先行生成する。
6. 割り込み後、古いチャンクが後から流れない。
7. Stop連打でも例外がない。
8. 複数タブで古いタブの音声が割り込まない。
9. Service Worker再起動後に古い音声が復活しない。
10. API再起動後に状態が矛盾しない。
11. 通常Auto、Next、Regen、Replayが既存契約どおり動く。

### 実行コマンド

最低限:

```text
npm run check:architecture
npm run test:python
npm run test:background
npm run test:e2e:mock
npm run check:public
npm run test:ci
```

復旧、永続化、キュー変更を含むため、最終`npm run test:ci`は2回連続成功を必須とする。

## 14. 性能計測

### 構造化イベント

- `assistant_chunk_stable`
- `tts_enqueued`
- `tts_generation_started`
- `tts_generation_completed`
- `quality_gate_completed`
- `playback_started`
- `playback_completed`
- `interrupt_input_detected`
- `stop_command_sent`
- `playback_silence_confirmed`
- `recording_started`
- `stale_output_discarded`

各イベントに`traceId`、`turnId`、`generationId`、`playbackId`、`chunkIndex`、`cancelEpoch`、結果、文字数、テキストハッシュを含める。本文全文は含めない。

### 時計

- JavaScript: `performance.now()`と`Date.now()`
- Python: `time.perf_counter_ns()`と`time.time_ns()`
- プロセス内時間はmonotonicで測る。
- ブラウザとPythonをまたぐ時間はwall clockを使い、開始前にloopback pingで時計差を記録する。
- 停止確認はAPI応答時刻ではなく、HTMLAudio停止確認またはsounddevice再生停止確認を使う。

### 集計

- GPUウォームアップ前と後を分ける。
- 原則20回測定する。
- 中央値、p95、最大値を報告する。
- 成功回だけに限定せず、失敗・stale破棄数も報告する。

### 目標

- ユーザー操作から停止要求発行: 50ms以内
- ユーザー割り込みから無音: 通常150ms以内
- stable textから最初の音: 温まったGPUで1.5秒以内
- チャンク間無音: 250ms以内
- 古い音声再生: 0件

目標未達時は、数値を合格扱いせず、実測値、主要ボトルネック、次の改善案を報告する。

## 15. 文書更新

実装と一致するよう、最終フェーズで次を更新する。

- `ARCHITECTURE.md`
- `docs/operation.md`
- `docs/irodori.md`
- 必要な設定例
- `CHANGELOG.md`

`README.md`は通常利用者向けの公開UXに変更が必要な場合だけ更新する。実験フォルダを本番依存先として記載しない。

## 16. 最終判定

最終報告は次のいずれかで始める。

- `completed`: 実装、単体テスト、回帰テスト、E2E、実測が完了した。
- `implemented_but_verification_blocked`: 実装と自動テストは完了したが、ログイン、実機、GPU、音声デバイスなどリポジトリ外要因で一部E2E・実測だけが未実行。
- `incomplete`: 実装または必須自動テストに未完了がある。
- `stopped_for_safety`: 安全な基点や作業境界を維持できない。

未実行、失敗、目標未達を成功として扱わない。

## 17. 最終報告項目

- 実装した構成
- 新規・変更ファイル
- 割り込みの経路
- 生成キャンセルの仕組み
- 採用TTS設定
- 品質ゲート内容
- テスト結果
- 実測遅延
- 未達の目標と理由
- 残タスク
- 通常作業ツリーが未変更である確認
- `30`と実験フォルダが未変更である確認
- 実装コードのcommit、push、PRを行っていない確認
- 共有スキル更新判定: `updated` / `not_needed` / `blocked`

## 18. 本計画コミット後の運用

- 本ファイルのコミットを実装契約の基準点とする。
- 実装中はフェーズごとのチェック結果、測定値、設計変更理由を本ファイルまたは専用記録へ追記する。
- 計画から外れる必要が生じた場合、理由と影響を先に記録する。
- この計画コミットは実装コードのcommit許可を意味しない。
- ユーザーの追加指示があるまで、実装コードは専用worktree内の未コミット差分として完成・検証する。
