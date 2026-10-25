#!/usr/bin/env python3
"""独立 cpio(newc) 逐记录对比：回归 ramdisk vs R3c 基线。不复用 builder 代码。"""
import gzip, hashlib, sys

def newc_records(blob):
    """解析 newc 流: 返回 [(name, mode, uid, gid, nlink, filesize, sha256(data), data)]"""
    out, off = [], 0
    while off < len(blob):
        if blob[off:off+6] != b"070701":
            raise ValueError(f"bad newc magic at {off:#x}")
        f = [int(blob[off+6+i*8:off+14+i*8], 16) for i in range(13)]
        ino, mode, uid, gid, nlink, mtime, size = f[0], f[1], f[2], f[3], f[4], f[5], f[6]
        namesize = f[11]
        name_off = off + 110
        name = blob[name_off:name_off+namesize-1].decode()
        data_off = (name_off + namesize + 3) & ~3
        data = blob[data_off:data_off+size]
        if len(data) != size:
            raise ValueError(f"truncated record {name}")
        out.append(dict(name=name, mode=mode, uid=uid, gid=gid, nlink=nlink,
                        size=size, sha=hashlib.sha256(data).hexdigest(), data=data))
        off = (data_off + size + 3) & ~3
        if name == "TRAILER!!!":
            break
    return out

def load(path):
    raw = open(path, "rb").read()
    assert raw[:4] == bytes.fromhex("88168858"), f"{path}: no MTK ROOTFS magic"
    body_len = int.from_bytes(raw[4:8], "little")
    body = raw[512:]
    assert len(body) == body_len, f"{path}: MTK hdr len {body_len} != body {len(body)}"
    return newc_records(gzip.decompress(body))

a, b = load(sys.argv[1]), load(sys.argv[2])  # a=old(R3c), b=new(regression)
print(f"R3c records={len(a)}  new records={len(b)}")
da = {r['name']: r for r in a}; db = {r['name']: r for r in b}
print("only-in-R3c:", sorted(set(da) - set(db)))
print("only-in-new :", sorted(set(db) - set(da)))
diffs = []
for n in sorted(set(da) & set(db)):
    x, y = da[n], db[n]
    why = []
    if x['sha'] != y['sha']: why.append("content")
    if (x['mode'], x['uid'], x['gid']) != (y['mode'], y['uid'], y['gid']):
        why.append(f"meta {x['mode']:o}/{x['uid']}:{x['gid']} -> {y['mode']:o}/{y['uid']}:{y['gid']}")
    if why:
        diffs.append((n, why, x, y))
print(f"\n== differing records: {len(diffs)} ==")
for n, why, x, y in diffs:
    print(f"  {n}: {','.join(why)}  ({x['size']}B -> {y['size']}B)")

# 导出三条差异记录的两版内容供人工 diff
import pathlib
for n in ("init.z1.rc", "z1_touch_load.sh"):
    for side, recs in (("r3c", da), ("new", db)):
        pathlib.Path(f"/tmp/diff_{side}_{n}").write_bytes(recs[n]['data'])
for side, recs in (("r3c", da), ("new", db)):
    pathlib.Path(f"/tmp/diff_{side}_z1_cst836u.ko").write_bytes(recs["z1_cst836u.ko"]['data'])
