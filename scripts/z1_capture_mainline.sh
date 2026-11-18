#!/bin/bash
# 抓 mainline kernel 启动日志，经 ESP32-C3 USB-SJAG ↔ UART1 桥。
# C3 IDF 固件 UART1 固定 921600；PC 侧 USB CDC line coding 始终保持 115200。
# 用法: z1_capture_mainline.sh [max_seconds] [image]（默认 3600 秒）
set -u

MAX="${1:-3600}"
IMAGE="${2:-${Z1_IMAGE:-}}"
IMAGE_SHA256="${Z1_IMAGE_SHA256:-}"
LOG_DIR=/home/lxj/claude/6572/mainline_recon
LOCK_FILE=/tmp/z1_mainline_capture.lock
PID_FILE=/tmp/z1_mainline_capture.pid
PID_FILE_CREATED=0
LOG=
CAP_PID=
READER_STATUS=not-started

case "$MAX" in
    ''|*[!0-9]*) printf 'ERROR: max_seconds must be a positive integer\n' >&2; exit 2 ;;
esac
[ "$MAX" -gt 0 ] || { printf 'ERROR: max_seconds must be greater than zero\n' >&2; exit 2; }

PORT="${C3_PORT:-}"
PORT_KIND=c3-usb-cdc
if [ -z "$PORT" ]; then
    link=
    for pat in 'usb-Espressif_USB_JTAG_serial_debug_unit_*' \
               'usb-GigaDevice_*' 'usb-1a86_*' 'usb-QinHeng_*'; do
        link=$(find /dev/serial/by-id -maxdepth 1 -type l -name "$pat" 2>/dev/null | head -n1)
        [ -n "$link" ] && break
    done
    [ -n "$link" ] && PORT=$(readlink -f "$link")
fi
[ -n "$PORT" ] && [ -c "$PORT" ] || {
    printf 'ERROR: USB serial port not found (Espressif / GigaDevice / 1a86 / QinHeng by-id)\n' >&2
    printf 'Available /dev/serial/by-id ports:\n' >&2
    ls -1 /dev/serial/by-id/ 2>/dev/null | sed 's/^/  /' >&2 || printf '  (none)\n' >&2
    exit 1
}
case "$PORT" in
    /dev/ttyACM*) PORT_KIND=ch340-uart ;;
esac
if [ -n "${Z1_CAPTURE_PORT_KIND:-}" ]; then
    PORT_KIND=$Z1_CAPTURE_PORT_KIND
fi
case "$PORT_KIND" in
    c3-usb-cdc)
        HOST_BAUD=115200
        UART1_BAUD=921600
        ;;
    ch340-uart)
        # CH340 直连 Z1 UART1, 波特率须匹配当前 Z1 console(115200, 用 921600 会复现 console 冻结)
        HOST_BAUD=115200
        UART1_BAUD=115200
        ;;
    *)
        printf 'ERROR: unsupported capture port kind: %s\n' "$PORT_KIND" >&2
        exit 1
        ;;
esac
# Z1_CAPTURE_BAUD 覆盖抓取波特率(当前 Z1 console=115200);
# CH340 直连时 wire baud == console baud, 同步 UART1_BAUD。
if [ -n "${Z1_CAPTURE_BAUD:-}" ]; then
    HOST_BAUD=$Z1_CAPTURE_BAUD
    [ "$PORT_KIND" = ch340-uart ] && UART1_BAUD=$Z1_CAPTURE_BAUD
fi

if [ -n "$IMAGE" ]; then
    [ -f "$IMAGE" ] && [ -r "$IMAGE" ] || { printf 'ERROR: image is not a readable regular file: %s\n' "$IMAGE" >&2; exit 1; }
    IMAGE=$(readlink -f "$IMAGE")
    if [ -z "$IMAGE_SHA256" ]; then
        IMAGE_SHA256=$(sha256sum "$IMAGE") || { printf 'ERROR: cannot hash image: %s\n' "$IMAGE" >&2; exit 1; }
        IMAGE_SHA256=${IMAGE_SHA256%% *}
        IMAGE_HASH_SOURCE=computed
    else
        IMAGE_HASH_SOURCE=supplied
    fi
else
    IMAGE='<not-supplied>'
    IMAGE_SHA256='<not-supplied>'
    IMAGE_HASH_SOURCE=not-supplied
fi

if ! exec 9>"$LOCK_FILE"; then
    printf 'ERROR: cannot open capture lock: %s\n' "$LOCK_FILE" >&2
    exit 1
fi
if ! flock -n 9; then
    printf 'ERROR: another Z1 capture is already running\n' >&2
    exit 1
