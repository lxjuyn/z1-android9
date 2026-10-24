#!/usr/bin/env python3
"""Prepare and validate a boot-only display-takeover candidate from original R9.

The original zImage, boot command line and all non-allowlisted ramdisk records
are preserved. This tool never flashes or accesses a device.
"""

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile

# Keep sibling packer imports working under isolated host execution (python -I).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from z1_add_bootdiag import encode
from z1_make_android9_bootimg import (
    BOOT_LIMIT, MTK_HEADER_SIZE, check_boot_header_cmdline, check_image,
    check_ramdisk, dt_bootargs, dt_cell,
)
from z1_make_stock_android9 import ramdisk_index, ramdisk_key
from z1_prepare_android9_ramdisk import INITRD_START, check_newc

ROOT = Path(__file__).resolve().parent.parent
DT_MAGIC = b"\xd0\x0d\xfe\xed"
TAKEOVER_COMMAND = (
    b"    exec u:r:init:s0 root root -- "
    b"/system/bin/sh /z1_display_takeover.sh"
)
SCRIPT_NAMES = ("z1_display_takeover.sh", "z1_fbcon_takeover.sh")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def padded(data, page):
    return data + bytes(-len(data) % page)


def boot_id(kernel, ramdisk):
    digest = hashlib.sha1()
    for payload in (kernel, ramdisk, b""):
        digest.update(payload)
        digest.update(struct.pack("<I", len(payload)))
    return digest.digest() + bytes(12)


@dataclass(frozen=True)
class Boot:
    data: bytes
    page: int
    kernel: bytes
    ramdisk: bytes
    zimage: bytes
    dtb: bytes
    cmdline: str
    initrd_start: int
    initrd_end: int


@dataclass(frozen=True)
class Record:
    name: bytes
    fields: list
    data: bytes
    raw: bytes

    @property
    def key(self):
        return ramdisk_key(self.name)


def parse_boot(data):
    if len(data) < 2048 or data[:8] != b"ANDROID!":
        raise ValueError("input is not an Android v0 boot image")
    ks, ka, rs, ra, ss, sa, ta, page, ds = struct.unpack_from("<9I", data, 8)
    if page != 2048 or ss or ds:
        raise ValueError("expected R9 v0 layout with appended DTB and no second image")
    if (ka, ra, sa, ta) != (0x10008000, 0x11000000, 0x10F00000, 0x10000100):
        raise ValueError("unexpected R9 boot load addresses")
    ramdisk_offset = page + (ks + page - 1) // page * page
    image_end = ramdisk_offset + (rs + page - 1) // page * page
    if len(data) != image_end or ks <= MTK_HEADER_SIZE or rs <= MTK_HEADER_SIZE:
        raise ValueError("truncated boot image or unsupported trailing data")
    kernel = data[page:page + ks]
    ramdisk = data[ramdisk_offset:ramdisk_offset + rs]
    if (any(data[page + ks:ramdisk_offset]) or
            any(data[ramdisk_offset + rs:image_end])):
        raise ValueError("nonzero boot payload padding")
    if (kernel[:4] != b"\x88\x16\x88\x58" or
            kernel[8:14] != b"KERNEL" or
            struct.unpack_from("<I", kernel, 4)[0] != len(kernel) - MTK_HEADER_SIZE):
        raise ValueError("invalid MTK KERNEL header or body length")
    if (ramdisk[:4] != b"\x88\x16\x88\x58" or
            ramdisk[8:14] != b"ROOTFS" or
            struct.unpack_from("<I", ramdisk, 4)[0] != len(ramdisk) - MTK_HEADER_SIZE):
        raise ValueError("invalid MTK ROOTFS header or body length")
    if not ramdisk[MTK_HEADER_SIZE:].startswith(b"\x1f\x8b"):
        raise ValueError("R9 ramdisk body must be a gzip-compressed cpio")
    body = kernel[MTK_HEADER_SIZE:]
    dt_offset = body.rfind(DT_MAGIC)
    if dt_offset <= 0 or dt_offset + 8 > len(body):
        raise ValueError("appended DTB not found after the zImage payload")
    dt_size = struct.unpack_from(">I", body, dt_offset + 4)[0]
    if dt_size < 40 or dt_offset + dt_size != len(body):
        raise ValueError("appended DTB does not end at the MTK kernel body boundary")
    zimage, dtb = body[:dt_offset], body[dt_offset:]
    if boot_id(kernel, ramdisk) != data[576:608]:
        raise ValueError("input boot header ID does not match its original payloads")
    with tempfile.TemporaryDirectory(prefix="z1-r9-read-") as temp:
        dtb_path = Path(temp) / "original.dtb"
        dtb_path.write_bytes(dtb)
        cmdline = dt_bootargs(dtb_path)
        start = dt_cell(dtb_path, "linux,initrd-start")
        end = dt_cell(dtb_path, "linux,initrd-end")
    check_boot_header_cmdline(data, cmdline)
    if start != INITRD_START or end != start + len(ramdisk) - MTK_HEADER_SIZE:
        raise ValueError("original DT initrd range does not match its ROOTFS body")
    return Boot(data, page, kernel, ramdisk, zimage, dtb, cmdline, start, end)


