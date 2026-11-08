#!/system/bin/sh
# z1_gpu_gate.sh — keep Mesa/lima opt-in; the default is the SwiftShader fallback.
#
# WHY THIS WORKS (all verified against this device's own binaries/logs):
#  * A9 libEGL's load_system_driver() picks a driver out of /vendor/lib/egl by
#    file name.  Board evidence, same boot.img, only the system partition differs:
#      mesa present -> "libEGL : loaded /vendor/lib/egl/libGLES_mesa.so"
#        (mainline_recon/z1_boot_mainline_20261031_230505.log:1578)
#      mesa absent  -> "libEGL : loaded /vendor/lib/egl/libEGL_swiftshader.so"
#        (mainline_recon/z1_boot_mainline_20261101_095615.log)
#    There is no fallback once the dso is chosen (egl.cpp caches it), so the
#    only lever is "is a libGLES_* file visible in that directory".
#  * /system is mounted ro (fstab.z1: /dev/block/mmcblk0p4 /system ext4 ro,noatime)
#    and CONFIG_OVERLAY_FS is not set (fork_linux/.config:6502), so the cheapest
#    reversible hiding mechanism is a tmpfs mounted *over* the directory
#    (mounting over a dir never writes to the underlying fs; CONFIG_TMPFS=y,
#    .config:6541), repopulated with the three SwiftShader libs.
#  * Do NOT try "bind-mount the original dir somewhere, then tmpfs over it":
#    init.rc:327 does `mount rootfs rootfs / shared rec`, and a bind mount of
#    that directory joins the shared peer group, so the tmpfs mount propagates
#    onto the bind too and you get an ELOOP. Reproduced on the host.
#
# SAFE POINT IN BOOT: run from post-fs-data.  Measured on the board
# (mainline_recon/z1_boot_mainline_20261104_221018.log):
#   post-fs 11.36s, post-fs-data 14.97s, "starting service 'surfaceflinger'" 18.57s.
#
# FAIL-OPEN BY DESIGN: if anything is missing (no stage copy, tmpfs refused,
# short copy) the gate restores the original directory and leaves mesa in place.
# A wrong "hide" is worse than a wrong "keep": with the directory emptied the
# loader finds no driver at all and EGL init fails.
#
# Manual control (no adb, but the console shell is up: init.rc:743 + ro.debuggable=1,
# "init: starting service 'console'" at 18.67s, user shell):
#   disable mesa (persists across reboots):
#       touch /data/local/tmp/z1_gpu_disable   && reboot
#   re-enable mesa (overrides any disable marker):
#       touch /data/local/tmp/z1_gpu_force     && reboot
#   clear everything:
#       rm -f /data/local/tmp/z1_gpu_{disable,force} /cache/z1_gpu_* && reboot
#
# Env overrides exist only so the script can be exercised off-device:
#   Z1_EGL_DIR Z1_STAGE_DIR Z1_MARK_DIR Z1_CACHE_DIR

# init's exec/exec_background do not guarantee a usable PATH; the scripts call
# toybox applets (mount cp md5sum wc cut pidof dmesg grep sleep). Appending is
# harmless on a host used for dry runs.
case ":$PATH:" in
    *:/system/bin:*) : ;;
    *) PATH="$PATH:/system/bin:/vendor/bin:/sbin" ;;
esac
export PATH

EGL_DIR=${Z1_EGL_DIR:-/vendor/lib/egl}
STAGE_DIR=${Z1_STAGE_DIR:-/cache/z1_egl_stage}
MARK_DIR=${Z1_MARK_DIR:-/data/local/tmp}
CACHE_DIR=${Z1_CACHE_DIR:-/cache}
STATE_TAG=u:object_r:same_process_hal_file:s0

SWIFTSHADER="libEGL_swiftshader.so libGLESv1_CM_swiftshader.so libGLESv2_swiftshader.so"
MESA=libGLES_mesa.so

klog()  { [ -w /dev/kmsg ] && printf '<6>[z1gpu] gate: %s\n' "$*" > /dev/kmsg 2>/dev/null; return 0; }
kwarn() { [ -w /dev/kmsg ] && printf '<4>[z1gpu] gate: %s\n' "$*" > /dev/kmsg 2>/dev/null; return 0; }
prop()  { command -v getprop >/dev/null 2>&1 && getprop "$1" 2>/dev/null; return 0; }

# ---------------------------------------------------------------- decision
forced=0
[ -e "$MARK_DIR/z1_gpu_force" ]      && forced=1
[ -e "$CACHE_DIR/z1_gpu_force" ]     && forced=1
[ -e "/data/z1_gpu_force" ]          && forced=1
[ "$(prop persist.sys.z1.gpu.force)" = "1" ] && forced=1

disabled=0
[ -e "$MARK_DIR/z1_gpu_disable" ]    && disabled=1
[ -e "$CACHE_DIR/z1_gpu_disable" ]   && disabled=1
[ -e "/data/z1_gpu_disable" ]        && disabled=1
[ "$(prop persist.sys.z1.gpu.disabled)" = "1" ] && disabled=1

