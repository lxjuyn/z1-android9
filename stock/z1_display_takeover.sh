#!/system/bin/sh
# The MT6572 kernel uses fbcon but its ueventd setup leaves /dev/tty0 absent.
# Create Linux's standard VT0 character node so KDSETMODE can switch to graphics.
if [ ! -c /dev/tty0 ]; then
    /system/bin/mknod -m 0600 /dev/tty0 c 4 0
fi

# z1_bootdiag reports whether KD_GRAPHICS was verified on the active VT.
# Keep the fbcon sysfs detach as a fallback even when VT mode is unavailable.
# Never fail the boot: log the error and continue.
/z1_bootdiag --graphics-mode
graphics_status=$?

/system/bin/sh /z1_fbcon_takeover.sh
fbcon_status=$?

if [ "$graphics_status" -ne 0 ]; then
    echo "z1_bootdiag --graphics-mode failed: $graphics_status" > /dev/kmsg 2>/dev/null || true
fi
if [ "$fbcon_status" -ne 0 ]; then
    echo "z1_fbcon_takeover failed: $fbcon_status" > /dev/kmsg 2>/dev/null || true
fi
exit 0
