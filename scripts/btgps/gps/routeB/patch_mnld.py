#!/usr/bin/env python3
"""Disabled legacy Route B entrypoint; never modify an input or output binary.

The former patches hid gpsdrv open failures instead of implementing the
required interface. The first site's branch target was also unverified.
Retain existing blobs for forensic comparison only, not GPS deployment.
"""
import sys


def main():
    print("Route B disabled: cannot replace GPS driver failures with success; "
          "no binary read or written.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