fi

cleanup() {
    local rc=$?
    trap - EXIT INT TERM HUP
    if [ -n "$CAP_PID" ] && kill -0 "$CAP_PID" 2>/dev/null; then
        # Reader runs in its own session; terminate timeout and tr together.
        kill -TERM -- "-$CAP_PID" 2>/dev/null || kill "$CAP_PID" 2>/dev/null || true
        wait "$CAP_PID" 2>/dev/null || true
    fi
    if [ -n "$LOG" ] && [ -e "$LOG" ]; then
        {
            printf 'capture_end=%s\n' "$(date -Is 2>/dev/null || date)"
            printf 'reader_status=%s\n' "$READER_STATUS"
            printf 'script_exit_status=%s\n' "$rc"
        } >&3 2>/dev/null || true
        exec 3>&- 2>/dev/null || true
    fi
    [ "$PID_FILE_CREATED" = 1 ] && rm -f "$PID_FILE"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

if ! mkdir -p "$LOG_DIR"; then
    printf 'ERROR: cannot create log directory: %s\n' "$LOG_DIR" >&2
    exit 1
fi
LOG="$LOG_DIR/z1_boot_mainline_$(date +%Y%m%d_%H%M%S 2>/dev/null || echo boot).log"
if [ -e "$LOG" ]; then
    LOG="${LOG%.log}_$$.log"
fi
if ! exec 3>>"$LOG"; then
    printf 'ERROR: cannot open log: %s\n' "$LOG" >&2
    exit 1
fi

{
    printf 'capture_start=%s\n' "$(date -Is 2>/dev/null || date)"
    printf 'script=%s\n' "$0"
    printf 'max_seconds=%s\n' "$MAX"
    printf 'port=%s\n' "$PORT"
    printf 'port_kind=%s\n' "$PORT_KIND"
    printf 'bridge_usb_line_coding=%s\n' "$HOST_BAUD"
    printf 'bridge_uart1_baud=%s\n' "$UART1_BAUD"
    printf 'image=%s\n' "$IMAGE"
    printf 'image_sha256=%s\n' "$IMAGE_SHA256"
    printf 'image_hash_source=%s\n' "$IMAGE_HASH_SOURCE"
    printf '%s\n' 'capture_payload_begin'
} >&3 || { printf 'ERROR: cannot write capture metadata: %s\n' "$LOG" >&2; exit 1; }

# C3 USB-SJAG remains a USB byte stream at 115200; a direct CH340 UART
# adapter must instead follow Z1 UART1 at the current console baud (115200).
if ! stty -F "$PORT" "$HOST_BAUD" raw -echo -echoe -echok -echoctl -echoke; then
    printf 'ERROR: failed to configure capture port: %s\n' "$PORT" >&2
    exit 1
fi

# 不预读/不 drain：DEBUG_LL 的第一字节（例如 entry marker !）必须保留。
# 新 session 使信号清理可同时终止 timeout 和 tr。
PAYLOAD_BEGIN_BYTES=$(wc -c < "$LOG" 2>/dev/null || printf 0)
# Flush each serial read immediately so short shell replies reach z1push before its timeout.
setsid timeout --foreground "$MAX" stdbuf -o0 tr -d '\000' < "$PORT" >&3 2>&3 &
CAP_PID=$!
printf '%s\n' "$$" > "$PID_FILE" || { printf 'ERROR: cannot write PID file: %s\n' "$PID_FILE" >&2; exit 1; }
PID_FILE_CREATED=1
printf '[cap] ready: port=%s kind=%s host=%s UART1=%s log=%s max=%ss\n' "$PORT" "$PORT_KIND" "$HOST_BAUD" "$UART1_BAUD" "$LOG" "$MAX"

if wait "$CAP_PID"; then
    READER_STATUS=0
else
    READER_STATUS=$?
fi
CAP_PID=
TOTAL_BYTES=$(wc -c < "$LOG" 2>/dev/null || printf 0)
PAYLOAD_BYTES=$((TOTAL_BYTES - PAYLOAD_BEGIN_BYTES))
printf 'capture_payload_end\nreader_status=%s\npayload_bytes=%s\n' "$READER_STATUS" "$PAYLOAD_BYTES" >&3
printf '[cap] finished: reader_status=%s payload_bytes=%s log=%s\n' "$READER_STATUS" "$PAYLOAD_BYTES" "$LOG"

# timeout(124) is an expected bounded capture completion; other reader errors propagate.
[ "$READER_STATUS" = 0 ] || [ "$READER_STATUS" = 124 ]
