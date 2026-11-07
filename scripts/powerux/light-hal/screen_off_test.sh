#!/system/bin/sh
# Deterministic screen off / on test without waiting for the timeout.
# KEYCODE_SLEEP=223 forces the display power state to OFF, which is exactly
# the path DisplayPowerController -> DisplayPowerState -> light HAL takes.
PATH=/system/bin:/system/xbin:$PATH
BL=/sys/class/leds/lcd-backlight/brightness
settimeout() { settings put system screen_off_timeout "$1" >/dev/null 2>&1; }
setstay()    { settings put global stay_on_while_plugged_in "$2" >/dev/null 2>&1; }

echo "before: brightness=$(cat $BL)  wakefulness=$(dumpsys power | grep -m1 mWakefulness=)"
settimeout 30000            # 30 s, replaces z1_pm_guard's 2 h
setstay 0 0                 # do not keep the screen on while charging
input keyevent 223          # SLEEP
sleep 3
echo "after sleep: brightness=$(cat $BL)"
dumpsys power | grep -E "mWakefulness=|Display Power:"
input keyevent 224          # WAKEUP
sleep 3
echo "after wake:  brightness=$(cat $BL)"
dumpsys power | grep -E "mWakefulness=|Display Power:"