def records(archive):
    """Retain original newc records byte-for-byte unless explicitly changed."""
    check_newc(archive)
    result = []
    offset = 0
    while True:
        fields = [int(archive[p:p + 8], 16) for p in range(offset + 6, offset + 110, 8)]
        name_start = offset + 110
        name_end = name_start + fields[11]
        data_start = (name_end + 3) & ~3
        data_end = data_start + fields[6]
        next_offset = (data_end + 3) & ~3
        result.append(Record(archive[name_start:name_end - 1], fields,
                             archive[data_start:data_end], archive[offset:next_offset]))
        offset = next_offset
        if result[-1].name == b"TRAILER!!!":
            return result


def add_takeover_command(rc):
    if b"/z1_display_takeover.sh" in rc or b"/z1_fbcon_takeover.sh" in rc:
        raise ValueError("input is not pristine R9: display takeover is already referenced")
    lines = rc.splitlines(keepends=True)
    matches = [i for i, line in enumerate(lines)
               if re.match(rb"^on[ \t]+post-fs-data[ \t]*\r?\n$", line)]
    if not matches:
        raise ValueError("init.z1.rc has no literal 'on post-fs-data' action")
    start = matches[0]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(rb"^(?:on|service)[ \t]+", lines[i]):
            end = i
            break
    insertion = end
    while insertion > start + 1 and not lines[insertion - 1].strip():
        insertion -= 1
    newline = b"\r\n" if lines[start].endswith(b"\r\n") else b"\n"
    lines.insert(insertion, TAKEOVER_COMMAND + newline)
    updated = b"".join(lines)
    action = updated.splitlines(keepends=True)[start:end + 1]
    if sum(line.strip() == TAKEOVER_COMMAND.strip() for line in action) != 1:
        raise ValueError("failed to insert the synchronous takeover command")
    return updated


def patch_ramdisk(original_body):
    before = records(gzip.decompress(original_body))
    index = ramdisk_index([(r.name, r.fields, r.data) for r in before])
    rc_name = next((r.name for r in before if r.key == "init.z1.rc"), None)
    if rc_name is None:
        raise ValueError("R9 ramdisk lacks init.z1.rc")
    if any(name in index for name in SCRIPT_NAMES):
        raise ValueError("input is not pristine R9: takeover scripts already exist")
    rc = add_takeover_command(index["init.z1.rc"][2])
    next_inode = max(r.fields[0] for r in before)
    additions = []
    for name in SCRIPT_NAMES:
        data = (ROOT / "stock" / name).read_bytes()
        next_inode += 1
        fields = [next_inode, stat.S_IFREG | 0o755, 0, 0, 1, 0,
                  len(data), 0, 0, 0, 0, 0, 0]
        additions.append(Record(name.encode(), fields, data, encode(name.encode(), fields, data)))
    output = []
    for record in before:
        if record.key == "TRAILER!!!":
            output.extend(addition.raw for addition in additions)
        if record.name == rc_name:
            output.append(encode(record.name, record.fields, rc))
        else:
            output.append(record.raw)
    archive = b"".join(output)
    archive += bytes(-len(archive) % 512)
    after = records(archive)
    final = ramdisk_index([(r.name, r.fields, r.data) for r in after])
    added = sorted(set(final) - set(index))
    removed = sorted(set(index) - set(final))
    changed = sorted(key for key in set(index) & set(final)
                     if index[key][2] != final[key][2])
    if added != sorted(SCRIPT_NAMES) or removed or changed != ["init.z1.rc"]:
        raise ValueError("cpio changes exceed the takeover allowlist")
    unchanged_raw = [r.raw for r in before if r.name != rc_name]
    retained_raw = [r.raw for r in after if r.key not in SCRIPT_NAMES and r.name != rc_name]
    if unchanged_raw != retained_raw:
        raise ValueError("an unchanged cpio record was re-encoded or reordered")
    old_rc, new_rc = index["init.z1.rc"][1], final["init.z1.rc"][1]
    if old_rc[:6] + old_rc[7:11] != new_rc[:6] + new_rc[7:11]:
        raise ValueError("init.z1.rc ownership, mode or metadata changed")
    for name in SCRIPT_NAMES:
        _, fields, data = final[name]
        if (fields[1] != 0o100755 or fields[2:5] != [0, 0, 1] or
                data != (ROOT / "stock" / name).read_bytes()):
            raise ValueError(f"invalid metadata or content for /{name}")
    return gzip.compress(archive, compresslevel=9, mtime=0), added, changed


