"""Probe an isolated Python plus verified vendor tree using synthetic inputs.

The caller extracts the release-locked music package and places its unchanged
source tests under tests/. This tool verifies the actual imported helper paths.
It never downloads dependencies or calls media services.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
from importlib import metadata
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--music-package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    vendor, package = args.vendor.resolve(), args.music_package.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    assert sys.flags.isolated and not sys.flags.no_site, 'ISOLATED_PYTHON_REQUIRED'
    sys.path.insert(0, str(vendor))
    tempfile.tempdir = str(output)
    os.environ['NUMBA_CACHE_DIR'] = str(output / 'numba-cache')
    os.environ['MPLCONFIGDIR'] = str(output / 'matplotlib-cache')
    os.environ['OPENBLAS_NUM_THREADS'] = '2'
    modules = {}
    for name in ('PIL', 'jsonschema', 'referencing', 'numpy', 'scipy', 'soundfile', 'librosa', 'numba', 'llvmlite'):
        module = importlib.import_module(name)
        file = Path(module.__file__).resolve()
        assert file.is_relative_to(vendor), 'IMPORT_ESCAPED_VENDOR:' + name
        modules[name] = {'file': file.relative_to(vendor).as_posix(), 'sha256': sha(file)}
    versions = {name: metadata.version(name) for name in ('Pillow','jsonschema','referencing','numpy','scipy','soundfile','librosa','numba','llvmlite')}
    import numpy as np
    import soundfile as sf
    import librosa
    from scipy.signal import resample_poly
    from PIL import Image
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    signal = (.1*np.sin(2*np.pi*440*np.arange(16000)/16000)).astype('float32')
    resampled = resample_poly(signal, 3, 2)
    assert resampled.shape == (24000,) and np.isfinite(resampled).all()
    wav = output/'synthetic.wav'
    sf.write(wav, signal, 16000, subtype='FLOAT')
    decoded, rate = sf.read(wav, dtype='float32')
    assert rate == 16000
    np.testing.assert_array_equal(decoded, signal)
    spectrum = librosa.stft(signal, n_fft=1024, hop_length=256)
    recovered = librosa.istft(spectrum, hop_length=256, length=len(signal))
    np.testing.assert_allclose(recovered, signal, atol=1e-6)
    png=output/'synthetic.png';Image.new('RGB',(32,24),(12,34,56)).save(png)
    with Image.open(png) as image:
        assert image.size==(32,24) and image.getpixel((10,10))==(12,34,56)
    child={'$id':'urn:vf:probe:positive','type':'integer','minimum':1}
    registry=Registry().with_resource(child['$id'], Resource.from_contents(child,default_specification=DRAFT202012))
    validator=Draft202012Validator({'type':'object','properties':{'value':{'$ref':child['$id']}},'required':['value']}, registry=registry)
    assert validator.is_valid({'value':2}) and not validator.is_valid({'value':0})
    suite=unittest.TestSuite(); helper_paths={}; test_hashes={}
    for test_path in sorted((package/'tests').glob('test_*.py')):
        name='runtime_'+test_path.stem
        spec=importlib.util.spec_from_file_location(name,test_path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        test_hashes[test_path.name]=sha(test_path)
        for variable in ('AUDIT','REPLACE','VALIDATE','PREPARE','LIFT'):
            helper=getattr(module,variable,None)
            if helper is not None:
                path=Path(helper.__file__).resolve()
                assert path.is_relative_to(package/'scripts'), 'HELPER_NOT_FROM_PACKAGE'
                helper_paths[path.name]=sha(path)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    assert suite.countTestCases() > 0, 'NO_MUSIC_TESTS'
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    log=stream.getvalue().replace(str(Path.cwd()),'%WORKSPACE%').replace(str(Path.home()),'%USERPROFILE%')
    (output/'music-tests.log').write_text(log,encoding='utf-8')
    receipt={'python':platform.python_version(),'architecture':platform.machine(),'isolated':bool(sys.flags.isolated),
             'stdlib_prefix_inside_runtime': Path(sys.executable).resolve().is_relative_to(Path(sys.prefix).resolve()),
             'versions':versions,'imported_vendor_files':modules,'checks':{'native_resampling':True,'wav_roundtrip':True,'stft_roundtrip':True,'png_roundtrip':True,'offline_schema_refs':True},
             'music_tests':result.testsRun,'music_tests_passed':result.wasSuccessful(),'helper_hashes':helper_paths,'test_hashes':test_hashes,
             'synthetic_inputs_only':True,'windows_execution':sys.platform=='win32','desktop_acceptance':False,'shipped_runtime':False}
    (output/'runtime-probe.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('python','architecture','checks','music_tests','music_tests_passed','desktop_acceptance')}))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
