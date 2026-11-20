#!/system/bin/sh
# Linux 7 Z1 radio bring-up guard. This runs asynchronously from init so a
# missing/bad module or absent device node cannot stall Android startup.

LOG=/system/bin/log
BASE=/system/lib/modules/z1-radio
INSMod=/system/bin/insmod
mode=${1:-radio}
case "$mode" in radio|--bt-identity-only) ;; *) exit 2 ;; esac

# This system-image script is launched from post-fs after /system is mounted.
# Reset the optional radio result even when a later safety gate returns early.
if [ "$mode" = radio ] && [ -x /system/bin/setprop ]; then
    /system/bin/setprop sys.z1.radio.wifi_modules_ready 0 2>/dev/null
fi

say() {
    "$LOG" -t Z1Radio "$*"
}

skip() {
    say "radio bring-up skipped: $*"
    exit 0
}

DT=/proc/device-tree
if [ ! -d "$DT" ]; then
    DT=/sys/firmware/devicetree/base
fi

require_compatible() {
    node="$1"
    compatible="$2"
    file="$DT$node/compatible"
    [ -r "$file" ] || return 1
    /system/bin/grep -a -F -q "$compatible" "$file"
}

property_hex() {
    file="$1"
    expected="$2"
    [ -r "$file" ] || return 1
    actual=$(/system/bin/od -An -tx1 -v "$file" 2>/dev/null | /system/bin/tr -d '[:space:]')
    [ "$actual" = "$expected" ]
}

require_dt_hex() {
    node="$1"
    property="$2"
    expected="$3"
    property_hex "$DT$node/$property" "$expected"
}

node_enabled_or_unspecified() {
    status="$DT$1/status"
    [ -r "$status" ] || return 0
    property_hex "$status" 6f6b617900
}

kernel_release=$(cat /proc/sys/kernel/osrelease 2>/dev/null)
[ "$kernel_release" = "7.0.0-rc7-z1+" ] || skip "unexpected kernel release '$kernel_release'"
[ -x "$INSMod" ] || skip "Android insmod utility is missing"
[ -x "$LOG" ] || exit 0

# Verify the active DT (not just the build input). Exact resource cells guard
# against probing the wrong CONNSYS/MMIO/IRQ block. The in-tree CONNSYS power
# driver must stay disabled to avoid a second owner for these registers.
require_compatible / jbo,z1 || skip "active DT is not Z1"
require_compatible / mediatek,mt6572 || skip "active DT is not MT6572"
require_compatible /btif@1100e000 mediatek,btif || skip "BTIF DT node missing"
require_compatible /btif@1100e000/btif_tx@11000380 mediatek,btif_tx || skip "BTIF TX DMA DT node missing"
require_compatible /btif@1100e000/btif_rx@11000400 mediatek,btif_rx || skip "BTIF RX DMA DT node missing"
require_compatible /connectivity@18070000 mediatek,mt6572-consys || skip "CONSYS DT node missing"
require_compatible /wifi@180f0000 mediatek,wifi || skip "Wi-Fi HIF DT node missing"
node_enabled_or_unspecified /btif@1100e000 || skip "BTIF DT node disabled"
node_enabled_or_unspecified /connectivity@18070000 || skip "CONSYS DT node disabled"
node_enabled_or_unspecified /wifi@180f0000 || skip "Wi-Fi DT node disabled"
property_hex "$DT/connsys@18070000/status" 64697361626c656400 || skip "legacy in-tree CONNSYS power driver is not disabled"
require_dt_hex /btif@1100e000 reg 1100e0000000100011000380000000801100040000000080 || skip "BTIF MMIO map mismatch"
require_dt_hex /btif@1100e000 interrupts 000000000000003400000008 || skip "BTIF IRQ mismatch"
require_dt_hex /btif@1100e000/btif_tx@11000380 reg 1100038000000080 || skip "BTIF TX DMA MMIO mismatch"
require_dt_hex /btif@1100e000/btif_tx@11000380 interrupts 000000000000002900000008 || skip "BTIF TX DMA IRQ mismatch"
require_dt_hex /btif@1100e000/btif_rx@11000400 reg 1100040000000080 || skip "BTIF RX DMA MMIO mismatch"
require_dt_hex /btif@1100e000/btif_rx@11000400 interrupts 000000000000002a00000008 || skip "BTIF RX DMA IRQ mismatch"
require_dt_hex /connectivity@18070000 reg 18070000000010001000000000002000 || skip "CONSYS MMIO map mismatch"
require_dt_hex /connectivity@18070000 interrupts 000000000000007a00000008000000000000007c00000008 || skip "CONSYS IRQ map mismatch"
require_dt_hex /wifi@180f0000 reg 180f000000001000 || skip "Wi-Fi HIF MMIO map mismatch"
require_dt_hex /wifi@180f0000 interrupts 000000000000007b00000008 || skip "Wi-Fi HIF IRQ mismatch"

