# Z1 Bluetooth controller probe

The R7 vendor library uses the factory NVRAM and GORM initialization path.
`z1_boot_r14_verify.log` at lines 231021–231038 records `/dev/stpbt` open
followed by NVRAM readiness failure and a missing HCI descriptor. The exact
radio source recorded in `docs/Z1_WIFI_SOURCE_REPRO_20261119.md` implements
`BT_open`, `BT_read`, `BT_write`, and `BT_poll` in
`conn_soc/linux/pub/stp_chrdev_bt.c`. Its open requests WMT Bluetooth power
and its read/write forward STP Bluetooth payloads. The factory binary's
`BT_SendHciCommand` at `0x12ba` prepends packet type `0x01` and sends the
whole command together. The probe exercises that same framing directly.

From the workspace root, build and archive the probe with the pinned bundled
Android NDK:

```sh
z1a9_gpt/scripts/btgps/bt/build_probe.sh /tmp/z1-bt-probe-output
```

The current verified build is installed as `/system/bin/z1_bt_hci_probe` in
the `r7-bt-20261120` system candidate. It is Android ARM API 28, depends only
on `libc.so` and `libdl.so`, and has SHA256
`b0f783c0f295e183ec78454a4b410d7cc33fdb331f3e434384e443389096940e`.

On the Z1, disable Android Bluetooth and confirm the Bluetooth HAL has
released `/dev/stpbt` before running the probe. Opening and closing the node
changes controller power, and HCI Reset clears controller state. Copy the
binary into `/data/local/tmp` and run with a shell allowed to open the node:

```sh
/data/local/tmp/z1_bt_hci_probe --exclusive-controller
```

If open fails, correlate the error with WMT and STP logs. If Reset or Read
Local Version fails, retain the failed opcode and the corresponding kernel
logs. If both return `PASS`, the transport and these commands work without
factory GORM initialization. Scan, device address provisioning, pairing and
audio still need their own validation before selecting a replacement backend.

Run the bounded framework diagnostic first:

```sh
sh /system/bin/z1diag/z1_diag_bt.sh
```

Its facts include `nvram_init`, `sys.z1.radio.bt_nvram_ready`,
`persist.mtk.wcn.combo.chipid`, `/dev/stpbt`, and the first framework failure.
The factory library needs both NVRAM readiness and combo ID `0x6572`; a live
`/dev/stpbt` by itself is not sufficient.

The ARM API 28 executable ran under QEMU with the R7 linker and libraries,
including the missing-device and required-argument paths. Host socket fixtures
covered both commands, fragmented events, rejection status, malformed command
completion and timeout. Controller results require the board.
