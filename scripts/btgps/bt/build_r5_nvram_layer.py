#!/usr/bin/env python3
"""Build the isolated NVRAM fix on the exact R7 R5 system image."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))
from release import z1_build_driver_candidate as image
from z1_prepare_recovery_boot import sha

# Pin e2fsprogs write timestamps so identical inputs yield identical bytes.
FS_WRITE_EPOCH = '1795273200'
BASE_SHA = '774a84d6ec72cac67221248e6c857a7da077073a3c2bab11720ae06c9aea8571'
RADIO = '/bin/z1_linux7_radio_init.sh'
HAL_RC = '/vendor/etc/init/android.hardware.bluetooth@1.0-service.rc'
ORIGINAL = {
    RADIO: '9e0b4268725f04bd494ad1ab5a9615ed46d69db113c232f2e3ce71e637cd6924',
    HAL_RC: 'a1a79c808317d403a4364c949307628895434e0b6201a9cc4d5a75bd61f89699',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('r5_system', type=Path)
    p.add_argument('output_directory', type=Path)
    args = p.parse_args()
    base = args.r5_system.resolve(strict=True)
    out = args.output_directory.resolve()
    image.require(image.sha(base) == BASE_SHA, 'Not the R7 R5 system image')
    out.mkdir()
    entries = []
    for path, digest in ORIGINAL.items():
        original = subprocess.run(['debugfs', '-R', f'cat {path}', str(base)],
                                  check=True, capture_output=True).stdout
        image.require(sha(original) == digest, f'Unexpected R5 member: {path}')
        if path == RADIO:
            data = (SCRIPTS / 'z1_linux7_radio_init.sh').read_bytes()
            image.require(sha(data) ==
                          'b3979f564462d2801202e5684691ee107efa7fb7abefc45bfc80e1bffa6d3ad2',
                          'Radio script differs from the audited NVRAM fix')
            subprocess.run(['sh', '-n'], input=data, check=True)
            mode, label = '0755', 'system_file'
        else:
            data, count = re.subn(rb'(?m)^    group bluetooth$', b'    group bluetooth nvram', original)
            image.require(count == 1, 'Unexpected R5 BT HAL group declaration')
            mode, label = '0644', 'vendor_configs_file'
        file = out / Path(path).name
        file.write_bytes(data)
        entries.append(dict(path=path, source=str(file), original_sha256=digest,
                            sha256=sha(data), uid=0, gid=0, mode=mode,
                            selinux=f'u:object_r:{label}:s0'))
    spec = out / 'nvram-inputs.json'
    spec.write_text(json.dumps(dict(version=1, base=dict(path=str(base), sha256=BASE_SHA),
                                    replacements=entries), indent=2) + '\n')
    image.ENV = dict(image.ENV, E2FSPROGS_FAKE_TIME=FS_WRITE_EPOCH)
    image.execute(spec, out / 'system.img', True,
                  allowed=re.compile('|'.join(re.escape(p) for p in ORIGINAL)))
    print(out / 'system.img')


if __name__ == '__main__':
    main()
