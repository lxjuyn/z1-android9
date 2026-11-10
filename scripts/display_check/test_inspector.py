#!/usr/bin/env python3
"""Host-only tests; all fake sysfs files live inside this owned directory.
Run: PYTHONDONTWRITEBYTECODE=1 python3 test_inspector.py -v
Also: DISPLAY_CHECK_SHELL='/bin/busybox ash' ... (default /bin/dash)
No test accesses a live device, /sys, Android settings, or a serial port.
"""
import hashlib
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'inspect_backlight.sh'
SHELL = shlex.split(os.environ.get('DISPLAY_CHECK_SHELL', '/bin/dash'))


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.fixture_', dir=HERE)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'root with spaces'
        self.root.mkdir()

    def provider(self, cls='leds', name='lcd-backlight', **attrs):
        p = self.root / 'sys/class' / cls / name
        p.mkdir(parents=True)
        values = {'brightness': '3\n', 'max_brightness': '6\n'}
        values.update(attrs)
        for key, value in values.items():
            if value is not None:
                (p / key).write_text(value)
        return p

    def snapshot(self):
        result = {}
        for p in self.base.rglob('*'):
            s = p.lstat()
            if p.is_symlink():
                payload = os.readlink(p)
            elif stat.S_ISREG(s.st_mode) and s.st_mode & 0o444:
                payload = hashlib.sha256(p.read_bytes()).hexdigest()
            else:
                payload = None
            result[str(p.relative_to(self.base))] = (
                s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns, payload)
        return result

    def run_inspector(self, expected=0, args=None, env=None, bootstrap=None):
        before = self.snapshot()
        result = subprocess.run(
            SHELL + (['-c', bootstrap, 'fault-injection', str(SCRIPT)] if bootstrap else [str(SCRIPT)])
            + (args if args is not None else ['--root', str(self.root)]),
            capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        self.assertEqual(before, self.snapshot(), 'inspector mutated fixture metadata/content')
        if expected != 2:
            self.assertEqual(result.stderr, '')
        return result.stdout

    def test_led_no_actual_is_explicitly_unknown(self):
        self.provider(trigger='none [backlight] timer\n')
        text = self.run_inspector()
        for field in ['brightness=3', 'range_max=6', 'range_check=valid',
                      'actual_brightness_status=missing', 'selected_trigger=backlight',
                      'physical_backlight=unverified', 'panel_image=unverified']:
            self.assertIn(field, text)

    def test_actual_difference_is_not_automatically_a_fault(self):
        self.provider('backlight', 'panel0', actual_brightness='0\n', bl_power='4\n')
        text = self.run_inspector()
        self.assertIn('current_actual_relation=different_not_necessarily_fault', text)
        self.assertIn('power_observation=powerdown_requested', text)

    def test_zero_does_not_claim_visible_off(self):
        self.provider(brightness='0\n')
        text = self.run_inspector()
        self.assertIn('brightness_observation=zero', text)
        self.assertIn('physical_backlight=unverified', text)

    def test_multiple_providers_are_not_selected(self):
        self.provider()
        self.provider('backlight', 'panel0', max_brightness='255\n')
        text = self.run_inspector()
        self.assertIn('provider_count=2', text)
        self.assertIn('selection=multiple_candidates_no_auto_selection', text)

    def test_empty_root_is_not_no_hardware_claim(self):
        text = self.run_inspector(3)
        self.assertIn('none_no_hardware_conclusion', text)

    def test_other_led_names_are_not_invented_as_panel_providers(self):
        self.provider(name='notification')
        text = self.run_inspector(3)
        self.assertNotIn('name=notification', text)

    def test_required_root_and_unknown_options(self):
        for args in [[], ['--root'], ['--root', '/nonexistent-display-check-root'],
                     ['--root', str(self.root), '--write'], ['--write', '6'], ['--help', 'x']]:
            with self.subTest(args=args):
                self.run_inspector(2, args)

    def test_plan_and_help_do_not_touch_sysfs(self):
        self.assertIn('Read-only', self.run_inspector(args=['--help']))
        plan = self.run_inspector(args=['--plan'])
        self.assertIn('not executed', plan)
        self.assertIn('selected LED trigger', plan)
        self.assertIn('verified wake source', plan)

    def test_missing_required_attributes(self):
        self.provider(brightness=None, max_brightness=None)
        text = self.run_inspector(4)
        self.assertIn('brightness_status=missing', text)
        self.assertIn('range_check=unknown', text)

    def test_invalid_numeric_data_is_not_zero_or_echoed(self):
        p = self.provider()
        for value in ['', '-1', '1.5', 'private_marker', '3\n9\n', ' 3',
                      '2147483648', '99999999999999999999']:
            with self.subTest(value=value):
                (p / 'brightness').write_text(value)
                text = self.run_inspector(4)
                self.assertIn('brightness_status=invalid_number', text)
                self.assertIn('brightness=unknown', text)
                self.assertNotIn('private_marker', text)

    def test_leading_zero_is_decimal(self):
        self.provider(brightness='08\n', max_brightness='010\n', actual_brightness='08\n')
        text = self.run_inspector()
        self.assertIn('brightness=8\n', text)
        self.assertIn('max_brightness=10\n', text)
        self.assertIn('current_actual_relation=equal', text)

    def test_large_but_valid_integer(self):
        self.provider(brightness='2147483647\n', max_brightness='2147483647\n')
        self.assertIn('range_check=valid', self.run_inspector())

    def test_maximum_zero_invalid(self):
        self.provider(brightness='0\n', max_brightness='0\n')
        self.assertIn('range_check=invalid', self.run_inspector(4))

    def test_current_outside_range(self):
        self.provider(brightness='7\n')
        self.assertIn('range_check=invalid', self.run_inspector(4))

    def test_actual_outside_range(self):
        self.provider(actual_brightness='7\n')
        self.assertIn('actual_range_check=invalid', self.run_inspector(4))

    def test_power_codes(self):
        p = self.provider('backlight', 'panel')
        for value, result, rc in [('0', 'unblank_requested', 0),
                                  ('1', 'other_fb_blank_code', 0),
                                  ('4', 'powerdown_requested', 0),
                                  ('5', 'unknown_code', 4)]:
            (p / 'bl_power').write_text(value)
            with self.subTest(value=value):
                self.assertIn('power_observation=' + result, self.run_inspector(rc))

    def test_class_symlink_relative_to_device_tree(self):
        p = self.provider()
        target = self.root / 'sys/devices/platform/test-led'
        target.parent.mkdir(parents=True)
        p.rename(target)
        p.symlink_to('../../devices/platform/test-led')
        self.assertIn('provider_status=resolved', self.run_inspector())

    def test_relative_root(self):
        self.provider()
        text = self.run_inspector(args=['--root', os.path.relpath(self.root)])
        self.assertIn('provider_count=1', text)

    def test_provider_symlink_escape_rejected(self):
        p = self.root / 'sys/class/backlight/escape'
        p.parent.mkdir(parents=True)
        p.symlink_to(self.base)
        self.assertIn('provider_status=unresolved_or_unsafe', self.run_inspector(4))

    def test_class_symlink_escape_rejected(self):
        p = self.root / 'sys/class/backlight'
        p.parent.mkdir(parents=True)
        p.symlink_to(self.base)
        self.assertIn('class_backlight_status=', self.run_inspector(4))

    def test_attribute_symlink_escape_rejected_without_leaking_content(self):
        p = self.provider()
        outside = self.base / 'private_value'
        outside.write_text('private_marker')
        (p / 'brightness').unlink()
        (p / 'brightness').symlink_to(outside)
        text = self.run_inspector(4)
        self.assertIn('brightness_status=unsafe_path', text)
        self.assertNotIn('private_marker', text)

    def test_internal_attribute_symlink_supported(self):
        p = self.provider()
        (p / 'brightness').rename(p / 'value')
        (p / 'brightness').symlink_to('value')
        self.assertIn('brightness=3', self.run_inspector())

    def test_dangling_provider_link_fails_closed(self):
        p = self.root / 'sys/class/backlight/missing'
        p.parent.mkdir(parents=True)
        p.symlink_to('not-present')
        self.assertIn('provider_status=unresolved_or_unsafe', self.run_inspector(4))

    def test_looping_provider_link_fails_closed(self):
        p = self.root / 'sys/class/backlight/loop'
        p.parent.mkdir(parents=True)
        p.symlink_to('loop')
        self.assertIn('provider_status=unresolved_or_unsafe', self.run_inspector(4))

    def test_fifo_attribute_never_opened(self):
        p = self.provider(brightness=None)
        os.mkfifo(p / 'brightness')
        self.assertIn('brightness_status=not_regular', self.run_inspector(4))

    def test_directory_attribute_rejected(self):
        p = self.provider(brightness=None)
        (p / 'brightness').mkdir()
        self.assertIn('brightness_status=not_regular', self.run_inspector(4))

    @unittest.skipIf(os.getuid() == 0, 'DAC unreadability requires unprivileged host user')
    def test_unreadable_attribute(self):
        p = self.provider()
        (p / 'brightness').chmod(0)
        self.assertIn('brightness_status=unreadable', self.run_inspector(4))

    def test_read_error_is_not_zero(self):
        self.provider()
        # Function injection also overrides BusyBox ash's built-in applets;
        # a fake PATH head is bypassed when CONFIG_FEATURE_SH_STANDALONE=y.
        text = self.run_inspector(4, bootstrap='head() { return 1; }; script=$1; shift; . "$script"')
        self.assertIn('brightness_status=read_error', text)

    def test_oversized_attribute_is_bounded(self):
        self.provider(brightness='1' * 20000)
        self.assertIn('brightness_status=too_long', self.run_inspector(4))

    def test_oversized_newline_attribute_is_bounded(self):
        self.provider(brightness='3' + chr(10) * 10000)
        self.assertIn('brightness_status=too_long', self.run_inspector(4))

    def test_extra_trailing_newline_invalid(self):
        self.provider(brightness='3' + chr(10) * 2)
        self.assertIn('brightness_status=invalid_number', self.run_inspector(4))

    def test_backlight_provider_type(self):
        self.provider('backlight', 'panel0', type='raw' + chr(10))
        self.assertIn('type=raw', self.run_inspector())

    def test_unknown_type_does_not_echo_raw_content(self):
        self.provider('backlight', 'panel0', type='private_marker')
        text = self.run_inspector(4)
        self.assertIn('type_status=unknown_type', text)
        self.assertNotIn('private_marker', text)

    @unittest.skipIf(os.getuid() == 0, 'DAC requires unprivileged host user')
    def test_unreadable_class_is_not_empty_success(self):
        p = self.root / 'sys/class/backlight'
        p.mkdir(parents=True)
        p.chmod(0)
        self.addCleanup(p.chmod, 0o755)
        self.assertIn('class_backlight_status=', self.run_inspector(4))

    def test_malformed_trigger(self):
        self.provider(trigger='private_marker')
        text = self.run_inspector(4)
        self.assertIn('trigger_status=invalid_trigger', text)
        self.assertNotIn('private_marker', text)

    def test_provider_name_cannot_inject_output_fields(self):
        self.provider('backlight', 'bad\npanel_image=working')
        text = self.run_inspector(4)
        self.assertIn('provider_status=invalid_name', text)
        self.assertNotIn('panel_image=working', text)

    def test_production_script_has_no_mutating_commands(self):
        # A simple additional guard, not a shell sandbox; runtime snapshots above
        # verify regular fixtures remain identical after every invocation.
        text = SCRIPT.read_text()
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            self.assertNotRegex(line, r'^\s*(settings|setprop|input|svc|mount|chmod|chown|tee|dd)\s')
            self.assertNotRegex(line, r'>>?\s*["\x27]?\$(root|provider|resolved|candidate)')


if __name__ == '__main__':
    unittest.main()
