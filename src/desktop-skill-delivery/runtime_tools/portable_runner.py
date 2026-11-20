"""VF vendor-runtime entry point, invoked with the adjacent Python -I -B."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import platform
import re
import runpy
import sys
import tempfile


def main():
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise SystemExit('ISOLATED_RUNTIME_REQUIRED')
    root=Path(__file__).resolve().parent
    vendor=root/'vendor'
    if not vendor.is_dir():raise SystemExit('VENDOR_MISSING')
    sys.path.insert(0,str(vendor))
    cache=os.environ.get('VF_RUNTIME_CACHE')
    cache_path=Path(cache).absolute() if cache else Path(tempfile.mkdtemp(prefix='vf-skill-runtime-'))
    if cache_path==root or root in cache_path.parents:raise SystemExit('CACHE_INSIDE_IMMUTABLE_RUNTIME')
    if any(p.is_symlink() for p in (cache_path,*cache_path.parents)):raise SystemExit('CACHE_LINK_REJECTED')
    cache_path.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.environ['NUMBA_CACHE_DIR']=str(cache_path/'numba')
    os.environ['MPLCONFIGDIR']=str(cache_path/'matplotlib')
    os.environ['PYTHONPYCACHEPREFIX']=str(cache_path/'pycache')
    os.environ['TMPDIR']=str(cache_path);os.environ['TEMP']=str(cache_path);os.environ['TMP']=str(cache_path)
    tempfile.tempdir=str(cache_path)
    args=sys.argv[1:]
    if not args or args[0] in ('--help','-h'):
        print('vf-python info | script FILE [ARGS...] | module NAME [ARGS...] | command NAME [ARGS...]')
        return 0
    registry=json.loads((root/'console-commands.json').read_text(encoding='utf-8'))
    if args==['info']:
        print(json.dumps({'python':platform.python_version(),'architecture':platform.machine(),
                          'vendor':'vendor','isolated':True,'commands':sorted(registry['commands'])}))
        return 0
    if len(args)<2:raise SystemExit('RUNTIME_TARGET_REQUIRED')
    mode,target,*rest=args
    if mode=='script':
        path=Path(target).absolute()
        if not path.is_file():raise SystemExit('SCRIPT_MISSING')
        sys.path.insert(1,str(path.parent));sys.argv=[str(path),*rest]
        runpy.run_path(str(path),run_name='__main__')
    elif mode=='module':
        if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',target):raise SystemExit('INVALID_MODULE')
        sys.argv=[target,*rest];runpy.run_module(target,run_name='__main__',alter_sys=True)
    elif mode=='command':
        record=registry['commands'].get(target)
        if record is None:raise SystemExit('UNKNOWN_RUNTIME_COMMAND')
        sys.argv=[target,*rest]
        if record['type']=='script':
            path=root/record['path']
            if not path.resolve().is_relative_to(vendor.resolve()):raise SystemExit('COMMAND_ESCAPED_VENDOR')
            runpy.run_path(str(path),run_name='__main__')
        else:
            module,attribute=record['target'].split(':')
            function=importlib.import_module(module)
            for component in attribute.split('.'):function=getattr(function,component)
            result=function()
            if result is not None:raise SystemExit(result)
    else:raise SystemExit('UNKNOWN_RUNTIME_MODE')
    return 0


if __name__=='__main__':raise SystemExit(main())
