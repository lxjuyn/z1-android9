# Z1 off-mode charger

The ARM executable reads `/sys/class/power_supply/battery/capacity`
with the same parser as the Android Z1 Health HAL. It draws the reported
whole percentage when the sample is valid, including `0%`. If the
attribute is absent, unreadable or malformed, it draws `--%`. The R7
charging image enables the kernel's Z1 OAM estimate from BATSNS and
the latched battery open-circuit sample. The USB power supply reports
cable status separately.

Run `z1_charge_ui --sample` for a read-only check without the framebuffer.
For manual display validation, launch `z1_charge_ui` from a foreground
console when the framebuffer is available and no other display service
owns it. It polls capacity every two seconds and renders animation
frames every 200 ms. Stop it with Ctrl-C.

For boot integration, store the static executable as `/sbin/z1_charge_ui`
in the boot ramdisk and select it with `rdinit=/sbin/z1_charge_ui` in
the appended DT bootargs. Keep the Android init ELF at `/init`. The
standalone build produces only the executable. Automatic startup needs
an image with that ramdisk entry, bootarg and LK boot-mode handoff.
As PID 1, the executable reads the live
`/chosen/mediatek,boot-mode` device-tree property through sysfs.
Only exact big-endian u32 values 8 and 9 select the off-mode charger.
For other, missing or malformed values it executes Android `/init`.

The charging path uses procfs, sysfs and devtmpfs from the kernel.
It starts the display when USB is online and the framebuffer exists.
The existing R7 PWM1 module gates the panel backlight after six seconds.
A short power-key press shows the animation again. A release after a
hold of at least 500 ms requests a normal boot when the battery voltage
is above 3450 mV. This uses mainline BATSNS `voltage_now` for the gate
and the MT6572 watchdog's Z1 charger restart path for the LK transition.

The charger monitors
`/sys/class/power_supply/usb/online` once per second. An observed
attachment followed by three consecutive valid offline samples
requests kernel power-off. An unreadable or invalid sample breaks
the offline sequence. Physical shutdown requires the built-in
MT6323 power-off handler. Charger mode leaves the Android partitions
unmounted.

Build and run the host and ARM QEMU fixtures with Android NDK r26d.

```sh
z1a9_gpt/health/charge_ui/build.sh /path/to/new-output-directory
```

Tests cover unavailable capacity, 0%, 100%, a padded framebuffer with
a nonzero viewport offset, exact and malformed boot mode properties,
USB removal, and the power-key and voltage thresholds. Live validation
needs to confirm visible framebuffer and backlight behavior, transition
to normal boot, agreement with Android's percentage, and power-off
when the cable is removed.
