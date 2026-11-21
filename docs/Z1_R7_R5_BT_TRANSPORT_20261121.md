# R7 R5: Bluetooth startup and H4 transmission

Status: `BOARD_TESTED_PARTIAL_PASS`.

## Findings

R3 reached WMT function-on and STP readiness, then the factory userspace
initializer reported `Get NVRAM restore ready fails!` and closed the controller.
No R4 board test occurred. Offline R4 review found that init copied
`/z1_libbt-vendor.so` into `/system/vendor/lib/libbt-vendor.so`; R4 still
carried the older ramdisk vendor library (`43944199...`). R5 pins both locations
to the factory-first/direct-H4 adapter (`a705913c...`). The boot is derived from
the verified lock-screen-gate boot and replaces `/z1_libbt-vendor.so` inside
the ramdisk. This matters because `z1stage_boot.sh` can copy the ramdisk copy to
`/system/vendor/lib/libbt-vendor.so` at boot.

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

Package:
`/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/z1-archive/r7-r5-bt-h4-writev-lock-20261121/`

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `boot.img` | 5,986,304 | `5ae4c69066d5285a6819d88f82ed11a4d3bce4b3bc9bd8775dc8425270a2be7d` |
| `system.img` | 943,718,400 | `774a84d6ec72cac67221248e6c857a7da077073a3c2bab11720ae06c9aea8571` |
| `mtk_stp_bt_soc.ko` | 12,192 | `af2f849794e26272716dbc79ba7239803a05d1ecbce53e55eb98f60c3c76203f` |

The boot base is the lock-gate candidate
`1b0519b30d4b2dffeecabaeef70ee6e73eebccea6b422b3f7fffa117c0eb74eb`.
Its zImage remains
`b6b5174e8f394a70e8bde22bf2ff472a60aeb500d31ed1088090a0abb654c63e`.
The ramdisk record `/z1_libbt-vendor.so` is the direct-H4 adapter
`a705913c606709447958ac1071366df3d5d5af521a975b29d6cd9815edd0dc7f`,
mode `100644`, root:root.

The system base is
`76f7c5f333bbc8ed41192b696eff5e392344c7c796293df629480585c862dd58`.
It retains the same direct-H4 `libbt-vendor.so`
`a705913c606709447958ac1071366df3d5d5af521a975b29d6cd9815edd0dc7f`
and BTIF RX fix
`a72b21df2173f210b04dc33583950ae0553c20de06366a877ca568f612724540`.

Debugfs readback matched the module SHA256, regular-file type, mode `0644`,
root:root ownership, and `u:object_r:system_file:s0`. The system vendor library
also read back as `a705913c...`, mode `0644`, root:root, with
`u:object_r:vendor_file:s0`. `e2fsck -fn` passed on the final ext4 image.

The corresponding host archive is
`bridge_backups/products/products_r7_r5_stp_writev_20261121_221226/`; it
contains the boot, module, patch, build logs, WMT runtime symbols, and system
replacement manifest. The external package above is the materialized pair.

Do not flash `out/r7_bt_writev_transport_20261121_222229/`: that intermediate
package contains the correct system module but retained the R3 boot and its old
ramdisk vendor library.

## Board gate

Start direct CH340 capture at 921600 before powering the R5 board (matching its live ttyS0 boot argument). Confirm boot
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

## Board result

R5 was flashed successfully on 2026-11-21. `boot.img` and `system.img` matched
the published hashes, the module running in memory was 16,384 bytes in
`/proc/modules`, and system readback found the new module at
`/system/lib/modules/z1-radio/mtk_stp_bt_soc.ko` with SHA256
`af2f8497...`. The installed vendor adapter read back as `a705913c...`.

The board proved the transport change:

- `z1-bt-vendor` loaded the direct-H4 adapter;
- after the factory initializer failed, it opened `/dev/stpbt` directly;
- Android received HCI command-complete events through the new descriptor;
- no `H4Protocol::Send` writev error occurred.

The next failure was layer-specific. Multiple R5 crash records identify
`parse_read_local_extended_features_response` in `libbluetooth.so`, with
`CHECK(stream != NULL)` at LineageOS 16 `hci_packet_parser.cc:131`. This
matches the parser for HCI Read Local Extended Features (`0x1004`). The
abort record does not include the raw event or identify the failing page.
Do not attribute this crash to LE Read White List Size or infer an HCI
status from the abort. In Android's HCI status table, `0x30` is Parameter
Out Of Mandatory Range; Controller Busy is `0x3a`.

The same boot still had `nvram_init` empty and
`sys.z1.radio.bt_nvram_ready=0`. R5 predates the committed NVRAM 66/514-byte
shape and AID 1050 access fix. The factory path therefore never completed its
controller setup before direct H4 was selected.

## R6 follow-up

R6 combines the board-tested R5 transport with the committed NVRAM fix:

`/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/z1-archive/r7-r6-bt-nvram-writev-20261121_231045/`

| File | SHA256 |
| --- | --- |
| `boot.img` | `5ae4c69066d5285a6819d88f82ed11a4d3bce4b3bc9bd8775dc8425270a2be7d` |
| `system.img` | `5bfbb8eaf17803190b6c6c6a0bff149fc3d600dcdb4d9e6bb87f455f81719663` |
| R5-to-R6 delta | `46218a794bea5c3f834d175c9dccc46f4f3429d301c7b38eb0c2840d82abb1a9` |

R6 passed replacement readback, exact delta replay, and `e2fsck -fn`. It is
host verified and awaits a clean board boot. The decisive check is whether the
factory NVRAM path now completes and `/dev/stpbt` reaches the controller-ready
state before Android sends the BLE initialization commands.
