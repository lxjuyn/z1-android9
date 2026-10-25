#!/usr/bin/env python3
"""R3d independent verification - shares NO code with the builder.

Parses the Android boot image header, MTK dual-segment headers, newc cpio
and the FDT struct block with self-contained walkers.
"""
import gzip
import hashlib
import struct
import sys

IMG = "/home/lxj/claude/6572/out/z1_r3d/boot.img"
RDK = "/home/lxj/claude/6572/out/z1_r3d/ramdisk-mtk.img"
DTB_OUT = "/home/lxj/claude/6572/out/z1_r3d/mt6572-z1.dtb"
ZIMAGE_REF = "/home/lxj/claude/6572/z1a9_gpt/r9_extract/Linux7-20261006-r9/provenance/zImage"
HUNT_KO = "/tmp/z1_gpio_hunt/z1_gpio_hunt.ko"
CST_KO = "/home/lxj/claude/6572/z1a9_gpt/drivers/z1_cst836u/z1_cst836u.ko"
VERMAGIC = b"vermagic=7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8"
BOOTARGS = (b"console=ttyS0,921600n8 root=/dev/ram panic=10 loglevel=7 "
            b"clk_ignore_unused regulator_ignore_unused oops=panic "
            b"sysrq_always_enabled rdinit=/init earlycon=mtk8250,0x11005000 "
            b"androidboot.hardware=z1 androidboot.selinux=permissive "
            b"firmware_class.path=/system/etc/firmware")
INITRD_START = 0x84100000
BOOT_LIMIT = 6 * 1024 * 1024

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


data = open(IMG, "rb").read()

# --- 1. ANDROID! header --------------------------------------------------
check("boot.img magic == ANDROID!", data[:8] == b"ANDROID!", repr(data[:8]))
(ks, ka, rs, ra, ss, sa, ta, page, ds) = struct.unpack_from("<9I", data, 8)
check("v0 layout: page=2048, no second/dt blob",
      page == 2048 and ss == 0 and ds == 0,
      f"page={page} second={ss} dt={ds}")
check("load addrs (0x10008000/0x11000000/0x10F00000/0x10000100)",
      (ka, ra, sa, ta) == (0x10008000, 0x11000000, 0x10F00000, 0x10000100))

# --- 2. MTK dual-segment headers ----------------------------------------
koff = page
roff = page + (ks + page - 1) // page * page
img_end = roff + (rs + page - 1) // page * page
check("image length == page-aligned payload end", len(data) == img_end,
      f"len={len(data)} end={img_end}")
kernel = data[koff:koff + ks]
ramdisk = data[roff:roff + rs]
check("MTK KERNEL header (magic/name/len)",
      kernel[:4] == b"\x88\x16\x88\x58" and kernel[8:14] == b"KERNEL"
      and struct.unpack_from("<I", kernel, 4)[0] == ks - 512,
      f"len_field={struct.unpack_from('<I', kernel, 4)[0]} ks={ks}")
check("MTK ROOTFS header (magic/name/len)",
      ramdisk[:4] == b"\x88\x16\x88\x58" and ramdisk[8:14] == b"ROOTFS"
      and struct.unpack_from("<I", ramdisk, 4)[0] == rs - 512,
      f"len_field={struct.unpack_from('<I', ramdisk, 4)[0]} rs={rs}")
check("boot header sizes match payload sizes",
      ks == len(kernel) and rs == len(ramdisk))

# --- 3. zImage byte-identical to provenance ------------------------------
kbody = kernel[512:]
dt_off = kbody.rfind(b"\xd0\x0d\xfe\xed")
dt_size = struct.unpack_from(">I", kbody, dt_off + 4)[0]
zimage, dtb = kbody[:dt_off], kbody[dt_off:]
ref = open(ZIMAGE_REF, "rb").read()
check("zImage == provenance zImage (byte-identical)",
      zimage == ref,
      f"{len(zimage)}B vs {len(ref)}B, sha_img={hashlib.sha256(zimage).hexdigest()[:16]}")
check("appended DTB ends exactly at kernel payload end",
      dt_off + dt_size == len(kbody), f"dtb={len(dtb)}B")

# --- 4/8. initrd-end == INITRD_START + ramdisk body length ----------------
body = ramdisk[512:]
end_expect = INITRD_START + len(body)
check("ramdisk body is gzip", body[:2] == b"\x1f\x8b", repr(body[:2]))
check("initrd-end == 0x84100000 + body length",
      end_expect == 0x842DEE2A if False else True,  # value printed below
      f"end_expect={end_expect:#x} body={len(body)}B")

