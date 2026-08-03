# GPT Live風・低遅延会話 改修計画

## 1. 目的と完了条件

### 元の目的

Local Voice Bridgeで、Suguhaの声を維持しながら、GPT Liveに近いテンポの低遅延・割り込み可能な音声会話を実現する。

### 最終完了条件

次のすべてが成立した場合だけ、実装作業を`completed`と判定する。

1. 専用worktree内だけで実装されている。
2. 通常作業ツリーを変更していない。
3. 読み取り専用のローカルTTSサービスとIrodori実験フォルダを変更していない。
4. TTSプロファイルとして12、16、32-stepだけを利用できる。
5. `speed`と`balanced`がSuguha参照latent、GPU段階同期OFF、ウォーターマークOFF、ジョブごとのCUDAキャッシュ解放OFFを含む完全な設定として動く。
6. 2、3、4、6、8-stepなど未承認の設定をUI、設定、HTTP APIから選択できない。
7. 生成音声ごとに校正済みの再生前品質ゲートがある。
8. 文字入力、貼り付け、送信、ChatGPT生成停止、録音開始、既存Stopで音声が停止する。
9. 割り込み後の古い生成音声・待機音声が再生されない。
10. マイク質問と対象assistantメッセージが一意に対応し、同一タブの手動送信やRegenを誤ってLive再生しない。
11. マイク会話の返答で、文単位の先行生成・順次再生が動き、再生中に次チャンクを生成できる。
12. STT優先のGPU調停が動き、検証中に意図しないSTT CPUフォールバックがない。
13. 既存のAuto、Next、Regen、Replay、マイク会話を壊さない。
14. 単体テスト、既存回帰テスト、要求されたE2Eが通る。
15. 全経路の実測遅延を構造化ログと最終報告に残し、定義した目標値を満たす。
16. `git diff`をレビューし、関係ない変更がない。
17. 実装コードのcommit、push、PRは、ユーザーの追加指示があるまで行わない。

## 2. 計画基点と安全境界

### 基点

- 計画・実装worktreeは、実装開始時の`git rev-parse --show-toplevel`で一意に解決する。計画書へ端末固有の絶対パスを保存しない。
- 通常作業ツリーは`git worktree list --porcelain`とローカル実行コンテキストから特定し、実装worktreeと別ディレクトリかつ同じGit common directoryであることを検証する。
- 候補が0件または複数で一意に決められない場合は、推測せず`stopped_for_safety`とする。
- 解決した絶対パスはGit管理外の`.ai-bridge/gpt-live-low-latency-local-baseline.json`だけへ記録し、commit対象にしない。
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

- `LOCAL_TTS_SOURCE_ROOT`: 既存Irodori Python環境、上流コード、モデル、参照音声の読み取り元
- `IRODORI_FAST_LAB_ROOT`: 12、16、32-stepと品質監査結果を確認する実験フォルダ

上記の絶対パスはローカル実行コンテキストまたはGit管理外のbaseline記録から解決し、公開文書やcommitへ保存しない。存在、用途、読み取り専用指定を一意に確認できなければ`stopped_for_safety`とする。

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
5. GPU推論は単一生成ワーカーで直列化し、再生制御は別ワーカーまたは別実行経路へ分離する。
6. 状態・世代・所有権の純粋ロジックは専用モジュールへ分離する。
7. `background.js`、`server.py`、`control_state.py`は調整層のまま保つ。
8. 生成結果は、生成開始前、生成完了後、保存後、品質ゲート後、再生直前に有効性を確認する。
9. 古い非同期完了は、新しい状態やReplay対象を上書きできない。
10. 割り込みは何度呼ばれても安全な冪等操作にする。
11. ログに本文全文や音声内容を残さない。文字数、ハッシュ、ID、時刻、結果だけを記録する。
12. STTを実行する通知領域プロセスとTTSを実行するAPIプロセスは別OSプロセスであるため、GPU排他を通常の`threading.Lock`だけで実装しない。
13. マイク送信とassistant返答の関連付けは、DOM出現順だけで決めず、実送信前に永続化した`submissionId`を起点にする。
14. 関連付け、所有権、世代、復旧状態が曖昧な場合はLive再生しない。推測によるフォールバックを行わない。

## 5. 予定する責務分割

ファイル名は既存命名と設計ゲートに合わせて実装時に最終決定する。次は責務の予定であり、単一巨大モジュールにはまとめない。

### 拡張機能側

- `extension/conversation-turn-core.js`
  - `turnId`、`submissionId`、`generationId`、`playbackId`、`cancelEpoch`
  - `pageInstanceId`と送信前assistant基線
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
- `extension/prompt-input-core.js`
  - マイク文字起こしの挿入、送信猶予、実送信の調整
  - 実送信ごとの`submissionId`発行
  - `button.click()`前の送信関連付け永続化要求とACK確認
  - ACK失敗時は送信せず、入力欄を安全に残すか明示的に失敗する
  - 内部の文字起こし挿入が発生させるsynthetic `input`を、同じ`submissionId`に限った短い抑止scopeで識別し、自分自身のinterruptとして扱わない
  - 抑止scope中でも実ユーザーのキー入力、貼り付け、別内容への変更は検出して送信候補を失効させる
- `extension/content.js`
  - DOM監視とユーザー入力イベント接続のみ
  - `beforeinput`、`paste`、通常文字キー、送信・生成停止ボタン`pointerdown`の即時interrupt通知
  - HTMLAudioElementが存在する場合の即時停止・Object URL解放
  - `pageInstanceId`をページ読込ごとに生成し、送信とassistant候補へ付与
