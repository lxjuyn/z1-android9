#!/system/bin/sh
# Z1 WiFi/BT bring-up: insmod CONNSYS stack + WMT on + wlan loader
# Order matters: stp_uart→stp_bt→stp_gps→wmt_wifi→hif_sdio→wlan
MODDIR=/system/lib/modules
FWD=/system/etc/firmware
dmesg -n 1
for m in mtk_stp_uart mtk_stp_bt mtk_stp_gps mtk_wmt_wifi mtk_hif_sdio mtk_stp_wmt; do
  if [ -f "$MODDIR/$m.ko" ]; then
    insmod "$MODDIR/$m.ko" 2>/dev/null && echo "z1wifi: $m OK" || echo "z1wifi: $m FAIL"
  fi
done
# WMT chip power-on via wmt_loader (opens /dev/stpwmt, pushes WMT_SOC.cfg + RAM code)
if [ -x /system/bin/wmt_loader ]; then
  /system/bin/wmt_loader &
  sleep 2
fi
# WLAN driver + firmware
if [ -f "$MODDIR/wlan_mt6620.ko" ]; then
  insmod "$MODDIR/wlan_mt6620.ko" 2>/dev/null && echo "z1wifi: wlan OK" || echo "z1wifi: wlan FAIL"
fi

# Bluetooth: kernel modules already loaded above (mtk_stp_bt).
# Userspace: libbt-vendor.so dlopens libbluetoothdrv.so (factory, in /system/lib)
# requiring libstdc++.so (factory 4.4 libc++ replacement, also in /system/lib).
# wpa_supplicant (LOS14 binary, deps all present in A9 /system/lib) starts via
# init.z1.network.rc service z1_wpa_supplicant on sys.wlan0.up=1 (v7d+).
