#!/usr/bin/env python3
"""Build the host-verified Z1 offline GPS candidate pair.

The candidate preserves the R5 kernel and system baseline, replaces the GNSS
placeholder with the ABI adapter, and installs the untouched factory HAL under
a distinct SONAME. It also rewrites the boot ramdisk mnld service so the
factory HAL's init.svc.mnld contract and socket DAC permissions are satisfied.
No device access or flashing is performed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[4]
SCRIPTS = WORKSPACE / "z1a9_gpt" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

import prepare_init_rc  # noqa: E402
import z1_build_adaptation_boot as boot_builder  # noqa: E402
import z1_prepare_recovery_boot as boot_format  # noqa: E402

BASE_BOOT = (
    WORKSPACE
    / "out/r5_offline_candidate_20261119/boot_hsq_control_r5.img"
)
BASE_BOOT_SHA = "b175077f8ce3b2322335f4b10f64f0186281242a00cb17dd267cf16d432168b1"
BASE_SYSTEM = WORKSPACE / "out/r5_offline_candidate_20261119/system.img"
BASE_SYSTEM_SHA = "a7c3a16ab5a3a4a8c519e5bd5f97a37be26f55f52d5e4baabf922ee898e98a59"
STOCK_HAL = (
    WORKSPACE
    / "out/z1_gps_stage/factory/lib/hw/gps.default.so"
)
STOCK_HAL_SHA = "515f239b2dab4344ce0d1ca9a5af02582b66e9f8b0088477ffb21e3f4937808a"
GPSDRV = (
    WORKSPACE
    / "z1a9_gpt/scripts/btgps/gps/z1_gpsdrv/z1_gpsdrv.ko"
)
GPSDRV_SHA = "5cab8a98e9bad3fc7cfa369af2b850dc9c271cd81678150b77279d22667de717"
PREP = HERE.parent / "z1_gps_prep.sh.patched"
PREP_SHA = "2b71808f78b54eef7a930c92e33b1a2a3262bb66178587e6ffc1302fc4971571"
ORIGINAL_PREP_SHA = "4042b0557905bca6b98fcc4f45f236e97aa8c6090034917aa42aed97a4d782cd"
ORIGINAL_RC_SHA = "3d032bec6427b90459af8d1134632cb81eafc795ff33300a0d95d3ea76bd9854"
DEFAULT_OUTPUT = WORKSPACE / "out/z1_gps_offline_candidate_20261119"


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def run(command, *, cwd=None, stdout=True):
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        check=False,
        capture_output=not stdout,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(map(str, command))}\n"
            f"{result.stdout or ''}{result.stderr or ''}"
        )
    if not stdout:
        return result.stdout + result.stderr
    return None


def debugfs_verify(image, spec):
    path, expected, mode, uid, gid, label = spec
    with tempfile.TemporaryDirectory(prefix="z1-gps-readback-") as directory:
        dump = Path(directory) / "payload"
        run(["debugfs", "-R", f"dump {path} {dump}", image], stdout=False)
        require(dump.is_file(), f"debugfs did not dump {path}")
        require(sha_file(dump) == expected, f"readback mismatch: {path}")
    stat = run(["debugfs", "-R", f"stat {path}", image], stdout=False)
    require(re.search(rf"Mode:\s+{re.escape(mode)}\b", stat),
            f"mode mismatch: {path}")
    require(re.search(rf"User:\s+{uid}\s+Group:\s+{gid}\b", stat),
            f"owner mismatch: {path}")
    attrs = run(["debugfs", "-R", f"ea_list {path}", image], stdout=False)
    require(label in attrs, f"SELinux label mismatch: {path}")


def package_note(report):
    boot = report["boot"]
    system = report["system"]
    adapter = report["artifacts"]["adapter"]
    stock = report["artifacts"]["factory_hal"]
    return f"""# Z1 offline GPS candidate - 2026-11-19

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `boot.img` | {boot['bytes']:,} | `{boot['sha256']}` |
| `system.img` | {system['bytes']:,} | `{system['sha256']}` |
| `artifacts/gps.default.so` | {adapter['bytes']:,} | `{adapter['sha256']}` |
| `artifacts/gps.z1stock.so` | {stock['bytes']:,} | `{stock['installed_sha256']}` |

This pair keeps the R5 kernel, DTB, system baseline, and HSQ control bootargs.
It replaces the no-fix GNSS placeholder with an Android 9 ABI adapter around
the factory MTK HAL. The factory HAL remains the real data path through
`mnld -> libmnlp_mt6572 -> /dev/stpgps -> WMT GPS`; the adapter only fixes the
Android 4.4 callback-table order and never fabricates a location. Raw factory
extension tables are withheld until their payload and lifetime contracts have
separate ABI adapters, so assisted GPS is unavailable in this candidate.

The boot ramdisk now exposes the init contract the factory HAL expects:

```text
service name: mnld
socket:       /dev/socket/mnld, 0660 gps:system
prep:         /z1_gps_prep.sh from the boot ramdisk
```

