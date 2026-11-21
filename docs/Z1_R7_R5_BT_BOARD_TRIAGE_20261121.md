# R7 R5 board Bluetooth triage — 2026-11-21

Status: R5 reaches Android boot complete, but Bluetooth cannot remain on.
The NVRAM layer below passed offline validation; it is not a Bluetooth fix
for all the observed failures and has not been installed.

Evidence: local capture `mainline_recon/z1_boot_mainline_20261121_225400.log`.
The board's vendor library and STP driver file match the pinned R5 digests.

| Layer | Board result | What would close the issue |
|---|---|---|
| WMT/BTIF | At 256.5 s the first BT open begins. At 277 s WMT times out receiving the function-on response. The combo reset tears down Wi-Fi; Wi-Fi then recovers and a later BT open succeeds (lines 4427–4490). | Cold enable with Wi-Fi off, then a separate cold enable with Wi-Fi on. Compare WMT RX/TX and combo-reset logs. The BTIF `no sync pkt` line comes from a diagnostic scan after the timeout; that line alone does not locate the lost response. |
| Factory NVRAM | Factory lib logs `Get NVRAM restore ready fails!`. It waits five seconds each retry before direct H4 is tried. The older R5 payload does not include the 66/514-byte storage-record and AID 1050 changes. | Verify both `nvram_init=Ready` and the BT HAL supplementary group, and check the factory initializer proceeds without NVRAM open errors. Record contents remain untouched. |
| HCI host startup | Direct H4 opens successfully and Android emits `event_finish_startup`; `com.android.bluetooth` then aborts in `parse_read_local_extended_features_response` (`stream != NULL`, lines 4723–4732). The same startup thread logs return status `0x30` (Parameter Out Of Range, lines 5204–5206). R5 `libbluetooth.so` callsite `0x91066` in the tombstone disassembles to the later-page loop, ruling out page 0. The exact later page number was not captured. | Capture the raw `0x1004` command/completion to distinguish page 1 from page 2. Verify successful classic discovery and pairing, and confirm any exposed LE and encryption-key-size support from the actual bitmap. |
| Settings | `ConnectedDeviceDashboardFragment` creates `UsbBackend`; the absent `UsbManager` crashes `updatePorts()` at line 231 (captured lines 4866–4873). | Handle the missing USB service independently; verify the connected devices page with Bluetooth OFF and after successful enable. |

Some later restarts are explicit Android `sys.powerctl` requests (e.g. line
6446 reports `reboot,shell`). They must not be counted as spontaneous Bluetooth
or WMT resets. The first earlier abrupt reboot remains unexplained.

## Isolated NVRAM layer: preserve the exact R5 baseline

Run `python3 scripts/btgps/bt/build_r5_nvram_layer.py /path/to/r5/system.img /path/to/new-output`.
The tool pins the R5 input and the two original files, applies the audited
`f6536ab` radio script and adds `nvram` to the actual R5 service declaration.
The R5 ramdisk invokes the radio script from `/system/bin` and contains no
replacement radio script or Bluetooth service declaration. Keep its original
boot and H4 module; no other image file is changed by the tool.

Host checks: regular-file type, uid/gid/mode, SELinux labels, replacement bytes,
and read-only `e2fsck` before/after, plus two byte-identical builds with fixed
filesystem timestamps. The resulting system SHA-256 is
`ce9339b987fdbde73a07985e65b74905877e6381bfd2080b95e5b098fbe41ae3`.
Archive directory: `z1-archive/r7-r5-nvram-layer-20261121/` on the project HDD.
This is an intermediate system plus the original boot, not a full flash package.

## Shared Android source compatibility fixes

The R5 stack is LineageOS 16 rather than generic AOSP Pie. The shipped
`libbluetooth.so` and the pinned LineageOS parser agree on the crash location.
The status is not Controller Busy (`0x3a`) or a BLE white-list reply. Keep the
page-zero feature response mandatory. Validate the length, opcode, status,
requested page and reported limit before using the reply. For a later-page
failure, bound capabilities to the last validated page. If page 1 fails, the
original LE capability gate therefore stays false. If page 2 fails, its
features stay zero. The encryption-key-size safety requirement is unchanged.

Settings now treats an unavailable `UsbManager` as no USB port or functions.
Pie's SystemServer only publishes `UsbService` on systems advertising USB host
or accessory support (or an emulator), so USB absence is valid on this port.
No hardware feature XML, USB host or gadget behavior is changed.

Both source patches are pinned by base/result hashes and apply through
`apply_stock_patches.py --userspace-compat-only` on the mainline build path.
Verified application and postimage hashes from the project's pinned LineageOS
revisions. A host C++ harness exercised real page 0, eligible page 1, invalid
page, out-of-range page count, `0x30`, short error packet, truncated packet,
wrong opcode, and null response; it also checked that acquired replies are
released once. No complete build of the signed Settings or Bluetooth APK
payload has run on this host. The isolated NVRAM image does not include these
source patches, and no full repair image was flashed.
