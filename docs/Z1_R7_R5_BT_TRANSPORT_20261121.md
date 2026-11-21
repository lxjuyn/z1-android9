# R7 R5: Bluetooth startup and H4 transmission

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`.

## Findings

R3 reached WMT function-on and STP readiness, then the factory userspace
initializer reported `Get NVRAM restore ready fails!` and closed the controller.
No R4 board test occurred. Offline R4 review found that init copied
`/z1_libbt-vendor.so` into `/system/vendor/lib/libbt-vendor.so`; R4 still
carried the older ramdisk vendor library (`43944199...`). R5 pins both locations
to the factory-first/direct-H4 adapter (`a705913c...`). This transport package
retains the R3 boot unchanged so it isolates the Bluetooth experiment; the
separately verified boot-only lock-screen gate is not mixed in.

The Android 9 HAL selects H4 with one descriptor. AOSP Pie
`H4Protocol::Send` sends packet type and payload in two `iovec` entries via
`writev()`. The old `/dev/stpbt` only exposed `.write`; this kernel's vectored
write fallback at `fork_linux/fs/read_write.c:1057-1061` invokes `.write`
separately for each entry. The factory `BT_SendHciCommand` sends one contiguous
H4 frame instead. R5 changes the exact pinned MT6572 STP Bluetooth module to
`.write_iter`, copies both entries under the existing TX semaphore, and performs
one STP send. Normal single-buffer writes still work through
`new_sync_write()`.

Existing send limits, reset handling, and error returns are retained. Only
`mtk_stp_bt_soc.ko` changes in system relative to the checked direct-H4-on-R3
system. All 35 imports resolve against the matched kernel or the untouched WMT
module extracted from R4.

Primary references:

- [AOSP Android 9 vendor interface](https://android.googlesource.com/platform/hardware/interfaces/+/android-9.0.0_r61/bluetooth/1.0/default/vendor_interface.cc)
- [AOSP H4 protocol](https://android.googlesource.com/platform/hardware/interfaces/+/android-9.0.0_r61/bluetooth/1.0/default/h4_protocol.cc)
- [AOSP address selection](https://android.googlesource.com/platform/hardware/interfaces/+/android-9.0.0_r61/bluetooth/1.0/default/bluetooth_address.cc)
- local matching kernel `fork_linux/fs/read_write.c`
- `gabin8/mt6572-connsys-modules@e9961becdc889266c3a8b5a488ff741159b6ad80`

## Reproducible build

The pinned source archive is 1,200,358 bytes with SHA256
`af8bb14c434e2ef78d7367a28f2cd5a3a662adbece6b5c67d1e56f20e954613f`.
The original BT module is 12,292 bytes with SHA256
`01fbd5706e9c74dc9759d3da60da5e3a5443c5c71c12c0dc1a1464495e45f63e`.

An isolated `M=` build must receive the existing R4 WMT exports through
`KBUILD_EXTRA_SYMBOLS`. Without them, modpost emits `depends=` rather than the
required `depends=mtk_stp_wmt_soc`. The audited export list is included in
the package as `mtk_stp_wmt_soc.Module.symvers`.

Two clean builds produced this stripped module byte for byte:

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `mtk_stp_bt_soc.ko` | 12,192 | `af2f849794e26272716dbc79ba7239803a05d1ecbce53e55eb98f60c3c76203f` |

The `.modinfo` vermagic is
`7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8`. The fops relocation points to
`BT_write_iter`. Every undefined symbol resolves from this kernel or
`mtk_stp_wmt_soc`.

## Package

Package: `out/r7_bt_writev_transport_20261121_222229/`

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `boot.img` | 5,984,256 | `5baddbade8fb3b50e53220370d1eb11f1690a428af2690869e1e44c7e4e8441b` |
| `system.img` | 943,718,400 | `4ea4fa8a6314fbfb94582a0273176abd2568bb117421ba4edce334d017f9dd7b` |
| block delta | 20,742 | `5e49a26de9ff37ea98e7e866a921ef0bf8c1390d5f6cbb724d4d7ef184eeb841` |

The system base is `76f7c5f333bbc8ed41192b696eff5e392344c7c796293df629480585c862dd58`.
It retains direct-H4 `libbt-vendor.so`
`a705913c606709447958ac1071366df3d5d5af521a975b29d6cd9815edd0dc7f`
and BTIF RX fix
`a72b21df2173f210b04dc33583950ae0553c20de06366a877ca568f612724540`.

Debugfs readback matched the module SHA256, regular-file type, mode `0644`,
root:root ownership, and `u:object_r:system_file:s0`. The block delta reapplied
to the exact system SHA256. `e2fsck -fn` passed. SHA256SUMS passed again after
copying the package to
`bridge_backups/products/products_r7_bt_writev_transport_20261121_222548/`.

## Board gate

Start CH340 capture at 115200 before powering the board. Confirm boot
completion and storage stability before enabling Bluetooth.

The pass sequence is:

1. `mtk_stp_bt_soc` loads with `depends=mtk_stp_wmt_soc`;
2. WMT turns on Bluetooth and `/dev/stpbt` opens;
3. the factory path succeeds or logs the direct-H4 fallback;
4. Android logs successful HCI initialization and keeps controller traffic
   running beyond five seconds;
5. no `android.hardware.bluetooth-hci-h4` write error appears.

Then check Read BD_ADDR against the device's known identity without posting the
address to shared logs. No factory calibration record or address override is
shipped. If no HCI reply follows a successful WMT open, stop Android Bluetooth
and confirm it released `/dev/stpbt` before using the exclusive HCI probe.
Test scan, pairing, reconnect, Wi-Fi coexistence, and A2DP separately. Earlier
SoC resets remain a separate issue.
