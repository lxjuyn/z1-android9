"""Compare R7 GNSS and MD1 assets with the Z1 factory ext4 system image."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from xml.etree import ElementTree


def read_image(image, path):
    result = subprocess.run(['debugfs', '-R', 'cat ' + path, str(image)], capture_output=True,
                            check=True)
    errors = result.stderr.decode().splitlines()[1:]
    if errors:
        if all('File not found by ext2_lookup' in line for line in errors):
            return None
        raise ValueError(errors)
    return result.stdout


def inventory(stock, r7):
    def required(image, path):
        data = read_image(image, path)
        if not data:
            raise ValueError(f'{image}: missing {path}')
        return data

    def digest(data):
        return hashlib.sha256(data).hexdigest()

    md = '/etc/firmware/modem_1_2g_n.img'
    firmware = required(stock, md)
    header = firmware[-188:]
    if header[:12] != b'CHECK_HEADER' or struct.unpack_from('<I', header, 12)[0] != 2:
        raise ValueError('unexpected factory MD1 header')
    patches = {}
    for index in (0, 1):
        path = f'/etc/firmware/mt6572_82_patch_e1_{index}_hdr.bin'
        patches[path] = required(stock, path) == required(r7, path)
    manifest = ElementTree.fromstring(required(r7, '/vendor/etc/vintf/manifest.xml'))
    gnss = [hal.findtext('transport') for hal in manifest.findall('hal')
            if hal.findtext('name') == 'android.hardware.gnss']
    init = required(r7, '/vendor/etc/init/android.hardware.gnss@1.0-service.rc')
    paths = ['/vendor/lib/hw/gps.default.so', '/vendor/lib/hw/gps.z1stock.so',
             '/vendor/bin/mnld', '/xbin/libmnlp_mt6572', '/vendor/bin/mtk_agpsd',
             '/vendor/bin/hw/android.hardware.gnss@1.0-service']
    for module in ['mtk_stp_wmt_soc', 'mtk_stp_gps_soc', 'z1_gpsdrv']:
        paths.append(f'/lib/modules/z1-radio/{module}.ko')
    r7_md = read_image(r7, md)
    return {'stock_md1_sha256': digest(firmware), 'stock_md1_size': len(firmware),
            'stock_md1_required_memory': struct.unpack_from('<I', header, 0xac)[0],
            'r7_standard_path_md1_sha256': digest(r7_md) if r7_md else None,
            'identical_connectivity_patches': patches,
            'r7_gnss_payload_sha256': {path: digest(required(r7, path)) for path in paths},
            'vendor_manifest_gnss_transports': gnss,
            'vendor_gnss_rc_services': [line.decode() for line in init.splitlines()
                                        if line.strip().startswith(b'service ')]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('r7', type=Path)
    args = parser.parse_args()
    print(json.dumps(inventory(args.stock, args.r7), indent=2, sort_keys=True))
