#!/system/bin/sh
# Passive, bounded power_supply evidence; run explicitly with sh.
# No sysfs writes, register/IIO access, service changes, or SOC estimation.
# /proc/uptime is boot-relative and includes suspend; it is not wall time.
# A row's timestamp is the start of its sweep, not an atomic multi-file read.

LC_ALL=C
export LC_ALL

usage() {
    cat <<'HELP'
Usage: sh z1_battery_sample.sh [options] > samples.tsv
  --root DIR          Filesystem/fixture root (default /).
                      Reads DIR/sys/class/power_supply and DIR/proc/uptime.
  --samples N         Sweeps, 1..600 (default 12).
  --interval N        Seconds between sweeps, 0..60 (default 5).
  --duration N        Whole-process timeout, 1..3600 seconds (default 90).
  --read-timeout N    Per-file timeout, 1..10 seconds (default 2).
  --max-supplies N    Supplies per sweep, 1..64 (default 16).
  --help              Show this help without sampling.

Reads only type, voltage_now (uV), capacity (%), status, online, and uptime.
Missing/empty fields are 'missing'; bad values 'invalid'; failed reads
'unreadable'; timed-out reads 'timeout'. No voltage-to-capacity conversion.
A missing supply directory emits a 'missing' row, not fabricated zeroes.
Requires sh, timeout (-k), head (-c), and sleep; no unbounded fallback.
Exit 0 means all requested sweeps ended; 2 is usage/dependency failure;
3 is clock/supply-limit failure; timeout exits nonzero (usually 124/137).
Only a '# end' footer certifies completion. Capture stderr separately.
Userspace deadlines cannot interrupt a kernel task stuck in D state.
HELP
}

fail() { printf '%s\n' "z1_battery_sample: $*" >&2; exit 2; }
uint() {
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    [ "${#1}" -le 9 ]
}
option_number() {
    uint "$2" || fail "$1 must be an integer"
    # Reject leading zeroes so shell arithmetic cannot interpret octal.
    case "$2" in 0|[1-9]) ;; 0*) fail "$1 must not have leading zeroes" ;; esac
    [ "$2" -ge "$3" ] && [ "$2" -le "$4" ] || fail "$1 out of range $3..$4"
}

worker=0
# Private re-entry used only by the parent timeout wrapper; limits remain
# validated in both processes. Do not invoke --bounded-worker directly.
if [ "${1:-}" = --bounded-worker ]; then worker=1; shift; fi
root=/
samples=12
interval=5
duration=90
read_timeout=2
max_supplies=16
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --root|--samples|--interval|--duration|--read-timeout|--max-supplies)
            [ "$#" -ge 2 ] || fail "missing value for $1"
            case "$1" in
                --root) root=$2 ;;
                --samples) option_number "$1" "$2" 1 600; samples=$2 ;;
                --interval) option_number "$1" "$2" 0 60; interval=$2 ;;
                --duration) option_number "$1" "$2" 1 3600; duration=$2 ;;
                --read-timeout) option_number "$1" "$2" 1 10; read_timeout=$2 ;;
                --max-supplies) option_number "$1" "$2" 1 64; max_supplies=$2 ;;
            esac
            shift 2 ;;
        *) fail "unknown option: $1" ;;
    esac
