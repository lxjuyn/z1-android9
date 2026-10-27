#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1_diag_all.sh - Z1 (MT6572 / Android 9 / arm32) one-shot evidence collector
#
# Collects every subsystem's passive evidence into
#   /data/local/z1diag/collect_<timestamp>/*.txt
# then packs it as
#   /data/local/z1diag/collect_<timestamp>.tar.gz
#
# STRICTLY PASSIVE / READ-ONLY towards the system: it never writes outside
# /data/local/z1diag, never insmod/rmmod, never touches GPIO/power, never
# starts a scan or a stream. Trigger actions live in the per-subsystem
# z1_diag_{wifi,bt,gps,audio}.sh deep-dive scripts.
#
# Compatibility: POSIX sh only (Android 9 /system/bin/sh = mksh + toybox).
#   - no bash arrays, no [[ ]], no local, no ${x:1:2} slicing
#   - every optional command is existence-checked and failure-guarded
#
# Usage:  sh /data/local/z1diag/z1_diag_all.sh [tag]
#   tag is optional, alnum/_/- only, appended to the directory name
#   e.g. sh z1_diag_all.sh afterboot  ->  collect_20261027_120000_afterboot/
# ---------------------------------------------------------------------------

PATH=/system/bin:/system/xbin:/vendor/bin:/sbin:/bin:$PATH
export PATH
umask 022

