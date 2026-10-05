#!/usr/bin/env python3
"""Read-only Linux7/Pie artifact checks, not a claim of successful device boot.

Requires debugfs, e2fsck, fdtget, readelf and modinfo. Only explicitly selected
HALs/services, their ELF dependencies and the network module manifest are read.
"""
import argparse
import gzip
import hashlib
import json
import lzma
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from z1_add_bootdiag import entries
from z1_make_android9_bootimg import check_image, check_ramdisk, dt_cell, dt_bootargs
from z1_prepare_android9_ramdisk import INITRD_START

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ["android.hardware.health@2.0-service.z1",
            "android.hardware.audio@2.0-service", "android.hardware.keymaster@3.0-service",
            "android.hardware.graphics.allocator@2.0-service",
            "android.hardware.graphics.composer@2.1-service"]
HWS = ["audio.primary.default.so",
       "android.hardware.audio@2.0-impl.so", "android.hardware.audio.effect@2.0-impl.so",
       "android.hardware.keymaster@3.0-impl.so", "android.hardware.graphics.mapper@2.0-impl.so",
       "android.hardware.graphics.allocator@2.0-impl.so",
       "android.hardware.graphics.composer@2.1-impl.so"]
REQUIRED_MODULES = {"ipv6", "nfnetlink", "nfnetlink_log", "x_tables", "ip_tables",
                    "ip6_tables", "xt_NFLOG", "xt_quota2", "xt_IDLETIMER"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(argv):
    return subprocess.run(argv, check=True, text=True, capture_output=True, timeout=180)


def arm_elf(path):
    with path.open("rb") as stream:
        head = stream.read(52)
    require(len(head) == 52 and head[:6] == b"\x7fELF\x01\x01" and
            struct.unpack_from("<H", head, 18)[0] == 40, f"not ARM32 little-endian ELF: {path}")


def ramdisk_files(blob):
    body = blob[512:]
    archive = gzip.decompress(body) if body.startswith(b"\x1f\x8b") else lzma.decompress(body)
    result = {}
    for name, fields, data in entries(archive):
        name = name.decode().removeprefix("./")
        require(name not in result, f"duplicate ramdisk entry: {name}")
        result[name] = (fields, data)
    return result


def init_actions(rc):
    actions, current = {}, None
    for line in rc.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("on "):
            current = line[3:].strip()
            actions.setdefault(current, [])
        elif line[0].isspace() and current is not None:
            actions[current].append(line.strip())
        else:
            current = None
    return actions


def check_ramdisk_usb_ownership(files):
    """Require the ramdisk ACM helper to be the sole init USB gadget owner."""
    rc_files = []
    acm_services = []
    acm_starts = []
    generic_usb_rc = re.compile(r"init\.usb[^/]*\.rc\Z")
    generic_usb_import = re.compile(r"(?:^|/)init\.usb[^/]*\.rc\Z")
    forbidden = (
        (re.compile(r"\badbd\b"), "ADB daemon rule"),
        (re.compile(r"\bz1_usb_setup\b"), "competing Z1 ADB helper"),
        (re.compile(r"/config/usb_gadget(?:/|\b)"), "configfs USB gadget owner"),
        (re.compile(r"/sys/class/android_usb(?:/|\b)"), "legacy Android USB gadget owner"),
        (re.compile(r"/sys/class/udc(?:/|\b)"), "UDC sysfs owner"),
        (re.compile(r"\bUDC\b"), "UDC binding rule"),
        (re.compile(r"/dev/usb-ffs/adb\b|\bffs\.adb\b|\bfunctionfs\s+adb\b"),
         "ADB FunctionFS gadget rule"),
    )

    for name, (fields, data) in files.items():
        basename = name.rsplit("/", 1)[-1]
        require(not generic_usb_rc.fullmatch(basename),
                f"generic USB init file remains in ramdisk: {name}")
        if not name.endswith(".rc"):
            continue
        require((fields[1] & 0o170000) == 0o100000,
                f"init RC is not a regular file: {name}")
        content = "\n".join(line.split("#", 1)[0]
                             for line in data.decode().splitlines())
        rc_files.append((name, content))

    require(rc_files, "ramdisk has no init RC files")
    board_rc = dict(rc_files).get("init.z1.rc", "")
    ready_reset = init_actions(board_rc).get("property:ro.persistent_properties.ready=true")
    require(ready_reset == ["setprop persist.sys.usb.config none", "setprop sys.usb.config none"],
            "init.z1.rc must clear persisted ADB USB state after persistent properties load")
    for name, content in rc_files:
        for line in content.splitlines():
            tokens = line.split()
            if tokens and tokens[0] == "import" and len(tokens) == 2:
                require(not generic_usb_import.search(tokens[1]),
                        f"generic USB init import remains in {name}: {tokens[1]}")
        for pattern, description in forbidden:
            require(not pattern.search(content), f"{description} remains in ramdisk RC: {name}")
        require(not re.search(r"^\s*on\s+.*(?:property:[^\n]*\b(?:sys|persist)\.usb\.|"
                              r"property:[^\n]*usb\.ffs|property:[^\n]*\b(?:usb|adb)\b)",
                              content, re.M | re.I),
                f"generic USB property trigger remains in ramdisk RC: {name}")

        for line in content.splitlines():
            tokens = line.split()
            if tokens and tokens[0] in {"service", "start", "exec", "exec_background", "exec_start"}:
                usb_service = re.search(r"\b(?:usb|adb)\b", line, re.I)
                is_acm_service = (tokens[0] == "service" and len(tokens) >= 4 and
                                  tokens[1] == "z1_acm_log" and
                                  tokens[2:4] == ["/z1_bootdiag", "--acm"])
                is_acm_start = tokens[:2] == ["start", "z1_acm_log"]
                require(not usb_service or is_acm_service or is_acm_start,
                        f"USB/ADB service or exec rule remains in {name}: {' '.join(tokens)}")
            if len(tokens) >= 3 and tokens[0] == "setprop" and tokens[1].startswith(
                    ("sys.usb.", "persist.sys.usb.")):
                require((tokens[1], tokens[2]) in {
                    ("sys.usb.config", "none"),
                    ("persist.sys.usb.config", "none"),
                }, f"unexpected USB property owner in {name}: {' '.join(tokens)}")

        acm_services.extend((name, service, command.strip()) for service, command in re.findall(
            r"^service\s+(\S+)\s+([^\n]*?/z1_bootdiag\s+--acm[^\n]*)$", content, re.M))
        acm_starts.extend((name, action) for action, commands in init_actions(content).items()
                          for command in commands if command == "start z1_acm_log")

    require(acm_services == [("init.z1.rc", "z1_acm_log", "/z1_bootdiag --acm")],
            "z1_bootdiag --acm must be the single USB gadget service in init.z1.rc")
    require(acm_starts == [("init.z1.rc", "init")],
            "z1_acm_log must have exactly one start action, on init")


def check_ramdisk_services(files):
    def text(name):
        require(name in files, f"missing ramdisk {name}")
        require((files[name][0][1] & 0o170000) == 0o100000, f"not regular ramdisk file: {name}")
        return files[name][1].decode()
    rc = text("init.z1.rc")
    require("import /init.${ro.hardware}.rc" in text("init.rc"), "board RC not imported by Android init")
    require(re.search(r"^import /init\.z1\.network\.rc\s*$", rc, re.M), "network RC not imported")
    for service, mode, trigger in (("z1_acm_log", "--acm", "init"),
                                   ("z1_logcat_relay", "--logcat", "post-fs-data")):
        require(re.search(rf"^service {service} /z1_bootdiag {mode}\s*$", rc, re.M),
                f"missing independent diagnostic service {service}")
        require(f"start {service}" in init_actions(rc).get(trigger, []), f"missing startup action {service}")
    check_ramdisk_usb_ownership(files)
    require("z1_bootdiag" in files and files["z1_bootdiag"][0][1] & 0o111,
            "missing executable /z1_bootdiag")
    return text("init.z1.network.rc")


def check_module_manifest(manifest, rc):
    require(manifest.get("kernel_release") == "7.0.0-rc7-z1+", "wrong module kernel release")
    mods = manifest.get("modules", [])
    require(mods and isinstance(mods, list), "empty module manifest")
    loaded = re.findall(r"^\s*insmod /system/lib/modules/z1-network/(\S+)\s*$", rc, re.M)
    require(re.search(r"^on post-fs\s*$", rc, re.M), "modules are not loaded on post-fs")
    in_action = re.findall(r"^insmod /system/lib/modules/z1-network/(\S+)\s*$",
                           "\n".join(init_actions(rc).get("post-fs", [])), re.M)
    require(in_action == loaded, "module insmods must all run in post-fs action")
    require(loaded == [m["file"] for m in mods], "module loading order/content differs from manifest")
    names, filenames = set(), set()
    for module in mods:
        require(re.fullmatch(r"[A-Za-z0-9_-]+\.ko", module["file"]), "unsafe module filename")
        require(re.fullmatch(r"[0-9a-f]{64}", module["sha256"]), "invalid module SHA256")
        require(module["name"] not in names and module["file"] not in filenames, "duplicate module")
        require(set(module["depends"]) <= names, f"missing/out-of-order dependency: {module['name']}")
        names.add(module["name"])
        filenames.add(module["file"])
    require(REQUIRED_MODULES <= names, f"missing network modules: {sorted(REQUIRED_MODULES - names)}")
    return mods


class ImageFiles:
    def __init__(self, image, tree, temp):
        self.image, self.tree, self.temp = image, tree.resolve(), temp
        self.checked = {}

    def get(self, relative):
        require(re.fullmatch(r"[A-Za-z0-9_.@/+\-]+", relative) and
                not relative.startswith("/") and ".." not in relative.split("/"), "unsafe image path")
        if relative in self.checked:
            return self.temp / relative
        source = self.tree / relative
        require(source.is_file() and source.resolve().is_relative_to(self.tree), f"missing tree file {relative}")
        destination = self.temp / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # debugfs may return zero on failed dump: independently check the output.
        run(["debugfs", "-R", f'dump /{relative} "{destination}"', str(self.image)])
        require(destination.is_file(), f"missing image file {relative}")
        digest = sha(destination)
        require(digest == sha(source), f"stale/different image file: {relative}")
        self.checked[relative] = digest
        return destination

    def require_absent(self, relative):
        require(re.fullmatch(r"[A-Za-z0-9_.@/+\-]+", relative), "unsafe image path")
        result = run(["debugfs", "-R", f"stat /{relative}", str(self.image)])
        require(not re.search(r"^Inode:\s*\d+", result.stdout, re.M),
                f"duplicate/obsolete health service RC in image: {relative}")


def check_elf_closure(image, roots):
    todo, seen = list(roots), set()
    while todo:
        relative = todo.pop()
        if relative in seen:
            continue
        seen.add(relative)
        path = image.get(relative)
        arm_elf(path)
        dynamic = run(["readelf", "-d", str(path)]).stdout
        for needed in re.findall(r"\(NEEDED\).*\[([^]]+)\]", dynamic):
            require("/" not in needed, f"unexpected dependency path: {needed}")
            candidates = [f"lib/{needed}", f"vendor/lib/{needed}"]
            dependency = next((p for p in candidates if (image.tree / p).is_file()), None)
            require(dependency is not None, f"unresolved dependency {relative}: {needed}")
            todo.append(dependency)
        headers = run(["readelf", "-l", str(path)]).stdout
        for interpreter in re.findall(r"Requesting program interpreter: ([^]]+)", headers):
            require(interpreter == "/system/bin/linker", f"unexpected interpreter: {relative}: {interpreter}")
            todo.append("bin/linker")
    return sorted(seen)


def verify(args):
    report = {"status": "FAIL", "device_runtime_verified": False, "checks": []}
    with tempfile.TemporaryDirectory(prefix="z1-candidate-check-") as work:
        temp = Path(work)
        ramdisk, body_size = check_ramdisk(args.ramdisk)
        check_image(args.boot.read_bytes(), args.zimage.read_bytes() + args.dtb.read_bytes(), ramdisk)
        require(dt_cell(args.dtb, "linux,initrd-start") == INITRD_START and
                dt_cell(args.dtb, "linux,initrd-end") == INITRD_START + body_size, "wrong DT initrd bounds")
        bootargs = dt_bootargs(args.dtb).split()
        require("androidboot.hardware=z1" in bootargs and
                "console=ttyS0,921600n8" in bootargs and
                "earlycon=mtk8250,0x11005000" in bootargs and
                "console=tty0" not in bootargs,
                "missing Z1 serial logging or tty0 is still writing over the Android framebuffer")
        files = ramdisk_files(ramdisk)
        module_rc = check_ramdisk_services(files)
        helper = temp / "z1_bootdiag"
        helper.write_bytes(files["z1_bootdiag"][1])
        arm_elf(helper)
        require("INTERP" not in run(["readelf", "-l", str(helper)]).stdout, "diagnostic helper needs /system")
        helper_manifest = json.loads(args.helper_manifest.read_text())
        helper_source = ROOT / "tools/z1_android9_bootdiag.c"
        require(isinstance(helper_manifest, dict), "helper build manifest must be a JSON object")
        require(helper_manifest.get("source") == "tools/z1_android9_bootdiag.c",
                "helper build manifest names an unexpected source")
        source_hash = helper_manifest.get("source_sha256")
        require(isinstance(source_hash, str) and re.fullmatch(r"[0-9a-f]{64}", source_hash) and
                source_hash == sha(helper_source),
                "helper source hash differs from repository source")
        binary_hash = helper_manifest.get("binary_sha256")
        require(isinstance(binary_hash, str) and re.fullmatch(r"[0-9a-f]{64}", binary_hash) and
                binary_hash == sha(helper),
                "ramdisk helper hash differs from helper build manifest")
        require(helper_manifest.get("target") == "ARMv7-A EABI5 hard-float; static ELF32",
                "helper build manifest has an unexpected target")
        manifest = json.loads(args.module_manifest.read_text())
        modules = check_module_manifest(manifest, module_rc)
        report["checks"].append("boot payload, DT initrd, ACM-only ramdisk ownership, helper source/binary hashes, logcat relay and ordered module RC")
        with args.system.open("rb") as stream:
            stream.seek(1080)
            require(stream.read(2) == b"\x53\xef", "system input is not raw ext4 (sparse images rejected)")
        fsck = run(["e2fsck", "-f", "-n", str(args.system)])
        report["e2fsck"] = fsck.stdout + fsck.stderr
        image = ImageFiles(args.system, args.system_tree, temp / "system")
        loader = image.get("etc/ld.config.txt").read_text()
        require(re.search(r"^namespace.default.isolated\s*=\s*false\s*$", loader, re.M) and
                "/system/${LIB}" in loader and "/vendor/${LIB}" in loader,
                "ELF search assumptions require the legacy system/vendor linker namespace")
        build_props = image.get("build.prop").read_text()
        require(re.findall(r"^ro\.hardware\.egl=(.*)$", build_props, re.M) == ["mesa"],
                "mainline system property must select Mesa EGL")
        require(re.findall(r"^persist\.sys\.usb\.config=(.*)$", build_props, re.M) == ["none"],
                "Android USB must default to none so ramdisk ACM retains UDC ownership")
        mesa_gles = "lib/egl/libGLES_mesa.so"
        gallium_dri = "lib/dri/gallium_dri.so"
        image.get(mesa_gles)
        dri_module = image.get(gallium_dri)
        require(b"lima\0" in dri_module.read_bytes(),
                "installed Gallium DRI module does not contain the Lima driver marker")
        xml = ET.parse(image.get("vendor/etc/vintf/manifest.xml"))
        hals = {hal.findtext("name"): hal for hal in xml.getroot().findall("hal")}
        for name, version in (("health", "2.0"), ("audio", "2.0"), ("audio.effect", "2.0"), ("keymaster", "3.0"),
                              ("graphics.allocator", "2.0"), ("graphics.mapper", "2.0"),
                              ("graphics.composer", "2.1")):
            hal = hals.get("android.hardware." + name)
            require(hal is not None and hal.findtext("version") == version and
                    any(x.text == "default" for x in hal.findall("interface/instance")),
                    f"missing/wrong manifest HAL {name}@{version}/default")
        mapper = hals["android.hardware.graphics.mapper"]
        require(mapper.findtext("transport") == "passthrough", "mapper must be passthrough")
        if "vendor_file_contexts" in files:  # Non-Treble policy is carried by root ramdisk.
            contexts = files["vendor_file_contexts"][1].decode()
        else:
            contexts = image.get("vendor/etc/selinux/vendor_file_contexts").read_text()
        health_path = "/vendor/bin/hw/android.hardware.health@2.0-service.z1"
        require(any(len(line.split()) >= 2 and
                    line.split()[-1] == "u:object_r:hal_health_default_exec:s0" and
                    re.fullmatch(line.split()[0], health_path)
                    for line in contexts.splitlines() if line.strip() and not line.lstrip().startswith("#")),
                "health .z1 service lacks hal_health_default_exec file context")
        for obsolete in ("etc/init/healthd.rc", "vendor/etc/init/android.hardware.health@2.0-service.rc",
                         "vendor/etc/init/android.hardware.health@1.0-service.rc"):
            image.require_absent(obsolete)
        roots = ["bin/surfaceflinger", "bin/audioserver", "bin/netd", "bin/logcat",
                 mesa_gles, gallium_dri]
        for service in SERVICES:
            roots.append(f"vendor/bin/hw/{service}")
            rc = image.get(f"vendor/etc/init/{service}.rc").read_text()
            require(f"/vendor/bin/hw/{service}" in rc, f"RC has wrong service binary: {service}")
        roots += ["vendor/lib/hw/" + hw for hw in HWS]
        roots += ["lib/hw/gralloc.z1.so", "lib/hw/hwcomposer.z1.so"]
        for module in modules:
            relative = "lib/modules/z1-network/" + module["file"]
            path = image.get(relative)
            require(sha(path) == module["sha256"], f"module hash differs from manifest: {relative}")
            arm_elf(path)
            actual_deps = run(["modinfo", "-F", "depends", str(path)]).stdout.strip()
            require({d.replace("-", "_") for d in actual_deps.split(",") if d} == set(module["depends"]),
                    f"module depends differs: {relative}")
            require(run(["modinfo", "-F", "vermagic", str(path)]).stdout.split()[0] == manifest["kernel_release"],
                    f"module vermagic differs: {relative}")
        if args.m6:
            require(b"z1status" in helper.read_bytes(), "M6 helper lacks read-only status protocol")
            require(args.kernel_config is not None, "M6 requires frozen kernel config")
            config = args.kernel_config.read_text()
            report["m6_kernel_config_sha256"] = sha(args.kernel_config)
            for option in ("CONFIG_FUSE_FS=m", "CONFIG_XFRM_USER=m",
                           "CONFIG_IP_MULTIPLE_TABLES=y", "CONFIG_IPV6_MULTIPLE_TABLES=y",
                           "CONFIG_NETFILTER_XT_MATCH_POLICY=m"):
                require(option in config.splitlines(), "missing M6 kernel option: " + option)
            require({"fuse", "xfrm_user", "xt_policy"} <= {m["name"] for m in modules},
                    "missing M6 FUSE/XFRM/policy modules")
            channel_names = run(["fdtget", "-t", "s", str(args.dtb),
                                 "/adc-battery", "io-channel-names"]).stdout.split()
            require(channel_names == ["voltage"], "M6 DT advertises uncalibrated ADC temperature")
            sdcard = image.get("bin/sdcard")
            require(b"Z1 legacy FUSE" in sdcard.read_bytes(), "M6 sdcard is not board FUSE backend")
            roots.append("bin/sdcard")
            roots.append("bin/vold")
            props = image.get("build.prop").read_text()
            require("ro.vold.z1_fuse=true" in props.splitlines(), "M6 FUSE product gate missing")
            report["checks"].append("M6 calibrated-temperature guard, legacy FUSE backend, route/XFRM config")
        report["elf_closure"] = check_elf_closure(image, roots)
        report["verified_image_files"] = image.checked
        report["checks"].append("Mesa EGL/GLES and Lima DRI artifacts, logcat and HAL ARM ELF dependency closure, clean ext4 and module hashes")
        report["checks"].append("ACM-only system USB default and ACM helper UDC ownership configuration")
        report["inputs"] = {name: {"path": str(getattr(args, name)), "sha256": sha(getattr(args, name))}
                            for name in ("boot", "system", "zimage", "dtb", "ramdisk", "module_manifest",
                                         "helper_manifest")}
        report["status"] = "STATIC_ARTIFACT_PASS"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("boot", "system", "zimage", "dtb", "ramdisk", "system-tree"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--module-manifest", type=Path,
                        default=ROOT / "android9/device/jiabo/z1/network/modules.json")
    parser.add_argument("--helper-manifest", type=Path, required=True,
                        help="build_z1_bootdiag.sh manifest for the helper injected into the ramdisk")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--m6", action="store_true", help="require M6 temperature/storage fixes")
    parser.add_argument("--kernel-config", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args)
    except (OSError, ValueError, KeyError, IndexError, struct.error, ET.ParseError,
            subprocess.SubprocessError) as exc:
        report = {"status": "FAIL", "device_runtime_verified": False, "error": str(exc)}
        if isinstance(exc, subprocess.CalledProcessError):
            report["command_output"] = ((exc.stdout or "") + (exc.stderr or ""))[-8000:]
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n")
    print(encoded)
    return 0 if report["status"] == "STATIC_ARTIFACT_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
