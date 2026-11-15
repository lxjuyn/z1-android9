#!/usr/bin/env python3
"""Offline adaptation system copy, with two explicit audio asset extensions.

Uses the driver candidate's hash, inode, xattr, fsck and publication checks.
The existing driver candidate CLI keeps its original path whitelist. This
separate entry point additionally supports the installed audio loader and
primary playback policy, both at fixed paths that must already exist.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from release import z1_build_driver_candidate as base
from z1_build_adaptation_boot import check_arm

LOADER = "/lib/modules/load_audio.sh"
POLICY = "/vendor/etc/primary_audio_policy_configuration.xml"
ALLOWED = re.compile("(?:" + base.ALLOW.pattern + "|" +
                     "|".join(re.escape(p) for p in (LOADER, POLICY)) + ")")


def validate_payload(entry):
    with base.regular_open(entry["source"]) as source:
        data = source.read()
    base.require(hashlib.sha256(data).hexdigest() == entry["sha256"],
                 "Replacement SHA256 mismatch")
    path = entry["path"]
    if path.endswith((".ko", ".so")):
        check_arm(data, path)
    elif path == LOADER:
        base.require(data.startswith(b"#!/system/bin/sh\n"), "Invalid audio loader shebang")
        subprocess.run(["dash", "-n"], input=data, check=True, capture_output=True)
    elif path == POLICY:
        policy = ET.fromstring(data)
        base.require(policy.tag == "module" and policy.get("name") == "primary" and
                     policy.get("halVersion") == "2.0", "Invalid primary audio policy")
        ports = policy.findall("mixPorts/mixPort")
        base.require(ports and all(p.get("role") == "source" for p in ports) and
                     not any(p.get("type", "").startswith("AUDIO_DEVICE_IN_")
                             for p in policy.findall("devicePorts/devicePort")),
                     "Primary HAL currently supports playback only")


def execute(manifest, output=None, build=False):
    manifest = base.host_path(str(manifest), Path.cwd())
    parsed = base.load_manifest(manifest, allowed=ALLOWED)
    for entry in parsed["replacements"]:
        validate_payload(entry)
    return base.execute(manifest, output, build, allowed=ALLOWED)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(execute(args.manifest, args.output, args.build), indent=2))


if __name__ == "__main__":
    main()