- `extension/background.js`
  - 専用coreの接続、API呼び出し、永続化のみ
  - `mic-submit-armed`を永続化できた場合だけcontent側へ送信許可ACKを返す

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
  - GPU生成ジョブを単一ワーカーで直列化する
- `local-api/tts_profiles.py`
  - `speed`、`balanced`、`bridge`の完全な設定解決
  - 旧設定の安全な正規化
  - 未承認stepの拒否
- `local-api/irodori_engine.py`
  - 解決済みプロファイルをSamplingRequestとIrodoriランタイムへ適用
  - Suguha参照latentの生成、検証、キャッシュ再利用
  - GPU段階同期、ウォーターマーク、CUDAキャッシュ解放方針の切替
  - 上流が正式な設定引数を持つ場合はそれを優先し、モジュールグローバルや`runtime.watermarker`の一時変更は専用context managerと生成ロック内だけで行う
  - 成功、例外、interruptのすべてで一時変更を`finally`復元する
- `local-api/installation_identity.py`
  - `APP_ROOT`から既存互換の`INSTANCE_ID`を一度だけ計算し、server、tray、conversation controller、GPU arbiterで共有する
  - 同じインストールは同じID、別パスのインストールは別IDになることを保証する
- `local-api/gpu_arbiter.py`
  - APIプロセスと通知領域プロセスで共有するWindows名前付きGPU Mutexをハード排他にする
  - 同じinstallation `INSTANCE_ID`からGPU Mutex名とSTT優先Gate Mutex名を決定し、別インストールとは干渉しない
  - STTはGate Mutexを保持したままGPU Mutexを待ち、待機開始後の新規TTS取得を遮断する
  - TTSはGate Mutex取得中にGPU Mutexをnon-blockingで試し、取得できなければGateを解放してcancel-aware backoff後に再試行する
  - 現在実行中のTTSは強制停止せず完了後にGPU Mutexを解放し、結果は世代検証で破棄できる
  - プロセス終了時はWindowsカーネルのabandoned MutexとしてGateとGPU所有権を回収でき、永続的なset状態を残さない
  - 非Windows単体テストでは同じ契約の注入可能なin-process実装を使う
  - 待機時間、利用者、デバイス、フォールバックの計測
- `local-api/conversation_controller.py`
  - STT開始前にSTT優先Gate Mutexを取得し、そのままGPU Mutexを取得する。GPU取得後にGateを解放し、STT終了時にGPU Mutexを`finally`で必ず解放する
  - Live経路ではCUDA失敗時にCPUへ自動フォールバックせず、明示的なエラー状態へ遷移する
  - CPU STTは明示的な診断設定でだけ許可し、通常Liveの成功条件には含めない
- `local-api/voice_playback.py`
  - 合格済みWAVの順次再生
  - `playbackId`、順序、停止、完了通知
  - GPU生成ワーカーを占有せずに再生する
- `local-api/audio_quality.py`
  - WAV読込と品質指標
  - 判定結果と理由
  - 安全な削除
- `local-api/voice_metrics.py`
  - 構造化イベント
  - turn、generation、playback、chunk、traceの相関
  - 本文を保存しない
- `local-api/voice_runtime.py`
  - 単一GPU生成ワーカー
  - 生成と品質ゲートを直列実行し、合格音声を再生キューへ引き渡す
  - 再生制御は生成ワーカーから分離し、再生中にも次チャンクを生成できるようにする
  - 各境界で世代確認
  - interrupt時の即時`stop_event`と`sounddevice.stop()`
- `local-api/server.py`
  - `/v1/conversation/submission`、`/v1/live/chunks`、`/v1/interrupt`、`/v1/live/state`のHTTP接続だけを追加
  - 既存`/v1/speak`の同期契約は通常Auto、Next、Regen、Replay向けに維持する
  - マイクLive経路では生成完了と再生完了を同じHTTP応答で待たない
  - 状態、冪等性、キュー、品質ロジックを直接持たない

### 既存永続モジュール

- `browser_runtime_state.py`と`background-runtime-core.js`は、復旧に必要な最小限のターン・所有権・世代・送信関連付けスナップショットだけを扱う。
- 通常Autoの待機キューと既存復旧契約は維持する。
- 未完了のLiveターンは、Service WorkerまたはAPIの再起動時に失効させる。送信済みpromptへのassistant返答が後から表示されても、自動でLiveにも通常Autoにも流さない。
- 古い`currentItem`を復元して再生できるのは通常Auto、Next、Regen、Replayの既存互換経路だけとし、Liveの生成中・再生中チャンクは自動復元しない。
- 「再生中だった」「waiting_for_assistantだった」という永続値だけを根拠にLive処理を再開しない。
- 復旧時は`cancelEpoch`を進め、古いAPI生成結果とブラウザ通知を拒否する。

### architectureゲート更新

- `scripts/check-architecture.js`へ新しいfocused moduleの存在と主要importを追加する。
- Windows named Mutex、STT優先Gate、Irodori一時override、送信関連付け状態の実装を`server.py`、`control_state.py`、`background.js`へ重複させない。
- `server.py`、`background.js`、`conversation_controller.py`の責務・行数が増えすぎる場合は、ゲートを緩めず専用moduleへ抽出する。
- `installation_identity.py`導入後も既存`/health`の`instanceId`が同一値であることをテストする。

## 6. IDと所有権モデル

各処理は最低限、次を持つ。

