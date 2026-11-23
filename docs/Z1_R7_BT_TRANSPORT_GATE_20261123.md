# R7 Bluetooth transport gate - 2026-11-23

Status: `HOST_VERIFIED_BOARD_BLOCKED_NO_MEDIA`.

## Host result

The opt-in direct-H4 selector and the exclusive raw transport gate are
implemented in commits `450e4e8` and `0d1d2d4`. The gate refuses to compare
single-buffer `write` with Android's two-iovec `writev` on the same kernel
`boot_id`, stops Android Bluetooth and `vendor.bluetooth-1-0`, and requires
no remaining `/dev/stpbt` opener before sending Reset and Read Local Version.

Host verification passed:

- pinned probe rebuild: 1 test;
- `.write_iter`, feature-page cap, module and package checks: 6 tests;
- vendor adapter factory/direct selection: 10 cases;
- new transport gate: 4 tests;
- shell syntax, host dry-run, `git diff --check`, package SHA readback.

Packages:

```text
out/r7_bt_transport_gate_20261123/
bridge_backups/products/products_r7_bt_transport_gate_20261123_155134/

out/r7_bt_transport_board_20261123/
bridge_backups/products/products_r7_bt_transport_board_20261123_155426/
```

The board package contains:

| File | SHA256 |
| --- | --- |
| candidate `boot.img` | `98aa0d0aed409feacee34d75fa26cd1ecd0b90378e41e8a048f6349b8f97c046` |
| radio-off rollback | `0840aee45d58f56230cc294fb4c97d0365349eae90cff4c6382600b819629260` |
| radio-on rollback | `472075ae9c3e74012ab0147469390b817d3b88b8781bbbf5b3c1c6f1b0254791` |
| pinned H4 probe | `5f9ef202b68e8b77924435c17ec54aa0a19399d28b523b4e02e69203591c9f36` |

The candidate boot is 5,986,304 bytes and `tools/validate_mtk_boot.py`
returns `VALID`. Its ramdisk is 1,644,084 bytes, its
`/z1_libbt-vendor.so` hashes to `3b5eb59f...`, and its DTB
`linux,initrd-end` is `0x84291434`, exactly matching the MTK ROOTFS payload.
The paired system remains
`f48322725f2f5f53d2d4b3c3b9d92379e0aea715b9d3b04ada03755381843cab`.
Read-only extraction confirms page-capped `libbluetooth.so`
`86f6c3f0...`, writev STP BT module `af2f8497...`, WMT module `c9c62c26...`,
BTIF module `a72b21df...`, and radio initializer `718cb404...`.

## Physical blocker

No boot or system partition was written in this round. At 2026-11-23
15:39-15:55 the host USB inventory contained only the CH340 UART adapter
`1a86:7523`; no MediaTek Preloader/BROM device was present. The active UART
capture `z1_boot_mainline_20261123_153919.log` contains only its 453-byte
metadata header and no device payload.

The board action requires the Z1 in Preloader/BROM mode on a direct rear USB
port. The prepared command is:

```sh
tools/flash_with_retry.sh bootimg \
  out/r7_bt_transport_board_20261123/boot.img \
  out/r7_bt_transport_board_20261123/rollback_radio_off.img \
  8
```

After a stable radio-enabled boot with Wi-Fi off:

```sh
Z1LOG=/path/to/active-capture.log \
  z1a9_gpt/scripts/btgps/bt/z1_bt_transport_host.sh write

# Power-cycle, wait for boot completion, then:
Z1LOG=/path/to/active-capture.log \
  z1a9_gpt/scripts/btgps/bt/z1_bt_transport_host.sh writev
```

## Acceptance boundary

Each mode must return `probe_rc=0`, `HCI_RESET=PASS`, and
`HCI_LOCAL_VERSION=PASS`. The two logs must carry different `boot_id` values.
This closes only the kernel H4 framing and read path. Android HCI startup,
controller feature negotiation, adapter ON, discovery, pairing, reconnect,
Wi-Fi coexistence, SCO and A2DP remain separate gates.
