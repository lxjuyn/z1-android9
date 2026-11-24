#!/usr/bin/env python3
"""Build and verify a separate R7 donor-Wi-Fi test pair. Does not flash."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from z1_build_r7_bt_cold_boot import BASE_SHA, SYSTEM_SHA, build as cold_boot
from z1_prepare_recovery_boot import contents, cpio, dt_read, edit_rootfs, make_image, require, sha, split_boot

DONOR_SHA = "bc7e05250c6849e883d66856470e185e5cb61e71cd56675c01fd441cd9493088"
SERVICE_SHA = "759c5d0d3bac9609dee9900a658ce7a15b4ba5d9ffc78b9217c9ca875d9abe44"
HAL = "/vendor/lib/libwifi-hal.so"
ORIGINAL = "/vendor/lib/libwmt-orig.so"
SERVICE = "/vendor/bin/hw/android.hardware.wifi@1.0-service"
SERVICE_RC = "/vendor/etc/init/android.hardware.wifi@1.0-service.rc"
LOADER = "/bin/z1_linux7_radio_init.sh"
FINISH = "z1_radio_finish.sh"


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(args, **kwargs):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, **kwargs)


def debugfs(image, instruction, work, write=False):
    return command(["debugfs", *(["-w"] if write else []), "-R", instruction, str(image)],
                   cwd=work)


def dump(image, source, path):
    require(not path.exists(), f"unexpected existing {path.name}")
    debugfs(image, f"dump {source} {path.name}", path.parent)
    require(path.is_file() and path.stat().st_size, f"missing image file: {source}")
    return path.read_bytes()


def check_metadata(image, target, mode, label, work):
    report = debugfs(image, f"stat {target}", work).decode()
    require(re.search(r"Type: regular\s+Mode:\s+" + mode, report) and
            re.search(r"User:\s+0\s+Group:\s+0(?:\s|$)", report) and
            f'= "{label}"' in report, f"wrong metadata for {target}")


def install(image, source, target, mode, label, work, replace):
    if replace:
        check_metadata(image, target, mode, label, work)
        debugfs(image, f"rm {target}", work, True)
    label_file = work / "selinux-label"
    label_file.write_bytes(label.encode())
    for instruction in (
        f"write {source.name} {target}",
        f"sif {target} mode 0100{mode[1:]}",
        f"sif {target} uid 0",
        f"sif {target} gid 0",
        f"ea_set -f {label_file.name} {target} security.selinux",
    ):
        debugfs(image, instruction, work, True)
    check_metadata(image, target, mode, label, work)
    require(dump(image, target, work / "readback") == source.read_bytes(),
            f"wrong contents for {target}")
    (work / "readback").unlink()


def replace_once(data, old, new):
    require(data.count(old) == 1, f"unexpected source shape for {old!r}")
    return data.replace(old, new, 1)


def build_boot(original, work):
    header, kh, kernel, old_dtb, rootfs = split_boot(cold_boot(original))
    entries = contents(*cpio(rootfs))
    # Both startup scripts can set the ACL. Only this Wi-Fi device changes group.
    finish = replace_once(entries[FINISH],
        b'if [ -c /dev/wmtWifi ]; then\n'
        b'    /system/bin/chmod 0660 /dev/wmtWifi 2>/dev/null || true\n'
        b'    /system/bin/chown 1000:1000 /dev/wmtWifi 2>/dev/null || true\n',
        b'if [ -c /dev/wmtWifi ] &&\n'
        b'    /system/bin/chmod 0660 /dev/wmtWifi 2>/dev/null &&\n'
        b'    /system/bin/chown 1000:1010 /dev/wmtWifi 2>/dev/null; then\n')
    rootfs = edit_rootfs(rootfs, {FINISH: finish})
    dtb = work / "appended.dtb"
    dtb.write_bytes(old_dtb)
    start = int(dt_read(dtb, "/chosen", "linux,initrd-start"), 16)
    old_end = int(dt_read(dtb, "/chosen", "linux,initrd-end"), 16)
    new_end = start + len(rootfs) - 512
    edit = ["fdtput", "-t", "x", str(dtb), "/chosen", "linux,initrd-end"]
    command([*edit, f"{new_end:x}"])
    require(int(dt_read(dtb, "/chosen", "linux,initrd-end"), 16) == new_end,
            "new initrd end mismatch")
    new_dtb = dtb.read_bytes()
    command([*edit, f"{old_end:x}"])
    require(dtb.read_bytes() == old_dtb, "unexpected DTB change")
    output = make_image(header, kh, kernel, new_dtb, rootfs)
    _, _, verified_kernel, verified_dtb, verified_rootfs = split_boot(output)
    require((verified_kernel, verified_dtb) == (kernel, new_dtb) and
            contents(*cpio(verified_rootfs)) == (entries | {FINISH: finish}),
            "unplanned boot payload change")
    return output


def compile_and_probe(image, work, cc, donor):
    guest = work / "root"
    for directory in ("system/bin", "system/lib", "vendor/lib"):
        (guest / directory).mkdir(parents=True)
    for name in ("libc", "libdl", "libm", "libbase", "libcutils", "liblog", "libutils",
                 "libc++", "libvndksupport", "libprocessgroup", "libnl"):
        dump(image, f"/lib/{name}.so", guest / f"system/lib/{name}.so")
    dump(image, "/bin/linker", guest / "system/bin/linker")
    (guest / "system/bin/linker").chmod(0o755)
    vendor = guest / "vendor/lib"
    system = guest / "system/lib"
    (vendor / Path(ORIGINAL).name).write_bytes(donor)
    command([cc, "-O2", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
             "-Wl,-z,defs,-soname,libwifi-hal.so", f"-L{vendor}", f"-L{system}",
             "-Wl,--no-as-needed", "-l:libwmt-orig.so", "-Wl,--as-needed", "-lcutils",
             "-llog", "-o", str(vendor / "libwifi-hal.so"), str(HERE / "wmt_wifi.c")])
    command([cc, "-O2", "-Wall", "-Wextra", "-Werror", "-fno-builtin", "-fPIE",
             "-pie", "-rdynamic", "-o", str(guest / "probe"), str(HERE / "probe.c"), "-ldl"])
    qemu = command(["qemu-arm", "-L", str(guest), "-E",
                    f"LD_LIBRARY_PATH={system}:{vendor}", str(guest / "probe")])
    return (vendor / "libwifi-hal.so").read_bytes(), qemu


def build(boot, system, output, cc):
    require(file_sha(boot) == BASE_SHA and file_sha(system) == SYSTEM_SHA,
            "expected the exact installed R7 core-wake/livegain pair")
    require(not output.exists(), "output directory already exists")
    with tempfile.TemporaryDirectory(prefix=".z1-r7-wmt-", dir=output.parent) as temp:
        work = Path(temp)
        original = dump(system, HAL, work / "original")
        require(sha(original) == DONOR_SHA, "unexpected donor Wi-Fi ELF")
        service = dump(system, SERVICE, work / "service")
        require(sha(service) == SERVICE_SHA, "unexpected donor Wi-Fi service")
        service_rc = dump(system, SERVICE_RC, work / "wifi-service.rc")
        require(service_rc.count(b"    user wifi\n    group wifi gps\n") == 1,
                "unexpected Wi-Fi service credentials")
        for filename in ("nvram/WIFI", "regulatory.db"):
            dump(system, f"/etc/firmware/{filename}", work / Path(filename).name)
        donor = replace_once(original, b"libwifi-hal.so\0", b"libwmt-orig.so\0")
        shim, probe_log = compile_and_probe(system, work, cc, donor)

        candidate = work / "candidate"
        candidate.mkdir()
        (candidate / "boot.img").write_bytes(build_boot(boot.read_bytes(), work))
        (candidate / "TESTS.txt").write_bytes(probe_log)
        (work / "shim.so").write_bytes(shim)
        (work / "donor.so").write_bytes(donor)
        loader = dump(system, LOADER, work / "old-loader.sh")
        (work / "loader.sh").write_bytes(replace_once(
            loader, b"chown 1000 1000 /dev/wmtWifi", b"chown 1000:1010 /dev/wmtWifi"))
        system_out = candidate / "system.img"
        command(["cp", "--reflink=auto", "--sparse=always", system, system_out])
        install(system_out, work / "shim.so", HAL, "0644", "u:object_r:vendor_file:s0", work, True)
        install(system_out, work / "donor.so", ORIGINAL, "0644", "u:object_r:vendor_file:s0", work, False)
        install(system_out, work / "loader.sh", LOADER, "0755", "u:object_r:system_file:s0", work, True)
        require(dump(system_out, SERVICE, work / "readback-service") == service and
                dump(system_out, SERVICE_RC, work / "readback-service.rc") == service_rc,
                "unexpected Wi-Fi service change")
        fsck = command(["e2fsck", "-fn", str(system_out)])
        with (candidate / "TESTS.txt").open("ab") as stream:
            stream.write(fsck)
        with (candidate / "SHA256SUMS").open("w") as stream:
            for name in ("boot.img", "system.img"):
                print(f"{file_sha(candidate / name)}  {name}", file=stream)
        os.rename(candidate, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("boot", "system", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--ndk", type=Path, default=REPO.parent / "android-ndk-r26d")
    args = parser.parse_args()
    cc = args.ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang"
    require(cc.is_file() and shutil.which("qemu-arm"), "missing API 28 compiler or qemu-arm")
    build(*(path.resolve() for path in (args.boot, args.system, args.output, cc)))


if __name__ == "__main__":
    main()
