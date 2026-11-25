# Z1 off-mode charger and percentage preview

The ARM executable reads `/sys/class/power_supply/battery/capacity`
with the same parser as the Android Z1 Health HAL. It draws the reported
whole percentage when the sample is valid, including `0%`. If the
attribute is absent, unreadable or malformed, it draws `--%`. It does
not estimate state of charge from USB VCDT or voltage. The reliability
of the displayed percentage depends on the kernel's capacity provider.
A voltage-table estimate is still an estimate. This program adds no
fuel gauge or measurement.

Run `z1_charge_ui --sample` for a read-only check without the framebuffer.
For manual display validation, launch `z1_charge_ui` from a foreground
console when the framebuffer is available and no other display service
owns it. The process polls every two seconds and redraws when the value
changes. Stop it with Ctrl-C. It does not change brightness, blanking,
input handling, boot mode or charger state.

For boot integration, store the static executable as `/sbin/z1_charge_ui`
in the boot ramdisk and select it with `rdinit=/sbin/z1_charge_ui` in
the appended DT bootargs. Keep the Android init ELF at `/init`. The
standalone build produces only the executable. Automatic startup needs
an image with that ramdisk entry, bootarg and LK boot-mode handoff.
As PID 1, the executable reads the live
`/chosen/mediatek,boot-mode` device-tree property through sysfs.
Only the exact big-endian u32 value 8 selects the off-mode charger.
For other, missing or malformed values it executes Android `/init`.

The charging path uses procfs, sysfs and devtmpfs from the kernel.
It starts the display when the framebuffer node appears and monitors
`/sys/class/power_supply/usb/online` once per second. An observed
attachment followed by three consecutive valid offline samples
requests kernel power-off. An unreadable or invalid sample breaks
the offline sequence. Physical shutdown requires the built-in
MT6323 power-off handler. Charger mode leaves the Android partitions
unmounted and does not implement a power-key transition to Android.

Build and run the host and ARM QEMU fixtures with Android NDK r26d.

```sh
z1a9_gpt/health/charge_ui/build.sh /path/to/new-output-directory
```

Tests cover unavailable capacity, 0%, 100%, a padded framebuffer with
a nonzero viewport offset, exact and malformed boot mode properties,
and transient and stable USB removal. Live board validation needs to
confirm LK mode handoff, unaffected normal boot, visible fbdev drawing,
agreement with kernel and Android Health HAL percentage, and physical
shutdown when the cable is removed.
