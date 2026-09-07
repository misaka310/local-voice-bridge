# Repository instructions

作業を始める前に、必ず次の公開仕様を読んでください。

1. `README.md`
2. `CONTRIBUTING.md`

このファイルはエージェント向けの入口であり、仕様の正本を重複させません。README、CONTRIBUTING、既存テスト、UI文言にある利用者導線を、内部都合で削除・置換しないでください。

## 外部連携・責務拡張の禁止

- Local Voice Bridgeの通常runtimeまたはセットアップへ、別リポジトリのアプリ／runtime、別常駐プロセス／アプリ、新しいlocalhostサービス／ポート、新しいインストール・起動・監視手順、外部runtime依存、またはLocal Voice Bridgeの目的外の利用者機能を追加してはいけません。
- 上記が本当に必要な場合は、実装を開始する前に止まり、利用者へ構成・追加される管理対象・UX上の影響を示して明示承認を得てください。承認なしの実装は禁止です。
- 「既存ツールを再利用できる」「責務分離がきれいになる」「別ツールの不具合を直せる」は、責務境界を越える理由になりません。
- 通常利用者が新たに何かをインストール・起動・設定・監視する必要が生じる変更は、内部変更として扱わず必ず事前承認対象とします。
- `scripts/check-runtime-boundaries.js`を通常CIで維持し、承認のない別runtimeや追加loopbackポートの再導入を検出してください。

## タブ状態とfaviconの所有権

- このリポジトリは、ChatGPTタブの回答生成中、回答完了未確認、確認済み、読み上げ中、ChatGPTテキスト回答の生成エラーと、それらを示すfaviconの唯一の所有者です。
- 状態検出、確認解除、再生状態通知、静的favicon資産、`<link rel="icon">`の競合制御は17で実装・検証します。
- `73_chatgpt-tab-memo`はページ内の作業メモだけを担当します。17のタブ状態やfavicon責務を73へ移植・複製しません。
- タブ状態またはfaviconに関する変更は、`docs/tab-status-and-resource-design.md`と`tests/content-completion-marker.test.js`を同じ変更で更新します。

## Local Voice Bridgeの更新導線

拡張機能ソース`extension/`の更新時は共有`browser-extension-update-delivery`を適用します。エージェントの正式導線は`scripts/reload-extension.ps1`です。ローカル制御経路へ`reload_extension`を送信し、ACK後に拡張機能側の`chrome.runtime.reload()`を実行させ、再接続後の`loadedVersion == expectedVersion`まで確認します。

Windows Local Voice小窓の`拡張機能を再読み込み`ボタンは通常利用者向けの同じ導線として維持します。エージェントは`chrome://extensions`をユーザーの前面へ開いたり、ユーザーへReload操作を依頼したりして完了扱いにしません。旧版や切断で自己再読み込み不能なら、安全なbootstrapまたは制御経路の修復を先に行い、現在環境で不可能な場合だけblockedとして報告します。

## ブラウザ実機検証

実機ブラウザ検証は共有`playwright`および`avoid-agent-focus-steal`スキルに従い、リポジトリのE2Eまたは隔離プロファイルを使用します。ユーザーが利用中のChrome / Braveの既存タブ、分割表示、入力欄をテストに使わないでください。

## 解析対象の境界

通常のコードレビュー・構造解析は、追跡対象の`extension/`、`local-api/*.py`、`scripts/`、`tests/`、`docs/`とルート文書を優先します。`.ai-bridge/`、`.ai-review/`、`.venv/`、`local-api/.venv/`、`local-api/runtime/`、`local-api/logs/`、`test-results/`、`.e2e-profile*/`、`.demo-profile*/`、`.tmp-verify/`、`node_modules/`、`__pycache__/`、`.npm-cache/`は、runtime証跡そのものを調べる依頼でない限り再帰走査しません。runtime調査が必要な場合も対象ファイル・ログを限定して読みます。

## 完了条件

変更に近いテストに加え、公開ツリー、アーキテクチャ、background、mock E2Eを含む既存CIを通してください。無関係な未コミット変更を破棄・上書き・commitへ混入させないでください。

## 仕様の正本

- 仕様の正本: `docs/SPEC.md`
- `README.md` と `CONTRIBUTING.md` は利用者向け公開文書として `docs/SPEC.md` と整合させる。
- 共有の`validate-new-repository-spec.py`を`--repo .`で実行し、`NEW_REPOSITORY_SPEC_OK`を返す状態を維持する。
- 実装前に意図する仕様を正本へ反映し、仕様変更時は同じ変更で正本と検証を更新する。
