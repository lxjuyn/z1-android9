#!/usr/bin/env bash
# Materialize and verify the R7 Bluetooth candidate system image.
set -euo pipefail

HERE=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -P -- "$HERE/../../.." && pwd)
BASE="$ROOT/out/r7_speaker_gpio_candidate_20261119/system.img"
DELTA="$ROOT/out/r7_bt_candidate_20261120/system_from_r7_speaker.img.z1delta"
TOOL="$HERE/z1_image_block_delta.py"
EXPECTED_SYSTEM=650d2c4146162e49469454871eb3aa1ed607058d3e41dfa0aad8bcb772227449

if [[ $# != 1 ]]; then
    echo "usage: $0 <new-system.img>" >&2
    exit 2
fi
OUT=$1

for path in "$BASE" "$DELTA" "$TOOL"; do
    [[ -f "$path" && ! -L "$path" ]] || {
        echo "missing regular input: $path" >&2
        exit 2
    }
done
[[ ! -e "$OUT" && ! -L "$OUT" ]] || {
    echo "refusing to overwrite output: $OUT" >&2
    exit 2
}

python3 "$TOOL" apply --base "$BASE" --delta "$DELTA" --output "$OUT"
system_actual=$(sha256sum "$OUT" | awk '{print $1}')
[[ "$system_actual" = "$EXPECTED_SYSTEM" ]] || {
    echo "materialized system hash mismatch: $system_actual" >&2
    exit 3
}
[[ "$(stat -c%s "$OUT")" = 943718400 ]] || {
    echo "materialized system size mismatch" >&2
    exit 3
}
e2fsck -fn "$OUT" >/dev/null

declare -A expected=(
    [/bin/z1_linux7_radio_init.sh]=dff768b4846765f462abbc6bca6b3c041ccc1d1e8458172bc4289f99354872a5
    [/bin/z1_bt_hci_probe]=b0f783c0f295e183ec78454a4b410d7cc33fdb331f3e434384e443389096940e
    [/bin/z1diag/z1_diag_bt.sh]=96201f608e633b4da2a3877cecbeeabc4428f8369af10f63c2bf0f18ccbebbe6
    [/bin/z1_runtime_manifest]=c23f3bac57985757541d305859989383bc29a49e58f48aa2b268809c77f62075
)
for path in "${!expected[@]}"; do
    actual=$(debugfs -R "cat $path" "$OUT" 2>/dev/null | sha256sum)
    actual=${actual%% *}
    [[ "$actual" = "${expected[$path]}" ]] || {
        echo "readback mismatch: $path expected=${expected[$path]} actual=$actual" >&2
        exit 3
    }
done

printf 'system=%s\nsha256=%s\nbytes=%s\n' \
    "$OUT" "$system_actual" "$(stat -c%s "$OUT")"
