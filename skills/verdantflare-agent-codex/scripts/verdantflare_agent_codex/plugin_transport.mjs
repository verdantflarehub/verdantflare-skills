// Private worker: JSONL to dynamic Codex plugin IPC. Never opens a TCP listener.
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import readline from 'node:readline';
import { randomUUID } from 'node:crypto';

const versions = {
  'thread-owner-discovery': 1,
  'thread-follower-start-turn': 2,
  'thread-follower-interrupt-turn': 4,
  'thread-follower-read-model-settings': 1,
  'thread-follower-load-complete-history': 1,
  'thread-follower-submit-user-input': 1,
  'thread-follower-submit-mcp-server-elicitation-response': 1,
  'thread-stream-following-changed': 1,
  'thread-unarchived': 1,
};
const extensionDir = process.argv[2];
if (extensionDir) {
  try {
    const extJs = fs.readFileSync(path.join(extensionDir, 'out', 'extension.js'), 'utf8');
    for (const key of Object.keys(versions)) {
      const match = extJs.match(new RegExp(`"${key}"\\s*:\\s*(\\d+)`));
      if (match) versions[key] = Number(match[1]);
    }
  } catch {}
}
const output = value => process.stdout.write(JSON.stringify(value) + '\n');
const socket = net.createConnection('\\\\.\\pipe\\codex-ipc');
let clientId = 'initializing-client';
let buffer = Buffer.alloc(0);
let closed = false;
const pending = new Map();
const subscriptions = new Map();
const send = message => {
  if (closed) throw new Error('disconnected');
  const bytes = Buffer.from(JSON.stringify(message));
  const frame = Buffer.alloc(4 + bytes.length);
  frame.writeUInt32LE(bytes.length);
  bytes.copy(frame, 4);
  socket.write(frame);
};
function request(method, params, targetClientId) {
  return new Promise((resolve, reject) => {
    const requestId = randomUUID();
    const timer = setTimeout(() => { pending.delete(requestId); reject(new Error('timeout')); }, 30000);
    pending.set(requestId, { resolve, reject, timer });
    // Local requests omit envelope hostId, matching the plugin's local sender.
    send({ type: 'request', requestId, sourceClientId: clientId, method, params,
           version: versions[method] ?? 0, targetClientId });
  });
}
const ready = new Promise((resolve, reject) => {
  socket.once('error', reject);
  socket.once('connect', async () => {
    try {
      const init = await request('initialize', { clientType: 'verdantflare-agent-codex' });
      clientId = init.result.clientId;
      resolve();
    } catch (error) { reject(error); }
  });
});
// Prevent unhandled rejection before the first incoming request.
ready.catch(() => {});
socket.on('data', chunk => {
  try {
    buffer = Buffer.concat([buffer, chunk]);
    while (buffer.length >= 4) {
      const length = buffer.readUInt32LE();
      if (length > 16 * 1024 * 1024) throw new Error('oversized-frame');
      if (buffer.length < length + 4) return;
      const message = JSON.parse(buffer.subarray(4, length + 4));
      buffer = buffer.subarray(length + 4);
      if (message.type === 'response') {
        const item = pending.get(message.requestId);
        if (!item) continue;
        clearTimeout(item.timer);
        pending.delete(message.requestId);
        item.resolve(message);
      } else if (message.type === 'client-discovery-request') {
        send({ type: 'client-discovery-response', requestId: message.requestId, response: { canHandle: false } });
      } else if (message.type === 'broadcast') {
        const thread = message.params?.conversationId;
        if (message.method === 'thread-stream-state-changed' && subscriptions.get(thread) === message.sourceClientId) {
          output({ method: 'plugin/state', params: message.params });
        }
        if (message.method === 'thread-stream-following-status-requested' && subscriptions.has(thread)) {
          following(thread, subscriptions.get(thread), true);
        }
      }
    }
  } catch { socket.destroy(); }
});
function following(threadId, owner, enabled) {
  send({ type: 'broadcast', method: 'thread-stream-following-changed', sourceClientId: clientId,
    version: versions['thread-stream-following-changed'] ?? 1, targetClientIds: [owner], params: { conversationId: threadId, hostId: 'local', following: enabled } });
}
socket.on('error', () => {});
socket.on('close', () => {
  closed = true;
  for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error('disconnected')); }
  pending.clear();
  output({ method: 'bridge/disconnected' });
});
const lines = readline.createInterface({ input: process.stdin });
lines.on('line', async line => {
  let incoming;
  try {
    incoming = JSON.parse(line);
    await ready;
    const { method, params = {} } = incoming;
    let result;
    if (method === 'subscribe') {
      subscriptions.set(params.thread_id, params.owner);
      following(params.thread_id, params.owner, true);
      result = { subscribed: true };
    } else if (method === 'unsubscribe') {
      if (subscriptions.has(params.thread_id)) following(params.thread_id, subscriptions.get(params.thread_id), false);
      subscriptions.delete(params.thread_id);
      result = { subscribed: false };
    } else if (method === 'notify_unarchived') {
      send({ type: 'broadcast', method: 'thread-unarchived', sourceClientId: clientId,
        version: versions['thread-unarchived'] ?? 1,
        params: { conversationId: params.thread_id, hostId: 'local' } });
      result = { broadcasted: true };
    } else if (Object.hasOwn(versions, method)) {
      result = await request(method, params.payload, params.owner);
    } else throw new Error('unsupported-method');
    output({ id: incoming.id, result });
  } catch {
    output({ id: incoming?.id ?? null, error: { code: -32000, message: 'Plugin transport request failed' } });
  }
});
lines.on('close', () => {
  if (!closed) for (const [thread, owner] of subscriptions) following(thread, owner, false);
  socket.destroy();
});