BASE=/data/local/z1diag
LOCK=$BASE/.running
SELF=$0
case "$SELF" in
    /*) ;;
    *) SELF=$(pwd 2>/dev/null)/$SELF ;;
esac

TAG=$1
case "$TAG" in
    *[!A-Za-z0-9_-]*) echo "z1_diag_all: tag '$TAG' has bad chars, ignoring"; TAG= ;;
esac

# --- automatic elevation ---------------------------------------------------
# Most evidence (dmesg, /proc/modules, /data/tombstones) needs uid 0.
# One non-interactive try via su, but ONLY when a timeout binary exists: an
# su that decides to ask for a password would otherwise hang the collector
# forever. If it cannot elevate we continue and record RUN_UID in facts.txt.
if [ "$(id -u 2>/dev/null)" != "0" ] && [ -z "$Z1DIAG_ELEVATED" ]; then
    if command -v timeout >/dev/null 2>&1 && command -v su >/dev/null 2>&1; then
        if timeout 5 su -c true >/dev/null 2>&1; then
            Z1DIAG_ELEVATED=1
            export Z1DIAG_ELEVATED
            exec su -c "Z1DIAG_ELEVATED=1 sh '$SELF' '$TAG'"
        fi
    fi
fi

# --- helpers ---------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

# cap <outfile> <label> <shell command string>
# Appends a labelled block. The command string is evaluated by a child sh so
# that globs/pipes/loops inside it work; all failures are swallowed.
cap() {
    _o=$1
    _l=$2
    _c=$3
    {
        printf '\n=== %s\n' "$_l"
        sh -c "$_c" 2>&1
        printf '=== end %s\n' "$_l"
    } >> "$_o" 2>/dev/null || true
}

# hdr <outfile> <title>
hdr() {
    : > "$2" 2>/dev/null || true
    {
        printf '# %s\n' "$1"
        printf '# collected %s  uptime=%s  uid=%s\n' \
            "$(date 2>/dev/null)" "$(cut -d' ' -f1 /proc/uptime 2>/dev/null)" \
            "$(id -u 2>/dev/null)"
    } >> "$2" 2>/dev/null || true
}

# fact <KEY> <VALUE>   -> machine-readable presence/state table
fact() { printf '%s=%s\n' "$1" "$2" >> "$FACTS" 2>/dev/null || true; }

# p <path>  -> "yes"/"no" for a filesystem object
p() { if [ -e "$1" ]; then printf 'yes'; else printf 'no'; fi; }
pc() { if [ -c "$1" ]; then printf 'yes'; else printf 'no'; fi; }

# pcount <pattern>  -> number of processes whose command line matches
# count matching processes; the concurrent grep itself must not be counted
pcount() {
    _n=$( ( ps -A 2>/dev/null || ps 2>/dev/null ) | grep -- "$1" 2>/dev/null | grep -v 'grep' 2>/dev/null | wc -l )
    [ -n "$_n" ] || _n=0
    printf '%s' "$_n"
}

# note <text>  -> into the current section file
note() { printf '\n--- NOTE: %s\n' "$1" >> "$CUR" 2>/dev/null || true; }

# --- run setup -------------------------------------------------------------
# /data/local/z1diag is the directory stock/z1_logpersist.sh already uses from
# the init domain, so writing it from init is proven to work on this device.
# /cache/z1diag is the same script's no-/data fallback: if /data is missing,
# full, or not yet mounted we still leave evidence somewhere.
BASE_FALLBACK=/cache/z1diag
if ! mkdir -p "$BASE" 2>/dev/null; then
    echo "z1_diag_all: cannot create $BASE, falling back to $BASE_FALLBACK"
    BASE=$BASE_FALLBACK
    LOCK=$BASE/.running
    if ! mkdir -p "$BASE" 2>/dev/null; then
        echo "z1_diag_all: cannot create $BASE either - aborting"
        exit 1
    fi
fi

if mkdir "$LOCK" 2>/dev/null; then
    :
else
    echo "z1_diag_all: another collection is running ($LOCK)."
    echo "  If you are sure it is stale: rm -rf $LOCK"
    exit 2
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' 0 1 2 15

TS=$(date +%Y%m%d_%H%M%S 2>/dev/null)
[ -n "$TS" ] || TS=$(cut -d. -f1 /proc/uptime 2>/dev/null)
[ -n "$TS" ] || TS=unknown
if [ -n "$TAG" ]; then
    OUT=$BASE/collect_${TS}_${TAG}
else
    OUT=$BASE/collect_${TS}
fi
mkdir -p "$OUT" 2>/dev/null || { echo "z1_diag_all: cannot create $OUT"; exit 1; }

FACTS=$OUT/facts.txt
: > "$FACTS" 2>/dev/null || true

# timeout wrapper: use it when the platform has one
TO=
if have timeout; then
    TO="timeout 90"
fi

NOTROOT=
[ "$(id -u 2>/dev/null)" = "0" ] || NOTROOT=1

echo "z1_diag_all: collecting into $OUT"
printf '<6>[z1diag] collect start %s\n' "$OUT" > /dev/kmsg 2>/dev/null || true

# ---------------------------------------------------------------------------
# 01 props - full getprop
# ---------------------------------------------------------------------------
CUR=$OUT/01_props.txt
hdr "getprop (full property list)" "$CUR"
cap "$CUR" "getprop" "getprop"
cap "$CUR" "selected display/egl/audio/bt props" \
    "getprop | grep -i -E 'egl|opengl|hwui|hwcomposer|gralloc|renderer'"
cap "$CUR" "selected lmk props" "getprop | grep -i 'lmk'"
cap "$CUR" "selected lmk props (ro.lmk*)" "getprop | grep -i 'ro.lmk'"
cap "$CUR" "init service states" "getprop | grep -E '^\[init\.svc\.'"
fact PROP_COUNT "$(getprop 2>/dev/null | wc -l)"

# ---------------------------------------------------------------------------
# 02 kernel identity
# ---------------------------------------------------------------------------
CUR=$OUT/02_kernel_identity.txt
hdr "kernel / build identity" "$CUR"
cap "$CUR" "uname -a" "uname -a"
cap "$CUR" "/proc/version" "cat /proc/version"
cap "$CUR" "/proc/cmdline" "cat /proc/cmdline"
cap "$CUR" "build fingerprint" "getprop ro.build.fingerprint"
cap "$CUR" "build type / debuggable" \
    "getprop ro.build.type; getprop ro.debuggable; getprop ro.secure; getprop ro.adb.secure"
cap "$CUR" "board / device props" \
    "getprop ro.product.device; getprop ro.hardware; getprop ro.board.platform; getprop ro.arch"
cap "$CUR" "/proc/sys/kernel/osrelease + version" \
    "cat /proc/sys/kernel/osrelease /proc/sys/kernel/version"
fact KERNEL_RELEASE "$(cat /proc/sys/kernel/osrelease 2>/dev/null)"
fact BUILT_DISPLAY_ID "$(getprop ro.build.display.id 2>/dev/null)"
fact RUN_UID "$(id -u 2>/dev/null)"

# ---------------------------------------------------------------------------
# 03 dmesg - full kernel log (bounded)
# ---------------------------------------------------------------------------
CUR=$OUT/03_dmesg.txt
hdr "dmesg (full, line-capped)" "$CUR"
cap "$CUR" "dmesg" "dmesg 2>/dev/null | head -n 40000"
if [ -n "$NOTROOT" ]; then
    note "running as non-root: dmesg may be restricted (dmesg_restrict=1)."
fi
cap "$CUR" "dmesg line count" "dmesg 2>/dev/null | wc -l"

# ---------------------------------------------------------------------------
# 04 modules / built-in driver inventory
# ---------------------------------------------------------------------------
CUR=$OUT/04_modules.txt
hdr "loaded modules and module tree" "$CUR"
cap "$CUR" "/proc/modules" "cat /proc/modules"
cap "$CUR" "lsmod" "lsmod"
cap "$CUR" "/system/lib/modules listing" "ls -l /system/lib/modules"
cap "$CUR" "/vendor/lib/modules listing" "ls -l /vendor/lib/modules"
cap "$CUR" "vermagic of each Z1 module" \
    'for m in /system/lib/modules/*.ko; do [ -f "$m" ] || continue; v=$(grep -a -o "vermagic=[^ ]*" "$m" 2>/dev/null | head -n 1); printf "%s %s\n" "$m" "${v:-vermagic-unknown}"; done'
cap "$CUR" "module params (wmt/lmk/zram)" \
    "ls /sys/module 2>/dev/null | grep -i -E 'wmt|stp|mtk|zram|lowmem|lima|snd'"
fact MODULES_LOADED "$(cat /proc/modules 2>/dev/null | wc -l)"
fact MOD_VERMAGIC_KERNEL "$(grep -a -o 'vermagic=[^ ]*' /system/lib/modules/wlan_mt6620.ko 2>/dev/null | head -n 1)"
fact MOD_VERMAGIC_RUNNING "$(cat /proc/version 2>/dev/null)"

# ---------------------------------------------------------------------------
# 05 devices - char/block device inventory of interest
# ---------------------------------------------------------------------------
CUR=$OUT/05_devices.txt
hdr "device node inventory" "$CUR"
cap "$CUR" "/proc/devices" "cat /proc/devices"
cap "$CUR" "/proc/misc" "cat /proc/misc"
cap "$CUR" "CONNSYS/USB/SND/DRI/INPUT nodes" \
    "ls -l /dev/stpbt /dev/stpwmt /dev/stpgps /dev/stp_uart /dev/wmtWifi /dev/wmtcombo /dev/wmtdetect /dev/ccci* /dev/ttyGS0 /dev/ttyACM* /dev/snd /dev/dri /dev/input /dev/i2c-1 /dev/graphics 2>/dev/null"
cap "$CUR" "all /dev entries (sorted)" "ls -l /dev 2>/dev/null | head -n 300"
fact DEV_STPBT "$(pc /dev/stpbt)"
fact DEV_STPWMT "$(pc /dev/stpwmt)"
fact DEV_STPGPS "$(pc /dev/stpgps)"
fact DEV_STPUART "$(pc /dev/stp_uart)"
fact DEV_WMTWIFI "$(pc /dev/wmtWifi)"
fact DEV_TTYGS0 "$(pc /dev/ttyGS0)"
fact DEV_SND_DIR "$(p /dev/snd)"
fact DEV_DRI_DIR "$(p /dev/dri)"

# ---------------------------------------------------------------------------
# 06 wireless - WiFi (MT6620 SoC, wlan_gen2) transport + netdev view
# ---------------------------------------------------------------------------
CUR=$OUT/06_wireless.txt
hdr "WiFi / CONNSYS transport" "$CUR"
cap "$CUR" "/proc/net/dev" "cat /proc/net/dev"
cap "$CUR" "/proc/net/wireless" "cat /proc/net/wireless"
cap "$CUR" "ifconfig -a" "ifconfig -a"
cap "$CUR" "ip addr / ip link" "ip addr; ip link"
cap "$CUR" "iw dev" "iw dev"
cap "$CUR" "iw dev wlan0 info / link / station dump" \
    "iw dev wlan0 info; iw dev wlan0 link; iw dev wlan0 station dump"
cap "$CUR" "/sys/class/net entries and key attributes" \
    'ls -l /sys/class/net; for d in /sys/class/net/*; do echo "-- $d"; cat "$d/address" "$d/operstate" "$d/mtu" 2>/dev/null; done'
cap "$CUR" "wlan0 sysfs detail" \
    'ls /sys/class/net/wlan0 2>/dev/null; cat /sys/class/net/wlan0/carrier /sys/class/net/wlan0/flags 2>/dev/null'
cap "$CUR" "CONNSYS nodes (/dev/stp*, /dev/wmtWifi)" \
    "ls -l /dev/stp* /dev/wmtWifi /dev/wmtcombo /dev/wmtdetect 2>/dev/null"
cap "$CUR" "wmt debug nodes" \
    "ls -l /proc/driver/wmt_dbg /proc/driver/wmt_* /sys/kernel/debug/wmt 2>/dev/null; cat /proc/driver/wmt_dbg 2>/dev/null | head -n 80"
cap "$CUR" "firmware trees" \
    "ls -l /system/etc/firmware /vendor/etc/firmware /etc/firmware 2>/dev/null"
cap "$CUR" "WiFi firmware blobs of interest" \
    "ls -l /system/etc/firmware/*6620* /system/etc/firmware/*WIFI* /system/etc/firmware/*wifi* /system/etc/firmware/*rom* /system/etc/firmware/*patch* 2>/dev/null"
cap "$CUR" "WMT cfg files" \
    "ls -l /system/etc/firmware/WMT_SOC.cfg /system/etc/firmware/*.cfg /data/vendor/wifi 2>/dev/null"
cap "$CUR" "wlan/wmt modules loaded" \
    "grep -i -E 'wlan|wmt|stp|hif' /proc/modules"
cap "$CUR" "related processes" \
    "(ps -A 2>/dev/null || ps) | grep -i -E 'wpa_supplicant|6620|wmt|dhcp' | grep -v grep"
cap "$CUR" "wifi props" \
    "getprop | grep -i -E 'wifi|wlan|wmt|connsys'"
cap "$CUR" "dumpsys wifi (capped)" "$TO dumpsys wifi | head -n 200"
cap "$CUR" "dmesg wlan/wmt/stp/sdio lines" \
    "dmesg 2>/dev/null | grep -i -E 'wlan|wifi|wmt|stp|6620|hif|sdio' | tail -n 400"
cap "$CUR" "logcat wifi lines" \
    "logcat -d 2>/dev/null | grep -i -E 'wlan|wifi|wpa_supplicant|WifiService|wmt' | tail -n 300"
fact WLAN0_EXISTS "$(p /sys/class/net/wlan0)"
fact WLAN0_OPERSTATE "$(cat /sys/class/net/wlan0/operstate 2>/dev/null)"
fact WIFI_FW_6620_FILES "$(ls /system/etc/firmware/*6620* 2>/dev/null | wc -l)"
fact WIFI_WPA_RUNNING "$(pcount wpa_supplicant)"

# ---------------------------------------------------------------------------
# 07 bluetooth - mtk_btif_drv + mtk_stp_bt_soc transport and stack
# ---------------------------------------------------------------------------
CUR=$OUT/07_bluetooth.txt
hdr "Bluetooth (BTIF + STP transport + Android stack)" "$CUR"
cap "$CUR" "/dev/stpbt node" "ls -l /dev/stpbt /dev/stpwmt 2>/dev/null"
cap "$CUR" "bt modules loaded" "grep -i -E 'stp_bt|btif|bluetooth' /proc/modules"
cap "$CUR" "bt processes" \
    "(ps -A 2>/dev/null || ps) | grep -i -E 'bluetooth|bt_' | grep -v grep"
cap "$CUR" "hciconfig -a" "hciconfig -a"
cap "$CUR" "hciconfig -a hci0" "hciconfig -a hci0"
cap "$CUR" "rfkill list" "rfkill list"
cap "$CUR" "/sys/class/bluetooth" "ls -l /sys/class/bluetooth"
cap "$CUR" "bt props" "getprop | grep -i -E 'bluetooth|^\[persist\.bt|ro\.bt'"
cap "$CUR" "init.svc bt services" "getprop | grep -E 'init\.svc\.(bt|bluetooth)'"
cap "$CUR" "bt libraries present" \
    "ls -l /system/lib/libbluetoothdrv.so /system/lib/libbt-vendor.so /vendor/lib/libbt-vendor.so /system/lib/libstdc++.so /vendor/lib/libstdc++.so 2>/dev/null"
cap "$CUR" "bt HIDL service binary" \
    "ls -l /vendor/bin/hw/android.hardware.bluetooth* /system/bin/hw/android.hardware.bluetooth* 2>/dev/null"
cap "$CUR" "dumpsys bluetooth_manager (capped)" "$TO dumpsys bluetooth_manager | head -n 200"
cap "$CUR" "settings bluetooth_on" "settings get global bluetooth_on"
cap "$CUR" "dmesg bt/stp lines" \
    "dmesg 2>/dev/null | grep -i -E 'btif|stpbt|bluetooth|stp_bt' | tail -n 300"
cap "$CUR" "logcat bt lines" \
    "logcat -d 2>/dev/null | grep -i -E 'bluetooth|btif|bt_vendor|hci' | tail -n 400"
fact BT_STPBT "$(pc /dev/stpbt)"
fact BT_STACK_RUNNING "$(pcount com.android.bluetooth)"
fact BT_BLUETOOTH_ON_PROP "$(settings get global bluetooth_on 2>/dev/null)"

# ---------------------------------------------------------------------------
# 08 gps - mnld + libnvram + GPS HAL
# ---------------------------------------------------------------------------
CUR=$OUT/08_gps.txt
hdr "GNSS / GPS stack" "$CUR"
cap "$CUR" "gps device nodes" \
    "ls -l /dev/stpgps /dev/gps /dev/gps1 /dev/gps2 /dev/ttyMT1 2>/dev/null"
cap "$CUR" "gps modules loaded" "grep -i -E 'stp_gps|gps' /proc/modules"
cap "$CUR" "gps processes (mnld)" \
    "(ps -A 2>/dev/null || ps) | grep -i -E 'mnld|gps|gnss' | grep -v grep"
cap "$CUR" "GPS NVRAM/data paths" \
    "ls -l /data/misc/GPS* /data/misc/gps /data/vendor/gps /data/gps 2>/dev/null"
cap "$CUR" "GPS NVRAM files detail" \
    "ls -lR /data/misc/GPS* /data/vendor/gps 2>/dev/null | head -n 120"
cap "$CUR" "GPS libs / HAL / nvram libs" \
    "ls -l /system/lib/hw/gps*.so /vendor/lib/hw/gps*.so /vendor/lib/hw/*gnss*.so /system/lib/hw/*gnss*.so /system/lib/libmnl*.so /vendor/lib/libmnl*.so /system/lib/libnvram*.so /vendor/lib/libnvram*.so 2>/dev/null"
cap "$CUR" "GPS config files (gps.conf / GPS_CHIP.cfg / agps profiles)" \
    "ls -l /system/etc/gps.conf /vendor/etc/gps.conf /data/misc/GPS_CHIP.cfg /vendor/etc/agps_profiles_conf.xml 2>/dev/null; cat /data/misc/GPS_CHIP.cfg 2>/dev/null"
cap "$CUR" "gps init service states (z1_gps_prep starts z1_mnld)" \
    "getprop | grep -E 'init\.svc\.(z1_mnld|z1_gps|mnld)'; getprop init.svc.z1_mnld"
cap "$CUR" "gps properties" "getprop | grep -i -E 'gps|gnss'"
cap "$CUR" "location settings" \
    "settings get secure location_mode; settings get secure location_providers_allowed"
cap "$CUR" "dumpsys location (capped)" "$TO dumpsys location | head -n 200"
cap "$CUR" "dmesg gps/stp lines" \
    "dmesg 2>/dev/null | grep -i -E 'gps|gnss|stp_gps' | tail -n 200"
cap "$CUR" "logcat gps lines" \
    "logcat -d 2>/dev/null | grep -i -E 'gps|gnss|mnld|libgps' | tail -n 300"
fact GPS_STPGPS "$(pc /dev/stpgps)"
fact GPS_MNLD_RUNNING "$(pcount mnld)"
fact GPS_NVRAM_DIRS "$(ls -d /data/misc/GPS* /data/vendor/gps 2>/dev/null | wc -l)"
fact GPS_HAL_LIB "$(ls /system/lib/hw/gps*.so /vendor/lib/hw/gps*.so /vendor/lib/hw/*gnss*.so 2>/dev/null | wc -l)"
fact GPS_MNLD_SVC "$(getprop init.svc.z1_mnld 2>/dev/null)"
fact GPS_CHIP_CFG "$(ls /data/misc/GPS_CHIP.cfg 2>/dev/null | wc -l)"

# ---------------------------------------------------------------------------
# 09 audio - ASoC (gabin8 / MT6572 AFE / MT6323) + Android audio HAL
# ---------------------------------------------------------------------------
CUR=$OUT/09_audio.txt
hdr "Audio: kernel ASoC view + Android audio HAL" "$CUR"
cap "$CUR" "/proc/asound/cards" "cat /proc/asound/cards"
cap "$CUR" "/proc/asound/devices" "cat /proc/asound/devices"
cap "$CUR" "/proc/asound/pcm" "cat /proc/asound/pcm"
cap "$CUR" "/dev/snd nodes" "ls -l /dev/snd"
cap "$CUR" "card dirs" "ls -l /proc/asound"
cap "$CUR" "pcm substream status" \
    'for s in /proc/asound/card*/pcm*/sub*/status; do [ -r "$s" ] && printf "%s: %s\n" "$s" "$(head -n 1 "$s" 2>/dev/null)"; done'
