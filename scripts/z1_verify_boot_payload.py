#!/usr/bin/env python3
"""Compare two packed Z1 Android boot images payload-by-payload.

Prints the boot header fields, the byte ranges that differ inside the kernel
section (zImage+appended DTB) and inside the ramdisk, and a record-level diff
of the two ramdisks (name / size / sha / mode). Read-only.

    z1_verify_boot_payload.py out/z1_v8/boot.img out/z1_wifi_v10/boot.img
"""

import gzip
import hashlib
import struct
import sys
from pathlib import Path

PAGE = 2048
MTK_MAGIC = b"\x88\x16\x88\x58"


def hdr(blob):
    if blob[:8] != b"ANDROID!":
        sys.exit("not an Android boot image")
    f = struct.unpack_from("<10I", blob, 8)
    return dict(ksize=f[0], kaddr=f[1], rsize=f[2], raddr=f[3], ssize=f[4],
                saddr=f[5], tags=f[6], page=f[7], dtsize=f[8], extra=f[9],
                cmdline=blob[64:64 + 512].split(b"\x00")[0].decode(),
                extra_cmdline=blob[64 + 512 + 32:64 + 512 + 32 + 1024]
                .split(b"\x00")[0].decode())


def sections(blob):
    h = hdr(blob)
    p = h["page"]
    kernel = blob[p:p + h["ksize"]]
    roff = p + ((h["ksize"] + p - 1) // p) * p
    ramdisk = blob[roff:roff + h["rsize"]]
    return h, kernel, ramdisk


def records(rootfs):
    body = gzip.decompress(rootfs[512:])
    pos, out = 0, []
    while pos < len(body) and body[pos:pos + 6] == b"070701":
        f = [int(body[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        ns, fs = f[11], f[6]
        name = body[pos + 110:pos + 110 + ns - 1].decode()
        he = (pos + 110 + ns + 3) & ~3
        fe = (he + fs + 3) & ~3
        out.append((name, fs, hashlib.sha256(body[he:he + fs]).hexdigest()[:12],
                    oct(f[1])))
        pos = fe
        if name == "TRAILER!!!":
            break
    return out, len(body)


def diffs(a, b, limit=24):
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


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    pa, pb = Path(sys.argv[1]), Path(sys.argv[2])
    blob_a, blob_b = pa.read_bytes(), pb.read_bytes()
    ha, ka, ra = sections(blob_a)
    hb, kb, rb = sections(blob_b)

    print(f"--- headers\n{pa.name}: {len(blob_a)} b  {ha}")
    print(f"{pb.name}: {len(blob_b)} b  {hb}")
    for k in ("cmdline", "extra_cmdline", "page", "dtsize", "ssize",
              "kaddr", "raddr", "tags"):
        flag = "SAME" if ha[k] == hb[k] else "DIFF"
        print(f"  {flag:4s} {k}: {ha[k]!r}" + ("" if flag == "SAME" else f" -> {hb[k]!r}"))

    runs, total = diffs(ka, kb)
    print(f"\n--- kernel section (zImage+DTB, MTK KERNEL header included)")
    print(f"  len {len(ka)} vs {len(kb)}  sha {hashlib.sha256(ka).hexdigest()[:16]}"
          f" vs {hashlib.sha256(kb).hexdigest()[:16]}")
    print(f"  differing runs: {total}")
    for s, e in runs:
        print(f"    [{s:#08x},{e:#08x}) {e - s} b: "
              f"{ka[s:e].hex()} -> {kb[s:e].hex()}")

    print(f"\n--- ramdisk section")
    print(f"  len {len(ra)} vs {len(rb)}  sha {hashlib.sha256(ra).hexdigest()[:16]}"
          f" vs {hashlib.sha256(rb).hexdigest()[:16]}")
    for tag, r in (("A", ra), ("B", rb)):
        body_len = struct.unpack_from("<I", r, 4)[0]
        print(f"  {tag} rootfs: magic={r[:4] == MTK_MAGIC} tag={r[8:14]!r} "
              f"hdr_len={body_len} stored_body={len(r) - 512}")

    ra_, ba = records(ra)
    rb_, bb = records(rb)
    print(f"\n--- cpio records (cpio body {ba} -> {bb} b)")
    da = {n: (s, h, m) for n, s, h, m in ra_}
    db = {n: (s, h, m) for n, s, h, m in rb_}
    for n, (s, h, m) in db.items():
        if n not in da:
            print(f"  ADDED    {n:24s} {s:8d} {m}")
        elif da[n][1] != h:
            print(f"  CHANGED  {n:24s} {da[n][0]} -> {s} b  {da[n][2]} -> {m}")
    for n in da:
        if n not in db:
            print(f"  REMOVED  {n}")
    same = sum(1 for n in db if n in da and da[n][1] == db[n][1])
    print(f"  unchanged: {same} of {len(ra_)}/{len(rb_)} records")


if __name__ == "__main__":
    main()
