# Z1 Bluetooth direct-H4 fallback

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`.

The R7 logs show two distinct stages. First, `/dev/stpbt` can power Bluetooth
through WMT and report `BT_open` success. The legacy
`libbluetooth_mtk.so` initializer can still fail afterward while it performs
its own NVRAM/GORM work. In that case the HAL returns
`initializationComplete(INITIALIZATION_ERROR)` about five seconds after the
kernel open, and `com.android.bluetooth` aborts in `hci_thread`.

The vendor adapter now keeps the factory path as the first choice. If
`mtk_bt_enable()` does not return an HCI descriptor, the adapter opens
`/dev/stpbt` directly with nonblocking H4 semantics and returns that descriptor
to Android's `H4Protocol`. The fallback does not copy NVRAM or invent a
Bluetooth address. It gives the Android HCI stack a chance to initialize the
controller directly after the legacy initializer fails.

Descriptor ownership is explicit:

- a factory descriptor is released through `mtk_bt_disable(fd)`;
- a direct descriptor is released with `close(fd)`;
- factory disable failure retains the descriptor and driver for a later retry;
- direct close consumes the descriptor on Linux even if `close()` reports
  `EINTR`.

The production adapter source SHA256 is
`cbd21aac7a551444289a625604390768457553ca02fcf768e6302182d3c37543`.
The ARMv7 API28 candidate SHA256 is
`a705913c606709447958ac1071366df3d5d5af521a975b29d6cd9815edd0dc7f`.
Its only dynamic dependencies are `libc.so`, `libdl.so`, and `liblog.so`.

Host verification covers 8 cases and 727 assertions with no failures. The
explicit cleanup probe covers 9 cases and 1,070 assertions with no failures.
Both normal and ASan/UBSan runs pass. These tests prove adapter state,
descriptor ownership, factory fallback, callback behavior, and close paths.
They do not prove that the real controller answers HCI commands.

The paired system image is derived from the verified R7 speaker base. Apply
`out/r7_bt_direct_h4_20261121/system_from_r7_speaker.img.z1delta` to that base,
or use the materialized `system.img`.

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `boot.img` | 5,984,256 | `4fde12b18f2f0ee7423d63a0165847a0075f2c67148100e77c09d01de08b33bd` |
| `system.img` | 943,718,400 | `1b7c7d5fc9788cbcbc8d8db7e4cffd0ba251e29ffd989494c1b229fb891fb116` |
| system delta | 94,581 | `b0a7c946dbdcd0677cd76d077ef59e6e57403c6cf00646e591da5660aaeda58f` |
| `libbt-vendor.so` | 8,328 | `a705913c606709447958ac1071366df3d5d5af521a975b29d6cd9815edd0dc7f` |

The system passed `e2fsck -fn`. `/vendor/lib/libbt-vendor.so` was read back
from the ext4 image with the exact ELF hash, UID/GID `0:0`, mode `0644`, and
SELinux label `u:object_r:vendor_file:s0`.

For the next board experiment, prefer
`out/r7_bt_direct_h4_on_r3_20261121/`. It uses the R3 25 MHz boot and fixed
`mtk_btif_drv.ko`, then overlays only the new adapter onto that system. Its
system SHA256 is
`76f7c5f333bbc8ed41192b696eff5e392344c7c796293df629480585c862dd58`;
the overlay is derived from R3 system
`7e0aa0c8ee5a1e443156a9031a4255275bb0e9f58055b515d4f9b6cd36503e2f`
and has a 7-block, 28,943-byte block delta. This combined package keeps the
BTIF arithmetic fix and the userspace fallback in one test.

The next board run should stop Android Bluetooth, enable it from a clean boot,
and capture kernel plus `logcat -b all`. A successful transition should show
`factory Bluetooth initialization failed` followed by `using direct H4`, then a
successful HCI initialization and controller traffic. The five-second
`initializationComplete(INITIALIZATION_ERROR)` abort must disappear. Discovery,
pairing, reconnect, and A2DP remain separate acceptance gates.
