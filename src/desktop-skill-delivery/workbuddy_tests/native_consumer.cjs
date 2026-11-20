'use strict';
// Executes only fixed, previously extracted parser functions. No app or network.
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2]);
assert.equal(crypto.createHash('sha256').update(source).digest('hex'),
  '06756026779070ba66d04233a5507085cbb710689b34f8962b138393214dbe71');
const text = source.toString('utf8');
function extract(name) {
  const marker = `function ${name}(`;
  const at = text.indexOf(marker);
  assert(at >= 0 && text.indexOf(marker, at + marker.length) < 0);
  const end = text.indexOf('\n}', at);
  assert(end > at);
  return text.slice(at, end + 2);
}
const names = ['isRecord$1', 'parseMcpServerEntry', 'parseOneIdAuthInjection',
  'toTransport', 'toTimeoutMs', 'toStringList', 'toStringRecord', 'toScalarString'];
const ctx = vm.createContext({URL});
vm.runInContext(names.map(extract).join('\n'), ctx, {timeout: 1000});
ctx.input = JSON.parse(fs.readFileSync(0, 'utf8'));
assert.equal(new URL(ctx.input.url).href, ctx.input.url);
const parsed = JSON.parse(JSON.stringify(vm.runInContext('parseMcpServerEntry(input)', ctx, {timeout: 1000})));
assert.equal(parsed.transport.kind, 'http');
assert.equal(parsed.transport.url, ctx.input.url);
assert.equal(parsed.transport.headers, undefined);
assert.equal(parsed.transport.command, undefined);
assert.equal(parsed.timeoutMs, 30000);
assert.equal(parsed.disabled, false);
process.stdout.write(JSON.stringify({native_config_consumed: true,
  desktop_acceptance: 'not_run', business_acceptance: 'not_run'}));
