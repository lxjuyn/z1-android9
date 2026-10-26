#!/system/bin/sh
# R3b: persist diagnostics onto /data so a dead USB link can no longer lose
# evidence. dmesg every cycle (atomic replace), wider snapshot every 3rd
# cycle, tiny beat log with rotation. Runs forever; init restarts on exit.

D=/data/local/z1diag
C=/cache/z1diag
mkdir -p "$D" 2>/dev/null || true
mkdir -p "$C" 2>/dev/null || true
[ -d "$D" ] || [ -d "$C" ] || exit 0
echo "=== logpersist start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s $(date) ===" >> "$D/beats.txt" 2>/dev/null
echo "=== logpersist start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s $(date) ===" >> "$C/beats.txt" 2>/dev/null
i=0
while :; do
    up=$(cut -d. -f1 /proc/uptime 2>/dev/null)
    for dir in "$D" "$C"; do
        [ -d "$dir" ] || continue
        dmesg > "$dir/dmesg.txt.new" 2>/dev/null && mv "$dir/dmesg.txt.new" "$dir/dmesg.txt"
    done

    if [ $((i % 3)) -eq 0 ]; then
        {
            echo "=== snapshot uptime=${up}s $(date) ==="
            echo '--- logcat tail ---'
            /system/bin/logcat -d -b main -b system -t 800 2>/dev/null
            echo '--- input devices ---'
            cat /proc/bus/input/devices 2>/dev/null
            echo '--- i2c devices ---'
            ls /sys/bus/i2c/devices 2>/dev/null
            echo '--- z1/boot/usb props ---'
            /system/bin/getprop 2>/dev/null | grep -iE 'z1|boot|usb|radio'
            echo '--- wake locks ---'
            cat /sys/power/wake_lock 2>/dev/null
            echo '--- swap ---'
            grep -E 'SwapTotal|SwapFree' /proc/meminfo 2>/dev/null
        } > "$D/snap.txt.new" && mv "$D/snap.txt.new" "$D/snap.txt"

        # R3e (A3-5): property-area sentinels. An empty ro.build.fingerprint or
        # an abilist vanished from the raw default_prop area means the
        # /dev/__properties__ region lost records again (R3b failure class).
        # An empty dd output counts as lost too: it means the area is unreadable.
        if [ -z "$(/system/bin/getprop ro.build.fingerprint 2>/dev/null)" ]; then
            printf '<3>[z1prop] AREA BROKEN: ro.build.fingerprint is empty\n' > /dev/kmsg 2>/dev/null || true
        fi
        raw=$(dd if=/dev/__properties__/u:object_r:default_prop:s0 bs=4096 2>/dev/null | strings 2>/dev/null | grep -c abilist)
        if [ -z "$raw" ] || [ "$raw" = "0" ]; then
            printf '<3>[z1prop] raw lost: abilist missing from default_prop area\n' > /dev/kmsg 2>/dev/null || true
        fi
    fi

    echo "beat $i up=${up}s" >> "$D/beats.txt"
    if [ "$(wc -c < "$D/beats.txt" 2>/dev/null || echo 0)" -gt 102400 ]; then
        tail -n 500 "$D/beats.txt" > "$D/beats.txt.new" 2>/dev/null && mv "$D/beats.txt.new" "$D/beats.txt"
    fi
    i=$((i + 1))
    sleep 20
done
