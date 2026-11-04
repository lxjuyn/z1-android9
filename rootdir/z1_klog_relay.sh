#!/system/bin/sh
# Z1 调试：把 Android logcat 直接送到内核 kmsg → CH340 串口。
# 为什么需要它：本机的 ACM USB gadget 从未枚举成功过（全项目日志 0 命中 0e8d:2006），
# 所以"开机后插 USB 看 logcat"这条路不存在。而 CH340 串口（ttyMT0@921600）是
# **唯一被证实可用**的通道（本节就是靠它抓到完整启动日志的）。
# 因此把 logcat 灌进 kmsg，即可从串口看到 Android 层日志（含 SurfaceFlinger 的 abort message）。
#
# 注意：只过滤与显示/图形/崩溃相关的 tag，避免把串口刷爆（kmsg 有 rate limit）。
log() { printf '<6>[z1klog] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

log "klog relay start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s"

# 等 logcat 可用（zygote 起来后才会有日志）
i=0
while [ ! -e /dev/socket/logd ] && [ $i -lt 60 ]; do sleep 2; i=$((i+2)); done
log "logd socket ready after ${i}s ($([ -e /dev/socket/logd ] && echo yes || echo NO))"

# 2026-11-04: one-shot dump of the Java crash stacks.  The filter below is
# line-based and only keeps lines containing FATAL, so every ART stack trace
# ("Process: ...", "java.lang.Xxx", "at ...") has been thrown away -- across
# the whole project history we have zero Settings/Home stack traces.  Android 9
# writes each Java FATAL into /data/system/dropbox/data_app_crash@*.txt, and
# this relay runs as root, so dump the newest few straight to kmsg once the
# framework has settled.  Also grab the newest native tombstones' headers.
drop_once() {
    i=0
    while [ "$(/system/bin/getprop sys.boot_completed 2>/dev/null)" != "1" ] && [ $i -lt 600 ]; do
        sleep 2; i=$((i+2))
    done
    sleep 45
    log "---- dropbox crash dump begin (boot_completed after ${i}s) ----"
    n=0
    for f in $(ls -t /data/system/dropbox/data_app_crash@*.txt 2>/dev/null | head -6); do
        n=$((n+1))
        log "===== [$n] ${f##*/} ====="
        grep -aE '^(Process|PID|Package|Flags|Package:|Subject|Build|Exception|Caused by|java\.|android\.|com\.|at |\s+at )' "$f" 2>/dev/null | head -60 | while IFS= read -r L; do
            printf '<6>[z1drop] %s\n' "$L" > /dev/kmsg 2>/dev/null || true
        done
    done
    [ "$n" = 0 ] && log "no data_app_crash files under /data/system/dropbox"
    for t in $(ls -t /data/tombstones/tombstone_* 2>/dev/null | head -2); do
        log "===== tombstone ${t##*/} ====="
        head -40 "$t" 2>/dev/null | while IFS= read -r L; do
            printf '<6>[z1drop] %s\n' "$L" > /dev/kmsg 2>/dev/null || true
        done
    done
    log "---- dropbox crash dump end ----"
}
drop_once &

# 分两批抓：① 全量 crash/fatal/abort ② 显示与图形栈相关
while :; do
    # ① 崩溃与致命错误（这是我们要的 SF abort 原因）
    /system/bin/logcat -d -b main -b system -b crash -t 2000 2>/dev/null \
      | grep -aiE 'FATAL|Abort message|signal [0-9]|SIGABRT|backtrace|libEGL|libGLES|eglInitialize|gralloc|hwcomposer|SurfaceFlinger|tombstone|Beginning of crash' \
      | while IFS= read -r L; do printf '<6>[z1klog] %s\n' "$L" > /dev/kmsg 2>/dev/null || true; done
    log "---- klog relay cycle done uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s ----"
    sleep 25
done
