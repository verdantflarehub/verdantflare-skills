'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {startBridge, rewriteHtml, extendMarkdown} = require('./bridge');
const MarkdownIt = require('markdown-it');

function request(url, headers = {}, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request(url, {headers, method}, res => {
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({status:res.statusCode, headers:res.headers, body:Buffer.concat(chunks).toString()}));
    });
    req.on('error', reject); req.end();
  });
}

test('real HTTP: junction, auth, ranges, cache, traversal, removal and streaming', async t => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'gdrive-test-'));
  t.after(() => fs.rmSync(temp, {recursive:true, force:true}));
  const root = path.join(temp, 'project'), drive = path.join(temp, 'drive');
  fs.mkdirSync(root); fs.mkdirSync(drive); fs.mkdirSync(path.join(root, 'docs'));
  fs.symlinkSync(drive, path.join(root, '.gdrive'), 'junction');
  for (const bucket of ['managed', 'output']) {
    fs.mkdirSync(path.join(drive, bucket));
    fs.writeFileSync(path.join(drive, bucket, '图 space.mp4'), '0123456789');
  }
  fs.writeFileSync(path.join(drive, 'output', 'secret.json'), '{}');
  fs.writeFileSync(path.join(drive, 'output', 'empty.png'), '');
  const outside = path.join(temp, 'outside'); fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, 'secret.png'), 'secret');
  fs.symlinkSync(outside, path.join(drive, 'escape'), 'junction');
  let roots = [root];
  const bridge = await startBridge(() => roots);
  t.after(() => bridge.close());
  const doc = {scheme:'file', fsPath:path.join(root, 'docs', 'test.md')};
  const url = bridge.url('../.gdrive/output/图%20space.mp4', doc);
  const base = url.slice(0, url.indexOf('/output/'));
  assert.match(url, /^http:\/\/127\.0\.0\.1:\d+\/[a-f0-9]{32}\/0\//);
  const whole = await request(url); assert.equal(whole.status, 200); assert.equal(whole.body, '0123456789');
  assert.equal((await request(url, {}, 'HEAD')).body, '');
  assert.equal((await request(url, {}, 'POST')).status, 405);
  assert.equal((await request(url.replace(/\/[a-f0-9]{32}\//, '/bad/'))).status, 403);
  assert.equal((await request(url, {Host:'example.com'})).status, 403);
  assert.equal((await request(url, {'If-None-Match':whole.headers.etag})).status, 304);
  for (const [range, body] of [['bytes=2-4','234'],['bytes=-3','789'],['bytes=8-','89'],['bytes=8-99','89']]) {
    const result = await request(url, {Range:range}); assert.equal(result.status, 206); assert.equal(result.body, body);
  }
  for (const range of ['bytes=20-','bytes=4-2','bytes=-0','bytes=-','bytes=0-1,3-4','garbage'])
    assert.equal((await request(url, {Range:range})).status, 416);
  assert.equal((await request(url, {Range:'bytes=2-4', 'If-Range':whole.headers.etag})).status, 200);
  assert.equal((await request(base + '/output/empty.png')).status, 200);
  assert.equal((await request(base + '/output/secret.json')).status, 403);
  assert.equal((await request(base + '/escape/secret.png')).status, 403);
  assert.equal((await request(base + '/%2e%2e%5coutside/secret.png')).status, 403);
  assert.equal((await request(base + '/output/%ZZ.png')).status, 404);
  assert.equal((await request(base + '/managed/%E5%9B%BE%20space.mp4')).status, 200);
  assert.equal(bridge.url('https://example.com/.gdrive/a.png', doc), null);
  assert.equal(bridge.url('../.gdrive-other/a.png', doc), null);
  assert.equal(bridge.url('../.gdrive/a.png', {...doc, scheme:'vscode-remote'}), null);
  roots = []; assert.equal((await request(url)).status, 404);
  roots = [outside]; assert.equal((await request(url)).status, 404);
});

test('HTML exact media attributes, quoted/unquoted values and exclusions', () => {
  const map = value => value.startsWith('../.gdrive/') ? 'http://127.0.0.1:1/token/0/image.png' : null;
  for (const tag of ['img', 'video', 'source', 'audio']) {
    assert.match(rewriteHtml(`<${tag} src = '../.gdrive/a.png'>`, map), /src = "http:\/\/127/);
    assert.match(rewriteHtml(`<${tag} src=../.gdrive/a.png>`, map), /data-gdrive-preview/);
  }
  assert.match(rewriteHtml('<video poster="../.gdrive/a.png">', map), /poster="http/);
  for (const input of ['<a href="../.gdrive/a.png">', '<iframe src="../.gdrive/a.png">',
    '<img data-src="../.gdrive/a.png">', '<!-- <img src="../.gdrive/a.png"> -->',
    '<script>const a = \'<img src="../.gdrive/a.png">\';</script>']) assert.equal(rewriteHtml(input,map),input);
});

test('Markdown renderer preserves outer link rule and uses original image src', () => {
  const originalLink = () => '<a href="../.gdrive/a.png">';
  const md = {renderer:{rules:{link_open:originalLink, image:(tokens, i) => tokens[i].attrGet('src')}}, utils:{}};
  const attrs = new Map([['src','webview:rewritten'],['data-src','../.gdrive/a.png']]);
  const token = {attrGet:k=>attrs.get(k), attrSet:(k,v)=>attrs.set(k,v)};
  extendMarkdown(md, {url:raw=>raw === '../.gdrive/a.png' ? 'http://127.0.0.1/media' : null});
  assert.equal(md.renderer.rules.image([token],0,{},{}), 'http://127.0.0.1/media');
  assert.equal(md.renderer.rules.link_open, originalLink);
  assert.equal(attrs.get('data-gdrive-preview'),'1');
});

test('real Markdown parser renders nested images, entities, fenced code and HTML media', async t => {
  const root = path.resolve('preview-fixture');
  const bridge = await startBridge(() => [root]);
  t.after(() => bridge.close());
  const md = extendMarkdown(new MarkdownIt({html:true}), bridge);
  const env = {currentDocument:{scheme:'file', fsPath:path.join(root, 'docs', 'review.md')}};
  const out = md.render('[![图](../.gdrive/output/a%20b.png)](../.gdrive/output/a%20b.png)\n\n' +
    '<video poster="../.gdrive/managed/a.png"><source src="../.gdrive/output/a&amp;b.mp4"></video>\n\n' +
    '```html\n<img src="../.gdrive/output/example.png">\n```', env);
  assert.match(out, /href="\.\.\/\.gdrive\/output\/a%20b.png"/);
  assert.match(out, /src="http:\/\/127\.0\.0\.1:\d+\/[a-f0-9]+\/0\/output\/a%20b.png"/);
  assert.match(out, /output\/a%26b.mp4/);
  assert.match(out, /poster="http/);
  assert.match(out, /&lt;img src=&quot;\.\.\/\.gdrive\/output\/example.png&quot;&gt;/);
});