cap "$CUR" "tinymix contents" "tinymix"
cap "$CUR" "tinyplay/tinycap/tinypcminfo presence" \
    "ls -l /system/bin/tinyplay /system/bin/tinycap /system/bin/tinypcminfo /vendor/bin/tinyplay 2>/dev/null"
cap "$CUR" "snd modules" "grep -i -E 'snd|afe|mt6323|gabin|audio' /proc/modules"
cap "$CUR" "audio props" \
    "getprop | grep -i -E 'audio|sound|codec|afe'"
cap "$CUR" "audio HAL libs (legacy primary + HIDL impl)" \
    "ls -l /vendor/lib/hw/*audio*.so /system/lib/hw/*audio*.so /vendor/lib/hw/android.hardware.audio* /system/lib/hw/android.hardware.audio* 2>/dev/null"
cap "$CUR" "audio module loader (load_audio.sh brings in the gabin8 stack)" \
    "ls -l /system/lib/modules/load_audio.sh 2>/dev/null; grep -i -E 'snd|afe|gabin|mt6323' /system/lib/modules/load_audio.sh 2>/dev/null"
cap "$CUR" "audio policy xml" \
    "ls -l /vendor/etc/audio_policy_configuration.xml /system/etc/audio_policy_configuration.xml 2>/dev/null"
