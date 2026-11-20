"""Test-only private pipe to the pinned official native management fixture.

The parent retains the same backend while separate Python coordinators exit.
This is not a supported desktop transport or a product bridge.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time

import host_deepseek as h
from host_deepseek_operation import Observation


class NativeBackend:
    def __init__(self, modules, root):
        self.process = None
        self.buffer = b''
        self.mutations = 0
        self.observations = 0
        self.observer = None
        self.before_mutate = None
        root = Path(root).resolve()
        root.mkdir(mode=0o700)
        scripts = Path(__file__).resolve().parent
        repo = scripts.parents[3]
        node, sandbox = shutil.which('node'), shutil.which('sandbox-exec')
        h.require(node and sandbox, 'DEEPSEEK_NATIVE_TEST_REQUIREMENTS')
        command = [sandbox, '-p', '(version 1)(allow default)(deny network*)', node,
            '--experimental-permission', '--allow-fs-read=' + str(Path(modules).resolve()),
            '--allow-fs-read=' + str(root), '--allow-fs-read=' + str(scripts),
            '--allow-fs-write=' + str(root), str(scripts / 'drive_deepseek_management.mjs'),
            str(Path(modules).resolve()), str(root / 'profile')]
        self.process = subprocess.Popen(command, cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0,
            env={'PATH': str(Path(node).parent), 'LANG': 'en_US.UTF-8'})
        try:
            ready = self.receive()
            h.require(ready.get('ready') is True and ready.get('isolated_native_fixture') is True,
                      'DEEPSEEK_NATIVE_TEST_START_FAILED')
            self.identity = h.Identity('com.deepseek.dsh', '0.2.2-rc.3', 'macos', 'arm64',
                h.DMG_SHA256, h.SETTINGS_SHA256, ready['generation'], 'isolated-management-profile', 'probe')
            self.identity.validate()
        except BaseException:
            self.close()
            raise

    def receive(self):
        deadline = time.monotonic() + 45
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                remaining = deadline - time.monotonic()
                h.require(remaining > 0 and selector.select(max(0, remaining)), 'DEEPSEEK_NATIVE_TEST_TIMEOUT')
                data = os.read(self.process.stdout.fileno(), 65536)
                h.require(data, 'DEEPSEEK_NATIVE_TEST_EXITED')
                self.buffer += data
                h.require(len(self.buffer) <= 2 * 1048576, 'DEEPSEEK_NATIVE_TEST_LIMIT')
        line, self.buffer = self.buffer.split(b'\n', 1)
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError):
            raise h.Conflict('DEEPSEEK_NATIVE_TEST_INVALID_REPLY') from None
        h.require(type(value) is dict, 'DEEPSEEK_NATIVE_TEST_INVALID_REPLY')
        return value

    def request(self, value):
        raw = h.encode(value) + b'\n'
        self.process.stdin.write(raw)
        self.process.stdin.flush()
        result = self.receive()
        h.require(result.get('ok') is True, 'DEEPSEEK_NATIVE_TEST_REJECTED')
        return result

    def view(self):
        result = self.request({'action': 'observe'})
        h.require(result['generation'] == self.identity.backend_generation, 'DEEPSEEK_NATIVE_TEST_GENERATION')
        return result

    def observe(self):
        self.observations += 1
        result = self.view()
        view = result['view']
        h.require(view['hasDocument'] is True and len(view['namespaces']) == 1,
                  'DEEPSEEK_NATIVE_TEST_NAMESPACE')
        observation = Observation(self.identity, h.encode(view['namespaces'][0]), view['writable'], ())
        return self.observer(observation) if self.observer else observation

    def mutate(self, mutation):
        if self.before_mutate:
            self.before_mutate(mutation)
        result = self.request({'action': 'mutate', 'mutation': mutation})
        if result.get('refused'):
            raise h.Conflict('DEEPSEEK_NATIVE_TEST_REFUSED')
        h.require(result.get('submitted') is True, 'DEEPSEEK_NATIVE_TEST_INVALID_REPLY')
        self.mutations += 1

    def close(self):
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                self.request({'action': 'close'})
                process.wait(timeout=10)
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
