/** Execute only pinned native Settings path operations in a synthetic context.
 * No application imports, profile access, credentials, network, or desktop.
 * This is a consumer check, not native ConfigEditor/Loader integration.
 */
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { runInNewContext } from 'node:vm';

const pin = '041a2682ed77a7025ceb9bf659b80db8b788c338cd1aa644f0bcc6bc122a4091';
try {
  if (process.argv.length !== 3) throw new Error('NATIVE_SETTINGS_SOURCE_REQUIRED');
  const raw = readFileSync(process.argv[2]);
  if (createHash('sha256').update(raw).digest('hex') !== pin) throw new Error('NATIVE_SETTINGS_PIN_MISMATCH');
  const text = raw.toString('utf8');
  function region(start, end) {
    if (text.indexOf(start) < 0 || text.indexOf(start) !== text.lastIndexOf(start)) throw new Error('NATIVE_SOURCE_SHAPE');
    const a = text.indexOf(start), b = text.indexOf(end, a);
    if (b <= a) throw new Error('NATIVE_SOURCE_SHAPE');
    return text.slice(a, b);
  }
  const functions = region('function isPlainObject(', 'function describeRejected(')
    + region('function member(', '/** Entry ids of the removed');
  const method = region('async mutate(ns, ops, expectedRevision)', '\n\tasync write(ns,');
  const input = readFileSync(0, 'utf8');
  if (Buffer.byteLength(input) > 1024 * 1024) throw new Error('NATIVE_INPUT_LIMIT');
  const context = { input };
  const result = runInNewContext(`${functions}
    const data = JSON.parse(input);
    const service = {
      ${method},
      async write(ns, change, revision, paths) {
        if (revision !== data.revision) throw new Error('SETTINGS_CONFLICT');
        const next = change(data.current, data.base, data.schema);
        return JSON.stringify({ next, ns, revision, paths });
      }
    };
    // The actual native method returns void. Capture its change callback output.
    let answer;
    const write = service.write;
    service.write = async function(...args) { answer = await write.apply(this, args); };
    service.mutate(...data.arguments).then(() => answer);
  `, context, { timeout: 1000 });
  const answer = await result;
  if (typeof answer !== 'string') throw new Error('NATIVE_RESULT_MISSING');
  process.stdout.write(answer + '\n');
} catch (error) {
  // Native exceptions may contain values; expose only a fixed diagnostic.
  process.stderr.write('NATIVE_SETTINGS_PROBE_FAILED\n');
  process.exitCode = 1;
}
