"""Exercise an explicit media component with synthetic local inputs only.

No services, credentials, creative generation or system PATH dependency. This
checks media tooling; it cannot prove a desktop agent or MCP business flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import wave

from build_media_runtime import no_links, require, sha


def probe(component,work):
    component=component.absolute();work=work.absolute()
    no_links(component);no_links(work)
    require(not work.exists() and work.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    for name in ('ffmpeg','ffprobe'):
        no_links(component/'bin'/name)
        require((component/'bin'/name).is_file(),'MEDIA_BINARY_MISSING')
    initial={name:sha(component/'bin'/name) for name in ('ffmpeg','ffprobe')}
    build_receipt=json.loads((component/'build-receipt.json').read_bytes())
    require(build_receipt['platform']=='macos-arm64' and all(
        initial[n]==build_receipt['binaries'][n]['sha256'] for n in initial),'MEDIA_BUILD_MISMATCH')
    work.mkdir(mode=0o700);(work/'logs').mkdir();(work/'temp').mkdir()
    original=work/'initial parent';original.mkdir()
    shutil.copytree(component,original/'media')
    relocated=work/'relocated parent with spaces';original.rename(relocated)
    require(not original.exists(),'MEDIA_RELOCATION_FAILED')
    media=relocated/'media';ffmpeg=media/'bin/ffmpeg';ffprobe=media/'bin/ffprobe'
    env={'PATH':'','LC_ALL':'C','LANG':'C','TMPDIR':str(work/'temp')}
    commands=[]
    def run(name,args):
        cp=subprocess.run([str(x) for x in args],env=env,cwd=work,
                          capture_output=True,timeout=120)
        sanitize=lambda raw:raw.replace(os.fsencode(work),b'%PROBE%').replace(os.fsencode(Path.home()),b'%USERPROFILE%')
        (work/'logs'/(name+'.stdout')).write_bytes(sanitize(cp.stdout))
        (work/'logs'/(name+'.stderr')).write_bytes(sanitize(cp.stderr))
        commands.append({'step':name,'argv':[str(x).replace(str(work),'%PROBE%') for x in args],
                         'exit_code':cp.returncode})
        (work/'probe-commands.json').write_text(json.dumps(commands,indent=2)+'\n',encoding='utf-8')
        require(cp.returncode==0,'MEDIA_PROBE_STEP_FAILED')
        return cp.stdout
    for program in (ffmpeg,ffprobe):run(program.name+'-version',[program,'-version'])
    for flag in ('encoders','decoders','demuxers','muxers','filters','protocols','buildconf'):
        run(flag,[ffmpeg,'-hide_banner','-'+flag])
    # Ten one-tenth-second RGB frames, with a known smooth spatial gradient.
    width,height,frames=64,48,10
    raw=b''.join(bytes((20+x*3,30+y*3,80+frame*9))
                 for frame in range(frames) for y in range(height) for x in range(width))
    (work/'synthetic.rgb').write_bytes(raw)
    pcm=b''.join(struct.pack('<h',round(10000*math.sin(2*math.pi*440*i/16000))) for i in range(16000))
    with wave.open(str(work/'synthetic.wav'),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(pcm)
    run('h264-aac-mp4',[ffmpeg,'-nostdin','-hide_banner','-v','error','-n',
        '-f','rawvideo','-pixel_format','rgb24','-video_size','64x48','-framerate','10',
        '-i','synthetic.rgb','-i','synthetic.wav','-c:v','libx264','-pix_fmt','yuv420p',
        '-c:a','aac','-shortest','synthetic.mp4'])
    data=json.loads(run('probe-mp4',[ffprobe,'-v','error','-show_streams','-show_format','-of','json','synthetic.mp4']))
    videos=[s for s in data['streams'] if s['codec_type']=='video']
    audios=[s for s in data['streams'] if s['codec_type']=='audio']
    require(len(videos)==len(audios)==1 and videos[0]['codec_name']=='h264'
            and videos[0]['width']==width and videos[0]['height']==height
            and int(videos[0]['nb_frames'])==frames and audios[0]['codec_name']=='aac'
            and .9<=float(data['format']['duration'])<=1.1,'MEDIA_MP4_ASSERTION')
    run('extract-png',[ffmpeg,'-nostdin','-v','error','-n','-i','synthetic.mp4','-frames:v','1','frame.png'])
    png=(work/'frame.png').read_bytes()
    require(png[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',png[16:24])==(width,height),'MEDIA_PNG_ASSERTION')
    run('decode-rgb',[ffmpeg,'-nostdin','-v','error','-n','-i','synthetic.mp4',
                      '-map','0:v:0','-pix_fmt','rgb24','-f','rawvideo','decoded.rgb'])
    decoded=(work/'decoded.rgb').read_bytes()
    require(len(decoded)==len(raw),'MEDIA_RGB_SIZE_ASSERTION')
    mae=sum(abs(a-b) for a,b in zip(decoded,raw))/len(raw)
    require(mae<6,'MEDIA_RGB_CONTENT_ASSERTION')
    run('wav-to-flac',[ffmpeg,'-nostdin','-v','error','-n','-i','synthetic.wav','-c:a','flac','synthetic.flac'])
    run('flac-to-wav',[ffmpeg,'-nostdin','-v','error','-n','-i','synthetic.flac','-c:a','pcm_s16le','roundtrip.wav'])
    with wave.open(str(work/'roundtrip.wav'),'rb') as stream:
        require(stream.getnchannels()==1 and stream.getframerate()==16000 and stream.getsampwidth()==2
                and stream.readframes(stream.getnframes())==pcm,'MEDIA_LOSSLESS_AUDIO_ASSERTION')
    run('audio-resample',[ffmpeg,'-nostdin','-v','error','-n','-i','synthetic.wav',
                          '-ar','48000','-ac','2','-c:a','pcm_s16le','resampled.wav'])
    # FFmpeg may use WAVE_FORMAT_EXTENSIBLE for stereo; ffprobe checks semantics.
    resampled=json.loads(run('probe-resampled',[ffprobe,'-v','error','-show_streams','-of','json','resampled.wav']))['streams'][0]
    require(resampled['sample_rate']=='48000' and resampled['channels']==2
            and resampled['codec_name']=='pcm_s16le' and abs(float(resampled['duration'])-1)<.001,
            'MEDIA_RESAMPLE_ASSERTION')
    require(initial=={n:sha(component/'bin'/n) for n in initial}
            =={n:sha(media/'bin'/n) for n in initial},'MEDIA_BINARY_CHANGED')
    receipt={'schema':'vf.media-runtime-probe/v1','platform':'macos-arm64','status':'synthetic_media_passed',
        'binaries':initial,'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'original_parent_absent':True,'relocated_parent_contains_spaces':True,'path_empty':True,
        'checks':['h264-aac-mp4','probe-video-audio-duration','extract-png','decode-rgb-content',
                  'lossless-flac-roundtrip','audio-resample'],'rgb_mean_absolute_error':mae,
        'outputs':{n:{'sha256':sha(work/n),'bytes':(work/n).stat().st_size}
                   for n in ('synthetic.mp4','frame.png','decoded.rgb','synthetic.flac','roundtrip.wav','resampled.wav')},
        'commands':commands,'synthetic_inputs_only':True,'desktop_acceptance':False,
        'mcp_acceptance':False,'windows_execution':False,'independent_review':'pending'}
    (work/'probe-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',type=Path,required=True);parser.add_argument('--work',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(probe(args.component,args.work)))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,subprocess.TimeoutExpired) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'MEDIA_PROBE_FAILED'
        print(json.dumps({'error':code}),file=__import__('sys').stderr);raise SystemExit(2)
