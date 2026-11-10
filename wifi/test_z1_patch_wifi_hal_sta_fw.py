#!/usr/bin/env python3
"""Required offline tests; unavailable fixtures/toolchains fail instead of skip.

Run with explicit fixtures (no hardware/network/service access):
 Z1_WIFI_HAL_ORIG=/absolute/original.so Z1_SYSTEM_IMAGE=/absolute/system.img \
   python3 /absolute/wifi/test_z1_patch_wifi_hal_sta_fw.py

Three machine-code functions run under qemu-arm with controlled PLT calls.
This is NOT a full Android linker/HIDL/nl80211/Settings integration test.
Original/vendor .so files and generated tests/results remain local artifacts.
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
TOOL = HERE / 'z1_patch_wifi_hal_sta_fw.py'
SPEC = importlib.util.spec_from_file_location('sta_patch', TOOL)
patch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patch)
ORIGINAL = Path(os.environ.get('Z1_WIFI_HAL_ORIG', str(
    PROJECT / 'bridge_backups/wifi_hidl_sta_fwfix_20261110_210313/libwifi-hal.so.orig')))
IMAGE = Path(os.environ.get('Z1_SYSTEM_IMAGE', str(PROJECT / 'out/z1_v44/sys/system_v44.img')))
PREBUILT = HERE / 'prebuilt/libwifi-hal.so'


def run(args):
    return subprocess.run([str(a) for a in args], capture_output=True, text=True,
                          timeout=30, env={**os.environ, 'LC_ALL': 'C'})


def command(name):
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError('required host tool missing: ' + name)
    return exe


def sleb(data, cursor):
    value, shift = 0, 0
    for _ in range(10):
        if cursor >= len(data):
            raise ValueError('truncated SLEB128')
        byte = data[cursor]
        cursor += 1
        value |= (byte & 127) << shift
        shift += 7
        if not byte & 128:
            if byte & 64:
                value -= 1 << shift
            return value, cursor
    raise ValueError('oversized SLEB128')


def aps2_offsets(blob):
    """Decode Android packed REL groups; readelf does not list these entries."""
    if blob[:4] != b'APS2':
        raise ValueError('not APS2')
    count, pos = sleb(blob, 4)
    offset, pos = sleb(blob, pos)
    if count < 0 or count > 100000:
        raise ValueError('invalid relocation count')
    output = []
    while len(output) < count:
        group, pos = sleb(blob, pos)
        flags, pos = sleb(blob, pos)
        if group <= 0 or group > count - len(output) or flags & ~15:
            raise ValueError('invalid group')
        if flags & 8:  # This pinned ELF is REL, not RELA.
            raise ValueError('unexpected packed addend')
        delta = 0
        if flags & 2:
            delta, pos = sleb(blob, pos)
        if flags & 1:
            _, pos = sleb(blob, pos)
        for _ in range(group):
            if not flags & 2:
                delta, pos = sleb(blob, pos)
            offset += delta
            if not flags & 1:
                _, pos = sleb(blob, pos)
            output.append(offset)
    # AOSP android-9.0.0_r61 bionic/tools/relocation_packer/src/elf_file.cc
    # lines 775-777 explicitly append zero padding; linker_reloc_iterators.h
    # lines 84-85 terminate at the advertised count, not section byte length.
    # Permit only that documented padding, never arbitrary trailing content.
    if any(blob[pos:]):
        raise ValueError('nonzero packed relocation padding')
    return output


def relocation_offsets(data):
    _, sections = patch.elf_layout(data)
    result, packed_count = [], 0
    for sec in sections:
        kind, start, size, entsize = sec[1], sec[4], sec[5], sec[9]
        if kind == 0x60000001:  # SHT_ANDROID_REL
            entries = aps2_offsets(data[start:start + size])
            result.extend(entries)
            packed_count += len(entries)
        elif kind in (9, 4):  # SHT_REL / SHT_RELA
            if entsize not in (8, 12) or size % entsize:
                raise ValueError('bad reloc table')
            result.extend(struct.unpack_from('<I', data, i)[0]
                          for i in range(start, start + size, entsize))
    return result, packed_count


def read_image_file(image, internal_path):
    # debugfs may return success on missing files; only an actual dumped file
    # counts. Do not merge stderr (the version banner) into config content.
    with tempfile.TemporaryDirectory(prefix='z1_wifi_read_') as tmp:
        output = Path(tmp) / 'dump'
        quoted = lambda s: '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'
        response = run([command('debugfs'), '-R',
                        'dump ' + quoted(internal_path) + ' ' + quoted(output), image])
        if response.returncode or not output.is_file() or not output.stat().st_size:
            raise FileNotFoundError('image file is absent/empty: ' + internal_path)
        return output.read_bytes()


class PatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = ORIGINAL.read_bytes()
        if patch.sha256(cls.original) != patch.ORIGINAL_SHA256:
            raise RuntimeError('original fixture does not match pinned SHA')
        cls.tmpdir = tempfile.TemporaryDirectory(prefix='z1_sta_tests_')
        cls.addClassCleanup(cls.tmpdir.cleanup)
        cls.tmp = Path(cls.tmpdir.name)
        cls.candidate = cls.tmp / 'candidate.so'
        patch.write_candidate(ORIGINAL, cls.candidate)
        cls.patched = cls.candidate.read_bytes()
        cls.qemu = command('qemu-arm')
        cls.exe = cls.tmp / 'harness'
        result = run([command('arm-linux-gnueabihf-gcc'), '-static', '-O2', '-Wall', '-Wextra',
                      '-Werror', '-o', cls.exe, HERE / 'test_wifi_hal_branch_harness.c'])
        if result.returncode:
            raise RuntimeError('harness compile failed: ' + result.stderr)
        cls.objdump = command('arm-linux-gnueabihf-objdump')

    def behavior(self, path, mode, firmware_result):
        result = run([self.qemu, self.exe, path, str(mode), str(firmware_result)])
        self.assertEqual(result.returncode, 0, result.stderr)
        return {key: int(value) for key, value in (x.split('=') for x in result.stdout.split())}

    def mutation(self, name, offset, value):
        data = bytearray(self.original)
        data[offset:offset + len(value)] = value
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_01_exact_candidate_identity_and_extent(self):
        self.assertEqual(len(self.patched), 95116)
        self.assertEqual(patch.sha256(self.patched), patch.PATCHED_SHA256)
        self.assertEqual(PREBUILT.read_bytes(), self.patched)
        self.assertEqual((HERE / 'prebuilt/libwifi-hal.so.sha256').read_text().split()[0],
                         patch.PATCHED_SHA256)
        self.assertEqual(self.original[:0xcf38], self.patched[:0xcf38])
        self.assertEqual(self.original[0xcf50:], self.patched[0xcf50:])
        self.assertEqual(self.patched[0xcf38:0xcf50], patch.PATCHED_BYTES)
        self.assertEqual(ORIGINAL.read_bytes(), self.original)
        self.assertEqual(self.candidate.stat().st_mode & 0o777, 0o644)

    def test_02_dynamic_symbol_and_elf_guards(self):
        self.assertEqual(patch.validate_elf(self.original), 0xcf38)
        self.assertEqual(patch.validate_elf(self.patched), 0xcf38)
        for offset, value in [(4, 2), (5, 2), (18, 62), (42, 0), (46, 0)]:
            data = bytearray(self.original)
            data[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                patch.validate_elf(data)
        data = bytearray(self.original)
        name = data.find(b'wifi_get_fw_path\x00')
        self.assertGreater(name, 0)
        data[name] = ord('X')
        with self.assertRaisesRegex(ValueError, 'exported symbol'):
            patch.validate_elf(data)
        data = bytearray(self.original)
        struct.pack_into('<I', data, 0xf5a0, 1)  # kFirmwareModeSta
        with self.assertRaisesRegex(ValueError, 'STA mode'):
            patch.validate_elf(data)

    def test_03_exact_branch_targets_and_literal_address(self):
        result = run([self.objdump, '-d', '--disassemble=wifi_get_fw_path', self.candidate])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r'df3c:\s+d804\s+bhi(?:\.n)?\s+df48')
        self.assertRegex(result.stdout, r'df3e:.*ldr\s+r1,.*df4c')
        self.assertRegex(result.stdout, r'df48:\s+2000\s+movs\s+r0,\s*#0')
        self.assertRegex(result.stdout, r'df4a:\s+4770\s+bx\s+lr')
        delta = struct.unpack_from('<I', self.patched, 0xcf4c)[0]
        self.assertEqual(0xdf40 + 4 + delta, 0x16aa4)
        # Original HIDL bool-return branch remains byte-identical.
        self.assertEqual(self.original[0x4556:0x4574], self.patched[0x4556:0x4574])
        result = run([self.objdump, '-dC', '--start-address=0x5556',
                      '--stop-address=0x5574', self.candidate])
        self.assertRegex(result.stdout, r'555e:\s+b138\s+cbz\s+r0,\s*5570')
        self.assertRegex(result.stdout, r'5570:\s+2001\s+movs\s+r0,\s*#1')

    def test_04_actual_android_relocations_do_not_cover_patch(self):
        offsets, count = relocation_offsets(self.original)
        self.assertEqual(count, 665)  # Actual APS2 entries, not empty readelf output.
        self.assertIn(0x16aa4, offsets)
        self.assertIn(0x16aa8, offsets)
        self.assertFalse([offset for offset in offsets if offset < 0xdf50 and offset + 4 > 0xdf38])
        self.assertEqual(relocation_offsets(self.patched), (offsets, count))
        valid = b'APS2' + bytes([1, 0, 1, 3, 4, 0])
        self.assertEqual(aps2_offsets(valid), [4])
        self.assertEqual(aps2_offsets(valid + bytes(200)), [4])
        malformed = [b'', b'APS2', b'APS2' + bytes([128]), valid[:-1],
                     valid + bytes([0, 1]), b'APS2' + bytes([1, 0, 0, 0]),
                     b'APS2' + bytes([1, 0, 2, 0]),
                     b'APS2' + bytes([1, 0, 1, 16]),
                     b'APS2' + bytes([1, 0, 1, 8])]
        for blob in malformed:
            with self.subTest(blob=blob.hex()), self.assertRaises(ValueError):
                aps2_offsets(blob)

    def test_05_sta_null_contract_for_success_and_failure(self):
        for result in [-1, 0, 5]:
            with self.subTest(firmware_result=result):
                original = self.behavior(ORIGINAL, 0, result)
                self.assertEqual(original, dict(mode=0, path=0x14baa, needed=1,
                                               result=int(result == 0), calls=1, passed=0x14baa))
                self.assertEqual(self.behavior(self.candidate, 0, result),
                                 dict(mode=0, path=0, needed=0, result=1, calls=0, passed=0))

    def test_06_ap_preserves_path_failures_and_success(self):
        for result in [-1, 0, 5]:
            with self.subTest(firmware_result=result):
                expected = dict(mode=1, path=0x14bca, needed=1,
                                result=int(result == 0), calls=1, passed=0x14bca)
                self.assertEqual(self.behavior(ORIGINAL, 1, result), expected)
                self.assertEqual(self.behavior(self.candidate, 1, result), expected)

    def test_07_p2p_and_invalid_modes_unchanged(self):
        modes = [2, 3, -1, -2147483648, 2147483647]
        rng = random.Random(6572)
        modes += [rng.randint(-2147483648, 2147483647) for _ in range(12)]
        for mode in modes:
            with self.subTest(mode=mode):
                expected = dict(mode=mode, path=0, needed=0, result=1, calls=0, passed=0)
                self.assertEqual(self.behavior(ORIGINAL, mode, -1), expected)
                self.assertEqual(self.behavior(self.candidate, mode, -1), expected)

    def test_08_rejected_e006_reproduces_old_false_positive(self):
        broken = self.mutation('e006.so', 0x455e, bytes.fromhex('06e0'))
        self.assertEqual(patch.sha256(broken.read_bytes()),
                         '7ffb33887994ac5b116d65c3859ea365ca93034ddcdf154c73a2e643325edab3')
        actual = self.behavior(broken, 0, -1)
        self.assertEqual(actual['calls'], 0)
        self.assertNotIn(actual['result'], (0, 1))  # Real non-NULL pointer, not fake r0=1.
        self.assertEqual(actual['needed'], 1)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            patch.patch_bytes(broken.read_bytes())
        result = run([self.objdump, '-dC', '--start-address=0x555e', '--stop-address=0x5560', broken])
        self.assertRegex(result.stdout, r'555e:\s+e006\s+b(?:\.n)?\s+556e')

    def test_09_e007_targets_true_but_incorrectly_bypasses_ap(self):
        broken = self.mutation('e007.so', 0x455e, bytes.fromhex('07e0'))
        actual = self.behavior(broken, 1, -1)
        self.assertEqual((actual['result'], actual['calls']), (1, 0))
        self.assertNotEqual(actual, self.behavior(self.candidate, 1, -1))
        result = run([self.objdump, '-dC', '--start-address=0x555e', '--stop-address=0x5560', broken])
        self.assertRegex(result.stdout, r'555e:\s+e007\s+b(?:\.n)?\s+5570')

    def test_10_unknown_truncated_and_patched_inputs_never_write(self):
        for label, data in [('empty', b''), ('truncated', self.original[:-1]),
                            ('unknown', self.original[:-1] + bytes([self.original[-1] ^ 1])),
                            ('patched', self.patched)]:
            with self.subTest(label=label):
                source, output = self.tmp / (label + '.input'), self.tmp / (label + '.out')
                source.write_bytes(data)
                with self.assertRaises(ValueError):
                    patch.write_candidate(source, output)
                self.assertFalse(output.exists())
                output.write_bytes(b'preserve output on failure')
                result = run([sys.executable, TOOL, source, output, '--force'])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(output.read_bytes(), b'preserve output on failure')

    def test_11_input_aliases_output_and_symlink_refused(self):
        source = self.tmp / 'alias_original.so'
        source.write_bytes(self.original)
        hard = self.tmp / 'hardlink.so'
        os.link(source, hard)
        sym = self.tmp / 'symlink.so'
        sym.symlink_to(source)
        for output in [source, hard, sym]:
            with self.subTest(output=output.name), self.assertRaises(ValueError):
                patch.write_candidate(source, output, force=True)
        self.assertEqual(source.read_bytes(), self.original)
        existing = self.tmp / 'existing.so'
        existing.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'output exists'):
            patch.write_candidate(source, existing)
        self.assertEqual(existing.read_bytes(), b'existing')
        patch.write_candidate(source, existing, force=True)
        self.assertEqual(existing.read_bytes(), self.patched)

    def test_12_bad_explicit_fixture_is_failure_not_skip(self):
        # Run only a fixture-dependent test in a child process to prove an
        # explicit missing/wrong original fails instead of silently skipping.
        bad = self.tmp / 'bad_explicit_fixture.so'
        bad.write_bytes(b'wrong fixture')
        for fixture in [bad, self.tmp / 'missing_fixture.so']:
            response = subprocess.run(
                [sys.executable, __file__, 'PatchTest.test_01_exact_candidate_identity_and_extent'],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, 'Z1_WIFI_HAL_ORIG': str(fixture),
                     'PYTHONDONTWRITEBYTECODE': '1', 'LC_ALL': 'C'})
            with self.subTest(fixture=fixture.name):
                self.assertNotEqual(response.returncode, 0)
                self.assertIn('FAILED (errors=1)', response.stderr)
                self.assertNotIn('skipped', response.stderr.lower())


class ImageAssemblyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not IMAGE.is_file():
            raise RuntimeError('required image fixture missing: ' + str(IMAGE))

    def test_01_hidl_service_definitions(self):
        rc = read_image_file(IMAGE, '/vendor/etc/init/wpa_supplicant.rc').decode()
        active = [line.strip() for line in rc.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        self.assertIn('service wpa_supplicant /vendor/bin/hw/wpa_supplicant \\', active)
        self.assertIn('interface android.hardware.wifi.supplicant@1.0::ISupplicant default', active)
        self.assertIn('interface android.hardware.wifi.supplicant@1.1::ISupplicant default', active)
        self.assertIn('disabled', active)
        self.assertIn('oneshot', active)
        self.assertFalse(any('/system/bin/wpa_supplicant' in line for line in active))
        hal = read_image_file(IMAGE, '/vendor/etc/init/android.hardware.wifi@1.0-service.rc').decode()
        self.assertIn('service vendor.wifi_hal_legacy /vendor/bin/hw/android.hardware.wifi@1.0-service', hal)

    def test_02_real_sta_configs_and_missing_file_failure(self):
        conf = read_image_file(IMAGE, '/vendor/etc/wifi/wpa_supplicant.conf').decode()
        self.assertRegex(conf, r'(?m)^\s*ctrl_interface=')
        overlay = read_image_file(IMAGE, '/vendor/etc/wifi/wpa_supplicant_overlay.conf').decode()
        self.assertIn('disable_scan_offload=1', overlay)
        with self.assertRaises(FileNotFoundError):
            read_image_file(IMAGE, '/vendor/etc/wifi/__missing_sta_test__.conf')

    def test_03_vintf_semantic_hal_version_and_instance(self):
        root = ET.fromstring(read_image_file(IMAGE, '/vendor/etc/vintf/manifest.xml'))
        for name, version, iface in [('android.hardware.wifi', '1.0', 'IWifi'),
                                     ('android.hardware.wifi.supplicant', '1.1', 'ISupplicant')]:
            matches = [h for h in root.findall('hal') if h.findtext('name') == name]
            self.assertEqual(len(matches), 1)
            hal = matches[0]
            self.assertEqual(hal.findtext('transport'), 'hwbinder')
            self.assertIn(version, [v.text for v in hal.findall('version')])
            self.assertIn('@%s::%s/default' % (version, iface), [f.text for f in hal.findall('fqname')])

    def test_04_image_has_exact_original_hal_and_hidl_supplicant(self):
        self.assertEqual(patch.sha256(read_image_file(IMAGE, '/vendor/lib/libwifi-hal.so')),
                         patch.ORIGINAL_SHA256)
        self.assertEqual(patch.sha256(read_image_file(IMAGE, '/vendor/bin/hw/wpa_supplicant')),
                         'fd24bb69310cd45077cb1186c3af9716ddc2952fe993ba200bf6b66f72c2d6cf')
        self.assertEqual(patch.sha256(read_image_file(IMAGE, '/vendor/bin/hw/android.hardware.wifi@1.0-service')),
                         '759c5d0d3bac9609dee9900a658ce7a15b4ba5d9ffc78b9217c9ca875d9abe44')


if __name__ == '__main__':
    unittest.main(verbosity=2)
