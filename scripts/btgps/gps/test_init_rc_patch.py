#!/usr/bin/env python3
"""Offline regression tests. Only TemporaryDirectory fixtures are modified.

Run: python3 test_init_rc_patch.py -v
Requires dash, BusyBox (ash), bash and Python 3; missing shells are failures.
The ineffective-write test runs a temporary Python wrapper that replaces
os.replace only inside the child process; the installed script is unmodified.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'apply_init_rc_patch.sh'
OLD = b'service z1_mnld /vendor/bin/mnld\n'
NEW = b'service mnld /vendor/bin/mnld\n'
SHELLS = [('dash', ['dash']), ('busybox-ash', ['busybox', 'ash']), ('bash', ['bash'])]


class InitRenameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shells = []
        for name, command in SHELLS:
            executable = shutil.which(command[0])
            if not executable:
                raise RuntimeError('required test shell missing: ' + name)
            cls.shells.append((name, [executable] + command[1:]))
        cls.script_hash = hashlib.sha256(SCRIPT.read_bytes()).digest()

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(SCRIPT.read_bytes()).digest() != cls.script_hash:
            raise AssertionError('tests modified the source script')

    def invoke(self, shell, root, *, env=None, args=None):
        return subprocess.run(shell + [str(SCRIPT)] + ([str(root)] if args is None else args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=10, env=env)

    def assert_result(self, result, success):
        description = (result.returncode, result.stdout, result.stderr)
        self.assertEqual(result.returncode == 0, success, description)
        self.assertEqual(b'OK:' in result.stdout, success, description)
        self.assertNotIn(b'Traceback', result.stderr, description)
        if not success:
            self.assertTrue(result.stderr, description)

    def check_case(self, original, expected=None, *, failure=False, mode=0o640):
        for name, shell in self.shells:
            with self.subTest(shell=name), tempfile.TemporaryDirectory(prefix='gps init rc ') as directory:
                root = Path(directory)
                target = root / 'init.z1.network.rc'
                target.write_bytes(original)
                target.chmod(mode)
                os.utime(target, ns=(1700000000000000000, 1700000000000000000))
                before = target.stat()
                result = self.invoke(shell, root)
                self.assert_result(result, not failure)
                self.assertEqual(target.read_bytes(), original if failure else expected)
                self.assertEqual(target.stat().st_mode, before.st_mode)
                self.assertEqual((target.stat().st_uid, target.stat().st_gid),
                                 (before.st_uid, before.st_gid))
                self.assertEqual(target.stat().st_mtime_ns, before.st_mtime_ns)
                if failure or original == expected:
                    self.assertEqual(target.stat().st_ino, before.st_ino)
                self.assertEqual(sorted(p.name for p in root.iterdir()), ['init.z1.network.rc'])
                if not failure:
                    # Every accepted output must remain identical and untouched on repeat.
                    after = target.stat()
                    second = self.invoke(shell, root)
                    self.assert_result(second, True)
                    self.assertIn(b'already renamed', second.stdout)
                    self.assertEqual(target.read_bytes(), expected)
                    self.assertEqual(target.stat().st_ino, after.st_ino)
                    self.assertEqual(target.stat().st_mtime_ns, after.st_mtime_ns)

    def test_shell_syntax_and_argument_validation(self):
        for name, shell in self.shells:
            with self.subTest(shell=name):
                result = subprocess.run(shell + ['-n', str(SCRIPT)], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                with tempfile.TemporaryDirectory() as directory:
                    for args in ([], [directory, 'extra']):
                        self.assert_result(self.invoke(shell, Path(directory), args=args), False)

    def test_basic_and_already_renamed(self):
        self.check_case(OLD + b'    disabled\n    socket mnld stream 0660 system system\n',
                        NEW + b'    disabled\n    socket mnld stream 0660 system system\n')
        self.check_case(NEW, NEW)

    def test_whitespace_comments_quotes_and_line_endings(self):
        for line in (b'  service\tz1_mnld\t/vendor/bin/mnld  \t\n',
                     b'service z1_mnld /vendor/bin/mnld\t # daemon\r\n',
                     b'\tservice "z1_mnld" "/vendor/bin/mnld" --test\r\n',
                     b'service z1_mnld /vendor/bin/mnld   '):
            with self.subTest(line=line):
                self.check_case(line, line.replace(b'z1_mnld', b'mnld'))

    def test_service_and_property_references(self):
        old_refs = (
            b'    onrestart restart z1_mnld\n'
            b'on property:init.svc.z1_mnld=stopped && property:x=y\n'
            b'    wait_for_prop init.svc.z1_mnld running\n'
            b'    start z1_mnld\n    stop z1_mnld\n    restart z1_mnld\n'
            b'    restart --only-if-running z1_mnld\n'
            b'    enable z1_mnld\n    exec_start z1_mnld\n'
            b'    setprop ctl.start z1_mnld\n    setprop ctl.stop z1_mnld\n'
            b'    setprop ctl.restart "z1_mnld"\n'
            b'    setprop ctl.oneshot_on z1_mnld\n    setprop ctl.oneshot_off z1_mnld\n'
            b'    setprop vendor.gps.state "${init.svc.z1_mnld}"\n')
        self.check_case(OLD + old_refs, NEW + old_refs.replace(b'z1_mnld', b'mnld'))
        # A declaration already renamed with stale references is repairable.
        self.check_case(NEW + old_refs, NEW + old_refs.replace(b'z1_mnld', b'mnld'))

    def test_unrelated_content_remains_byte_identical(self):
        unrelated = (
            b'# service z1_mnld /vendor/bin/mnld\n'
            b'# start z1_mnld; ${init.svc.z1_mnld}\n'
            b'service other /vendor/bin/z1_mnld_helper\n'
            b'    socket z1_mnld stream 0660 system system\n'
            b'on property:init.svc.z1_mnld_helper=running\n'
            b'    wait_for_prop init.svc.z1_mnld_helper running\n'
            b'    start z1_mnld_helper\n'
            b'    setprop vendor.note z1_mnld\n'
            b'    write /data/local/z1_mnld "unchanged"\n'
            b'    setprop vendor.state ${init.svc.z1_mnld_helper}\n')
        self.check_case(unrelated + OLD, unrelated + NEW)

    def test_conflicts_and_duplicate_definitions(self):
        for data in (OLD + NEW, NEW + OLD, OLD * 2, NEW * 2, OLD * 3,
                     OLD + b'\tservice mnld /vendor/bin/mnld \t\n',
                     OLD + b'service "z1_mnld" /vendor/bin/mnld\n',
                     OLD.rstrip(b'\n') + b'  \t\n' + NEW):
            with self.subTest(data=data):
                self.check_case(data, failure=True)

    def test_no_match_and_wrong_executable(self):
        for data in (b'', b'# ' + OLD, b'start z1_mnld\n',
                     b'service z1_mnld_helper /vendor/bin/mnld\n',
                     b'service z1_mnld /wrong/mnld\n',
                     b'service mnld /wrong/mnld\n', b'service z1_mnld\n'):
            with self.subTest(data=data):
                self.check_case(data, failure=True)

    def test_malformed_and_unsupported_references_fail_closed(self):
        for suffix in (b'    start z1_mnld unexpected\n',
                       b'    setprop ctl.start z1_mnld unexpected\n',
                       b'    exec -- /system/bin/sh -c "start z1_mnld"\n',
                       b'    exec_background -- /system/bin/start z1_mnld\n',
                       b'    wait_for_prop init.svc.z1_mnld running extra\n',
                       b'    start "z1_mnld\n', b'\0',
                       b'    start \\\n        z1_mnld\n'):
            with self.subTest(suffix=suffix):
                self.check_case(OLD + suffix, failure=True)

    def test_invalid_target_types(self):
        for name, shell in self.shells:
            for kind in ('missing-root', 'root-file', 'root-symlink', 'missing-file',
                         'directory', 'symlink', 'dangling-symlink', 'hardlink', 'fifo', 'live-root'):
                with self.subTest(shell=name, kind=kind), tempfile.TemporaryDirectory() as directory:
                    base = Path(directory)
                    root = base / 'ramdisk'
                    root.mkdir()
                    target = root / 'init.z1.network.rc'
                    sentinel = base / 'sentinel'
                    sentinel.write_bytes(OLD)
                    if kind == 'missing-root':
                        root = base / 'absent'
                    elif kind == 'root-file':
                        root = sentinel
                    elif kind == 'root-symlink':
                        link = base / 'link'
                        link.symlink_to(root, target_is_directory=True)
                        root = link
                    elif kind == 'directory':
                        target.mkdir()
                    elif kind == 'symlink':
                        target.symlink_to(sentinel)
                    elif kind == 'dangling-symlink':
                        target.symlink_to(base / 'absent')
                    elif kind == 'hardlink':
                        os.link(sentinel, target)
                    elif kind == 'fifo':
                        os.mkfifo(target)
                    elif kind == 'live-root':
                        root = Path('/')  # must reject before reading any init file
                    result = self.invoke(shell, root)
                    self.assert_result(result, False)
                    self.assertEqual(sentinel.read_bytes(), OLD)

    def test_ineffective_or_failed_write_is_not_success(self):
        for name, shell in self.shells:
            for behavior in ('noop', 'raise'):
                with self.subTest(shell=name, behavior=behavior), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    target = root / 'init.z1.network.rc'
                    target.write_bytes(OLD)
                    bindir = root / 'bin'
                    bindir.mkdir()
                    wrapper = bindir / 'python3'
                    action = ('pass' if behavior == 'noop' else "raise OSError('injected replace failure')")
                    wrapper.write_text('#!' + sys.executable + '\n'
                        'import os, sys\n'
                        'def fake_replace(source, target):\n    ' + action + '\n'
                        'os.replace = fake_replace\nsys.argv = sys.argv[1:]\n'
                        'exec(compile(sys.stdin.read(), "<patch-test>", "exec"), '
                        '{"__name__": "__main__"})\n')
                    wrapper.chmod(0o755)
                    env = os.environ.copy()
                    env['PATH'] = str(bindir) + os.pathsep + env.get('PATH', '')
                    result = self.invoke(shell, root, env=env)
                    self.assert_result(result, False)
                    self.assertEqual(target.read_bytes(), OLD)
                    expected = b'write read-back mismatch' if behavior == 'noop' else b'injected replace failure'
                    self.assertIn(expected, result.stderr)
                    self.assertFalse(list(root.glob('.init.z1.network.rc.*')))

    def test_existing_rc_copied_to_offline_fixture_only(self):
        # Read public input only; invoke the patch exclusively on temporary copies.
        public = HERE.parents[1] / 'ramdisk_r4_merge/init.z1.network.rc'
        before = public.read_bytes()
        self.assertEqual(before.count(OLD), 1)
        self.check_case(before, before.replace(OLD, NEW))
        self.assertEqual(public.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
