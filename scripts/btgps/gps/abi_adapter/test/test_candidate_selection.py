"""Regression check for the Android system selected for callback ABI testing."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_offline_candidate as candidate


class ReachedAbiTest(Exception):
    """Stop the build before the ramdisk and ext4 packaging steps."""


class CandidateSelectionTest(unittest.TestCase):
    def test_custom_base_is_used_for_callback_abi_test(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            inputs = {}
            for name in ('stock', 'gpsdrv', 'prep', 'custom.boot', 'custom.system'):
                path = root / name
                path.write_bytes(name.encode())
                inputs[name] = path
            boot = inputs['custom.boot']
            system = inputs['custom.system']
            self.assertNotEqual(system, candidate.BASE_SYSTEM)

            def checked_run(command, **_):
                path = Path(command[0])
                if path.name == 'run_arm_qemu.sh':
                    self.assertEqual(command, [candidate.HERE / 'test/run_arm_qemu.sh', system])
                    raise ReachedAbiTest
                if path.name == 'readelf':
                    return ('Library soname: [gps.z1stock.so]\n'
                            'Shared library: [libxlog.so]\ngps__get_gps_interface\n'
                            'Library soname: [gps.default.so]\n GLOBAL HMI\n')
                if path.name in ('patchelf', 'build.sh'):
                    return None
                self.fail(f'unexpected command: {command}')

            with patch.multiple(
                candidate, run=checked_run,
                STOCK_HAL=inputs['stock'], STOCK_HAL_SHA=candidate.sha_file(inputs['stock']),
                GPSDRV=inputs['gpsdrv'], GPSDRV_SHA=candidate.sha_file(inputs['gpsdrv']),
                PREP=inputs['prep'], PREP_SHA=candidate.sha_file(inputs['prep']),
            ):
                with self.assertRaises(ReachedAbiTest):
                    candidate.build(root / 'candidate', base_boot=boot,
                                    base_boot_sha=candidate.sha_file(boot),
                                    base_system=system,
                                    base_system_sha=candidate.sha_file(system))


if __name__ == '__main__':
    unittest.main()