The system image adds:

```text
/vendor/lib/hw/gps.default.so    Android 9 adapter (7,440 B)
/vendor/lib/hw/gps.z1stock.so    factory HAL, SONAME disambiguated
/lib/modules/z1-radio/z1_gpsdrv.ko
```

Host checks passed:

```text
Android 9 callback order under ARM qemu
real factory HAL dependency closure under ARM qemu
boot payload readback and initrd-end consistency
system file hash/mode/uid/gid/SELinux readback
e2fsck -fn
```

## Board gates

Flash boot and system as a pair. After boot, require all of these before
calling offline GPS working:

```sh
getprop init.svc.mnld
ls -l /dev/socket/mnld /dev/stpgps
ls /sys/class/gpsdrv/gps
dmesg | grep -E '\\[z1gps\\]|mt3326|GPS'
```

Then enable location, wait outdoors for a cold fix, and verify the framework
receives satellite status, NMEA data, and a nonzero location callback without
network access. A running service, loaded module, or discovered satellite is
not by itself a completed location fix.

Rollback pair: R5 control boot
`{report['base_boot']['sha256']}` and R5 system
`{report['base_system']['sha256']}`.
"""


def build(output):
    output = output.absolute()
    require(not output.exists(), f"output already exists: {output}")
    require(output.parent.is_dir(), "output parent does not exist")
    print(f"[gps] building adapter in {output}")
    output.mkdir()
    artifacts = output / "artifacts"
    sources = output / "sources"
    artifacts.mkdir()
    sources.mkdir()

    for path, expected in (
        (BASE_BOOT, BASE_BOOT_SHA),
        (BASE_SYSTEM, BASE_SYSTEM_SHA),
        (STOCK_HAL, STOCK_HAL_SHA),
        (GPSDRV, GPSDRV_SHA),
        (PREP, PREP_SHA),
    ):
        require(path.is_file(), f"missing input: {path}")
        require(sha_file(path) == expected, f"input identity mismatch: {path}")

    stock = artifacts / "gps.z1stock.so"
    shutil.copyfile(STOCK_HAL, stock)
    run(["patchelf", "--set-soname", "gps.z1stock.so", stock])
    dynamic = run(["readelf", "-d", stock], stdout=False)
    require("Library soname: [gps.z1stock.so]" in dynamic,
            "stock HAL SONAME was not disambiguated")
    require("Shared library: [libxlog.so]" in dynamic,
            "stock HAL lost its libxlog dependency")
    symbols = run(["readelf", "-Ws", stock], stdout=False)
    require("gps__get_gps_interface" in symbols,
            "stock HAL lacks gps__get_gps_interface")

    run([HERE / "build.sh", artifacts], stdout=False)
    adapter = artifacts / "gps.default.so"
    adapter_dynamic = run(["readelf", "-d", adapter], stdout=False)
    require("Library soname: [gps.default.so]" in adapter_dynamic,
            "adapter SONAME mismatch")
    adapter_symbols = run(["readelf", "-Ws", adapter], stdout=False)
    require(" GLOBAL " in adapter_symbols and " HMI\n" in adapter_symbols,
            "adapter does not export HMI")
    run([HERE / "test/run_arm_qemu.sh", BASE_SYSTEM], stdout=False)

    print("[gps] rebuilding boot ramdisk")
    boot_pkg = output / "boot_pkg"
    boot_manifest = output / "boot_inputs.json"
    base_boot = BASE_BOOT.read_bytes()
    _, _, zimage, dtb, rootfs = boot_format.split_boot(base_boot)
    require(sha_bytes(base_boot) == BASE_BOOT_SHA, "boot changed while reading")
    body, records = boot_format.cpio(rootfs)
    contents = boot_format.contents(body, records)
    for name, expected in (
        ("init.z1.network.rc", ORIGINAL_RC_SHA),
        ("z1_gps_prep.sh", ORIGINAL_PREP_SHA),
    ):
        require(name in contents, f"boot ramdisk lacks {name}")
        require(sha_bytes(contents[name]) == expected,
                f"unexpected base ramdisk identity: {name}")

    old_rc = sources / "init.z1.network.rc.original"
    new_rc = sources / "init.z1.network.rc"
    new_prep = sources / "z1_gps_prep.sh"
    zimage_path = sources / "zImage"
    dtb_path = sources / "base.dtb"
    old_rc.write_bytes(contents["init.z1.network.rc"])
    new_rc.write_bytes(prepare_init_rc.transform(contents["init.z1.network.rc"]))
    new_prep.write_bytes(PREP.read_bytes())
    zimage_path.write_bytes(zimage)
    dtb_path.write_bytes(dtb)

    boot_manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "base": {"path": str(BASE_BOOT), "sha256": BASE_BOOT_SHA},
                "zimage": {"path": str(zimage_path), "sha256": sha_file(zimage_path)},
                "dtb": {"path": str(dtb_path), "sha256": sha_file(dtb_path)},
                "replacements": [
                    {
                        "name": "init.z1.network.rc",
                        "source": {
                            "path": str(new_rc),
                            "sha256": sha_file(new_rc),
                        },
                        "original_sha256": ORIGINAL_RC_SHA,
                    },
                    {
                        "name": "z1_gps_prep.sh",
                        "source": {
                            "path": str(new_prep),
                            "sha256": sha_file(new_prep),
                        },
                        "original_sha256": ORIGINAL_PREP_SHA,
                    },
                ],
            },
            indent=2,
        )
        + "\n"
    )
    boot_builder.ALLOWED.update({"init.z1.network.rc", "z1_gps_prep.sh"})
    boot_report = boot_builder.build(boot_manifest, boot_pkg)
    boot = output / "boot.img"
    shutil.copy2(boot_pkg / "boot.img", boot)
    require(boot_report["boot_sha256"] == sha_file(boot), "boot readback mismatch")

    print("[gps] patching system image")
    system = output / "system.img"
    system_specs = [
        f"/vendor/lib/hw/gps.default.so:{adapter}:0100644",
        f"/vendor/lib/hw/gps.z1stock.so:{stock}:0100644",
        f"/lib/modules/z1-radio/z1_gpsdrv.ko:{GPSDRV}:0100644",
    ]
    run(
        [
            SCRIPTS / "z1_make_system_image.sh",
            BASE_SYSTEM,
            system,
            *system_specs,
        ]
    )
    fsck = run(["e2fsck", "-fn", system], stdout=False)
    require("FILE SYSTEM WAS MODIFIED" not in fsck, "e2fsck modified candidate")
    for spec in (
        (
            "/vendor/lib/hw/gps.default.so",
            sha_file(adapter),
            "0644",
            0,
            0,
            "u:object_r:same_process_hal_file:s0",
        ),
        (
            "/vendor/lib/hw/gps.z1stock.so",
            sha_file(stock),
            "0644",
            0,
            0,
            "u:object_r:same_process_hal_file:s0",
        ),
        (
            "/lib/modules/z1-radio/z1_gpsdrv.ko",
            GPSDRV_SHA,
            "0644",
            0,
            0,
            "u:object_r:system_file:s0",
        ),
    ):
        debugfs_verify(system, spec)
    run([HERE / "test/run_actual_hal_qemu.sh", system], stdout=False)

    report = {
        "status": "HOST_VERIFIED_NOT_DEVICE_TESTED",
        "base_boot": {"path": str(BASE_BOOT), "sha256": BASE_BOOT_SHA},
        "base_system": {"path": str(BASE_SYSTEM), "sha256": BASE_SYSTEM_SHA},
        "boot": {
            "path": str(boot),
            "bytes": boot.stat().st_size,
            "sha256": sha_file(boot),
            "builder_report": boot_report,
        },
        "system": {
            "path": str(system),
            "bytes": system.stat().st_size,
            "sha256": sha_file(system),
            "e2fsck": fsck,
        },
        "artifacts": {
            "adapter": {
                "path": str(adapter),
                "sha256": sha_file(adapter),
                "bytes": adapter.stat().st_size,
            },
            "factory_hal": {
                "source_sha256": STOCK_HAL_SHA,
                "installed_path": str(stock),
                "installed_sha256": sha_file(stock),
                "bytes": stock.stat().st_size,
            },
            "gpsdrv": {
                "path": str(GPSDRV),
                "sha256": GPSDRV_SHA,
                "bytes": GPSDRV.stat().st_size,
            },
        },
        "service_contract": {
            "name": "mnld",
            "hal_user": "gps",
            "socket": "/dev/socket/mnld 0660 gps:system",
            "stock_hal_callback_order": {
                "request_utc_time_cb": "0x20",
                "create_thread_cb": "0x24",
            },
        },
        "verification": {
            "android_9_callback_order_qemu": "PASS",
            "actual_factory_hal_load_qemu": "PASS",
            "unadapted_extension_tables_withheld": "PASS",
            "boot_payload_readback": "PASS",
            "system_payload_readback": "PASS",
            "system_e2fsck": "PASS",
        },
        "remaining_device_gates": [
            "factory HAL dlopen succeeds in the vendor namespace",
            "mnld remains running beyond the 8s stabilization window",
            "/dev/stpgps carries real STP traffic",
            "framework receives satellite status and NMEA",
            "framework receives a valid location callback without network",
            "optional AGPS/vendor extensions require separate ABI adapters",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    checksum_files = [boot, system, adapter, stock, new_rc, new_prep]
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{sha_file(path)}  {path.relative_to(output)}\n"
            for path in checksum_files
        )
    )
    (output / "PACKAGE_NOTE.md").write_text(package_note(report))
    print(
        f"[gps] boot {report['boot']['sha256']} "
        f"system {report['system']['sha256']}"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))


if __name__ == "__main__":
    main()
