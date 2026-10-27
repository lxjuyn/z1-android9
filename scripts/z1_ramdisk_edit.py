#!/usr/bin/env python3
"""Record-level editor for the Z1 MTK ROOTFS ramdisk.

Keeps every untouched cpio record byte-identical (the only safe way to edit
this ramdisk: a full cpio repack broke boot in earlier rounds). Supports
replacing an existing record's content and adding new records at the root.

Headers are reused from the original record when replacing; added records
borrow the header of a same-type record so inode/mode/uid/gid stay sane.
"""
import argparse
import gzip
import struct
import sys

ALIGN = 4


def parse(body):
    recs = []
    pos = 0
    while pos < len(body) and body[pos:pos + 6] == b'070701':
        fields = [int(body[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        namesize, filesize = fields[11], fields[6]
        name = body[pos + 110:pos + 110 + namesize - 1].decode()
        hdr_end = (pos + 110 + namesize + ALIGN - 1) & ~(ALIGN - 1)
        file_end = (hdr_end + filesize + ALIGN - 1) & ~(ALIGN - 1)
        recs.append(dict(name=name, size=filesize, pos=pos, hdr_end=hdr_end,
                         file_end=file_end, fields=fields))
        pos = file_end
        if name == 'TRAILER!!!':
            break
    return recs, pos


def set_size(hdr, size):
    hdr = bytearray(hdr)
    hdr[54:62] = ("%08X" % size).encode()
    return bytes(hdr)


def encode(hdr, data):
    total = len(hdr) + len(data)
    pad = ((total + ALIGN - 1) & ~(ALIGN - 1)) - total
    return set_size(hdr, len(data)) + data + b"\x00" * pad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--replace", action="append", default=[], metavar="NAME=FILE",
                    help="replace record NAME with contents of FILE")
    ap.add_argument("--add", action="append", default=[], metavar="NAME=FILE",
                    help="add a root-level record NAME with contents of FILE")
    ap.add_argument("--mode", action="append", default=[], metavar="NAME=0755")
    args = ap.parse_args()

    src = open(args.input, 'rb').read()
    header = src[:512]
    body = gzip.decompress(src[512:])
    recs, end = parse(body)
    trailer = body[recs[-1]['file_end']:]

    repl = {}
    for item in args.replace:
        name, path = item.split('=', 1)
        repl[name] = open(path, 'rb').read()
    adds = []
    for item in args.add:
        name, path = item.split('=', 1)
        adds.append((name, open(path, 'rb').read()))
    modes = {}
    for item in args.mode:
        name, val = item.split('=', 1)
        modes[name] = int(val, 8)

    out = bytearray()
    seen = set()
    for r in recs:
        content = body[r['hdr_end']:r['hdr_end'] + r['size']]
        hdr = body[r['pos']:r['hdr_end']]
        if r['name'] in repl:
            content = repl[r['name']]
            seen.add(r['name'])
        out += encode(hdr, content)

    missing = set(repl) - seen
    if missing:
        sys.exit(f"records not found: {sorted(missing)}")

    # Insert new records just before TRAILER, borrowing a regular-file header.
    base = next(r for r in recs if r['name'].endswith('.sh'))
    base_hdr = bytearray(body[base['pos']:base['hdr_end']])
    # Rewrite the name inside the borrowed header: name starts at 110.
    old_name_len = base['hdr_end'] - base['pos'] - 110
    trailer_rec_start = len(out) - len(encode(body[recs[-1]['pos']:recs[-1]['hdr_end']],
                                              body[recs[-1]['hdr_end']:recs[-1]['file_end']]))
    add_bytes = bytearray()
    for name, data in adds:
        nbytes = name.encode() + b"\x00"
        # build a fresh header: copy base but replace name field
        fields = list(base['fields'])
        fields[11] = len(nbytes)          # namesize
        fields[6] = 0                     # filesize, set by encode()
        head = bytearray(110)
        head[0:6] = b'070701'
        for i, v in enumerate(fields):
            head[6 + i * 8:14 + i * 8] = ("%08X" % v).encode()
        namepad = ((110 + len(nbytes) + ALIGN - 1) & ~(ALIGN - 1)) - (110 + len(nbytes))
        hdr = bytes(head) + nbytes + b"\x00" * namepad
        if name in modes:
            m = ("%08X" % modes[name]).encode()
            hdr = bytearray(hdr)
            hdr[14:22] = m
            hdr = bytes(hdr)
        add_bytes += encode(hdr, data)

    out = out[:trailer_rec_start] + add_bytes + out[trailer_rec_start:]
    out += trailer

    buf = gzip.compress(bytes(out), 9, mtime=0)
    hdr = bytearray(header)
    struct.pack_into("<I", hdr, 4, len(buf))
    open(args.output, 'wb').write(bytes(hdr) + buf)
    body_size = len(buf)
    print(f"wrote {args.output}: {len(hdr) + len(buf)} bytes, initrd-end 0x{0x84100000 + body_size:x}")


if __name__ == "__main__":
    main()
