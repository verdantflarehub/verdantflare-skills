/** Pinned native Settings -> ConfigEditor -> Loader in an isolated profile.
 * A self-owned inert plugin uses the actual Manager's static schema. The MCP
 * Manager itself is never instantiated. This is not desktop or MCP acceptance.
 * Run with Node filesystem permissions; deny network externally if required.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, lstatSync, realpathSync } from 'node:fs';
import { mkdir, writeFile, readFile, stat } from 'node:fs/promises';
import { resolve, join, relative, isAbsolute, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

const RECEIPT_PIN = '38434fe1574d170064080ab6b13d09755fa77ac6a2478fadad979c1e0aa1fe0b';
const hash = data => createHash('sha256').update(data).digest('hex');
const CORE = {
  'dsh-config-editor/lib/index.js': '17e953c5ef913d368b288e3d64e952276ea08a46967bfdd430534b69b9d0c039',
  'dsh-settings/lib/index.js': '041a2682ed77a7025ceb9bf659b80db8b788c338cd1aa644f0bcc6bc122a4091',
  'dsh-mcp-manager/lib/index.js': 'bab3d1bd02237e522cd1fdead26311113cbfbd0d4880bd24175f7011722367ff',
  'dsh-app-boot/lib/index.js': 'a8d2138bb274bd982079df14a615f6c0d5d7589c9faeae81973128a72293b6de',
  'dsh-atomic-write/lib/index.js': '872d7c1e6e9b267775199bdd3632870f82780d0a5ba047febc93fde94fde53f7',
  'cordis/lib/index.js': '627409afa06a265f7253200d3bc4648e88805c39fd3768568eb9b9518a31fe8f',
  'cordis-plugin-loader/lib/index.js': '4f0a93a1ef5bb09bd6e1c64b30df11be25efd46c316b9505c5ddf308974367a6',
  'schemastery/lib/index.mjs': 'cfcbe46c7416bdb35ce891c252152bbf519e08199bede5930dd7cb880c65f1ef',
};
const KEY = 'vf-studio';
const TARGET = { transport: 'streamable-http', url: 'https://studio.invalid/mcp', enabled: true,
  headers: { Authorization: 'Bearer ${credential:VF_STUDIO}' } };
const OTHER = { transport: 'stdio', command: 'INERT_FIXTURE_NEVER_EXECUTED', env: { KEY: 'SYNTHETIC_ONLY' } };
const ROW = { id: 'probe', name: 'cordis:vf-editor-probe', config: { servers: { other: OTHER } } };

async function main() {
  assert.equal(process.argv.length, 4);
  assert.equal(process.platform, 'darwin', 'This probe has only been exercised on macOS');
  assert.equal(process.permission?.has('child'), false, 'Use Node permission mode without child-process permission');
  const extracted = realpathSync(process.argv[2]);
  const output = resolve(process.argv[3]);
  const temporary = resolve('.tmp/agent-skill-delivery');
  const suffix = relative(temporary, output);
  assert(suffix && !suffix.startsWith('..' + sep) && suffix !== '..' && !isAbsolute(suffix));
  assert.equal(realpathSync(resolve(output, '..')), resolve(output, '..'), 'Output parent must be canonical');
  const receiptRaw = readFileSync(join(extracted, 'receipt.json'));
  assert.equal(hash(receiptRaw), RECEIPT_PIN);
  const source = JSON.parse(receiptRaw);
  assert.equal(source.dmg_sha256, '65ec0268d77b8e65786d51fd8b89bed13040f0e53e547d15ee2ca2e013ebeb40');
  assert.equal(source.detach_exit_code, 0);
  const declared = new Set();
  const directories = new Set();
  for (const member of source.files) {
    const parts = member.path.split('/');
    assert(parts.length > 3 && !parts.some(p => !p || p === '.' || p === '..'));
    assert(member.path.startsWith('dsh/node_modules/'));
    assert(!declared.has(member.path)); declared.add(member.path);
    for (let n = 1; n < parts.length; n++) directories.add(parts.slice(0, n).join('/'));
    const path = join(extracted, ...parts);
    for (let n = 1; n <= parts.length; n++) assert(!lstatSync(join(extracted, ...parts.slice(0, n))).isSymbolicLink());
    const bytes = readFileSync(path);
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
  inventory(extracted);
  assert.equal(actual.size, declared.size + 1);
  const deps = join(extracted, 'dsh/node_modules');
  for (const [member, pin] of Object.entries(CORE)) assert.equal(hash(readFileSync(join(deps, '@deepseek-ai', member))), pin);
  const imp = name => import(pathToFileURL(join(deps, '@deepseek-ai', name, 'lib/index.js')).href);
  const [{ Context }, { default: Loader }, { default: ConfigEditor }, { default: Settings }, boot, { default: Manager }] =
    await Promise.all(['cordis', 'cordis-plugin-loader', 'dsh-config-editor', 'dsh-settings', 'dsh-app-boot', 'dsh-mcp-manager'].map(imp));
  await mkdir(output, { mode: 0o700 });
  const cases = [];
  const descriptors = {};
  async function scenario(name, options, body) {
    const root = join(output, name); await mkdir(root, { mode: 0o700 });
    const profile = { home: join(root, 'home'), dir: join(root, 'profile'), patchPath: join(root, 'profile/cordis.patch.yml'),
      installAnchor: join(root, 'installation.json'), overlays: options.overlays ?? [], telemetryDisabledEnv: true };
    await mkdir(profile.home); await mkdir(profile.dir);
    await writeFile(join(profile.dir, 'package.json'), JSON.stringify({ name: 'vf-native-editor-fixture', private: true, dsh: { profile: { bundles: [] } } }));
    await writeFile(join(root, 'base.yml'), '[]\n');
    const patches = [{ insert: [options.row ?? ROW] }, ...(options.user ? [{ id: 'probe', name: ROW.name, config: options.user }] : [])];
    await writeFile(profile.patchPath, '# Native integration fixture, never a user profile.\n' + JSON.stringify(patches, null, 2) + '\n', { mode: 0o640 });
    const ctx = new Context(); ctx.baseUrl = pathToFileURL(root + '/').href;
    try {
      await ctx.plugin(Loader); ctx.provide('profileContext', profile);
      // The real schema is used; the plugin has no connection or credential behavior.
      function Probe() {}
      Probe.Config = Manager.Config;
      ctx.loader.builtins['vf-editor-probe'] = Probe;
      await ctx.plugin(ConfigEditor); await ctx.plugin(Settings);
      await boot.mountRootInclude(ctx, join(root, 'base.yml'), boot.readProfilePatches('dsh', profile));
      await ctx.loader.await();
      const describe = () => {
        const rows = ctx.settings.describe({ redactSecrets: true });
        assert.equal(rows.length, 1); assert.equal(rows[0].ns, 'probe'); return rows[0];
      };
      const before = await readFile(profile.patchPath);
      const result = await body({ ctx, profile, describe, before });
      cases.push({ name, passed: true, ...result });
    } finally { await ctx.fiber.dispose(); }
  }
  const set = (ctx, revision) => ctx.settings.mutate('probe', [{ op: 'set', path: ['servers', KEY], value: TARGET }], revision);
  const unset = (ctx, revision) => ctx.settings.mutate('probe', [{ op: 'unset', path: ['servers', KEY] }], revision);
  await scenario('materialization-and-native-schema', {}, async ({ ctx, profile, describe }) => {
    const before = describe(), fresh = describe();
    assert.notEqual(before.schema.uid, fresh.schema.uid);
    assert.deepEqual(before.user, {});
    await set(ctx, fresh.revision);
    const after = describe();
    assert.deepEqual(after.user.servers.other, OTHER);
    assert.deepEqual(after.user.servers[KEY], TARGET);
    assert.equal((await stat(profile.patchPath)).mode & 0o777, 0o600);
    descriptors.before = before; descriptors.fresh = fresh; descriptors.after = after;
    descriptors.set_arguments = ['probe', [{ op: 'set', path: ['servers', KEY], value: TARGET }], fresh.revision];
    await unset(ctx, after.revision);
    const removed = describe();
    assert.deepEqual(removed.user, {}); assert(!Object.hasOwn(removed.value.servers, KEY));
    descriptors.materialization_removed = removed;
    return { native_schema_graph: true, uid_renumbered: true, native_user_materialized: true, native_mode: '0600', removed_user_empty: true };
  });
  await scenario('explicit-inherited-override-collapse', { user: { servers: { other: OTHER } } }, async ({ ctx, describe }) => {
    const before = describe(); assert.deepEqual(before.user.servers.other, OTHER);
    await set(ctx, before.revision); const installed = describe();
    await unset(ctx, installed.revision); const after = describe();
    assert.deepEqual(after.user, {}); assert.deepEqual(after.value.servers.other, before.value.servers.other);
    descriptors.collapse_before = before; descriptors.collapse_installed = installed; descriptors.collapse_after = after;
    // Forensic capability test only: this full-layer operation is never emitted
    // by the VF planner, and all values belong to the isolated inert fixture.
    await set(ctx, after.revision);
    await ctx.settings.replace('probe', before.user, describe().revision);
    const replaced = describe(); assert.deepEqual(replaced.user, {});
    descriptors.collapse_replaced = replaced;
    return { original_explicit_user_lost: true, effective_unchanged: true,
      native_replace_also_collapses: true, full_uninstall_recovery: false };
  });
  await scenario('mixed-explicit-defaults', { user: { servers: { custom: { transport: 'stdio', command: 'INERT_CUSTOM', enabled: true } } } }, async ({ ctx, describe }) => {
    const before = describe(); await set(ctx, before.revision); const after = describe();
    assert.deepEqual(after.user.servers.custom, before.user.servers.custom);
    await unset(ctx, after.revision); const removed = describe();
    assert.deepEqual(removed.user.servers.custom, before.user.servers.custom);
    assert(!Object.hasOwn(removed.user.servers, 'other'));
    assert.deepEqual(removed.user, before.user);
    descriptors.mixed_before = before; descriptors.mixed_after = after; descriptors.mixed_removed = removed;
    return { explicit_defaults_preserved: true, explicit_user_layer_restored: true, inherited_dictionary_masked_by_user_dictionary: true };
  });
  await scenario('origin-update-remove', {}, async ({ ctx, describe }) => {
    const before = describe(); await set(ctx, before.revision); const installed = describe();
    const target = { ...TARGET, url: 'https://studio.invalid/updated-mcp' };
    await ctx.settings.mutate('probe', [{ op: 'set', path: ['servers', KEY], value: target }], installed.revision);
    const updated = describe(); await unset(ctx, updated.revision); const removed = describe();
    assert.deepEqual(removed.user, before.user); assert.deepEqual(removed.user, {});
    descriptors.origin_update = { before, installed, updated, removed, target };
    return { native_update_performed: true, exact_original_user_restored: true };
  });
  await scenario('empty-inherited-config', { row: { ...ROW, config: {} } }, async ({ ctx, describe }) => {
    const before = describe(); await set(ctx, before.revision); const installed = describe();
    await unset(ctx, installed.revision); const removed = describe();
    assert.deepEqual(before.user, {}); assert.deepEqual(removed.user, { servers: {} });
    descriptors.empty_inherited = { before, installed, removed };
    return { original_user_empty: true, native_empty_servers_remain: true, exact_user_restoration: false };
  });
  for (const config of [{}, { servers: {} }]) {
    await scenario('empty-container-cleanup-' + (Object.hasOwn(config, 'servers') ? 'explicit-base' : 'absent-base'),
      { row: { ...ROW, config } }, async ({ ctx, describe }) => {
      const before = describe(); await set(ctx, before.revision); const installed = describe();
      const operations = [{ op: 'unset', path: ['servers', KEY] }, { op: 'unset', path: ['servers'] }];
      await ctx.settings.mutate('probe', operations, installed.revision);
      const removed = describe(); assert.deepEqual(removed.user, before.user); assert.deepEqual(removed.user, {});
      assert(!Object.hasOwn(removed.value.servers, KEY)); assert.deepEqual(removed.value, before.value);
      descriptors['empty_cleanup_' + (Object.hasOwn(config, 'servers') ? 'explicit' : 'absent')] = { before, installed, operations, removed };
      return { exact_user_restored: true, effective_unchanged: true, same_transaction_operations: 2 };
    });
  }
  await scenario('empty-container-concurrent-insertion', { row: { ...ROW, config: {} } },
    async ({ ctx, profile, describe }) => {
    await set(ctx, describe().revision); const installed = describe();
    const external = JSON.stringify([{ insert: [{ ...ROW, config: {} }] },
      { id: 'probe', name: ROW.name, config: { servers: { [KEY]: TARGET, other: OTHER } } }]) + '\n';
    await writeFile(profile.patchPath, external);
    const operations = [{ op: 'unset', path: ['servers', KEY] }, { op: 'unset', path: ['servers'] }];
    await assert.rejects(ctx.settings.mutate('probe', operations, installed.revision),
      error => error.code === 'SETTINGS_CONFLICT');
    assert.equal(await readFile(profile.patchPath, 'utf8'), external);
    const after = describe(); assert.deepEqual(after.user.servers.other, OTHER);
    assert.deepEqual(after.user.servers[KEY], TARGET);
    return { stale_revision_rejected_inside_native_lock: true, inserted_connection_preserved: true,
      exact_external_bytes_preserved: true };
  });
  for (const config of [{}, { servers: {} }]) {
    const name = 'explicit-empty-user-' + (Object.hasOwn(config, 'servers') ? 'explicit-base' : 'absent-base');
    await scenario(name, { row: { ...ROW, config }, user: { servers: {} } }, async ({ ctx, describe }) => {
      const before = describe(); await set(ctx, before.revision); const installed = describe();
      await unset(ctx, installed.revision); const removed = describe();
      assert.deepEqual(before.user, { servers: {} });
      assert.deepEqual(removed.user, Object.hasOwn(config, 'servers') ? {} : { servers: {} });
      descriptors[name] = { before, installed, removed };
      return { original_explicit_empty_user: true, original_user_restored: !Object.hasOwn(config, 'servers') };
    });
  }
  await scenario('schema-rejection-before-write', {}, async ({ ctx, profile, describe, before }) => {
    await assert.rejects(ctx.settings.mutate('probe', [{ op: 'set', path: ['servers', KEY], value: { transport: 123 } }], describe().revision));
    assert.deepEqual(await readFile(profile.patchPath), before); assert(!Object.hasOwn(describe().value.servers, KEY));
    return { exact_file_preserved: true };
  });
  await scenario('external-revision-rejection', {}, async ({ ctx, profile, describe }) => {
    const observed = describe();
    const external = JSON.stringify([{ insert: [ROW] }, { id: 'probe', config: { servers: { external: { transport: 'stdio', command: 'INERT_EXTERNAL' } } } }]) + '\n';
    await writeFile(profile.patchPath, external);
    await assert.rejects(set(ctx, observed.revision), error => error.code === 'SETTINGS_CONFLICT');
    assert.equal(await readFile(profile.patchPath, 'utf8'), external);
    assert(!Object.hasOwn(describe().value.servers, KEY));
    return { stale_revision_rejected: true, external_file_preserved: true };
  });
  await scenario('reconcile-failure-rollback', {}, async ({ ctx, profile, describe, before }) => {
    let injected = 0, committedMode;
    ctx.on('app-boot/config-reload', () => {
      if (!injected && readFileSync(profile.patchPath, 'utf8').includes(KEY)) {
        injected++; committedMode = lstatSync(profile.patchPath).mode & 0o777;
        throw new Error('VF_SYNTHETIC_RECONCILE_FAILURE');
      }
    });
    await assert.rejects(set(ctx, describe().revision), /VF_SYNTHETIC_RECONCILE_FAILURE/);
    assert.equal(injected, 1); assert.equal(committedMode, 0o600);
    assert.deepEqual(await readFile(profile.patchPath), before);
    assert(!Object.hasOwn(describe().value.servers, KEY));
    // Native rollback restores bytes but enforces 0600 rather than original 0640.
    assert.equal((await stat(profile.patchPath)).mode & 0o777, 0o600);
    return { native_write_observed_before_failure: true, exact_bytes_restored: true, effective_restored: true, rollback_mode: '0600', injection: 'self-owned config-reload listener' };
  });
  await scenario('upper-overlay-write-rejection', { overlays: [{ id: 'probe', config: { servers: { [KEY]: { ...TARGET, url: 'https://overlay.invalid/mcp' } } } }] }, async ({ ctx, profile, describe, before }) => {
    await assert.rejects(set(ctx, describe().revision), /overridden by a home patch or command-line overlay/);
    assert.deepEqual(await readFile(profile.patchPath), before);
    return { overridden_candidate_rejected: true, exact_file_preserved: true };
  });
  await scenario('replaced-entry-before-write', {}, async ({ ctx, profile, before }) => {
    const entry = ctx.configEditor.entries().find(row => row.options.id === 'probe'); assert(entry);
    const external = JSON.stringify([{ insert: [{ ...ROW, id: 'replacement' }] }]) + '\n';
    await writeFile(profile.patchPath, external);
    await assert.rejects(ctx.configEditor.edit(entry, current => current), /Configuration entry changed during reload/);
    assert.equal(await readFile(profile.patchPath, 'utf8'), external);
    return { replaced_entry_rejected: true, external_file_preserved: true };
  });
  await writeFile(join(output, 'descriptors.json'), JSON.stringify(descriptors, null, 2) + '\n', { mode: 0o600 });
  const result = { schema: 'vf.deepseek-native-editor-probe/v1', passed: true, cases,
    probe_sha256: hash(readFileSync(new URL(import.meta.url))),
    official_dmg_sha256: source.dmg_sha256, extraction_receipt_sha256: RECEIPT_PIN,
    verified_source_files: source.files.length, core_sha256: CORE, actual_modules: ['Settings', 'ConfigEditor', 'Loader', 'profile composition', 'atomic write', 'Manager static Config'],
    inert_target_plugin: true, manager_instantiated: false, desktop_started: false, credentials_resolved: false,
    mcp_connections_started: false, desktop_acceptance: false, mcp_acceptance: false, complete_adapter: false };
  await writeFile(join(output, 'receipt.json'), JSON.stringify(result, null, 2) + '\n', { mode: 0o600 });
  process.stdout.write(JSON.stringify({ passed: true, cases: cases.length, verified_source_files: source.files.length }) + '\n');
}
main().catch(() => { process.stderr.write('DEEPSEEK_NATIVE_EDITOR_PROBE_FAILED\n'); process.exitCode = 1; });
