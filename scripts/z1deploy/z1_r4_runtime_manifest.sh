#!/system/bin/sh
# Read-only R4 runtime identity gate. It hashes installed files and reads fixed
# Android properties; it never writes properties, files, services, or devices.

PATH=/system/bin:/system/xbin:/sbin:/bin
export PATH

if [ "$#" -gt 1 ]; then
    printf '%s\n' '[r4manifest] ERROR too many arguments'
    exit 2
fi
case "${1:-}" in
    '') ;;
    --help)
        printf '%s\n' 'Usage: sh z1_r4_runtime_manifest.sh'
        printf '%s\n' 'Read-only R4 runtime identity and readiness check.'
        exit 0
        ;;
    *)
        printf '[r4manifest] ERROR unknown option: %s\n' "$1"
        exit 2
        ;;
esac

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[r4manifest] check=%s status=PASS expected=%s\n' "$1" "$2"
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[r4manifest] check=%s status=FAIL expected=%s actual=%s\n' "$1" "$2" "$3"
}

get_prop_value() {
    command -v getprop >/dev/null 2>&1 || return 1
    getprop "$1" 2>/dev/null
}

check_prop() {
    _cp_name=$1
    _cp_expected=$2
    _cp_actual=$(get_prop_value "$_cp_name") || {
        fail "$_cp_name" "$_cp_expected" "GETPROP_FAILED"
        return
    }
    if [ "$_cp_actual" = "$_cp_expected" ]; then
        pass "$_cp_name" "$_cp_expected"
    else
        fail "$_cp_name" "$_cp_expected" "${_cp_actual:-UNSET}"
    fi
}

check_hwc_source() {
    _chs_actual=$(get_prop_value z1stage.hwcomposer.source) || {
        fail hwcomposer-source "ramdisk|active:candidate-match|active:pinned" GETPROP_FAILED
        return
    }
    case "$_chs_actual" in
        ramdisk|active:candidate-match|active:pinned)
            pass hwcomposer-source "ramdisk|active:candidate-match|active:pinned"
            ;;
        *)
            fail hwcomposer-source "ramdisk|active:candidate-match|active:pinned" "${_chs_actual:-UNSET}"
            ;;
    esac
}

check_hash() {
    _ch_path=$1
    _ch_expected_sha=$2
    _ch_expected_mode=$3
    if [ -L "$_ch_path" ]; then
        fail "$_ch_path" "regular_sha256=${_ch_expected_sha}" SYMLINK
        return
    fi
    if [ ! -e "$_ch_path" ]; then
        fail "$_ch_path" "regular_sha256=${_ch_expected_sha}" MISSING
        return
    fi
    if [ ! -f "$_ch_path" ] || [ ! -r "$_ch_path" ]; then
        fail "$_ch_path" "regular_sha256=${_ch_expected_sha}" NOT_READABLE_REGULAR_FILE
        return
    fi
    _ch_meta=$(stat -c '%a:%u:%g' "$_ch_path" 2>/dev/null) || {
        fail "$_ch_path" "mode=${_ch_expected_mode} uid=0 gid=0 sha256=${_ch_expected_sha}" STAT_FAILED
        return
    }
    _ch_meta_mode=${_ch_meta%%:*}
    if [ "$_ch_meta_mode" != "$_ch_expected_mode" ] ||
       [ "${_ch_meta#*:}" != "0:0" ]; then
        fail "$_ch_path" "mode=${_ch_expected_mode} uid=0 gid=0 sha256=${_ch_expected_sha}" "metadata=${_ch_meta}"
        return
    fi
    _ch_result=$(sha256sum "$_ch_path" 2>/dev/null) || {
        fail "$_ch_path" "regular_sha256=${_ch_expected_sha}" HASH_FAILED
        return
    }
    _ch_actual=${_ch_result%% *}
    case "$_ch_actual" in
        *[!0-9a-f]*)
            fail "$_ch_path" "regular_sha256=${_ch_expected_sha}" INVALID_HASH_OUTPUT
            ;;
        *)
            if [ "${#_ch_actual}" -ne 64 ]; then
                fail "$_ch_path" "regular_sha256=${_ch_expected_sha}" INVALID_HASH_OUTPUT
            elif [ "$_ch_actual" = "$_ch_expected_sha" ]; then
                pass "$_ch_path" "$_ch_expected_sha"
            else
                fail "$_ch_path" "$_ch_expected_sha" "$_ch_actual"
            fi
            ;;
    esac
}

check_line() {
    _cl_id=$1
    _cl_path=$2
    _cl_line=$3
    if [ -L "$_cl_path" ] || [ ! -f "$_cl_path" ] || [ ! -r "$_cl_path" ]; then
        fail "$_cl_id" "$_cl_line" FILE_MISSING_OR_NOT_READABLE
        return
    fi
    if grep -Fq "$_cl_line" "$_cl_path" 2>/dev/null; then
        pass "$_cl_id" "$_cl_line"
    else
        fail "$_cl_id" "$_cl_line" "LINE_NOT_FOUND"
    fi
}

check_loaded_module() {
    _clm_name=$1
    if [ ! -r /proc/modules ]; then
        fail "loaded-module:${_clm_name}" PRESENT PROC_MODULES_UNREADABLE
        return
    fi
    if awk -v wanted="$_clm_name" '$1 == wanted { found=1 } END { exit !found }' \
        /proc/modules 2>/dev/null; then
        pass "loaded-module:${_clm_name}" PRESENT
    else
        fail "loaded-module:${_clm_name}" PRESENT NOT_LOADED
    fi
}

