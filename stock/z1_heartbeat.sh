#!/system/bin/sh
# R3: 15-second heartbeat into kmsg. If the box ever freezes or suspends,
# the ACM replay timeline shows exactly when the beats stopped, and the
# uptime/freeze counters separate "userspace dead" from "kernel alive".
# R3e (A8-1/A5-5): zero-fork sampling. The R3b "stopped heart" was kmsg
# writes dropped by printk rate limiting, and every beat forking cut/awk/ls/
# grep made the loop fragile under memory pressure. up/tasks/free are now
# read with shell builtins only - the only external processes left are sleep
# and the one-shot cat for the battery voltage (per the R3e spec).
i=0
while :; do
    read up _ < /proc/uptime
    read _ _ _ tasks _ < /proc/loadavg
    free=
    while read key val _; do
        case $key in
            MemFree:) free=$val ;;
        esac
    done < /proc/meminfo
    voltage=$(cat /sys/class/power_supply/adc-battery/voltage_now 2>/dev/null)
    printf '<6>[z1hb] beat=%s uptime=%ss free=%skB tasks=%s voltage=%s\n' \
        "$i" "$up" "$free" "$tasks" "$voltage" \
        > /dev/kmsg 2>/dev/null || true
    i=$((i + 1))
    sleep 15
done
