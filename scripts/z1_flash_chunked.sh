#!/bin/bash
# Verified MTK chunk transfers; CLI: <partition> <image> [chunk_mb] [maxtries].
# Shared implementation holds /tmp/z1flash.lock through all reads and writes.
set -eu
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$HERE/z1_chunked_flash.py" write "$@"