cap "$CUR" "dmesg snd/afe/mt6323 lines" \
    "dmesg 2>/dev/null | grep -i -E 'snd|asoc|afe|mt6323|mt-soc|gabin|dai' | tail -n 400"
cap "$CUR" "dumpsys media.audio_flinger (capped)" "$TO dumpsys media.audio_flinger | head -n 200"
cap "$CUR" "dumpsys media.audio_policy (capped)" "$TO dumpsys media.audio_policy | head -n 120"
cap "$CUR" "dumpsys audio (capped)" "$TO dumpsys audio | head -n 120"
cap "$CUR" "logcat audio lines" \
    "logcat -d 2>/dev/null | grep -i -E 'audio|AudioFlinger|AudioPolicy|tinyalsa|ASoC' | tail -n 400"
fact AUDIO_CARDS "$(sed -n '/^ *[0-9]/p' /proc/asound/cards 2>/dev/null | wc -l)"
fact AUDIO_SND_NODES "$(ls /dev/snd 2>/dev/null | wc -l)"
fact AUDIO_HAL_LIB "$(ls /vendor/lib/hw/*audio*.so /system/lib/hw/*audio*.so /vendor/lib/hw/android.hardware.audio* 2>/dev/null | wc -l)"
fact AUDIO_LOADER "$(ls /system/lib/modules/load_audio.sh 2>/dev/null | wc -l)"
fact AUDIO_TINYMIX "$(p /system/bin/tinymix)"

