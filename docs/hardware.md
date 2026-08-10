# 動作環境

「検証済み」は、このリポジトリの現在のセットアップまたはテストを実行して確認した範囲です。推測で対応済みにはしていません。

| 項目 | 必須／推奨 | 検証済み | 未対応・未検証 | 備考 |
| --- | --- | --- | --- | --- |
| OS | 実音声はWindows必須 | Windows 11 | Windows 10は未検証 | 軽量デモはNode.jsとChromiumが動く環境を対象 |
| Chrome | 推奨 | Playwright Chromiumで拡張経路を確認 | Chrome本体の手動確認は未実施 | Manifest V3拡張を展開読み込み |
| Brave | 対応想定 | なし | 未検証 | Chromium拡張として読み込み可能な構成 |
| Edge | 任意 | なし | 未検証 | Chromium系でも正式な実機確認は未実施 |
| Firefox | なし | なし | 未対応 | Chrome拡張として実装 |
| Python | 実音声で3.10以上 | Python 3.11 | 3.10、3.12以降は未検証 | 軽量デモでは不要 |
| NVIDIA GPU | 実音声で必須 | NVIDIA CUDA環境 | AMD / Intel GPUは未対応 | 通常セットアップは`--strict-cuda`を使用 |
| CUDA | 実音声で必須 | CUDA対応Torchで確認 | CUDAなしは未対応 | NVIDIAドライバーも必要 |
| VRAM | 12GB以上を推奨 | 16GB環境 | 12GB未満は未検証 | モデル読み込みや生成に失敗する可能性あり |
| CPU実行 | なし | なし | 通常セットアップでは未対応 | 再現可能なCPUセットアップとE2Eを提供していない |
| macOS | 軽量デモのみ想定 | なし | 実音声・デモとも未検証 | 対応済みとは記載しない |
| Linux | CIで使用する軽量経路 | なし | 軽量デモ・実音声とも未検証 | GitHub Actions用の構成はあるが、実音声の正式導線ではない |
| 実音声モード | Windows / Python / NVIDIA / CUDA / モデル | Irodori v3 direct | 上記以外 | 初回`setup-voice-env.cmd` → 通常起動`LocalVoiceBridge.exe` |
| 軽量デモモード | Node.js 22 / Chromium | Windows 11 / Playwright Chromium | Firefox | Python、CUDA、GPU、モデル、ChatGPTログイン不要 |

## 通常セットアップ

`setup-voice-env.cmd`で環境を準備し、通常利用は`LocalVoiceBridge.exe`から開始します。起動時のpreflightでもCUDAとモデル環境を確認します。`run-voice-stack.cmd`はターミナルでサーバーログを直接確認する診断用で、通常利用の入口ではありません。`--strict-cuda`を外してCPU実行へ切り替える方法は、通常利用としてサポートしていません。

## CIとデモの範囲

`npm run test:ci`と`npm run demo`は、GPU・Pythonモデル・実ChatGPTログインを使わないmock APIで、拡張機能の検出、通信、音声取得、再生完了、Autoの基準、Next、Regen、Replayを確認します。これはIrodoriの音質、GPU使用、実ChatGPTの将来のDOMを保証するテストではありません。
