#!/usr/bin/env python3
"""Build a compact R7 pair that makes every audio HAL loader path identical."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))

from release import z1_build_driver_candidate as system  # noqa: E402
import z1_build_adaptation_boot as boot  # noqa: E402
from z1_image_block_delta import create_delta  # noqa: E402
from z1_prepare_recovery_boot import cpio, contents, sha, split_boot  # noqa: E402


BASE_BOOT_SHA = "e761299886d61bd5cfca6ff0876cf36d0355a3216c7cbb050a9cf08016f56a1d"
BASE_SYSTEM_SHA = "21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920"
HAL_SHA = "670cd3e97d07bf342d1635771b21b755f6c35b257949c959e481055e450176b6"
ROUTE_SHA = "1ab33c4e48f4d693822a9c6e7a09eed7eab19795611e40c81fdbea73476d57bb"
AUDIO_MTK_ORIGINAL = "029ed847bb149399fe127c5d811c7b2f64b235b6248613ebe6ced8e8769a5bd3"
AUDIO_DEFAULT_ORIGINAL = "3f1b3a841aea4eedd7cb7ba5c22ebe12e6062b6ec88ec5d28eed6864251e1722"
ROUTE_ORIGINAL = "8af73480059626ab6f0e056e4ae7cc3d17f83e77cced0f6be10c3fb6cd7d4f58"
RUNTIME_ORIGINAL = "3ad1445e34adde697d1481bac81d9ae8f8c233116ad5cb3d5d3cc2b0c0f5c789"

BOOT_HAL = "z1_audio.primary.mt6572.so"
BOOT_STAGE = "z1stage_boot.sh"
MTK_HAL = "/vendor/lib/hw/audio.primary.mt6572.so"
DEFAULT_HW_HAL = "/vendor/lib/hw/audio.primary.default.so"
DEFAULT_LIB_HAL = "/vendor/lib/libaudio.primary.default.so"
ROUTE = "/vendor/bin/z1_audio_route.sh"
RUNTIME = "/bin/z1_runtime_manifest"

SYSTEM_PATHS = (MTK_HAL, DEFAULT_HW_HAL, DEFAULT_LIB_HAL, ROUTE, RUNTIME)
SYSTEM_ALLOWED = re.compile(
    "|".join(re.escape(path) for path in SYSTEM_PATHS)
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def regular(path: Path) -> None:
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def check_hash(path: Path, expected: str) -> None:
    regular(path)
    actual = digest(path)
    require(actual == expected, f"SHA256 mismatch: {path} expected={expected} actual={actual}")


def read_image_file(image: Path, path: str) -> bytes:
    result = subprocess.run(
        ["debugfs", "-R", f"cat {path}", str(image)],
        capture_output=True,
        check=False,
    )
    require(result.returncode == 0 and result.stdout, f"cannot read {path} from {image}")
    return result.stdout


def payload(path: Path, expected_sha: str) -> dict:
    regular(path)
    require(digest(path) == expected_sha, f"input SHA256 mismatch: {path}")
    return {"path": str(path), "sha256": expected_sha}


def build_boot(base_boot: Path, hal: Path, stage: Path, work: Path) -> dict:
    raw = base_boot.read_bytes()
    _, _, zimage, dtb, rootfs = split_boot(raw)
    body, records = cpio(rootfs)
    files = contents(body, records)
    require(sha(files[BOOT_HAL]) == AUDIO_MTK_ORIGINAL, "unexpected base boot audio HAL")
    require(sha(files[BOOT_STAGE]) == (
        "ebb609da07466b8b7fd123296ec6e961846b10d562469f9bdaa8f62396bbb2e0"
    ), "unexpected base boot stage script")

    inputs = work / "boot-inputs"
    inputs.mkdir()
    for name, data in (("zImage", zimage), ("base.dtb", dtb)):
        (inputs / name).write_bytes(data)
    manifest = {
        "version": 1,
        "base": {"path": str(base_boot), "sha256": BASE_BOOT_SHA},
        "zimage": payload(inputs / "zImage", sha(zimage)),
        "dtb": payload(inputs / "base.dtb", sha(dtb)),
        "replacements": [
            {
                "name": BOOT_HAL,
                "original_sha256": AUDIO_MTK_ORIGINAL,
                "source": payload(hal, HAL_SHA),
            },
            {
                "name": BOOT_STAGE,
                "original_sha256": sha(files[BOOT_STAGE]),
                "source": payload(stage, digest(stage)),
            },
        ],
    }
    spec = work / "boot-inputs.json"
    spec.write_text(json.dumps(manifest, indent=2) + "\n")
    return boot.build(spec, work / "boot-package")


def build_system(base_system: Path, hal: Path, route: Path, runtime: Path, work: Path) -> dict:
    require(digest(base_system) == BASE_SYSTEM_SHA, "unexpected base system")
    expected = {
        MTK_HAL: AUDIO_MTK_ORIGINAL,
        DEFAULT_HW_HAL: AUDIO_DEFAULT_ORIGINAL,
        DEFAULT_LIB_HAL: AUDIO_DEFAULT_ORIGINAL,
        ROUTE: ROUTE_ORIGINAL,
        RUNTIME: RUNTIME_ORIGINAL,
    }
    sources = {
        MTK_HAL: (hal, HAL_SHA, "0644", "u:object_r:vendor_file:s0"),
        DEFAULT_HW_HAL: (hal, HAL_SHA, "0644", "u:object_r:vendor_file:s0"),
        DEFAULT_LIB_HAL: (hal, HAL_SHA, "0644", "u:object_r:vendor_file:s0"),
        ROUTE: (route, ROUTE_SHA, "0755", "u:object_r:vendor_file:s0"),
        RUNTIME: (runtime, digest(runtime), "0755", "u:object_r:system_file:s0"),
    }
    replacements = []
    for path in SYSTEM_PATHS:
        source, source_sha, mode, selinux = sources[path]
        regular(source)
        require(digest(source) == source_sha, f"input SHA256 mismatch: {source}")
        replacements.append({
            "path": path,
            "source": str(source),
            "original_sha256": expected[path],
            "sha256": source_sha,
            "mode": mode,
            "uid": 0,
            "gid": 0,
            "selinux": selinux,
        })
    manifest = {
        "version": 1,
        "base": {"path": str(base_system), "sha256": BASE_SYSTEM_SHA},
        "replacements": replacements,
    }
    spec = work / "system-inputs.json"
    spec.write_text(json.dumps(manifest, indent=2) + "\n")
    return system.execute(spec, work / "system.img", build=True, allowed=SYSTEM_ALLOWED)


def make_runtime_manifest(
    base_manifest: Path,
    boot_report: dict,
    system_report: dict,
    delta_report: dict,
    runtime_sha: str,
) -> dict:
    manifest = json.loads(base_manifest.read_text())
    manifest["schema"] = "z1-r7-audio-runtime/v1"
    manifest["candidate"] = "r7-audio-paths-20261122"
    manifest["package"] = {
        "bootimg": {
            "bytes": boot_report["boot_bytes"],
            "sha256": boot_report["boot_sha256"],
        },
        "android": {
            "bytes": os.path.getsize(system_report["output"]),
            "sha256": system_report["sha256"],
        },
    }
    audio_paths = {MTK_HAL, DEFAULT_HW_HAL, DEFAULT_LIB_HAL}
    files = []
    for item in manifest["runtime_files"]:
        if item["path"] in audio_paths:
            continue
        if item["path"] == ROUTE:
            item = dict(item, sha256=ROUTE_SHA, role="corrected DAPM speaker and MIC1 route")
        elif item["path"] in (RUNTIME, "/system/bin/z1_runtime_manifest"):
            item = dict(item, sha256=runtime_sha)
        files.append(item)
    role = "complete A9 playback and microphone primary HAL"
    files.extend(
        {"path": path, "sha256": HAL_SHA, "mode": "644", "role": role}
        for path in (MTK_HAL, DEFAULT_HW_HAL, DEFAULT_LIB_HAL)
    )
    manifest["runtime_files"] = files
    for item in manifest["content_checks"]:
        if item["id"] == "microphone-route":
            item["line"] = 'set_one "MIC Route Switch" 1'
    manifest["current_status"] = {
        "as_of": "2026-11-22T00:00:00+08:00",
        "status": "HOST_VERIFIED_NOT_DEVICE_TESTED",
        "accepted_post_flash_boot": False,
        "reason": (
            "All three audio HAL loader paths and the boot seed now carry the "
            "complete HAL, and the corrected MIC route is packaged. Physical "
            "speaker, microphone and framework playback remain board gates."
        ),
    }
    manifest["z1_delta"] = delta_report
    return manifest


def build(
    base_dir: Path,
    output: Path,
    work: Path,
    runtime_override: Path | None = None,
) -> dict:
    base_dir = base_dir.resolve()
    output = output.resolve()
    work = work.resolve()
    regular(base_dir / "boot.img")
    regular(base_dir / "system.img")
    check_hash(base_dir / "boot.img", BASE_BOOT_SHA)
    check_hash(base_dir / "system.img", BASE_SYSTEM_SHA)
    require(output.parent.is_dir() and not output.exists(), "output must be new below an existing parent")
    require(not work.exists(), "work directory must not exist")

    hal = (ROOT / "z1a9_gpt/scripts/r5g_audio/libaudio.primary.z1.so").resolve()
    stage = (ROOT / "z1a9_gpt/scripts/z1deploy/z1stage_boot.sh").resolve()
    route = (ROOT / "z1a9_gpt/scripts/r8_audio/z1_audio_route.sh").resolve()
    runtime = (
        runtime_override
        if runtime_override is not None
        else ROOT / "z1a9_gpt/scripts/z1deploy/z1_r4_runtime_manifest.sh"
    ).resolve()
    check_hash(hal, HAL_SHA)
    check_hash(route, ROUTE_SHA)
    regular(stage)
    regular(runtime)

    work.mkdir()
    boot_report = build_boot(base_dir / "boot.img", hal, stage, work)
    boot_image = work / "boot-package/boot.img"
    require(boot_image.stat().st_size <= 6 * 1024 * 1024, "boot image exceeds 6 MiB")
    require(boot_image.read_bytes()[:8] == b"ANDROID!", "boot image lacks ANDROID! magic")

    system_report = build_system(
        base_dir / "system.img", hal, route, runtime, work
    )
    target_system = Path(system_report["output"])
    e2fsck = subprocess.run(["e2fsck", "-fn", str(target_system)], capture_output=True)
    require(e2fsck.returncode == 0, "target system failed read-only e2fsck")

    with tempfile.TemporaryDirectory(prefix=".z1-audio-package-", dir=output.parent) as td:
        package = Path(td)
        boot_out = package / "boot.img"
        boot_out.write_bytes(boot_image.read_bytes())
        delta_out = package / "system_from_r7_speaker.img.z1delta"
        delta_report = create_delta(
            base_dir / "system.img", target_system, delta_out
        )
        hal_out = package / "audio.primary.z1.so"
        hal_out.write_bytes(hal.read_bytes())
        manifest = make_runtime_manifest(
            HERE / "r7_runtime_manifest.json",
            boot_report,
            system_report,
            delta_report,
            digest(runtime),
        )
        (package / "r7_audio_runtime_manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        (package / "materialize_system.py").write_text(
            f"""#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from z1_image_block_delta import apply_delta