def update_ramdisk_records(body, updates):
    before = records(gzip.decompress(body))
    out = []
    for record in before:
        if record.name in updates:
            out.append(encode(record.name, record.fields, updates[record.name]))
        else:
            out.append(record.raw)
    archive = b"".join(out)
    archive += bytes(-len(archive) % 512)
    return gzip.compress(archive, compresslevel=9, mtime=0)


def normalized_dts(path):
    result = subprocess.run(["dtc", "--quiet", "-I", "dtb", "-O", "dts", str(path)],
                            check=True, capture_output=True)
    pattern = rb"linux,initrd-end\s*=\s*<[^>]+>;"
    normalized, count = re.subn(pattern, b"linux,initrd-end = <INITRD_END>;", result.stdout)
    if count != 1:
        raise ValueError("DTB does not contain exactly one linux,initrd-end property")
    return normalized


def patch_dtb(original, body_size, directory):
    if body_size > 0xFFFFFFFF - INITRD_START:
        raise ValueError("ramdisk exceeds the 32-bit initrd address range")
    old_path = directory / "original.dtb"
    new_path = directory / "mt6572-z1.dtb"
    old_path.write_bytes(original)
    new_path.write_bytes(original)
    end = INITRD_START + body_size
    subprocess.run(["fdtput", "-t", "x", str(new_path), "/chosen",
                    "linux,initrd-end", f"{end:x}"], check=True, capture_output=True)
    updated = new_path.read_bytes()
    if (dt_cell(new_path, "linux,initrd-start") != INITRD_START or
            dt_cell(new_path, "linux,initrd-end") != end):
        raise ValueError("patched DT initrd range verification failed")
    if normalized_dts(old_path) != normalized_dts(new_path):
        raise ValueError("DT semantics changed beyond linux,initrd-end")
    return updated, end


