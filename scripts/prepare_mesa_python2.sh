#!/usr/bin/env bash
set -euo pipefail

LOS_ROOT=${1:?usage: prepare_mesa_python2.sh /path/to/lineage16}
LOS_ROOT=$(cd "$LOS_ROOT" && pwd)
CACHE_ROOT="$LOS_ROOT/.cache/z1-mesa-python-deps"
MODULE_ROOT="$LOS_ROOT/.cache/z1-python2"
MAKO_URL=https://files.pythonhosted.org/packages/50/78/f6ade1e18aebda570eed33b7c534378d9659351cadce2fcbc7b31be5f615/Mako-1.1.2-py2.py3-none-any.whl
MAKO_SHA256=8e8b53c71c7e59f3de716b6832c4e401d903af574f6962edbbbf6ecc2a5fe6c9
MARKUPSAFE_URL=https://files.pythonhosted.org/packages/b9/2e/64db92e53b86efccfaea71321f597fa2e1b2bd3853d8ce658568f7a13094/MarkupSafe-1.1.1.tar.gz
MARKUPSAFE_SHA256=29872e92839765e546828bb7754a68c418d927cd064fd4708fab9fe9c8bb116b
MARKER="$MODULE_ROOT/.z1-python2-deps"

mkdir -p "$CACHE_ROOT"
download_verified() {
    local url=$1
    local sha256=$2
    local output=$3
    if [[ ! -f "$output" ]] || ! printf '%s  %s\n' "$sha256" "$output" | sha256sum --check --status; then
        rm -f "$output" "$output.part"
        curl --fail --location --retry 3 --output "$output.part" "$url"
        printf '%s  %s\n' "$sha256" "$output.part" | sha256sum --check --status
        mv "$output.part" "$output"
    fi
}

MAKO_WHEEL="$CACHE_ROOT/Mako-1.1.2-py2.py3-none-any.whl"
MARKUPSAFE_TARBALL="$CACHE_ROOT/MarkupSafe-1.1.1.tar.gz"
download_verified "$MAKO_URL" "$MAKO_SHA256" "$MAKO_WHEEL"
download_verified "$MARKUPSAFE_URL" "$MARKUPSAFE_SHA256" "$MARKUPSAFE_TARBALL"

if [[ ! -f "$MARKER" ]] || \
        [[ "$(cat "$MARKER")" != "$MAKO_SHA256 $MARKUPSAFE_SHA256" ]]; then
    rm -rf -- "$MODULE_ROOT"
    mkdir -p "$MODULE_ROOT" "$CACHE_ROOT/markupsafe-src"
    unzip -q "$MAKO_WHEEL" 'mako/*' -d "$MODULE_ROOT"
    tar -xzf "$MARKUPSAFE_TARBALL" --strip-components=1 -C "$CACHE_ROOT/markupsafe-src"
    cp -a "$CACHE_ROOT/markupsafe-src/src/markupsafe" "$MODULE_ROOT/markupsafe"
    printf '%s\n' "$MAKO_SHA256 $MARKUPSAFE_SHA256" > "$MARKER"
fi

echo "$MODULE_ROOT"
