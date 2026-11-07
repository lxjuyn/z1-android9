#!/system/bin/sh
# Z1 power/backlight chain check.  Run on the board as root:
#   adb shell sh /data/local/tmp/powerux_check.sh      (or over the CH340 console)
# Read-only except for the explicit backlight write test at the end.
PATH=/system/bin:/system/xbin:$PATH
BL=/sys/class/leds/lcd-backlight/brightness

echo "== 1. light HAL service (VINTF registration) =="
getprop | grep -i "init.svc.*light"
ps -A 2>/dev/null | grep -i "light@2.0" || ps | grep -i "light@2.0"
service list 2>/dev/null | grep -i "android.hardware.light"

echo "== 2. light HAL ready marker (needs logcat) =="
logcat -d -b all 2>/dev/null | grep -i -m3 "Light HAL service is Ready\|Could not register service for Light HAL\|Unable to get ILight"

echo "== 3. sysfs node + ownership (DAC gate) =="
ls -lZ /sys/class/leds/ 2>/dev/null | grep -i backlight
ls -l "$BL" 2>/dev/null
echo -n "max_brightness = "; cat /sys/class/leds/lcd-backlight/max_brightness 2>/dev/null
echo -n "brightness     = "; cat "$BL" 2>/dev/null
echo "(expected owner: system system  0644; max_brightness 6)"

echo "== 4. write test (bypasses framework/HAL) =="
for v in 0 1 6; do
    echo "$v" > "$BL" 2>/dev/null
    sleep 1
    echo "wrote $v -> readback $(cat "$BL" 2>/dev/null)"
done

echo "== 5. framework display state =="
dumpsys power 2>/dev/null | grep -E "mWakefulness=|Display Power:|mScreenBrightnessOverrideFromWindowManager|mScreenOffTimeoutSetting|mStayOnWhilePluggedInSetting"
dumpsys display 2>/dev/null | grep -i -m5 "mScreenState\|state=OFF\|Display Power"

echo "== 6. AVC noise (permissive today, must be fixed before enforcing) =="
dmesg 2>/dev/null | grep -i "hal_light_default" | tail -5

echo "== done =="
