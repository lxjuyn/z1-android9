# R7 donor Wi-Fi integration experiment

Build from the installed core-wake/livegain pair. The boot starts from the
cold Bluetooth comparator and removes the early Wi-Fi scan only. It keeps
the launcher, factory Bluetooth configuration and the early shared WMT
debug setting. The two scripts that assign the Wi-Fi node both use
UID 1000 and Wi-Fi GID 1010 with mode 0660. The Wi-Fi service still runs
as user `wifi`, with groups `wifi gps` and its existing capabilities and
SELinux policy.

The Wi-Fi HAL shim exports three DriverTool methods. The donor library
has a distinct SONAME and still supplies the HAL ABI, including the
function table and firmware mode handling. Boot and system form a test
pair. To roll back, return to the exact input boot and system.

```sh
python3 z1a9_gpt/wifi/wmt_power/donor/build_candidate.py \
    installed-boot.img installed-system.img /path/to/new-test-directory
```

The build runs the shim under QEMU with the exact Pie linker and libraries.
It checks all three DriverTool vtable entries, donor symbol resolution,
the STA firmware path and vendor function table, start, query, stop,
repeat toggles, mismatched device number, unavailable module, refused
open, WMT write failures, interface registration timeout and rollback.
It also reads back boot and system payloads and runs read-only e2fsck.

The vendor service controls the normal stop path after legacy cleanup.
It still has the stock Pie exceptional path when a later stage fails.
For example the framework can stop setting up an STA interface after
starting the HAL and before registering the interface. If it does not
stop the HAL, the kernel can still have Wi-Fi powered. After an off
request fails in the HAL, a future successful start/stop cycle can retry
the request. The separate Pie source patch repairs those lifecycle
callers.

This is an offline experiment. The old boot-time probe also suppressed
WMT power save after activating Wi-Fi. This pair does not repeat that
operation. Board validation must observe cold Bluetooth with Wi-Fi off,
Wi-Fi enable and association, subsequent Wi-Fi disable with Bluetooth
connected, and repeated Wi-Fi toggles. Capture WMT function transitions
and power-save events during coexistence.
