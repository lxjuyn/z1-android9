#!/usr/bin/env python3
"""Verify stock Z1 identity and report decoding directly from factory bytes.

Read-only, no network or hardware access. Requires Python capstone. The address
map is pinned to the checked image hash. Optional --disassemble prints the
complete report function for an independent review (no guessed key geometry).
"""
import argparse
import hashlib
import lzma
from pathlib import Path
import struct

BOOT_SHA = '2dd41702e839ec2547ce0dea2bbd3603a5d019786502d939ebba4a6b578eaab5'
KERNEL_SHA = '3128816c91314be52586f45a592a1f642c8a80baabae3c08d19b451c755b2500'
BASE = 0xc0008000


def main():
    import capstone
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('boot', nargs='?', type=Path,
                    default=Path(__file__).resolve().parents[3] / '1/boot.img')
    ap.add_argument('--disassemble', action='store_true')
    args = ap.parse_args()
    boot = args.boot.read_bytes()
    assert hashlib.sha256(boot).hexdigest() == BOOT_SHA
    assert boot[:8] == b'ANDROID!'
    size = struct.unpack_from('<I', boot, 8)[0]
    page = struct.unpack_from('<I', boot, 36)[0]
    assert page == 2048
    region = boot[page:page+size]
    # Existing audit found two XZ magic candidates; independently test all.
    kernels = []
    for offset in range(len(region)):
        if region[offset:offset+6] != b'\xfd7zXZ\x00':
            continue
        try:
            k = lzma.LZMADecompressor().decompress(region[offset:])
            if b'Linux version 3.4.67' in k:
                kernels.append((offset, k))
        except lzma.LZMAError:
            pass
    assert len(kernels) == 1
    offset, raw = kernels[0]
    assert offset == 0x3d34 and len(raw) == 10191300
    assert hashlib.sha256(raw).hexdigest() == KERNEL_SHA
    print('PASS factory boot sha256', BOOT_SHA)
    print('PASS decompressed kernel sha256', KERNEL_SHA)
    identity = b'HYN_CST836U_DWZN_MT6572_4.4_V1.0_20181106\0'
    assert raw[0x885e08:0x885e08+len(identity)] == identity
    assert raw[0x70bf34:0x70bf34+7] == b'hyn_ts\0'
    print('PASS factory CST836U identity at kernel offset 0x885e08')
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    start, end = 0xc04879f8, 0xc0487db8
    insns = list(md.disasm(raw[start-BASE:end-BASE], start))
    byaddr = {i.address: (i.mnemonic, i.op_str) for i in insns}
    checks = {
        0xc0487af4: ('mov', 'ip, #0'),
        0xc0487b08: ('mov', 'r3, #0xf'),
        0xc0487b18: ('mov', 'r2, #1'),
        0xc0487b1c: ('strh', 'ip, [r7]'),
        0xc0487b28: ('mov', 'r1, r7'),
        0xc0487b30: ('str', 'r3, [sp]'),
        0xc0487b34: ('mov', 'r3, r4'),
        0xc0487b58: ('bl', '#0xc04882a0'),
        0xc0487bb4: ('ldrb', 'r2, [fp, #-0x86]'),
        0xc0487bc0: ('and', 'r2, r2, #0xf'),
        0xc0487bf0: ('ldrb', 'r0, [r2, #5]'),
        0xc0487bf4: ('ldrb', 'ip, [r2, #3]'),
        0xc0487bf8: ('ldrb', 'r8, [r2, #4]'),
        0xc0487c00: ('ldrb', 'lr, [r2, #6]!'),
        0xc0487c04: ('and', 'r7, r7, #0xf00'),
        0xc0487c14: ('lsr', 'r0, r0, #4'),
        0xc0487c20: ('lsr', 'ip, ip, #6'),
        0xc0487c94: ('movw', 'r2, #0x14a'),
        0xc0487cd8: ('mov', 'r2, #0x35'),
        0xc0487cf0: ('mov', 'r2, #0x36'),
        0xc0487d74: ('movw', 'r2, #0x14a'),
        0xc0487d78: ('mov', 'r3, #0'),
    }
    for addr, expected in checks.items():
        assert byaddr[addr] == expected, (hex(addr), byaddr[addr], expected)
    # All direct calls in the complete loop are accounted for. The only I2C
    # helper call is reg=0, write_len=1, read_len=15 at c0487b58. This does not
    # establish the physical button locations or exclude other firmware modes.
    targets = {int(i.op_str[1:], 16) for i in insns if i.mnemonic == 'bl'}
    assert targets == {0xc0231d80, 0xc00679d8, 0xc06b50cc, 0xc005b7b4,
                       0xc005b6bc, 0xc04882a0, 0xc005ad70, 0xc02f1534, 0xc06abd5c}
    print('PASS stock loop reads register 0x00, 15 bytes, count at byte2 & 0x0f')
    print('PASS stock point stride=6; X/Y=12bit; event=XH>>6; id=YH>>4')
    print('PASS stock loop emits BTN_TOUCH and ABS_MT_POSITION_X/Y')
    print('UNVERIFIED physical nav-key geometry; no independent key read in this loop')
    # Attribute presence alone must not be misreported as configured key areas.
    assert raw[0x886334:0x886334+20] == b'virtualkeys.mtk-tpd\0'
    assert struct.unpack_from('<I', raw, 0x9a33b0)[0] == BASE+0x886334
    assert struct.unpack_from('<I', raw, 0x9a33b8)[0] == 0xc0488da0
    print('PASS generic virtualkeys attribute/callback present; initialization unknown')
    if args.disassemble:
        for ins in insns:
            print(f'{ins.address:#010x}  {ins.bytes.hex():8s}  {ins.mnemonic:7s} {ins.op_str}')


if __name__ == '__main__':
    main()
