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
  - R3d --extra-module: optional additional out-of-tree modules (first user:
    z1_gpio_hunt, runtime GPIO scan for the touch I2C hunt) packed root:root
    0644 before the cpio TRAILER; --dtb replaces the appended DTB wholesale
    (R3d: corrected bootargs) with initrd-end still recomputed.

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
# R3d: display takeover also runs early (idempotent, background) so the fbcon
# '_' cursor is gone within seconds of /system mounting instead of surviving
# until boot_completed minutes later; the boot_completed exec remains as the
# second, verifying pass.
on post-fs
    write /sys/power/wake_lock z1diag
    start z1_heartbeat
    start z1_logpersist
    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_display_takeover.sh
    exec_background u:r:init:s0 root root -- /system/bin/sh /system/bin/z1_linux7_radio_init.sh
    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_radio_finish.sh
    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_touch_load.sh

service z1_heartbeat /system/bin/sh /z1_heartbeat.sh
    class core
    user root
    group root
    disabled
    seclabel u:r:init:s0

service z1_logpersist /system/bin/sh /z1_logpersist.sh
    class core
    user root
    group root system sdcard_rw
    disabled
    seclabel u:r:init:s0
"""

HEARTBEAT_MARKER = b"z1_heartbeat"
RADIO_MARKER = b"z1_linux7_radio_init.sh"
WAKELOCK_MARKER = b"/sys/power/wake_lock"
PM_GUARD_EXEC = (
    b"    exec_background u:r:init:s0 root root -- /system/bin/sh /z1_pm_guard.sh\n"
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


def patch_init_z1_rc(rc, with_adb):
    if HEARTBEAT_MARKER in rc or RADIO_MARKER in rc or WAKELOCK_MARKER in rc:
        raise ValueError("baseline init.z1.rc already carries R3 additions")
    if b"service adbd " in rc and with_adb:
        raise ValueError("baseline init.z1.rc already has an adbd service")

    # 1. Rewrite the touch exec line and add the pm guard right after it.
    #    exec_background from R3c on: a blocking exec in a boot_completed
    #    action stalls the init main loop (service restarts, property sets)
    #    for the whole script runtime.
    if TOUCH_EXEC not in rc:
        raise ValueError("baseline init.z1.rc lacks the R1R2 touch exec line")
    rc = rc.replace(TOUCH_EXEC, PM_GUARD_EXEC, 1)

    # 2. Append the post-fs block; ADB block only in composite mode (R3
    # device evidence: composite never bound and left the board USB-blind).
    if not rc.endswith(b"\n"):
        rc += b"\n"
    rc = rc + POST_FS_BLOCK
    if with_adb:
        rc = rc + ADB_DEBUG_BLOCK
    return rc


def patch_default_prop(prop, with_low_ram):
    # Replace in place rather than append: init must see exactly one value.
    if b"ro.adb.secure=0" in prop:
        raise ValueError("default.prop already has ro.adb.secure=0")
    if b"ro.adb.secure=1" not in prop:
        raise ValueError("baseline default.prop lacks ro.adb.secure=1 to replace")
    prop = prop.replace(b"ro.adb.secure=1", b"ro.adb.secure=0", 1)

    # R3b device evidence: a restarted zygote aborted with an EMPTY
    # ro.product.cpu.abilist32 even though /system/build.prop carries the
    # full set. Setting them here (earliest property source) removes the
    # whole failure class: app_process can never see them unset again.
    for line in (b"ro.product.cpu.abi=armeabi-v7a",
                 b"ro.product.cpu.abi2=armeabi",
                 b"ro.product.cpu.abilist=armeabi-v7a,armeabi",
                 b"ro.product.cpu.abilist32=armeabi-v7a,armeabi"):
        if line + b"\n" in prop:
            raise ValueError(f"default.prop already contains {line.decode()}")
    prop += (b"ro.product.cpu.abi=armeabi-v7a\n"
             b"ro.product.cpu.abi2=armeabi\n"
             b"ro.product.cpu.abilist=armeabi-v7a,armeabi\n"
             b"ro.product.cpu.abilist32=armeabi-v7a,armeabi\n")

    # Cap the dex2oat heap: 512m on 1 GiB RAM invites memory-pressure kills
    # during background dexopt (crash-restart window around minute 6).
    prop = prop.replace(b"dalvik.vm.dex2oat-Xmx=512m", b"dalvik.vm.dex2oat-Xmx=256m", 1)

    if not with_low_ram:
        return prop
    if b"ro.config.low_ram" in prop:
        raise ValueError("default.prop already sets ro.config.low_ram")
    if not prop.endswith(b"\n"):
        prop += b"\n"
    return prop + b"ro.config.low_ram=true\n"


def rebuild_ramdisk(body, module_path, bootdiag_path, with_adb, with_low_ram,
                    extra_modules=()):
    before = records(gzip.decompress(body))
    index = ramdisk_index([(r.name, r.fields, r.data) for r in before])

    # Content updates only; ownership stays as packed for these two.
    updates = {}
    updates[b"init.z1.rc"] = patch_init_z1_rc(index["init.z1.rc"][2], with_adb)
    updates[b"default.prop"] = patch_default_prop(index["default.prop"][2], with_low_ram)
    body = update_ramdisk_records(body, updates)

    bootdiag = bootdiag_path.read_bytes()
    if bootdiag[:4] != b"\x7fELF" or b"falling back to ACM-only gadget" not in bootdiag:
        raise ValueError("bootdiag binary failed its allowlist checks")
    if with_adb and b"ACM and ADB bound" not in bootdiag:
        raise ValueError("composite bootdiag lacks the ADB-bound marker")
    module = module_path.read_bytes()
    if VERMAGIC not in module:
        raise ValueError("z1_cst836u.ko vermagic does not match the R9 kernel")

    # R3d --extra-module: additional out-of-tree modules (e.g. z1_gpio_hunt)
    # packed the same way as the touch module: root:root 0644 via new_record,
    # inserted before TRAILER!!!, vermagic asserted against the R9 kernel.
    extra_blobs = []
    seen = {MODULE_NAME}
    for path in extra_modules:
        blob = path.read_bytes()
        if VERMAGIC not in blob:
            raise ValueError(f"{path.name} vermagic does not match the R9 kernel")
        if path.name.encode() in seen:
            raise ValueError(f"duplicate extra module name: {path.name}")
        seen.add(path.name.encode())
        extra_blobs.append((path.name.encode(), blob))

    # Replace (not just update) the records whose content AND metadata change:
    # the R1R2 baseline shipped the helper and scripts owned by uid/gid 1000.
    listing = records(gzip.decompress(body))
    replacements = {
        b"z1_bootdiag": bootdiag,
        b"z1_touch_load.sh": (ROOT / "stock/z1_touch_load.sh").read_bytes(),
    }
    for name in replacements:
        if name not in [r.name for r in listing]:
            raise ValueError(f"baseline ramdisk lacks {name.decode()} to replace")

    additions = [
        (MODULE_NAME, module, 0o644),
        (b"z1_pm_guard.sh", (ROOT / "stock/z1_pm_guard.sh").read_bytes(), 0o755),
        (b"z1_heartbeat.sh", (ROOT / "stock/z1_heartbeat.sh").read_bytes(), 0o755),
        (b"z1_logpersist.sh", (ROOT / "stock/z1_logpersist.sh").read_bytes(), 0o755),
        (b"z1_radio_finish.sh", (ROOT / "stock/z1_radio_finish.sh").read_bytes(), 0o755),
    ]
    # Extra modules join the additions list (all landed root:root 0644/0755 by
    # new_record, all emitted before the TRAILER!!! record).
    additions += [(name, blob, 0o644) for name, blob in extra_blobs]
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


def build(base: Boot, module_path, bootdiag_path, directory, with_adb, with_low_ram, dtb_override=None, extra_modules=()):
    body, additions = rebuild_ramdisk(base.ramdisk[MTK_HEADER_SIZE:], module_path,
                                      bootdiag_path, with_adb, with_low_ram,
                                      extra_modules=extra_modules)
    # dtb_override arrives as a Path (argparse); patch_dtb() takes bytes.
    # R3d fix: the feature had never been exercised and crashed on
    # write_bytes(Path) before this read.
    source_dtb = dtb_override.read_bytes() if dtb_override is not None else base.dtb
    dtb, end = patch_dtb(source_dtb, len(body), directory)

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
    check_ramdisk(rd_path, require_android_logging=True, allow_adb=with_adb)
    final = parse_boot(image)
    if final.zimage != base.zimage or final.cmdline != base.cmdline or final.initrd_end != end:
        raise ValueError("final readback differs from the baseline")

    verify = ramdisk_index([(r.name, r.fields, r.data) for r in records(gzip.decompress(final.ramdisk[MTK_HEADER_SIZE:]))])
    rc = verify["init.z1.rc"][2]
    markers = [HEARTBEAT_MARKER, RADIO_MARKER, WAKELOCK_MARKER, b"z1_pm_guard.sh",
               b"z1_logpersist.sh", b"z1_radio_finish.sh",
               b"exec_background u:r:init:s0 root root -- /system/bin/sh /z1_touch_load.sh"]
    if with_adb:
        markers.append(b"functionfs")
    for marker in markers:
        if marker not in rc:
            raise ValueError(f"init.z1.rc readback missing {marker.decode()}")
    if b"z1_cst836u.ko" not in verify["z1_touch_load.sh"][2]:
        raise ValueError("z1_touch_load.sh readback does not load the polled module")
    if "edt-ft5x06.ko" in verify:
        raise ValueError("superseded edt-ft5x06.ko still present")
    for key in ("z1_cst836u.ko", "z1_pm_guard.sh", "z1_heartbeat.sh",
                "z1_logpersist.sh", "z1_radio_finish.sh"):
        if key not in verify:
            raise ValueError(f"ramdisk readback missing {key}")
    if VERMAGIC not in verify["z1_cst836u.ko"][2]:
        raise ValueError("module vermagic missing from final ramdisk")
    for path in extra_modules:
        key = path.name
        if key not in verify:
            raise ValueError(f"ramdisk readback missing extra module {key}")
        if VERMAGIC not in verify[key][2]:
            raise ValueError(f"extra module {key} vermagic missing from final ramdisk")
    if (b"ro.config.low_ram=true" in verify["default.prop"][2]) != with_low_ram:
        raise ValueError("default.prop low_ram state does not match the requested mode")
    if b"ro.product.cpu.abilist32=armeabi-v7a,armeabi\n" not in verify["default.prop"][2]:
        raise ValueError("default.prop abilist insurance missing")
    if b"dex2oat-Xmx=512m" in verify["default.prop"][2]:
        raise ValueError("dex2oat Xmx still 512m")

    return image, ramdisk, dtb, end, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path,
                        help="R1R2 merged boot image to layer on")
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--bootdiag", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--composite-adb", action="store_true",
                        help="bind the ACM+ADB composite gadget (R3 default; "
                             "R3 device evidence says composite never bound, "
                             "so plain builds ship ACM-only)")
    parser.add_argument("--low-ram", action="store_true",
                        help="set ro.config.low_ram=true (R3 device suspect "
                             "for the boot_completed/display regression)")
    parser.add_argument("--dtb", type=Path, default=None,
                        help="replace the appended DTB wholesale (initrd-end "
                             "still patched); its /chosen bootargs must equal "
                             "the baseline cmdline byte for byte")
    parser.add_argument("--extra-module", type=Path, action="append", default=[],
                        metavar="KO",
                        help="additional out-of-tree module to pack into the "
                             "ramdisk as root:root 0644 before the TRAILER "
                             "(repeatable); its vermagic must match the R9 "
                             "kernel (R3d: z1_gpio_hunt)")
    args = parser.parse_args()

    data = args.input.read_bytes()
    if sha(data) != R1R2_SHA256:
        raise ValueError("input is not the device-verified R1R2 merged baseline")

    base = parse_boot(data)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".z1-r3-", dir=args.output_dir) as temp:
        work = Path(temp)
        image, ramdisk, dtb, end, _ = build(base, args.module, args.bootdiag, work,
                                            with_adb=args.composite_adb,
                                            with_low_ram=args.low_ram,
                                            dtb_override=args.dtb,
                                            extra_modules=args.extra_module)

    outputs = {"boot.img": image, "ramdisk-mtk.img": ramdisk, "mt6572-z1.dtb": dtb}
    for name, blob in outputs.items():
        (args.output_dir / name).write_bytes(blob)

    manifest = {
        "status": "HOST_PACKAGE_PASS_NOT_DEVICE_VERIFIED",
        "input_baseline": {"name": args.input.name, "sha256": sha(data)},
        "dtb_override": {"sha256": sha(args.dtb.read_bytes())} if args.dtb else None,
        "outputs": {n: {"bytes": len(b), "sha256": sha(b)} for n, b in outputs.items()},
        "new_initrd_end": f"{end:#x}",
        "initrd_start": f"{INITRD_START:#x}",
        "ramdisk_body_bytes": len(ramdisk) - MTK_HEADER_SIZE,
        "module": {"name": "z1_cst836u.ko", "sha256": sha(args.module.read_bytes()),
                   "driver": "polled CST836U (FT6206 protocol) @0x15, no IRQ dependency"},
        "extra_modules": [{"name": p.name, "sha256": sha(p.read_bytes())}
                          for p in args.extra_module],
        "bootdiag": {"sha256": sha(args.bootdiag.read_bytes()),
                     "composite": args.composite_adb,
                     "acm_only": not args.composite_adb},
        "ramdisk_delta": [
            "init.z1.rc: +wake_lock/heartbeat/logpersist/radio-init blocks, "
            "+early touch exec (post-fs) +pm_guard exec"
            + (", +approved ADB block" if args.composite_adb else ""),
            "default.prop: ro.adb.secure=1->0"
            + (" +ro.config.low_ram=true" if args.low_ram else ""),
            "z1_bootdiag: rebuild"
            + (" (composite ACM+ADB)" if args.composite_adb else " (ACM-only, R1R2-proven)"),
            "z1_touch_load.sh: rewritten for the polled driver, retrying loader",
            "+z1_cst836u.ko +z1_pm_guard.sh +z1_heartbeat.sh +z1_logpersist.sh",
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
