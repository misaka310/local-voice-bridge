# Local Voice Bridge

[![CI](https://github.com/misaka310/local-voice-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/misaka310/local-voice-bridge/actions/workflows/ci.yml)

chatgpt.comにローカル音声読み上げと、任意のプッシュ・トゥ・トーク音声入力を追加するWindows向けの非公式補助ツールです。読み上げだけでも利用でき、マイク会話機能は初期状態でオフです。

> **非公式・非提携について**
> このプロジェクトは独立して開発された非公式ツールであり、OpenAIの公式製品、提携製品、承認製品、スポンサー製品ではありません。ChatGPT、OpenAIおよび関連する名称・商標は各権利者に帰属します。

https://github.com/user-attachments/assets/55580bbe-1325-4548-a03b-d70f7004a7fb

再生ボタンから、ChatGPTの新しい返答を自動で読み上げる流れを映像と音声で確認できます。

映像は実ChatGPTアカウントではなく、安全なローカルフィクスチャで実際の拡張機能コードを動かしています。ローカル音声生成エンジンにはIrodori v3を使用しています。

## 主な機能

- Autoをオンにした後の新しい返答だけを検出し、初期値では最大2行・80文字の冒頭を一度だけ再生（拡張機能のオプションで変更可能）
- バックグラウンドで返答が完了したChatGPTタブは、黄色い完了アイコンとタイトル先頭の`●`で示し、そのタブを開くと解除
- Autoをオンにする前から表示されていた返答は読み上げない
- `Next`で続き、`Replay`で聞き直し、`Regen`で現在の部分を再生成
- API・生成音声・任意の参照音声を同じPC内で管理
- 実モデル不要のデモとCIで、拡張機能の通信・再生境界を確認可能
- Windowsの通知領域に常駐し、通常利用ではターミナルを開かない
- 日常操作はChrome外のWindows Local Voice小窓へ集約し、開いている全ChatGPTタブの返答を1つの共通キューで読み上げ
- 明示的に有効化した場合だけ、右Ctrl＋右Shift左の`＼ / _`キーを長押しして録音し、ローカルfaster-whisperから入力欄へ送信
- 対応するYouTube Dictation Pause Controlが起動中なら、録音開始・終了を`source=local-voice-bridge`として直接通知し、YouTubeの停止・再開と同期
- 入力先は録音開始時に最後にフォーカスしていたChatGPT入力欄へ固定し、文字起こし中に別タブへ移っても変更しない
- STTモデルは選択・有効化時に先に準備し、初回ダウンロード中は録音を開始しない
- 文字起こし表示後は初期値0.7秒だけEscでキャンセルでき、録音中・文字起こし中は別タブの新しい返答を割り込み再生しない
- デスクトップペットは左ドラッグで移動し、ダブルクリックでLocal Voice小窓を表示・非表示

## GPU不要の2分デモ

```bat
npm ci
npx playwright install chromium
npm run demo
```

Node.js 22とChromiumだけで起動します。Python、CUDA、GPU、Hugging Faceモデル、ChatGPTへのログインは不要です。表示される画面は「ローカルデモフィクスチャ」であり、実ChatGPT画面ではありません。終了時はChromiumを閉じるか`Ctrl+C`を押してください。

終了コードで検証する場合：

```bat
npm run demo:check
```

## Setup / 初回セットアップ

初回だけ次を実行します。

```bat
setup-voice-env.cmd
```

小型セットアップ画面が開き、次の3種類から選べます。通常は**読み上げのみ**を選択してください。

| 選択 | 内容 | 推定ダウンロード | 必要な空き容量 |
| --- | --- | ---: | ---: |
| 読み上げのみ | Irodori v3、CUDA版PyTorch、FFmpeg、Windows小窓 | 約8〜14 GB | 約15〜25 GB |
| 読み上げ + マイク会話 | 上記 + faster-whisper、録音依存 | 約8〜17 GB | 約18〜29 GB |
| 開発者向け（通常は不要） | 上記 + npm、Playwright、Windows GUIスモーク依存 | 約9〜19 GB | 約20〜33 GB |

工程ごとの成功・失敗・失敗コードを画面に表示します。完了済み工程は`local-api/runtime/setup/state.json`へ記録されるため、途中失敗後の再実行は失敗工程から再開します。詳細ログは`local-api/runtime/setup/setup.log`です。

## Usage / 起動と操作

セットアップ後はWindows検索で`Local Voice Bridge`を開きます。セットアップが現在のユーザーのスタートメニューへショートカットを登録します。リポジトリ内の`LocalVoiceBridge.exe`を直接ダブルクリックしても同じです。EXEは既存のPython環境をターミナルなしで起動します。日常の音声操作はChrome外のWindows Local Voice小窓で行います。小窓はデスクトップペットのダブルクリック、または通知領域の`Show Local Voice panel`から開閉できます。通知領域は小窓の表示、状態確認、再起動、フォルダ表示、自動起動、再セットアップ、終了を担当します。Voice Bridgeを再起動した場合も、開いたままのChatGPTタブは自動で再接続されるため、タブの再読み込みは不要です。ペットは`Ref`と自動連動して1体だけ表示され、左ドラッグで移動できます。シングルクリックと右クリックでは何も起きません。

`http://127.0.0.1:8717/health`を開き、`ok=true`と`engine=irodori_direct`を確認します。その後、[拡張機能の導入・更新手順](extension/INSTALL.md)に従い、Chrome / Braveの**Load unpacked**から`extension/`を選択してください。更新後に旧版が残っている場合は、Windows Local Voice小窓が拡張機能の再読み込みを案内します。拡張機能を再読み込みすると、開いているChatGPTタブへ自動で再接続するため、ChatGPTタブ自体の再読み込みは不要です。

読み上げる最大行数・最大文字数、文字起こしモデル、文字起こし後の送信前猶予は、Chrome / Braveの拡張機能アイコンを右クリックして`オプション`を開き、設定します。`マイク会話`のオン・オフは、これまでどおりWindows Local Voice小窓で操作します。

拡張機能のソースが更新され、接続中の版が自動再読み込みに対応している場合は、小窓に`拡張機能を再読み込み`ボタンが表示されます。この機能を含まない旧版から更新する最初の1回だけは、Chrome / Braveの拡張機能画面で手動再読み込みしてください。

最初は`Ref=none`のまま、Windows Local Voice小窓でAutoをオンにしてから新しいメッセージを送ります。開いているどのChatGPTタブでも、新しい返答の先頭プレビューが再生されれば完了です。参照音声の追加方法は[参照音声](docs/reference-audio.md)を参照してください。

双方向の音声会話を使う場合は、セットアップ画面で`読み上げ + マイク会話`を完了してから、小窓の`マイク会話`をオンにします。送信先にしたい入力欄へフォーカスを入れ、右Ctrl＋右Shift左の`＼ / _`キーを押している間だけ録音します。どちらか一方を離すと日本語をローカル文字起こしし、録音開始時にフォーカスしていた入力欄へ表示します。初期値では0.7秒以内にEscを押すと今回挿入した文字だけを削除し、押さなければ送信ボタンから自動送信します。猶予時間は拡張機能の`オプション`で変更できます。詳しい準備、状態表示、保存データ、制約は[操作と検証](docs/operation.md#マイク会話モード)を参照してください。

対応版のYouTube Dictation Pause Controlが`127.0.0.1:17654`で起動している場合、同じ録音開始・終了を直接通知します。YouTube側が起動していない場合や通知に失敗した場合でも、録音・文字起こし・ChatGPT送信は継続します。ポートを変更した場合だけ、環境変数`YOUTUBE_DICTATION_PAUSE_STATE_URL`へ通知先（例: `http://127.0.0.1:27654/state`）を設定します。通知先は`http`のloopbackホストと`/state`だけを受け付け、外部URLや不正な値は既定のloopback URLへ戻します。

生成音声は起動時と生成後に自動整理され、初期値では**最新1,000件・合計1GB・14日以内**のすべてを満たす範囲だけを保持します。通知領域の`Clear generated audio...`から生成音声だけを即時削除できます。参照音声と設定は削除されません。サーバーログとコントローラーログも各2MB・バックアップ2世代までに制限されます。

通知領域の`Uninstall Local Voice Bridge...`はWindows自動起動とスタートメニュー登録を解除し、生成音声とログを削除します。参照音声、設定、モデル、リポジトリ本体は残るため、再利用する場合は`LocalVoiceBridge.exe`を起動できます。完全に削除する場合は、その後にリポジトリフォルダとHugging Faceのモデルキャッシュを利用者が削除します。

ターミナルでサーバーログを直接確認したい場合だけ、診断用の`run-voice-stack.cmd`を使用します。

## Requirements / 対応環境

| モード | 必須 | 検証済み | 未対応・未検証 |
| --- | --- | --- | --- |
| 軽量デモ / mock CI | Node.js 22、Chromium | Windows 11のPlaywright Chromium | Firefox、macOSの実行は未検証 |
| 実音声 | Windows、Python、NVIDIA GPU、CUDA、Irodori v3 | Windows 11、Windows外部小窓、Playwright Chromium、NVIDIA CUDA環境 | CPUのみ、macOS、Linux、Firefox、Edgeは未検証または未対応 |

GPU、VRAM、ブラウザごとの扱いは[動作環境](docs/hardware.md)にまとめています。未検証の環境を対応済みとはしていません。

## Verification / 動作確認

セットアップ画面で`開発者向けの項目を表示`を有効にした場合だけ、開発者向けセットアップを選択できます。開発者向けでは次を実行します。

```bat
npm run test:python
npm run test:background
npm run test:e2e:mock
npm run check:public
```

`test:background`は、Service Workerの全タブ共通キュー、外部設定・コマンド同期、参照音声正規化、loopback音声URL制限、バイナリ変換を検証し、`background-core.js`へ95%のline coverageを要求します。通常起動の確認は、通知領域、Windows Local Voice小窓、`http://127.0.0.1:8717/health`の`ok=true`を使用します。ペットのダブルクリックを含むWindows実画面確認手順は[起動とヘルス確認](docs/startup.md)にあります。

## Limitations / 制約

- ChatGPTのDOM変更により、返答検出が一時的に動作しなくなる可能性があります。
- CIはChatGPTに似た固定フィクスチャを使い、将来の実ChatGPT DOMを保証しません。
- 軽量デモは統合動作の確認用で、Irodoriの音声品質評価ではありません。
- 実モデルE2EにはWindows、NVIDIA GPU、CUDA、モデル取得が必要です。
- ローカルAPIには認証がないため、LAN、インターネット、トンネルへ公開できません。

## 詳細ドキュメント

- [初回セットアップ](docs/setup.md)
- [起動とヘルス確認](docs/startup.md)
- [操作とテスト](docs/operation.md)
- [動作環境](docs/hardware.md)
- [困ったとき](docs/troubleshooting.md)
- [参照音声](docs/reference-audio.md)
- [セキュリティ境界](SECURITY.md)
- [構成](ARCHITECTURE.md)
