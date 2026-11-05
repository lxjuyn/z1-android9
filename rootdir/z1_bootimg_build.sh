#!/system/bin/sh
# z1_bootimg_build — 上板一次性重建 ART boot image（.sf6 提速件）
#
# 背景：payload 的 /system/framework/arm 世代 boot.art/oat 与现役 ART 不匹配
# （patchoat 冻结根因），修复时被改名 arm.noimage，此后每次开机全解释执行
# + JIT = 30-60 分钟爬行。本服务在 sys.boot_completed=1 后等系统空闲，
# 用设备自己的 dex2oat 重建与现役运行时严格匹配的 boot image，写回
# /system/framework/arm/ —— 下次开机起加载，开机回到分钟级。
#
# 护栏：
#   * 已有可用 boot.art 则直接跳过（幂等）
#   * dex2oat 失败则删除半成品，/system 保持 arm.noimage 现状（可无限重试）
#   * 全程日志落 /cache/z1diag/bootimg_build.log
LOG=/cache/z1diag/bootimg_build.log
mkdir -p /cache/z1diag 2>/dev/null
echo "[z1bootimg] start $(date)" > "$LOG"

# 等开机后尘埃落定（后台 dexopt/启动期写入结束）再动 /system
sleep 600

if [ -f /system/framework/arm/boot.art ] && [ -s /system/framework/arm/boot.art ]; then
    echo "[z1bootimg] boot.art already present, skip" >> "$LOG"
    exit 0
fi

SID=$(cat /system/etc/z1_system_id 2>/dev/null | tr -d ' \r\n')
DONE=/data/misc/z1_bootimg_attempted
if [ -n "$SID" ] && [ -f "$DONE" ] && [ "$(cat "$DONE" 2>/dev/null | tr -d ' \r\n')" = "$SID" ]; then
    echo "[z1bootimg] already attempted for $SID, skip (delete $DONE to retry)" >> "$LOG"
    exit 0
fi

echo "[z1bootimg] remount rw /" >> "$LOG"
if ! mount -o remount,rw /system >> "$LOG" 2>&1; then
    echo "[z1bootimg] remount rw FAILED, abort" >> "$LOG"
    exit 1
fi

mkdir -p /system/framework/arm

DEXARGS=""
for jar in $(echo "$BOOTCLASSPATH" | tr ':' ' '); do
    if [ -f "$jar" ]; then
        DEXARGS="$DEXARGS --dex-file=$jar"
    else
        echo "[z1bootimg] WARN missing boot jar: $jar" >> "$LOG"
    fi
done
echo "[z1bootimg] dex inputs: $(echo $DEXARGS | wc -w)" >> "$LOG"

# Mark BEFORE running: a crash or a reboot in the middle of dex2oat must not
# turn into an infinite retry of a 30+ minute job on every boot.
mkdir -p /data/misc 2>/dev/null
echo "$SID" > "$DONE"

/system/bin/dex2oat -j2 $DEXARGS \
    --image=/system/framework/arm/boot.art \
    --oat-file=/system/framework/arm/boot.oat \
    --instruction-set=arm \
    --instruction-set-features=default \
    --image-classes=/system/etc/preloaded-classes \
    --runtime-arg -Xms64m --runtime-arg -Xmx128m \
    >> "$LOG" 2>&1
rc=$?
echo "[z1bootimg] dex2oat rc=$rc" >> "$LOG"

if [ "$rc" -eq 0 ] && [ -s /system/framework/arm/boot.oat ]; then
    echo "[z1bootimg] SUCCESS — 下次开机加载 boot image（提速生效）" >> "$LOG"
else
    echo "[z1bootimg] FAILED — 清理半成品，保持现状" >> "$LOG"
    rm -f /system/framework/arm/boot.art \
          /system/framework/arm/boot.oat \
          /system/framework/arm/boot-*.art
fi

sync
mount -o remount,ro /system >> "$LOG" 2>&1
echo "[z1bootimg] done $(date)" >> "$LOG"
