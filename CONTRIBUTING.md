# Contributing

Local Voice Bridgeへの変更では、利用者向けREADMEに記載された操作と安全境界を維持してください。

## UX compatibility

- Autoは有効化後に現れた新しいassistant返答だけを対象にします。
- 既定の自動読み上げは冒頭プレビュー（最大2行・80文字）で、返答全文を自動分割して読み上げません。
- `Next`、`Replay`、`Regen`の既存動作を変更する場合は、READMEとテストを同じPRで更新してください。
- ローカルAPIはloopback専用です。LAN、インターネット、トンネルへ公開する構成を追加しないでください。

## Multi-tab resource contract

- 通常利用では30個以上のChatGPTタブが同時に開かれる前提で、正しさだけでなく定常時のCPU・メッセージ数も評価してください。
- 通常検知はDOM・ブラウザイベントを使い、全タブ再走査は取りこぼし回復専用とします。全タブ再走査は20秒未満に設定せず、既定は60秒以上としてください。
- 完了候補を待つ1タブだけの短時間再確認は許可しますが、時間制限付き・置換可能・タブ単位でなければなりません。
- ブラウザ変更では、30タブ以上を表すテスト、idleタブへsub-second再確認が繰り返されないテスト、待機中1タブが速やかに再確認されるテストを含めてください。
- 全タブ高頻度ポーリング、タブごとの数秒heartbeat、faviconの定期アニメーションで正しさだけを回復する変更は完了扱いにしません。
- 数値、回答元タブへの状態通知、favicon優先順位は[タブ状態と30タブ向け低負荷設計](docs/tab-status-and-resource-design.md)を維持してください。

## Architecture boundaries

次のファイルは調整層として小さく保ち、新しい責務は専用モジュールへ分離してください。

- `local-api/control_state.py`
- `local-api/server.py`
- `extension/content.js`
- `extension/background.js`

設定と入力正規化、永続outbox、ブラウザ状態、HTTP I/O、readiness、音声生成・再生は、既存の専用モジュールに実装します。行数上限や必須モジュール検査を弱めてCIを通す変更は行わないでください。

ChatGPT固有の責務は次の境界を維持します。

- assistant本文抽出は `assistant-text-extractor.js`
- 出典・citation UI判定は `assistant-source-filter.js`
- Autoのbaseline、streaming安定性、完了判定は `auto-speech-controller.js`
- ProseMirror入力、送信ボタン、送信前ACKは `prompt-input-core.js`
- Auto受付、既読境界、Next・Regen、キュー項目生成は `background-queue-core.js`

## Public-tree rules

- 公開ツリーはGit追跡済みソースから作成してください。
- 生成音声、参照音声、モデル、`.venv`、ブラウザプロファイル、テスト結果、ログ、AI作業メモをコミットしないでください。
- Windowsの通常起動、自動起動、ドキュメントは `LocalVoiceBridge.exe` を入口とします。VBSを通常入口または互換入口として追加しないでください。

## Verification

```bat
npm run check:architecture
npm run test:ci
```

永続化、ACK・再配信、Service Worker復旧、キュー復元、文字起こし配送を変更した場合は、同じHEADでフルCIが安定して通ることを確認してください。文字起こし配送の恒久エラーは状態を報告してACKし、後続イベントを止めないでください。一時的なDOM欠落など、再試行で回復し得る失敗だけを未ACKで残します。

マイク送信を変更した場合は、通常のmock E2Eに加えて次を実行してください。

```bat
npm run test:e2e:brave-mic
```

この検証は専用Braveプロファイル、テスト用拡張コピー、別APIポートを使用します。利用者の普段使いプロファイルを再起動・再利用してはいけません。ChatGPTに近いProseMirror入力欄、ネイティブ入力、送信前後のComposerノード差し替え、送信クリック、submission commitまで確認します。

assistant本文・出典フィルタを変更した場合は、`tests/e2e/assistant-text-extractor-dom.spec.js`のDOMマトリクスを更新してください。消すべき出典だけでなく、通常リンク、説明付きリンク、本文中の数字・年・容量表記、出典チップの前後や隣にある本文が残ることも確認します。スクリーンショット1件だけを再現するテストでは完了扱いにしません。

ブラウザ自動検証では、予期しない音声再生を避けるため `voiceVolume=0` と `--mute-audio` を使用してください。
