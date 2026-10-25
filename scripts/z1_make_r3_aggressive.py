#!/usr/bin/env python3
"""Build the R3 aggressive bring-up boot image from the R1R2 merged baseline.

R3 layers, on top of the already device-verified R1R2 boot image:
  - z1_cst836u.ko polled touch driver + rewritten loader script (edt-ft5x06.ko
    is removed: the mainline driver cannot probe without an IRQ GPIO and the
    Z1 board has no evidenced touch IRQ/reset wiring).
  - Kernel wake_lock + settings tuning at boot_completed (Android suspend
    freezes userspace with no wakeup source and kills the USB ACM link).
  - 15 s kmsg heartbeat service.
  - Launch of the staged /system/bin/z1_linux7_radio_init.sh (CONNSYS Wi-Fi/BT
    transport bring-up; everything it needs already lives on the R9 system).
  - Composite ACM+ADB bootdiag helper (rebuilt static binary) with an
    ACM-only fallback if adbd's FunctionFS endpoint never appears.

The zImage, appended DTB (except linux,initrd-end), boot cmdline and all
other ramdisk records stay byte-identical to the flashed R1R2 baseline.
"""

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from z1_repair_r9_boot import (
    Boot, Record, boot_id, parse_boot, records, padded, patch_dtb, sha,
    update_ramdisk_records,
)
from z1_prepare_android9_ramdisk import INITRD_START
from z1_make_android9_bootimg import (
    MTK_HEADER_SIZE, BOOT_LIMIT, check_image, check_ramdisk, ADB_DEBUG_BLOCK,
)
from z1_make_stock_android9 import ramdisk_index

ROOT = Path(__file__).resolve().parent.parent
R1R2_SHA256 = "4299527db26c644cf4d9ec682fd54aacbb222594b793a14ca2a123ce6d77c5d7"

POST_FS_BLOCK = b"""
# R3: never suspend (no wakeup source on this DT; suspend kills USB ACM and
# freezes userspace behind the MCU panel's static GRAM frame).
on post-fs
    write /sys/power/wake_lock z1diag
    start z1_heartbeat
    exec_background u:r:init:s0 root root -- /system/bin/sh /system/bin/z1_linux7_radio_init.sh

service z1_heartbeat /system/bin/sh /z1_heartbeat.sh
    class core
    user root
    group root
    disabled
    seclabel u:r:init:s0
"""

HEARTBEAT_MARKER = b"z1_heartbeat"
RADIO_MARKER = b"z1_linux7_radio_init.sh"
WAKELOCK_MARKER = b"/sys/power/wake_lock"
PM_GUARD_EXEC = (
    b"    exec u:r:init:s0 root root -- /system/bin/sh /z1_pm_guard.sh\n"
)
TOUCH_EXEC = (
    b"    exec u:r:init:s0 root root -- /system/bin/sh /z1_touch_load.sh\n"
)

MODULE_NAME = b"z1_cst836u.ko"
VERMAGIC = b"7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8"


def encode_record(name, fields, data):
    """Re-encode a newc record the same way the repair packer does."""
    from z1_repair_r9_boot import encode
    return encode(name, fields, data)


def new_record(name, data, mode, inode):
    fields = [inode, stat.S_IFREG | mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, 0, 0]
    return Record(name, fields, data, encode_record(name, fields, data))


def patch_init_z1_rc(rc):
    if HEARTBEAT_MARKER in rc or RADIO_MARKER in rc or WAKELOCK_MARKER in rc:
        raise ValueError("baseline init.z1.rc already carries R3 additions")
    if b"service adbd " in rc:
        raise ValueError("baseline init.z1.rc already has an adbd service")

    # 1. Rewrite the touch exec line and add the pm guard right after it.
    if TOUCH_EXEC not in rc:
        raise ValueError("baseline init.z1.rc lacks the R1R2 touch exec line")
    rc = rc.replace(TOUCH_EXEC, TOUCH_EXEC + PM_GUARD_EXEC, 1)

    # 2. Append the post-fs wake_lock/heartbeat/radio block and the approved
    # composite-ADB block verbatim (check_ramdisk allows exactly this block).
    if not rc.endswith(b"\n"):
        rc += b"\n"
    return rc + POST_FS_BLOCK + ADB_DEBUG_BLOCK


