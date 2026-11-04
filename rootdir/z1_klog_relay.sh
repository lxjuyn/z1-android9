#!/system/bin/sh
# Z1 调试：把 Android logcat 直接送到内核 kmsg → CH340 串口。
# 为什么需要它：本机的 ACM USB gadget 曾在全项目日志里 0 命中，CH340 串口是唯一
# 被证实可用的通道（本节就是靠它抓到完整启动日志的）。
log() { printf '<6>[z1klog] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "klog relay start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s"

i=0
while [ ! -e /dev/socket/logd ] && [ $i -lt 60 ]; do sleep 2; i=$((i+2)); done
log "logd socket ready after ${i}s ($([ -e /dev/socket/logd ] && echo yes || echo NO))"

# 2026-11-04: the line filter below only keeps lines containing FATAL, so every
# ART stack trace ("Process: ...", "java.lang.Xxx", "at ...") was thrown away --
# across the whole project history there are zero Settings/Home stack traces.
# Android 9 keeps Java crashes in the logcat **crash** buffer, so dump that
# buffer verbatim (it only holds crash entries) once the framework has settled.
# Also try dropbox and the newest native tombstones.
drop_once() {
    i=0
    while [ "$(/system/bin/getprop sys.boot_completed 2>/dev/null)" != "1" ] && [ $i -lt 600 ]; do
        sleep 2; i=$((i+2))
    done
    sleep 45
    log "---- crash dump begin (boot_completed after ${i}s) ----"

    log "===== logcat -b crash (verbatim) ====="
    /system/bin/logcat -d -b crash -t 4000 2>/dev/null | head -400 | while IFS= read -r L; do
        printf '<6>[z1crash] %s\n' "$L" > /dev/kmsg 2>/dev/null || true
    done

    log "===== logcat -b main (FATAL window) ====="
    /system/bin/logcat -d -b main -t 3000 2>/dev/null | grep -aE 'FATAL|AndroidRuntime|Process:|Exception|Caused by|^\s+at ' | head -200 | while IFS= read -r L; do
        printf '<6>[z1crash] %s\n' "$L" > /dev/kmsg 2>/dev/null || true
    done

    n=0
    for f in $(ls -t /data/system/dropbox/data_app_crash@*.txt 2>/dev/null | head -4); do
        n=$((n+1)); log "===== dropbox ${f##*/} ====="
        head -60 "$f" 2>/dev/null | while IFS= read -r L; do
            printf '<6>[z1crash] %s\n' "$L" > /dev/kmsg 2>/dev/null || true
        done
    done
    [ "$n" = 0 ] && log "no data_app_crash files under /data/system/dropbox"

    for t in $(ls -t /data/tombstones/tombstone_* 2>/dev/null | head -2); do
        log "===== tombstone ${t##*/} ====="
        head -30 "$t" 2>/dev/null | while IFS= read -r L; do
            printf '<6>[z1crash] %s\n' "$L" > /dev/kmsg 2>/dev/null || true
        done
    done
    log "---- crash dump end ----"
}
drop_once &

while :; do
    /system/bin/logcat -d -b main -b system -b crash -t 2000 2>/dev/null \
      | grep -aiE 'FATAL|Abort message|signal [0-9]|SIGABRT|backtrace|libEGL|libGLES|eglInitialize|gralloc|hwcomposer|SurfaceFlinger|tombstone|Beginning of crash' \
      | while IFS= read -r L; do printf '<6>[z1klog] %s\n' "$L" > /dev/kmsg 2>/dev/null || true; done
    log "---- klog relay cycle done uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s ----"
    sleep 25
done