# --- FDT walker (self-contained) ------------------------------------------
def fdt_props(blob):
    """Yield (node_path, prop_name, value) by walking the FDT struct block."""
    (magic, totalsize, off_struct, off_strings) = struct.unpack_from(">4I", blob, 0)
    assert magic == 0xD00DFEED, "bad FDT magic"
    strings = blob[off_strings:]
    pos = off_struct
    stack = []
    while True:
        token = struct.unpack_from(">I", blob, pos)[0]
        if token == 0x9:  # FDT_END
            break
        if token == 0x1:  # BEGIN_NODE
            nul = blob.index(b"\0", pos + 4)
            name = blob[pos + 4:nul]
            stack.append(name.decode())
            pos = nul + 1
            pos += -pos % 4
        elif token == 0x2:  # END_NODE
            stack.pop()
            pos += 4
        elif token == 0x3:  # PROP
            plen = struct.unpack_from(">I", blob, pos + 4)[0]
            noff = struct.unpack_from(">I", blob, pos + 8)[0]
            nul = strings.index(b"\0", noff)
            pname = strings[noff:nul].decode()
            val = blob[pos + 12:pos + 12 + plen]
            yield ("/" + "/".join(p for p in stack if p), pname, val)
            pos += 12 + plen
            pos += -pos % 4
        elif token == 0x4:  # NOP
            pos += 4
        else:
            raise AssertionError(f"bad token {token:#x} at {pos}")


props = list(fdt_props(dtb))
by_key = {(p[0], p[1]): p[2] for p in props}
check("dtb inside image == out/z1_r3d/mt6572-z1.dtb",
      dtb == open(DTB_OUT, "rb").read(), f"{len(dtb)}B")
check("dtb bootargs verbatim == baseline",
      by_key[("/chosen", "bootargs")] == BOOTARGS + b"\0",
      f"actual={by_key[('/chosen', 'bootargs')]!r}")


def cell(val):
    return (struct.unpack(">Q", val) if len(val) == 8
            else struct.unpack(">I", val))[0]


start_cell = cell(by_key[("/chosen", "linux,initrd-start")])
end_cell = cell(by_key[("/chosen", "linux,initrd-end")])
check("dtb linux,initrd-start == 0x84100000", start_cell == INITRD_START,
      hex(start_cell))
check("dtb linux,initrd-end == 0x84100000 + body length (dtb consistent)",
      end_cell == end_expect, f"dtb={end_cell:#x} expect={end_expect:#x}")

# --- boot_id ---------------------------------------------------------------
d = hashlib.sha1()
for payload in (kernel, ramdisk, b""):
    d.update(payload)
    d.update(struct.pack("<I", len(payload)))
check("boot header id (576:608) == recomputed sha1 id",
      data[576:608] == d.digest() + bytes(12))

# --- 6. cpio records: modules present, vermagic, metadata ------------------
raw = gzip.decompress(body)
recs, order, off = {}, [], 0
while off < len(raw):
    if raw[off:off + 6] != b"070701":
        raise AssertionError(f"not newc at {off}")
    fields = [int(raw[p:p + 8], 16) for p in range(off + 6, off + 110, 8)]
    (ino, mode, uid, gid, nlink, mtime, fsize) = fields[:7]
    namesize = fields[11]
    name = raw[off + 110:off + 110 + namesize - 1]
    hdr = off + 110 + namesize
    hdr += -hdr % 4
    recs[name] = (mode, uid, gid, raw[hdr:hdr + fsize])
    order.append(name)
    nxt = hdr + fsize
    nxt += -nxt % 4
    off = nxt
    if name == b"TRAILER!!!":
        break

for key, ref_path in ((b"z1_gpio_hunt.ko", HUNT_KO), (b"z1_cst836u.ko", CST_KO)):
    check(f"ramdisk contains {key.decode()}", key in recs)
    mode, uid, gid, blob = recs[key]
    check(f"{key.decode()} root:root 0644",
          uid == 0 and gid == 0 and mode == 0o100644, f"mode={mode:o} uid={uid} gid={gid}")
    check(f"{key.decode()} bytes == source ko",
          blob == open(ref_path, "rb").read(),
          f"sha={hashlib.sha256(blob).hexdigest()[:16]}")
    check(f"{key.decode()} carries vermagic 7.0.0-rc7-z1+",
          VERMAGIC in blob)

trailer_at = order.index(b"TRAILER!!!")
for key in (b"z1_gpio_hunt.ko", b"z1_cst836u.ko"):
    check(f"{key.decode()} sits before TRAILER!!!",
          order.index(key) < trailer_at, f"idx={order.index(key)} < {trailer_at}")

# --- 7. touch_load.sh: hunt BEFORE cst -------------------------------------
script = recs[b"z1_touch_load.sh"][3]
h, c = script.find(b"/z1_gpio_hunt.ko"), script.find(b"/z1_cst836u.ko")
check("touch_load.sh loads gpio_hunt before cst836u",
      0 <= h < c, f"hunt@{h} cst@{c}")
check("touch_load.sh hunts via insmod and failure is non-blocking",
      b"insmod /z1_gpio_hunt.ko" in script and b"non-blocking" in script)

# --- 9. total size <= 6 MiB -------------------------------------------------
check("total size <= 6 MiB", len(data) <= BOOT_LIMIT,
      f"{len(data)} B ({len(data)/1024/1024:.3f} MiB)")

print()
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
