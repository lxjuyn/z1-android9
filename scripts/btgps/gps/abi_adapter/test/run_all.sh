#!/bin/bash
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../../../../.." && pwd)
CANDIDATE=${1:-"$ROOT/out/z1_gps_offline_candidate_20261119"}

bash -n "$HERE/../build.sh"
bash -n "$HERE/run_arm_qemu.sh"
bash -n "$HERE/run_actual_hal_qemu.sh"

PYTHONPATH="$HERE/..:$HERE" python3 -m unittest -v test_prepare_init_rc
"$HERE/run_arm_qemu.sh" "$ROOT/out/adaptation_candidate_20261115_flash/system.img"
"$HERE/run_actual_hal_qemu.sh" "$CANDIDATE/system.img"
python3 -B "$ROOT/z1a9_gpt/scripts/btgps/gps/contract_tests/test_gps_binary_contract.py"
python3 -B "$ROOT/z1a9_gpt/scripts/btgps/gps/test_init_rc_patch.py"
python3 -B "$ROOT/z1a9_gpt/scripts/btgps/gps/test_gps_scripts.py"
(cd "$CANDIDATE" && sha256sum -c SHA256SUMS)
e2fsck -fn "$CANDIDATE/system.img"
