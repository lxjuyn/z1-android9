#!/usr/bin/env python3
"""Build a host-verified recovery candidate from the audited cmpfix boot.

Preserve its zImage and r14 graphics binaries. Replace the stage script,
put the storage guard before post-fs writers, remove the legacy HWC copy,
start audio loading after installation, and update only chosen/linux,initrd-end
in the appended DTB. Never flash.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

from z1_ramdisk_edit import encode, parse

BASE_SHA = "6388be8659e3b77c3facc233d1eb604343a2d9defe6a014540f7675e44bf0a56"
MTK = bytes.fromhex("88168858")
LIMIT = 6 * 1024 * 1024
AUDIO_LOAD = "    exec_background u:r:init:s0 root root -- /system/bin/sh /system/lib/modules/load_audio.sh\n"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pad(data, page):
    return data + bytes((-len(data)) % page)


def split_boot(data):
    require(data[:8] == b"ANDROID!", "missing Android header")
    require(len(data) <= LIMIT, "boot exceeds partition")
    ks, rs, ss, page, ds = [struct.unpack_from("<I", data, x)[0]
                            for x in (8, 16, 24, 36, 40)]
    require(page == 2048 and ss == ds == 0, "unsupported boot layout")
    roff = page + ((ks + page - 1) // page) * page
    require(roff + rs <= len(data), "truncated boot")
    kernel, rootfs = data[page:page + ks], data[roff:roff + rs]
    for section, tag in ((kernel, b"KERNEL"), (rootfs, b"ROOTFS")):
        require(section[:4] == MTK and section[8:14] == tag, "bad MTK header")
        require(struct.unpack_from("<I", section, 4)[0] == len(section) - 512,
                "MTK length mismatch")
    zlen = struct.unpack_from("<I", kernel, 512 + 0x2c)[0]
    zimage, dtb = kernel[512:512 + zlen], kernel[512 + zlen:]
    require(dtb[:4] == bytes.fromhex("d00dfeed"), "missing appended DTB")
    require(struct.unpack_from(">I", dtb, 4)[0] == len(dtb), "DTB length mismatch")
    return data[:page], kernel[:512], zimage, dtb, rootfs


def cpio(rootfs):
    body = gzip.decompress(rootfs[512:])
    records, end = parse(body)
    require(records and records[-1]["name"] == "TRAILER!!!", "missing cpio trailer")
    require(not any(body[end:]), "unexpected data after cpio trailer")
    names = [r["name"] for r in records]
    require(len(set(names)) == len(names), "duplicate cpio names")
    require(all(r["file_end"] <= len(body) for r in records), "truncated cpio")
    return body, records


def contents(body, records):
    return {r["name"]: body[r["hdr_end"]:r["hdr_end"] + r["size"]] for r in records}


def replace_once(text, old, new):
    require(text.count(old) == 1, "unexpected rc shape: " + repr(old[:80]))
    return text.replace(old, new, 1)


def patch_rc(text):
    text = replace_once(text, "on fs\n    mount_all /fstab.z1\n",
        "on fs\n    mount_all /fstab.z1\n"
        "    # Stop before post-fs/cache and post-fs-data writers on failure.\n"
        "    setprop sys.z1.storage_ready 0\n"
        "    exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh --storage-only\n"
        "    wait_for_prop sys.z1.storage_ready 1\n")
    for line in (
        "    exec u:r:init:s0 root root -- /system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so\n",
        "    exec u:r:init:s0 root root -- /system/bin/chmod 0644 /vendor/lib/hw/hwcomposer.z1.so\n",
    ):
        text = replace_once(text, line, "")
    text = replace_once(text,
        "    exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh\n"
        "    start z1_rootd\n", "")
    text = replace_once(text, "on post-fs-data\n    swapon_all /fstab.z1\n",
        "on post-fs-data\n"
        "    # Install before launching ART helpers or any graphics writer.\n"
        "    setprop z1stage.status installing\n"
        "    exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh --skip-storage\n"
        "    wait_for_prop z1stage.status ready\n"
        "    # Load the installed audio modules, including on the first boot.\n"
        + AUDIO_LOAD +
        "    start z1_rootd\n"
        "    swapon_all /fstab.z1\n")
    # The old deployment block describes the removed duplicate installer.
    begin = text.index("# Why a separate \"on post-fs-data\" block")
    end = text.index("# Root command daemon", begin)
    text = text[:begin] + "# Storage and staging actions are installed above.\n\n" + text[end:]
    text = replace_once(text, "service z1_rootd /system/bin/sh /z1rootd.sh\n    class core\n",
        "service z1_rootd /system/bin/sh /z1rootd.sh\n    class core\n    disabled\n")
    verify_rc(text)
    return text


def verify_rc(text):
    require(text.count("/z1stage_boot.sh --storage-only") == 1, "storage guard count")
    require(text.count("/z1stage_boot.sh --skip-storage") == 1, "installer count")
    require(text.count("wait_for_prop sys.z1.storage_ready 1") == 1, "missing storage gate")
    require(text.count("wait_for_prop z1stage.status ready") == 1, "missing installation gate")
    require(text.count(AUDIO_LOAD) == 1, "audio loader count")
    require("/system/bin/cp /hwcomposer.z1.so" not in text, "legacy installer remains")
    require(text.index("--storage-only") < text.index("wait_for_prop sys.z1.storage_ready 1")
            < text.index("on post-fs-data"), "guard runs too late")
    require(text.index("--skip-storage") < text.index("wait_for_prop z1stage.status ready")
            < text.index(AUDIO_LOAD) < text.index("start z1_rootd")
            < text.index("/z1_art_seed.sh"), "audio/ART races installer")


def patch_network_rc(text):
    # The original loader runs in post-fs and otherwise wins the race against
    # the post-fs-data module installer; it skips already loaded modules later.
    text = replace_once(text, "on post-fs\n" + AUDIO_LOAD,
                        "# Audio module loading follows the guarded installer in init.z1.rc.\n")
    require(not any("load_audio.sh" in line and not line.lstrip().startswith("#")
                    for line in text.splitlines()), "early audio loader remains")
    return text


def edit_rootfs(rootfs, replacements):
    body, records = cpio(rootfs)
    existing = {r["name"] for r in records}
    require(set(replacements) <= existing, "missing replacement record")
    out = bytearray()
    for r in records:
        if r["name"] in replacements:
            require(r["fields"][1] & 0o170000 == 0o100000, "replacement not regular")
            out += encode(body[r["pos"]:r["hdr_end"]], replacements[r["name"]])
        else:
            out += body[r["pos"]:r["file_end"]]
    out += body[records[-1]["file_end"]:]
    gz = gzip.compress(bytes(out), compresslevel=9, mtime=0)
    header = bytearray(rootfs[:512])
    struct.pack_into("<I", header, 4, len(gz))
    return bytes(header) + gz


def dt_read(path, node, prop, kind="x"):
    return subprocess.check_output(["fdtget", "-t", kind, str(path), node, prop], text=True).strip()


def make_image(header, kh, zimage, dtb, rootfs):
    kh = bytearray(kh)
    struct.pack_into("<I", kh, 4, len(zimage) + len(dtb))
    kernel = bytes(kh) + zimage + dtb
    header = bytearray(header)
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 16, len(rootfs))
    # Android boot v0 ID includes each section's little-endian size.
    digest = hashlib.sha1()
    for part in (kernel, rootfs, b""):
        digest.update(part)
        digest.update(struct.pack("<I", len(part)))
    header[576:608] = digest.digest() + bytes(12)
    image = bytes(header) + pad(kernel, 2048) + pad(rootfs, 2048)
    require(len(image) <= LIMIT, "boot exceeds 6 MiB")
    split_boot(image)
    return image


def build(base, stage, output):
    raw = base.read_bytes()
    require(sha(raw) == BASE_SHA, "base must be the audited out/boot_cmpfix.img")
    header, kh, zimage, dtb, rootfs = split_boot(raw)
    old_body, old_records = cpio(rootfs)
    old = contents(old_body, old_records)
    require(b"wait_for_prop" in old["init"], "init lacks wait_for_prop builtin")
    script = stage.read_bytes()
    for token in (b"--storage-only", b"--skip-storage", b"sys.z1.storage_ready"):
        require(token in script, "stage script lacks required storage interface")
    rc = patch_rc(old["init.z1.rc"].decode()).encode()
    network_rc = patch_network_rc(old["init.z1.network.rc"].decode()).encode()
    updated = edit_rootfs(rootfs, {"init.z1.rc": rc, "init.z1.network.rc": network_rc,
                                   "z1stage_boot.sh": script})
    require(not output.exists(), "output directory already exists; use a new name")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".z1-recovery-", dir=output.parent) as td:
        work = Path(td)
        dt = work / "appended.dtb"
        dt.write_bytes(dtb)
        start = int(dt_read(dt, "/chosen", "linux,initrd-start"), 16)
        require(start == 0x84100000, "unexpected LK initrd address")
        end = start + len(updated) - 512
        subprocess.run(["fdtput", "-t", "x", str(dt), "/chosen", "linux,initrd-end", f"{end:x}"], check=True)
        new_dt = dt.read_bytes()
        # Reverting this one property must recreate the original DTB exactly.
        original_dt = work / "original.dtb"
        original_dt.write_bytes(dtb)
        original_end = dt_read(original_dt, "/chosen", "linux,initrd-end")
        check_dt = work / "check.dtb"
        check_dt.write_bytes(new_dt)
        subprocess.run(["fdtput", "-t", "x", str(check_dt), "/chosen", "linux,initrd-end", original_end], check=True)
        require(check_dt.read_bytes() == dtb, "DTB changed beyond initrd-end")
        image = make_image(header, kh, zimage, new_dt, updated)
        _, _, zi2, dt2, rd2 = split_boot(image)
        require(zi2 == zimage and dt2 == new_dt and rd2 == updated, "packed payload mismatch")
        new_body, new_records = cpio(rd2)
        new = contents(new_body, new_records)
        require(set(old) == set(new), "cpio name set changed")
        changed = sorted(n for n in old if old[n] != new[n])
        require(changed == ["init.z1.network.rc", "init.z1.rc", "z1stage_boot.sh"],
                "unexpected cpio change")
        for a, b in zip(old_records, new_records):
            require(a["name"] == b["name"], "record order changed")
            if a["name"] not in changed:
                require(old_body[a["pos"]:a["file_end"]] == new_body[b["pos"]:b["file_end"]],
                        "untouched cpio record changed: " + a["name"])
        manifest = {
            "status": "HOST_VERIFIED_NOT_FLASHED", "base_sha256": sha(raw),
            "boot_sha256": sha(image), "boot_bytes": len(image),
            "builder": {
                "path": "scripts/z1_prepare_recovery_boot.py",
                "sha256": sha(Path(__file__).read_bytes()),
            },
            "zimage_sha256": sha(zimage), "zimage_preserved": True,
            "rootfs_sha256": sha(updated), "rootfs_body_bytes": len(updated) - 512,
            "initrd_start": hex(start), "initrd_end": hex(end),
            "dtb_sha256": sha(new_dt), "dtb_changed_properties": ["/chosen/linux,initrd-end"],
            "cpio_changed_records": changed,
            "graphics": {n: {"sha256": sha(new[n]), "bytes": len(new[n]), "preserved": True}
                         for n in ("hwcomposer.z1.so", "gralloc.z1.so")},
            "stage_script_sha256": sha(script),
            "audio_loading": "background loader starts only after stage.status ready; completion still needs board verification",
            "storage_failure_behavior": "init waits before post-fs; no automatic format or reboot",
            "needs_board_validation": ["early storage guard", "fsck recovery", "cold boot", "r14 graphics", "readback"],
        }
        # Publish a complete directory only after all reverse checks succeed.
        (work / "boot.img").write_bytes(image)
        (work / "rootfs.bin").write_bytes(updated)
        (work / "init.z1.rc").write_bytes(rc)
        (work / "init.z1.network.rc").write_bytes(network_rc)
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (work / "SHA256SUMS").write_text("".join(f"{sha((work/n).read_bytes())}  {n}\n" for n in
                                               ("boot.img", "rootfs.bin", "appended.dtb", "init.z1.rc", "init.z1.network.rc", "manifest.json")))
        check_dt.unlink()
        original_dt.unlink()
        os.rename(work, output)
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--stage-script", type=Path, default=Path(__file__).parent / "z1deploy/z1stage_boot.sh")
    ap.add_argument("--output-dir", required=True, type=Path)
    args = ap.parse_args()
    try:
        print(json.dumps(build(args.base, args.stage_script, args.output_dir), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        ap.exit(1, f"recovery build failed: {exc}\n")


if __name__ == "__main__":
    main()
