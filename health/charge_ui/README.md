# Z1 off-mode charging percentage preview

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

The exact R7 boot `09b2c74d…` includes the stock Pie `on charger`
class trigger and a `/charger -> /sbin/charger` symlink, but no
`/sbin/charger` or service definition. System `10decdbb…` includes
the Z1 Health HAL and omits the Pie charger program and resources.
The legacy Android 4.4 recovery assets and healthd have a separate
startup contract. The preview is standalone and is not registered
with init. Automatic off-mode display needs a board-verified LK mode
handoff and charger lifecycle.

Build and run the host and ARM QEMU fixtures with Android NDK r26d.

```sh
z1a9_gpt/health/charge_ui/build.sh /path/to/new-output-directory
```

Tests cover absent and malformed capacity, legitimate 0%, 100%, and
a padded framebuffer with a nonzero viewport offset. Live board
validation still needs to confirm that fbdev drawing appears on the
panel and that the observed percentage agrees with the kernel and
Android Health HAL.
