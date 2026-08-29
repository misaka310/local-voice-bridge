# Local Voice Bridge 拡張機能の導入・更新

この拡張機能は現在Chrome Web Storeでは配布していないため、Chrome / Braveの「パッケージ化されていない拡張機能」として導入します。通常版Chromeでは、Web Store外の任意CRXを恒久的に自動導入する方法は提供されていません。

## 初回導入

1. Chromeでは `chrome://extensions`、Braveでは `brave://extensions` を開きます。
2. 右上の「デベロッパーモード」をオンにします。
3. 「パッケージ化されていない拡張機能を読み込む」を押します。
4. このリポジトリの `extension` フォルダを選択します。
5. 開いているchatgpt.comのタブが自動で接続されることを確認します。タブの再読み込みは不要です。

Local Voice小窓のタブ数が実際に開いているChatGPTタブ数へ変われば導入完了です。

## 詳細設定

Windows Local Voice小窓の`詳細設定`を押すと、拡張機能が所有する設定画面を開きます。読み上げる最大行数・最大文字数、文字起こしモデル、文字起こし後の送信前猶予を変更できます。日常操作の`Voice`、`Volume`、`マイク会話`、`Auto`、`Next`、`Regen`、`Stop`、`Replay`はWindows Local Voice小窓で行います。`Voice`は既存の`referenceVoice`設定の表示名です。小窓を使えない場合は、Chrome / Braveの拡張機能アイコンを右クリックして`オプション`を開くこともできます。

## リポジトリ更新後

拡張機能のソースが更新されても、すでに読み込まれている拡張機能は自動更新されません。Windows Local Voice小窓に「拡張機能の再読み込みが必要」と表示された場合は、次を行います。

1. Windows Local Voice小窓の`拡張機能を再読み込み`を押します。
2. 開いているchatgpt.comのタブは触らず、小窓のタブ数が自動で復元するまで待ちます。
3. 接続済みの旧版が自己再読み込みに対応していないと小窓が明示した場合だけ、Chromeでは`chrome://extensions`、Braveでは`brave://extensions`を開き、Local Voice Bridgeのカードにある再読み込みボタンを押します。これはフォールバック手順です。

## フォルダを移動した場合

元の拡張機能を一度削除し、移動後の `extension` フォルダを選び直してください。拡張機能IDが変わる場合があるため、古い登録を残したまま二重導入しないでください。