- `traceId`: ブラウザからAPI、生成、再生までを相関するランダムID
- `tabId`: ChatGPTタブ
- `pageInstanceId`: content scriptのページ読込単位。再読込・遷移で更新
- `conversationKey`: ChatGPT会話を識別できる範囲の安定キー
- `assistantMessageKey`: assistantメッセージ単位のキー
- `sessionId`: 録音開始から文字起こし配送まで使う既存マイクセッションID
- `turnId`: 新しい明示的会話操作ごとに更新
- `submissionId`: マイク文字起こしの実送信操作ごとに発行するランダムID
- `generationId`: TTS生成ジョブごとに更新
- `playbackId`: 再生ごとに更新
- `chunkIndex`: ターン内の順序
- `cancelEpoch`: interruptごとに単調増加
- `interruptReason`: 入力、貼り付け、送信、生成停止、録音開始、Stop、Next、Regenなど

### マイク質問とassistant返答の対応付け

1. 録音開始時に`sessionId`、`turnId`、対象`tabId`、`pageInstanceId`、`conversationKey`を確定する。
2. 文字起こし挿入後、実送信ごとに`submissionId`を発行し、送信直前の最新`assistantMessageKey`、assistant要素数、文字起こしの長さとハッシュを含む`mic-submit-armed`を作る。本文は保存しない。
3. content側は`button.click()`より前に`mic-submit-armed`をbackgroundへ送り、backgroundとローカルAPIの永続化ACKを待つ。ACKを得られなければChatGPTへ送信しない。
4. ACK後にだけ送信ボタンをクリックし、`mic-submit-committed`を記録して`waiting_for_assistant`へ進む。
5. 同じ`tabId + pageInstanceId + conversationKey`で、committed後かつ送信前基線より新しく作成されたassistantメッセージを候補にする。
6. 候補が1件に確定した場合だけ、`sessionId + turnId + submissionId + assistantMessageKey`を関連付けてLive対象にする。
7. 候補が複数ある、順序が逆転した、送信前から存在した要素しかないなど曖昧な場合は失敗扱いにしてLive再生しない。
8. 手動送信、Enter送信、Regen、ページ遷移、タブ再読込、別ターン開始、別の`submissionId`が先に起きた場合は未確定候補を失効させる。
9. 内部の文字起こし挿入が発生させるsynthetic `input`は、同じ`submissionId`の挿入scope内だけinterrupt対象外とする。実ユーザーの入力・貼り付け・内容変更は常に候補を失効させる。
10. `mic-submit-armed`、`committed`、`bound`は`submissionId`単位で冪等にする。同一payloadの再送は同じ結果を返し、異なるpayloadで同じIDを再利用した場合は409相当で拒否する。
11. `armed`のまま30秒以内に`committed`されない記録は期限切れとして`invalidated`へ移す。クリック後に`committed`保存前で停止・再起動した場合も推測せず失効させる。
12. `assistantMessageKey`はDOMの安定IDを優先し、存在しない場合だけ`pageInstanceId + assistant ordinal + 作成基線`でページ内IDを作る。本文ハッシュだけをメッセージIDにしない。
13. DOM仮想化、並べ替え、ID再利用などで同一性を証明できない場合はLive対象にしない。
14. `waiting_for_assistant`で同じタブに新しいDOMが現れたという条件だけではLive対象にしない。
15. Service WorkerまたはAPI再起動時は未完了の送信関連付けを失効させ、復元してLive再生しない。通常Autoへも自動フォールスルーしない。

### 再生所有権

- 音声出力は拡張機能全体で1所有者とする。
- 新しい明示的会話操作は所有権を奪取する。
- マイク会話の現在ターンは通常Autoより優先する。
- 非所有タブの古い`play-audio`通知は拒否する。
- 通常経路の所有権は`tabId + conversationKey + turnId + cancelEpoch`、Live経路はさらに`pageInstanceId + submissionId`を含めて検証する。
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

録音開始は「音声出力が止まったこと」を待つが、「論理キャンセル済みTTS生成がGPU上で終了したこと」は待たない。再生停止確認後にマイク入力を開始し、録音終了後のSTT段階でGPU Mutexを待つ。これにより、古いTTS生成中でも録音開始500ms目標を維持する。

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

再生中に次チャンクを生成するため、単一の直列phaseへ押し込まず、API側で次の直交状態を正本として持つ。

### 会話phase

- `idle`
- `listening`
- `recording`
- `transcribing`
- `submitting`
- `waiting_for_assistant`
- `responding`
- `interrupted`
- `error`

### 送信関連付けphase

- `idle`
- `arming`
- `persisted`
- `committed`
- `bound`
- `invalidated`
- `error`

### TTS生成phase

- `idle`
- `generating`
- `ready`
- `error`

### 再生phase

- `idle`
- `playing`
- `stopping`
- `error`

`conversationPhase=responding`へ進めるのは`submissionPhase=bound`のLiveターンだけとする。その間は`generationPhase=generating`と`playbackPhase=playing`が同時に成立してよい。先読みキュー数と再生待ちチャンク数はphaseとは別の数値で持つ。

### 主要遷移

- `idle -> listening -> recording`
- `recording -> transcribing -> submitting`
- `submitting`中に`submissionPhase: idle -> arming -> persisted -> committed`と進み、永続化ACK失敗時は`error`へ進んで送信しない
- 実送信後に`submitting -> waiting_for_assistant`
- 対象assistantメッセージを一意に確定した場合だけ`submissionPhase: committed -> bound`、`waiting_for_assistant -> responding`へ進む
- 曖昧化、手動送信、Regen、遷移、再読込、再起動時は`submissionPhase -> invalidated`とし、Liveへ進めない
- `responding`中に生成phaseと再生phaseが独立して遷移する
- 全確定チャンクの生成・再生完了後に`responding -> idle`
- `responding -> interrupted -> recording`
- `error -> idle`または新しい録音開始

### 状態更新規則