def repair(input_path, output_dir, expected_sha256=None, restorecon_etc=False):
    if not input_path.is_file() or output_dir.exists() or not output_dir.parent.is_dir():
        raise ValueError("input must exist; output directory must be new with an existing parent")
    base_data = input_path.read_bytes()
    if expected_sha256 is not None and sha(base_data) != expected_sha256:
        raise ValueError("input boot SHA-256 differs from the required original R9 baseline")
    base = parse_boot(base_data)
    with tempfile.TemporaryDirectory(prefix=".z1-r9-repair-", dir=output_dir.parent) as temp:
        work = Path(temp)
        old_rd_path = work / "original-ramdisk-mtk.img"
        old_rd_path.write_bytes(base.ramdisk)
        check_ramdisk(old_rd_path, require_android_logging=True)
        body, added, changed = patch_ramdisk(base.ramdisk[MTK_HEADER_SIZE:])
        if restorecon_etc:
            rc_index = ramdisk_index([(r.name, r.fields, r.data) for r in records(gzip.decompress(body))])
            defaults = rc_index.get("default.prop")
            if defaults is None or b"selinux.restorecon_recursive=" in defaults[2]:
                raise ValueError("cannot safely select /system/etc for init SELinux restore")
            body = update_ramdisk_records(body, {b"default.prop": defaults[2] + b"selinux.restorecon_recursive=/system/etc\n"})
            changed.append("default.prop")
        final_index = ramdisk_index([(r.name, r.fields, r.data) for r in records(gzip.decompress(body))])
        if restorecon_etc:
            expected_defaults = rc_index["default.prop"][2] + b"selinux.restorecon_recursive=/system/etc\n"
            if final_index["default.prop"][2] != expected_defaults:
                raise ValueError("SELinux restore property readback differs")
        dtb, end = patch_dtb(base.dtb, len(body), work)
        kernel_header = bytearray(base.kernel[:MTK_HEADER_SIZE])
        struct.pack_into("<I", kernel_header, 4, len(base.zimage) + len(dtb))
        kernel = bytes(kernel_header) + base.zimage + dtb
        rd_header = bytearray(base.ramdisk[:MTK_HEADER_SIZE])
        struct.pack_into("<I", rd_header, 4, len(body))
        ramdisk = bytes(rd_header) + body
        rd_path = work / "ramdisk-mtk.img"
        rd_path.write_bytes(ramdisk)
        check_ramdisk(rd_path, require_android_logging=True)
        header = bytearray(base.data[:base.page])
        struct.pack_into("<I", header, 8, len(kernel))
        struct.pack_into("<I", header, 16, len(ramdisk))
        header[576:608] = boot_id(kernel, ramdisk)
        image = bytes(header) + padded(kernel, base.page) + padded(ramdisk, base.page)
        if len(image) > BOOT_LIMIT:
            raise ValueError(f"candidate exceeds the {BOOT_LIMIT}-byte boot partition")
        check_image(image, base.zimage + dtb, ramdisk, base.cmdline)
        check_image(base.data, base.zimage + base.dtb, base.ramdisk, base.cmdline)
        final = parse_boot(image)
        if (final.zimage != base.zimage or final.cmdline != base.cmdline or
                final.kernel[:4] != base.kernel[:4] or
                final.kernel[8:MTK_HEADER_SIZE] != base.kernel[8:MTK_HEADER_SIZE] or
                final.initrd_start != INITRD_START or final.initrd_end != end):
            raise ValueError("final kernel, cmdline or initrd readback differs from the baseline")
        staging = work / "output"
        staging.mkdir()
        artifacts = {"boot.img": image, "ramdisk-mtk.img": ramdisk, "mt6572-z1.dtb": dtb}
        for name, data in artifacts.items():
            (staging / name).write_bytes(data)
        rc_before = ramdisk_index([(r.name, r.fields, r.data) for r in records(
            gzip.decompress(base.ramdisk[MTK_HEADER_SIZE:]))])["init.z1.rc"]
        rc_after = ramdisk_index([(r.name, r.fields, r.data) for r in records(
            gzip.decompress(body))])["init.z1.rc"]
        manifest = {
            "status": "HOST_PACKAGE_PASS_NOT_DEVICE_VERIFIED",
            "device_boot_verified": False,
            "flashing_performed": False,
            "input_boot": {"name": input_path.name, "bytes": len(base_data), "sha256": sha(base_data)},
            "outputs": {name: {"bytes": len(data), "sha256": sha(data)}
                        for name, data in artifacts.items()},
            "preserved": {
                "zimage_bytes": len(base.zimage), "zimage_sha256": sha(base.zimage),
                "mtk_kernel_header_sha256": sha(base.kernel[:MTK_HEADER_SIZE]),
                "cmdline": base.cmdline, "cmdline_sha256": sha(base.cmdline.encode()),
                "kernel_load_address": "0x10008000", "ramdisk_load_address": "0x11000000",
                "initrd_start": f"{INITRD_START:#x}",
                "original_initrd_end": f"{base.initrd_end:#x}", "new_initrd_end": f"{end:#x}",
                "original_rootfs_body_bytes": len(base.ramdisk) - MTK_HEADER_SIZE,
                "new_rootfs_body_bytes": len(body),
                "watchdog_configuration": "unchanged",
                "dexopt_changes": False,
                "selinux_restorecon_recursive": "/system/etc" if restorecon_etc else None,
            },
            "ramdisk_changes": {
                "added": added, "modified": changed, "removed": [],
                "init_z1_rc": {
                    "old_sha256": sha(rc_before[2]), "new_sha256": sha(rc_after[2]),
                    "uid": rc_after[1][2], "gid": rc_after[1][3],
                    "mode": f"{stat.S_IMODE(rc_after[1][1]):04o}",
                    "inserted_command": TAKEOVER_COMMAND.strip().decode(),
                },
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(staging, output_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"manifest.json sha256 {sha((output_dir / 'manifest.json').read_bytes())}")
    print("Host packaging checks passed; no flashing or device verification performed.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original_boot", type=Path, help="pristine R9 boot.img, not a fix3 derivative")
    parser.add_argument("output_dir", type=Path, help="new directory for boot.img, DTB, ROOTFS and manifest")
    parser.add_argument("--expected-sha256", help="optionally pin the exact original R9 input hash")
    parser.add_argument("--restorecon-etc", action="store_true", help="let Android init label /system/etc additions from existing file_contexts")
    args = parser.parse_args()
    repair(args.original_boot, args.output_dir, args.expected_sha256, args.restorecon_etc)


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
