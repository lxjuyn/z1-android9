#!/usr/bin/env python3
"""Offline power readiness inspector; never collect from a running device.
Usage: python3 inspect_power.py --snapshot /absolute/offline/directory
Exit 2 unsafe input; 3 observed blocker; 4 readiness unproven.
Passive evidence never certifies a successful suspend/resume.
"""
import argparse
import errno
import json
import os
import re
import stat
import sys

LIMIT = 65536
MAX_FILES = 128


class Snapshot:
    def __init__(self, root):
        if not os.path.isabs(root) or os.path.normpath(root) != root:
            raise ValueError('normalized absolute directory required')
        if root == '/' or any(root == x or root.startswith(x + '/') for x in ('/sys', '/proc', '/dev')):
            raise ValueError('live roots forbidden')
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        fd = os.open('/', flags)
        try:
            for component in root.strip('/').split('/'):
                nxt = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = nxt
            self.dev = os.fstat(fd).st_dev
            if self.dev in {os.stat(p).st_dev for p in ('/sys', '/proc', '/dev')}:
                raise ValueError('pseudo-filesystem forbidden')
        except Exception:
            os.close(fd)
            raise
        self.fd = fd
        self.reads = 0
        self.inventory = {}

    def close(self):
        os.close(self.fd)

    def read(self, relative):
        self.reads += 1
        if self.reads > MAX_FILES:
            raise ValueError('file budget exceeded')
        parts = relative.split('/')
        if any(p in ('', '.', '..') for p in parts):
            raise ValueError('unsafe path')
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                os.close(fd)
                fd = nxt
                if os.fstat(fd).st_dev != self.dev:
                    raise ValueError('cross-filesystem input rejected')
            # O_PATH acquires metadata only: even a device/FIFO is never opened for I/O.
            nxt = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_dev != self.dev:
                raise ValueError('ordinary same-filesystem files required')
            if st.st_size > LIMIT:
                raise ValueError('file too large')
            # Reopen the pinned, verified regular inode; pathname replacement cannot
            # redirect this read to a device. /proc here is only our own fd.
            read_fd = os.open('/proc/self/fd/' + str(fd), os.O_RDONLY | os.O_NONBLOCK)
            try:
                data = os.read(read_fd, LIMIT + 1)
            finally:
                os.close(read_fd)
            if len(data) > LIMIT:
                raise ValueError('read limit exceeded')
            text = data.decode('utf-8', errors='strict')
            if '\x00' in text:
                raise ValueError('binary input rejected')
            self.inventory[relative] = 'present'
            return text
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                self.inventory[relative] = 'missing'
                return None
            raise ValueError('unavailable or unsafe snapshot file') from None
        finally:
            os.close(fd)


def active_lines(text):
    return '\n'.join(x.split('#', 1)[0] for x in (text or '').splitlines())


