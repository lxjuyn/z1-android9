#!/system/bin/sh
# Relay Bluetooth logcat tags into the kernel log for serial capture.
# Output may contain peer identifiers; do not paste unfiltered logs publicly.
while [ ! -e /dev/log/main ] && [ ! -S /dev/socket/logdw ]; do sleep 1; done
exec logcat -v brief -s BluetoothManagerService:V BluetoothAdapter:V AdapterService:V \
  BluetoothHci:V BluetoothService:V bt_vendor:V z1-bt-vendor:V bt_stack:V btif:V \
  bt_hci:V bt_snoop:V btsnoop:V > /dev/kmsg
