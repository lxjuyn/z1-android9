#!/usr/bin/env python3
"""R3e independent verification — written fresh for R3e; shares NO code with
the builder (z1_make_r3_aggressive.py) nor with z1_verify_r3d.py. All parsing
(boot header, MTK dual-segment headers, newc cpio, FDT struct block, cmdline
field reassembly, boot_id) re-implemented from the on-disk formats.

Checks: R3d's 21-item list (adapted to the R3e --cmdline string) plus the
three R3e-specific items (dtb ramoops node legality, printk.devkmsg=on,
z1_wifi_probe.sh + wifi-nvram-WIFI.bin in the ramdisk) plus independent
re-checks of the R3e semantic deltas (z1_6620 ignition, watchdogd 10 30,
dex2oat-threads=1, NVRAM persist / wmt_dbg in z1_radio_finish.sh).
"""
import gzip
import hashlib
import struct
import sys

IMG = "/home/lxj/claude/6572/out/z1_r3e/boot.img"
RDK = "/home/lxj/claude/6572/out/z1_r3e/ramdisk-mtk.img"
DTB_OUT = "/home/lxj/claude/6572/out/z1_r3e/mt6572-z1.dtb"
DTB_SRC = "/tmp/z1_r3d_tree/arch/arm/boot/dts/mediatek/mt6572-z1.dtb"
ZIMAGE_REF = "/home/lxj/claude/6572/z1a9_gpt/r9_extract/Linux7-20261006-r9/provenance/zImage"
HUNT_KO = "/tmp/z1_gpio_hunt/z1_gpio_hunt.ko"
CST_KO = "/home/lxj/claude/6572/z1a9_gpt/drivers/z1_cst836u/z1_cst836u.ko"
PROBE_SH = "/home/lxj/claude/6572/z1a9_gpt/stock/z1_wifi_probe.sh"
NVRAM = "/home/lxj/claude/6572/mainline_recon/wifi-nvram-WIFI.bin"

VERMAGIC = b"vermagic=7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8"
CMDLINE = (b"console=ttyS0,921600n8 root=/dev/ram panic=10 loglevel=7 "
           b"clk_ignore_unused regulator_ignore_unused oops=panic "
           b"sysrq_always_enabled rdinit=/init earlycon=mtk8250,0x11005000 "
           b"androidboot.hardware=z1 androidboot.selinux=permissive "
           b"firmware_class.path=/system/etc/firmware printk.devkmsg=on")
INITRD_START = 0x84100000
BOOT_LIMIT = 6 * 1024 * 1024
CMD_OFF = 64          # 8 + 10*4 + 16 (v0 header, after 10 size fields + 16B name)
CMD_MAIN = 512
CMD_EXTRA_OFF = 608   # 64 + 512 + 8*4
CMD_EXTRA = 1024
BOOTID_OFF = 576

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


data = open(IMG, "rb").read()

# --- 1. ANDROID! header ---------------------------------------------------
check("01 boot.img magic == ANDROID!", data[:8] == b"ANDROID!", repr(data[:8]))
(ks, ka, rs, ra, ss, sa, ta, page, ds) = struct.unpack_from("<9I", data, 8)
check("02 v0 layout: page=2048, no second/dt blob",
      page == 2048 and ss == 0 and ds == 0,
      f"page={page} second={ss} dt={ds}")
check("03 load addrs (0x10008000/0x11000000/0x10F00000/0x10000100)",
      (ka, ra, sa, ta) == (0x10008000, 0x11000000, 0x10F00000, 0x10000100),
      f"{ka:#x}/{ra:#x}/{sa:#x}/{ta:#x}")

# --- 2. MTK dual-segment headers ------------------------------------------
koff = page
roff = page + (ks + page - 1) // page * page
img_end = roff + (rs + page - 1) // page * page
check("04 image length == page-aligned payload end", len(data) == img_end,
      f"len={len(data)} end={img_end}")
kernel = data[koff:koff + ks]
ramdisk = data[roff:roff + rs]
check("05 MTK KERNEL header (magic/name/len)",
      kernel[:4] == bytes.fromhex("88168858") and kernel[8:14] == b"KERNEL"
      and struct.unpack_from("<I", kernel, 4)[0] == ks - 512,
      f"len_field={struct.unpack_from('<I', kernel, 4)[0]} ks={ks}")
