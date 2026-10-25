#!/system/bin/sh
# Optional Z1 touch evidence helper. Not started by init automatically.
# This never scans the bus, enables GPIOs, changes power or binds a DT node.
set -u
log() { echo "z1touch: $*" > /dev/kmsg 2>/dev/null || echo "z1touch: $*"; }
mode=${1:-help}
WORK=/data/local/tmp/z1-driver-evidence
prepare() {
  /system/bin/mkdir -p "$WORK" || return 1
  /system/bin/toybox zcat /system/etc/z1-driver-evidence/i2cprobe.gz > "$WORK/i2cprobe" || return 1
  /system/bin/chmod 0755 "$WORK/i2cprobe" || return 1
  /system/bin/toybox zcat /system/etc/z1-driver-evidence/edt-ft5x06.ko.gz > "$WORK/edt-ft5x06.ko" || return 1
}
case "$mode" in
  probe)
    if [ ! -c /dev/i2c-1 ]; then
      log "missing /dev/i2c-1; no probe attempted"
      exit 2
    fi
    prepare || { log "could not unpack probe to $WORK"; exit 2; }
    "$WORK/i2cprobe" /dev/i2c-1 0x15 2>&1 | tee /dev/kmsg
    ;;
  identity)
    if [ ! -c /dev/i2c-1 ]; then
      log "missing /dev/i2c-1; no identity read attempted"
      exit 2
    fi
    prepare || { log "could not unpack probe to $WORK"; exit 2; }
    "$WORK/i2cprobe" /dev/i2c-1 dump 0x15 0xa6 6 2>&1 | tee /dev/kmsg
    ;;
  module)
    # Information only; no node means normal insmod must not create a device.
    if [ -f /system/etc/z1-driver-evidence/edt-ft5x06.ko.gz ]; then
      /system/bin/toybox zcat /system/etc/z1-driver-evidence/edt-ft5x06.ko.gz | /system/bin/toybox sha256sum 2>&1 | tee /dev/kmsg
    else
      log "compressed module missing"
      exit 2
    fi
    ;;
  *)
    echo "Usage: /z1_touch_evidence.sh probe|identity|module"
    echo "Read-only targeted I2C1 0x15 evidence only; no full scan or GPIO operation."
    exit 2
    ;;
esac
