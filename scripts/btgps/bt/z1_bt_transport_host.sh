#!/usr/bin/env bash
# Push and run the exclusive Z1 H4 transport gate over the serial shell.
set -euo pipefail

HERE=$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -P -- "$HERE/../../../.." && pwd)
PUSH="$ROOT/z1a9_gpt/scripts/z1deploy/z1push.sh"
GATE="$HERE/z1_bt_transport_gate.sh"
PROBE=${Z1_BT_PROBE:-"$ROOT/out/r7_bt_probe_writev_20261123_r2/z1_bt_hci_probe"}
REMOTE_STAGE=/data/local/tmp/z1stage
MODE=
DRY_RUN=0

usage() {
    cat <<'USAGE'
usage: z1_bt_transport_host.sh <write|writev> [--dry-run]

Environment:
  Z1LOG       append-only UART capture file (required online)
  Z1DEV       UART device; defaults to a by-id CH340/QinHeng adapter
  Z1_BT_PROBE probe executable; defaults to the pinned 2026-11-23 build

Run each mode after a separate cold boot. The device-side gate records boot_id
and refuses to compare two modes on the same boot.
USAGE
}

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage; exit 0 ;;
        write|writev)
            [[ -z "$MODE" ]] || {
                echo "mode specified more than once" >&2
                exit 2
            }
            MODE=$arg
            ;;
        *)
            echo "unknown argument: $arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

[[ -n "$MODE" ]] || {
    usage >&2
    exit 2
}
for path in "$PUSH" "$GATE" "$PROBE"; do
    [[ -f "$path" && ! -L "$path" ]] || {
        echo "missing regular file: $path" >&2
        exit 2
    }
done
[[ -x "$GATE" ]] || {
    echo "device gate is not executable: $GATE" >&2
    exit 2
}
[[ -x "$PROBE" ]] || {
    echo "probe is not executable: $PROBE" >&2
    exit 2
}

DEV=${Z1DEV:-}
if [[ -z "$DEV" ]]; then
    for pattern in \
        'usb-1a86_*' 'usb-QinHeng_*' \
        'usb-Espressif_USB_JTAG_serial_debug_unit_*' 'usb-GigaDevice_*'
    do
        link=$(find /dev/serial/by-id -maxdepth 1 -type l -name "$pattern" 2>/dev/null |
            head -n1 || true)
        if [[ -n "$link" ]]; then
            DEV=$(readlink -f "$link")
            break
        fi
    done
fi
DEV=${DEV:-/dev/ttyUSB0}

if [[ "$DRY_RUN" -eq 0 && -z "${Z1LOG:-}" ]]; then
    echo "Z1LOG must name the active append-only UART capture" >&2
    exit 2
fi

PROBE_SHA=$(sha256sum "$PROBE" | awk '{print $1}')
PUSH_OPTS=(--mode 0755)
ROOT_OPTS=(--wait 180)
if [[ "$DRY_RUN" -eq 1 ]]; then
    PUSH_OPTS+=(--dry-run)
    ROOT_OPTS+=(--dry-run)
fi

push_file() {
    local source=$1 remote=$2
    env Z1DEV="$DEV" Z1LOG="${Z1LOG:-}" \
        "$PUSH" "${PUSH_OPTS[@]}" "$source" "$remote"
}

remote_command="/system/bin/sh $REMOTE_STAGE/z1_bt_transport_gate.sh $MODE $REMOTE_STAGE/z1_bt_hci_probe $PROBE_SHA"

if [[ "$DRY_RUN" -eq 1 ]]; then
    push_file "$PROBE" z1_bt_hci_probe
    push_file "$GATE" z1_bt_transport_gate.sh
    env Z1DEV="$DEV" Z1LOG="${Z1LOG:-}" \
        "$PUSH" "${ROOT_OPTS[@]}" --rootcmd "$remote_command"
    exit 0
fi

mkdir -p "$ROOT/out"
LOG="$ROOT/out/r7_bt_transport_${MODE}_$(date +%Y%m%d_%H%M%S).log"
{
    printf 'started=%s\n' "$(date --iso-8601=seconds)"
    printf 'mode=%s\n' "$MODE"
    printf 'probe=%s\n' "$PROBE"
    printf 'probe_sha256=%s\n' "$PROBE_SHA"
    printf 'uart_device=%s\n' "$DEV"
    printf 'uart_capture=%s\n' "$Z1LOG"
    printf 'payload_begin\n'
} > "$LOG"

if ! push_file "$PROBE" z1_bt_hci_probe >> "$LOG" 2>&1; then
    echo "probe push failed; see $LOG" >&2
    tail -n 40 "$LOG" >&2
    exit 1
fi
if ! push_file "$GATE" z1_bt_transport_gate.sh >> "$LOG" 2>&1; then
    echo "gate push failed; see $LOG" >&2
    tail -n 40 "$LOG" >&2
    exit 1
fi
if ! env Z1DEV="$DEV" Z1LOG="$Z1LOG" \
    "$PUSH" "${ROOT_OPTS[@]}" --rootcmd "$remote_command" >> "$LOG" 2>&1; then
    echo "gate execution failed; see $LOG" >&2
    tail -n 80 "$LOG" >&2
    exit 1
fi

printf 'payload_end\nfinished=%s\n' "$(date --iso-8601=seconds)" >> "$LOG"
tail -n 120 "$LOG"
if ! grep -q "^TRANSPORT_RESULT=PASS mode=$MODE " "$LOG"; then
    echo "transport gate did not pass; see $LOG" >&2
    exit 1
fi
echo "transport_log=$LOG"