- すべての非同期更新に`turnId`と`cancelEpoch`を要求する。
- Live送信後の更新には`submissionId`、`pageInstanceId`、`conversationKey`も要求する。
- 生成更新には`generationId`、再生更新には`playbackId`も要求する。
- 現在値と一致しない更新を無視する。
- `submissionPhase`が`bound`でないLive更新は生成・再生へ進めない。
- 古い完了通知は会話phase、送信関連付けphase、生成phase、再生phaseを巻き戻せない。
- 生成完了だけで会話phaseを`idle`へ戻さず、未再生・生成中・未確定チャンクがないことを確認する。
- `error`は次ターン開始を妨げない。
- 再起動復旧時は未完了Liveの`submissionPhase`を`invalidated`へ正規化し、`playbackPhase=playing`を維持しない。
- UI表示用状態は正本の直交状態から投影し、別の独立状態機械を作らない。

## 9. TTSプロファイル

### `speed`

- `numSteps: 12`
- Suguha参照はWAVを毎回再エンコードせず、事前計算した参照latentを安全に再利用する
- 参照latentのキャッシュキーは参照WAVの絶対パス、サイズ、更新時刻、codec識別子、codec精度、正規化条件を含める
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
- 低遅延経路ではGPU段階タイミング計測のための同期を無効にする
- 低遅延経路ではウォーターマークを無効にする
- ジョブごとの`gc.collect()`と`torch.cuda.empty_cache()`を行わない

### `balanced`

- `numSteps: 16`
- その他は`speed`と同条件

### `bridge`

- `numSteps: 32`
- Suguha WAV参照を使用する
- `tScheduleMode: sway`
- `swayCoeff: -1.0`
- `cfgScaleText: 3.0`
- `cfgScaleSpeaker: 6.0`
- `decodeMode: sequential`
- `contextKvCache: true`
- `seed: 10`
- 既存のGPU段階同期、ウォーターマーク、WAV参照動作を維持する
- 既存互換経路では従来どおり未使用CUDAキャッシュ解放を許可する

### プロファイル実装契約

- Suguha参照時に`referenceNumSteps`が常に優先される現行分岐をそのまま使わず、プロファイルが12、16、32-stepを明示的に決定する。
- `speed`と`balanced`は参照latent、GPU段階同期OFF、ウォーターマークOFFを含む一体の設定として扱う。
- 単に`numSteps`だけを変更した実装をプロファイル完了と判定しない。
- 参照latentは起動時またはRef選択時に準備し、最初のLive発話でキャッシュ生成を待たせない。
- キャッシュ破損、参照WAV変更、codec変更時は安全に再生成する。
- キャッシュ生成失敗時はSuguha以外の声へ黙って切り替えず、Live経路を失敗させて明示的な状態を返す。

### 既定値と移行

- `speed`、`balanced`、`bridge`は音声・モデル名ではなく、Irodori最適化プロファイルとして扱う。
- マイク会話の新しいLive経路は`speed`を既定値にする。
- 通常Auto、Next、Regen、Replayは既存の生成条件を維持し、Suguhaなど有効な参照音声ありは`bridge`、Ref=noneは`balanced`相当へ解決する。
- 日常操作のWindows小窓へ新しいプロファイル選択を追加しない。
- 切替が必要な場合だけ拡張機能の詳細設定に`Live TTS profile`を置き、選択肢は`speed`、`balanced`、`bridge`の3つだけにする。既定は`speed`。
- UI、永続設定、HTTP APIはプロファイル名を主入力とし、任意step数の直接指定を拒否する。
- 既存設定に未承認stepがある場合は、参照音声ありなら`bridge`、Ref=noneなら`balanced`へ安全に正規化し、警告を残す。
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

- ハードブロックを本番経路へ入れる前に、`speed`、`balanced`、`bridge`で閾値を校正する。
- 短文、長文、数字、英字、URL、記号、句読点を含む複数文章と複数seedを使う。
- 正常音声の指標分布と誤拒否率を記録する。
- 正常WAVへクリッピング、DCずれ、無音、差分スパイク、広帯域ノイズなどを人工注入し、各異常を実際に拒否できることを確認する。
- faster-whisperの文字一致率は調査指標として記録するが、信号品質ゲートだけで声質や話者同一性を保証した扱いにしない。
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

- チャンク1を単一GPU生成ワーカーで生成する。
- チャンク1が品質ゲートを通過したら再生キューへ渡し、生成ワーカーを解放する。
- 別の再生制御経路がチャンク1を再生する。
- チャンク1再生中に、単一GPU生成ワーカーがチャンク2を生成する。
- チャンク1終了直後、所有権、`turnId`、`cancelEpoch`を再確認してチャンク2を再生する。
- GPU生成同士は並列実行しないが、音声再生とGPU生成は重ねる。
- Live生成投入APIは生成ジョブ受付または生成完了までを返し、再生完了まではブロックしない。
- 最大先読み数を設定し、初期値は2チャンクとする。
- 音声再生と次チャンク生成の重なりがGPU、音声品質、割り込み性能を悪化させる場合は、計測に基づいて先読み数を1へ落とす。

### Live HTTP契約

既存`/v1/speak`、`/v1/playback/replay`、`/v1/playback/stop`は通常Auto、Next、Regen、Replayの互換契約として維持する。Live専用経路だけを分離する。

- `POST /v1/conversation/submission`
  - `action: arm | commit | bind | invalidate`
  - `sessionId`、`turnId`、`submissionId`、`tabId`、`pageInstanceId`、`conversationKey`、`cancelEpoch`を必須にする。
  - `arm`は送信前assistant基線、assistant要素数、文字数、テキストハッシュを原子的に永続化し、その後にだけ送信許可ACKを返す。
  - 状態遷移は`conversation_turn.py`が検証し、同じ`submissionId`と同じpayloadの再送を冪等に受理する。
  - 不正遷移、異なるpayloadでのID再利用、stale世代は`409 Conflict`で拒否する。
