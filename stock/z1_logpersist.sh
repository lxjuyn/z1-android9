#!/system/bin/sh
# R3b: persist diagnostics onto /data so a dead USB link can no longer lose
# evidence. dmesg every cycle (atomic replace), wider snapshot every 3rd
# cycle, tiny beat log with rotation. Runs forever; init restarts on exit.

D=/data/local/z1diag
mkdir -p "$D" 2>/dev/null || exit 0
echo "=== logpersist start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s $(date) ===" >> "$D/beats.txt"
i=0
while :; do
    up=$(cut -d. -f1 /proc/uptime 2>/dev/null)
    dmesg > "$D/dmesg.txt.new" 2>/dev/null && mv "$D/dmesg.txt.new" "$D/dmesg.txt"

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
        } > "$D/snap.txt.new" && mv "$D/snap.txt.new" "$D/snap.txt"
    fi

    echo "beat $i up=${up}s" >> "$D/beats.txt"
    if [ "$(wc -c < "$D/beats.txt" 2>/dev/null || echo 0)" -gt 102400 ]; then
        tail -n 500 "$D/beats.txt" > "$D/beats.txt.new" 2>/dev/null && mv "$D/beats.txt.new" "$D/beats.txt"
    fi
    i=$((i + 1))
    sleep 20
done
