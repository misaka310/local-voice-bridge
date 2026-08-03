# Irodori direct

既定の実行方式は `irodori_direct` です。

- 既定の model key: `irodori-v3`
- 初回の公開導線: `Ref=none`
- 生成音声の保存先: `local-api/runtime/audio`

まずは参照音声なしで動作確認してください。参照音声を使う場合は、`docs/reference-audio.md` を見てください。

## TTSプロファイル

Irodoriのモデル名と、速度・品質プロファイルは別の概念です。利用できるプロファイルは次の3つだけです。

| プロファイル | steps | 用途 |
|---|---:|---|
| `speed` | 12 | マイクLiveの既定。参照latent cache、同期・watermark無効、生成ごとのCUDA cache解放なし |
| `balanced` | 16 | 速度と品質の中間。参照音声なしの通常生成にも使用 |
| `bridge` | 32 | 従来品質互換。通常Auto、Next、Regen、Replayで参照音声を使う場合の既定 |

任意のstep数はAPI・設定・UIのすべてで拒否します。`2`、`3`、`4`、`6`、`8`などを速度調整として直接指定できません。Liveの高度設定は`speed`、`balanced`、`bridge`の3択だけで、Windows小窓の日常操作には追加しません。

Suguha参照latentの作成に失敗した場合、別の声や参照なしへ黙ってフォールバックしません。Irodori内部の計測関数、同期設定、watermarkerを一時変更する場合は生成ロック内で行い、成功・例外のどちらでも`finally`で元へ戻します。

## 品質ゲート

生成直後、再生・Replay更新前にPCM WAVを検査します。RMS、clipping率、DC offset、差分spike、高域比、spectral flatness、長さを確認し、不合格WAVは削除して再生しません。閾値は`audioQuality`設定で校正できますが、既定では異常音声を成功扱いにしません。

## 実機検証

プロファイル別測定はウォームアップを合否から除外し、20〜80文字の日本語を各20回生成してmedian、p95、max、品質合格数を記録します。Liveの最終判定は`speed`で行い、`bridge`の結果を低遅延成功の代用にはしません。ローカル測定結果と参照音声は`.ai-bridge`およびGit管理外の`local-api/reference/`へ置き、公開ツリーへ実パスや私有音声を含めません。
