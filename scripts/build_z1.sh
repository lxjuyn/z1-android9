#!/usr/bin/env bash
set -eo pipefail
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${LOS_ROOT:?Set LOS_ROOT to the LineageOS 16.0 source checkout}"
: "${HOST_TOOLS:?Set HOST_TOOLS to the local host compatibility tools directory}"
export JAVA_HOME="$LOS_ROOT/prebuilts/jdk/jdk9/linux-x86"
export PATH="$HOST_TOOLS/python2/bin:$JAVA_HOME/bin:$PATH"
export Z1_KERNEL_VARIANT=${Z1_KERNEL_VARIANT:-stock}
case "$Z1_KERNEL_VARIANT" in stock|mainline) ;; *) echo "Invalid Z1_KERNEL_VARIANT" >&2; exit 2;; esac
export GOCACHE="$LOS_ROOT/.cache/go-build"
export LD_LIBRARY_PATH="$HOST_TOOLS/compat-root/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p "$GOCACHE"
cd "$LOS_ROOT"
source build/envsetup.sh
lunch lineage_z1-userdebug
m -j"${BUILD_JOBS:-8}" "$@"
