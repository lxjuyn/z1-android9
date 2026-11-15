#!/usr/bin/env python3
"""Add the factory MTK NVRAM daemon closure to a Z1 Android 9 system image.

The input base image is never modified. Factory files are extracted read-only
from the device's own Android 4.4 system image, checked against pinned hashes,
and written to a new raw ext4 candidate. No NVRAM contents or calibration data
are read from the NVRAM partition or copied into the candidate.

Inspection is the default. Pass --build and --output to publish a verified
image plus its JSON manifest. The daemon is registered disabled and must not
be started automatically: its legacy library can reset calibration files.

Accept a build only when the command exits 0, the final image and JSON manifest
exist, their SHA256 agrees, and neither <output>.INVALID nor
<output>.manifest.json.INVALID exists. Failure markers never contain success.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT / "scripts" / "release"))
import z1_build_driver_candidate as base


DEFAULT_BASE = WORKSPACE / "out/adaptation_candidate_20261115_r2/system.img"
DEFAULT_BASE_SHA256 = "89ed63a88dc5ede8b4ddedf7ff5befc067023ff4aee3f98d167d16ff91534c9a"
DEFAULT_FACTORY = WORKSPACE / "1/system.img"
DEFAULT_FACTORY_SHA256 = "0aad817b3f87e7b328378c0b147da1ea048a87f9aa2ecfa338178be7176c62f7"
INIT_RC = ROOT / "rootdir/z1_nvram.rc"


@dataclass(frozen=True)
class Payload:
    source: str
    target: str
    runtime: str
    sha256: str
    mode: int


PAYLOADS = (
    Payload(
        "/bin/nvram_daemon",
        "/bin/nvram_daemon",
        "/system/bin/nvram_daemon",
        "c71bc78eed97bedbe307d66927af0b94176fd36be1e015ae9181b66a7684c5cb",
        0o755,
    ),
    Payload(
        "/lib/libnvram.so",
        "/vendor/lib/libnvram.so",
        "/vendor/lib/libnvram.so",
        "6765f0f69200f29e13b6da156b5a9af6a23f0bec92c3b128f01877a283ec17ee",
        0o644,
    ),
    Payload(
        "/lib/libcustom_nvram.so",
        "/vendor/lib/libcustom_nvram.so",
        "/vendor/lib/libcustom_nvram.so",
        "cf34ca23a06d478394774b63e4a0dcf742b4f96d6c9b81201b19ce9e9fe34d25",
        0o644,
    ),
    Payload(
        "/lib/libnvram_sec.so",
        "/vendor/lib/libnvram_sec.so",
        "/vendor/lib/libnvram_sec.so",
        "ade22e2f50cd0bc02982ea244008b2caf31eeaf99bcc155ee5f8e639ad49dd5e",
        0o644,
    ),
    Payload(
        "/lib/libnvram_platform.so",
        "/vendor/lib/libnvram_platform.so",
        "/vendor/lib/libnvram_platform.so",
        "e7be213ca2d94cb159c4c7f9905433395ccc0b88bbed2f3caccb6abeb13a21dc",
        0o644,
    ),
    Payload(
        "/lib/libfile_op.so",
        "/vendor/lib/libfile_op.so",
        "/vendor/lib/libfile_op.so",
        "13733f2587936acbb7e8571f78f7db16337c5ee2d0669fc4123d29509bf452a7",
        0o644,
    ),
    Payload(
        "/lib/libhwm.so",
        "/vendor/lib/libhwm.so",
        "/vendor/lib/libhwm.so",
        "330976d4822fd2114cf0fa18a93b55dc93d7b0610eb224fff4c6058feb1c6b7e",
        0o644,
    ),
    Payload(
        "/lib/libnvram_daemon_callback.so",
        "/vendor/lib/libnvram_daemon_callback.so",
        "/vendor/lib/libnvram_daemon_callback.so",
        "3cf182f0c3586e0533ff00cd70982233228292328b3f94df6235441423db53df",
        0o644,
    ),
)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def check_arm_elf(data, name):
    base.require(
        len(data) >= 52
        and data[:6] == b"\x7fELF\x01\x01"
        and struct.unpack_from("<H", data, 16)[0] in (2, 3)
        and struct.unpack_from("<H", data, 18)[0] == 40,
        "Payload is not ELF32 little-endian ARM: " + name,
    )
    if name.endswith(".so"):
        base.require(struct.unpack_from("<H", data, 16)[0] == 3,
                     "Shared payload is not ET_DYN: " + name)


def validate_init_rc(data):
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("NVRAM init rc is not ASCII") from error
    base.require(all(character == "\n" or 32 <= ord(character) <= 126
                     for character in text) and "\\" not in text,
                 "NVRAM init rc contains controls or line continuations")
    allowed = (
        "service nvram_daemon /system/bin/nvram_daemon",
        "    class main",
        "    user nvram",
        "    group nvram system",
        "    oneshot",
        "    disabled",
        "    seclabel u:r:init:s0",
    )
    # A single exact service block is the whole permitted grammar. In
    # particular, substring checks must not accept extra actions, setprop,
    # ctl.start, duplicate options/services, or commented-out safety options.
    lines = tuple(line for line in text.splitlines()
                  if line.strip() and not line.lstrip().startswith("#"))
    base.require(lines == allowed,
                 "NVRAM init rc must contain only the disabled diagnostic service")


def inspect_inputs(factory, base_image):
    factory = base.host_path(str(factory), Path.cwd())
    base_image = base.host_path(str(base_image), Path.cwd())
    base.require(INIT_RC.is_file() and not INIT_RC.is_symlink(),
                 "Missing regular NVRAM init rc: " + str(INIT_RC))
    rc_data = INIT_RC.read_bytes()
    validate_init_rc(rc_data)
    return factory, base_image, rc_data


def extract_payloads(factory_fd, work):
    extracted = []
    for index, item in enumerate(PAYLOADS):
        output = work / f"payload-{index}"
        base.debug(factory_fd, work, f"dump {item.source} {output}")
        data = output.read_bytes()
        base.require(sha_bytes(data) == item.sha256,
                     "Factory SHA256 mismatch: " + item.source)
        check_arm_elf(data, item.source)
        extracted.append((item, data))
    return extracted


def path_exists(fd, work, path):
    try:
        base.metadata(fd, work, path)
        return True
    except ValueError:
        return False


def write_payload(fd, work, target, data_path, mode, label_path):
    if path_exists(fd, work, target):
        base.debug(fd, work, f"rm {target}", write=True)
    base.debug(fd, work, f"write {data_path} {target}", write=True)
    full_mode = stat.S_IFREG | mode
    base.debug(fd, work, f"sif {target} mode 0{full_mode:o}", write=True)
    base.debug(fd, work, f"sif {target} uid 0", write=True)
    base.debug(fd, work, f"sif {target} gid 0", write=True)
    base.debug(
        fd,
        work,
        f"ea_set -f {label_path.name} {target} security.selinux",
        write=True,
    )


def verify_payload(fd, work, item, data, label, *, check_elf=True):
    entry = {
        "path": item.target,
        "sha256": item.sha256,
        "original_sha256": item.sha256,
        "mode": f"0{item.mode:o}",
        "uid": 0,
        "gid": 0,
        "selinux": "u:object_r:vendor_file:s0"
        if item.target.startswith("/vendor/")
        else "u:object_r:system_file:s0",
    }
    actual_label = base.inspect(fd, work, entry, item.sha256)
    base.require(actual_label in (label, label + b"\0"),
                 "SELinux byte preservation failed: " + item.target)
    base.debug(fd, work, f"dump {item.target} verify-readback")
    readback = (work / "verify-readback").read_bytes()
    base.require(readback == data, "Readback bytes differ: " + item.target)
    if check_elf:
        check_arm_elf(readback, item.target)


def write_report_fd(fd, report):
    """Write/fsync a report without taking ownership of the caller's fd."""
    with os.fdopen(os.dup(fd), "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def same_inode_at(directory, path, identity):
    """Only withdraw names which still refer to our created inode."""
    if identity is None:
        return False
    try:
        return os.path.samestat(
            identity, os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        )
    except FileNotFoundError:
        return False


def withdraw_owned(directory, output, partial, reportpath, invalidreport,
                   image_identity, report_identity, invalid_state, error):
    base.withdraw(
        directory, output, partial, reportpath, invalidreport,
        same_inode_at(directory, output, image_identity),
        same_inode_at(directory, reportpath, report_identity),
        invalid_state, error,
    )


def execute(factory, base_image, factory_sha256=DEFAULT_FACTORY_SHA256,
            base_sha256=DEFAULT_BASE_SHA256, output=None, build=False):
    factory, base_image, rc_data = inspect_inputs(factory, base_image)
    base.require(not build or output is not None, "--build requires --output")
    base.require(build or output is None, "--output requires explicit --build")
    output = base.host_path(str(output), Path.cwd()) if output is not None else None

    report = {
        "status": "INSPECTED_ONLY_NOT_BUILT",
        "factory": {"path": str(factory), "sha256": factory_sha256},
        "base": {"path": str(base_image), "sha256": base_sha256},
        "init_rc": {"path": str(INIT_RC), "sha256": sha_bytes(rc_data)},
        "nvram_service": {
            "registered_disabled": True,
            "automatic_start": False,
            "ready_property_mapping": False,
            "calibration_validated": False,
        },
        "required_payloads": [
            {
                "factory": item.source,
                "image": item.target,
                "runtime": item.runtime,
                "sha256": item.sha256,
                "mode": f"0{item.mode:o}",
            }
            for item in PAYLOADS
        ],
        "optional_not_packaged": [
            "/system/bin/nvram_agent_binder",
            "/vendor/lib/libnvramagentclient.so",
        ],
        "device_data_dependency": [
            "/data/nvram/RestoreFlag",
            "/data/nvram/APCFG/",
            "/dev/nvram",
        ],
    }

    with base.regular_open(factory) as factory_file, base.regular_open(base_image) as base_file:
        base.raw_ext4(factory_file)
        base.raw_ext4(base_file)
        base.require(base.sha(f"/proc/self/fd/{factory_file.fileno()}") == factory_sha256,
                     "Factory image SHA256 mismatch")
        base.require(base.sha(f"/proc/self/fd/{base_file.fileno()}") == base_sha256,
                     "Base image SHA256 mismatch")
        if not build:
            with tempfile.TemporaryDirectory(prefix="z1-nvram-inspect-") as temp:
                work = Path(temp)
                extract_payloads(factory_file.fileno(), work)
            return report

        paths = [
            output,
            Path(str(output) + ".manifest.json"),
            Path(str(output) + ".INVALID"),
            Path(str(output) + ".manifest.json.INVALID"),
            Path(str(output) + ".manifest.json.partial"),
        ]
        inputs = (factory, base_image, INIT_RC)
        base.require(output.parent.is_dir(), "Output parent must already exist")
        base.require(all(not os.path.lexists(path) and path not in inputs for path in paths),
                     "Output, partial, or INVALID marker aliases an existing file/input")
        reportpath, partial_path, invalidreport, report_partial = paths[1:]
        invalid_state = {
            "status": "INVALID",
            "error": "Build incomplete or aborted",
            "protocol": "Accept only exit 0, final image/report and no INVALID names",
        }
        image_identity = report_identity = directory_identity = None
        try:
            with base.parent_fd(output) as directory:
                directory_identity = os.fstat(directory)
                try:
                    # Create the immutable failure marker before writing an image.
                    marker_fd = os.open(
                        invalidreport.name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=directory,
                    )
                    try:
                        marker_identity = os.fstat(marker_fd)
                        write_report_fd(marker_fd, invalid_state)
                    finally:
                        os.close(marker_fd)
                    os.fsync(directory)
                    partial_fd = os.open(
                        partial_path.name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=directory,
                    )
                    try:
                        image_identity = os.fstat(partial_fd)
                        base.require(stat.S_ISREG(image_identity.st_mode),
                                     "Partial output is not a regular file")
                        partial = Path(f"/proc/self/fd/{partial_fd}")
                        report["e2fsck_before"] = base.fsck(base_file.fileno(), Path.cwd())
                        base_file.seek(0)
                        with tempfile.TemporaryDirectory(prefix="z1-nvram-build-") as temp:
                            work = Path(temp)
                            extracted = extract_payloads(
                                factory_file.fileno(), work
                            )
                            candidate_fd = partial_fd
                            with open(partial, "r+b", buffering=0) as candidate:
                                shutil.copyfileobj(base_file, candidate)
                                candidate.flush()
                                os.fsync(candidate.fileno())
                            base.require(
                                base.sha(f"/proc/self/fd/{candidate_fd}") == base_sha256,
                                "Base changed while copying",
                            )
                            for index, (item, data) in enumerate(extracted):
                                label = work / f"label-{index}"
                                label.write_bytes(
                                    b"u:object_r:vendor_file:s0"
                                    if item.target.startswith("/vendor/")
                                    else b"u:object_r:system_file:s0"
                                )
                                write_payload(
                                    candidate_fd,
                                    work,
                                    item.target,
                                    work / f"payload-{index}",
                                    item.mode,
                                    label,
                                )

                            rc_path = work / "z1_nvram.rc"
                            rc_path.write_bytes(rc_data)
                            rc_label = work / "rc-label"
                            rc_label.write_bytes(b"u:object_r:system_file:s0")
                            write_payload(
                                candidate_fd,
                                work,
                                "/etc/init/z1_nvram.rc",
                                rc_path,
                                0o644,
                                rc_label,
                            )

                            report["e2fsck_after"] = base.fsck(candidate_fd, work)
                            for index, (item, data) in enumerate(extracted):
                                verify_payload(
                                    candidate_fd,
                                    work,
                                    item,
                                    data,
                                    (work / f"label-{index}").read_bytes(),
                                )
                            verify_payload(
                                candidate_fd,
                                work,
                                Payload(
                                    "/rootdir/z1_nvram.rc",
                                    "/etc/init/z1_nvram.rc",
                                    "/etc/init/z1_nvram.rc",
                                    sha_bytes(rc_data),
                                    0o644,
                                ),
                                rc_data,
                                b"u:object_r:system_file:s0",
                                check_elf=False,
                            )

                        os.fsync(partial_fd)
                        base.require(
                            base.sha(f"/proc/self/fd/{base_file.fileno()}") == base_sha256,
                            "Base changed during build",
                        )
                        report.update(
                            status="HOST_VERIFIED_NOT_DEVICE_TESTED",
                            output=str(output),
                            sha256=base.sha(f"/proc/self/fd/{partial_fd}"),
                        )
                    finally:
                        os.close(partial_fd)

                    report_fd = os.open(
                        report_partial.name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=directory,
                    )
                    try:
                        report_identity = os.fstat(report_fd)
                        write_report_fd(report_fd, report)
                    finally:
                        os.close(report_fd)
                    with base.parent_fd(output) as current:
                        base.require(os.path.samestat(directory_identity, os.fstat(current)),
                                     "Output parent changed")
                    base.require(same_inode_at(directory, partial_path, image_identity),
                                 "Candidate identity changed")
                    base.require(same_inode_at(directory, report_partial, report_identity),
                                 "Report identity changed")
                    os.link(partial_path.name, output.name,
                            src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                    os.link(report_partial.name, reportpath.name,
                            src_dir_fd=directory, dst_dir_fd=directory,
                            follow_symlinks=False)
                    base.require(same_inode_at(directory, output, image_identity)
                                 and same_inode_at(directory, reportpath, report_identity),
                                 "Published identity changed")
                    os.fsync(directory)
                    os.unlink(partial_path.name, dir_fd=directory)
                    os.unlink(report_partial.name, dir_fd=directory)
                    os.fsync(directory)
                    base.require(same_inode_at(directory, invalidreport, marker_identity),
                                 "Failure marker identity changed")
                    os.unlink(invalidreport.name, dir_fd=directory)
                    os.fsync(directory)
                except BaseException as error:
                    withdraw_owned(directory, output, partial_path, reportpath, invalidreport,
                                   image_identity, report_identity, invalid_state, error)
                    raise
        except BaseException as error:
            # Includes a directory-close failure after successful publication.
            if image_identity is not None or report_identity is not None:
                try:
                    with base.parent_fd(output) as recovery:
                        base.require(os.path.samestat(directory_identity, os.fstat(recovery)),
                                     "Rollback directory identity changed")
                        withdraw_owned(recovery, output, partial_path, reportpath, invalidreport,
                                       image_identity, report_identity, invalid_state, error)
                except (OSError, ValueError) as recovery_error:
                    error.add_note("Rollback unavailable: " + str(recovery_error))
            raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--base-sha256", default=DEFAULT_BASE_SHA256)
    parser.add_argument("--factory", type=Path, default=DEFAULT_FACTORY)
    parser.add_argument("--factory-sha256", default=DEFAULT_FACTORY_SHA256)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            execute(
                args.factory,
                args.base,
                args.factory_sha256,
                args.base_sha256,
                args.output,
                args.build,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
