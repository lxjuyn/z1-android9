#!/usr/bin/env python3
"""Route B: make MediaTek mnld tolerate a missing /sys/class/gpsdrv/gps/*.

Two 4-byte Thumb-2 patches, both in the open()-failure path:

  mnl_set_status()  @ 0x1a3c:  blx __errno  ->  movs r4,#0 ; b.n 0x1ac4
  mnl_write_attr()  @ 0x1b08:  blx __errno  ->  movs r4,#0 ; b.n 0x1bb0

Effect: if open("/sys/class/gpsdrv/gps/...", O_RDWR) fails, the helper returns
0 (success) instead of -errno, so mnl_init() continues.  Nothing else changes;
the sysfs nodes remain absent (mnld never reads them), and the real GPS power
still goes through WMT / /dev/stpgps.

Usage: ./patch_mnld.py <input-mnld> <output-mnld>
Exit status 0 on success, 2 if the expected original bytes are not present.
"""
import sys

PATCHES = [
    (0x1A3C, bytes.fromhex("fff766ee"), bytes.fromhex("002441e0"),
     "mnl_set_status open() failure -> return 0"),
    (0x1B08, bytes.fromhex("fff700ee"), bytes.fromhex("002451e0"),
     "mnl_write_attr open() failure -> return 0"),
]

def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    d = bytearray(open(src, "rb").read())
    for off, old, new, why in PATCHES:
        cur = bytes(d[off:off + 4])
        if cur != old:
            print("refusing: %04x is %s, expected %s (%s)" %
                  (off, cur.hex(), old.hex(), why), file=sys.stderr)
            return 2
        d[off:off + 4] = new
    open(dst, "wb").write(d)
    print("patched %s -> %s" % (src, dst))
    for off, _, new, why in PATCHES:
        print("  %04x = %s  %s" % (off, new.hex(), why))
    return 0

if __name__ == "__main__":
    sys.exit(main())
