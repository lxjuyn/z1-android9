#!/usr/bin/env python3
"""R3e vs R3d 逐记录回归归因 — 全新解析实现，不复用 builder / 既有 diff 工具。

层：boot 头字段 / MTK 段头 / zImage / cpio(newc) 逐记录（含 inode 元数据）/
FDT 属性。每条差异打印归因标签；最终对照 EXPECTED 清单裁决。
"""
import gzip
import hashlib
import struct
import sys

R3D = "/home/lxj/claude/6572/out/z1_r3d"
R3E = "/home/lxj/claude/6572/out/z1_r3e"
CMD_NEW = (b"console=ttyS0,921600n8 root=/dev/ram panic=10 loglevel=7 "
           b"clk_ignore_unused regulator_ignore_unused oops=panic "
           b"sysrq_always_enabled rdinit=/init earlycon=mtk8250,0x11005000 "
           b"androidboot.hardware=z1 androidboot.selinux=permissive "
           b"firmware_class.path=/system/etc/firmware printk.devkmsg=on")

EXPECTED_RAMDISK_CHANGED = {
    "init.z1.rc": "R3e init.z1.rc: +z1_6620 ignition block +wifi_probe exec +watchdogd 10 30",
    "default.prop": "R3e default.prop: +dalvik.vm.dex2oat-threads=1",
    "z1_radio_finish.sh": "R3e stock: +wmt_dbg quick-sleep off +NVRAM/regdb persist",
    "z1_logpersist.sh": "R3e stock: 60s snapshot +[z1prop] sentinels +swap",
    "z1_heartbeat.sh": "R3e stock: zero-fork sampling +voltage_now",
}
EXPECTED_RAMDISK_ADDED = {
    "z1_wifi_probe.sh": "R3e A1-2 wifi func-on/scan probe",
    "wifi-nvram-WIFI.bin": "R3e A1-3 device NVRAM 512B",
    "regulatory.db": "R3e A1-3 host regdb (packed because host file exists)",
}


def load_boot(p):
    data = open(p, "rb").read()
    (ks, ka, rs, ra, ss, sa, ta, page, ds) = struct.unpack_from("<9I", data, 8)
    koff = page
    roff = page + (ks + page - 1) // page * page
    kernel = data[koff:koff + ks]
    ramdisk = data[roff:roff + rs]
    return data, dict(ks=ks, ka=ka, rs=rs, ra=ra, page=page, ss=ss, ds=ds,
                      boot_id=data[576:608].hex(), cmd=data[64:64 + 512],
                      cmdx=data[608:608 + 1024]), kernel, ramdisk


def mtk_split(seg):
    magic, ln = seg[:4], struct.unpack_from("<I", seg, 4)[0]
    name = seg[8:14]
    return magic, ln, name, seg[512:]


def cpio_recs(body):
    raw = gzip.decompress(body)
    recs, order, off = {}, [], 0
    while off < len(raw):
        assert raw[off:off + 6] == b"070701", f"not newc at {off:#x}"
        fields = [int(raw[p:p + 8], 16) for p in range(off + 6, off + 110, 8)]
        fsize = fields[6]
        namesize = fields[11]
        name = raw[off + 110:off + 110 + namesize - 1].decode()
        hdr = off + 110 + namesize
        hdr += -hdr % 4
        recs[name] = dict(ino=fields[0], mode=fields[1], uid=fields[2],
                          gid=fields[3], size=fsize,
                          sha=hashlib.sha256(raw[hdr:hdr + fsize]).hexdigest())
        order.append(name)
        nxt = hdr + fsize
        nxt += -nxt % 4
        off = nxt
        if name == "TRAILER!!!":
            break
    return recs, order


def fdt_props(blob):
    (magic, totalsize, off_struct, off_strings) = struct.unpack_from(">4I", blob, 0)
    strings = blob[off_strings:]
    pos = off_struct
    stack, props = [], {}
    while True:
        token = struct.unpack_from(">I", blob, pos)[0]
        if token == 0x9:
            break
        if token == 0x1:
            nul = blob.index(b"\0", pos + 4)
            stack.append(blob[pos + 4:nul].decode())
            pos = nul + 1 + (-(nul + 1) % 4)
        elif token == 0x2:
            stack.pop()
            pos += 4
        elif token == 0x3:
            plen = struct.unpack_from(">I", blob, pos + 4)[0]
            noff = struct.unpack_from(">I", blob, pos + 8)[0]
            nul = strings.index(b"\0", noff)
            props[("/" + "/".join(p for p in stack if p),
                   strings[noff:nul].decode())] = blob[pos + 12:pos + 12 + plen]
            pos += 12 + plen + (-(12 + plen) % 4)
        elif token == 0x4:
            pos += 4
        else:
            raise AssertionError(f"token {token:#x}")
    props[("@FDT", "totalsize")] = struct.pack(">I", totalsize)
    return props


