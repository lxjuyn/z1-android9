#!/usr/bin/env python3
"""R4 final boot.img 独立终检。

判据出处：任务书第 3 步 + 合并决策 finalDiff。全部格式解析（ANDROID! 头 /
MTK KERNEL+ROOTFS 双段头 / FDT structure blocks / cpio newc）在本文件内自写，
不 import 任何构建代码；基准值全部从 out/z1_v11/boot.img 与
z1a9_gpt/scripts/ramdisk_r4_merge/ 决策源件现场读取。
"""
import gzip
import hashlib
import struct
import sys
from pathlib import Path

REPO = Path("/home/lxj/claude/6572")
R4 = REPO / "out/z1_r4_final/boot.img"
V11 = REPO / "out/z1_v11/boot.img"
R4U = REPO / "out/z1_r4_merge/boot_mainline_r4.img"
SRC = REPO / "z1a9_gpt/scripts/ramdisk_r4_merge"
LOCAL_RD = Path("/tmp/r4_final_build/ramdisk_r4_final.rootfs")
INITRD_START = 0x84100000
PAGE = 2048
MTK_MAGIC = b"\x88\x16\x88\x58"

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------- 自写 boot 头解析 ----------------
def parse_boot(blob):
    assert blob[:8] == b"ANDROID!"
    f = struct.unpack_from("<10I", blob, 8)
    return dict(
        ksize=f[0], kaddr=f[1], rsize=f[2], raddr=f[3], ssize=f[4], saddr=f[5],
        tags=f[6], page=f[7], dtsize=f[8], f10=f[9],
        board=blob[48:64], cmdline=blob[64:64 + 512],
        boot_id=blob[64 + 512:64 + 512 + 32],
        extra_cmdline=blob[64 + 512 + 32:64 + 512 + 32 + 1024],
    )


# ---------------- 自写 FDT structure-block 解析 ----------------
def fdt_chosen(blob):
    magic, totalsize, off_st, off_str = struct.unpack_from(">4I", blob, 0)
    assert magic == 0xD00DFEED, "not an FDT"
    pos, cur, props = off_st, [], {}
    while True:
        (tag,) = struct.unpack_from(">I", blob, pos)
        pos += 4
        if tag == 1:  # FDT_BEGIN_NODE
            e = blob.index(b"\x00", pos)
            cur.append(blob[pos:e].decode())
            pos = (e + 1 + 3) & ~3
        elif tag == 2:  # FDT_END_NODE
            cur.pop()
        elif tag == 3:  # FDT_PROP
            plen, noff = struct.unpack_from(">II", blob, pos)
            pos += 8
            e = blob.index(b"\x00", off_str + noff)
            name = blob[off_str + noff:e].decode()
            val = blob[pos:pos + plen]
            pos = (pos + plen + 3) & ~3
            if "/" + "/".join(n for n in cur if n) == "/chosen":
                props[name] = val
        elif tag == 4:  # FDT_NOP
            continue
        elif tag == 9:  # FDT_END
            break
        else:
            raise ValueError(f"unknown FDT tag {tag:#x} at {pos:#x}")
    return props


