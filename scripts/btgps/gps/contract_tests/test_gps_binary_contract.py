#!/usr/bin/env python3
"""Host-only binary/source evidence checks; does not execute ARM code or contact a device.
Run with python3 -B; requires pyelftools and capstone. Passing is NOT hardware success.
"""
import hashlib
import io
import json
import struct
import unittest
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile

ROOT = Path(__file__).resolve().parents[5]
FACTORY = ROOT / 'out/z1_gps_stage/factory'
HIDL = ROOT / 'out/z1_gps_stage/gnss_hidl/android.hardware.gnss@1.0-impl.so'
DRIVER = ROOT / 'fork_linux/drivers/misc/mediatek/connectivity/common/linux/pub/stp_chrdev_gps.c'

class Image:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.elf = ELFFile(io.BytesIO(self.data))
        if self.elf.elfclass != 32 or not self.elf.little_endian:
            raise ValueError('Expected ARM32 little-endian ELF')
    def at(self, address, size):
        for seg in self.elf.iter_segments():
            start = seg['p_vaddr']
            if seg['p_type'] == 'PT_LOAD' and start <= address and address + size <= start + seg['p_filesz']:
                off = seg['p_offset'] + address - start
                return self.data[off:off + size]
        raise ValueError(f'Unbacked virtual address {address:#x}')
    def word(self, address):
        return struct.unpack('<I', self.at(address, 4))[0]
    def text(self, address):
        result = bytearray()
        for i in range(512):
            ch = self.at(address + i, 1)
            if ch == bytes([0]):
                return result.decode('ascii')
            result.extend(ch)
        raise ValueError('Unterminated string')
    def relative(self, literal, add_address):
        return (self.word(literal) + add_address + 4) & 0xffffffff
    def instructions(self, address, size):
        return list(Cs(CS_ARCH_ARM, CS_MODE_THUMB).disasm(self.at(address, size), address))

def sleb(data, cursor):
    value = shift = 0
    for _ in range(10):
        if cursor >= len(data):
            raise ValueError('Truncated SLEB128')
        byte = data[cursor]
        cursor += 1
        value |= (byte & 127) << shift
        shift += 7
        if not byte & 128:
            if byte & 64:
                value -= 1 << shift
            return value, cursor
    raise ValueError('Oversized SLEB128')

def packed_rel(data):
    """Decode Android APS2 REL, rejecting unsupported RELA."""
    if data[:4] != b'APS2':
        raise ValueError('Expected APS2')
    cursor = 4
    def read():
        nonlocal cursor
        value, cursor = sleb(data, cursor)
        return value
    count, offset = read(), read()
    if not 0 <= count <= 100000:
        raise ValueError('Invalid count')
    output = []
    while len(output) < count:
        group, flags = read(), read()
        if group <= 0 or group > count - len(output) or flags & ~3:
            raise ValueError('Invalid group or unsupported RELA flags')
        delta = read() if flags & 2 else None
        info = read() if flags & 1 else None
        for _ in range(group):
            offset += delta if delta is not None else read()
            output.append((offset, info if info is not None else read()))
    if any(data[cursor:]):
        raise ValueError('Nonzero trailing APS2 data')
    return output

class GpsContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mnl = Image(FACTORY / 'xbin/libmnlp_mt6572.pre_xlog')
        cls.mnld = Image(FACTORY / 'xbin/mnld.pre_xlog')
        cls.hal = Image(FACTORY / 'lib/hw/gps.default.so.pre_xlog')
        cls.hidl = Image(HIDL)
    def test_evidence_identity(self):
        for image, expected in [(self.mnl, 'f0b9b7ace3e1bad98ce88d3715e72331046ce9bc89ffdd47f00cd175fd9d9520'), (self.mnld, 'dc2d6fa263a486372dd5658fa3ebfcd83af9c947e825ef96519ece883172eed4'), (self.hal, 'ca0ac8aa2f1ba23c21755f06d8a11dbbd8aad289d21964034769116175a3d757'), (self.hidl, 'd6c0bf6488912095716f4741472fdad20ffccbbe49b977abd4c3c0b892fbd8b9')]:
            self.assertEqual(hashlib.sha256(image.data).hexdigest(), expected)
    def test_xlog_patch_preserved_text(self):
        for rel, original in [('xbin/libmnlp_mt6572', self.mnl), ('xbin/mnld', self.mnld), ('lib/hw/gps.default.so', self.hal)]:
            self.assertEqual(Image(FACTORY / rel).elf.get_section_by_name('.text').data(), original.elf.get_section_by_name('.text').data())
    def test_vendor_seven_is_rtc(self):
        records = json.loads(Path(__file__).with_name('source_evidence.json').read_text())
        vendor = next(x for x in records if x['name'] == 'vendor_gps_ioctl')
        text = ' '.join(' '.join(x['text'].split()) for x in vendor['excerpts'])
        self.assertIn('COMBO_IOC_RTC_FLAG 7', text)
        self.assertIn('COMBO_IOC_CO_CLOCK_FLAG 8', text)
        self.assertIn('retval = rtc_GPS_low_power_detected();', text)
    def test_ioctl_and_log_records(self):
        ins = self.mnl.instructions(0x2dc6, 0x32)
        self.assertEqual([(x.mnemonic, x.op_str) for x in ins[:2]], [('movs', 'r1, #7'), ('movs', 'r2, #0')])
        self.assertEqual(self.mnl.at(0x2dce, 4).hex(), 'fef7c8ef')
        for literal, add, expected in [(0x2fec, 0x2dde, '%s: clear RTC'), (0x2ff4, 0x2df2, '%s: need do nothing for RTC')]:
            record = self.mnl.relative(literal, add)
            self.assertEqual(self.mnl.text(self.mnl.word(record + 4)).strip(), expected)
    def test_actual_branch_slice_all_results_rejoin(self):
        # Minimal interpreter of the actual cmp/branch slice, not a success stub.
        code = {x.address: x for x in self.mnl.instructions(0x2dd2, 0x26)}
        for result in (-1, 0, 1, 2):
            pc, equal, rtc_mask, steps = 0x2dd2, False, None, 0
            while pc != 0x2df8:
                steps += 1
                self.assertLess(steps, 24)
                x = code[pc]
                if x.mnemonic == 'cmp':
                    self.assertEqual(x.op_str, 'r0, #1')
                    equal = result == 1
                if x.mnemonic == 'bne' and not equal:
                    pc = int(x.op_str[1:], 0)
                    continue
                if x.mnemonic == 'b':
                    pc = int(x.op_str[1:], 0)
                    continue
                if x.mnemonic == 'movs' and x.op_str.startswith('r7,'):
                    rtc_mask = int(x.op_str.split('#')[1], 0)
                self.assertNotIn(x.mnemonic, ('bx', 'pop'))
                pc += x.size
            self.assertEqual(rtc_mask, 8 if result == 1 else None)
        self.assertEqual(self.mnl.relative(0x2ff0, 0x2de4), self.mnl.relative(0x3020, 0x2ec8))
        self.assertEqual(self.mnl.at(0x2ecc, 4).hex(), 'fef79cef')
        self.assertEqual(self.mnl.at(0x2f40, 4).hex(), 'fef76eef')
        self.assertEqual(self.mnl.at(0x2f54, 6).hex(), '112f40f00681')
    def test_transport_gates_separate_from_rtc(self):
        text = DRIVER.read_text()
        self.assertEqual(hashlib.sha256(DRIVER.read_bytes()).hexdigest(), '82c9bc1005bb259bdc07377e0e687303dc415b2305e2ddafa3877ce1580d4b05')
        self.assertIn('mtk_wcn_wmt_func_on(WMTDRV_TYPE_GPS)', text)
        self.assertIn('if (mtk_wcn_stp_is_ready())', text)
        self.assertIn('mtk_wcn_stp_send_data(&o_buf[0], copy_size, GPS_TASK_INDX)', text)
        self.assertNotIn('case COMBO_IOC_RTC_FLAG', text)
    def test_mnld_exact_chip_selector(self):
        self.assertEqual(self.mnld.text(self.mnld.relative(0x2374, 0x2124)), '0x6620')
        self.assertEqual(self.mnld.text(self.mnld.relative(0x2398, 0x2178)), '0x6572')
        self.assertEqual(self.mnld.at(0x217a, 4).hex(), 'fff72eeb')
        self.assertEqual(self.mnld.at(0x226a, 4).hex(), 'fff7bcea')
    def test_real_callback_relocations_show_thread_mismatch(self):
        dynsym = self.hidl.elf.get_section_by_name('.dynsym')
        slots = {}
        for off, info in packed_rel(self.hidl.elf.get_section_by_name('.rel.dyn').data()):
            if 0x1a018 <= off < 0x1a048:
                self.assertEqual(info & 255, 2)
                slots[off - 0x1a018] = dynsym.get_symbol(info >> 8).name
        self.assertIn('createThreadCb', slots[0x20])
        self.assertIn('requestUtcTimeCb', slots[0x24])
        self.assertIn('locationCb', slots[4])
        self.assertIn('gpsSvStatusCb', slots[12])
        self.assertEqual(self.hal.relative(0x4244, 0x4082), 0xf13c)
        self.assertEqual(self.hal.at(0x41d2, 4).hex(), 'daf82430')
        self.assertEqual(self.hal.at(0x41da, 2).hex(), '9847')
        self.assertNotIn('createThreadCb', slots[0x24])
    def test_fixed_callback_copy_not_size_only(self):
        ins = self.hal.instructions(0x406e, 0x14)
        self.assertEqual([x.op_str for x in ins if x.mnemonic.startswith('ldm')], ['r4!, {r0, r1, r2, r3}', 'r4!, {r0, r1, r2, r3}', 'r4, {r0, r1, r2}'])
        self.assertEqual(self.hal.word(0xedac), 52)
        self.assertEqual(self.hidl.word(0x1a018), 48)
    def test_satellite_layout_requires_further_translation_audit(self):
        self.assertEqual(self.hal.at(0x5b4e, 4).hex(), '41f26842')
        ins = self.hidl.instructions(0xeef4, 0x12)
        self.assertIn('#0x288', ins[0].op_str)
        self.assertTrue(any('#0x290' in x.op_str for x in ins))
    def test_parser_rejects_truncated_and_invalid_fixtures(self):
        for data in (b'', b'APS2', b'APS2' + bytes([1, 0, 0, 0]), b'APS2' + bytes([128])):
            with self.assertRaises(ValueError):
                packed_rel(data)

if __name__ == '__main__':
    unittest.main()
