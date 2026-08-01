# アーキテクチャ

このリポジトリは、ChatGPTの新しいassistant返答を検知し、PC上のIrodori v3 direct APIで冒頭プレビューを読み上げるChrome / Brave拡張とWindows常駐アプリです。通信は127.0.0.1のローカルAPIだけを使用します。

## 役割

- `LocalVoiceBridge.exe`: 既存の`local-api/.venv`と`pythonw.exe`を使い、通知領域アプリを起動する小さなランチャー
- `local-api/tray_controller.py`: Windows小窓、デスクトップペット、APIプロセス、通知領域、自動起動を管理
- `local-api/control_panel.py`: `Ref`、`Volume`、`Auto`、`Next`、`Regen`、`Replay`、状態、現在文章、キュー数を表示する常時最前面の小窓
- `local-api/control_state.py`: 設定、ブラウザ状態、配信outboxを組み合わせて保存する薄い調整層
- `local-api/state_normalization.py`: 設定・拡張状態・マイク状態の入力境界と既定値
- `local-api/browser_runtime_state.py`: タブ・最新返答・キュー・録音開始時送信先の永続スキーマ
- `local-api/durable_outbox.py`: コマンドと文字起こしイベントの非破壊poll、consumer別ACK、再配信、容量制限
- `local-api/http_io.py`: JSON入出力と切断済みsocketの扱い
- `local-api/runtime_readiness.py`: process、依存関係、拡張機能、タブ、モデル状態を分けたReady判定
- `local-api/desktop_pet.py`: Windowsデスクトップ上のペット1体の表示、左ドラッグ移動、ダブルクリック通知を担当
- `extension/content.js`: 各ChatGPTタブの返答検知、プレビュー作成、入力欄への文字起こし反映、delivery IDの重複防止を担当。音声は再生しない
- `extension/background.js`: タブ登録、共通キュー、再生進行、各専用モジュールの接続だけを担当
- `extension/background-settings-core.js`: Chrome設定の既定値、移行、入力正規化、Refの明示的`none`と旧設定の区別を副作用なしで担当
- `extension/background-runtime-core.js`: Service Worker再起動時の状態シリアライズ・復元・キュー重複排除を副作用なしで担当
- `extension/background-control-sync.js`: 安定consumer ID、control-panel poll / ACK、再配信カーソル、外部設定同期、録音開始時の送信先への文字起こし配送を担当
- `local-api/server.py`: 永続状態API、Irodori v3 direct、ローカル再生、参照音声一覧、外部パネルAPI、ペット選択同期を担当
- `local-api/voice_runtime.py`: 起動時モデル準備と、生成・再生・Replay・Stopを1本のワーカーで直列化
- `local-api/conversation_controller.py`: 右Ctrl＋`＼ / _`の録音状態、ローカルSTT、ChatGPT送信に加え、対応するYouTube Dictation Pause Controlへの入力元別状態通知を担当

## Windows Local Voice小窓

小窓に表示する日常操作は次だけです。

- `Ref`
- `Volume`
- `Auto`
- `Next`
- `Regen`
- `Replay`

`Voice`はIrodori v3 direct固定です。小窓はデスクトップペットのダブルクリック、または通知領域の`Show Local Voice panel`から表示・非表示を切り替えます。×は終了ではなく非表示です。位置は`local-api/runtime/control-panel-window.json`へ保存します。

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

## 返答検知と全タブAuto

1. 開いている各ChatGPTタブが`background.js`へ登録されます。
2. 各`content.js`が既存assistant返答を基準として記録します。
3. 外部小窓で`Auto`をオンにすると、すべての登録済みChatGPTタブが基準を作り直します。
4. その後で各タブへ新しく表示されたassistant返答を検知します。
5. 各タブの最大2行・80文字の冒頭プレビューを1つの共通キューへ追加します。
6. 共通キューの順番で1件ずつ読み上げます。

Autoの対象は、最後に触った1タブだけではありません。開いている全ChatGPTタブです。`思考中`、`考え中`、`Thinking`、`画像を分析しています`だけの途中状態と、Autoをオンにする前から表示されていた返答は読みません。

## 音声生成と再生

```text
各ChatGPT content.js
  -> background.js がローカル永続キューへ同期
  -> POST http://127.0.0.1:8717/v1/speak { playLocal: true }
  -> voice_runtime.py の単一ワーカーで生成
  -> 同じワーカーがPCの音声デバイスで再生完了まで待機
  -> 次のキュー項目へ進む
```

`uiOwnerTabId`と`selectedTabId`は手動操作の対象返答を決める内部値です。音声再生先ではありません。Autoの検出対象も制限しません。タブを閉じても生成・再生はローカルワーカー上で継続し、APIまたはService Workerの再起動後は永続状態からタブ・最新返答・待機キューを復元します。

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

## Refとデスクトップペット

外部小窓で`Ref`を変更すると、同じIDを次へ送ります。

```text
POST http://127.0.0.1:8717/v1/desktop-pet
```

空、`none`、旧`qwen`系値、不正なパス形式は`placeholder`として扱います。指定IDの素材がない場合、デスクトップ側が利用可能な既定素材へフォールバックします。

ペットの操作は次だけです。

- 左ドラッグ: 位置を移動・保存
- 左ダブルクリック: Windows Local Voice小窓を表示・非表示
- シングルクリック、右クリック: 何もしない
- ドラッグ直後のダブルクリック: 誤操作防止のため無効

ペットの位置と選択IDは`local-api/runtime/desktop-pet-settings.json`へ保存します。

## 通知領域と起動

通常入口は`LocalVoiceBridge.exe`です。EXEは`pythonw.exe local-api/tray_controller.py`を非表示で起動します。

通知領域は、外部小窓の表示・非表示、状態確認、再起動、フォルダ表示、自動起動、再セットアップ、終了を担当します。ペット専用の表示・種類・位置メニューは持ちません。Windowsログイン時の自動起動もEXEを直接指定します。

## 保存する設定

Chrome側:

- AutoのON / OFF
- 127.0.0.1のAPI URLとhealth URL
- Ref ID
- 音量
- プレビュー判定の内部値

Windows側:

- 外部小窓の設定と位置
- ペットの選択IDと位置
- 拡張機能から受け取った直近状態
- 登録タブ、選択タブ、最新返答、Auto / Next境界、待機キュー、Replay対象、録音開始時の送信先セッション
- 未ACKコマンド・文字起こしイベントとconsumer別ACK位置

Voice、Tab、Petの独立選択設定は保存しません。

## 変更時の設計ゲート

`npm run check:architecture`は、責務分離に必要なモジュール、主要import、禁止された重複実装、オーケストレータの行数上限を確認します。`control_state.py`、`server.py`、`background.js`へ新しい責務を直接追加して上限を超える変更は、別モジュールへ切り出さない限りCIで失敗します。

## テスト

- Pythonテスト: loopback境界、外部状態ストア、外部Qt小窓、通知領域、ペットのドラッグ・ダブルクリック、ランチャー
- background単体テスト: 全タブ共通キュー、外部設定反映、ACK再配信、Service Worker / API復旧、delivery ID、ローカル再生、Ref・ペット同期
- mock E2E: Chrome内パネルなし、外部Auto、Next / Regen / Replay、短文、途中状態除外、複数タブ共通キュー、マイク文字起こし
- real E2E: 専用loopbackポートでIrodori v3 direct、Next、実参照音声・ペット同期、複数タブ共通キュー

エージェント実行のブラウザ検証では`voiceVolume=0`と`--mute-audio`を使用します。