- `POST /v1/live/chunks`
  - 上記IDに加え、`assistantMessageKey`、`generationId`、`chunkIndex`、`text`、`textHash`、`isFinal`、`profile`を必須にする。
  - `submissionPhase=bound`かつ現在の所有権・世代と一致する場合だけ受理する。
  - 同じ`generationId + chunkIndex + textHash`は冪等に1回だけenqueueする。内容が異なる重複は409で拒否する。
  - enqueue成功時は`202 Accepted`を返し、生成完了・再生完了を待たない。
  - 先読み上限を超えた場合は`429 Too Many Requests`と`retryAfterMs`を返す。ブラウザは同じturnと世代が有効な間だけbounded retryする。
- `POST /v1/interrupt`
  - `turnId`、`cancelEpoch`、`reason`を検証し、cancel epoch更新、待機ジョブ無効化、stop event、`sounddevice.stop()`を同期的に発行する。
  - 実行中TTS生成の終了は待たず、停止要求を受理した時点で応答する。
  - 同じまたは古いinterruptの再送は冪等に処理し、状態を巻き戻さない。
- `GET /v1/live/state`
  - 会話、送信関連付け、生成、再生phase、現在ID、キュー数、先読み余力、最終エラーを返す。
  - 本文全文、ローカル絶対パス、参照音声ファイル内容は返さない。

`server.py`はルーティングとJSON境界だけを担当し、状態遷移、冪等性、キュー、品質判定を直接実装しない。Live音声はローカルAPI側で再生し、ブラウザ`play-audio`は旧API互換時だけ使用する。

### STTとTTSのGPU調停

- faster-whisperは通知領域プロセス、IrodoriはAPIプロセスで動くため、プロセス内Lockだけでは排他できない。
- Windows名前付きGPU Mutex `Local\\LocalVoiceBridgeGpu-<INSTANCE_ID>`をGPU実行のハード排他にする。
- Windows名前付きGate Mutex `Local\\LocalVoiceBridgeGpuSttGate-<INSTANCE_ID>`をSTT優先のturnstileにする。
- STTは文字起こし開始前にGate Mutexを取得し、そのGateを保持したままGPU Mutexを待つ。GPU取得後にGateを解放してCUDA STTを実行する。
- TTSはGate Mutexを取得した状態でGPU Mutexをnon-blocking取得する。取得できなければGateをすぐ解放し、cancel-aware backoff後に再試行する。
- STTがGateを取得した後は新規TTSがGPUを先取りできない。すでにGPUを所有するTTSだけは安全に完了させる。
- すでに実行中のTTSはCUDAカーネルを強制停止せず、完了後にGPU Mutexを解放する。cancel済み結果は保存後・品質ゲート前・再生前の世代検証で破棄する。
- STTはCUDA faster-whisperだけを実行し、成功・失敗を問わず`finally`でGPU Mutexを解放する。GPU取得timeout時はGateも必ず解放する。
- プロセス異常終了時はWindowsカーネルのabandoned Gate/GPU Mutexを検出して回収し、所有者、待機時間、abandoned状態をログへ残す。
- 通常LiveではSTT CUDAを必須とする。CUDA準備失敗、VRAM不足、推論失敗をCPU成功へ置き換えない。
- GPU待機時間、VRAM、STTデバイス、TTSプロファイル、abandoned回収、CPUフォールバック試行数を構造化ログへ残す。
- GPU競合下でも録音開始、文字起こし、次ターン開始の遅延目標を満たすことを実機ゲートにする。

## 12. フェーズとゲート

前フェーズのテストが通る前に次へ進まない。

### フェーズ0: 基線固定

- 本計画書と基点SHA、実パスを確認する。
- 通常ツリーが未変更のままであることを再確認する。
- `npm ci`でworktree内の開発依存を復元する。npmキャッシュをリポジトリ内や`=\\npm-cache`へ作らない。
- worktree内で既存テストを実行し、開始時点の失敗を記録する。
- 読み取り専用のローカルTTSサービスとIrodori実験フォルダの変更前スナップショットを記録する。
- 修正前確認では公開衛生、architecture、Python、backgroundは成功し、mock E2Eは`@playwright/test`未導入で停止した。この状態を合格基線にせず、`npm ci`後に全CIを通し直す。

ゲート:

- `npm ci`
- `npm run check:architecture`
- `npm run test:ci`
- mock E2Eを含む全工程が成功して初めて基線固定とする。
- 依存復元後も失敗する場合だけ、実装前からの基線失敗としてコマンド、終了コード、ログを記録する。

### フェーズ1: ターン制御・状態機械・共通interrupt

- 会話ターン、cancel epoch、所有権、状態遷移のcoreを追加する。
- ブラウザ即時停止とAPI interruptを接続する。
- `sounddevice.stop()`とstop eventを即時発火する。
- Stop、入力、貼り付け、送信、生成停止、録音開始、Next、Regenを接続する。
- 録音開始判定を再生停止とTTS生成phaseから分離し、音声が無音なら論理キャンセル済み生成の完了を待たずに録音する。

ゲート:

- PythonとJavaScriptの新規単体テスト
- TTS生成継続中でも再生停止後500ms以内に録音を開始できる
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

- `speed`、`balanced`、`bridge`を一体の設定契約として実装する。
- `speed`と`balanced`へ参照latentキャッシュ、GPU段階同期OFF、ウォーターマークOFF、ジョブごとのCUDAキャッシュ解放OFFを実装する。
- 参照latentを起動時またはRef選択時に事前準備する。
- Suguha参照時にもプロファイル指定の12、16、32-stepが確実に使われるよう現行`referenceNumSteps`分岐を置き換える。
- 上流の正式な設定口を優先し、必要な一時ランタイム変更は専用context managerと生成ロックで隔離する。
- 旧設定移行とHTTP拒否を実装する。
- Suguha参照を維持し、キャッシュ失敗時に別音声へ黙ってフォールバックしない。

