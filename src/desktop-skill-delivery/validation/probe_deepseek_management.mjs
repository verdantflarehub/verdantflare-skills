/** Official modules over an in-memory HTTP carrier, never desktop acceptance.
 * Run under Node filesystem permissions AND an external network-denial sandbox.
 * All tokens/cookies/synthetic credentials remain within this isolated process.
 */
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { readFileSync, readdirSync, lstatSync, realpathSync } from 'node:fs';
import { mkdir, writeFile, readFile, stat } from 'node:fs/promises';
import { resolve, join, relative, isAbsolute, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

const RECEIPT_PIN = '38434fe1574d170064080ab6b13d09755fa77ac6a2478fadad979c1e0aa1fe0b';
const hash = data => createHash('sha256').update(data).digest('hex');
const TARGET = { transport: 'streamable-http', enabled: true, url: 'https://studio.invalid/mcp',
  headers: { Authorization: 'Bearer ${credential:VF_STUDIO}' } };

export async function runManagementProbe(driver) {
  assert.equal(process.argv.length, 4);
  assert.equal(process.platform, 'darwin');
  assert.equal(process.permission?.has('child'), false);
  const extracted = realpathSync(process.argv[2]), output = resolve(process.argv[3]);
  const suffix = relative(resolve('.tmp/agent-skill-delivery'), output);
  assert(suffix && suffix !== '..' && !suffix.startsWith('..' + sep) && !isAbsolute(suffix));
  assert.equal(realpathSync(resolve(output, '..')), resolve(output, '..'));
  const receiptRaw = readFileSync(join(extracted, 'receipt.json'));
  assert.equal(hash(receiptRaw), RECEIPT_PIN);
  const source = JSON.parse(receiptRaw);
  assert.equal(source.dmg_sha256, '65ec0268d77b8e65786d51fd8b89bed13040f0e53e547d15ee2ca2e013ebeb40');
  assert.equal(source.detach_exit_code, 0);
  const declared = new Set(), directories = new Set();
  for (const member of source.files) {
    const parts = member.path.split('/');
    assert(parts.length > 3 && !parts.some(p => !p || p === '.' || p === '..'));
    assert(member.path.startsWith('dsh/node_modules/'));
    assert(!declared.has(member.path)); declared.add(member.path);
    for (let n = 1; n < parts.length; n++) directories.add(parts.slice(0, n).join('/'));
    for (let n = 1; n <= parts.length; n++) assert(!lstatSync(join(extracted, ...parts.slice(0, n))).isSymbolicLink());
    const bytes = readFileSync(join(extracted, ...parts));
    assert.equal(bytes.length, member.bytes); assert.equal(hash(bytes), member.sha256);
  }
  const actual = new Set();
  function inventory(directory, prefix = '') {
    for (const name of readdirSync(directory)) {
      const member = prefix + name, path = join(directory, name), info = lstatSync(path);
      assert(!info.isSymbolicLink());
      if (info.isDirectory()) { assert(directories.has(member)); inventory(path, member + '/'); }
      else { assert(info.isFile()); assert(member === 'receipt.json' || declared.has(member)); actual.add(member); }
    }
  }
  inventory(extracted); assert.equal(actual.size, declared.size + 1);
  const deps = join(extracted, 'dsh/node_modules/@deepseek-ai');
  const imp = name => import(pathToFileURL(join(deps, name, 'lib/index.js')).href);
  const [{ Context }, { default: Loader }, { default: ConfigEditor }, { default: Settings }, boot,
    { default: Manager }, { default: Credentials }, connectionPlugin, { default: Registry },
    { default: Access }, { default: Gateway }, { default: Controller }, launch] = await Promise.all([
    'cordis', 'cordis-plugin-loader', 'dsh-config-editor', 'dsh-settings', 'dsh-app-boot',
    'dsh-mcp-manager', 'dsh-credentials-local', 'dsh-client-connection', 'dsh-typert-registry',
    'dsh-api-remote-access', 'dsh-api-gateway', 'dsh-api-settings-controller', 'dsh-launch-environment'].map(imp));
  const { TYPERT } = await import(pathToFileURL(join(deps, 'dsh-api-settings-controller/lib/typert.host.js')).href);
  await mkdir(output, { mode: 0o700 });
  const profile = { home: join(output, 'home'), dir: join(output, 'profile'),
    patchPath: join(output, 'profile/cordis.patch.yml'), installAnchor: join(output, 'installation.json'),
    overlays: [], telemetryDisabledEnv: true };
  await mkdir(profile.home, { mode: 0o700 }); await mkdir(profile.dir, { mode: 0o700 });
  await writeFile(join(profile.dir, 'package.json'), JSON.stringify({ name: 'vf-management-probe', private: true,
    dsh: { profile: { bundles: [] } } }));
  await writeFile(join(output, 'base.yml'), '[]\n');
  await writeFile(profile.patchPath, '# Isolated inert fixture, not a user profile.\n' + JSON.stringify([
    { insert: [{ id: 'probe', name: 'cordis:vf-management-probe', config: { servers: {} } }] }
  ], null, 2) + '\n', { mode: 0o600 });
  const cases = [], modules = {};
  for (const name of ['dsh-client-connection', 'dsh-api-gateway', 'dsh-typert-registry', 'dsh-api-remote-access',
    'dsh-api-settings-controller', 'dsh-credentials-local', 'dsh-launch-environment']) {
    modules[name] = hash(readFileSync(join(deps, name, 'lib/index.js')));
  }
  modules.generated_host = hash(readFileSync(join(deps, 'dsh-api-settings-controller/lib/typert.host.js')));
  const ctx = new Context(); ctx.baseUrl = pathToFileURL(output + '/').href;
  const secrets = [], descriptors = {};
  let rpcId = 0;
  try {
    ctx.provide('launchEnvironment', launch.createLaunchEnvironmentSnapshot([]));
    await ctx.plugin(Loader); ctx.provide('profileContext', profile);
    function Probe() {} Probe.Config = Manager.Config;
    ctx.loader.builtins['vf-management-probe'] = Probe;
    await ctx.plugin(ConfigEditor); await ctx.plugin(Settings);
    await boot.mountRootInclude(ctx, join(output, 'base.yml'), boot.readProfilePatches('dsh', profile));
    await ctx.loader.await();
    await ctx.plugin(Credentials, { path: join(profile.home, '.credentials.yaml'), watch: false });
    await ctx.plugin(Registry); ctx.typert.register(TYPERT);
    await ctx.plugin(Access); await ctx.plugin(Controller); await ctx.plugin(Gateway, {});
    const connectionFiber = await ctx.plugin(connectionPlugin, {});
    assert(ctx.connection); assert(ctx.typertGateway); assert(ctx.credentials);
    assert.equal(ctx.get('webServer'), undefined);
    const host = 'localhost:43117', base = 'http://' + host;
    const request = (path, cookie, headers = {}) => ({ method: 'GET', url: path,
      headers: { host, ...(cookie ? { cookie } : {}), ...headers } });
    function exchange(path, headers = {}) {
      const res = { status: 0, headers: {}, ended: false,
        writeHead(status, values = {}) { this.status = status; this.headers = values; },
        end() { this.ended = true; } };
      ctx.connection.authorizeConnect(request(path, undefined, headers), res);
      assert(res.ended); return res;
    }
    assert.equal(exchange('/connect?token=invalid').status, 401);
    assert.equal(ctx.connection.admit(request('/api/settings/describe')).rejection, 401);
    cases.push({ name: 'missing-and-invalid-authorization', passed: true });
    const authUrl = new URL(ctx.connection.authenticatedUrl(base + '/connect'));
    secrets.push(...authUrl.searchParams.values());
    assert.equal(exchange(authUrl.pathname + authUrl.search, { origin: 'https://foreign.invalid' }).status, 403);
    const connected = exchange(authUrl.pathname + authUrl.search);
    assert.equal(connected.status, 302); assert.equal(connected.headers['location'], './api/remote.mux');
    const cookie = connected.headers['set-cookie'].split(';', 1)[0]; secrets.push(cookie);
    assert.equal(ctx.connection.admit(request('/api/settings/describe', cookie,
      { origin: 'https://foreign.invalid' })).rejection, 403);
    assert.equal(ctx.connection.admit(request('/api/settings/describe', cookie,
      { host: 'localhost:43118' })).rejection, 401);
    cases.push({ name: 'official-exchange-and-authority-origin-fence', passed: true });
    async function rpc(method, payload = {}, options = {}) {
      const incoming = new Request(base + '/api/' + method, { method: 'POST',
        headers: { 'content-type': 'application/json', host, cookie: options.cookie ?? cookie, ...options.headers },
        body: JSON.stringify({ type: 'client-request', rpcId: String(++rpcId), method, payload: { args: payload } }) });
      const admission = ctx.connection.admit({ method: incoming.method,
        url: new URL(incoming.url).pathname, headers: Object.fromEntries(incoming.headers) });
      if ('rejection' in admission) return { status: admission.rejection };
      const response = await ctx.connection.createSharedFetchHandler('/api').fetch(incoming, admission.peer);
      return { status: response.status, body: await response.json() };
    }
    async function ok(method, payload = {}) {
      const result = await rpc(method, payload); assert.equal(result.status, 200);
      assert.equal(result.body.result.ok, true, 'official RPC rejected ' + method);
      return result.body.result.value;
    }
    const describe = () => ok('settings/describe');
    // Test-only private pipes can retain this exact native backend while a
    // Python coordinator process exits. Never a desktop management endpoint.
    if (driver !== undefined) {
      assert.equal(typeof driver, 'function');
      return await driver({ rpc, describe, configDigest: async () => hash(await readFile(profile.patchPath)) });
    }
    const initial = await describe(); assert(initial.writable); assert(initial.hasDocument);
    assert.equal(initial.namespaces.length, 1); const before = initial.namespaces[0];
    assert.equal(before.ns, 'probe'); assert.deepEqual(before.user, {});
    descriptors.before = before;
    const credential = randomBytes(32).toString('hex'), other = randomBytes(32).toString('hex');
    secrets.push(credential, other);
    await ok('credentials/set', { ref: 'VF_OTHER', value: other });
    await ok('credentials/set', { ref: 'VF_STUDIO', value: credential });
    const view = await ok('credentials/describe', { refs: ['VF_STUDIO', 'VF_OTHER'] });
    assert(view.VF_STUDIO.configured); assert(view.VF_STUDIO.writable);
    assert.equal((await ctx.credentials.resolve('VF_STUDIO')).value, credential);
    assert.equal((await stat(join(profile.home, '.credentials.yaml'))).mode & 0o777, 0o600);
    assert(!JSON.stringify(view).includes(credential));
    cases.push({ name: 'official-credential-write-describe-resolve-private-file', passed: true });
    const installed = await ok('settings/mutate', { ns: 'probe', ops: [
      { op: 'set', path: ['servers', 'vf-studio'], value: TARGET }], expectedRevision: before.revision });
    const after = (await describe()).namespaces[0];
    assert.deepEqual(after.user.servers['vf-studio'], TARGET);
    assert.equal(after.revision, installed.revision); assert(after.revision > before.revision);
    descriptors.installed = after;
    const bytes = await readFile(profile.patchPath);
    const erase = { ns: 'probe', ops: [{ op: 'unset', path: ['servers', 'vf-studio'] }], expectedRevision: after.revision };
    for (const [options, status] of [[{ cookie: '' }, 401], [{ cookie: cookie + 'invalid' }, 401],
      [{ headers: { origin: 'https://foreign.invalid' } }, 403], [{ headers: { host: 'localhost:43118' } }, 401]]) {
      assert.equal((await rpc('settings/mutate', erase, options)).status, status);
      assert.deepEqual(await readFile(profile.patchPath), bytes);
    }
    cases.push({ name: 'unauthorized-mutation-refused-before-file-write', passed: true });
    const stale = await rpc('settings/mutate', { ns: 'probe', ops: [
      { op: 'unset', path: ['servers', 'vf-studio'] }], expectedRevision: before.revision });
    assert.equal(stale.status, 200); assert.equal(stale.body.result.ok, false);
    assert.deepEqual(await readFile(profile.patchPath), bytes);
    const malformed = await rpc('settings/mutate', { ns: 'probe', ops: [], expectedRevision: 'invalid' });
    assert.equal(malformed.body.result.ok, false); assert.deepEqual(await readFile(profile.patchPath), bytes);
    cases.push({ name: 'generated-schema-and-stale-revision-refusal', passed: true });
    const updatedTarget = { ...TARGET, url: 'https://studio.invalid/updated-mcp' };
    await ok('settings/mutate', { ns: 'probe', ops: [
      { op: 'set', path: ['servers', 'vf-studio'], value: updatedTarget }], expectedRevision: after.revision });
    const updated = (await describe()).namespaces[0]; descriptors.updated = updated;
    assert.deepEqual(updated.user.servers['vf-studio'], updatedTarget);
    await ok('settings/mutate', { ns: 'probe', ops: [
      { op: 'unset', path: ['servers', 'vf-studio'] }, { op: 'unset', path: ['servers'] }],
      expectedRevision: updated.revision });
    const removed = (await describe()).namespaces[0]; descriptors.removed = removed;
    assert.deepEqual(removed.user, before.user); assert(!Object.hasOwn(removed.value.servers, 'vf-studio'));
    cases.push({ name: 'official-settings-add-update-remove-independent-readback', passed: true });
    await ok('credentials/unset', { ref: 'VF_STUDIO' });
    const revoked = await ok('credentials/describe', { refs: ['VF_STUDIO', 'VF_OTHER'] });
    assert(!revoked.VF_STUDIO.configured); assert(revoked.VF_OTHER.configured);
    assert.equal(await ctx.credentials.resolve('VF_STUDIO'), undefined);
    assert.equal((await ctx.credentials.resolve('VF_OTHER')).value, other);
    cases.push({ name: 'credential-revocation-preserves-other-reference', passed: true });
    const socketAdmission = ctx.connection.admitConnection(request('/api/remote.mux', cookie));
    assert(!('rejection' in socketAdmission)); await socketAdmission.release();
    assert.equal((await rpc('settings/describe')).status, 401);
    cases.push({ name: 'released-peer-cookie-does-not-revive', passed: true });
    // A fresh official exchange, followed by Connection replacement. Not an Electron restart.
    const second = exchange(authUrl.pathname + authUrl.search);
    assert.equal(second.status, 302); const secondCookie = second.headers['set-cookie'].split(';', 1)[0];
    secrets.push(secondCookie);
    await connectionFiber.dispose(); await ctx.plugin(connectionPlugin, {});
    assert.equal(ctx.connection.admit(request('/api/settings/describe', secondCookie)).rejection, 401);
    cases.push({ name: 'connection-replacement-rejects-old-peer', passed: true });
    const report = { source_receipt_sha256: RECEIPT_PIN, vendor_files: declared.size, modules, cases,
      transport: 'in-memory-http-carrier', official_credentials_provider: true, webserver_started: false,
      desktop_started: false, user_profile_accessed: false, mcp_manager_instantiated: false,
      desktop_acceptance: false, native_connection_replacement_only: true };
    for (const raw of [JSON.stringify(report), JSON.stringify(descriptors)]) {
      for (const secret of secrets) assert(!raw.includes(secret), 'Do not persist synthetic secrets or authentication material');
      assert(!raw.includes(extracted)); assert(!raw.includes(output));
    }
    await writeFile(join(output, 'descriptors.json'), JSON.stringify(descriptors, null, 2) + '\n', { mode: 0o600 });
    await writeFile(join(output, 'receipt.json'), JSON.stringify(report, null, 2) + '\n', { mode: 0o600 });
  } finally { await ctx.fiber.dispose(); }
}

// Vendor exception messages may contain local paths or authentication data.
// Keep details in assertions during development only; the reusable probe emits a safe constant.
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  runManagementProbe().catch(() => { process.stderr.write('DEEPSEEK_NATIVE_MANAGEMENT_PROBE_FAILED\n'); process.exitCode = 1; });
}
