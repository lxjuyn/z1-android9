#!/usr/bin/env python3
"""Rewrite only the Z1 mnld service block in an offline init rc copy."""

import argparse
import re
from pathlib import Path

SERVICE_START = b"service z1_mnld /vendor/bin/mnld\n"
SERVICE_ALREADY = b"service mnld /vendor/bin/mnld\n"
AUTO_PREP = re.compile(
    rb"^(on property:sys\.z1\.radio\.modules_ready=1\n)"
    rb"    exec_background (?:u:r:init:s0 root root )?-- /system/bin/sh /z1_gps_prep\.sh\n",
    re.MULTILINE,
)
NEW_BLOCK = (
    b"service mnld /vendor/bin/mnld\n"
    b"    class main\n"
    b"    user system\n"
    b"    # Numeric IDs: system, nvram, gps, inet, misc, radio.\n"
    b"    group 1000 1050 1021 3003 9997 1001\n"
    b"    # The factory HAL runs as user gps and must connect to this socket.\n"
    b"    socket mnld stream 0660 gps system\n"
    b"    disabled\n"
    b"    # Keep one-shot while the real GPS startup path is under board test.\n"
    b"    oneshot\n"
    b"    seclabel u:r:init:s0\n"
    b"\n"
)


def transform(data):
    if b"\0" in data:
        raise ValueError("NUL byte in init rc")

    lines = data.splitlines(keepends=True)
    starts = [
        i for i, line in enumerate(lines)
        if line in (SERVICE_START, SERVICE_ALREADY)
    ]
    if len(starts) != 1:
        raise ValueError(
            f"expected exactly one mnld service declaration, found {len(starts)}"
        )
    start = starts[0]
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.startswith((b" ", b"\t")):
            break
        end += 1

    block = b"".join(lines[start:end])
    if b"/vendor/bin/mnld" not in block or b"socket mnld stream" not in block:
        raise ValueError("mnld service block lacks expected executable or socket")

    output = b"".join(lines[:start]) + NEW_BLOCK + b"".join(lines[end:])
    if output.count(b"service mnld /vendor/bin/mnld\n") != 1:
        raise ValueError("output does not contain exactly one mnld service")
    if b"service z1_mnld " in output or b"start z1_mnld" in output:
        raise ValueError("output retains the legacy service name")
    output, removed = AUTO_PREP.subn(rb"\1", output)
    if removed > 1 or any(
        not line.lstrip().startswith(b"#") and b"/z1_gps_prep.sh" in line
        for line in output.splitlines()
    ):
        raise ValueError("unexpected automatic GPS prep action")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    data = args.input.read_bytes()
    rewritten = transform(data)
    args.output.write_bytes(rewritten)
    print(
        f"wrote {args.output}: {len(rewritten)} bytes; "
        "mnld socket owner=gps group=system"
    )


if __name__ == "__main__":
    main()