# ---------------------------------------------------------------------------
# 10 display - framebuffer, DRI/GPU, SurfaceFlinger/composer
# ---------------------------------------------------------------------------
CUR=$OUT/10_display_gpu.txt
hdr "Display / GPU (fb + DRI + SurfaceFlinger)" "$CUR"
cap "$CUR" "fb0 identity" \
    "cat /sys/class/graphics/fb0/name /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/stride /sys/class/graphics/fb0/bits_per_pixel 2>/dev/null"
cap "$CUR" "/sys/class/graphics listing" "ls -l /sys/class/graphics"
cap "$CUR" "fb mode / pan" \
    "cat /sys/class/graphics/fb0/modes 2>/dev/null; cat /sys/class/graphics/fb0/pan 2>/dev/null"
cap "$CUR" "/dev/dri nodes" "ls -l /dev/dri /dev/graphics 2>/dev/null"
cap "$CUR" "dri sysfs" \
    "ls -l /sys/class/drm; cat /sys/class/drm/*/status 2>/dev/null"
cap "$CUR" "gpu modules" "grep -i -E 'lima|drm|gpu|mali' /proc/modules"
cap "$CUR" "graphics props" \
    "getprop | grep -i -E 'egl|opengl|hwui|gralloc|hwcomposer|debug.sf|ro.sf'"
cap "$CUR" "gfxsys props" "getprop | grep -i 'gralloc'"
cap "$CUR" "composer/HWC binaries" \
    "ls -l /vendor/bin/hw/android.hardware.graphics* /vendor/lib/hw/hwcomposer*.so /vendor/lib/hw/gralloc*.so 2>/dev/null"
