#!/usr/bin/env python3
"""Derive a separate Speech candidate using explicitly pinned local media tools.

No upload, inference, denoising or approval. Tool pins establish byte identity,
not publisher trust. Capability provenance must come from the actual MCP call.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
from types import ModuleType
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

MAX_SOURCE = 128 * 1024 * 1024
MAX_CAPABILITY = 256 * 1024
FORMATS = 'wav,mov,mp3,aac,flac,ogg'
RATES = (16000, 22050, 24000, 44100, 48000)


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def no_links(path):
    path = Path(path).absolute()
    require(all(not p.is_symlink() for p in (path, *path.parents)), 'REFERENCE_LINK_REJECTED')
    return path


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink,
            info.st_uid, info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, limit):
    path = no_links(path)
    before = path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and 0 < before.st_size <= limit, 'REFERENCE_INPUT_TYPE_OR_SIZE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(fingerprint(os.fstat(stream.fileno())) == fingerprint(before), 'REFERENCE_INPUT_CHANGED')
        raw = stream.read(limit + 1)
        require(fingerprint(os.fstat(stream.fileno())) == fingerprint(before), 'REFERENCE_INPUT_CHANGED')
    require(len(raw) == before.st_size and fingerprint(path.stat()) == fingerprint(before),
            'REFERENCE_INPUT_CHANGED')
    return raw, fingerprint(before)


def decode(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'REFERENCE_JSON_INVALID')
            result[key] = value
        return result

    def floating(value):
        number = float(value)
        require(math.isfinite(number), 'REFERENCE_JSON_INVALID')
        return number

    def constant(_):
        raise ValueError('REFERENCE_JSON_INVALID')

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_float=floating, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('REFERENCE_JSON_INVALID') from None


def seconds(value):
    require(type(value) is str and re.fullmatch(r'(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,6})?', value),
            'REFERENCE_TIME_INVALID')
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError('REFERENCE_TIME_INVALID') from None


def helper():
    path = no_links(Path(__file__).absolute().with_name('prepare_voice_profile.py'))
    raw, mark = read_file(path, MAX_CAPABILITY)
    # This sibling is covered by the skill file manifest, never a user import path.
    # Execute the exact verified source bytes; -B alone still reads existing pyc.
    module = ModuleType('vf_reference_profile')
    module.__file__ = str(path)
    exec(compile(raw, 'prepare_voice_profile.py', 'exec'), module.__dict__)
    require(read_file(path, MAX_CAPABILITY) == (raw, mark), 'REFERENCE_HELPER_CHANGED')
    return module, sha(raw)


def run_tool(program, args, work):
    try:
        result = subprocess.run([str(program), *args], cwd=work,
            env={'PATH': '', 'LC_ALL': 'C', 'LANG': 'C', 'TMPDIR': str(work)},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except subprocess.TimeoutExpired:
        raise ValueError('REFERENCE_TOOL_TIMEOUT') from None
    require(result.returncode == 0, 'REFERENCE_TOOL_FAILED')
    require(len(result.stdout) <= MAX_CAPABILITY, 'REFERENCE_TOOL_OUTPUT_LIMIT')
    return result.stdout


def derive(reference, capabilities, out, *, ffmpeg, ffprobe, ffmpeg_sha256, ffprobe_sha256,
           start, duration, sample_rate, bit_depth, stream_index, channel_index=None, mixdown=False):
    require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
    require(type(sample_rate) is int and sample_rate in RATES and type(bit_depth) is int
            and bit_depth in (16, 24) and type(stream_index) is int and 0 <= stream_index < 256,
            'REFERENCE_TARGET_INVALID')
    require(type(mixdown) is bool and ((mixdown and channel_index is None)
            or (not mixdown and type(channel_index) is int and 0 <= channel_index < 32)),
            'REFERENCE_CHANNEL_SELECTION_REQUIRED')
    begin, length = seconds(start), seconds(duration)
    require(0 <= begin <= 86400 and 1 <= length <= 15, 'REFERENCE_SELECTION_INVALID')
    profile, helper_hash = helper()
    original, original_mark = read_file(reference, MAX_SOURCE)
    caps, caps_mark = read_file(capabilities, MAX_CAPABILITY)
    try:
        selected = profile.selected_capability(decode(caps))
        model_hash = selected.get('configured_model_manifest_sha256')
        bounds = selected.get('reference_seconds')
        require(type(model_hash) is str and re.fullmatch('[0-9a-f]{64}', model_hash)
                and model_hash != '0' * 64 and type(bounds) is dict, 'REFERENCE_CAPABILITY_INVALID')
        minimum, maximum, max_bytes = bounds.get('min'), bounds.get('max'), selected.get('reference_max_bytes')
        require(all(type(n) in (int, float) and math.isfinite(n) and n > 0
                    for n in (minimum, maximum, max_bytes)) and minimum <= maximum,
                'REFERENCE_CAPABILITY_INVALID')
        require(minimum <= float(length) <= maximum
                and sample_rate in selected.get('sample_rates_hz', RATES), 'REFERENCE_CAPABILITY_LIMIT')
    except (TypeError, AttributeError):
        raise ValueError('REFERENCE_CAPABILITY_INVALID') from None
    except ValueError as exc:
        if str(exc).startswith('REFERENCE_'):
            raise
        raise ValueError('REFERENCE_CAPABILITY_INVALID') from None
    expected_frames = int((length * sample_rate).to_integral_value())
    require(44 + expected_frames * (bit_depth // 8) <= min(max_bytes, 2 << 20),
            'REFERENCE_CAPABILITY_LIMIT')
    tools = {}
    for name, path, pin in (('ffmpeg', ffmpeg, ffmpeg_sha256), ('ffprobe', ffprobe, ffprobe_sha256)):
        require(type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin), 'REFERENCE_TOOL_PIN_REQUIRED')
        raw, mark = read_file(path, MAX_SOURCE)
        require(sha(raw) == pin and os.access(path, os.X_OK), 'REFERENCE_TOOL_PIN_MISMATCH')
        tools[name] = (no_links(path), pin, mark)
    out = no_links(out)
    require(not out.exists() and out.parent.is_dir(), 'REFERENCE_NEW_OUTPUT_REQUIRED')
    out.mkdir(mode=0o700)
    require(stat.S_IMODE(out.stat().st_mode) == 0o700 and out.stat().st_uid == os.getuid(),
            'REFERENCE_PRIVATE_OUTPUT_REQUIRED')
    staged = out / 'source.media'
    with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(original)
    source_args = ['-protocol_whitelist', 'file', '-format_whitelist', FORMATS]
    probed = decode(run_tool(tools['ffprobe'][0], ['-v', 'error', *source_args, '-show_entries',
        'stream=index,codec_type,codec_name,sample_rate,channels,duration:format=duration',
        '-of', 'json', 'source.media'], out))
    require(type(probed) is dict and type(probed.get('streams')) is list, 'REFERENCE_PROBE_INVALID')
    streams = [s for s in probed['streams'] if type(s) is dict and s.get('index') == stream_index
               and s.get('codec_type') == 'audio']
    require(len(streams) == 1, 'REFERENCE_AUDIO_STREAM_REQUIRED')
    source = streams[0]
    channels = source.get('channels')
    require(type(channels) is int and 1 <= channels <= 32, 'REFERENCE_SOURCE_CHANNELS_INVALID')
    require(mixdown or channel_index < channels, 'REFERENCE_CHANNEL_OUT_OF_RANGE')
    channel_filter = ('+'.join(f'{1 / channels:.17g}*c{i}' for i in range(channels))
                      if mixdown else f'c{channel_index}')
    filters = f'atrim=start={start}:duration={duration},asetpts=PTS-STARTPTS,pan=mono|c0={channel_filter},aresample={sample_rate}'
    run_tool(tools['ffmpeg'][0], ['-nostdin', '-hide_banner', '-v', 'error', '-n', '-threads', '1',
        *source_args, '-i', 'source.media', '-map', f'0:{stream_index}', '-vn', '-sn', '-dn',
        '-map_metadata', '-1', '-filter_threads', '1', '-af', filters, '-ac', '1',
        '-c:a', f'pcm_s{bit_depth}le', '-fflags', '+bitexact', '-flags:a', '+bitexact',
        '-fs', str(min(int(max_bytes), 2 << 20) + 4096), '-f', 'wav', 'reference.wav'], out)
    wav = out / 'reference.wav'
    data, wav_mark = read_file(wav, 2 << 20)
    try:
        info = profile.inspect_reference(data)
    except ValueError:
        raise ValueError('REFERENCE_OUTPUT_PCM_INVALID') from None
    require(abs(info['frames'] - expected_frames) <= 1, 'REFERENCE_SOURCE_TOO_SHORT')
    require(minimum <= info['duration_seconds'] <= maximum and info['size'] <= max_bytes
            and info['sample_rate_hz'] == sample_rate and info['bit_depth'] == bit_depth,
            'REFERENCE_OUTPUT_CAPABILITY_LIMIT')
    require(read_file(reference, MAX_SOURCE) == (original, original_mark)
            and read_file(capabilities, MAX_CAPABILITY) == (caps, caps_mark)
            and read_file(staged, MAX_SOURCE)[0] == original, 'REFERENCE_INPUT_CHANGED')
    for path, pin, mark in tools.values():
        raw, after = read_file(path, MAX_SOURCE)
        require(sha(raw) == pin and after == mark, 'REFERENCE_TOOL_CHANGED')
    require(read_file(wav, 2 << 20) == (data, wav_mark), 'REFERENCE_OUTPUT_CHANGED')
    wav.chmod(0o600)
    staged.unlink()
    receipt = {'schema': 'vf.speech-reference-derivation/v1', 'state': 'candidate',
        'source': {'sha256': sha(original), 'size': len(original), 'audio_stream': source},
        'capabilities_sha256': sha(caps), 'model_manifest_sha256': model_hash,
        'ready_at_discovery': selected.get('ready') is True,
        'tools_sha256': {n: r[1] for n, r in tools.items()}, 'profile_helper_sha256': helper_hash,
        'selection': {'start_seconds': start, 'duration_seconds': duration, 'stream_index': stream_index,
            'channel_index': channel_index, 'equal_weight_mixdown': mixdown},
        'reference': info, 'processing': ['explicit_selection', 'channel_selection_or_equal_weight_mixdown',
            'resample', f'pcm{bit_depth}'], 'listening_review': 'pending',
        'remote_membership_verified': False, 'capability_origin_verified': False,
        'desktop_acceptance': False, 'mcp_acceptance': False,
        'warnings': ['full_scale_samples'] if info['clipped_samples'] else []}
    with os.fdopen(os.open(out / 'derivation.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   'w', encoding='utf-8') as stream:
        stream.write(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + '\n')
    return receipt


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('REFERENCE_ARGUMENTS_INVALID')


def main():
    try:
        require(sys.flags.isolated and sys.dont_write_bytecode, 'REFERENCE_ISOLATED_RUNTIME_REQUIRED')
        parser = Parser(description=__doc__)
        for name in ('reference', 'capabilities', 'out', 'ffmpeg', 'ffprobe'):
            parser.add_argument('--' + name, type=Path, required=True)
        for name in ('ffmpeg-sha256', 'ffprobe-sha256', 'start', 'duration'):
            parser.add_argument('--' + name, required=True)
        parser.add_argument('--sample-rate', type=int, required=True)
        parser.add_argument('--bit-depth', type=int, choices=(16, 24), required=True)
        parser.add_argument('--stream-index', type=int, required=True)
        choice = parser.add_mutually_exclusive_group(required=True)
        choice.add_argument('--channel-index', type=int)
        choice.add_argument('--mixdown', action='store_true')
        result = derive(**vars(parser.parse_args()))
        print(json.dumps({'prepared': True, 'reference_sha256': result['reference']['sha256'],
                          'listening_review': 'pending', 'remote_membership_verified': False}))
        return 0
    except Exception as exc:
        code = str(exc)
        if not re.fullmatch('[A-Z_]+', code):
            code = 'REFERENCE_DERIVATION_FAILED'
        print(json.dumps({'prepared': False, 'error': code}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