check_input_name() {
    if [ ! -r /proc/bus/input/devices ]; then
        fail touch-input-registered 'Name="z1_cst836u"' INPUT_PROC_UNREADABLE
        return
    fi
    if awk '
        BEGIN { RS=""; found=0 }
        index($0, "Name=\"z1_cst836u\"") { found=1 }
        END { exit !found }
    ' /proc/bus/input/devices 2>/dev/null; then
        pass touch-input-registered 'Name="z1_cst836u"'
    else
        fail touch-input-registered 'Name="z1_cst836u"' DEVICE_NOT_REGISTERED
    fi
}

printf '%s\n' '[r4manifest] BEGIN schema=z1-r4-runtime/v1 candidate=r4-20261115 read_only=1'
_utc=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null) || _utc=UNAVAILABLE
printf '[r4manifest] sampled_utc=%s\n' "$_utc"

if [ -r /proc/version ] && grep -Fq 'Linux version 7.0.0-rc7-z1+' /proc/version; then
    pass kernel-version 7.0.0-rc7-z1+
else
    fail kernel-version 7.0.0-rc7-z1+ VERSION_NOT_OBSERVED
fi

check_prop sys.boot_completed 1
check_prop sys.z1.storage_ready 1
check_prop z1stage.status ready
check_prop sys.z1.radio.modules_ready 1
check_prop sys.z1.radio.wifi_modules_ready 1
check_prop z1stage.hwcomposer.sha 35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912
check_prop z1stage.hwcomposer.boot 35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912
check_prop init.svc.vendor.hwcomposer-2-1 running
check_hwc_source

check_hash /hwcomposer.z1.so \
    35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912 644
check_hash /vendor/lib/hw/hwcomposer.z1.so \
    35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912 644
check_hash /data/local/tmp/z1stage/hwcomposer.z1.so.ramdisk \
    35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912 644
check_hash /system/usr/keylayout/mt6779-keypad.kl \
    da29948bb71336ccc73f0b05333be610cf18feee3987fddddafd7d44d59c5f55 644
check_hash /system/lib/modules/z1_cst836u.ko \
    03b0f94c67fab10551ea0085beffff4fb02c87878a2034f184fd28bca8368745 664
check_hash /system/lib/modules/snd-soc-mt6323.ko \
    af3931113c5d96e0768e06ee4b30596a71b5c72fd06233f0ee3cf248388e8945 664
check_hash /system/etc/init/z1_touch_navigation.rc \
    32b84644e3eeb7bb99af897d1f104ce7518fc87beafe919d0b81de4960b56901 644
check_hash /system/build.prop \
    421b3173d9f8aa93be410868001c449088ccd4cee7441db64b60f8c7426514c8 644
check_hash /system/lib/modules/z1-radio/mtk_stp_bt_soc.ko \
    01fbd5706e9c74dc9759d3da60da5e3a5443c5c71c12c0dc1a1464495e45f63e 644
check_hash /system/lib/modules/z1-radio/wlan_gen2.ko \
    2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b 644
check_hash /vendor/lib/egl/libGLES_mesa.so \
    bb0ac77c03e79648db5dc8a480ec478be566b78c6105e9f781fd22100b46f445 644
check_hash /vendor/lib/libgallium_dri.so \
    492f0b5e871868f55023351722a609da6b65e186fbf31366a28d7faf981232b9 644
check_hash /system/lib/libart.so \
    81226e14966cd9d342a0df36f24c2ad12ecdcc926c0a14bf354c1b373cb2ea94 644
check_hash /system/framework/arm/boot.oat \
    baef5ea2e9a91031ae68d0b6ce853ee5b2822a4ed394178a1b97d4167ee2553c 644
check_hash /system/framework/oat/arm/services.odex \
    750554a7f8e6b07fe36b4f22562a282a0eabf602ca7f0fcf7e87f1d6af83dc08 644
check_hash /system/priv-app/SystemUI/SystemUI.apk \
    edb83b4f626549b9080ad3968ad4b5d1761182ee43298351601d8e8a8e67114d 644

check_line keylayout-158-volume-up \
    /system/usr/keylayout/mt6779-keypad.kl 'key 158   VOLUME_UP'
check_line touch-boundary-320 \
    /system/etc/init/z1_touch_navigation.rc \
    'write /sys/module/z1_cst836u/parameters/key_y_min 320'
check_line hide-virtual-navigation /system/build.prop 'qemu.hw.mainkeys=1'

for _module in mt6572_mt6323 mt6572_afe_pcm snd_soc_mt6323 z1_cst836u \
    wlan_gen2 mtk_stp_bt_soc mtk_stp_wmt_soc mtk_btif_drv; do
    check_loaded_module "$_module"
done

check_input_name
if [ -r /proc/asound/cards ] && grep -Fq 'mt6572-mt6323' /proc/asound/cards; then
    pass audio-card-present mt6572-mt6323
else
    fail audio-card-present mt6572-mt6323 CARD_NOT_OBSERVED
fi
if [ -e /sys/class/net/wlan0 ]; then
    pass wlan0-present PRESENT
else
    fail wlan0-present PRESENT INTERFACE_MISSING
fi

if [ "$FAIL_COUNT" -eq 0 ]; then
    printf '[r4manifest] summary pass=%s fail=%s status=PASS\n' "$PASS_COUNT" "$FAIL_COUNT"
    printf '%s\n' '[r4manifest] END'
    exit 0
fi
printf '[r4manifest] summary pass=%s fail=%s status=FAIL\n' "$PASS_COUNT" "$FAIL_COUNT"
printf '%s\n' '[r4manifest] END'
exit 1
