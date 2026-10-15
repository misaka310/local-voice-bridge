# 参照音源

このリポジトリには参照音声を同梱しません。

## いつ必要か

`Ref=none` の場合は不要です。

参照音声が必要なのは、参照音声による追加設定 や参照音声つきの処理フローを使う場合だけです。

## 配置

`reference/voices/{voiceId}/` で管理します。

```text
local-api/reference/voices/
  default/
    voice.wav
    voice.txt
  my-voice/
    voice.wav
    voice.txt
```

- フォルダ名が `voiceId`
- `voice.wav`: 利用条件を確認した参照音声
- `voice.txt`: 参照音声の文字起こし

`voice.txt` の代わりに `text.txt` / `transcript.txt` も検出します。

## Chrome側

Chromeパネルの `Ref` に `default` / `my-voice` が出ます。`none` を選ぶと参照音声なしです。

## 音声の入手方針

- 自分で録音した音声を使う
- CC0 / Public Domain など、利用条件が明確な音声だけを使う
- 取得元、ライセンス、利用条件をローカルで控える

Mozilla Common Voice のようなCC0データセットを使う場合も、リポジトリに再配布せず、利用者が自分で取得して配置してください。
