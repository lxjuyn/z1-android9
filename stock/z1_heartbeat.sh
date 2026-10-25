#!/system/bin/sh
# R3: 15-second heartbeat into kmsg. If the box ever freezes or suspends,
# the ACM replay timeline shows exactly when the beats stopped, and the
# uptime/freeze counters separate "userspace dead" from "kernel alive".
i=0
while :; do
    printf '<6>[z1hb] beat=%s uptime=%ss free=%skB tasks=%s\n' \
        "$i" \
        "$(cut -d. -f1 /proc/uptime 2>/dev/null)" \
        "$(awk '/MemFree/{print $2}' /proc/meminfo 2>/dev/null)" \
        "$(ls /proc 2>/dev/null | grep -c '^[0-9]')" \
        > /dev/kmsg 2>/dev/null || true
    i=$((i + 1))
    sleep 15
done