# The CONSYS node must have its PMIC/regulator/reset wiring; module insertion
# will call into this power path when the controller is enabled.
for property in mediatek,pwrap-regmap power-domains resets reset-names \
                vcn18-supply vcn28-supply vcn33_bt-supply vcn33_wifi-supply; do
    [ -r "$DT/connectivity@18070000/$property" ] || skip "CONSYS DT property $property missing"
done
for node in /btif@1100e000 /btif@1100e000/btif_tx@11000380 \
            /btif@1100e000/btif_rx@11000400 /connectivity@18070000 /wifi@180f0000; do
    node_enabled_or_unspecified "$node" || skip "radio DT node disabled: $node"
done

# The factory Bluetooth HAL polls this property for five seconds and aborts
# initialization when it is missing or "-1". The stock Z1 ramdisk sets the
# same value before radio userspace starts.
publish_bt_identity() {
    combo_chip_id=0x6572
    /system/bin/setprop persist.mtk.wcn.combo.chipid "$combo_chip_id" ||
        return 1
    observed_combo_chip_id=$(/system/bin/getprop persist.mtk.wcn.combo.chipid 2>/dev/null)
    [ "$observed_combo_chip_id" = "$combo_chip_id" ] ||
        return 1
    say "Bluetooth combo chip id=$observed_combo_chip_id"

    # R7 has no Android 4.4 nvram_daemon. The factory Bluetooth library still
    # gates every NVRAM read on this property. Publish it only when the existing
    # per-device AP NVRAM tree has the expected shape; neither record is copied or
    # rewritten. The runtime gate separately exposes the result to CI and board
    # diagnostics.
    bt_nvram_record=/data/nvram/APCFG/APRDEB/BT_Addr
    wifi_nvram_record=/data/nvram/APCFG/APRDEB/WIFI
    bt_nvram_ready=0
    if [ -f "$bt_nvram_record" ] && [ ! -L "$bt_nvram_record" ] &&
       [ -f "$wifi_nvram_record" ] && [ ! -L "$wifi_nvram_record" ] &&
       [ "$(wc -c < "$bt_nvram_record" 2>/dev/null)" = 64 ] &&
       [ "$(wc -c < "$wifi_nvram_record" 2>/dev/null)" = 512 ] &&
       [ -n "$(od -An -tx1 -N64 "$bt_nvram_record" 2>/dev/null | tr -d ' 0\n')" ]; then
        if [ "$(/system/bin/getprop nvram_init 2>/dev/null)" != Ready ]; then
            /system/bin/setprop nvram_init Ready ||
                say "cannot publish NVRAM readiness for the factory Bluetooth library"
        fi
        if [ "$(/system/bin/getprop nvram_init 2>/dev/null)" = Ready ]; then
            bt_nvram_ready=1
            say "Bluetooth NVRAM shape valid; factory nvram_init=Ready"
        fi
    else
        say "Bluetooth NVRAM unavailable: expected 64-byte BT_Addr and 512-byte WIFI under /data/nvram/APCFG/APRDEB"
    fi
    /system/bin/setprop sys.z1.radio.bt_nvram_ready "$bt_nvram_ready" ||
        say "cannot publish Bluetooth NVRAM readiness state"
    return 0
}

if [ "$mode" = --bt-identity-only ]; then
    [ "$(/system/bin/getprop ro.persistent_properties.ready)" = true ] || exit 1
    publish_bt_identity || { say "cannot publish or verify factory Bluetooth chip id"; exit 1; }
    exit 0
fi
# Keep the initial publication for older boot images. A matching init action
# repeats it after loading /data's persistent properties, before framework use.
publish_bt_identity || say "factory Bluetooth chip id not ready; radio module setup continues"

