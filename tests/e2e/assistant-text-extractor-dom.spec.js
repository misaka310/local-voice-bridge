'use strict';

const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const ROOT = path.resolve(__dirname, '../..');
const CONTENT_TEXT_CORE = fs.readFileSync(path.join(ROOT, 'extension', 'content-text-core.js'), 'utf8');
const ASSISTANT_SOURCE_FILTER = fs.readFileSync(path.join(ROOT, 'extension', 'assistant-source-filter.js'), 'utf8');
const ASSISTANT_TEXT_EXTRACTOR = fs.readFileSync(path.join(ROOT, 'extension', 'assistant-text-extractor.js'), 'utf8');

const CASES = [
  {
    name: 'compact provider chip with reversed host word order and a count',
    html: [
      '<p>冒頭の本文です。</p>',
      '<div class="source-chip">',
      '<a href="https://one.google.com/about/plans">Google One</a>',
      '<span>+2</span>',
      '<a href="https://support.google.com/googleone/answer/9004013">Google ヘルプ</a>',
      '</div>',
      '<p>続きの本文です。</p>',
    ].join(''),
    contain: ['冒頭の本文です。', '続きの本文です。'],
    exclude: ['Google One', 'Google ヘルプ', '+2'],
  },
  {
    name: 'explicit citation metadata removes a localized label and count',
    html: [
      '<p>回答本文です。</p>',
      '<div data-testid="citation-group">',
      '<a aria-label="source" href="https://support.example.com/help">公式ヘルプ</a>',
      '<span>3 sources</span>',
      '</div>',
    ].join(''),
    contain: ['回答本文です。'],
    exclude: ['公式ヘルプ', '3 sources'],
  },
  {
    name: 'repeated bare provider labels are treated as UI noise',
    html: [
      '<p>本文を残します。</p>',
      '<div class="source-chip">',
      '<a href="https://github.com/example/one">GitHub</a>',
      '<a href="https://github.com/example/two">GitHub</a>',
      '<a href="https://github.com/example/three">GitHub</a>',
      '</div>',
    ].join(''),
    contain: ['本文を残します。'],
    exclude: ['GitHubGitHub', 'GitHub GitHub'],
  },
  {
    name: 'two ordinary provider links inside one paragraph remain speech content',
    html: '<p><a href="https://github.com/">GitHub</a>と<a href="https://one.google.com/">Google One</a>を比較します。</p>',
    contain: ['GitHub', 'Google One', '比較します。'],
    exclude: [],
  },
  {
    name: 'two ordinary provider links inside a generic div remain speech content',
    html: '<div><a href="https://github.com/">GitHub</a>と<a href="https://one.google.com/">Google One</a>を比較します。</div>',
    contain: ['GitHub', 'Google One', '比較します。'],
    exclude: [],
  },
  {
    name: 'an ordinary bare provider link inside prose remains speech content',
    html: '<p>詳しくは<a href="https://github.com/">GitHub</a>を確認してください。</p>',
    contain: ['GitHub', '確認してください。'],
    exclude: [],
  },
  {
    name: 'descriptive localized link text remains speech content',
    html: '<p>詳しくは<a href="https://one.google.com/about/plans">Google Oneの料金</a>を確認してください。</p>',
    contain: ['Google Oneの料金', '確認してください。'],
    exclude: [],
  },
  {
    name: 'a source-code aria label does not masquerade as citation UI',
    html: '<p><a aria-label="source code" href="https://example.com/code">ソースコード</a>を確認します。</p>',
    contain: ['ソースコード', '確認します。'],
    exclude: [],
  },
  {
    name: 'a source-code class name does not masquerade as citation UI',
    html: '<p><a class="source-code-link" href="https://example.com/code">ソースコード</a>を確認します。</p>',
    contain: ['ソースコード', '確認します。'],
    exclude: [],
  },
  {
    name: 'a resource class name does not masquerade as source metadata',
    html: '<p><a class="resource-link" href="https://example.com/docs">資料リンク</a>を確認します。</p>',
    contain: ['資料リンク', '確認します。'],
    exclude: [],
  },
  {
    name: 'descriptive repository link remains speech content',
    html: '<p><a href="https://github.com/example/repo">example/repo</a>を確認しました。</p>',
    contain: ['example/repo', '確認しました。'],
    exclude: [],
  },
  {
    name: 'a provider link beside a signed numeric value remains speech content',
    html: '<p><a href="https://one.google.com/">Google One</a>は容量を+2GB増やせます。</p>',
    contain: ['Google One', '+2GB', '増やせます。'],
    exclude: [],
  },
  {
    name: 'ordinary provider prose and a year remain intact',
    html: '<p>Google One 2026年版を比較します。</p>',
    contain: ['Google One 2026年版を比較します。'],
    exclude: [],
  },
  {
    name: 'a source chip does not delete a normal sibling link',
    html: [
      '<div>',
      '<span class="source-chip"><a href="https://github.com/example/repo">GitHub</a><span>+2</span></span>',
      '<a href="https://example.com/docs">Documentation</a>',
      '</div>',
    ].join(''),
    contain: ['Documentation'],
    exclude: ['GitHub', '+2'],
  },
  {
    name: 'a chip nested beside prose does not delete the prose container',
    html: [
      '<section>',
      '<span>前半です。</span>',
      '<span class="source-chip"><a href="https://github.com/openai/example">GitHub</a><span>+4</span></span>',
      '<span>後半です。</span>',
      '</section>',
    ].join(''),
    contain: ['前半です。', '後半です。'],
    exclude: ['GitHub', '+4'],
  },
];

test('assistant text extraction handles the source-chip DOM matrix', async ({ page }) => {
  await page.setContent('<main id="fixture"></main>');
  await page.addScriptTag({ content: CONTENT_TEXT_CORE });
  await page.addScriptTag({ content: ASSISTANT_SOURCE_FILTER });
  await page.addScriptTag({ content: ASSISTANT_TEXT_EXTRACTOR });

  for (const fixture of CASES) {
    await test.step(fixture.name, async () => {
      const extracted = await page.evaluate((html) => {
        const node = document.createElement('div');
        node.innerHTML = html;
        document.querySelector('#fixture').replaceChildren(node);
        return globalThis.LocalVoiceAssistantText.extractAssistantText(node);
      }, fixture.html);

      for (const value of fixture.contain) expect(extracted).toContain(value);
      for (const value of fixture.exclude) expect(extracted).not.toContain(value);
    });
  }
});
