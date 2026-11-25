#!/usr/bin/env python3
"""Build a boot-only R7 charging image from the pinned core-wake boot."""
import argparse
import gzip
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import z1_prepare_recovery_boot as boot
from z1_ramdisk_edit import encode

BASE_SHA = "09b2c74d7c0de0c944ba32f041a5fd92a054df3df4debe40ae8434cfe38030d4"
DISPATCHER = "sbin/z1_charge_ui"
COMPATIBLE = ["jbo,z1-adc-battery", "adc-battery"]


def check_dispatcher(data, path):
    boot.require(data[:6] == b"\x7fELF\x01\x01" and len(data) >= 52 and
                 struct.unpack_from("<HHI", data, 16) == (2, 40, 1),
                 "dispatcher must be a little-endian ARM ELF32 executable")
    headers = subprocess.check_output(["readelf", "-lW", path], text=True)
    types = {line.split()[0] for line in headers.splitlines() if line.strip()}
    boot.require("LOAD" in types and "INTERP" not in types and "DYNAMIC" not in types,
                 "dispatcher must be statically linked")


def add_dispatcher(rootfs, dispatcher):
    body, records = boot.cpio(rootfs)
    by_name = {record["name"]: record for record in records}
    boot.require(DISPATCHER not in by_name and
                 stat.S_ISDIR(by_name["sbin"]["fields"][1]),
                 "unexpected base ramdisk sbin layout")
    name = DISPATCHER.encode() + b"\0"
    inode = 1 + max(record["fields"][0] for record in records)
    fields = [inode, stat.S_IFREG | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(name), 0]
    header = boot.pad(b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
                      + name, 4)
    record = encode(header, dispatcher)
    trailer = records[-1]["pos"]
    compressed = gzip.compress(body[:trailer] + record + body[trailer:], mtime=0)
    mtk_header = bytearray(rootfs[:512])
    struct.pack_into("<I", mtk_header, 4, len(compressed))
    return bytes(mtk_header) + compressed


def write_property(path, node, key, kind, values):
    subprocess.run(["fdtput", "-t", kind, path, node, key, *values], check=True)


def patch_dtb(path, dtb, old_rootfs, new_rootfs):
    path.write_bytes(dtb)
    start = int(boot.dt_read(path, "/chosen", "linux,initrd-start"), 16)
    old_end = boot.dt_read(path, "/chosen", "linux,initrd-end")
    boot.require(start == 0x84100000 and int(old_end, 16) == start + len(old_rootfs) - 512,
                 "unexpected base initrd bounds")
    args = boot.dt_read(path, "/chosen", "bootargs", "s")
    boot.require([arg for arg in args.split() if arg.startswith("rdinit=")] == ["rdinit=/init"],
                 "unexpected base rdinit")
    names = boot.dt_read(path, "/battery", "io-channel-names", "s").split()
    channels = boot.dt_read(path, "/battery", "io-channels").split()
    boot.require(names == ["voltage", "temperature"] and len(channels) == 4 and
                 channels[0] == channels[2] and
                 boot.dt_read(path, "/battery", "compatible", "s") == "adc-battery",
                 "unexpected base battery binding")
    changes = [
        ("/chosen", "bootargs", "s", [args], [args.replace("rdinit=/init", f"rdinit=/{DISPATCHER}", 1)]),
        ("/chosen", "linux,initrd-end", "x", [old_end], [f"{start + len(new_rootfs) - 512:x}"]),
        ("/battery", "compatible", "s", ["adc-battery"], COMPATIBLE),
        ("/battery", "io-channel-names", "s", names, names + ["boot-ocv"]),
        ("/battery", "io-channels", "x", channels, channels + [channels[0], "11"]),
    ]
    for node, key, kind, _, values in changes:
        write_property(path, node, key, kind, values)
        boot.require(boot.dt_read(path, node, key, kind) == " ".join(values),
                     f"invalid DT readback for {node}/{key}")
    updated = path.read_bytes()
    for node, key, kind, original, _ in changes:
        write_property(path, node, key, kind, original)
    boot.require(path.read_bytes() == dtb, "DTB changed outside the intended properties")
    return updated


def build(base, zimage, dispatcher_path):
    boot.require(boot.sha(base) == BASE_SHA, "base must be the pinned R7 core-wake boot")
    boot.require(len(zimage) >= 0x40, "truncated ARM zImage")
    branch, magic, start, end, endian, marker = struct.unpack_from("<6I", zimage, 0x20)
    entry = 0x28 + (branch & 0x00FFFFFF) * 4
    boot.require(branch >> 24 == 0xEA and 0x3c <= entry < len(zimage) and
                 (magic, start, end, endian, marker) ==
                 (0x016F2818, 0, len(zimage), 0x04030201, 0x45454545),
                 "expected a little-endian ARM R7 zImage without an appended DTB")
    dispatcher = dispatcher_path.read_bytes()
    check_dispatcher(dispatcher, dispatcher_path)
    header, kh, _, dtb, rootfs = boot.split_boot(base)
    updated = add_dispatcher(rootfs, dispatcher)
    with tempfile.TemporaryDirectory(prefix="z1-r7-charge-") as directory:
        dt = patch_dtb(Path(directory) / "appended.dtb", dtb, rootfs, updated)
    image = boot.make_image(header, kh, zimage, dt, updated)
    h2, kh2, z2, dt2, root2 = boot.split_boot(image)
    boot.require((z2, dt2, root2) == (zimage, dt, updated), "packed payload mismatch")
    boot.require((h2[:8], h2[12:16], h2[20:576], h2[608:]) ==
                 (header[:8], header[12:16], header[20:576], header[608:]) and
                 kh2[:4] + kh2[8:] == kh[:4] + kh[8:] and
                 root2[:4] + root2[8:512] == rootfs[:4] + rootfs[8:512],
                 "unexpected boot or MTK header change")
    body, records = boot.cpio(root2)
    record = next(record for record in records if record["name"] == DISPATCHER)
    boot.require(record["fields"][1] == stat.S_IFREG | 0o755 and
                 body[record["hdr_end"]:record["hdr_end"] + record["size"]] == dispatcher and
                 body[:record["pos"]] + body[record["file_end"]:] == gzip.decompress(rootfs[512:]),
                 "unexpected cpio record delta")
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("zimage", type=Path)
    parser.add_argument("dispatcher", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        image = build(args.base.read_bytes(), args.zimage.read_bytes(), args.dispatcher)
        with args.output.open("xb") as output:
            output.write(image)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"R7 charge boot build failed: {exc}\n")
    print(boot.sha(image), args.output, len(image))


if __name__ == "__main__":
    main()