def patch_default_prop(prop):
    # Replace in place rather than append: init must see exactly one value.
    if b"ro.adb.secure=0" in prop:
        raise ValueError("default.prop already has ro.adb.secure=0")
    if b"ro.adb.secure=1" not in prop:
        raise ValueError("baseline default.prop lacks ro.adb.secure=1 to replace")
    prop = prop.replace(b"ro.adb.secure=1", b"ro.adb.secure=0", 1)
    if b"ro.config.low_ram" in prop:
        raise ValueError("default.prop already sets ro.config.low_ram")
    if not prop.endswith(b"\n"):
        prop += b"\n"
    return prop + b"ro.config.low_ram=true\n"


def rebuild_ramdisk(body, module_path, bootdiag_path):
    before = records(gzip.decompress(body))
    index = ramdisk_index([(r.name, r.fields, r.data) for r in before])

    # Content updates only; ownership stays as packed for these two.
    updates = {}
    updates[b"init.z1.rc"] = patch_init_z1_rc(index["init.z1.rc"][2])
    updates[b"default.prop"] = patch_default_prop(index["default.prop"][2])
    body = update_ramdisk_records(body, updates)

    bootdiag = bootdiag_path.read_bytes()
    if bootdiag[:4] != b"\x7fELF" or b"ffs.adb" not in bootdiag or \
            b"falling back to ACM-only gadget" not in bootdiag:
        raise ValueError("composite bootdiag binary failed its allowlist checks")
    module = module_path.read_bytes()
    if VERMAGIC not in module:
        raise ValueError("z1_cst836u.ko vermagic does not match the R9 kernel")

    # Replace (not just update) the records whose content AND metadata change:
    # the R1R2 baseline shipped the helper and scripts owned by uid/gid 1000.
    listing = records(gzip.decompress(body))
    replacements = {}
    for name, data, mode in (
        (b"z1_bootdiag", bootdiag, 0o755),
        (b"z1_touch_load.sh", (ROOT / "stock/z1_touch_load.sh").read_bytes(), 0o755),
    ):
        if name not in [r.name for r in listing]:
            raise ValueError(f"baseline ramdisk lacks {name.decode()} to replace")
        replacements[name] = data

    additions = [
        (MODULE_NAME, module, 0o644),
        (b"z1_pm_guard.sh", (ROOT / "stock/z1_pm_guard.sh").read_bytes(), 0o755),
        (b"z1_heartbeat.sh", (ROOT / "stock/z1_heartbeat.sh").read_bytes(), 0o755),
    ]
    names = [r.name for r in listing]
    for name, _, _ in additions:
        if name in names:
            raise ValueError(f"baseline ramdisk already contains {name.decode()}")
    if b"edt-ft5x06.ko" not in names:
        raise ValueError("baseline ramdisk lacks edt-ft5x06.ko to remove")

    inode = max(r.fields[0] for r in listing)
    out = []
    for record in listing:
        if record.name == b"edt-ft5x06.ko":
            continue
        if record.name in replacements:
            inode += 1
            out.append(new_record(record.name, replacements[record.name], 0o755, inode).raw)
            continue
        if record.name == b"TRAILER!!!":
            for name, data, mode in additions:
                inode += 1
                out.append(new_record(name, data, mode, inode).raw)
        out.append(record.raw)
    raw = b"".join(out)
    raw += bytes(-len(raw) % 512)
    return gzip.compress(raw, compresslevel=9, mtime=0), additions


