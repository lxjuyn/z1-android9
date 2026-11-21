#!/usr/bin/env python3
"""Execute the read-only production preflight in isolated Android fixtures."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('z1_nvram_preflight.sh')
SHELLS = [['/bin/dash'], ['/bin/bash']]
if shutil.which('busybox'):
    SHELLS.append([shutil.which('busybox'), 'ash'])


class PreflightTests(unittest.TestCase):
    def run_fixture(self, change=None, args=()):
        for shell in SHELLS:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory(prefix='nvram-fixture-') as td:
                root = Path(td)
                (root/'bin').mkdir()
                (root/'dev').mkdir()
                (root/'dev/null').touch()
                elf = bytearray(52)
                elf[:6] = b'\x7fELF\x01\x01'
                elf[16:20] = b'\x03\x00\x28\x00'
                for path in ['system/bin/nvram_daemon', *[
                    'vendor/lib/'+lib+'.so' for lib in ['libnvram', 'libcustom_nvram',
                    'libnvram_sec', 'libnvram_platform', 'libhwm', 'libfile_op',
                    'libnvram_daemon_callback']]]:
                    p = root/path
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(elf)
                    p.chmod(0o755 if p.name == 'nvram_daemon' else 0o644)
                secret = b'PRIVATE_CALIBRATION_CONTENT_DO_NOT_PRINT'
                data = root/'data/nvram'
                (data/'APCFG/APRDEB').mkdir(parents=True)
                (data/'APCFG/APRDCL').mkdir()
                (data/'RestoreFlag').write_bytes(b'\x78\x56\x34\x12'+secret)
                (data/'APCFG/APRDEB/BT_Addr').write_bytes(
                    secret.ljust(66, b'\xa5'))
                (data/'APCFG/APRDEB/WIFI').write_bytes(
                    secret.ljust(514, b'\xa5'))
                (data/'APCFG/APRDCL/FILE_VER').write_bytes(
                    secret.ljust(3404, b'\xa5'))
                props = {'init.svc.nvram_daemon': 'running', 'nvram_init': 'Ready'}
                if change:
                    change(root, props)
                getprop = root/'bin/getprop'
                getprop.write_text('#!/usr/bin/python3\nimport sys\np='+repr(props)+
                                   '\nif p.pop("__query_failure__", False): sys.exit(1)\nprint(p.get(sys.argv[1], ""))\n')
                getprop.chmod(0o755)
                # Any attempted mutation fails the test, including ash builtins.
                blocked = '\n'.join(f'{c}() {{ echo MUTATION_ATTEMPT >&2; return 99; }}'
                    for c in ['setprop', 'start', 'stop', 'mount', 'mknod', 'cp',
                              'mv', 'rm', 'mkdir', 'chmod', 'chown'])
                source = re.sub(r'/(?:system|vendor|data|dev)(?=/)',
                                lambda m: str(root)+m.group(), SCRIPT.read_text())
                fixture = root/'preflight.sh'
                fixture.write_text(blocked+'\n'+source)
                def snapshot():
                    return {str(p.relative_to(root)): (p.lstat().st_mode,
                        p.readlink().as_posix() if p.is_symlink() else
                        hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
                        for p in root.rglob('*')}
                before = snapshot()
                result = subprocess.run(shell+[str(fixture), *args], capture_output=True,
                    text=True, timeout=10, env={'PATH': str(root/'bin')+':/usr/bin:/bin',
                                              'LC_ALL': 'C'})
                self.assertNotIn('MUTATION_ATTEMPT', result.stderr)
                self.assertNotIn(secret.decode(), result.stdout+result.stderr)
                self.assertEqual(snapshot(), before)
                yield result

    def test_complete_shapes_remain_unknown_not_hardware_success(self):
        for r in self.run_fixture():
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn('PASS restore_marker_format', r.stdout)
            self.assertIn('PASS daemon_self_reported_ready', r.stdout)
            self.assertIn('PASS record_shape_66_bytes', r.stdout)
            self.assertIn('PASS record_shape_514_bytes', r.stdout)
            self.assertIn('PASS record_shape_3404_bytes', r.stdout)
            self.assertIn('UNKNOWN nvram_raw_restore_backend_unavailable', r.stdout)
            self.assertIn('READ_ONLY_CHECK_ONLY', r.stdout)

    def test_missing_dependency_is_failure(self):
        def change(root, props): (root/'vendor/lib/libfile_op.so').unlink()
        for r in self.run_fixture(change):
            self.assertEqual(r.returncode, 1)
            self.assertRegex(r.stdout, r'FAIL ELF32_ARM .*/libfile_op.so')

    def test_wrong_elf_architecture_is_failure(self):
        def change(root, props):
            p=root/'vendor/lib/libhwm.so'; b=bytearray(p.read_bytes());b[18]=62;p.write_bytes(b)
        for r in self.run_fixture(change): self.assertEqual(r.returncode, 1)

    def test_missing_or_invalid_restore_marker_is_failure(self):
        for value in (b'', b'\x78\x56\x34', b'12345678', b'\x12\x34\x56\x78'):
            def change(root, props): (root/'data/nvram/RestoreFlag').write_bytes(value)
            for r in self.run_fixture(change):
                self.assertEqual(r.returncode, 1)
                self.assertIn('FAIL restore_marker_format', r.stdout)

    def test_record_size_and_type_are_checked_without_reading_contents(self):
        for kind in ('short', 'storage-short', 'missing', 'symlink', 'fifo'):
            def change(root, props):
                p=root/'data/nvram/APCFG/APRDEB/BT_Addr';p.unlink()
                if kind=='short': p.write_bytes(b'\0'*63)
                elif kind=='storage-short': p.write_bytes(b'\0'*65)
                elif kind=='symlink': p.symlink_to(root/'data/nvram/RestoreFlag')
                elif kind=='fifo': os.mkfifo(p)
            for r in self.run_fixture(change):
                self.assertEqual(r.returncode, 1)
                self.assertIn('FAIL no_complete_bt_record', r.stdout)

    def test_version_table_must_be_a_3404_byte_regular_file(self):
        for kind in ('missing', 'short', 'symlink'):
            def change(root, props):
                p=root/'data/nvram/APCFG/APRDCL/FILE_VER';p.unlink()
                if kind=='short': p.write_bytes(b'\0'*3403)
                elif kind=='symlink': p.symlink_to(root/'data/nvram/RestoreFlag')
            for r in self.run_fixture(change):
                self.assertEqual(r.returncode, 1)
                if kind == 'short':
                    self.assertIn('FAIL record_size_invalid', r.stdout)
                else:
                    self.assertIn(
                        'FAIL file_version_regular_readable', r.stdout
                    )

    def test_fallback_record_does_not_bypass_ready_or_provenance(self):
        def change(root, props):
            (root/'data/nvram/APCFG/APRDEB/BT_Addr').unlink()
            (root/'data/BT_Addr').write_bytes(b'\xa5'*64)
            props['nvram_init']=''
        for r in self.run_fixture(change):
            self.assertEqual(r.returncode, 2)
            self.assertIn('UNKNOWN daemon_has_not_reported_ready', r.stdout)

    def test_unobserved_service_state_remains_unknown(self):
        def change(root, props): props.clear()
        for r in self.run_fixture(change):
            self.assertEqual(r.returncode, 2)
            self.assertIn('UNKNOWN daemon_service_state_unobserved', r.stdout)

    def test_failed_property_query_is_not_reported_as_missing_service(self):
        def change(root, props): props['__query_failure__'] = True
        for r in self.run_fixture(change):
            self.assertEqual(r.returncode, 2)
            self.assertIn('UNKNOWN daemon_service_state_unobserved', r.stdout)
            self.assertNotIn('FAIL ', r.stdout)

    def test_arguments_cannot_override_live_paths(self):
        for r in self.run_fixture(args=('/dev/nvram',)):
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout, 'FAIL arguments unsupported\n')


if __name__ == '__main__':
    unittest.main()