# The Linux7 kernel has FW_LOADER built in. Its firmware_class.path bootarg
# directs request_firmware() to /system/etc/firmware after init has mounted
# /system (this script runs from post-fs, before the radio modules are loaded).
# This only verifies firmware lookup availability; it does not turn Wi-Fi on
# or establish that the factory blob works with the Z1 radio at runtime.
FIRMWARE_DIR=/system/etc/firmware
FIRMWARE_PATH_PARAM=/sys/module/firmware_class/parameters/path
WIFI_RAM_CODE=$FIRMWARE_DIR/WIFI_RAM_CODE_SOC
firmware_path=$(cat "$FIRMWARE_PATH_PARAM" 2>/dev/null)
if [ -s "$WIFI_RAM_CODE" ]; then
    wifi_blob_present=yes
else
    wifi_blob_present=no
fi
if [ "$firmware_path" = "$FIRMWARE_DIR" ] && [ -s "$WIFI_RAM_CODE" ]; then
    say "factory SOC Wi-Fi firmware is visible to request_firmware; Wi-Fi remains masked pending runtime validation"
else
    say "Wi-Fi firmware lookup is not ready (firmware_class.path='$firmware_path', SOC blob present=$wifi_blob_present); radio continues with Wi-Fi masked"
fi

# Bluetooth depends on the BTIF/STP/WMT chain. Wi-Fi has a separate optional
# cfg80211/WLAN chain. A failure in WLAN must not suppress Bluetooth startup:
# these modules cannot always be unloaded after a failed insertion, so the
# failing wireless branch stays masked while WMT/BT initialization continues.
BT_MODULES="mtk_btif_drv mtk_stp_wmt_soc mtk_stp_bt_soc"
WIFI_MODULES="cfg80211 mtk_wmt_wifi_soc wlan_gen2"

module_file() {
    case "$1" in
        cfg80211) printf '%s\n' cfg80211.ko ;;
        mtk_btif_drv) printf '%s\n' mtk_btif_drv.ko ;;
        mtk_stp_wmt_soc) printf '%s\n' mtk_stp_wmt_soc.ko ;;
        mtk_stp_bt_soc) printf '%s\n' mtk_stp_bt_soc.ko ;;
        mtk_wmt_wifi_soc) printf '%s\n' mtk_wmt_wifi_soc.ko ;;
        wlan_gen2) printf '%s\n' wlan_gen2.ko ;;
        *) return 1 ;;
    esac
}

module_sha256_matches() {
    module=$1
    expected=$2
    file=$(module_file "$module") || return 1
    [ -r "$BASE/$file" ] || return 1
    result=$(/system/bin/sha256sum "$BASE/$file" 2>/dev/null) || return 1
    [ "${result%% *}" = "$expected" ]
}

# Validate the common helpers before loading non-unloadable BT/WMT modules.
for file in /system/bin/6620_launcher /system/bin/wmt_loader \
            /system/etc/firmware/WMT_SOC.cfg \
            /system/etc/firmware/ROMv1_patch_0_hdr.bin \
            /system/etc/firmware/ROMv1_patch_1_hdr.bin; do
    [ -s "$file" ] || skip "factory WMT helper input missing: $file"
done
[ -x /system/bin/6620_launcher ] || skip "6620_launcher is not executable"
[ -x /system/bin/wmt_loader ] || skip "wmt_loader is not executable"

for module in $BT_MODULES; do
    file=$(module_file "$module") || skip "unknown required Bluetooth module: $module"
    [ -s "$BASE/$file" ] || skip "required Bluetooth/WMT module file missing: $BASE/$file"
done

for module in $BT_MODULES; do
    [ -d "/sys/module/$module" ] && continue
    file=$(module_file "$module") || skip "unknown required Bluetooth module: $module"
    if ! "$INSMod" "$BASE/$file"; then
        skip "required Bluetooth/WMT module insertion failed: $file"
    fi
done

# Try the independent WLAN chain after the required Bluetooth transport. Any
# missing file or failed insert leaves sys.z1.radio.wifi_modules_ready at 0;
# it must not return before the BT/WMT helper readiness property is set.
wifi_ready=1
if ! module_sha256_matches wlan_gen2 \
       143c5611267eb4c5e20135fffdd39db491753147ef295278c3a69330c1fbf80f; then
    wifi_ready=0
    say "wlan_gen2.ko identity mismatch; refusing to load an unpinned Wi-Fi driver"
