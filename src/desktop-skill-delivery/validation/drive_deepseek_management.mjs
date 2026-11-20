/** Test-only stdio carrier retaining ONE isolated official native backend.
 * No product bridge, HTTP listener, desktop process, or user credentials.
 */
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import { runManagementProbe } from './probe_deepseek_management.mjs';

runManagementProbe(async ({ rpc, describe, configDigest }) => {
  const generation = randomUUID();
  const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
  emit({ ready: true, generation, isolated_native_fixture: true });
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  try {
    for await (const line of input) {
      assert(Buffer.byteLength(line) <= 1048576);
      let result;
      try {
        const command = JSON.parse(line);
        assert(command !== null && typeof command === 'object' && !Array.isArray(command));
        if (command.action === 'close') {
          assert.deepEqual(Object.keys(command), ['action']); emit({ ok: true }); return;
        }
        if (command.action === 'observe') {
          assert.deepEqual(Object.keys(command), ['action']);
          result = { view: await describe(), file_sha256: await configDigest(), generation };
        } else if (command.action === 'mutate') {
          assert.deepEqual(Object.keys(command).sort(), ['action', 'mutation']);
          const [ns, ops, expectedRevision] = command.mutation;
          assert.equal(ns, 'probe'); assert(Array.isArray(ops));
          // Only self-owned fixture settings. No credential/controller passthrough.
          assert(ops.every(op => ['set', 'unset'].includes(op.op) && Array.isArray(op.path) &&
            op.path[0] === 'servers' && (op.path.length === 1 || op.path.length === 2 &&
              ['vf-studio', 'test-other'].includes(op.path[1]))));
          const response = await rpc('settings/mutate', { ns, ops, expectedRevision });
          assert.equal(response.status, 200);
          if (!response.body.result.ok) {
            const code = response.body.result.error?.code;
            result = { refused: /^[a-zA-Z0-9/_-]{1,80}$/.test(code) ? code : 'NATIVE_REFUSAL' };
          } else result = { submitted: true };
        } else throw new Error('UNKNOWN_TEST_COMMAND');
        emit({ ok: true, ...result });
      } catch { emit({ ok: false, code: 'DEEPSEEK_TEST_CARRIER_REJECTED' }); }
    }
  } finally { input.close(); }
}).catch(() => { process.stderr.write('DEEPSEEK_NATIVE_MANAGEMENT_DRIVER_FAILED\n'); process.exitCode = 1; });
