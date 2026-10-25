#!/system/bin/sh
# R3: load the polled z1_cst836u touch driver. The module instantiates its
# own i2c client at 0x15 on whichever adapter ACKs, so this only loads it,
# reports the outcome, and dumps the evidence needed to recalibrate axes.

log() { printf '<6>[z1touch] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "R3b touch load starting (kernel $(cat /proc/sys/kernel/osrelease 2>/dev/null))"

# Retry: an early post-fs run can race slower boot-class work.
try=0
while [ $try -lt 10 ]; do
    if [ -d /sys/module/z1_cst836u ]; then
        log "module already loaded"
        break
    fi
    if insmod /z1_cst836u.ko 2>/tmp/z1_touch_insmod.err; then
        log "z1_cst836u insmod OK (try $try)"
        break
    fi
    log "z1_cst836u insmod FAILED (try $try): $(cat /tmp/z1_touch_insmod.err 2>/dev/null)"
    sleep 3
    try=$((try + 1))
done

sleep 3

if grep -q 'z1-cst836u' /proc/bus/input/devices 2>/dev/null; then
    log "input device registered; orientation raw samples are in dmesg"
else
    log "input device NOT registered yet; chip may still be scanning (module retries for 5 minutes)"
fi

# Evidence snapshot for the ACM replay: i2c adapters and devices present.
for d in /sys/bus/i2c/devices/i2c-*; do
    [ -d "$d" ] || continue
    log "i2c adapter ${d##*/}: $(cat "$d/name" 2>/dev/null)"
done
for d in /sys/bus/i2c/devices/*-00*; do
    [ -d "$d" ] || continue
    log "i2c device ${d##*/}: $(cat "$d/name" 2>/dev/null)"
done
log "R3 touch load done"
exit 0