check("06 MTK ROOTFS header (magic/name/len)",
      ramdisk[:4] == bytes.fromhex("88168858") and ramdisk[8:14] == b"ROOTFS"
      and struct.unpack_from("<I", ramdisk, 4)[0] == rs - 512,
      f"len_field={struct.unpack_from('<I', ramdisk, 4)[0]} rs={rs}")
check("07 boot header sizes match payload sizes",
      ks == len(kernel) and rs == len(ramdisk))

# --- 3. zImage provenance + dtb tail --------------------------------------
# (R3d 口径: rfind 取最后一个 FDT magic —— zImage 压缩流内允许 4 字节假命中,
#  "恰收尾"由 dt_size 延伸到 kbody 末尾 + FDT totalsize 自洽双断言保证)
kbody = kernel[512:]
magic = b"\xd0\x0d\xfe\xed"
dt_off = kbody.rfind(magic)
dt_size = struct.unpack_from(">I", kbody, dt_off + 4)[0]
zimage, dtb = kbody[:dt_off], kbody[dt_off:]
ref = open(ZIMAGE_REF, "rb").read()
check("08 zImage == provenance zImage (byte-identical)", zimage == ref,
      f"{len(zimage)}B vs {len(ref)}B")
check("09 appended DTB ends exactly at kernel payload end",
      dt_off + dt_size == len(kbody) and len(dtb) == dt_size,
      f"dtb={len(dtb)}B end={dt_off + dt_size} kbody={len(kbody)}")

# --- 4. initrd arithmetic --------------------------------------------------
body = ramdisk[512:]
end_expect = INITRD_START + len(body)
check("10 ramdisk body is gzip", body[:2] == b"\x1f\x8b", repr(body[:2]))
check("11 initrd-end == 0x84100000 + body length", True,
      f"end_expect={end_expect:#x} body={len(body)}B")

# --- FDT walker (self-contained) -------------------------------------------
def fdt_parse(blob):
    """Return (props {(path,name):bytes}, nodes [paths]) from an FDT blob."""
    (fdmagic, totalsize, off_struct, off_strings, _, ver) = struct.unpack_from(">6I", blob, 0)
    assert fdmagic == 0xD00DFEED, "bad FDT magic"
    assert totalsize == len(blob), f"fdt totalsize {totalsize} != blob {len(blob)}"
    strings = blob[off_strings:]
    pos = off_struct
    stack, nodes, props = [], [], {}
    while True:
        token = struct.unpack_from(">I", blob, pos)[0]
        if token == 0x9:
            break
        if token == 0x1:
            nul = blob.index(b"\0", pos + 4)
            name = blob[pos + 4:nul].decode()
            stack.append(name)
            # root node name is the empty string; filter it like the R3d walker
            path = "/" + "/".join(p for p in stack if p)
            nodes.append(path)
            pos = nul + 1
            pos += -pos % 4
        elif token == 0x2:
            stack.pop()
            pos += 4
        elif token == 0x3:
            plen = struct.unpack_from(">I", blob, pos + 4)[0]
            noff = struct.unpack_from(">I", blob, pos + 8)[0]
            nul = strings.index(b"\0", noff)
            pname = strings[noff:nul].decode()
            props[("/" + "/".join(p for p in stack if p), pname)] = blob[pos + 12:pos + 12 + plen]
            pos += 12 + plen
            pos += -pos % 4
        elif token == 0x4:
            pos += 4
        else:
            raise AssertionError(f"bad FDT token {token:#x} at {pos:#x}")
    return props, nodes


props, nodes = fdt_parse(dtb)
check("12 dtb inside image == out/z1_r3e/mt6572-z1.dtb",
      dtb == open(DTB_OUT, "rb").read(), f"{len(dtb)}B")
bootargs = props.get(("/chosen", "bootargs"), b"")
check("13 dtb bootargs verbatim == --cmdline string (incl. printk.devkmsg=on)",
      bootargs == CMDLINE + b"\0", f"actual={bootargs!r}")

# --- boot header cmdline fields reassembled (independent writer rules) -----
raw_main = data[CMD_OFF:CMD_OFF + CMD_MAIN]
raw_extra = data[CMD_EXTRA_OFF:CMD_EXTRA_OFF + CMD_EXTRA]
hdr_cmd = raw_main.split(b"\0")[0] + raw_extra.split(b"\0")[0]
check("14 boot header cmdline fields == --cmdline string", hdr_cmd == CMDLINE,
      f"len={len(hdr_cmd)} (expected {len(CMDLINE)})")