# ---------------- 自写 cpio newc 解析 ----------------
def cpio(body):
    recs, pos = [], 0
    while pos < len(body) and body[pos:pos + 6] == b"070701":
        f = [int(body[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        namesize, filesize = f[11], f[6]
        name = body[pos + 110:pos + 110 + namesize - 1].decode()
        he = (pos + 110 + namesize + 3) & ~3
        data = body[he:he + filesize]
        fe = (he + filesize + 3) & ~3
        recs.append(dict(name=name, fields=f, data=data))
        pos = fe
        if name == "TRAILER!!!":
            break
    return recs


def diff_runs(a, b, limit=16):
    n = min(len(a), len(b))
    runs, start = [], None
    for i in range(n):
        if a[i] != b[i]:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, n))
    if len(a) != len(b):
        runs.append((n, max(len(a), len(b))))
    return runs[:limit], len(runs)


# ================= 开始 =================
blob = R4.read_bytes()
v11 = V11.read_bytes()
h, hv = parse_boot(blob), parse_boot(v11)

print(f"== 基本信息")
print(f"R4 final : {R4}  {len(blob)} B  sha256 {sha(blob)}")
print(f"v11 base : {len(v11)} B  sha256 {sha(v11)}")

# 1. ANDROID! magic
check("ANDROID! magic", blob[:8] == b"ANDROID!")

# 2. 头字段 vs v11：10 个 32-bit 字段除 rsize 外逐字段相同；board/cmdline/extra 同
diffs = [k for k in ("ksize", "kaddr", "raddr", "ssize", "saddr", "tags",
                     "page", "dtsize", "f10") if h[k] != hv[k]]
diffs += [k for k in ("board", "cmdline", "extra_cmdline")
          if h[k] != hv[k]]
check("boot 头 10 字段中除 ramdisk_size 外 9 个逐字段==v11", not diffs,
      f"差异字段: {diffs or '无'}")
check("boot 头 ramdisk_size 1973439 -> 1977444（差 4005）",
      h["rsize"] - hv["rsize"] == 4005 and h["rsize"] == 1977444,
      f"{hv['rsize']} -> {h['rsize']}")

# 2b. boot_id：打包器内容指纹，与上游成品一致；相对 v11 变化系 ramdisk/dtb 更新
hu = parse_boot(R4U.read_bytes())
check("boot_id 与上游成品一致（同输入同指纹）",
      h["boot_id"] == hu["boot_id"],
      f"{h['boot_id'].hex()[:40]}… (v11 {hv['boot_id'].hex()[:40]}…，随 ramdisk/dtb 更新重算)")

# 3. 加载地址语义
check("加载地址 (kaddr,raddr,saddr,tags)=(10008000,11000000,10F00000,10000100)",
      (h["kaddr"], h["raddr"], h["saddr"], h["tags"]) ==
      (0x10008000, 0x11000000, 0x10F00000, 0x10000100),
      f"{h['kaddr']:#x},{h['raddr']:#x},{h['saddr']:#x},{h['tags']:#x}")

# 4. MTK 双段头
kernel = blob[PAGE:PAGE + h["ksize"]]
check("MTK KERNEL 段头 magic+长度",
      kernel[:4] == MTK_MAGIC and kernel[8:14] == b"KERNEL"
      and struct.unpack_from("<I", kernel, 4)[0] == h["ksize"] - 512)
ramdisk = blob[PAGE + ((h["ksize"] + PAGE - 1) // PAGE) * PAGE:
               PAGE + ((h["ksize"] + PAGE - 1) // PAGE) * PAGE + h["rsize"]]
check("MTK ROOTFS 段头 magic+tag+长度",
      ramdisk[:4] == MTK_MAGIC and b"ROOTFS" in ramdisk[:32]
      and struct.unpack_from("<I", ramdisk, 4)[0] == h["rsize"] - 512)

# 5. zImage 与 v11 逐字节一致（payload = 512B MTK 头 + zImage + dtb）
zimg, v11zimg = kernel[512:-20407], v11[PAGE + 512:PAGE + 512 + 4207104]
check("zImage 与 v11 逐字节一致", zimg == v11zimg,
      f"len {len(zimg)} sha {sha(zimg)[:16]}… (v11 {sha(v11zimg)[:16]}…)")
check("zImage sha256==59bc73c3…（决策定案）",
      sha(v11zimg).startswith("59bc73c3efe30110"))

# 6. dtb diff 恰 2 字节（FDT 属性大端：initrd-end 0x842e1abf -> 0x842e2a64）
dtb = kernel[512 + len(zimg):]
v11dtb = v11[PAGE + 512 + 4207104:PAGE + 512 + 4207104 + 20407]
runs, total = diff_runs(v11dtb, dtb)
check("dtb 与 v11 dtb 全树 diff 恰 1 run / 2 字节", total == 1 and
      runs and runs[0][1] - runs[0][0] == 2,
      f"runs={runs} v11dtb {len(v11dtb)}B vs {len(dtb)}B")
if runs and runs[0][1] - runs[0][0] == 2:
    s, e = runs[0]
    check("dtb 2 字节差异内容 1abf -> 2a64（initrd-end 大端末两字节）",
          v11dtb[s:e] == b"\x1a\xbf" and dtb[s:e] == b"\x2a\x64",
          f"@{s:#x} {v11dtb[s:e].hex()} -> {dtb[s:e].hex()}")

# 7. FDT /chosen：bootargs==v11==boot 头 cmdline；initrd 算式
ch, chv = fdt_chosen(dtb), fdt_chosen(v11dtb)
check("dtb /chosen bootargs 与 v11 逐字一致", ch["bootargs"] == chv["bootargs"])
check("dtb bootargs == boot 头 cmdline（去 NUL）",
      ch["bootargs"].rstrip(b"\x00") == h["cmdline"].split(b"\x00")[0])
istart, = struct.unpack_from(">I", ch["linux,initrd-start"])
iend, = struct.unpack_from(">I", ch["linux,initrd-end"])
body = h["rsize"] - 512
check("initrd-start == 0x84100000", istart == INITRD_START, f"{istart:#x}")
check("initrd-end == 0x84100000 + body(1976932) == 0x842e2a64",
      iend == INITRD_START + body == 0x842E2A64, f"{iend:#x} body={body}")
check("ROOTFS 头长度字段与 body 互为镜像",
      struct.unpack_from("<I", ramdisk, 4)[0] == body)

# 8. cpio 记录级断言
recs = cpio(gzip.decompress(ramdisk[512:]))
v11roff = PAGE + ((hv["ksize"] + PAGE - 1) // PAGE) * PAGE
v11recs = cpio(gzip.decompress(v11[v11roff:v11roff + hv["rsize"]][512:]))
names = [r["name"] for r in recs if r["name"] != "TRAILER!!!"]
check("cpio 记录总数 74（不含 TRAILER）+ TRAILER 在场",
      len(names) == 74 and any(r["name"] == "TRAILER!!!" for r in recs),
      f"实际 {len(names)} 条")

v11map = {r["name"]: r for r in v11recs}
r4map = {r["name"]: r for r in recs}

# 8a. 5 条 WiFi 记录 == 决策源件逐字节
wifi_expect = {
    "init.z1.network.rc": ("replace", 0o100750, "ee598ea5f742"),
    "z1_wifi_probe.sh": ("replace", None, "d90cc0892fae"),
    "z1_wpa_start.sh": ("replace", 0o755, None),
    "z1_wifi_pre.sh": ("add", 0o755, None),
    "z1_wifi_connect.sh": ("add", 0o755, None),
}
for name, (kind, mode, prefix) in wifi_expect.items():
    src = (SRC / name).read_bytes()
    rec = r4map.get(name)
    ok = rec is not None and rec["data"] == src
    detail = f"{len(rec['data'])}B vs src {len(src)}B" if rec else "记录缺失"
    if ok and prefix:
        ok = sha(rec["data"]).startswith(prefix)
        detail += f" sha {sha(rec['data'])[:12]}…"
    if ok and mode is not None:
        ok = rec["fields"][1] == mode
        detail += f" mode {oct(rec['fields'][1])}"
    check(f"记录 {name} == 决策源件逐字节（{kind}）", ok, detail)

# 8b. wpa_start 头形态：重用 v11 原生追加记录头（mode 0o755，树内 .sh 追加先例
#     z1_gps_prep.sh 同；initramfs 解包 sys_open 忽略类型位，无坑 10 之虞）
rec = r4map["z1_wpa_start.sh"]
check("z1_wpa_start.sh mode=0o755(=v11/w10 原生) 且内容==w10 修复版(2828B)",
      rec["fields"][1] == 0o755 and len(rec["data"]) == 2828
      and rec["data"] == (SRC / "z1_wpa_start.sh").read_bytes()
      and v11map["z1_gps_prep.sh"]["fields"][1] == 0o755)

# 8c. 其余 69 条与 v11 逐条逐字节一致
changed = [n for n in names if n not in wifi_expect]
mismatch = [n for n in changed
            if n not in v11map or v11map[n]["data"] != r4map[n]["data"]
            or v11map[n]["fields"] != r4map[n]["fields"]]
check("其余 69 条与 v11 逐条逐字节一致（含头字段）", not mismatch,
      f"不一致: {mismatch[:5] or '无'}")
check("无意外增删（记录名集合 = v11 + 2 条新增）",
      set(names) - set(v11map) == {"z1_wifi_pre.sh", "z1_wifi_connect.sh"})

# 8d. GPS 段在位
g = v11map.get("z1_gps_prep.sh")
check("GPS 段 z1_gps_prep.sh 在位（v11 独有，1071B）",
      g is not None and len(g["data"]) == 1071 and
      r4map["z1_gps_prep.sh"]["data"] == g["data"])

# 8e. 两个外编 ko 在位且 vermagic 匹配
for ko in ("z1_cst836u.ko", "z1_gpio_hunt.ko"):
    rec = r4map.get(ko)
    vm = b"vermagic=7.0.0-rc7-z1+" in rec["data"] if rec else b""
    check(f"{ko} 在位且 vermagic=7.0.0-rc7-z1+",
          rec is not None and vm,
          f"{len(rec['data'])}B" if rec else "缺失")

# 8f. network.rc 内容级闸：B1 路径 / seclabel / B3 段 / GPS 服务保留
rc = r4map["init.z1.network.rc"]["data"]
rc_active = b"\n".join(l.split(b"#", 1)[0] for l in rc.splitlines())
check("[B1] wpa_supplicant exec 路径为 ramdisk 根 /z1_wpa_start.sh（代码区无坏路径）",
      b"service z1_wpa_supplicant /system/bin/sh /z1_wpa_start.sh" in rc
      and b"/system/bin/z1_wpa_start.sh" not in rc_active)
check("[B2] 两条服务 seclabel 均为 u:r:init:s0 且无 su 域",
      rc.count(b"seclabel u:r:init:s0") >= 2 and b"u:r:su:s0" not in rc)
check("[B3] on post-fs exec /z1_wifi_pre.sh 段在场（w10 原生完整形式）",
      b"exec u:r:init:s0 root root -- /system/bin/sh /z1_wifi_pre.sh" in rc)
check("v11 GPS/audio/diag 段保留（z1_mnld/z1_gps_prep.sh/load_audio/diag_onboot）",
      b"service z1_mnld" in rc and b"z1_gps_prep.sh" in rc and
      b"load_audio" in rc and b"z1_diag_onboot" in rc)

# 9. 大小与语义自洽
check("镜像 ≤ 6MiB（6291456B）", len(blob) <= 6 * 1024 * 1024,
      f"{len(blob)} B")
check("ramdisk 段 == 本次构建产物逐字节",
      ramdisk == LOCAL_RD.read_bytes(), f"{len(ramdisk)}B")

print()
if fails:
    print(f"共 {len(fails)} 项 FAIL: {fails}")
    sys.exit(1)
print("全部判据 PASS")
