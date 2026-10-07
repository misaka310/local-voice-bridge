# DESK2 Frontend / DESK Generation Backend Design

## Purpose

Local Voice Bridgeを、DESK2が日常利用のフロントエンドとキャラクター資産を所有し、DESKはGPU音声生成だけを担当する2台構成へ整理する。

利用者がDESK側のLocal Voice Bridge GUI/trayを起動・管理する必要はない。DESK側へキャラクター別の参照音声やUI設定を永続複製しない。

## Required outcome

### DESK2 (frontend / main PC)

DESK2が次を所有する。

- Chrome / Brave拡張
- ChatGPTタブ状態、favicon、Auto、Next、Replay、Regen、Stop
- Local Voice BridgeのWindows小窓 / tray
- Local API `127.0.0.1:8717`
- Local APIのユーザー設定とcontrol state
- `referenceVoice`の選択状態
- キャラクター一覧
- キャラクターごとの参照音声 `voice.wav`
- 参照テキスト
- デスクトップペット等の表示資産
- 生成済み音声の保存と再生

DESK2自身はIrodori/CUDA音声生成を実行しない。DESK2にはフロント実行に必要な最小依存だけを導入し、IrodoriモデルやCUDA版PyTorchを要求しない。

### DESK (sub PC / generation backend)

DESKが所有するのは生成に必要な実行基盤だけとする。

- NVIDIA GPU / CUDA
- Irodori runtime
- Irodori model/cache
- generation worker
- リクエスト単位の一時的な参照音声・参照テキスト
- 一時的な生成結果

DESKに次を永続配置しない。

- キャラクター一覧
- キャラクター別reference voice library
- `referenceVoice`の利用者設定
- Local Voice Bridge GUI / tray
- ChatGPT拡張
- デスクトップペット
- DESK用Local Voice Bridge Startup登録
- Local API `8717` listener

## Current problem

現行のDESK2 `LocalVoiceBridgeRemoteClient` は、DESKの `127.0.0.1:8717` をSSH越しにDESK2の `127.0.0.1:8717` へ丸ごと中継している。

そのためLocal API、設定、reference voice解決、生成処理が実質DESK側にある。`voice_service.py`も `referenceVoice` IDを生成側ローカルの `reference/voices/<id>/voice.wav` に解決する。

この構成では、DESKのLocal Voice Bridgeを停止するとDESK2のLocal Voice Bridge全体が使えなくなり、キャラクター資産もDESKに複製しなければならない。

## Selected architecture

8717全体をリモート中継しない。

DESK2で通常のLocal Voice Bridge Local APIを動かし、`VoiceRuntime`の生成関数だけをremote generation adapterへ差し替える。既存`VoiceRuntime`は生成と再生を分離しているため、生成だけDESK、再生はDESK2という境界を維持できる。

```text
ChatGPT / Extension
        |
        v
DESK2 Local Voice Bridge :8717
  - UI / tray
  - settings / control state
  - character/reference assets
  - queue / playback / favicon
        |
        | existing SSH trust
        | text + profile + reference wav/text
        v
DESK generation worker
  - Irodori model / CUDA
  - no UI / no Local API
  - no character library
        |
        | generated WAV bytes + metadata
        v
DESK2 Local Voice Bridge
  - save normal generated audio
  - quality check
  - local playback
```

## Network and firewall

実機確認済みのネットワーク境界をそのまま使用する。

- DESK LAN: `192.168.0.66/24`
- DESK2 Wi-Fi: `192.168.0.12/24`
- DESK OpenSSH: TCP/22
- DESK Windows Firewall `OpenSSH-Server-In-TCP`: remote address `192.168.0.12` only
- DESK sshd listenerは現在 `0.0.0.0:22` だが、FirewallでDESK2以外を遮断している

生成用の新しいLAN公開ポート、HTTP listener、Tailscale listenerを追加しない。既存SSH許可ルールを広げない。

SSHクライアントはDESK2 Local APIプロセス内のPythonライブラリとして実装する。`ssh.exe`をhidden/CreateNoWindowプロセスとして常駐起動する方式、VBS、GUI注入は使わない。既存のSSH鍵と`known_hosts`を利用し、未知host keyは拒否する。

## Frontend dependency boundary

現行`requirements-core.txt`はIrodori生成依存まで含むため、DESK2 frontend用途には重すぎる。

frontend modeでは、UI・loopback API・ローカル再生・SSH transportに必要な依存だけを導入する。Irodori/CUDA/PyTorch/model cacheはDESK2へ要求しない。

DESKの既存reading/Irodori環境は生成workerが利用する。

DESK2の`server_supervisor`はremote backend modeではlocal CUDA/model preflightを実行しない。代わりにfrontend runtimeとSSH/backend疎通を別状態として扱う。DESKがofflineでもDESK2 Local API自体は起動し続ける。