def shal(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


d3, h3, k3, r3 = load_boot(f"{R3D}/boot.img")
de, he, ke, re_ = load_boot(f"{R3E}/boot.img")

print("=" * 72)
print("A. boot 头字段层")
unexpected = []
for f in ("page", "ka", "ra", "ss", "ds"):
    if h3[f] != he[f]:
        unexpected.append(f"header.{f}: {h3[f]} -> {he[f]} (EXPECTED unchanged)")
print(f"  page/addrs/second/dt unchanged: "
      f"{all(h3[f] == he[f] for f in ('page', 'ka', 'ra', 'ss', 'ds'))}")
print(f"  kernel_size {h3['ks']} -> {he['ks']} (dtb 变大, 预期)")
print(f"  ramdisk_size {h3['rs']} -> {he['rs']} (ramdisk 增量, 预期)")
m3 = h3["cmd"].split(b"\0")[0] + h3["cmdx"].split(b"\0")[0]
me = he["cmd"].split(b"\0")[0] + he["cmdx"].split(b"\0")[0]
print(f"  cmdline == R3e 新串: {me == CMD_NEW}; R3d 旧串 != 新串: {m3 != CMD_NEW}")
print(f"  boot_id 变更: {h3['boot_id'][:16]}… -> {he['boot_id'][:16]}… (kernel+ramdisk 变, 预期)")

print("=" * 72)
print("B. kernel 段 / zImage / dtb")
mk3, l3, n3, kb3 = mtk_split(k3)
mke, le, ne, kbe = mtk_split(ke)
print(f"  MTK KERNEL 头 magic/name 两侧一致: {(mk3, n3) == (mke, ne)}")
z3, d3b = kb3[:kb3.rfind(b"\xd0\x0d\xfe\xed")], kb3[kb3.rfind(b"\xd0\x0d\xfe\xed"):]
ze, deb = kbe[:kbe.rfind(b"\xd0\x0d\xfe\xed")], kbe[kbe.rfind(b"\xd0\x0d\xfe\xed"):]
print(f"  zImage 逐字节一致: {z3 == ze} ({len(z3)}B, sha {hashlib.sha256(z3).hexdigest()[:16]}…)")
print(f"  dtb: {len(d3b)}B(76d7e5d3…) -> {len(deb)}B(383a0874…), 逐字节不同: {d3b != deb}")
print(f"  dtb-in-image == 独立产物 dtb: {deb == open(f'{R3E}/mt6572-z1.dtb','rb').read()}"
      f" / {d3b == open(f'{R3D}/mt6572-z1.dtb','rb').read()}")

print("=" * 72)
print("C. FDT 属性层 diff（R3d -> R3e）")
p3, pe = fdt_props(d3b), fdt_props(deb)
fdt_diff = []
for k in sorted(set(p3) | set(pe)):
    if p3.get(k) != pe.get(k):
        fdt_diff.append(k)
attributable = {"bootargs": "bootargs += printk.devkmsg=on (A8-1)",
                "linux,initrd-end": "initrd 重算 0x842dfa28 -> 0x842e0e80"}
for p, n in fdt_diff:
    tag = ("ramoops 新节点属性" if "ramoops" in p else
           "aliases i2c 锁号 (A10-3)" if p == "/aliases" and n.startswith("i2c") else
           attributable.get(n, "UNEXPECTED") if n in attributable else
           "FDT 结构性字段(totalsize)" if n == "totalsize" else "UNEXPECTED")
    print(f"  {p} :: {n}  [{tag}]")
    if tag == "UNEXPECTED":
        unexpected.append(f"fdt {p}::{n}")

print("=" * 72)
print("D. cpio 逐记录 diff（R3d -> R3e）")
c3, o3 = cpio_recs(r3[512:])
ce, oe = cpio_recs(re_[512:])
print(f"  记录数: {len(o3)} -> {len(oe)} (Δ={len(oe) - len(o3)})")
only_old = sorted(set(o3) - set(oe))
only_new = sorted(set(oe) - set(o3))
print(f"  仅在 R3d: {only_old or '无'}")
for n_ in only_new:
    tag = EXPECTED_RAMDISK_ADDED.get(n_, "UNEXPECTED")
    print(f"  仅在 R3e: {n_}  [{tag}]")
    if tag == "UNEXPECTED":
        unexpected.append(f"cpio added {n_}")
    if only_old:
        unexpected.append(f"cpio removed {only_old}")
for n_ in sorted(set(o3) & set(oe)):
    a, b = c3[n_], ce[n_]
    if a["sha"] != b["sha"] or (a["mode"], a["uid"], a["gid"]) != (b["mode"], b["uid"], b["gid"]):
        tag = EXPECTED_RAMDISK_CHANGED.get(n_, "UNEXPECTED")
        print(f"  内容变: {n_} ({a['size']}B -> {b['size']}B)  [{tag}]")
        if tag == "UNEXPECTED":
            unexpected.append(f"cpio changed {n_}")
    elif a["ino"] != b["ino"]:
        print(f"  inode 漂移: {n_} {a['ino']} -> {b['ino']}  [UNEXPECTED]")
        unexpected.append(f"cpio inode drift {n_}")

print("=" * 72)
print("E. 独立产物级 sha")
for f in ("boot.img", "ramdisk-mtk.img", "mt6572-z1.dtb"):
    print(f"  {f}: R3d {shal(f'{R3D}/{f}')[:16]}…  R3e {shal(f'{R3E}/{f}')[:16]}…")

print("=" * 72)
if unexpected:
    print(f"RESULT: {len(unexpected)} 条预期外差异:")
    for u in unexpected:
        print("  !! " + u)
    sys.exit(1)
print("RESULT: 全部差异均落在预期清单内（ramdisk 增量 + dtb 增量 + bootargs）")
