"""Reproducible-source build of the separate GPL Stockfish trace executable.

python -m engine_trace.build --toolchain runtime/toolchain/w64devkit/bin
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import zipfile
from .prepare import prepare

COMMIT = 'edb0d9db6731067ec50ce619ff372b463bc4dd5d'
ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'runtime' / 'stockfish-trace'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--toolchain', type=Path)
    parser.add_argument('--stockfish', type=Path, default=ROOT / 'stockfish/stockfish/stockfish-windows-x86-64-universal.exe')
    parser.add_argument('--arch', default='x86-64-avx2')
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    source = BUILD / 'source'
    if not source.exists():
        archive = urllib.request.urlopen(f'https://codeload.github.com/official-stockfish/Stockfish/zip/{COMMIT}', timeout=60).read()
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            # Only extract paths under the pinned source root, never arbitrary archive paths.
            for info in zipped.infolist():
                relative = Path(*Path(info.filename).parts[1:])
                if not relative.parts or info.is_dir():
                    continue
                target = (source / relative).resolve()
                if not target.is_relative_to(source.resolve()):
                    raise ValueError('Unsafe upstream archive path')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zipped.read(info))
        (BUILD / 'upstream.json').write_text(json.dumps({'commit': COMMIT, 'archive_sha256': hashlib.sha256(archive).hexdigest()}))
    src = source / 'src'
    if not (src / 'tutor_trace.h').exists():
        prepare(src)
    shutil.copyfile(Path(__file__).with_name('tutor_trace.h'), src / 'tutor_trace.h')
    # w64devkit deliberately omits GCC LTO support; regular -O3 is sufficient.
    makefile = src / 'Makefile'
    flags = makefile.read_text().replace('-flto -flto-partition=one', '').replace('-flto=jobserver', '')
    if 'search.o engine.o: tutor_trace.h' not in flags:
        flags += '\nsearch.o engine.o: tutor_trace.h\n'
    makefile.write_text(flags)
    network_name = re.search(r'#define EvalFileDefaultName "([^"]+)"', (src / 'evaluate.h').read_text())[1]
    network = src / network_name
    if not network.exists():
        if args.stockfish.is_file():
            result = subprocess.run([str(args.stockfish.resolve())], input=f'export_net {network.as_posix()}\nquit\n',
                                    text=True, capture_output=True, timeout=60,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError('Unable to export the existing Stockfish network')
        else:
            urllib.request.urlretrieve(f'https://tests.stockfishchess.org/api/nn/{network_name}', network)
    if not hashlib.sha256(network.read_bytes()).hexdigest().startswith(network_name[3:15]):
        raise ValueError('Stockfish network hash mismatch')
    env = dict(os.environ)
    if args.toolchain:
        env['PATH'] = str(args.toolchain.resolve()) + os.pathsep + env['PATH']
    make = shutil.which('make', path=env['PATH'])
    if not make:
        raise RuntimeError('GNU make and a C++ compiler are required; see engine_trace/README.md')
    subprocess.run([make, '-j2', 'build', f'ARCH={args.arch}', 'COMP=gcc', 'EXE=stockfish-trace.exe' if os.name == 'nt' else 'EXE=stockfish-trace'],
                   cwd=src, env=env, check=True)
    binary = src / ('stockfish-trace.exe' if os.name == 'nt' else 'stockfish-trace')
    target = BUILD / binary.name
    shutil.copyfile(binary, target)
    manifest = {'stockfish_commit': COMMIT, 'binary_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                'header_sha256': hashlib.sha256((src / 'tutor_trace.h').read_bytes()).hexdigest(),
                'arch': args.arch, 'schema': 1}
    (BUILD / 'build.json').write_text(json.dumps(manifest, indent=2))
    print('Trace engine ready:', target)


if __name__ == '__main__':
    main()