cap "$CUR" "dumpsys SurfaceFlinger (capped)" "$TO dumpsys SurfaceFlinger | head -n 250"
cap "$CUR" "dumpsys display (capped)" "$TO dumpsys display | head -n 150"
cap "$CUR" "display processes" \
    "(ps -A 2>/dev/null || ps) | grep -i -E 'surfaceflinger|composer|allocator' | grep -v grep"
cap "$CUR" "logcat SF/HWC/gralloc/EGL lines" \
    "logcat -d 2>/dev/null | grep -i -E 'SurfaceFlinger|hwcomposer|gralloc|EGL|libGLES|lima' | tail -n 400"
cap "$CUR" "dmesg drm/lima/fb lines" \
    "dmesg 2>/dev/null | grep -i -E 'drm|lima|fb0|mtkfb|dsi|panel' | tail -n 300"
fact FB0_NAME "$(cat /sys/class/graphics/fb0/name 2>/dev/null)"
fact FB0_MODES "$(cat /sys/class/graphics/fb0/modes 2>/dev/null)"
fact DRI_NODES "$(ls /dev/dri 2>/dev/null | wc -l)"
fact EGL_PROP "$(getprop ro.hardware.egl 2>/dev/null)"
fact GLES_VERSION "$(getprop ro.opengles.version 2>/dev/null)"

# ---------------------------------------------------------------------------
# 11 input - keypad, PMIC keys, touch
# ---------------------------------------------------------------------------
CUR=$OUT/11_input.txt
hdr "Input devices, keylayouts and event capabilities" "$CUR"
cap "$CUR" "/proc/bus/input/devices" "cat /proc/bus/input/devices"
cap "$CUR" "input sysfs names" \
    'for d in /sys/class/input/input*; do [ -d "$d" ] && printf "%s %s\n" "$d" "$(cat "$d/name" 2>/dev/null)"; done'
cap "$CUR" "event handler map" \
    'for d in /sys/class/input/event*; do [ -d "$d" ] && printf "%s -> %s\n" "$d" "$(readlink -f "$d/device" 2>/dev/null)"; done'
cap "$CUR" "getevent -lp (device capabilities)" "$TO getevent -lp 2>&1 | head -n 300"
cap "$CUR" "keylayout directory" "ls -l /system/usr/keylayout /vendor/usr/keylayout 2>/dev/null"
cap "$CUR" "keylayout contents" \
    'for f in /system/usr/keylayout/*.kl /vendor/usr/keylayout/*.kl; do [ -f "$f" ] || continue; echo "--- $f"; cat "$f"; done'
cap "$CUR" "keychars/idc directory" "ls -l /system/usr/idc /vendor/usr/idc 2>/dev/null"
cap "$CUR" "input-related props" \
    "getprop | grep -i -E 'touch|keypad|mainkeys|input'"
cap "$CUR" "dmesg input/keypad/touch lines" \
    "dmesg 2>/dev/null | grep -i -E 'input|keypad|kpd|pmic-keys|cst836|goodix|touch' | tail -n 300"
cap "$CUR" "logcat input lines" \
    "logcat -d 2>/dev/null | grep -i -E 'InputReader|InputDispatcher|keypad|cst836' | tail -n 300"
fact INPUT_KPD "$(grep -c 'mtk-kpd' /proc/bus/input/devices 2>/dev/null)"
fact INPUT_TPD "$(grep -c 'mtk-tpd' /proc/bus/input/devices 2>/dev/null)"
fact INPUT_CST836 "$(grep -c 'cst836' /proc/bus/input/devices 2>/dev/null)"
fact INPUT_EVENT_NODES "$(ls /dev/input 2>/dev/null | grep -c '^event')"
fact HW_MAINKEYS "$(getprop qemu.hw.mainkeys 2>/dev/null)"

# ---------------------------------------------------------------------------
# 12 battery / power supply
# ---------------------------------------------------------------------------
CUR=$OUT/12_battery.txt
hdr "Battery / power supply" "$CUR"
cap "$CUR" "power_supply entries" "ls -l /sys/class/power_supply"
cap "$CUR" "uevent of every supply" \
    'for d in /sys/class/power_supply/*; do [ -d "$d" ] || continue; echo "--- $d"; cat "$d/uevent" 2>/dev/null; done'
cap "$CUR" "capacity/status/voltage" \
    "cat /sys/class/power_supply/adc-battery/capacity /sys/class/power_supply/adc-battery/status /sys/class/power_supply/adc-battery/voltage_now /sys/class/power_supply/adc-battery/temp 2>/dev/null"
cap "$CUR" "dumpsys battery" "$TO dumpsys battery"
cap "$CUR" "health HAL service" \
    "getprop | grep -i -E 'health|battery'; ls -l /vendor/bin/hw/android.hardware.health* 2>/dev/null"
cap "$CUR" "dmesg charger/battery lines" \
    "dmesg 2>/dev/null | grep -i -E 'battery|charger|adc-bat|mt6323' | tail -n 200"
fact BATTERY_CAPACITY "$(cat /sys/class/power_supply/adc-battery/capacity 2>/dev/null)"
fact BATTERY_VOLTAGE_NOW "$(cat /sys/class/power_supply/adc-battery/voltage_now 2>/dev/null)"
fact POWER_SUPPLIES "$(ls /sys/class/power_supply 2>/dev/null | wc -l)"

