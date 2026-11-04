#!/usr/bin/env python3
"""Assemble a Z1 MTK boot.img from a template image + new kernel/ramdisk parts.

Layout (page = 2048):
  [0]            Android boot header (2048, "ANDROID!")
  [2048]         MTK KERNEL header (512: 88 16 88 58 | size | "KERNEL")
  [2048+512]     kernel content = zImage + appended DT (CONFIG_ARM_APPENDED_DTB)
  <pad to page>  MTK ROOTFS header (512: 88 16 88 58 | size | "ROOTFS")
                 gzip cpio body
  <pad to page>

The MTK size fields EXCLUDE the 512-byte MTK header; the Android header
kernel_size/ramdisk_size equal the MTK kernel content length and the whole
ROOTFS blob (512 + gzip) respectively.

Byte-array slices are always written with explicit ranges of matching length
(a 17-byte value into a 16-byte slice silently shortens a bytearray and
shifts every following field -- that bug produced a 1-byte-offset header).
"""
import argparse, hashlib, struct, sys

PAGE = 2048
MTK_MAGIC = bytes.fromhex('88168858')


def rd(p):
    with open(p, 'rb') as f:
        return f.read()


def pad(b, page=PAGE):
    r = len(b) % page
    return b if r == 0 else b + b'\x00' * (page - r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--template', required=True, help='existing boot.img to copy headers from')
    ap.add_argument('--zimage', required=True, help='zImage (WITHOUT appended dtb)')
    ap.add_argument('--dtb', required=True)
    ap.add_argument('--rootfs', required=True, help='MTK ROOTFS blob (512-byte header + gzip)')
    ap.add_argument('--cmdline', default=None)
    ap.add_argument('--name', default=None)
    ap.add_argument('-o', '--output', required=True)
    a = ap.parse_args()

    tpl = rd(a.template)
    zimg, dtb, rootfs = rd(a.zimage), rd(a.dtb), rd(a.rootfs)

    assert zimg[-4:] != b'\xd0\x0d\xfe\xed', 'zImage already has an appended DT'
    assert dtb[:4] == b'\xd0\x0d\xfe\xed', 'dtb magic missing'
    assert rootfs[:4] == MTK_MAGIC and rootfs[8:16].startswith(b'ROOTFS'), 'rootfs is not an MTK ROOTFS blob'
    assert tpl[:8] == b'ANDROID!', 'template is not an Android boot image'

    kernel = zimg + dtb
    gz = rootfs[512:]

    # ---- Android header (2048) ----
    hdr = bytearray(tpl[:PAGE])
    # LK reads kernel_size bytes starting at the MTK header (not at the
    # payload), so the Android field is the payload PLUS the 512-byte MTK
    # header.  Verified against both the factory image (3337288 vs 3336776)
    # and the known-good z1_hafm_boot image (4236534 vs 4236022): diff is
    # always exactly 512.  Writing the payload length here truncates the
    # appended DT by 512 bytes and the DT fails to parse.
    struct.pack_into('<I', hdr, 8, len(kernel) + 512)    # kernel_size
    struct.pack_into('<I', hdr, 16, len(rootfs))         # ramdisk_size
    if a.cmdline is not None:
        cb = a.cmdline.encode()
        assert len(cb) < 512, 'cmdline too long'
        hdr[64:64 + 512] = cb + b'\x00' * (512 - len(cb))
    if a.name is not None:
        nb = a.name.encode()
        assert len(nb) <= 16, 'name too long (max 16)'
        hdr[48:64] = nb + b'\x00' * (16 - len(nb))
    hdr[576:584] = hashlib.sha1(kernel + rootfs).digest()[:8]

    # ---- MTK KERNEL header (512) ----
    kh = bytearray(tpl[PAGE:PAGE + 512])
    assert kh[:4] == MTK_MAGIC and kh[8:14] == b'KERNEL', 'template MTK kernel header unexpected'
    struct.pack_into('<I', kh, 4, len(kernel))

    # ---- MTK ROOTFS header (512) ----
    rh = bytearray(rootfs[:512])
    struct.pack_into('<I', rh, 4, len(gz))

    out = bytearray()
    out += hdr
    out += kh
    out += kernel
    out += b'\x00' * ((PAGE - (len(out) % PAGE)) % PAGE)
    out += rh
    out += gz
    out += b'\x00' * ((PAGE - (len(out) % PAGE)) % PAGE)

    with open(a.output, 'wb') as f:
        f.write(out)

    print(f'kernel  = zImage {len(zimg)} + dtb {len(dtb)} = {len(kernel)}')
    print(f'rootfs  = header 512 + gzip {len(gz)} = {len(rootfs)}')
    print(f'image   = {len(out)} bytes  (limit 6291456)')
    print(f'initrd-end should be 0x{0x84100000 + len(rootfs):x}')
    if len(out) > 6291456:
        sys.exit('ERROR: image exceeds the 6 MiB boot partition')


if __name__ == '__main__':
    main()
