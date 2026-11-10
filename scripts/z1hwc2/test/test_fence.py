#!/usr/bin/env python3
"""Compile exact production fence/client/present bodies with host-only mocks.

Usage: python3 test_fence.py --out-dir <products/...> [--source <backup.cpp>]
No HAL/device calls are made. Generated sources, binaries and logs stay in out-dir.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def function(text, name):
    match = re.search(r'^static [^;{}]*\b' + re.escape(name) +
                      r'\([^;{}]*\)\s*\{.*?^\}', text, re.M | re.S)
    if not match:
        raise ValueError('Production function not found: ' + name)
    return match.group(0)


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=here.parent / 'z1hwc2.cpp')
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = args.source.read_text()
    hdr = (here.parent / 'include/hardware/hwcomposer2.h').read_text()
    definitions = []
    for name in ('hwc2_error_t', 'hwc2_composition_t', 'hwc2_blend_mode_t'):
        blocks = re.findall(r'typedef enum \{[^}]*\}\s*' + name + ';', hdr, re.S)
        if len(blocks) != 1:
            raise ValueError('Expected exactly one local enum: ' + name)
        definitions.append(blocks[0])
    for name in ('Z1_HANDLE_MAGIC', 'Z1_MAX_PLANES'):
        definitions.append(re.search(r'^#define ' + name + r'\s+[^\n]+', src, re.M).group(0))
    for name in ('z1_handle', 'z1_plane', 'z1_fb', 'z1_dumb', 'z1_layer', 'z1_hwc2'):
        definitions.append(re.search(r'^struct ' + name + r'\b[^{}]*\{.*?^\};', src, re.M | re.S).group(0))
    bodies = '\n\n'.join(function(src, name) for name in
                         ('now_ms', 'wait_fence', 'client_target_fb', 'z1_present_display'))
    template = (here / 'test_fence_mock.cpp').read_text()
    generated = template.replace('/* PRODUCTION_TYPES */', '\n\n'.join(definitions))
    generated = generated.replace('/* PRODUCTION_FUNCTIONS */', bodies)
    cpp = out / 'test_fence_generated.cpp'
    binary = out / 'test_fence'
    cpp.write_text(generated)
    cmd = shlex.split(os.environ.get('CXX_HOST', 'g++')) + [
        '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-pthread']
    if args.sanitize:
        cmd += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    cmd += [str(cpp), '-o', str(binary)]
    print('source=' + str(args.source.resolve()))
    print('source_sha256=' + hashlib.sha256(args.source.read_bytes()).hexdigest())
    print('header_sha256=' + hashlib.sha256(hdr.encode()).hexdigest())
    print('compile=' + shlex.join(cmd), flush=True)
    built = subprocess.run(cmd, capture_output=True, text=True)
    (out / 'compile.log').write_text(built.stdout + built.stderr)
    print('compile_exit=' + str(built.returncode), flush=True)
    if built.returncode:
        print(built.stdout + built.stderr)
        return built.returncode
    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    (out / 'test.log').write_text(tested.stdout + tested.stderr)
    print(tested.stdout + tested.stderr, end='')
    print('test_exit=' + str(tested.returncode))
    return tested.returncode


if __name__ == '__main__':
    sys.exit(main())