# ---------------------------------------------------------------------------
# 13 memory / performance / zram
# ---------------------------------------------------------------------------
CUR=$OUT/13_memory_perf.txt
hdr "Memory, CPU load and zram" "$CUR"
cap "$CUR" "free -m" "free -m"
cap "$CUR" "free" "free"
cap "$CUR" "/proc/meminfo" "cat /proc/meminfo"
cap "$CUR" "/proc/pressure/* (kernel 4.20+, absent on 3.4 is normal)" \
    'ls /proc/pressure 2>/dev/null; for f in /proc/pressure/*; do [ -r "$f" ] && { echo "--- $f"; cat "$f"; }; done'
cap "$CUR" "loadavg / uptime / stat head" \
    "cat /proc/loadavg /proc/uptime; head -n 1 /proc/stat"
cap "$CUR" "dumpsys cpuinfo (capped)" "$TO dumpsys cpuinfo | head -n 80"
cap "$CUR" "dumpsys meminfo (capped)" "$TO dumpsys meminfo | head -n 120"
cap "$CUR" "zram block devices" "ls -l /sys/block/zram*; cat /sys/block/zram0/disksize 2>/dev/null"
cap "$CUR" "zram0 attributes" \
    'for f in /sys/block/zram0/*; do [ -f "$f" ] && printf "%s: %s\n" "$f" "$(cat "$f" 2>/dev/null)"; done | head -n 60'
cap "$CUR" "swap view" "cat /proc/swaps; swapon -s"
cap "$CUR" "lmkd state" \
    "(ps -A 2>/dev/null || ps) | grep lmkd | grep -v grep; getprop | grep -i lmk"
cap "$CUR" "lowmemorykiller params" \
    "cat /sys/module/lowmemorykiller/parameters/minfree /sys/module/lowmemorykiller/parameters/adj 2>/dev/null"
cap "$CUR" "procrank if present" "$TO procrank 2>/dev/null | head -n 40"
cap "$CUR" "top snapshot" "$TO top -b -n 1 2>/dev/null | head -n 40"
fact MEM_FREE_KB "$(awk '/^MemFree:/{print $2}' /proc/meminfo 2>/dev/null)"
fact MEM_AVAIL_KB "$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null)"
fact ZRAM_DISKSIZE "$(cat /sys/block/zram0/disksize 2>/dev/null)"
fact PSI_PRESENT "$(p /proc/pressure)"
fact LMKD_MINREE "$(cat /sys/module/lowmemorykiller/parameters/minfree 2>/dev/null)"
fact PROP_RO_LMK "$(getprop ro.lmk.use_minfree_levels 2>/dev/null)"

# ---------------------------------------------------------------------------
# 14 crash / logcat / tombstones
# ---------------------------------------------------------------------------
CUR=$OUT/14_crash_logcat.txt
hdr "Crashes, tombstones and full logcat" "$CUR"
cap "$CUR" "/data/tombstones listing" "ls -l /data/tombstones 2>/dev/null"
cap "$CUR" "/data/anr listing" "ls -l /data/anr 2>/dev/null"
cap "$CUR" "/data/system/dropbox listing (capped)" "ls -lt /data/system/dropbox 2>/dev/null | head -n 60"
cap "$CUR" "logcat -b crash" "logcat -d -b crash 2>/dev/null | tail -n 500"
cap "$CUR" "logcat -b events (am_crash/am_anr)" \
    "logcat -d -b events 2>/dev/null | grep -E 'am_crash|am_anr|am_kill|am_proc_died' | tail -n 300"
cap "$CUR" "logcat -b system (errors)" \
    "logcat -d -b system 2>/dev/null | grep -E ' E | F ' | tail -n 400"
cap "$CUR" "logcat all buffers (capped)" "logcat -d -b all -v threadtime 2>/dev/null | tail -n 6000"
cap "$CUR" "dmesg lowmemorykiller/ANR/Watchdog" \
    "dmesg 2>/dev/null | grep -i -E 'lowmemorykiller|lmk|ANR|watchdog|oom|killed process' | tail -n 300"
fact TOMBSTONES_COUNT "$(ls /data/tombstones 2>/dev/null | wc -l)"
fact ANR_COUNT "$(ls /data/anr 2>/dev/null | wc -l)"
fact LOGCAT_LINES "$(logcat -d 2>/dev/null | wc -l)"

# ---------------------------------------------------------------------------
# 15 selinux and avc denials
# ---------------------------------------------------------------------------
CUR=$OUT/15_selinux.txt
hdr "SELinux mode and AVC denials" "$CUR"
cap "$CUR" "getenforce" "getenforce"
cap "$CUR" "enforce file" "cat /sys/fs/selinux/enforce /sys/fs/selinux/policyvers 2>/dev/null"
cap "$CUR" "selinux props" "getprop | grep -i -E 'selinux|enforce'"
cap "$CUR" "avc denials from dmesg (top 40, deduped)" \
    "dmesg 2>/dev/null | grep -i 'avc:' | sed 's/^.*avc: //' | sort | uniq -c | sort -rn | head -n 40"
cap "$CUR" "avc denials from dmesg (raw tail)" \
    "dmesg 2>/dev/null | grep -i 'avc:' | tail -n 200"
cap "$CUR" "avc denials from logcat (top 40, deduped)" \
    "logcat -d 2>/dev/null | grep -i 'avc:' | sed 's/^.*avc: //' | sort | uniq -c | sort -rn | head -n 40"
