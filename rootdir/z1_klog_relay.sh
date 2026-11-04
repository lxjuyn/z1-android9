#!/system/bin/sh
# Z1 调试通道：Android logcat / dmesg → /dev/kmsg → CH340 串口(921600)。
#
# 2026-11-04 v4 重写，目标：**尽可能丰富**地把现场送到串口，同时不把 921600 的
# 链路刷爆（实测 ttyS0 输入溢出过一次，所以每段都有行数上限）。
#
# v1-v3 的问题：用 grep 关键词过滤（FATAL/backtrace/...），而 ART 的堆栈行
# "Process: ..." / "java.lang.Xxx" / "    at ..." 一个关键词都不含，全被丢弃 ——
# 这是全项目历史零条 Java 堆栈的根本原因。v4 改用 logcat 的**优先级过滤**
# '*:W'（Warning 及以上），堆栈行是 E 级，天然全部保留，不需要猜关键词。
#
# 分段：
#   ① 周期性（每 30s）: logcat 全部 buffer 的 W/E/F + events buffer 的 am_crash
#   ② 周期性（每 5min）: 内核 dmesg 尾部 + 内存/进程快照
#   ③ 一次性（boot_completed+45s）: crash buffer 原文 + tombstone + dropbox
#      + dumpsys window/activity + /proc/meminfo + ps

log() { printf '<6>[z1relay] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
relay() { while IFS= read -r L; do printf '<6>[%s] %s\n' "$1" "$L" > /dev/kmsg 2>/dev/null || true; done; }

log "relay v4 start uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s"

i=0
while [ ! -e /dev/socket/logd ] && [ $i -lt 60 ]; do sleep 2; i=$((i+2)); done
log "logd ready after ${i}s ($([ -e /dev/socket/logd ] && echo yes || echo NO))"

deep_dump() {
    j=0
    while [ "$(/system/bin/getprop sys.boot_completed 2>/dev/null)" != "1" ] && [ $j -lt 600 ]; do
        sleep 2; j=$((j+2))
    done
    sleep 45
    log "==== DEEP DUMP begin (boot_completed after ${j}s) ===="

    log "---- crash buffer (verbatim) ----"
    /system/bin/logcat -d -b crash -t 4000 2>/dev/null | head -500 | relay z1crash

    log "---- events buffer: am_crash / am_anr / am_proc_died ----"
    /system/bin/logcat -d -b events -t 4000 2>/dev/null \
      | grep -aE 'am_crash|am_anr|am_proc_died|am_kill|boot_progress|sf_stop_bootanim|wm_boot_animation_done' \
      | head -200 | relay z1evt

    log "---- main/system FATAL window ----"
    /system/bin/logcat -d -b main -b system -t 4000 2>/dev/null \
      | grep -aE 'FATAL|AndroidRuntime|Process:|Exception|Caused by|^[[:space:]]+at ' \
      | head -250 | relay z1java

    # 2026-11-04: AOSP 9 AMS.processClass() reports FLAG_SYSTEM packages as
    # "system_app", so Settings / Home / com.android.phone crashes land in
    # **system_app_crash@*.txt**, not data_app_crash@*.txt.  v2/v4 only globbed
    # the data_app_ form, which is why we kept reporting "no crash files".
    n=0
    for f in $(ls -t /data/system/dropbox/system_app_crash@*.txt /data/system/dropbox/data_app_crash@*.txt 2>/dev/null | head -6); do
        n=$((n+1)); log "---- dropbox ${f##*/} ----"
        head -70 "$f" 2>/dev/null | relay z1drop
    done
    [ "$n" = 0 ] && log "no system_app_crash/data_app_crash files under /data/system/dropbox"
    log "---- dumpsys dropbox --print system_app_crash (head 120) ----"
    /system/bin/dumpsys dropbox --print system_app_crash 2>/dev/null | head -120 | relay z1drop
    log "---- dumpsys dropbox --print data_app_crash (head 80) ----"
    /system/bin/dumpsys dropbox --print data_app_crash 2>/dev/null | head -80 | relay z1drop

    for t in $(ls -t /data/tombstones/tombstone_* 2>/dev/null | head -3); do
        log "---- tombstone ${t##*/} (head 45) ----"
        head -45 "$t" 2>/dev/null | relay z1tomb
    done

    log "---- dumpsys window (focus) ----"
    /system/bin/dumpsys window 2>/dev/null | grep -aE 'mCurrentFocus|mFocusedApp|mDreamingLockscreen' | head -10 | relay z1dump
    log "---- dumpsys activity (resumed) ----"
    /system/bin/dumpsys activity activities 2>/dev/null | grep -aE 'mResumedActivity|ResumedActivity|topResumedActivity' | head -10 | relay z1dump
    log "---- ps -A (head 60) ----"
    /system/bin/ps -A 2>/dev/null | head -60 | relay z1dump
    log "---- /proc/meminfo (key lines) ----"
    grep -aE 'MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Cached:' /proc/meminfo 2>/dev/null | relay z1dump
    log "==== DEEP DUMP end ===="
}
deep_dump &

# ① 周期性：全部 buffer 的 Warning 及以上（含完整 Java 堆栈）
# ② 每 10 个周期（约 5 分钟）：内核 dmesg 尾部 + 内存快照
cyc=0
while :; do
    /system/bin/logcat -d -b main -b system -b crash -t 1200 '*:W' 2>/dev/null \
      | grep -av 'avc: *denied' | head -250 | relay z1warn

    if [ $((cyc % 10)) -eq 0 ]; then
        log "---- dmesg tail (cycle $cyc) ----"
        dmesg 2>/dev/null | tail -80 | relay z1dmesg
        log "---- mem ----"
        grep -aE 'MemTotal|MemFree|MemAvailable' /proc/meminfo 2>/dev/null | relay z1dmesg
    fi

    log "---- relay cycle $cyc done uptime=$(cut -d. -f1 /proc/uptime 2>/dev/null)s ----"
    cyc=$((cyc+1))
    sleep 30
done
