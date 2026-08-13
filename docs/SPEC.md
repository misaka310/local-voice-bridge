# Local Voice Bridge PROJECT SPEC

## 1. 元の目的

ChatGPTのテキスト回答をWindows上で自然にローカル音声再生し、通常利用者がブラウザ拡張・ローカルAPI・TTS内部構成を意識せず日常利用できる状態を提供する。

このリポジトリはChatGPTタブの回答生成中・回答完了未確認・確認済み・読み上げ中・生成エラーと、それを示すfavicon状態を所有する。ページ内作業メモは`73_chatgpt-tab-memo`の責務とし、混在させない。

## 2. 期待する最終結果

- `LocalVoiceBridge.exe`から通常利用を開始できる。
- ChatGPTへ通常どおりメッセージを送り、Auto有効時は回答完了後に先頭プレビュー（最大2行・80文字）が一度だけ自動読み上げされる。
- `Next`、`Regen`、`Replay`、`Stop`、`Ref`、`Volume`など既存の主要操作が意味どおり動く。
- 回答生成中・完了未確認・確認済み・読み上げ中・生成エラーが正しい状態表示とfaviconへ反映される。
- 拡張機能更新時はWindows Local Voice小窓または正式なagent経路から再読み込みでき、通常利用で`chrome://extensions`の手動操作を要求しない。
- 通常利用ではターミナルを表示しない。
- 再起動、拡張機能再接続、TTS再生失敗などから利用者が状態を理解して復旧できる。

### 完成扱いにしないもの

- ローカルAPIが200を返すだけ。
- TTSエンジン単体が音声を生成できるだけ。
- 拡張機能がロードされているだけ。
- mock E2EだけがPASSし、実ブラウザ経路を確認していない状態。
- 音声が出てもノイズ、途切れ、1文字だけの読み上げ、意図しないMuteなど通常利用を妨げる不具合が残る状態。
- 拡張機能更新後に毎回`chrome://extensions`の手動Reloadが必要な状態。
- Ready表示でもChatGPT回答の検出・再生経路が実際には動かない状態。

## 3. ユーザー操作・導線

通常利用者の主経路はWindows Local Voiceを起動し、ChatGPTを普段どおり使う1本とする。内部script、API、開発者向け診断画面を通常利用の入口にしない。

### Happy Path

1. ユーザーが`LocalVoiceBridge.exe`を起動する。
2. ローカルAPI、TTS、拡張機能接続がReadyになる。
3. ユーザーがChromeまたはBraveでChatGPTを開く。
4. Windows Local VoiceでAutoを有効にする。
5. ユーザーがChatGPTへメッセージを送る。
6. 回答生成中は生成中状態を表示する。
7. 回答完了を検出し、新しい回答の先頭プレビュー（最大2行・80文字）だけを一度ローカルTTSへ渡す。
8. 音声を再生し、読み上げ状態を表示する。
9. `Next`、`Replay`、`Regen`などを必要に応じて操作できる。
10. 次の会話でも同じ経路を継続できる。

### エラー時

- TTS生成失敗、再生失敗、ブラウザ切断、拡張機能更新待ちを区別して表示する。
- 拡張機能の自己再読み込みが可能なら正式なloopback経路で再読み込みし、`loadedVersion == expectedVersion`と再接続まで確認する。
- 自己再読み込み不能なら安全なbootstrapまたは制御経路を修復し、現在環境で不可能な場合だけblockedとする。
- ユーザー利用中のChrome / Brave既存タブ、入力欄、分割表示をテストや復旧へ流用しない。
- 話している途中に意図しないMuteへ遷移しない。

## 4. 制約条件

