'use strict';

const DEMO_REPLY = [
  'これはオートをオンにした後に届いた新しい返答です。',
  '冒頭のプレビューだけが自動で読み上げられ、長い返答の続きはNextから再生できます。',
  'Replayは直前の音声を聞き直し、Regenは現在の部分を生成し直します。',
  '音声生成と再生ファイルの取得は、このパソコン内のローカルAPIだけで行われます。',
].join('\n');

function fixtureHtml() {
  return `<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local Voice Demo Fixture</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}*{box-sizing:border-box}
body{margin:0;background:#202123;color:#ececf1;min-height:100vh}.notice{position:fixed;z-index:3;top:12px;left:50%;transform:translateX(-50%);padding:10px 18px;border:1px solid #5b8def;border-radius:12px;background:#111827;color:#dbeafe;font-weight:700}.sidebar{position:fixed;inset:0 auto 0 0;width:230px;background:#171717;padding:22px 16px;border-right:1px solid #303030}.brand{font-weight:800;font-size:18px}.main{margin-left:230px;min-height:100vh;padding-top:64px}.chat{width:min(760px,calc(100vw - 310px));margin:auto;padding:30px 0 150px}.turn{display:grid;grid-template-columns:38px 1fr;gap:14px;padding:18px 10px;border-radius:14px;margin-bottom:10px}.turn.user{background:#2b2c30}.avatar{width:32px;height:32px;border-radius:9px;display:grid;place-items:center;font-weight:800;background:#10a37f}.user .avatar{background:#565869}.role{font-weight:750;margin-bottom:8px}.message{font-size:16px;line-height:1.75}.composer{position:fixed;left:230px;right:0;bottom:0;padding:24px 0;background:linear-gradient(transparent,#202123 30%)}.composer-inner{width:min(760px,calc(100vw - 310px));margin:auto;border:1px solid #555;border-radius:17px;background:#303136;padding:12px;display:flex;align-items:center;gap:12px}.prompt{flex:1;color:#d4d4d8}.send{border:0;border-radius:10px;padding:10px 18px;font-weight:800;cursor:pointer}.hint{text-align:center;color:#a1a1aa;font-size:12px;margin-top:8px}
</style>
</head>
<body>
<div class="notice">ローカルデモフィクスチャです。実際のChatGPT画面ではありません。</div>
<aside class="sidebar"><div class="brand">ChatGPT fixture</div></aside>
<main class="main"><section class="chat" id="chat">
<article class="turn user"><div class="avatar">私</div><div><div class="role">あなた</div><div class="message">この文章を要約してください。</div></div></article>
<article class="turn assistant" data-testid="conversation-turn-assistant"><div class="avatar">AI</div><div><div class="role">ChatGPT</div><div class="message" data-message-author-role="assistant" data-message-id="existing-reply">これはオートをオンにする前から表示されている返答です。この返答は読み上げられません。</div></div></article>
</section>
<div class="composer"><div class="composer-inner"><div class="prompt">新しい返答を表示して動作を確認</div><button class="send" id="add-reply">送信</button></div><div class="hint">Autoをオンにしてから送信してください</div></div></main>
<script>
const reply=${JSON.stringify(DEMO_REPLY)};
document.querySelector('#add-reply').addEventListener('click',()=>{
  const button=document.querySelector('#add-reply');button.disabled=true;
  const turn=document.createElement('article');turn.className='turn assistant';turn.dataset.testid='conversation-turn-assistant';
  turn.innerHTML='<div class="avatar">AI</div><div><div class="role">ChatGPT</div><div class="message" data-message-author-role="assistant" data-message-id="new-reply"></div></div>';
  document.querySelector('#chat').append(turn);
  setTimeout(()=>{turn.querySelector('.message').textContent=reply;const copy=document.createElement('button');copy.dataset.testid='copy-turn-action-button';copy.setAttribute('aria-label','Copy');turn.append(copy);window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'});},500);
});
</script>
</body></html>`;
}

module.exports = { DEMO_REPLY, fixtureHtml };