def build(base: Boot, module_path, bootdiag_path, directory):
    body, additions = rebuild_ramdisk(base.ramdisk[MTK_HEADER_SIZE:], module_path, bootdiag_path)
    dtb, end = patch_dtb(base.dtb, len(body), directory)

    kernel_header = bytearray(base.kernel[:MTK_HEADER_SIZE])
    struct.pack_into("<I", kernel_header, 4, len(base.zimage) + len(dtb))
    kernel = bytes(kernel_header) + base.zimage + dtb

    rd_header = bytearray(base.ramdisk[:MTK_HEADER_SIZE])
    struct.pack_into("<I", rd_header, 4, len(body))
    ramdisk = bytes(rd_header) + body

    header = bytearray(base.data[:base.page])
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 16, len(ramdisk))
    header[576:608] = boot_id(kernel, ramdisk)
    image = bytes(header) + padded(kernel, base.page) + padded(ramdisk, base.page)

    if len(image) > BOOT_LIMIT:
        raise ValueError(f"candidate exceeds the {BOOT_LIMIT}-byte boot partition")

    check_image(image, base.zimage + dtb, ramdisk, base.cmdline)
    rd_path = directory / "ramdisk-mtk.img"
    rd_path.write_bytes(ramdisk)
    check_ramdisk(rd_path, require_android_logging=True, allow_adb=True)
    final = parse_boot(image)
    if final.zimage != base.zimage or final.cmdline != base.cmdline or final.initrd_end != end:
        raise ValueError("final readback differs from the baseline")

    verify = ramdisk_index([(r.name, r.fields, r.data) for r in records(gzip.decompress(final.ramdisk[MTK_HEADER_SIZE:]))])
    rc = verify["init.z1.rc"][2]
    for marker in (HEARTBEAT_MARKER, RADIO_MARKER, WAKELOCK_MARKER,
                   b"z1_pm_guard.sh", b"functionfs"):
        if marker not in rc:
            raise ValueError(f"init.z1.rc readback missing {marker.decode()}")
    if b"z1_cst836u.ko" not in verify["z1_touch_load.sh"][2]:
        raise ValueError("z1_touch_load.sh readback does not load the polled module")
    if "edt-ft5x06.ko" in verify:
        raise ValueError("superseded edt-ft5x06.ko still present")
    for key in ("z1_cst836u.ko", "z1_pm_guard.sh", "z1_heartbeat.sh"):
        if key not in verify:
            raise ValueError(f"ramdisk readback missing {key}")
    if VERMAGIC not in verify["z1_cst836u.ko"][2]:
        raise ValueError("module vermagic missing from final ramdisk")

    return image, ramdisk, dtb, end, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="R1R2 merged boot image to layer on")
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--bootdiag", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    data = args.input.read_bytes()
    if sha(data) != R1R2_SHA256:
        raise ValueError("input is not the device-verified R1R2 merged baseline")

    base = parse_boot(data)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".z1-r3-", dir=args.output_dir) as temp:
        work = Path(temp)
        image, ramdisk, dtb, end, _ = build(base, args.module, args.bootdiag, work)

    outputs = {"boot.img": image, "ramdisk-mtk.img": ramdisk, "mt6572-z1.dtb": dtb}
    for name, blob in outputs.items():
        (args.output_dir / name).write_bytes(blob)

    manifest = {
        "status": "HOST_PACKAGE_PASS_NOT_DEVICE_VERIFIED",
        "input_baseline": {"name": args.input.name, "sha256": sha(data)},
        "outputs": {n: {"bytes": len(b), "sha256": sha(b)} for n, b in outputs.items()},
        "new_initrd_end": f"{end:#x}",
        "initrd_start": f"{INITRD_START:#x}",
        "ramdisk_body_bytes": len(ramdisk) - MTK_HEADER_SIZE,
        "module": {"name": "z1_cst836u.ko", "sha256": sha(args.module.read_bytes()),
                   "driver": "polled CST836U (FT6206 protocol) @0x15, no IRQ dependency"},
        "bootdiag": {"sha256": sha(args.bootdiag.read_bytes()),
                     "composite": True, "acm_only_fallback": True},
        "ramdisk_delta": [
            "init.z1.rc: +wake_lock/heartbeat/radio-init/adbd blocks, +pm_guard exec",
            "default.prop: +ro.adb.secure=0 +ro.config.low_ram=true",
            "z1_bootdiag: composite ACM+ADB rebuild",
            "z1_touch_load.sh: rewritten for the polled driver",
            "+z1_cst836u.ko +z1_pm_guard.sh +z1_heartbeat.sh",
            "-edt-ft5x06.ko (superseded: cannot probe without IRQ GPIO)",
        ],
        "device_boot_verified": False,
        "flashing_performed": False,
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["outputs"], indent=2))
    print(f"new initrd-end {end:#x}, boot image {len(image)} bytes")


if __name__ == "__main__":
    main()