check("15 header cmdline == dtb bootargs (single source of truth)",
      hdr_cmd + b"\0" == bootargs)
check("16 printk.devkmsg=on present as trailing cmdline token",
      hdr_cmd.split()[-1] == b"printk.devkmsg=on",
      repr(hdr_cmd.split()[-1]))


def cell(val):
    return (struct.unpack(">Q", val) if len(val) == 8
            else struct.unpack(">I", val))[0]


start_cell = cell(props[("/chosen", "linux,initrd-start")])
end_cell = cell(props[("/chosen", "linux,initrd-end")])
check("17 dtb linux,initrd-start == 0x84100000", start_cell == INITRD_START,
      hex(start_cell))
check("18 dtb linux,initrd-end == 0x84100000 + body length",
      end_cell == end_expect, f"dtb={end_cell:#x} expect={end_expect:#x}")

# --- boot_id: sha1 over (kernel,len) pairs ---------------------------------
d = hashlib.sha1()
for payload in (kernel, ramdisk, b""):
    d.update(payload)
    d.update(struct.pack("<I", len(payload)))
check("19 boot header id (576:608) == recomputed sha1 id",
      data[BOOTID_OFF:BOOTID_OFF + 32] == d.digest() + bytes(12))

# --- 6. cpio records --------------------------------------------------------
raw = gzip.decompress(body)
recs, order, off = {}, [], 0
while off < len(raw):
    if raw[off:off + 6] != b"070701":
        raise AssertionError(f"not newc at {off:#x}")
    fields = [int(raw[p:p + 8], 16) for p in range(off + 6, off + 110, 8)]
    fsize = fields[6]
    namesize = fields[11]
    name = raw[off + 110:off + 110 + namesize - 1]
    hdr = off + 110 + namesize
    hdr += -hdr % 4
    recs[name] = (fields[1], fields[2], fields[3], raw[hdr:hdr + fsize])
    order.append(name)
    nxt = hdr + fsize
    nxt += -nxt % 4
    off = nxt
    if name == b"TRAILER!!!":
        break

trailer_at = order.index(b"TRAILER!!!")
for key, ref_path in ((b"z1_gpio_hunt.ko", HUNT_KO), (b"z1_cst836u.ko", CST_KO)):
    mode, uid, gid, blob = recs[key]
    check(f"20 {key.decode()} present, root:root 0644",
          key in recs and uid == 0 and gid == 0 and mode == 0o100644,
          f"mode={mode:o} uid={uid} gid={gid}")
    check(f"21 {key.decode()} bytes == source ko",
          blob == open(ref_path, "rb").read(),
          f"sha={hashlib.sha256(blob).hexdigest()[:16]}")
    check(f"22 {key.decode()} vermagic 7.0.0-rc7-z1+", VERMAGIC in blob)
    check(f"23 {key.decode()} before TRAILER!!!",
          order.index(key) < trailer_at,
          f"idx={order.index(key)} < {trailer_at}")

script = recs[b"z1_touch_load.sh"][3]
h, c = script.find(b"/z1_gpio_hunt.ko"), script.find(b"/z1_cst836u.ko")
check("24 touch_load.sh loads gpio_hunt before cst836u", 0 <= h < c,
      f"hunt@{h} cst@{c}")
check("25 touch_load.sh insmod hunt, failure non-blocking",
      b"insmod /z1_gpio_hunt.ko" in script and b"non-blocking" in script)

check("26 total size <= 6 MiB", len(data) <= BOOT_LIMIT,
      f"{len(data)} B ({len(data) / 1024 / 1024:.3f} MiB)")

# ===================== R3e-specific checks ==============================
# 27. ramoops node present; range legal; no overlap with reserved-memory
#     siblings (reg AND alloc-ranges+size) nor the chosen framebuffer.
ram = "/reserved-memory/ramoops@be200000"
check("27 reserved-memory/ramoops@be200000 node exists", ram in nodes,
      f"reserved-memory children={[n for n in nodes if n.startswith('/reserved-memory/')]}")


def be_cells(v):
    """Interpret a reg/alloc-ranges value as (base, size) u32 pairs."""
    return [(cell(v[i:i + 4]), cell(v[i + 4:i + 8])) for i in range(0, len(v), 8)]


