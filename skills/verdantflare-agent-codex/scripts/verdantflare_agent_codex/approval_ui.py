"""Local, explicit human approval surface; never called by the MCP respond tool."""
import asyncio
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from urllib.parse import parse_qs


def decisions(payload):
    method, params = payload['method'], payload['params']
    if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
        allowed = params.get('availableDecisions')
        return {key: {'decision': key} for key in ('accept', 'decline', 'cancel')
                if allowed is None or key in allowed}
    if method == 'item/permissions/requestApproval':
        requested = params.get('permissions')
        if isinstance(requested, dict):
            return {'accept': {'permissions': requested, 'scope': 'turn'},
                    'decline': {'permissions': {}, 'scope': 'turn'}}
    return {}


class ApprovalUI:
    def __init__(self, engine):
        self.engine = engine
        self.loop = asyncio.get_running_loop()
        self.entries = {}
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Do not log capability URLs or approval payloads.

            def reply(self, status, body):
                data = body.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                # no-referrer can make a browser's form POST Origin opaque (null).
                # Keep same-origin forms usable without leaking the URL cross-site.
                self.send_header('Referrer-Policy', 'same-origin')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(data)

            def handle_request(self, submit=False):
                self.connection.settimeout(5)
                if self.headers.get('Host') != ui.address:
                    return self.reply(403, 'Invalid host')
                origin = self.headers.get('Origin')
                if submit and origin is not None and origin != ui.origin:
                    return self.reply(403, 'Invalid origin')
                if submit and origin != ui.origin:
                    return self.reply(403, 'Origin required')
                entry = ui.entries.get(self.path)
                if not entry:
                    return self.reply(404, 'Unknown approval')
                task_id, pending_id, csrf = entry
                choice = None
                if submit:
                    try:
                        length = int(self.headers.get('Content-Length', '0'))
                        if not 0 < length <= 1024 or self.headers.get('Transfer-Encoding'):
                            return self.reply(400, 'Invalid body')
                        values = parse_qs(self.rfile.read(length).decode('utf-8'), strict_parsing=True)
                    except (ValueError, UnicodeError, TimeoutError):
                        return self.reply(400, 'Invalid form')
                    if values.get('csrf') != [csrf] or len(values.get('decision', [])) != 1:
                        return self.reply(403, 'Invalid form token')
                    choice = values['decision'][0]
                future = asyncio.run_coroutine_threadsafe(
                    ui.engine.human_approval(task_id, pending_id, choice), ui.loop)
                try:
                    pending = future.result(timeout=15)
                except Exception:
                    return self.reply(409, 'Approval expired, unavailable or already handled. Refresh task status.')
                if submit:
                    return self.reply(200, '<h1>Decision sent</h1><p>You may close this page.</p>')
                labels = {'accept': 'Allow this request', 'decline': 'Decline', 'cancel': 'Cancel'}
                buttons = ''.join('<button name="decision" value="' + key + '">' + labels[key] + '</button> '
                                  for key in decisions(pending['payload']))
                details = html.escape(json.dumps({'task_id': task_id, 'turn_id': pending['turn_id'],
                                                 **pending['payload']}, ensure_ascii=False, indent=2))
                body = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Codex approval</title>'
                body += '<h1>Codex task approval</h1><p>Review the complete request before choosing.</p><pre>' + details + '</pre>'
                if buttons:
                    body += '<form method="post"><input type="hidden" name="csrf" value="' + csrf + '">' + buttons + '</form>'
                else:
                    body += '<p>This request type is unsupported. It has not been approved. Cancel the task through its caller.</p>'
                self.reply(200, body + '</html>')

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request(submit=True)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.address = '127.0.0.1:' + str(self.server.server_port)
        self.origin = 'http://' + self.address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def register(self, task_id, pending_id):
        path = '/approval/' + secrets.token_urlsafe(32)
        self.entries[path] = (task_id, pending_id, secrets.token_urlsafe(32))
        return self.origin + path

    async def close(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.entries.clear()
