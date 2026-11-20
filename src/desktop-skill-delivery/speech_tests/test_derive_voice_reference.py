"""Actual pinned-media tests with synthetic audio; no services or desktop claims."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import py_compile
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReferenceDerivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = Path(os.environ['VF_SPEECH_TEST_RUNTIME']).absolute()
        cls.tools = {name: cls.runtime / 'bin' / name for name in ('ffmpeg', 'ffprobe')}
        cls.pins = {name: digest(path) for name, path in cls.tools.items()}
        cls.temp_root = ROOT / '.tmp/speech-derivation-tests'
        cls.temp_root.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.temp_root)
        self.work = Path(self.temp.name)
        scripts = self.work / 'standalone-skill/scripts'
        scripts.mkdir(parents=True)
        self.script = scripts / 'derive_voice_reference.py'
        shutil.copyfile(SOURCE / 'speech_tools/derive_voice_reference.py', self.script)
        shutil.copyfile(SOURCE.parents[1] / 'skills/verdantflare-speech/scripts/prepare_voice_profile.py',
                        scripts / 'prepare_voice_profile.py')
        spec = importlib.util.spec_from_file_location('vf_derivation_test', self.script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.reference = self.work / 'original.wav'
        self.audio(self.reference)
        self.capabilities = self.work / 'capabilities.json'
        self.cap = {'service_version': 'synthetic-test-only', 'engines': [{
            'engine': 'indextts2', 'ready': False, 'model_manifest_sha256': 'a' * 64,
            'reference_audio': {'min_seconds': 1, 'max_seconds': 15, 'max_bytes': 2097152,
                                'sample_rates_hz': [16000, 24000, 48000], 'channels': 1}}]}
        self.save_caps()
        self.output = self.work / 'candidate'

    def tearDown(self):
        self.temp.cleanup()

    def save_caps(self):
        self.capabilities.write_text(json.dumps(self.cap), encoding='utf-8')

    def audio(self, path, *, channels=1, rate=16000, duration=4, antiphase=False):
        data = bytearray()
        for i in range(round(rate * duration)):
            left = round(10000 * math.sin(2 * math.pi * 440 * i / rate))
            values = [left]
            if channels == 2:
                values.append(-left if antiphase else round(8000 * math.sin(2 * math.pi * 880 * i / rate)))
            data.extend(struct.pack('<' + 'h' * channels, *values))
        with wave.open(str(path), 'wb') as wav:
            wav.setnchannels(channels); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(data)

    def options(self, **kwargs):
        result = dict(reference=self.reference, capabilities=self.capabilities, out=self.output,
            ffmpeg=self.tools['ffmpeg'], ffprobe=self.tools['ffprobe'],
            ffmpeg_sha256=self.pins['ffmpeg'], ffprobe_sha256=self.pins['ffprobe'],
            start='0.5', duration='2', sample_rate=24000, bit_depth=16, stream_index=0, channel_index=0)
        result.update(kwargs)
        return result

    def run_media(self, args):
        result = subprocess.run([str(self.tools['ffmpeg']), '-nostdin', '-v', 'error', '-n', *args],
            cwd=self.work, env={'PATH': '', 'LC_ALL': 'C'}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        self.assertEqual(result.returncode, 0)

    def assert_candidate(self, result, original_hash):
        self.assertEqual(digest(self.reference), original_hash)
        self.assertEqual(result['reference']['sha256'], digest(self.output / 'reference.wav'))
        self.assertEqual(result['state'], 'candidate')
        self.assertEqual(result['listening_review'], 'pending')
        self.assertFalse(result['remote_membership_verified'])
        self.assertFalse(result['ready_at_discovery'])
        self.assertFalse(result['capability_origin_verified'])
        self.assertFalse(result['desktop_acceptance'])
        self.assertFalse((self.output / 'source.media').exists())
        self.assertEqual(stat_mode(self.output), 0o700)
        self.assertEqual(stat_mode(self.output / 'reference.wav'), 0o600)
        self.assertEqual(stat_mode(self.output / 'derivation.json'), 0o600)
        self.assertNotIn(str(self.work), (self.output / 'derivation.json').read_text())

    def test_actual_m4a_to_pcm24_relocated_package_and_channel_selection(self):
        self.audio(self.reference, channels=2, rate=44100, duration=5)
        self.run_media(['-i', str(self.reference), '-c:a', 'aac', '-b:a', '128k', 'original.m4a'])
        self.reference = self.work / 'original.m4a'
        original_hash = digest(self.reference)
        result = self.module.derive(**self.options(start='1.25', duration='2.5', bit_depth=24, channel_index=1))
        self.assertEqual(result['reference']['frames'], 60000)
        self.assertEqual(result['reference']['bit_depth'], 24)
        self.assertEqual(result['source']['audio_stream']['channels'], 2)
        data = (self.output / 'reference.wav').read_bytes()
        cursor = 12
        while data[cursor:cursor + 4] != b'data':
            size = struct.unpack_from('<I', data, cursor + 4)[0]
            cursor += 8 + size + (size & 1)
        pcm = data[cursor + 8:]
        samples = [int.from_bytes(pcm[i:i + 3], 'little', signed=True) for i in range(0, len(pcm), 3)]
        crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
        self.assertAlmostEqual(crossings / 2.5, 880, delta=2)
        self.assert_candidate(result, original_hash)

    def test_explicit_equal_mixdown_rejects_antiphase_silence(self):
        self.audio(self.reference, channels=2, antiphase=True)
        with self.assertRaisesRegex(ValueError, 'REFERENCE_OUTPUT_PCM_INVALID'):
            self.module.derive(**self.options(sample_rate=16000, channel_index=None, mixdown=True))
        self.assertFalse((self.output / 'derivation.json').exists())

    def test_exact_wav_selected_samples_and_no_silent_shortening(self):
        result = self.module.derive(**self.options(start='1', sample_rate=16000))
        with wave.open(str(self.reference), 'rb') as source:
            source.setpos(16000); expected = source.readframes(32000)
        with wave.open(str(self.output / 'reference.wav'), 'rb') as output:
            self.assertEqual(output.readframes(output.getnframes()), expected)
        self.assertEqual(result['reference']['frames'], 32000)
        with self.assertRaisesRegex(ValueError, 'REFERENCE_SOURCE_TOO_SHORT'):
            self.module.derive(**self.options(out=self.work / 'too-short', start='3', duration='2'))
        self.assertFalse((self.work / 'too-short/derivation.json').exists())

    def test_live_limits_malformed_json_and_unknown_model_reject_before_output(self):
        for mutate in (
            lambda c: c['engines'][0]['reference_audio'].update(max_seconds=1),
            lambda c: c['engines'][0]['reference_audio'].update(sample_rates_hz=[16000]),
            lambda c: c['engines'][0]['reference_audio'].update(max_bytes=100),
            lambda c: c['engines'][0].update(model_manifest_sha256='0' * 64),
            lambda c: c['engines'].append(c['engines'][0].copy()),
        ):
            with self.subTest(mutate=mutate):
                changed = json.loads(json.dumps(self.cap)); mutate(changed)
                self.capabilities.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    self.module.derive(**self.options())
                self.assertFalse(self.output.exists())
        for raw in (b'{"engines":[],"engines":[]}', b'{"x":1e999}', b'{"x":NaN}', b'[]', b'\xff'):
            self.capabilities.write_bytes(raw)
            with self.assertRaises(ValueError):
                self.module.derive(**self.options())
            self.assertFalse(self.output.exists())

    def test_unmanaged_existing_output_links_special_files_and_pin_rejected(self):
        self.output.mkdir(); sentinel = self.output / 'keep'; sentinel.write_bytes(b'foreign content')
        with self.assertRaisesRegex(ValueError, 'REFERENCE_NEW_OUTPUT_REQUIRED'):
            self.module.derive(**self.options())
        self.assertEqual(sentinel.read_bytes(), b'foreign content')
        linked = self.work / 'linked.wav'; linked.symlink_to(self.reference)
        hard = self.work / 'hard.wav'; os.link(self.reference, hard)
        fifo = self.work / 'fifo'; os.mkfifo(fifo)
        for path in (linked, hard, fifo):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                self.module.derive(**self.options(reference=path, out=self.work / 'refused'))
        hard.unlink()
        with self.assertRaisesRegex(ValueError, 'REFERENCE_TOOL_PIN_MISMATCH'):
            self.module.derive(**self.options(out=self.work / 'wrong-pin', ffmpeg_sha256='0' * 64))
        self.assertFalse((self.work / 'wrong-pin').exists())

    def test_original_or_capability_edit_during_processing_cannot_get_success_receipt(self):
        original_run = self.module.run_tool
        for name, path in (('source', self.reference), ('capabilities', self.capabilities)):
            original = path.read_bytes(); directory = self.work / name
            def racing(program, args, work):
                raw = original_run(program, args, work)
                if Path(program).name == 'ffprobe':
                    path.write_bytes(original + b' ')
                return raw
            with self.subTest(name=name), patch.object(self.module, 'run_tool', side_effect=racing):
                with self.assertRaisesRegex(ValueError, 'REFERENCE_INPUT_CHANGED'):
                    self.module.derive(**self.options(out=directory))
            self.assertFalse((directory / 'derivation.json').exists())
            path.write_bytes(original)

    def test_playlists_invalid_streams_and_channel_range_rejected(self):
        self.reference.write_text('#EXTM3U\n' + str(self.work / 'other.wav') + '\n')
        with self.assertRaisesRegex(ValueError, 'REFERENCE_TOOL_FAILED'):
            self.module.derive(**self.options())
        self.audio(self.reference)
        for options, code in (({'stream_index': 12}, 'REFERENCE_AUDIO_STREAM_REQUIRED'),
                              ({'channel_index': 1}, 'REFERENCE_CHANNEL_OUT_OF_RANGE'),
                              ({'channel_index': None}, 'REFERENCE_CHANNEL_SELECTION_REQUIRED'),
                              ({'start': '-1'}, 'REFERENCE_TIME_INVALID'),
                              ({'duration': '16'}, 'REFERENCE_SELECTION_INVALID')):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, code):
                self.module.derive(**self.options(out=self.work / ('bad-' + code.lower()), **options))

    def test_isolated_cli_success_and_redacted_failure_from_exported_scripts(self):
        args = []
        for key, value in self.options(sample_rate=16000).items():
            args.extend(['--' + key.replace('_', '-'), str(value)])
        result = subprocess.run([sys.executable, '-I', '-B', str(self.script), *args],
            cwd=self.work, env={'PATH': '', 'LC_ALL': 'C'}, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(json.loads(result.stdout)['prepared'])
        result = subprocess.run([sys.executable, '-I', '-B', str(self.script), '--private-invalid-argument'],
            cwd=self.work, env={'PATH': ''}, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['error'], 'REFERENCE_ARGUMENTS_INVALID')
        self.assertEqual(result.stderr, b'')
        self.assertNotIn(b'private-invalid-argument', result.stdout)

    def test_unmanaged_bytecode_cannot_replace_verified_sibling_source(self):
        profile = self.script.with_name('prepare_voice_profile.py')
        raw, info = profile.read_bytes(), profile.stat()
        payload = self.work / 'synthetic_cache_marker.py'
        marker = b"unmanaged_cache_marker = 'synthetic-only'\n"
        payload.write_bytes(marker + b' ' * (len(raw) - len(marker)))
        os.utime(payload, ns=(info.st_atime_ns, info.st_mtime_ns))
        cache = Path(importlib.util.cache_from_source(str(profile)))
        cache.parent.mkdir()
        py_compile.compile(str(payload), cfile=str(cache), doraise=True)
        loaded, actual_hash = self.module.helper()
        self.assertFalse(hasattr(loaded, 'unmanaged_cache_marker'))
        self.assertEqual(actual_hash, hashlib.sha256(raw).hexdigest())
        self.assertTrue(callable(loaded.inspect_reference))
        result = self.module.derive(**self.options(sample_rate=16000))
        self.assertEqual(result['reference']['frames'], 32000)
        self.assertEqual(profile.read_bytes(), raw)


def stat_mode(path):
    return path.stat().st_mode & 0o777


if __name__ == '__main__':
    unittest.main()