- Windows向けローカル補助アプリとして動作する。
- 通常利用でターミナルやコンソールを表示しない。
- TTSの標準経路はIrodori v3 directを維持する。
- ChatGPTタブ状態とfaviconの所有権は17に置き、73へ移植・複製しない。
- ブラウザ実機検証は隔離E2Eまたは専用プロファイルを使用し、ユーザーが利用中のブラウザを壊さない。
- 拡張機能更新は`browser-extension-update-delivery`の契約を守る。
- 設定、キュー、再生状態、Ref、Autoなど既存UXを内部都合で削除・置換しない。
- 通常利用者へ内部APIや複数の起動経路を選ばせない。
- ローカルAPIはloopback専用を維持し、全HTTPリクエストで`Host`をloopback名またはloopback IPへ限定する。POSTはJSONだけを受け付け、通常Webページ由来の`Origin`は拒否し、Chrome拡張またはOriginを持たない同一PCのネイティブクライアントだけを許可する。1リクエストのbodyは32 MiB以下に制限する。
- Local APIのレスポンスへユーザー名を含む絶対ファイルパスやローカルキャッシュの実パスを返さない。診断上のパスはローカルログまたは明示的な開発者向け経路だけで扱う。
- マイク録音の生音声と文字起こし履歴は保存しない。一方、再接続・Service Worker復旧・未配送イベントの再配信に必要なassistant返答チャンク、読み上げキュー、未ACKの文字起こしイベントはローカルruntime状態へ限定的に保存してよい。privacy-safeなstructured runtime event logはサイズ上限と有限世代でローテーションし、無制限に増加させない。
- 30個以上のChatGPTタブを開く通常利用を前提に、定常時のブラウザ負荷をタブ数へ比例して高頻度化させない。Local APIの制御pollはマイク会話オフ時5秒、マイク会話ONの待機時500ms、録音・文字起こし・送信中など低遅延が必要な間だけ100msを上限とする。
- 通常の回答生成中は、MutationObserverの関連イベントから生成状態を追跡しても、Auto読み上げのためにassistant DOM全体を毎回cloneして本文抽出しない。本文抽出は回答完了時、または実際にstreaming本文が必要なLive会話中に限定する。
- 同一ChatGPTタブ内の通常クリック・フォーカスだけで全登録タブへ状態broadcastしない。所有者・URL・タイトルなど共有状態が実際に変化した場合だけ全タブ通知する。

## 5. 非目標

- ChatGPTそのものの実装やモデル選択。
- `73_chatgpt-tab-memo`が所有するページ内メモ機能。
- OpenAI公式機能として振る舞うこと。
- 通常利用者へ開発者向けCLI、内部API、`chrome://extensions`操作を必須にすること。
- TTSエンジン単体のベンチマーク成功を最終成果とすること。

## 6. 受入条件

- [ ] `LocalVoiceBridge.exe`から通常利用を開始できる。
- [ ] Ready状態でChatGPTへメッセージを送り、回答完了を実ブラウザで検出できる。
- [ ] Auto有効時に新しい回答の先頭プレビュー（最大2行・80文字）を一度だけ自然に読み上げられる。
- [ ] `Next`、`Regen`、`Replay`、`Stop`の主要操作が既存仕様どおり動く。
- [ ] 回答生成中、完了未確認、確認済み、読み上げ中、生成エラーの状態表示とfaviconが整合する。
- [ ] 意図しないMuteや操作不能状態が残っていない。
- [ ] 拡張機能更新後、正式な再読み込み導線で新versionへ反映し再接続できる。
- [ ] 通常復旧でユーザーに`chrome://extensions`の手動Reloadを要求しない。
- [ ] アプリ再起動後も主要経路が復旧する。
- [ ] 通常利用でターミナルやコンソールが表示されない。
- [ ] 非loopback `Host`のGET/POST/OPTIONSは拒否され、通常WebページからLocal APIのPOST系変更操作を実行できず、拡張機能とWindows小窓の正常通信は維持される。
- [ ] Local APIレスポンスにユーザー固有の絶対ファイルパスが露出せず、structured runtime event logは有限サイズでローテーションされる。
- [ ] 30タブ想定で、アイドル中にsub-second全タブpollや同一タブ操作起点の全タブbroadcastが発生せず、通常生成中のAuto監視がassistant本文の全DOM cloneを繰り返さない。
- [ ] 関連unit/integration/mock E2Eと実ブラウザ経路の両方を確認している。

## 7. 検証方法

1. 変更に近いunit/integration testを実行する。
2. 公開ツリー、architecture、background、mock E2Eを含む既存CI相当を通す。
3. 隔離ブラウザまたは専用プロファイルでChatGPTの実ユーザー経路を確認する。
4. Auto読み上げ、Replay、Regen、Next、Stopを実操作する。
5. 拡張機能更新時は正式なreload経路を実行し、version一致と再接続を確認する。
6. Local Voice Bridgeを再起動して設定・接続・再生経路を再確認する。
7. 最後に通常利用者視点で、処理中・成功・失敗・再試行・復旧方法が理解できることを目視確認する。
