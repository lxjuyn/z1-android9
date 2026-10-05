#!/usr/bin/env bash
set -eo pipefail
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${LOS_ROOT:?Set LOS_ROOT to the LineageOS 16.0 source checkout}"
: "${HOST_TOOLS:?Set HOST_TOOLS to the local host compatibility tools directory}"
LOS_ROOT=$(cd -- "$LOS_ROOT" && pwd -P)
HOST_TOOLS=$(cd -- "$HOST_TOOLS" && pwd -P)
export LOS_ROOT HOST_TOOLS
command -v flock >/dev/null || { echo "flock is required to serialize Z1 builds" >&2; exit 2; }
mkdir -p "$LOS_ROOT/out"
exec 9>"$LOS_ROOT/out/.z1-build.lock"
flock -n 9 || { echo "another Z1 build is already using $LOS_ROOT" >&2; exit 2; }
TMP_BASE=${TMPDIR:-/tmp}
TMP_BASE=$(cd -- "$TMP_BASE" && pwd -P)
export JAVA_HOME="$LOS_ROOT/prebuilts/jdk/jdk9/linux-x86"
export PATH="$HOST_TOOLS/python2/bin:$JAVA_HOME/bin:$PATH"
export Z1_KERNEL_VARIANT=${Z1_KERNEL_VARIANT:-stock}
case "$Z1_KERNEL_VARIANT" in stock|mainline) ;; *) echo "Invalid Z1_KERNEL_VARIANT" >&2; exit 2;; esac
if [[ "$Z1_KERNEL_VARIANT" == "stock" ]]; then
    if ! python3 "$REPO_ROOT/scripts/apply_stock_patches.py" --source "$LOS_ROOT" --check; then
        echo "Stock build preflight failed: required stock source patches are missing or modified; refusing to build." >&2
        exit 2
    fi
fi
if [[ "$Z1_KERNEL_VARIANT" == "mainline" ]]; then
    export Z1_MESA_SOURCE_DIR="$(bash "$REPO_ROOT/scripts/prepare_mesa_lima.sh" "$LOS_ROOT")"
    MESA_PYTHON_CACHE="$LOS_ROOT/.cache/z1-python2"
    export PYTHONPATH="$MESA_PYTHON_CACHE${PYTHONPATH:+:$PYTHONPATH}"
    bash "$REPO_ROOT/scripts/prepare_mesa_python2.sh" "$LOS_ROOT"
    python -c 'import mako.template' >/dev/null
fi
export GOCACHE="$LOS_ROOT/.cache/go-build"
export LD_LIBRARY_PATH="$HOST_TOOLS/compat-root/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p "$GOCACHE"

# Soong's source scanner does not walk the local device-tree symlink used by
# this workspace. Materialize only that small source tree for the duration of
# the build so Android.bp modules are discovered. Restore the original link on
# success, error, or an interrupt.
DEVICE_TREE="$LOS_ROOT/device/jiabo/z1"
DEVICE_TREE_LINK_BACKUP=""
DEVICE_TREE_MATERIALIZED=0
restore_device_tree_link() {
    if [[ "$DEVICE_TREE_MATERIALIZED" == "1" ]]; then
        rm -rf -- "$DEVICE_TREE"
        mv -- "$DEVICE_TREE_LINK_BACKUP" "$DEVICE_TREE"
        DEVICE_TREE_MATERIALIZED=0
    fi
}
trap restore_device_tree_link EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ -L "$DEVICE_TREE" && "$(readlink -f "$DEVICE_TREE")" == "$REPO_ROOT" ]]; then
    DEVICE_TREE_LINK_BACKUP="$TMP_BASE/z1-device-tree-link.$$"
    [[ ! -e "$DEVICE_TREE_LINK_BACKUP" && ! -L "$DEVICE_TREE_LINK_BACKUP" ]]
    mv -- "$DEVICE_TREE" "$DEVICE_TREE_LINK_BACKUP"
    DEVICE_TREE_MATERIALIZED=1
    mkdir -- "$DEVICE_TREE"
    tar --exclude='./.git' --exclude='./release' --exclude='*/__pycache__' \
        -C "$REPO_ROOT" -cf - . | tar -C "$DEVICE_TREE" -xf -
fi

python3 "$REPO_ROOT/scripts/apply_art_patches.py" --source "$LOS_ROOT"
python3 "$REPO_ROOT/scripts/apply_art_patches.py" --source "$LOS_ROOT" --check
python3 "$REPO_ROOT/scripts/apply_native_path_patches.py" --source "$LOS_ROOT"
python3 "$REPO_ROOT/scripts/apply_native_path_patches.py" --source "$LOS_ROOT" --check

# Stock init executes this gate from the ramdisk before starting Android.
# Build it before product makefiles are parsed so device.mk can require and
# copy the exact static helper into the stock ramdisk. Mainline has no gate.
if [[ "$Z1_KERNEL_VARIANT" == "stock" ]]; then
    if [[ -z "${Z1_STOCK_MOUNT_GUARD:-}" ]]; then
        export Z1_STOCK_MOUNT_GUARD="$LOS_ROOT/out/target/product/z1/stock_mount_guard/z1_stock_mount_guard"
    elif [[ "$Z1_STOCK_MOUNT_GUARD" != /* ]]; then
        export Z1_STOCK_MOUNT_GUARD="$PWD/$Z1_STOCK_MOUNT_GUARD"
    else
        export Z1_STOCK_MOUNT_GUARD
    fi
    bash "$REPO_ROOT/stock/build_mount_guard.sh" "$Z1_STOCK_MOUNT_GUARD"
fi

cd "$LOS_ROOT"
source build/envsetup.sh
lunch lineage_z1-userdebug
m -j"${BUILD_JOBS:-8}" "$@"