fi
if ! module_sha256_matches mtk_stp_wmt_soc \
       c9c62c26bc6027982f522ccba99ce0183ce976506e6432a311c682b895c7b07e; then
    wifi_ready=0
    say "mtk_stp_wmt_soc.ko identity mismatch; refusing the WMT/Wi-Fi branch"
fi
for module in $WIFI_MODULES; do
    file=$(module_file "$module") || { wifi_ready=0; say "unknown optional Wi-Fi module: $module"; break; }
    if [ ! -s "$BASE/$file" ]; then
        wifi_ready=0
        say "optional Wi-Fi module file missing: $BASE/$file; continuing with Bluetooth"
        break
    fi
done

if [ "$wifi_ready" = 1 ]; then
    for module in $WIFI_MODULES; do
        [ -d "/sys/module/$module" ] && continue
        file=$(module_file "$module") || { wifi_ready=0; break; }
        if ! "$INSMod" "$BASE/$file"; then
            wifi_ready=0
            say "optional Wi-Fi module insertion failed: $file; continuing with Bluetooth"
            break
        fi
    done
fi

# This source build uses legacy static major numbers because its
# WMT_CREATE_NODE_DYNAMIC option is unset. BT/WMT requires both transport
# nodes; the WLAN node is checked separately and cannot suppress Bluetooth.
ensure_radio_node() {
    path="$1"
    major="$2"
    minor="$3"
    expected=$(printf '%x:%x' "$major" "$minor")
    if [ -e "$path" ]; then
        [ -c "$path" ] || return 1
    else
        /system/bin/mknod -m 0660 "$path" c "$major" "$minor" || return 1
    fi
    actual=$(/system/bin/stat -c '%t:%T' "$path" 2>/dev/null)
    [ "$actual" = "$expected" ]
}

ensure_radio_node /dev/stpwmt 190 0 || skip "cannot create/validate /dev/stpwmt (190:0)"
ensure_radio_node /dev/stpbt 192 0 || skip "cannot create/validate /dev/stpbt (192:0)"

if [ "$wifi_ready" = 1 ] && ! ensure_radio_node /dev/wmtWifi 155 0; then
    wifi_ready=0
    say "cannot create/validate optional /dev/wmtWifi (155:0); continuing with Bluetooth"
fi

# Give userspace helpers their least-privilege endpoint owners.
tries=0
while [ "$tries" -lt 10 ]; do
    if [ -c /dev/stpwmt ] && [ -c /dev/stpbt ]; then
        break
    fi
    /system/bin/sleep 1
    tries=$((tries + 1))
done
[ -c /dev/stpwmt ] || skip "/dev/stpwmt not created by the loaded WMT module"
[ -c /dev/stpbt ] || skip "/dev/stpbt not created by the loaded BT module"

/system/bin/chmod 0660 /dev/stpwmt /dev/stpbt || skip "cannot set Bluetooth/WMT node modes"
/system/bin/chown 1000 1000 /dev/stpwmt || skip "cannot assign WMT ownership"
/system/bin/chown 1002 1001 /dev/stpbt || skip "cannot assign Bluetooth ownership"

if [ "$wifi_ready" = 1 ]; then
    if [ ! -c /dev/wmtWifi ]; then
        wifi_ready=0
        say "optional /dev/wmtWifi node was not created; continuing with Bluetooth"
    elif ! /system/bin/chmod 0660 /dev/wmtWifi ||
         ! /system/bin/chown 1000 1000 /dev/wmtWifi; then
        wifi_ready=0
        say "cannot set optional Wi-Fi node permissions; continuing with Bluetooth"
    fi
fi

if [ "$wifi_ready" = 1 ]; then
    /system/bin/setprop sys.z1.radio.wifi_modules_ready 1 ||
        say "could not publish optional Wi-Fi module readiness"
else
    /system/bin/setprop sys.z1.radio.wifi_modules_ready 0 ||
        say "could not publish unavailable Wi-Fi module state"
fi

say "Bluetooth/WMT transport is ready; starting shared factory radio helpers"
/system/bin/setprop sys.z1.radio.modules_ready 1 || skip "cannot signal radio readiness"
exit 0
