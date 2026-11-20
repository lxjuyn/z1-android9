#!/system/bin/sh
# Run after boot, unlocked. Exercise the physical touchscreen during this probe.
# Usage: sh z1_diag_stability.sh [duration_seconds>=600]
set -u

duration=${1:-600}
case "$duration" in ''|*[!0-9]*) echo 'duration must be an integer >=600' >&2; exit 2;; esac
[ "$duration" -ge 600 ] || { echo 'duration must be >=600 seconds' >&2; exit 2; }

fail() { printf 'Z1_STABILITY_END status=FAIL reason=%s\n' "$1"; exit 1; }
clock() {
    read -r up _ < /proc/uptime || return 1
    printf '%s\n' "${up%%.*}"
}
identity() {
    pid=$(pidof system_server) || return 1
    case "$pid" in ''|*[!0-9]*) return 1;; esac
    # Strip the parenthesized comm field; starttime is then field 20.
    start=$(sed 's/^.*) //' "/proc/$pid/stat" 2>/dev/null | awk '$20 > 0 {print $20}')
    [ -n "$start" ] || return 1
    printf '%s:%s\n' "$pid" "$start"
}

initial=$(identity) || fail missing_system_server
begin=$(clock) || fail missing_uptime
printf 'Z1_STABILITY_BEGIN uptime=%s server=%s duration=%s physical_touch=manual\n' "$begin" "$initial" "$duration"
while :; do
    [ "$(getprop sys.boot_completed)" = 1 ] || fail boot_not_complete
    [ "$(identity)" = "$initial" ] || fail system_server_restarted_or_gone
    [ "$(timeout 8 service check input)" = 'Service input: found' ] || fail missing_input_service
    state=$(timeout 8 dumpsys -t 5 input 2>&1) || fail input_dump_failed
    # A registered Binder name alone does not establish a responsive service.
    # The timeout or an incomplete Android dump must never count as a pass.
    printf '%s\n' "$state" | awk '
        /Input Dispatcher State at time of last ANR:/ {exit}
        /Input Dispatcher State:/ {live=1; next}
        live && $1=="DispatchEnabled:" && $2==1 {enabled=1}
        live && $1=="DispatchFrozen:" && $2==0 {unfrozen=1}
        END {exit !(enabled && unfrozen)}' || fail input_not_dispatching
    for svc in vendor.gnss_service mnld z1_mnld; do
        case "$(getprop "init.svc.$svc")" in ''|stopped) ;; *) fail "unexpected_gnss_service:$svc";; esac
    done
    [ "$(identity)" = "$initial" ] || fail system_server_restarted_or_gone
    now=$(clock) || fail missing_uptime
    elapsed=$((now - begin))
    [ "$elapsed" -ge 0 ] || fail uptime_reset
    printf 'Z1_STABILITY_SAMPLE uptime=%s elapsed=%s server=%s input=dispatching gnss=inactive\n' "$now" "$elapsed" "$initial"
    [ "$elapsed" -lt "$duration" ] || break
    remain=$((duration - elapsed))
    [ "$remain" -le 15 ] || remain=15
    sleep "$remain"
done
printf 'Z1_STABILITY_END status=PASS elapsed=%s physical_touch=UNVERIFIED\n' "$elapsed"