parser = argparse.ArgumentParser()
parser.add_argument("base", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
print(apply_delta(args.base, Path(__file__).with_name("system_from_r7_speaker.img.z1delta"), args.output))
"""
        )
        (package / "materialize_system.py").chmod(0o755)
        shutil.copy2(HERE / "z1_image_block_delta.py", package / "z1_image_block_delta.py")
        sums = []
        for path in sorted(package.iterdir()):
            if path.is_file() and path.name != "SHA256SUMS":
                sums.append(f"{digest(path)}  {path.name}\n")
        (package / "SHA256SUMS").write_text("".join(sums))
        os.rename(package, output)

    result = {
        "status": "HOST_VERIFIED_NOT_DEVICE_TESTED",
        "output": str(output),
        "boot_sha256": digest(output / "boot.img"),
        "boot_bytes": (output / "boot.img").stat().st_size,
        "target_system_sha256": system_report["sha256"],
        "target_system_bytes": target_system.stat().st_size,
        "delta_sha256": digest(output / "system_from_r7_speaker.img.z1delta"),
        "delta_bytes": (output / "system_from_r7_speaker.img.z1delta").stat().st_size,
        "hal_sha256": HAL_SHA,
        "route_sha256": ROUTE_SHA,
        "runtime_manifest_sha256": digest(runtime),
        "boot_report": boot_report,
        "system_report": system_report,
        "delta_report": delta_report,
    }
    (output / "build-result.json").write_text(json.dumps(result, indent=2) + "\n")
    with (output / "SHA256SUMS").open("a") as sums:
        sums.write(f"{digest(output / 'build-result.json')}  build-result.json\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=ROOT / "out/r7_speaker_gpio_candidate_20261119",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "out/r7_audio_hal_paths_20261122",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("/dev/shm/z1-r7-audio-hal-paths"),
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        help="optional clean runtime-manifest input path (default: tracked script)",
    )
    args = parser.parse_args()
    try:
        result = build(args.base_dir, args.output, args.work_dir, args.runtime)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"z1_build_r7_audio_paths: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