ok27, detail27, overlaps = False, "node missing", []
rambase = ramsize = None
if ram in nodes:
    r = props.get((ram, "reg"), b"")
    nmap = (ram, "no-map") in props
    recsz = cell(props[(ram, "record-size")])
    consz = cell(props[(ram, "console-size")])
    pmsg = cell(props[(ram, "pmsg-size")])
    ecc = cell(props[(ram, "ecc-size")])
    rambase, ramsize = cell(r[:4]), cell(r[4:8])
    # DRAM window from /memory@80000000 (multi-segment tolerant)
    dram = []
    for p in nodes:
        if p == "/memory" or p.startswith("/memory@"):
            dram += be_cells(props[(p, "reg")])
    in_dram = any(lo <= rambase and rambase + ramsize <= lo + sz for lo, sz in dram)
    ok27 = (rambase == 0xBE200000 and ramsize == 0x100000 and nmap
            and recsz == 0x20000 and consz == 0x40000 and pmsg == 0x20000
            and ecc == 0 and in_dram)
    detail27 = (f"reg={rambase:#x}+{ramsize:#x} no-map={nmap} rec={recsz:#x} "
                f"con={consz:#x} pmsg={pmsg:#x} ecc={ecc} dram={[f'{lo:#x}+{sz:#x}' for lo, sz in dram]}")
    base, size = rambase, ramsize
    for (p, n), v in props.items():
        # sibling reserved-memory reg + CMA alloc-ranges ranges
        if n in ("reg", "alloc-ranges") and p != ram and p.startswith("/reserved-memory/"):
            for pb, ps in be_cells(v):
                if ps and pb < base + size and base < pb + ps:
                    overlaps.append((p, n, hex(pb), hex(ps)))
    for (p, n), v in props.items():
        # chosen simple-framebuffer surface
        if n == "reg" and "framebuffer" in p:
            for pb, ps in be_cells(v):
                if pb < base + size and base < pb + ps:
                    overlaps.append((p, n, hex(pb), hex(ps)))
    ok27 = ok27 and not overlaps
    detail27 += f" overlaps={overlaps or 'NONE'}"
check("28 ramoops range legal (1MiB @be200000, no-map, sizes, in-DRAM, no overlap)", ok27, detail27)

# 29. wifi probe + NVRAM records in ramdisk
mode, uid, gid, blob = recs[b"z1_wifi_probe.sh"]
check("29 z1_wifi_probe.sh in ramdisk, root:root 0755, bytes == stock source",
      uid == 0 and gid == 0 and mode == 0o100755
      and blob == open(PROBE_SH, "rb").read(),
      f"mode={mode:o} sha={hashlib.sha256(blob).hexdigest()[:16]}")
mode, uid, gid, blob = recs[b"wifi-nvram-WIFI.bin"]
check("30 wifi-nvram-WIFI.bin in ramdisk, 512 B, root:root 0644, bytes == device blob",
      len(blob) == 512 and uid == 0 and gid == 0 and mode == 0o100644
      and blob == open(NVRAM, "rb").read(),
      f"{len(blob)}B mode={mode:o} sha={hashlib.sha256(blob).hexdigest()[:16]}")

# ---- R3e semantic deltas re-checked independently (builder asserted too) ----
rc = recs[b"init.z1.rc"][3]
check("31 init.z1.rc: z1_6620 armed by modules_ready=1 (6620_launcher -m 3, no oneshot)",
      b"on property:sys.z1.radio.modules_ready=1" in rc
      and b"start z1_6620" in rc
      and b"service z1_6620 /system/bin/6620_launcher -m 3 -p /system/etc/firmware/" in rc
      and b"service z1_6620" in rc and b"oneshot" not in rc.split(b"service z1_6620", 1)[1].split(b"service", 1)[0])
check("32 init.z1.rc: watchdogd 10 30 and no 5 10 line",
      b"service watchdogd /sbin/watchdogd 10 30" in rc
      and b"service watchdogd /sbin/watchdogd 5 10" not in rc)
check("33 init.z1.rc: post-fs exec of z1_wifi_probe.sh present",
      b"exec_background u:r:init:s0 root root -- /system/bin/sh /z1_wifi_probe.sh" in rc)
dprop = recs[b"default.prop"][3]
check("34 default.prop: dalvik.vm.dex2oat-threads=1",
      b"dalvik.vm.dex2oat-threads=1\n" in dprop)
fin = recs[b"z1_radio_finish.sh"][3]
check("35 z1_radio_finish.sh: wmt_dbg quick-sleep off + NVRAM persist block",
      b"wmt_dbg" in fin and b"wifi-nvram-WIFI.bin" in fin)

print()
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
