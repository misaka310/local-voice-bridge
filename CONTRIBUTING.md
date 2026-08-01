# Contributing

Local Voice Bridgeへの変更では、利用者向けREADMEに記載された操作と安全境界を維持してください。

## UX compatibility

- Autoは有効化後に現れた新しいassistant返答だけを対象にします。
- 既定の自動読み上げは冒頭プレビュー（最大2行・80文字）で、返答全文を自動分割して読み上げません。
- `Next`、`Replay`、`Regen`の既存動作を変更する場合は、READMEとテストを同じPRで更新してください。
- ローカルAPIはloopback専用です。LAN、インターネット、トンネルへ公開する構成を追加しないでください。

## Architecture boundaries

次のファイルは調整層として小さく保ち、新しい責務は専用モジュールへ分離してください。

- `local-api/control_state.py`
- `local-api/server.py`
- `extension/background.js`

設定と入力正規化、永続outbox、ブラウザ状態、HTTP I/O、readiness、音声生成・再生は、既存の専用モジュールに実装します。行数上限や必須モジュール検査を弱めてCIを通す変更は行わないでください。

## Public-tree rules

- 公開ツリーはGit追跡済みソースから作成してください。
- 生成音声、参照音声、モデル、`.venv`、ブラウザプロファイル、テスト結果、ログ、AI作業メモをコミットしないでください。
- Windowsの通常起動、自動起動、ドキュメントは `LocalVoiceBridge.exe` を入口とします。VBSを通常入口または互換入口として追加しないでください。

## Verification

```bat
npm run check:architecture
npm run test:ci
```

永続化、ACK・再配信、Service Worker復旧、キュー復元、文字起こし配送を変更した場合は、同じHEADでフルCIが安定して通ることを確認してください。

ブラウザ自動検証では、予期しない音声再生を避けるため `voiceVolume=0` と `--mute-audio` を使用してください。
