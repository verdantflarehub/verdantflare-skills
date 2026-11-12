'use strict';
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');

const MIME = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg',
  '.webp':'image/webp','.gif':'image/gif','.svg':'image/svg+xml',
  '.bmp':'image/bmp','.ico':'image/x-icon','.avif':'image/avif',
  '.mp4':'video/mp4','.webm':'video/webm','.ogv':'video/ogg',
  '.ogg':'audio/ogg','.mp3':'audio/mpeg','.wav':'audio/wav','.m4a':'audio/mp4'};

function inside(child, parent) {
  const rel = path.relative(parent, child);
  return rel !== '' && rel !== '..' && !rel.startsWith('..' + path.sep) && !path.isAbsolute(rel);
}

async function startBridge(getRoots) {
  const token = crypto.randomBytes(16).toString('hex');
  // Stable IDs prevent a removed workspace's URL from addressing a different root.
  const roots = [];
  let port;
  const server = http.createServer((req, res) => {
    const end = (status, headers = {}) => { res.writeHead(status, headers); res.end(); };
    try {
      if (!['GET', 'HEAD'].includes(req.method)) return end(405, {Allow:'GET, HEAD'});
      if (req.headers.host !== `127.0.0.1:${port}`) return end(403);
      const parts = (req.url || '').split('?')[0].split('/');
      if (parts.length < 4 || parts[0] !== '' || parts[1] !== token || !/^\d+$/.test(parts[2])) return end(403);
      const root = roots[Number(parts[2])];
      if (!root || !getRoots().includes(root)) return end(404);
      const segments = parts.slice(3).map(decodeURIComponent);
      if (segments.some(s => !s || s === '.' || s === '..' || /[\\/:\0]/.test(s))) return end(403);
      const base = fs.realpathSync(path.join(root, '.gdrive'));
      const target = fs.realpathSync(path.join(base, ...segments));
      if (!inside(target, base)) return end(403);
      const mime = MIME[path.extname(target).toLowerCase()];
      if (!mime) return end(403);
      const stat = fs.statSync(target);
      if (!stat.isFile()) return end(404);
      const etag = `W/"${stat.size}-${stat.mtimeMs}"`;
      const headers = {'Content-Type':mime, 'Accept-Ranges':'bytes', ETag:etag,
        'Cache-Control':'no-cache', 'X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'none'; sandbox"};
      if ((req.headers['if-none-match'] || '').split(/\s*,\s*/).some(v => v === etag || v === '*')) return end(304, headers);
      let start = 0, finish = stat.size - 1, status = 200;
      // A weak ETag cannot satisfy If-Range: send the complete current representation.
      if (req.headers.range && !req.headers['if-range'] && req.method !== 'HEAD') {
        const match = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
        if (!match || (!match[1] && !match[2])) return end(416, {'Content-Range':`bytes */${stat.size}`});
        start = match[1] ? Number(match[1]) : Math.max(0, stat.size - Number(match[2]));
        finish = match[1] && match[2] ? Math.min(Number(match[2]), stat.size - 1) : stat.size - 1;
        if (!Number.isSafeInteger(start) || !Number.isSafeInteger(finish) || start > finish || start >= stat.size) return end(416, {'Content-Range':`bytes */${stat.size}`});
        status = 206;
        headers['Content-Range'] = `bytes ${start}-${finish}/${stat.size}`;
      }
      headers['Content-Length'] = Math.max(0, finish - start + 1);
      if (req.method === 'HEAD' || stat.size === 0) return end(status, headers);
      const stream = fs.createReadStream(target, {start, end:finish});
      stream.once('error', () => { if (!res.headersSent) end(404); else res.destroy(); });
      res.once('close', () => stream.destroy());
      stream.once('open', () => { res.writeHead(status, headers); stream.pipe(res); });
    } catch { if (!res.headersSent) end(404); else res.destroy(); }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { port = server.address().port; resolve(); });
  });
  return {
    url(raw, doc) {
      if (!raw || !doc || doc.scheme !== 'file' || /^[a-z][a-z0-9+.-]*:|^[#\\/]/i.test(raw)) return null;
      let decoded;
      try { decoded = decodeURIComponent(raw.split(/[?#]/)[0]); } catch { return null; }
      const candidate = path.resolve(path.dirname(doc.fsPath), decoded);
      for (const root of getRoots()) {
        const base = path.join(root, '.gdrive');
        if (!inside(candidate, base)) continue;
        if (!roots.includes(root)) roots.push(root);
        return `http://127.0.0.1:${port}/${token}/${roots.indexOf(root)}/` +
          path.relative(base, candidate).split(path.sep).map(encodeURIComponent).join('/');
      }
      return null;
    },
    close() { return new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }); }
  };
}

function rewriteHtml(html, map, unescape = s => s) {
  // Consume comments/raw-text elements as a whole so examples and scripts stay untouched.
  return html.replace(/<!--[\s\S]*?-->|<(script|style|textarea)\b[^>]*>[\s\S]*?<\/\1\s*>|<(?:img|video|source|audio)\b(?:"[^"]*"|'[^']*'|[^'">])*>/gi, tag => {
    if (!/^<(img|video|source|audio)\b/i.test(tag)) return tag;
    let changed = false;
    const result = tag.replace(/(\s)([^\s=<>]+)(\s*=\s*)(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g,
      (full, space, attr, eq, double, single, bare) => {
        if (!/^(src|poster)$/i.test(attr)) return full;
        const value = map(unescape(double ?? single ?? bare));
        if (!value) return full;
        changed = true;
        return `${space}${attr}${eq}"${value}"`;
      });
    return changed ? result.replace(/\s*\/?>(?=$)/, ' data-gdrive-preview="1">') : result;
  });
}

function extendMarkdown(md, bridge, onMedia = () => {}) {
  const image = md.renderer.rules.image || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const token = tokens[idx];
    const mapped = bridge.url(token.attrGet('data-src') || token.attrGet('src'), env && env.currentDocument);
    if (mapped) { token.attrSet('src', mapped); token.attrSet('data-gdrive-preview', '1'); onMedia(); }
    return image(tokens, idx, options, env, self);
  };
  for (const kind of ['html_block', 'html_inline']) {
    const previous = md.renderer.rules[kind];
    md.renderer.rules[kind] = (tokens, idx, options, env, self) => rewriteHtml(
      previous ? previous(tokens, idx, options, env, self) : tokens[idx].content,
      raw => { const result = bridge.url(raw, env && env.currentDocument); if (result) onMedia(); return result; },
      md.utils && md.utils.unescapeAll);
  }
  return md;
}
module.exports = {startBridge, inside, rewriteHtml, extendMarkdown};
