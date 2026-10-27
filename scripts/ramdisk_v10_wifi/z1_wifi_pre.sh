#!/system/bin/sh
# v9 Z1 WiFi pre-stage. Runs at post-fs, SYNCHRONOUSLY (init "exec"), BEFORE
# z1_linux7_radio_init.sh insmods wlan_gen2.ko.
#
# Why this file exists (three host-verified facts):
#  1. wlan_gen2.ko opens the calibration blob with the absolute path
#     "/etc/firmware/nvram/WIFI" (string @0x8235c of the .ko). On Android
#     /etc is the ramdisk root, /system is a different partition, so the
#     copy that z1_radio_finish.sh used to make (/system/etc/firmware/nvram/WIFI)
#     is never opened by the driver. Stage it into the rootfs instead.
#  2. cfg80211 request_firmware("regulatory.db") only searches
#     firmware_class.path (=/system/etc/firmware) and /lib/firmware. The
#     ramdisk carries /regulatory.db at the root, so /lib/firmware would
#     never find it. Stage it as well (removes the
#     "Direct firmware load for regulatory.db failed with error -2" warning
#     and the all-passive default regdomain).
#  3. kalFirmwareOpen() tries (static name) -> "WIFI_RAM_CODE_%s" built from
#     glGetChipInfo() -> (static name). The factory tree only ships
#     WIFI_RAM_CODE_SOC, so the "%s" == "SOC" path must win. Cheap hedge:
#     also expose it as WIFI_RAM_CODE_MT6582 / WIFI_RAM_CODE in /lib/firmware
#     (a plain copy; nothing is renamed in /system).

log() { printf '<6>[z1wifipre] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

FIRMWARE_DIR=/system/etc/firmware

# 1. WIFI NVRAM at the exact path the driver filp_open()s.
mkdir -p /etc/firmware/nvram 2>/dev/null
if [ -s /wifi-nvram-WIFI.bin ] && [ ! -s /etc/firmware/nvram/WIFI ]; then
    if cp /wifi-nvram-WIFI.bin /etc/firmware/nvram/WIFI 2>/dev/null; then
        chmod 0644 /etc/firmware/nvram/WIFI 2>/dev/null
        log "NVRAM staged at /etc/firmware/nvram/WIFI ($(wc -c < /etc/firmware/nvram/WIFI 2>/dev/null) bytes)"
    else
        log "NVRAM staging FAILED (cp /wifi-nvram-WIFI.bin)"
    fi
elif [ -s /etc/firmware/nvram/WIFI ]; then
    log "NVRAM already staged"
else
    log "wifi-nvram-WIFI.bin missing from the ramdisk; driver will run on build defaults"
fi

# 2. /lib/firmware staging (request_firmware fallback directory).
mkdir -p /lib/firmware 2>/dev/null
if [ -s /regulatory.db ] && [ ! -s /lib/firmware/regulatory.db ]; then
    cp /regulatory.db /lib/firmware/regulatory.db 2>/dev/null &&
        log "regulatory.db staged at /lib/firmware/regulatory.db"
fi

# 3. Firmware-name hedge (skip silently when /system is not readable).
for alias in WIFI_RAM_CODE_MT6582 WIFI_RAM_CODE; do
    if [ -s "$FIRMWARE_DIR/WIFI_RAM_CODE_SOC" ] && [ ! -s "/lib/firmware/$alias" ]; then
        cp "$FIRMWARE_DIR/WIFI_RAM_CODE_SOC" "/lib/firmware/$alias" 2>/dev/null
    fi
done

# 4. Report the WMT inputs the kernel driver will parse, so a missing blob is
#    visible in dmesg long before the function-on attempt.
for f in WMT_SOC.cfg ROMv1_patch_0_hdr.bin ROMv1_patch_1_hdr.bin WIFI_RAM_CODE_SOC; do
    [ -s "$FIRMWARE_DIR/$f" ] || log "WMT input missing: $FIRMWARE_DIR/$f"
done
log "pre-stage done; firmware_class.path=$(cat /sys/module/firmware_class/parameters/path 2>/dev/null)"
exit 0