cap "$CUR" "avc count total" "dmesg 2>/dev/null | grep -c -i 'avc:'"
fact SELINUX "$(getenforce 2>/dev/null)"
fact AVC_COUNT "$(dmesg 2>/dev/null | grep -c -i 'avc:')"

# ---------------------------------------------------------------------------
# 16 system snapshot - processes, mounts, irq
# ---------------------------------------------------------------------------
CUR=$OUT/16_system_snapshot.txt
hdr "Process list, mounts, filesystems, interrupts" "$CUR"
cap "$CUR" "process list" "(ps -A 2>/dev/null || ps) | head -n 300"
cap "$CUR" "mount" "mount"
cap "$CUR" "df" "df"
cap "$CUR" "/proc/interrupts" "cat /proc/interrupts | head -n 120"
cap "$CUR" "/proc/partitions" "cat /proc/partitions"
cap "$CUR" "/proc/filesystems" "cat /proc/filesystems"
cap "$CUR" "kernel command line + boot reason" \
    "cat /proc/cmdline; getprop ro.boot.bootreason; cat /proc/sys/kernel/random/boot_id 2>/dev/null"
cap "$CUR" "data partition usage of diag dir" "du -sk /data/local/z1diag 2>/dev/null"

# ---------------------------------------------------------------------------
# 17 logpersist history - evidence that predates this collection
#
# stock/z1_logpersist.sh keeps writing /data/local/z1diag/{beats,dmesg,snap}.txt
# (and /cache/z1diag/*) from init. That is the ONLY record of what happened
# before we ran, e.g. a freeze or an lmkd kill an hour ago. We fold it in by
# reference (tails + sizes) instead of copying megabytes around.
# ---------------------------------------------------------------------------
CUR=$OUT/17_logpersist_history.txt
hdr "history written by z1_logpersist.sh before this run" "$CUR"
cap "$CUR" "files present" \
    "ls -l /data/local/z1diag/*.txt /cache/z1diag/*.txt 2>/dev/null"
cap "$CUR" "beats.txt tail (uptime timeline of earlier runs)" \
    "tail -n 200 /data/local/z1diag/beats.txt 2>/dev/null; echo '--- cache ---'; tail -n 100 /cache/z1diag/beats.txt 2>/dev/null"
cap "$CUR" "snap.txt (last wider snapshot)" \
    "cat /data/local/z1diag/snap.txt 2>/dev/null; echo '--- cache ---'; cat /cache/z1diag/snap.txt 2>/dev/null"
cap "$CUR" "dmesg.txt tail (pre-collection kernel log)" \
    "tail -n 3000 /data/local/z1diag/dmesg.txt 2>/dev/null"
cap "$CUR" "sizes" \
    "wc -c /data/local/z1diag/dmesg.txt /data/local/z1diag/snap.txt /data/local/z1diag/beats.txt 2>/dev/null"
fact LOGPERSIST_DIR "$(ls -d /data/local/z1diag 2>/dev/null | wc -l)"
fact LOGPERSIST_DMESG_BYTES "$(wc -c < /data/local/z1diag/dmesg.txt 2>/dev/null)"
fact LOGPERSIST_BEATS_LINES "$(wc -l < /data/local/z1diag/beats.txt 2>/dev/null)"

# ---------------------------------------------------------------------------
# index + tar
# ---------------------------------------------------------------------------
IDX=$(date 2>/dev/null)
{
    printf 'Z1 diagnostic bundle\n'
    printf 'created: %s\n' "$IDX"
    printf 'dir: %s\n\n' "$OUT"
    printf '%-34s %8s %10s\n' FILE LINES BYTES
    for f in "$OUT"/*.txt; do
        [ -f "$f" ] || continue
        n=$(basename "$f")
        l=$(wc -l < "$f" 2>/dev/null)
        b=$(wc -c < "$f" 2>/dev/null)
        printf '%-34s %8s %10s\n' "$n" "$l" "$b"
    done
} > "$OUT/00_index.txt" 2>/dev/null || true

if [ -n "$TAG" ]; then
    TARBALL=$BASE/collect_${TS}_${TAG}.tar.gz
else
    TARBALL=$BASE/collect_${TS}.tar.gz
fi
if tar -czf "$TARBALL" -C "$BASE" "$(basename "$OUT")" 2>/dev/null; then
    :
else
    # some toybox builds want the -C target listed differently
    ( cd "$BASE" && tar -czf "$TARBALL" "$(basename "$OUT")" ) 2>/dev/null || true
fi

SZ=
SUM=
if [ -f "$TARBALL" ]; then
    SZ=$(wc -c < "$TARBALL" 2>/dev/null)
    if have sha256sum; then
        SUM=$(sha256sum "$TARBALL" 2>/dev/null | cut -d' ' -f1)
    fi
fi

echo "----------------------------------------------------------------"
echo "z1_diag_all: DONE"
echo "  directory : $OUT"
echo "  tarball   : $TARBALL"
echo "  size      : ${SZ:-?} bytes"
echo "  sha256    : ${SUM:-unavailable}"
echo "  facts     : $FACTS"
echo "  next      : pull the tarball (see README.md) or run"
echo "              sh /data/local/z1diag/z1_diag_wifi.sh  etc."
echo "----------------------------------------------------------------"

{
    printf '<6>[z1diag] done tarball=%s size=%s\n' "$TARBALL" "${SZ:-0}"
} > /dev/kmsg 2>/dev/null || true

cat "$FACTS" 2>/dev/null
exit 0