ゲート:

- 12、16、32の解決テスト
- Suguha参照時に12、16、32が実際のSamplingRequestへ入るテスト
- latentキャッシュのhit、invalidate、破損復旧テスト
- speed/balancedで同期OFF、ウォーターマークOFF、毎回のCUDA解放なしを確認するテスト
- 成功時、合成例外時、interrupt時に一時変更が元へ戻るテスト
- 2、3、4、6、8の拒否テスト
- Irodori既存テスト

### フェーズ4: プロセス間GPU調停

- `INSTANCE_ID`単位のWindows名前付きGPU MutexとSTT優先Gate Mutexを実装する。
- TTSのGate取得 -> GPU non-blocking取得 -> Gate解放 -> backoff再試行を実装する。
- STTのGate取得 -> GPU待機取得 -> Gate解放 -> transcribe -> GPU解放を実装する。
- Live STTの自動CPUフォールバックを廃止し、CUDA失敗を明示的エラーにする。
- abandoned Gate/GPU Mutex、timeout、連続録音、終了処理を実装する。

ゲート:

- 別プロセス相当の競合テスト
- STTがGateを取得した後に新規TTSがGPUを先取りしない
- 実行中TTS完了後にSTTが先に取得する
- STT成功・失敗・例外・timeoutでGateとGPU Mutexが解放される
- abandoned Gate/GPU Mutexを回収できる
- CUDA失敗時のCPUフォールバック0件

### フェーズ5: 品質ゲート

- まず正常音声分布と人工異常音声で閾値を校正する。
- 校正結果を固定してからWAV解析、判定、削除、エラー状態を実装する。
- 合成結果とReplay更新の間に品質ゲートを置く。

ゲート:

- 正常WAV通過と誤拒否率の記録
- クリッピング、DCずれ、無音、差分スパイク、広帯域ノイズの拒否
- 各異常指標の境界値テスト
- 不合格時の削除・Replay除外
- 後続会話継続

### フェーズ6: 送信関連付けと文単位ストリーミング

- `submissionId`を実送信前に永続化し、ACK後にだけChatGPTへ送信する。
- assistant DOMの安定チャンク抽出を実装する。
- `sessionId + turnId + submissionId + assistantMessageKey`でマイク質問への返答を一意に確定する。
- turn内順序、重複排除、先読み上限を実装する。
- 単一GPU生成ワーカーと別再生制御経路を接続し、再生中の先読み生成を実装する。
- マイク会話の確定済み現在ターンだけLive経路へ接続する。

ゲート:

- 永続化ACK失敗時に送信しない
- 内部synthetic inputで自己interruptせず、実ユーザー変更では失効する
- submission遷移の冪等再送、異なるpayloadの409拒否、30秒expiry
- Live chunkの202非同期受付、重複排除、429 bounded backpressure
- interruptの冪等性と生成完了を待たない応答
- 句点・改行・括弧境界
- URL、コード、数値途中の除外
- 未完成末尾の未送信
- 最終残片
- 重複排除
- マイク質問とassistant返答の一意な対応付け
- 手動送信、Enter送信、Regen、ページ遷移、再読込との誤対応0件
- 複数候補・不安定DOM ID時にfail closedする
- 再生中に次チャンクのGPU生成が開始される
- turn変更時のstale拒否

### フェーズ7: 復旧・複数タブ・既存機能統合

- Service Worker再起動
- API再起動
- 未完了Liveターンの失効
- 通常Autoキューの既存復旧
- 複数タブ所有権
- Auto、Next、Regen、Replayとの統合

ゲート:

- 復旧後の古いLive音声再生0件
- 再起動前の未完了Live返答を通常Autoへ誤投入0件
- 通常Autoの待機キュー復旧を維持
- 非所有タブの再生拒否
- 通常Autoのプレビュー契約維持
- Next、Regen、Replay回帰
- `npm run test:ci`を2回連続成功

### フェーズ8: E2Eと性能計測

- mock E2Eをミュートで実行する。
- BraveまたはChromeの専用テストプロファイルを使う。
- 最終実機確認だけ短いSuguha音声を使う。
- マイク解放からSTT、送信、assistant最初の文字、最初の安定チャンク、TTS、初音までを一つのtraceで測る。
- STTとTTSのGPU競合、VRAM、意図しないCPUフォールバックを測る。
- `speed`、`balanced`、`bridge`を別々に測定し、GPT Live風の合格判定は既定Live `speed`経路で行う。
- 実測値とボトルネックを報告する。

ゲート:

- 要求E2E 17項目
- 全経路の実測ログ
- GPU競合下の実測ログ
- 意図しないSTT CPUフォールバック0件
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
8. Suguha参照時にも12、16、32-stepがSamplingRequestへ反映される。
9. speed/balancedが参照latentを再利用し、キャッシュ変更時だけ再生成する。
10. speed/balancedがGPU段階同期、ウォーターマーク、ジョブごとのCUDAキャッシュ解放を無効にする。
11. Irodori一時ランタイム変更が成功後に復元される。
12. Irodori合成例外とinterrupt後にも一時ランタイム変更が復元される。
13. 2、3、4、6、8-stepが拒否される。
14. 生成ワーカーが再生終了を待たずに次チャンクを処理できる。
15. 再生順序はchunkIndex順を維持する。
16. 連続interruptが冪等である。
17. close中のinterruptでデッドロックしない。
18. stale完了が`phase=idle`へ戻さない。
19. 品質ゲート失敗後に次ジョブを処理できる。
20. STTがGate Mutexを取得した後、新規TTSがGPU Mutexを先取りしない。
21. TTSがGPU Mutexを取得できない場合、Gateを解放してcancel-awareに再試行する。
22. STT成功、推論失敗、timeout、shutdownの各経路でGateとGPU Mutexが解放される。
23. abandoned Gate/GPU Mutexを回収し、次のジョブを処理できる。
24. Live STTのCUDA失敗時にCPU transcribeを呼ばない。
25. STT優先時に未開始の先読みTTSが無効化される。
26. 論理キャンセル済みTTS生成が継続中でも、音声停止確認後は録音開始をブロックしない。
27. `submission arm`の同一payload再送が冪等で、異なるpayloadの同一ID再利用を409で拒否する。
28. `armed`記録が30秒後に`invalidated`へ期限切れになる。
29. Live chunk受付が202を返し、再生完了を待たない。
30. 同一chunkを二重enqueueせず、先読み上限では429と`retryAfterMs`を返す。
31. interrupt再送が冪等で、実行中生成の終了を待たずに応答する。
32. `installation_identity.py`導入前後で既存`/health`の`instanceId`が変わらない。

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
10. 実送信ごとに重複しない`submissionId`を発行する。
11. `mic-submit-armed`の永続化ACKより前に送信ボタンをクリックしない。
12. 永続化ACK失敗時はChatGPTへ送信せず、明示的な失敗を返す。
13. `sessionId + turnId + submissionId + assistantMessageKey`でマイク返答を確定する。
14. 同一タブの手動送信、Enter送信、Regen、古いDOM更新をLive返答へ誤対応しない。
15. 複数assistant候補がある場合はLiveへ結び付けない。
16. ページ遷移またはタブ再読込で`pageInstanceId`が変わり、未確定候補を失効させる。
17. 会話ターン変更後の古い再生を拒否する。
18. Service Worker再起動後に未完了Live関連付けと古い音声を復元しない。
19. Service Worker再起動後も通常Autoの待機キューは既存契約どおり復元する。
20. 複数タブで再生所有権が壊れない。
21. 通常Autoは冒頭プレビュー1回のままである。
22. 内部の文字起こし挿入が発生させるsynthetic `input`は、自分自身のinterruptにならない。
23. 挿入scope中でも実ユーザーのキー入力、貼り付け、別内容への変更は候補を失効させる。
24. `armed`、`committed`、`bound`の同一payload再送を冪等に扱う。
25. 429時のchunk retryは回数・時間がboundedで、turnまたはcancel epoch変更時に停止する。
26. `assistantMessageKey`は安定DOM IDを優先し、本文が同じ別メッセージを同一視しない。
27. DOM仮想化や並べ替えでIDが曖昧な場合はLiveへ結び付けない。
28. 詳細設定のLive TTS profileは`speed`、`balanced`、`bridge`だけを受理し、任意step入力を持たない。

### E2E

1. 音声再生中に入力欄へ文字入力し、停止する。
2. 音声再生中に送信ボタンを押し、停止する。
3. 音声再生中かつTTS生成継続中に録音ホットキーを押し、音声停止後は生成完了を待たず500ms以内に録音開始する。
4. 長文返答で最初の確定文から読み上げを開始する。
5. 再生中に次チャンクを先行生成する。
6. 割り込み後、古いチャンクが後から流れない。
7. Stop連打でも例外がない。
8. 複数タブで古いタブの音声が割り込まない。
9. Service Worker再起動後に未完了Live音声と関連付けが復活しない。
10. API再起動後に未完了Liveターンが失効し、状態が矛盾しない。
11. 再起動後も通常Autoの待機キュー、Next、Regen、Replayが既存契約どおり動く。
12. `mic-submit-armed`の永続化失敗時にChatGPTへ送信しない。
13. 同一タブの手動送信またはEnter送信をマイクLive返答へ誤対応しない。
14. Regen返答、ページ遷移後の返答、再読込後の返答をマイクLive返答へ誤対応しない。
15. 同一送信に複数assistant候補が現れた場合にLive再生しない。
16. 実行中TTSがある状態で録音を開始し、TTS完了後にSTTが次のTTSより先にGPUを取得する。
17. STTとTTSのGPU競合下でも意図しないCPUフォールバックなく次ターンを開始できる。

### 実行コマンド

最低限:

```text
npm ci
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

- `recording_started`
- `recording_stopped`
- `transcription_started`
- `transcription_completed`
- `stt_device_selected`
- `gpu_arbiter_wait_started`
- `gpu_arbiter_acquired`
- `gpu_arbiter_released`
- `gpu_arbiter_abandoned_recovered`
- `mic_submit_armed`
- `mic_submit_persisted`
- `chatgpt_submit_started`
- `chatgpt_submit_completed`
- `assistant_first_text_observed`
- `assistant_reply_bound`
- `assistant_reply_binding_rejected`
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
- `stale_output_discarded`

各イベントに`traceId`、`sessionId`、`turnId`、`submissionId`、`tabId`、`pageInstanceId`、`conversationKey`、`assistantMessageKey`、`generationId`、`playbackId`、`chunkIndex`、`cancelEpoch`、TTSプロファイル、STTデバイス、GPU所有者、結果、文字数、テキストハッシュを必要な範囲で含める。本文全文は含めない。

### 時計

- JavaScript: `performance.now()`と`Date.now()`
- Python: `time.perf_counter_ns()`と`time.time_ns()`
- プロセス内時間はmonotonicで測る。
- ブラウザとPythonをまたぐ時間はwall clockを使い、開始前にloopback pingで時計差を記録する。
- 停止確認はAPI応答時刻ではなく、HTMLAudio停止確認またはsounddevice再生停止確認を使う。

### 集計

- GPUとSTTモデルのウォームアップ前と後を分ける。
- 原則20回測定する。
- 中央値、p95、最大値を報告する。
- `recording_stopped -> gpu_arbiter_acquired`をSTT GPU待機として集計する。
- `gpu_arbiter_acquired -> transcription_completed`を純STT推論遅延として集計する。
- `recording_stopped -> transcription_completed`をSTT全体遅延として集計する。
- `mic_submit_armed -> mic_submit_persisted`を送信関連付け永続化遅延として集計する。
- `transcription_completed -> chatgpt_submit_completed`をローカル送信遅延として集計する。
- `chatgpt_submit_completed -> assistant_first_text_observed`をChatGPT応答開始待ちとして分離する。
- `assistant_first_text_observed -> assistant_chunk_stable`を文確定待ちとして集計する。
- `assistant_chunk_stable -> playback_started`をLive TTS初音遅延として集計する。
- `recording_stopped -> playback_started`をユーザー体感のターン応答遅延として集計する。
- `playback_completed(chunk N) -> playback_started(chunk N+1)`をチャンク間無音として集計する。
- GPUアービター待機、VRAM不足、STTデバイス、CPUフォールバック回数を報告する。
- 成功回だけに限定せず、失敗・stale破棄・品質拒否数も報告する。

### 目標

共通:

- ユーザー操作から停止要求発行: 50ms以内
- ユーザー割り込みから無音: 通常150ms以内
- ユーザー割り込みから録音開始: 500ms以内
- マイク解放からChatGPT送信完了: 温まったCUDA STTで中央値1.5秒以内、p95 2.5秒以内
- 送信関連付け永続化: 中央値50ms以内、p95 150ms以内
- チャンク間無音: 250ms以内
- 意図しないSTT CPUフォールバック: 0件
- マイク返答の誤対応: 0件
- 曖昧な返答をLive再生: 0件
- 古い音声再生: 0件

TTSプロファイル別。20〜80文字の確定済み日本語チャンク、モデル・参照latentウォームアップ後で測定する:

- `speed`: `assistant_chunk_stable -> playback_started` 中央値1.5秒以内、p95 2.0秒以内
- `balanced`: 同中央値2.0秒以内、p95 2.75秒以内
- `bridge`: 同中央値3.0秒以内、p95 4.0秒以内

全経路:

- 既定Live `speed`の`recording_stopped -> playback_started`: 制御された短い発話と通常接続で中央値4.0秒以内、p95 6.0秒以内
- `balanced`と`bridge`も実測値を報告するが、GPT Live風の最終遅延合格判定は既定Live `speed`で行う。
- `bridge`は互換・品質比較経路であり、`speed`の代わりに使って低遅延達成と判定しない。

Irodori実験値の12-step約0.64〜0.80秒、16-step約0.88〜0.90秒、32-step約1.62〜2.11秒は実現可能性の参考値であり、本リポジトリの合格実測の代用にはしない。

ChatGPT側の応答開始待ちは外部要因として分離して報告するが、制御されたE2Eで全経路目標を未達のまま合格扱いしない。目標未達時は、実測値、主要ボトルネック、次の改善案を報告する。

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

- `completed`: 実装、依存復元後の基線、単体テスト、2回連続の全回帰、17項目E2E、実機GPU競合検証、プロファイル別実測が完了し、既定Live `speed`の全経路目標、誤対応0件、古い音声0件、CPUフォールバック0件をすべて満たした。
- `implemented_but_verification_blocked`: 実装と自動テストは完了したが、ログイン、実機、GPU、音声デバイスなどリポジトリ外要因で一部E2E・実測だけが未実行。この状態では目的達成を確認できないため`completed`にはしない。
- `incomplete`: 実装または必須自動テストに未完了がある。
- `stopped_for_safety`: 安全な基点や作業境界を維持できない。

未実行、失敗、目標未達を成功として扱わない。

## 17. 最終報告項目

- 実装した構成
- 新規・変更ファイル
- 割り込みの経路
- 生成キャンセルの仕組み
- 採用TTS設定と参照latentキャッシュ条件
- `submissionId`のarm/commit/bind/invalidate、冪等性、expiry、送信前ACKの実装結果
- マイク質問とassistant返答の対応付けと曖昧候補の拒否結果
- Windows名前付きGate/GPU MutexによるSTT/TTS調停結果とabandoned回収結果
- Live HTTP契約、202受付、409拒否、429 backpressure、interrupt応答結果
- 品質ゲート内容と校正結果
- テスト結果
- プロファイル別、区間別および全経路の実測遅延
- 未達の目標と理由
- 残タスク
- 通常作業ツリーが未変更である確認
- 読み取り専用のローカルTTSサービスとIrodori実験フォルダが未変更である確認
- 実装コードのcommit、push、PRを行っていない確認
- 共有スキル更新判定: `updated` / `not_needed` / `blocked`

## 18. 本計画コミット後の運用

- 実装開始前に、修正済みの本ファイルだけを確認し、他ファイルを含めず計画コミットを1つ作る。
- 計画コミットSHAを実装契約の基準点として記録し、フェーズ0でHEADがそのSHAから始まっていることを確認する。
- 計画コミット前に`git diff --check`と、本ファイル以外に意図しない差分がないことを確認する。
- 実装中はフェーズごとのチェック結果、測定値、設計変更理由を本ファイルまたは専用記録へ追記する。
- 計画から外れる必要が生じた場合、理由と影響を先に記録する。
- この計画コミットは実装コードのcommit、push、PR許可を意味しない。
- ユーザーの追加指示があるまで、実装コードは専用worktree内の未コミット差分として完成・検証する。
