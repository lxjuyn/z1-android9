#!/system/bin/sh
# Enable the Bluetooth framework on Z1 and report whether the adapter reached ON.
# Exit: 0 = adapter state ON, 1 = enable attempted but not ON, 2 = boot incomplete.
# This validates the framework/stack only; it does not prove controller, scan,
# pairing or A2DP operation.

[ "$(id -u)" = "0" ] || { echo "must run as root"; exit 1; }

waited=0
while [ "$(getprop sys.boot_completed)" != "1" ]; do
  waited=$((waited + 2))
  if [ "$waited" -ge 600 ]; then
    echo "boot_completed not reached after ${waited}s; not enabling"
    exit 2
  fi
  sleep 2
done

settings put global bluetooth_on 1

waited=0
while ! service check bluetooth_manager 2>/dev/null | grep -q "bluetooth_manager: found"; do
  waited=$((waited + 2))
  if [ "$waited" -ge 120 ]; then
    echo "bluetooth_manager service not registered after ${waited}s"
    exit 1
  fi
  sleep 2
done

svc bluetooth enable
sleep 3

# The service call fallback uses IBluetoothManager transaction 6
# (enable(String)) from android-9.0.0_r61 AIDL.
case "$(dumpsys bluetooth_manager 2>/dev/null)" in
  *"state: ON"*) ;;
  *)
    service call bluetooth_manager 6 s16 com.android.shell >/dev/null 2>&1
    ;;
esac

waited=0
state=""
while [ "$waited" -lt 60 ]; do
  dump="$(dumpsys bluetooth_manager 2>/dev/null)"
  state="$(printf '%s\n' "$dump" | grep -m1 -E '^[[:space:]]*state: ' | sed 's/^[[:space:]]*//')"
  enabled="$(printf '%s\n' "$dump" | grep -m1 -E '^[[:space:]]*enabled: ' | sed 's/^[[:space:]]*//')"
  case "$state" in
    "state: ON") break ;;
  esac
  waited=$((waited + 2))
  [ "$waited" -lt 60 ] && sleep 2
done

echo "adapter ${enabled:-enabled: <unknown>} / ${state:-state: <unknown>} after ${waited}s"

case "$state" in
  "state: ON")
    echo "RESULT: Bluetooth adapter is ON (framework only; controller/scan not verified)"
    exit 0
    ;;
  *)
    echo "RESULT: Bluetooth adapter did not reach ON"
    printf '%s\n' "$dump" | grep -E 'state:|enabled:|Bluetooth' | head -20
    exit 1
    ;;
esac
