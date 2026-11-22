#!/usr/bin/env python3
"""Capture ONLY the Z1 diagnostic gadget, never the CH340 adapter."""
import argparse
import datetime
import array
import fcntl
import os
from pathlib import Path
import re
import select
import termios
import time

ROOT = Path(__file__).resolve().parent.parent
CRASH_TRIGGER_TAIL = 8192
CRASH_TRIGGER_PATTERNS = (
    ('system_server', re.compile(
        rb"init: Service 'system_server' \(pid \d+\) received signal \d+")),
    ('zygote', re.compile(
        rb"init: Service 'zygote' \(pid \d+\) received signal \d+")),
    ('fatal', re.compile(
        rb"Fatal signal (?:6|11).{0,2048}(?:system_server|zygote)", re.DOTALL)),
)


def find_crash_trigger(previous, chunk):
    sample = (previous + chunk)[-CRASH_TRIGGER_TAIL:]
    for label, pattern in CRASH_TRIGGER_PATTERNS:
        if pattern.search(sample):
            return label
    return None


def identify(expected=('0e8d', '2006', 'Z1DIAG20261001')):
    for node in sorted(Path('/sys/class/tty').glob('ttyACM*')):
        device = (node / 'device').resolve()
        for parent in (device, *device.parents):
            try:
                vendor = (parent / 'idVendor').read_text().strip()
                product = (parent / 'idProduct').read_text().strip()
                serial = (parent / 'serial').read_text().strip()
            except OSError:
                continue
            if (vendor.lower(), product.lower(), serial) == expected:
                return Path('/dev') / node.name
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--wait', type=int, default=120, help='seconds to wait for enumeration')
    parser.add_argument('--tombstones', action='store_true',
                        help='request bounded read-only tombstone export on the first detected crash')
    parser.add_argument('--tombstones-at', type=float, metavar='SECONDS',
                        help='fallback tombstone request time after attach (requires --tombstones)')
    parser.add_argument('--status', action='store_true',
                        help='request bounded read-only boot/service/storage status every 60s (M6+)')
    parser.add_argument('--stock', action='store_true', help='capture exact stock S1 legacy ACM identity')
    args = parser.parse_args()
    if args.seconds <= 0 or args.wait < 0:
        parser.error('seconds must be positive and wait must be nonnegative')
    if args.tombstones_at is not None and not args.tombstones:
        parser.error('--tombstones-at requires --tombstones')
    if args.tombstones_at is not None and not 0 <= args.tombstones_at < args.seconds:
        parser.error('--tombstones-at must be between zero and --seconds')
    deadline = time.monotonic() + args.wait
    expected = ('17ef', '7439', 'Z1STOCK20261003') if args.stock else ('0e8d', '2006', 'Z1DIAG20261001')
    print(f'Waiting for Z1 {expected[0]}:{expected[1]} serial {expected[2]}; attach USB after kernel starts.', flush=True)
    while True:
        port = identify(expected)
        if port:
            break
        if time.monotonic() >= deadline:
            raise SystemExit('Z1 diagnostic gadget not found; CH340 was not opened.')
        time.sleep(.25)
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    saved = termios.tcgetattr(fd)
    try:
        attrs = saved.copy()
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = termios.B115200
        attrs[6] = saved[6].copy()
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        try:
            fcntl.ioctl(fd, termios.TIOCMBIS,
                        array.array('i', [termios.TIOCM_DTR | termios.TIOCM_RTS]))
        except OSError as exc:
            print(f'[acm] control-line setup unavailable: {exc}', flush=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = ROOT / 'mainline_recon' / f'z1_android9_acm_{stamp}.log'
        total = 0
        with path.open('xb') as out:
            # The gadget sends nothing until R; request replay only after our
            # raw tty and output file are ready. This command never executes a shell.
            if os.write(fd, b'R') != 1:
                raise OSError('could not request Z1 log replay')
            print(f'[acm] ready: port={port} log={path}', flush=True)
            deadline = time.monotonic() + args.seconds
            if args.tombstones_at is None:
                tombstones_at = time.monotonic() + min(60, args.seconds / 2)
            else:
                tombstones_at = time.monotonic() + args.tombstones_at
            scheduled_tombstones_sent = False
            tombstone_trigger_at = None
            tombstone_reason = None
            crash_tombstones_sent = False
            trigger_tail = b''
            replay_retry_at = time.monotonic() + 1
            replay_retries = 0
            status_at = time.monotonic()
            while time.monotonic() < deadline:
                if total == 0 and replay_retries < 10 and time.monotonic() >= replay_retry_at:
                    if os.write(fd, b'R') != 1:
                        raise OSError('could not retry Z1 log replay')
                    replay_retries += 1
                    replay_retry_at = time.monotonic() + 1
                if args.status and total > 0 and time.monotonic() >= status_at:
                    if os.write(fd, b'S') != 1:
                        raise OSError('could not request Z1 status')
                    status_at = time.monotonic() + 60
                    print('[acm] requested bounded read-only status', flush=True)
                now = time.monotonic()
                if args.tombstones and total > 0 and not scheduled_tombstones_sent and now >= tombstones_at:
                    if os.write(fd, b'T') != 1:
                        raise OSError('could not request Z1 tombstone export')
                    scheduled_tombstones_sent = True
                    print('[acm] requested read-only tombstone export on fallback schedule', flush=True)
                if (args.tombstones and total > 0 and not crash_tombstones_sent and
                        tombstone_trigger_at is not None and now >= tombstone_trigger_at):
                    if os.write(fd, b'T') != 1:
                        raise OSError('could not request Z1 tombstone export')
                    crash_tombstones_sent = True
                    print(f'[acm] requested read-only tombstone export after {tombstone_reason} crash',
                          flush=True)
                if not select.select([fd], [], [], min(.5, deadline-time.monotonic()))[0]:
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    print(f'[acm] disconnected/read error: {exc}', flush=True)
                    break
                if not chunk:
                    print('[acm] device closed the stream', flush=True)
                    break
                if args.tombstones and not crash_tombstones_sent and tombstone_trigger_at is None:
                    trigger_tail = (trigger_tail + chunk)[-CRASH_TRIGGER_TAIL:]
                    tombstone_reason = find_crash_trigger(b'', trigger_tail)
                    if tombstone_reason:
                        tombstone_trigger_at = time.monotonic() + 2
                        print(
                            f'[acm] detected {tombstone_reason} crash; '
                            'tombstone export queued',
                            flush=True,
                        )
                out.write(chunk)
                out.flush()
                total += len(chunk)
        print(f'[acm] finished: payload_bytes={total} log={path}', flush=True)
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, saved)
        except (OSError, termios.error):
            pass
        os.close(fd)


if __name__ == '__main__':
    main()
