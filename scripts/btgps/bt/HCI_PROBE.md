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

The reproducible build of the current source is Android ARM API 28, depends
only on `libc.so` and `libdl.so`, and has SHA256
`5f9ef202b68e8b77924435c17ec54aa0a19399d28b523b4e02e69203591c9f36`.
It adds the `--writev` mode used by the next transport comparison. Existing
packages that pin `b0f783c0...` contain the older probe and cannot exercise
that mode.

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

To inspect the local extended-feature pages that Android 9 requests during
controller start-up, use:

```sh
/data/local/tmp/z1_bt_hci_probe --exclusive-controller --features
```

This keeps the same exclusive-controller requirement and prints
`FEATURE requested=... page=... max_page=... status=... features=...` for
pages 0 through 3. It also reads the supported-commands bitmap and reports
Read Encryption Key Size (required by Android 9) and Read Local Supported
Codecs. The probe never prints the device address. A non-zero status is
reported for diagnosis. The default
probe behavior is unchanged when `--features` is omitted.

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

Use `--writev` to match Android H4's two iovecs (packet type and command).
Keep the default single `write` as the factory-path control. Run the two
variants from equivalent radio-on cold boots with no other opener; retain
both WMT and STP traces. A short write fails the probe instead of sending a
second STP frame. The `OPEN` line records the selected transport.

## Cold-boot transport gate

The host wrapper pushes the pinned probe and an on-device gate over the
existing serial deployment channel. It requires `Z1LOG` to name the active
append-only UART capture:

```sh
Z1LOG=/path/to/active-capture.log \
  z1a9_gpt/scripts/btgps/bt/z1_bt_transport_host.sh write

# Power-cycle the Z1, wait for boot completion, then run:
Z1LOG=/path/to/active-capture.log \
  z1a9_gpt/scripts/btgps/bt/z1_bt_transport_host.sh writev
```

Run each command after a separate cold boot. The device gate records the
kernel `boot_id` in `/data/local/tmp/z1_bt_transport_boot_state` and refuses
to compare `write` and `writev` on the same boot. It stops the Android
Bluetooth package and `vendor.bluetooth-1-0`, waits until no process still
has `/dev/stpbt` open, and then runs one probe mode. It records the probe,
adapter, `libbluetooth`, STP/WMT/BTIF module hashes and the relevant kernel
log around `HCI Reset` and `Read Local Version Information`.

A pass requires `probe_rc=0`, `HCI_RESET=PASS`, and
`HCI_LOCAL_VERSION=PASS`. The wrapper preserves each run under
`out/r7_bt_transport_<mode>_<timestamp>.log`. A successful raw-H4 result
still proves only the kernel transport path; Android framework enable,
discovery, pairing, reconnect, Wi-Fi coexistence, SCO and A2DP remain
separate gates.
