# セットアップ

公開導線は Irodori direct / irodori-v3 / `キャラクター=標準` です。`キャラクター`は内部では既存の`referenceVoice`設定を使い、保存キーは変わりません。

## 実行

リポジトリルートで `setup-voice-env.cmd` を実行します。`LocalVoiceBridge.exe --setup`でも同じ画面を開けます。

セットアップ画面では次のプロファイルを選択します。

| プロファイル | 導入内容 | 推定ダウンロード | 必要な空き容量 |
| --- | --- | ---: | ---: |
| 読み上げのみ | CUDA版PyTorch、TorchCodec、FFmpeg、Irodori、PySide6 | 約8〜14 GB | 約15〜25 GB |
| 読み上げ + マイク会話 | 読み上げ環境 + faster-whisper、sounddevice | 約8〜17 GB | 約18〜29 GB |
| 開発者向け（通常は不要） | 読み上げ・STT + npm、Playwright Chromium、Windows GUIスモーク依存 | 約9〜19 GB | 約20〜33 GB |

通常利用で読み上げだけを使う場合は、`読み上げのみ`を選択してください。マイク会話を後から使う場合は、再度セットアップ画面を開いて`読み上げ + マイク会話`を実行します。開発者向けセットアップは初期状態では表示されず、`開発者向けの項目を表示`を有効にした場合だけ選択できます。

## 工程表示と再開

各工程は画面上で`running`、`passed`、`skipped`、`failed`として表示されます。失敗時は`LVB-SETUP-xxx`形式のコードと失敗工程を表示します。

- 完了記録: `local-api/runtime/setup/state.json`
- 失敗詳細: `local-api/runtime/setup/last-failure.json`
- 全ログ: `local-api/runtime/setup/setup.log`

実行中は`キャンセル`を押すとセットアップの子プロセスを停止し、開始・プロファイル・詳細項目・閉じる操作を戻します。キャンセル後も完了済み工程の記録は保持されます。再実行時は完了済み工程を再検証し、問題がなければスキップします。途中でネットワーク切断や容量不足が起きても、最初から全工程を繰り返しません。画面の`失敗内容をコピー`で、失敗コードとログ末尾をクリップボードへ取得できます。

## 導入する主な工程

1. Python・空き容量と`nvidia-smi`によるNVIDIA GPU/ドライバーの軽量事前確認
2. `local-api/.venv`作成
3. CUDA対応Torch、TorchCodec、共有FFmpegの導入
4. 読み上げ用依存とIrodoriの導入
5. CUDA/Torchとモデル・codecの確認
6. `LocalVoiceBridge.exe`の構築
7. Windowsスタートメニューへの登録
8. 選択時のみSTTまたは開発依存の追加

完了表示は、選択したプロファイルに必要な全工程が成功した後にだけ出ます。

## 完了後の初回導線

セットアップ成功後は、その画面から次の順序で初回利用まで進めます。

1. `拡張機能の導入手順`を開き、Chrome / Braveへ`extension`フォルダを読み込むか、更新時は再読み込みする
2. セットアップ画面へ戻り、`Local Voice Bridge を開く`を押す
3. Windows Local Voice小窓で拡張機能の接続を待つ
4. 接続後に`テスト音声`を押し、既存の`/v1/speak`経路で音声が再生されることを確認する
5. 成功すると初回オンボーディング完了が永続化される

このhandoffのために別の音声APIや別の設定正本は作りません。以後のWindows自動起動は`--background`で静かに常駐します。

## ブラウザ拡張

セットアップ画面の`拡張機能の導入手順`、Windows小窓の初回オンボーディング、または[extension/INSTALL.md](../extension/INSTALL.md)を開き、Chrome / Braveへ`extension`フォルダを読み込みます。リポジトリ更新後に旧版が残っている場合は、Windows Local Voice小窓が再読み込みを案内します。

## キャッシュ

通常は `%USERPROFILE%\.cache\huggingface` です。`HF_HOME` を指定している場合はその場所です。

## CUDAを使えない場合

公開初回導線は NVIDIA GPU/CUDA を前提にしています。CUDA が使えない場合、セットアップは止まります。CPU実行へ設定を書き換える方法は通常利用としてサポートしていません。GPU要件を満たせない場合でも、拡張の通信確認だけなら `npm run test:ci` を利用できます。
