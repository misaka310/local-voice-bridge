# Repository instructions

作業を始める前に、次の公開仕様を確認してください。

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/SPEC.md`

このファイルはエージェント向けの入口です。README、CONTRIBUTING、既存テスト、UI文言にある利用者導線を内部都合で削除・置換しないでください。

## 責務境界

- このリポジトリは、ChatGPTタブの回答生成中、回答完了未確認、確認済み、読み上げ中、ChatGPTテキスト回答の生成エラーと、それらを示すfaviconを所有します。
- 状態検出、確認解除、再生状態通知、静的favicon資産、`<link rel="icon">`の競合制御はこのリポジトリ内で実装・検証します。
- ページ内メモなど、この製品と無関係なブラウザ機能は責務外です。別プロジェクトの名称・番号・内部運用契約を、このリポジトリの仕様やテスト条件へ持ち込まないでください。
- タブ状態またはfaviconに関する変更は、`docs/tab-status-and-resource-design.md`と`tests/content-completion-marker.test.js`を同じ変更で更新します。
- マイク会話はChatGPTへの音声入力だけを担当します。他サイトの再生制御や別アプリへの録音状態通知を追加しません。

## 外部連携・責務拡張

- 通常runtimeまたはセットアップへ、別リポジトリのアプリ／runtime、別常駐プロセス／アプリ、新しいlocalhostサービス／ポート、新しいインストール・起動・監視手順、外部runtime依存、または本製品の目的外の利用者機能を追加しません。
- 本当に必要な場合は、実装開始前に構成・追加される管理対象・UX上の影響を示して利用者の明示承認を得ます。
- `scripts/check-runtime-boundaries.js`を通常CIで維持し、承認のない追加loopbackサービスを検出します。

## 拡張機能の更新

- 拡張機能ソースは `extension/` です。
- リポジトリ内の正式な再読み込み経路とテストを使用し、更新後は期待versionと再接続を確認します。
- 開発者個人のワークスペース、共有Skill、別リポジトリの補助サービスを必須条件にしません。
- 通常利用者向けのWindows Local Voice小窓の`拡張機能を再読み込み`導線を維持します。

## ブラウザ検証

- E2Eまたは隔離プロファイルを使用し、検証を特定利用者の既存タブ・既存プロファイル・画面配置へ依存させません。
- 実ブラウザが必要な変更でも、再現可能な検証手順をこのリポジトリ内に置きます。

## 解析対象の境界

通常のコードレビュー・構造解析は、追跡対象の`extension/`、`local-api/*.py`、`scripts/`、`tests/`、`docs/`とルート文書を優先します。`.ai-bridge/`、`.ai-review/`、`.venv/`、`local-api/.venv/`、`local-api/runtime/`、`local-api/logs/`、`test-results/`、`.e2e-profile*/`、`.demo-profile*/`、`.tmp-verify/`、`node_modules/`、`__pycache__/`、`.npm-cache/`は、runtime証跡そのものを調べる依頼でない限り再帰走査しません。runtime調査が必要な場合も対象ファイル・ログを限定します。

## 完了条件

- 変更に近いテストに加え、公開ツリー、アーキテクチャ、runtime boundary、background、mock E2Eを含む既存CIを通します。
- 仕様、実装、テスト、利用者向け文書を同じ責務境界に揃えます。
- 無関係な未コミット変更を破棄・上書き・commitへ混入させません。

## 仕様の正本

- 仕様の正本: `docs/SPEC.md`
- `README.md` と `CONTRIBUTING.md` は利用者向け公開文書として `docs/SPEC.md` と整合させます。
- 実装前に意図する仕様を正本へ反映し、仕様変更時は同じ変更で正本と検証を更新します。