## Generation transport

既存のDESK2 -> DESK SSH trustを再利用する。

生成workerはSSH channelのstdin/stdout上でフレーム化されたrequest/responseを扱う。workerのためのlisten portは作らない。

### Request data

生成要求は最低限次を含む。

- protocol version
- request id
- text
- TTS profile / live flag
- voice prompt/instruction
- selected reference voiceの有無
- reference audio bytes（選択時のみ）
- reference text（存在する場合のみ）

キャラクターIDは診断用metadataとして送ってよいが、DESK側の永続ファイル解決には使わない。

### Response data

- protocol version
- success / failure
- generated WAV bytes
- resolved TTS profile
- supplied reference audioが使用されたか
- bounded diagnostic error code/message

DESK側の絶対パス、秘密鍵、ローカルcache pathはDESK2へ返さない。

## Reference voice ownership

`referenceVoice` IDとreference voice libraryの正本はDESK2。

生成時、DESK2は選択中IDからローカルのreference assetを解決し、そのリクエストに必要な音声・テキストだけをDESKへ送る。

DESK workerは受け取ったreference assetを一時ディレクトリへ置いてIrodoriへ渡し、生成完了・失敗・キャンセルのいずれでも一時物を削除する。

これによりDESK側へ `reference/voices/asuka` 等を永続複製しない。

## Lifecycle

### DESK2

Local Voice Bridgeは従来どおり通常のWindows Startup対象にできる。

Local API `127.0.0.1:8717` はDESK2ローカルで動く。

remote generation backendが未接続でも、UI、設定、favicon、tab stateは動作する。音声生成だけをbackend unavailable/degradedとして扱う。

### DESK

- Local Voice Bridge Startup: disabled / absent
- Local Voice Bridge tray: not running
- Local API `8717`: not running
- generation worker: DESK2のSSH生成channelが必要な間だけ起動

DESKログイン時にLocal Voice Bridge GUI/trayを勝手に起動しない。

## Failure behavior

- DESK offline: DESK2 frontend stays alive; generation returns backend-unavailable
- SSH reconnect: generation adapter reconnects without extension reload
- worker crash: current generation fails with bounded error; next request may create a new channel/worker
- DESK2 restart: normal frontend state restores locally; backend connection is re-established lazily
- reference asset missing on DESK2: reject before remote generation
- malformed/oversized worker frame: reject without writing outside the bounded temp/output directories
- DESK must never silently fall back to owning frontend settings
- DESK2 must never silently fall back to local AMD/iGPU generation

## Migration

1. Keep DESK Startup/tray/8717 disabled as it is now.
2. Add remote-generation worker mode to the existing repository without starting `server.py`/tray on DESK.
3. Add DESK2 remote generation adapter using in-process SSH transport.
4. Add frontend-only dependency/setup path for DESK2.
5. Move reference resolution before the remote boundary so `voice.wav` and reference text originate on DESK2.
6. Configure DESK2 Local API for remote generation backend while keeping `127.0.0.1:8717` local.
7. Retire `LocalVoiceBridgeRemoteClient` whole-API proxy after new path passes acceptance.
8. Verify no DESK Startup entry, tray process, or 8717 listener returns.
9. Keep the existing single-PC local generation path supported unless removal is separately approved.

## Acceptance criteria

- DESK2 `127.0.0.1:8717/health` is served by DESK2, not proxied to DESK.
- DESK2 Windows UI, settings, browser tab registry and favicon remain functional when DESK is offline.
- DESK has no Local Voice Bridge Startup link, tray process, or port 8717 listener.
- DESK keeps Irodori/CUDA/model/cache and generation worker only.
- A character/reference voice present only on DESK2 can successfully generate on DESK.
- The corresponding character directory does not exist on DESK before or after generation.
- Generated WAV returns to DESK2, passes the existing quality check and is played through the existing playback path.
- Backend loss reports a generation-specific degraded/error state rather than making the whole DESK2 Local API disappear.
- Reconnecting DESK does not require extension reload.
- Existing favicon/Auto/queue tests remain green.
- Architecture/runtime-boundary checks remain green; no new listening port is introduced.
- DESK firewall remains restricted to SSH from DESK2 `192.168.0.12`; no broader inbound allow rule is added.
- Existing single-PC local generation mode remains functional.

## Non-goals

- Running Irodori on DESK2 AMD iGPU.
- Copying the full character library to DESK.
- Re-enabling DESK Local Voice Bridge tray or Startup.
- Exposing a DESK generation service over LAN/Internet/Tailscale HTTP.
- Moving browser/tab/favicons/control UI ownership to DESK.