done
[ -d "$root" ] || fail "root must be an existing directory"
# Absolute paths also prevent option-looking fixture paths reaching head.
case "$root" in /*) ;; *) fail "root must be absolute" ;; esac
root=${root%/}
for tool in timeout head sleep sh; do
    command -v "$tool" >/dev/null 2>&1 || fail "required command missing: $tool"
done
if [ "$worker" -eq 0 ]; then
    # Bound the entire sweep process, including enumeration and sleep. -k
    # escalates TERM to KILL after one second; the worker owns no output files.
    timeout -k 1 "$duration" sh "$0" --bounded-worker \
        --root "${root:-/}" --samples "$samples" --interval "$interval" \
        --duration "$duration" --read-timeout "$read_timeout" \
        --max-supplies "$max_supplies"
    rc=$?
    [ "$rc" -eq 0 ] || printf 'z1_battery_sample: incomplete, exit=%s\n' "$rc" >&2
    exit "$rc"
fi

read_field() {
    field=$1
    path=$2
    if [ ! -e "$path" ]; then printf missing; return; fi
    # Never open fixture FIFOs, devices, or directories. Sysfs attributes
    # report regular-file mode even though their reported size is synthetic.
    if [ ! -f "$path" ] || [ ! -r "$path" ]; then printf unreadable; return; fi
    raw=$(timeout -k 1 "$read_timeout" head -c 129 "$path" 2>/dev/null)
    rc=$?
    case "$rc" in
        0) ;;
        124|137|143) printf timeout; return ;;
        *) printf unreadable; return ;;
    esac
    if [ -z "$raw" ]; then printf missing; return; fi
    if [ "${#raw}" -gt 128 ]; then printf invalid; return; fi
    case "$field" in
        voltage_now)
            if uint "$raw"; then printf '%s' "$raw"; else printf invalid; fi ;;
        capacity)
            if uint "$raw" && [ "$raw" -le 100 ]; then printf '%s' "$raw"; else printf invalid; fi ;;
        online)
            case "$raw" in 0|1) printf '%s' "$raw" ;; *) printf invalid ;; esac ;;
        status)
            case "$raw" in Unknown|Charging|Discharging|'Not charging'|Full) printf '%s' "$raw" ;; *) printf invalid ;; esac ;;
        type)
            case "$raw" in *[!A-Za-z0-9_-]*) printf invalid ;; *) printf '%s' "$raw" ;; esac ;;
        uptime) printf '%s' "$raw" ;;
    esac
}

printf 'sample\tmonotonic_s\tsupply\ttype\tvoltage_now_uV\tcapacity_pct\tstatus\tonline\n'
sample=0
last_seconds=0
last_fraction=0
while [ "$sample" -lt "$samples" ]; do
    clock=$(read_field uptime "$root/proc/uptime")
    mono=${clock%% *}
    seconds=${mono%%.*}
    fraction=${mono#*.}
    if [ "$fraction" = "$mono" ] || ! uint "$seconds" || ! uint "$fraction"; then
        printf 'z1_battery_sample: missing or invalid monotonic clock\n' >&2
        exit 3
    fi
    # uptime's integer part has no leading zeroes, except zero itself.
    case "$seconds" in 0|[1-9]) ;; 0*) exit 3 ;; esac
    # Normalize fractional seconds as a decimal integer without invoking
    # octal arithmetic (e.g. .09). Nine digits stay within signed 32-bit.
    while [ "${#fraction}" -lt 9 ]; do fraction=${fraction}0; done
    fraction_value=$((1$fraction - 1000000000))
    if [ "$seconds" -lt "$last_seconds" ] ||
       { [ "$seconds" -eq "$last_seconds" ] && [ "$fraction_value" -lt "$last_fraction" ]; }; then
        printf 'z1_battery_sample: monotonic clock moved backwards\n' >&2
        exit 3
    fi
    last_seconds=$seconds
    last_fraction=$fraction_value
    count=0
    for supply in "$root"/sys/class/power_supply/*; do
        [ -d "$supply" ] || continue
        if [ "$count" -ge "$max_supplies" ]; then
            printf 'z1_battery_sample: supply limit reached, incomplete sweep\n' >&2
            exit 3
        fi
        name=${supply##*/}
        case "$name" in ''|*[!A-Za-z0-9_.:-]*|missing)
            printf 'z1_battery_sample: unsupported supply name\n' >&2; exit 3 ;;
        esac
        type=$(read_field type "$supply/type")
        voltage=$(read_field voltage_now "$supply/voltage_now")
        capacity=$(read_field capacity "$supply/capacity")
        status=$(read_field status "$supply/status")
        online=$(read_field online "$supply/online")
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "$sample" "$mono" "$name" "$type" "$voltage" "$capacity" "$status" "$online"
        count=$((count + 1))
    done
    if [ "$count" -eq 0 ]; then
        printf '%s\t%s\tmissing\tmissing\tmissing\tmissing\tmissing\tmissing\n' "$sample" "$mono"
    fi
    sample=$((sample + 1))
    if [ "$sample" -lt "$samples" ] && [ "$interval" -gt 0 ]; then
        sleep "$interval" || exit 3
    fi
done
printf '# end\tsamples=%s\n' "$sample"