# 2026-11-08 (v33): 这个"安全网"本身是坏的 —— 它藏掉 mesa 后把 SwiftShader 三件套装进
# tmpfs，但那些库在那份镜像里 dlopen 不进去：
#   E libEGL: load_driver(/vendor/lib/egl/libEGL_swiftshader.so): dlopen failed
#   F libEGL: couldn't find an OpenGL ES implementation
# 结果 SurfaceFlinger 直接崩，系统进"无限重启"。实测（mainline_recon/z1_boot_mainline_
# 20261108_135205.log）：它只在 md5 层面比对文件，从不验证能不能加载，所以"失败关闭"
# 的设计在这里变成了必崩。为了不再把整机拖进启动循环，这条路径改成**永不隐藏 mesa**：
# 只在权限允许时把状态写进 /cache 供取证，然后原样退出。
#
# 要恢复"藏 mesa 换 SwiftShader"，必须先证明 SwiftShader 三件套在本镜像里能 dlopen
# （例如先在板上跑 eglChooseConfig 冒烟测试），再删掉下面这个 exit 0。
klog "v33: gate disabled -- mesa is never hidden (SwiftShader fallback broken in this image); forced=$forced disabled=$disabled"
mkdir -p "$CACHE_DIR" 2>/dev/null
( printf 'gate=disabled mesa=kept forced=%s disabled=%s\n' "$forced" "$disabled" \
    > "$CACHE_DIR/z1_gpu_gate.status" ) 2>/dev/null
exit 0

if [ "$forced" = "1" ]; then
    # A deliberate re-enable also rearms the watcher's reboot guard.
    rm -f "$MARK_DIR/.z1_gpu_autorebooted" "$CACHE_DIR/z1_gpu_autorebooted" 2>/dev/null
    klog "force marker set -> mesa left ENABLED (disabled=$disabled ignored)"
    exit 0
fi
if [ "$disabled" != "1" ]; then
    klog "no disable marker -> gate idle, mesa ENABLED"
    exit 0
fi
if [ ! -d "$EGL_DIR" ]; then
    kwarn "disable requested but $EGL_DIR does not exist; nothing to do"
    exit 0
fi
if [ ! -e "$EGL_DIR/$MESA" ]; then
    klog "disable requested; $MESA already absent -> SwiftShader is the only driver"
    exit 0
fi

# ---------------------------------------------------------------- stage
# Copy the three SwiftShader libs out of the directory BEFORE hiding it.
mkdir -p "$STAGE_DIR" 2>/dev/null
for f in $SWIFTSHADER; do
    src="$EGL_DIR/$f"
    dst="$STAGE_DIR/$f"
    if [ ! -s "$src" ]; then
        kwarn "PREFlight FAIL: $src missing/empty"
        if [ -s "$dst" ]; then
            kwarn "using previously staged $dst instead"
        else
            kwarn "no usable $f anywhere -> FAIL-OPEN, mesa stays enabled"
            exit 0
        fi
    elif [ ! -s "$dst" ] || [ "$(wc -c < "$src")" != "$(wc -c < "$dst")" ]; then
        cp -f "$src" "$dst" 2>/dev/null
    fi
    [ -s "$dst" ] || { kwarn "PREFlight FAIL: cannot stage $f -> FAIL-OPEN"; exit 0; }
done

# Content fingerprint of the staged copies. md5sum is present in this image's
# toybox (inode /bin/md5sum -> toybox), but fall back to sizes if it ever is not.
have_md5=0
command -v md5sum >/dev/null 2>&1 && have_md5=1
fingerprint() {
    out=""
    for f in $SWIFTSHADER; do
        if [ "$have_md5" = "1" ]; then
            out="$out $(md5sum "$1/$f" 2>/dev/null | cut -d' ' -f1)"
        else
            out="$out $(wc -c < "$1/$f" 2>/dev/null)"
        fi
    done
    echo "$out"
}
before=$(fingerprint "$STAGE_DIR")
klog "staged:$before"

# ---------------------------------------------------------------- hide
mounted=0
if mount -t tmpfs -o "size=16m,mode=0755,context=$STATE_TAG" tmpfs "$EGL_DIR" 2>/dev/null; then
    mounted=1
elif mount -t tmpfs -o "size=16m,mode=0755" tmpfs "$EGL_DIR" 2>/dev/null; then
    mounted=1
    kwarn "tmpfs mounted WITHOUT context= (SELinux permissive only)"
fi
if [ "$mounted" != "1" ]; then
    kwarn "mount tmpfs on $EGL_DIR FAILED -> FAIL-OPEN, mesa stays enabled"
    exit 0
fi

rc=0
for f in $SWIFTSHADER; do
    cp -f "$STAGE_DIR/$f" "$EGL_DIR/$f" 2>/dev/null || rc=1
done
sync 2>/dev/null

# ---------------------------------------------------------------- verify
if [ "$rc" = "0" ] && [ ! -e "$EGL_DIR/$MESA" ]; then
    after=$(fingerprint "$EGL_DIR")
    if [ "$before" = "$after" ]; then
        klog "ACTIVE: $MESA hidden, SwiftShader trio restored intact:$after"
        klog "next boot with mesa: touch $MARK_DIR/z1_gpu_force (or rm the disable marker)"
        mkdir -p "$CACHE_DIR" 2>/dev/null
        ( printf 'gate=active stage=%s md5=%s\n' "$STAGE_DIR" "$after" \
            > "$CACHE_DIR/z1_gpu_gate.status" ) 2>/dev/null
        exit 0
    fi
    kwarn "md5 mismatch after copy (before=$before after=$after)"
else
    kwarn "copy failed (rc=$rc) or $MESA still visible"
fi

# ---------------------------------------------------------------- rollback
umount "$EGL_DIR" 2>/dev/null
if [ -e "$EGL_DIR/$MESA" ]; then
    kwarn "rolled back: original $EGL_DIR restored, mesa enabled"
else
    kwarn "ROLLBACK FAILED: $EGL_DIR is empty and mesa is gone -- EGL will fail"
fi
exit 0
