# 起動

初回セットアップ後、Windows検索で`Local Voice Bridge`を開きます。セットアップは現在のユーザーのスタートメニューへショートカットを登録します。リポジトリ内の`LocalVoiceBridge.exe`を直接ダブルクリックしても同じです。

このEXEは既存の`local-api/.venv`を使って通知領域アプリ、Windows Local Voice小窓、デスクトップペット、ローカルAPIを管理する小さなWindowsランチャーです。

通常起動ではターミナルは表示されません。Windows右下の通知領域に`Local Voice Bridge`アイコンが表示され、デスクトップペットが1体起動します。Local Voice小窓は起動直後は非表示です。通知領域アイコンが隠れている場合は、タスクバー右端の上向き矢印を開いてください。

`.venv`、`pythonw.exe`、`PySide6`、`QtWidgets`、`QtSvg`のいずれかが不足している場合は、EXEがセットアップ画面を開くか確認します。再実行時は完了済み工程を検証後にスキップし、不足工程だけを続行します。

## 通知領域メニュー

- `Status: ...`: APIの現在状態
- `Show Local Voice panel` / `Hide Local Voice panel`: Windows小窓を開閉
- `Restart Voice Bridge`: 通知領域アプリ、小窓、デスクトップペット、ローカルAPIをまとめて再起動し、更新済みコードを読み直す
- `Open controller log`: `local-api/logs/controller.log`を開く
- `Open generated audio folder`: 生成された音声フォルダを開く
- `Clear generated audio...`: 参照音声と設定を残したまま、生成済み音声だけを確認後に削除する
- `Open reference voices folder`: 参照音声フォルダを開く
- `Start with Windows`: 現在のユーザーのWindowsログイン時自動起動を切り替える
- `Exit and run environment setup`: APIを停止し、セットアップを表示付きで再実行する
- `Uninstall Local Voice Bridge...`: 自動起動とスタートメニュー登録を解除し、生成音声とログを削除する。参照音声、設定、モデル、リポジトリ本体は残す
- `Exit`: 通知領域アプリ、Windows小窓、デスクトップペット、このアプリが所有するAPIを終了する

生成音声は起動時と生成後に自動整理します。初期値は最新1,000件、合計1GB、14日以内で、いずれかを超える古い音声から削除します。サーバーログとコントローラーログは各2MB、バックアップ2世代までです。

ペットの種類選択、位置初期化、常に手前の切り替えは通知領域にはありません。

## Windows Local Voice小窓

ペットを左ダブルクリックするか、通知領域の`Show Local Voice panel`を選ぶと開きます。

小窓には次を表示します。

- `Ref`
- `Volume`
- `Auto`
- `Next`
- `Regen`
- `Replay`
- API・再生状態
- 現在生成中または直前に再生した文章
- 共通キュー数
- 登録済みChatGPTタブ数

×は終了ではなく非表示です。上部を左ドラッグすると位置を移動できます。位置は次へ保存されます。

```text
local-api/runtime/control-panel-window.json
```

Chrome / Brave内にはLocal Voice操作パネルを表示しません。Autoの対象は最後に触った1タブではなく、開いている全ChatGPTタブです。

## デスクトップペットの操作

- 左ドラッグ: Windowsデスクトップ上の位置を移動
- 左ダブルクリック: Windows Local Voice小窓を表示・非表示
- 通常クリック、右クリック: 何もしない
- ドラッグ直後のダブルクリック: 誤操作防止のため無効

ペットの種類はWindows Local Voice小窓の`Ref`と自動連動します。Chrome内にはペット本体や`Pet`選択欄を表示しません。`Ref=none`または空の場合と、同じIDの素材がない場合は既定ペットへフォールバックします。

設定は`local-api/runtime/desktop-pet-settings.json`へ保存されます。このファイルはローカル専用で、Gitには追加されません。モニター構成やDPIが変わって保存位置が完全に画面外になった場合は、起動時に操作できる位置へ一時補正されます。補正後にユーザーがドラッグした場合だけ新しい位置として保存します。

## 状態表示

通知領域:

- `Starting`: API起動中
- `Checking environment`: CUDAとIrodoriの事前確認中
- `Ready`: 通知領域アプリが起動したAPIが正常
- `Ready (existing)`: 別の方法で起動済みの互換APIへ接続中。同じリポジトリ配置のAPIなら`Restart Voice Bridge`で安全に停止して更新版へ切り替える。別配置のAPIは停止しない
- `Environment missing`: `.venv`またはサーバーファイルがない
- `CUDA or model unavailable`: CUDA、依存関係、モデルの事前確認に失敗
- `Port 8717 in use`: 別サービスが同じポートを使用中
- `Unhealthy` / `Restarting`: API異常を検出し、復旧処理中

Windows小窓:

- `Waiting for ChatGPT`: APIは動作しているが、更新後の拡張機能が接続していない
- `Ready`: ChatGPTタブを認識し、待機中
- `Generating`: Irodori音声を生成中
- `Playing`: 音声を再生中
- `Played chunk ...`: 再生完了

デスクトップペットは、Voice Bridgeが正常なときに`idle`、異常時に`error`を表示します。

## 成功条件

`http://127.0.0.1:8717/health`で次の状態になればAPIは起動できています。

- `ok=true`
- `runtime=irodori_direct`
- `defaultModel=irodori-v3`

最初の`/v1/speak`は、モデルをGPUメモリへ読み込むため時間がかかります。

## Windowsで確認する手順

1. Windows検索で`Local Voice Bridge`を開く
2. 通知領域のアイコンで`Status: Ready`または`Status: Ready (existing)`を確認する
3. ペットを左ダブルクリックし、Windows Local Voice小窓が開くことを確認する
4. もう一度ダブルクリックして非表示になり、通知領域からも開閉できることを確認する
5. 小窓に`Ref`、`Volume`、`Auto`、`Next`、`Regen`、`Replay`だけがあり、Chrome内には操作パネルがないことを確認する
6. Chrome / Braveで2つ以上のChatGPTタブを開き、小窓で`Auto`をオンにする
7. 各タブで新しい返答を生成し、両方の返答が1つの共通キューで順番に読み上げられることを確認する
8. `思考中`だけの途中状態は読まず、最終返答だけを読むことを確認する
9. `Ref`を変更し、Chrome保存値、読み上げ参照音声、同じIDのデスクトップペットが一致することを確認する
10. ペットの左ドラッグで位置を移動でき、ドラッグ終了直後に小窓が誤って開かないことを確認する
11. 小窓の×で非表示にし、ペットのダブルクリックで再び開くことを確認する
12. `Exit`で終了し、再起動後に小窓位置、ペット位置、`Ref`、`Volume`、`Auto`が復元されることを確認する

`Exit`は通知領域アプリが起動した`server.py`だけを停止します。`run-voice-stack.cmd`などで先に起動した互換APIへ接続している場合、その外部プロセスは停止しません。

## 診断用の前面起動

ターミナルでログを直接確認する場合だけ`run-voice-stack.cmd`を使います。通常利用では使用しません。
