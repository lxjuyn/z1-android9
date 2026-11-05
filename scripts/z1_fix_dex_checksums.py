#!/usr/bin/env python3
"""Recompute the adler32 + SHA-1 in the header of every .dex inside a jar.

Why this exists
---------------
On 2026-11-05 every boot jar in the shipping Z1 image turned out to carry dex
files whose header checksum (bytes 8..11) and signature (bytes 12..31) do not
match their own contents:

    framework.jar   classes.dex   stored 0x844a8d61  actual 0xc51a8392
                    classes2.dex  stored 0x5dbff9f7  actual 0x49e5eec5
                    classes3.dex  stored 0x9b1a292c  actual 0x91432156
    core-oj / core-libart / conscrypt / okhttp / bouncycastle / apache-xml:
                    every one of them bad as well

That is the signature of a jar whose dex was edited after the fact without
updating the header.  ART validates both fields, and the prebuilt boot image
under /framework/arm.noimage records yet a third set of values, so nothing in
the chain agrees: the boot image is rejected, zygote falls back to imageless
mode, and dex2oat dies in art::dex_ir while laying out class data.

The fix here is metadata only: recompute both fields over the same ranges the
format specifies (adler32 over bytes 32..end, SHA-1 over the same range) and
write them back.  No bytecode is touched.

Usage:
  tools/z1_fix_dex_checksums.py <in.jar> <out.jar> [--report]
Exit codes: 0 = written (or already correct), 1 = error.
"""
import hashlib
import shutil
import struct
import sys
import zipfile
import zlib


def fix_dex(data):
    """Return (new_bytes, changed, stored, actual)."""
    if data[:4] != b'dex\n':
        return data, False, None, None
    stored_sum, = struct.unpack('<I', data[8:12])
    stored_sig = data[12:32]
    calc_sum = zlib.adler32(data[32:]) & 0xffffffff
    calc_sig = hashlib.sha1(data[32:]).digest()
    if stored_sum == calc_sum and stored_sig == calc_sig:
        return data, False, stored_sum, calc_sum
    out = bytearray(data)
    out[8:12] = struct.pack('<I', calc_sum)
    out[12:32] = calc_sig
    return bytes(out), True, stored_sum, calc_sum


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    report = '--report' in sys.argv[3:]

    zin = zipfile.ZipFile(src, 'r')
    changed_files = []
    with zipfile.ZipFile(dst, 'w') as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith('.dex'):
                new, changed, stored, actual = fix_dex(data)
                if changed:
                    changed_files.append((info.filename, stored, actual))
                data = new
            # preserve the entry's own compression method and timestamp
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zi.internal_attr = info.internal_attr
            zi.create_system = info.create_system
            zout.writestr(zi, data)
    if report or changed_files:
        for name, stored, actual in changed_files:
            print(f'  fixed {name}: checksum {stored:#010x} -> {actual:#010x}')
    print(f'{src} -> {dst}: {len(changed_files)} dex fixed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
