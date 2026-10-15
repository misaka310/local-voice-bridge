# ペット表示

ペットはWindowsデスクトップ上へ1体だけ表示します。Chrome / Brave内にはペット本体を表示しません。

## 役割と操作

- 左ドラッグ: デスクトップ上の位置を移動して保存
- 左ダブルクリック: Windows Local Voice小窓を表示・非表示
- 通常クリック: 何もしない
- 右クリック: メニューを開かず、何もしない
- ドラッグ直後のダブルクリック: 誤操作防止のため無効

ダブルクリックはChromeを開く操作ではありません。既にWindows上で動いているLocal Voice小窓の表示状態だけを切り替えます。小窓の×を押した場合も終了せず非表示になるため、再びペットをダブルクリックすると開けます。

通知領域にも`Show Local Voice panel`／`Hide Local Voice panel`があります。ペットの種類、位置初期化、最前面などの重複操作は通知領域へ追加しません。

## Refとの自動連動

ペットの種類はWindows Local Voice小窓にある`Ref`と自動連動します。ペット専用の選択欄はありません。

- `Ref=misaka`なら`misaka`
- `Ref=asuka`なら`asuka`
- `Ref=none`または空なら`placeholder`
- 参照音声と同じIDの素材がない場合も`placeholder`へフォールバック

以前の`petMode`、`selectedPetId`、`petPosition`などのブラウザ設定が残っていても、起動時に安全に無視または削除されます。現在の選択状態を書き換える通常経路はWindows小窓の`Ref`です。

## 公開リポに含めるもの

公開リリースに含めるペット素材は、次の汎用プレースホルダーだけです。

```text
extension/assets/pet/pet.json
extension/assets/pet/placeholder.svg
```

個人用のペット素材は`extension/assets/pet/local/`へ置きます。このフォルダはGitで無視されます。著作権や利用許諾が不明な画像、配信者画像、個人用素材は公開リポジトリへ追加しないでください。

## 素材の配置

参照音声IDと連動させる素材は、次へ置きます。

```text
extension/assets/pet/local/voices/<refId>/pet.json
extension/assets/pet/local/voices/<refId>/spritesheet.png
```

共通のローカル素材は次へ置けます。

```text
extension/assets/pet/local/pet.json
extension/assets/pet/local/spritesheet.png
```

ひな形として`extension/assets/pet/local.example/pet.json`を利用できます。`spritesheetPath`は`pet.json`からの相対パスか、`assets/pet/...`から始まるパスで指定できます。対応画像形式はPNG、WebP、SVGです。透明部分はアルファチャンネルを保ったまま描画されます。

## 読み込み順

デスクトップペットは次の順で素材を探します。

1. `extension/assets/pet/local/voices/<refId>/pet.json`
2. `extension/assets/pet/local/voices/placeholder/pet.json`
3. `extension/assets/pet/local/pet.json`
4. `extension/assets/pet/pet.json`

選択中の素材が削除された、JSONが壊れている、画像が不足している場合は次の候補へフォールバックします。公開プレースホルダーまで読み込めない場合でも、Voice Bridge本体はペット素材の不足だけでは終了しません。

## `pet.json`

```json
{
  "id": "sample",
  "displayName": "Sample Pet",
  "spritesheetPath": "spritesheet.png",
  "columns": 6,
  "rows": 8,
  "frameWidth": 176,
  "frameHeight": 176,
  "displayScale": 0.5,
  "animations": {
    "idle": { "frames": [0, 5], "speed": 900 },
    "error": { "frames": [19], "speed": 900 }
  }
}
```

`displayScale`を省略した場合は既定の表示幅を使用します。`animations`がない場合やフレーム番号が不正な場合も、表示可能なフレームへ補正して停止画像として扱います。

## Windows上の実装

`LocalVoiceBridge.exe`から通知領域アプリを起動すると、透明なQtツールウィンドウとしてペットを1体表示します。タイトルバー、枠、四角い背景、影はありません。タスクバーやAlt+Tabへ通常アプリとして表示されないよう、Qtの`Tool`指定とWindowsのツールウィンドウ拡張スタイルを使用します。

位置と現在のペットIDは次へ保存されます。

```text
local-api/runtime/desktop-pet-settings.json
```

以前の設定で`visible=false`になっていても、現在の構成では起動時に表示へ戻します。Voice Bridgeが正常な場合は`idle`、起動失敗や異常時は`error`を表示します。