def assess(s):
    findings = []
    def add(topic, status, detail, evidence):
        findings.append(dict(topic=topic, status=status, detail=detail, evidence=evidence))
    states = s.read('sys/power/state')
    mem = s.read('sys/power/mem_sleep')
    locks = s.read('sys/power/wake_lock')
    config = s.read('kernel.config')
    init = active_lines(s.read('init.z1.rc'))
    guard = active_lines(s.read('z1_pm_guard.sh'))
    logs = s.read('power_kernel.log')
    devices = s.read('proc/bus/input/devices')
    keylayout = active_lines(s.read('selected_pmic_keylayout.kl'))
    blockers = False
    if states is not None and states.strip():
        if any(t not in ('freeze', 'standby', 'mem', 'disk') for t in states.split()):
            raise ValueError('invalid power/state')
        add('suspend_interface', 'ADVERTISED', 'Interfaces exist; mem may select s2idle, not deep sleep.', ['sys/power/state'])
    else:
        add('suspend_interface', 'UNKNOWN', 'Missing or empty snapshot does not prove suspend unimplemented.', ['sys/power/state'])
    if mem is not None and mem.strip():
        tokens = mem.split()
        if any(not re.fullmatch(r'(?:s2idle|shallow|deep|\[(?:s2idle|shallow|deep)\])', t) for t in tokens):
            raise ValueError('invalid mem_sleep')
        if sum(t.startswith('[') for t in tokens) != 1:
            raise ValueError('exactly one current mode required')
        add('deep_sleep', 'ADVERTISED_UNTESTED' if 'deep' in [t.strip('[]') for t in tokens] else 'NOT_ADVERTISED', 'Advertised modes do not validate suspend/resume.', ['sys/power/mem_sleep'])
    else:
        add('deep_sleep', 'UNKNOWN', 'Need captured mem_sleep; config alone insufficient.', ['sys/power/mem_sleep'])
    if locks is not None and locks.split():
        blockers = True
        add('wake_locks', 'BLOCKED', 'Active userspace wake lock observed' + ('; z1diag present.' if 'z1diag' in locks.split() else '.'), ['sys/power/wake_lock'])
    else:
        add('wake_locks', 'UNKNOWN' if locks is None else 'NO_USER_LOCK_OBSERVED', 'Other kernel or framework wake sources may remain.', ['sys/power/wake_lock'])
    policy = bool(re.search(r'^\s*write\s+/sys/power/wake_lock\s+z1diag(?:\s|$)', init, re.M) or ('/sys/power/wake_lock' in guard and 'z1diag' in guard))
    add('guard_policy', 'CONFIGURED_BLOCK' if policy else 'UNKNOWN', 'Configured acquisition does not prove execution or an active lock.', ['init.z1.rc', 'z1_pm_guard.sh'])
    add('autosuspend', 'KERNEL_AUTOSLEEP_DISABLED' if config and '# CONFIG_PM_AUTOSLEEP is not set' in config else 'UNKNOWN', 'Android 9 libsuspend can use wakeup_count and state=mem without kernel autosleep.', ['kernel.config'])
    rtc_present = False
    for index in range(4):
        base = 'sys/class/rtc/rtc' + str(index)
        name = s.read(base + '/name')
        alarm = s.read(base + '/wakealarm')
        wake = s.read(base + '/device/power/wakeup')
        if all(t is None for t in (name, alarm, wake)):
            continue
        rtc_present = True
        if alarm is not None and alarm.strip() and not alarm.strip().isdigit():
            raise ValueError('invalid RTC alarm')
        if wake is not None and wake.strip() not in ('enabled', 'disabled', ''):
            raise ValueError('invalid RTC wake policy')
        add('rtc' + str(index) + '_alarm', 'ALARM_INTERFACE_PRESENT' if alarm is not None else 'UNKNOWN', 'Empty alarm means unarmed. Interface existence does not prove interrupt delivery.', [base + '/wakealarm'])
        add('rtc' + str(index) + '_wake', 'DISABLED' if wake is not None and wake.strip() == 'disabled' else 'UNKNOWN', 'Enabled policy does not prove parent EINT or actual resume.', [base + '/device/power/wakeup'])
    if not rtc_present:
        add('rtc', 'UNKNOWN', 'No evidence in rtc0..3; missing snapshot does not prove absence.', ['sys/class/rtc'])
    irq_failure = logs is not None and any(
        ('PMIC' in line or 'mt6397' in line or 'mt6323' in line or 'mtk-pmic' in line)
        and any(x in line for x in ('masking the parent IRQ', 'Failed to request alarm IRQ', 'Failed to request IRQ:', 'failed to register irq='))
        for line in logs.splitlines())
    blockers = blockers or irq_failure
    add('pmic_irq', 'BLOCKED' if irq_failure else 'UNKNOWN', 'Filtered failure signature; correlate with same boot and PMIC device.' if irq_failure else 'Need probe results and parent/child interrupt deltas.', ['power_kernel.log'])
    registered = devices is not None and bool(re.search(r'^N: Name="mtk-pmic-keys"$', devices, re.M))
    add('power_key_device', 'REGISTERED_UNTESTED' if registered else 'UNKNOWN', 'Registration does not prove KEY_POWER press/release or suspend wake.', ['proc/bus/input/devices'])
    mapping = bool(re.search(r'^\s*key\s+116\s+POWER(?:\s|$)', keylayout, re.M))
    add('power_key_android_mapping', 'MAPPING_PRESENT_UNTESTED' if mapping else 'UNKNOWN', 'Require actual InputReader selection and sanitized press/release evidence.', ['selected_pmic_keylayout.kl'])
    service = re.search(r'^\s*service\s+watchdogd\s+/sbin/watchdogd\s+(\d+)\s+(\d+)(?:\s|$)', init, re.M)
    detail = 'No device opened. Configuration does not prove hardware state.'
    if service:
        interval, margin = map(int, service.groups())
        detail += ' AOSP requested timeout=' + str(interval + margin) + 's; interval=' + str(interval) + 's; margin=' + str(margin) + 's. Driver may reject or adjust.'
    add('watchdog_config', 'CONFIGURED' if service else 'UNKNOWN', detail, ['init.z1.rc'])
    wd = 'sys/class/watchdog/watchdog0'
    state = s.read(wd + '/state')
    timeout = s.read(wd + '/timeout')
    nowayout = s.read(wd + '/nowayout')
    if state is not None and state.strip() not in ('active', 'inactive'):
        raise ValueError('invalid watchdog state')
    for v in (timeout, nowayout):
        if v is not None and not v.strip().isdigit():
            raise ValueError('invalid watchdog numeric value')
    if nowayout is not None and nowayout.strip() not in ('0', '1'):
        raise ValueError('invalid nowayout')
    add('watchdog_runtime', 'ACTIVE_HAZARD' if state and state.strip() == 'active' else 'UNKNOWN', 'User feeding freezes during suspend. Source callbacks stop/restart active WDT; actual driver and resume unverified. Missing sysfs may mean CONFIG_WATCHDOG_SYSFS=n.', [wd + '/state', wd + '/timeout', wd + '/nowayout'])
    return dict(schema_version=1, scope='offline_snapshot_only', readiness='BLOCKED' if blockers else 'UNKNOWN', hardware_tested=False, findings=findings, files_attempted=s.reads, bytes_per_file_limit=LIMIT, file_status=s.inventory, excluded=['live collection','wakeup_count read','device opens','sysfs writes','RTC programming','watchdog feeding','service changes','charging policy']), 3 if blockers else 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    snapshot = None
    try:
        snapshot = Snapshot(args.snapshot)
        report, code = assess(snapshot)
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({'error':'unsafe_or_invalid_snapshot','detail':type(exc).__name__,'readiness':'UNKNOWN'}))
        return 2
    finally:
        if snapshot is not None:
            snapshot.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
