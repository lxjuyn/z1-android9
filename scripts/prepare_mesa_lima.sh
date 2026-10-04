#!/usr/bin/env bash
set -euo pipefail

LOS_ROOT=${1:?usage: prepare_mesa_lima.sh /path/to/lineage16}
LOS_ROOT=$(cd "$LOS_ROOT" && pwd)
MESA_VERSION=19.1.8
MESA_SHA256=f0fe8289b7d147943bf2fc2147833254881577e8f9ed3d94ddb39e430e711725
CACHE_ROOT=${Z1_MESA_CACHE:-$LOS_ROOT/.cache/z1-mesa-lima}
ARCHIVE="$CACHE_ROOT/mesa-$MESA_VERSION.tar.xz"
SOURCE="$CACHE_ROOT/mesa-$MESA_VERSION"
MARKER="$SOURCE/.z1-archive-sha256"
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

mkdir -p "$CACHE_ROOT"
if [[ ! -f "$ARCHIVE" ]]; then
    curl --fail --location --retry 3 --output "$ARCHIVE.part" \
        "https://archive.mesa3d.org/older-versions/19.x/mesa-$MESA_VERSION.tar.xz"
    mv "$ARCHIVE.part" "$ARCHIVE"
fi
printf '%s  %s\n' "$MESA_SHA256" "$ARCHIVE" | sha256sum --check --status || {
    rm -f "$ARCHIVE"
    echo "Mesa $MESA_VERSION archive checksum mismatch" >&2
    exit 1
}

if [[ ! -f "$MARKER" ]] || [[ "$(cat "$MARKER")" != "$MESA_SHA256" ]] || \
        [[ ! -f "$SOURCE/Android.mk" ]] || ! rg -q 'lima\.HAVE_GALLIUM_LIMA' "$SOURCE/Android.mk"; then
    rm -rf -- "$SOURCE"
    mkdir -p "$SOURCE"
    tar -xJf "$ARCHIVE" --strip-components=1 -C "$SOURCE"
    printf '%s\n' "$MESA_SHA256" > "$MARKER"
fi

[[ "$(cat "$SOURCE/VERSION")" == "$MESA_VERSION" ]]
if rg -q '^MESA_PYTHON2 := python3$' "$SOURCE/Android.mk"; then
    sed -i 's/^MESA_PYTHON2 := python3$/MESA_PYTHON2 := python/' "$SOURCE/Android.mk"
elif ! rg -q '^MESA_PYTHON2 := python$' "$SOURCE/Android.mk"; then
    echo "Unexpected Mesa generator interpreter setting" >&2
    exit 1
fi

LIMA_SUBMIT="$SOURCE/src/gallium/drivers/lima/lima_submit.c"
sed -i \
    -e 's/\bdrmSyncobjCreate\b/z1_drmSyncobjCreate/g' \
    -e 's/\bDRM_SYNCOBJ_CREATE_SIGNALED\b/Z1_DRM_SYNCOBJ_CREATE_SIGNALED/g' \
    -e 's/\bdrmSyncobjDestroy\b/z1_drmSyncobjDestroy/g' \
    -e 's/\bdrmSyncobjImportSyncFile\b/z1_drmSyncobjImportSyncFile/g' \
    -e 's/\bdrmSyncobjWait\b/z1_drmSyncobjWait/g' \
    -e 's/\bdrmSyncobjExportSyncFile\b/z1_drmSyncobjExportSyncFile/g' \
    "$LIMA_SUBMIT"
if ! rg -q 'lima_syncobj_compat.h' "$LIMA_SUBMIT"; then
    sed -i '/#include "drm-uapi\/lima_drm.h"/a #include "lima_syncobj_compat.h"' "$LIMA_SUBMIT"
fi
cp "$REPO_ROOT/graphics/mesa/lima_syncobj_compat.h" \
    "$SOURCE/src/gallium/drivers/lima/lima_syncobj_compat.h"
EG_DEVICE="$SOURCE/src/egl/main/egldevice.c"
if ! rg -q 'z1_drmDevicesEqual' "$EG_DEVICE"; then
    sed -i \
        -e 's/drmDevicesEqual(/z1_drmDevicesEqual(/g' \
        -e '/#include <xf86drm.h>/a #include "egl_device_compat.h"' \
        "$EG_DEVICE"
fi
cp "$REPO_ROOT/graphics/mesa/egl_device_compat.h" \
    "$SOURCE/src/egl/main/egl_device_compat.h"
echo "$SOURCE"
