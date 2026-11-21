# Z1 R7 R3 Bluetooth board check

Status: board-tested on 2026-11-21. This report records the installed R3
BTIF/25 MHz system. It does not test the later direct-H4 or writev packages.

The tested boot is
`5baddbade8fb3b50e53220370d1eb11f1690a428af2690869e1e44c7e4e8441b`.
Runtime component readback identified the R3 base system:

| Component | SHA256 or build identity |
| --- | --- |
| `mtk_btif_drv.ko` | `a72b21df2173f210b04dc33583950ae0553c20de06366a877ca568f612724540` |
| loaded BTIF build ID | `e6b179511d0f297199d2f4dd204199b4dcb89c19` |
| `mtk_stp_bt_soc.ko` | `01fbd5706e9c74dc9759d3da60da5e3a5443c5c71c12c0dc1a1464495e45f63e` |
| `libbt-vendor.so` | `4394419927598342982a0960f3d3cf954a991e167866155ec4a86fc6c4cd4665` |

The direct-H4 adapter and writev-capable STP module were absent.

At a completed boot, the combo ID was `0x6572`, the Bluetooth HAL was running,
and the Bluetooth manager existed. However `nvram_init` was empty,
`sys.z1.radio.bt_nvram_ready=0`, and Bluetooth repeatedly failed.

The first enable reproduced the R3 WMT failure: RX timeout followed by
`there is no sync pkt in BTIF buffer` and a whole-chip reset. Kernel
`BT_open` later reached WMT and STP, but that alone is not userspace
initialization.

The two factory AP records are present as storage records:

| Record | Runtime size | Mode/owner | Payload expected by `NVM_GetFileDesc()` |
| --- | ---: | --- | ---: |
| `BT_Addr` | 66 | `0660 9997:9997` | 64 |
| `WIFI` | 514 | `0660 9997:9997` | 512 |

The two trailing bytes are the MTK storage checksum. The current init gate
compares the complete storage size with the payload size and therefore refuses
to publish `nvram_init=Ready`.

For a reversible single-variable diagnostic, `nvram_init` was temporarily set
to `Ready` and Bluetooth was enabled once. The factory library then reached
`NVM_GetFileDesc()`, but failed to open `BT_Addr` with errno 13. It issued
vendor HCI opcodes `0xfc1a`, `0xfc79`, `0xfc93`, `0xfc7a` and Reset
`0x0c03`, then timed out after 2000 ms waiting for the Reset completion.

This separates three issues:

1. The storage-versus-payload size gate blocks the factory library.
2. The HAL lacks permission to read the runtime NVRAM records.
3. No HCI Reset reply followed on this already retried boot.

The third result is not a clean transport verdict because the boot had already
undergone repeated open/close cycles and a whole-chip reset. The temporary
property was removed; final state was `nvram_init` empty, `bluetooth_on=0`,
and manager `enabled: false`, `state: OFF`.

R3's BTIF pointer fix is loaded, but it did not prevent the initial WMT RX
timeout. A reboot is required before testing a new HCI transport. A durable
NVRAM fix must validate the 66/514-byte storage records and checksums and make
the records readable by the HAL. Publishing `Ready` alone is diagnostic only.
Pairing, reconnect, Wi-Fi coexistence and A2DP remain untested.

Evidence:

- `mainline_recon/z1_boot_mainline_20261121_214741.log` lines 7056-7148 and
  7191-7626.
- `mainline_recon/z1_boot_mainline_20261121_221247.log` lines 46-56.
- `mainline_recon/z1_boot_mainline_20261121_221425.log` lines 144-154.
- `mainline_recon/z1_boot_mainline_20261121_222143.log` lines 50-55,
  1064-1101 and 1666-1673.

Log SHA256 values are recorded in
`mainline_recon/R3_BT_CHECK_20261121.md`.
