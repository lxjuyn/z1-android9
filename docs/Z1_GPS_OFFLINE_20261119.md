# Z1 offline GPS bring-up candidate

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`

The R5 system image shipped a `gps.default.so` placeholder that deliberately
did not touch the GPS hardware. The factory MTK HAL is available, but passing
an Android 9 callback table to it directly is unsafe: its thread callback is at
byte offset `0x24`, while Android 9 puts `requestUtcTimeCb` there. The resulting
call executes the wrong callback and never starts the factory GPS worker.

The new adapter preserves the factory data path:

```text
LocationManager
  -> android.hardware.gnss@1.0
  -> gps.default.so (Android 9 ABI adapter)
  -> gps.z1stock.so (factory Android 4.4 HAL)
  -> mnld socket
  -> libmnlp_mt6572
  -> /dev/stpgps
  -> WMT GPS hardware
```

It forwards location, status, SV, NMEA, wakelock, time, start, stop, cleanup,
position mode, aiding-data, and location injection. It does not generate a
synthetic location. Raw factory extension tables are withheld until each
payload and lifetime contract has its own ABI adapter; assisted GPS is
therefore unavailable in this candidate.

## Candidate

The local pair is:

```text
out/z1_gps_offline_candidate_20261119/boot.img
out/z1_gps_offline_candidate_20261119/system.img
```

The exact hashes and build record are in that directory's `manifest.json`,
`SHA256SUMS`, and `PACKAGE_NOTE.md`. The final image hash must be taken from
the generated manifest because `debugfs` rewrites inode checksums when the
system image is regenerated.

The boot candidate preserves the R5 zImage and DTB and changes only:

```text
init.z1.network.rc
z1_gps_prep.sh
```

The init service is named `mnld`, matching the factory HAL's
`init.svc.mnld` and `ctl.start mnld` contract. Its socket is
`0660 gps:system` so the gnss service, which runs as user `gps`, can connect.
The prep script retains the eight-second service/socket/STP stabilization
gate. It remains one-shot during board bring-up to prevent a restart storm.

The system candidate preserves the R5 system baseline and changes only:

```text
/vendor/lib/hw/gps.default.so       Android 9 ABI adapter
/vendor/lib/hw/gps.z1stock.so       factory HAL with unique SONAME
/lib/modules/z1-radio/z1_gpsdrv.ko  gpsdrv sysfs compatibility module
```

The factory HAL is renamed at the ELF SONAME level. Keeping its original SONAME
would let the Android linker return the already-loaded adapter as if it were
the factory library.

## Verification

The following host checks pass:

```sh
scripts/btgps/gps/abi_adapter/test/run_arm_qemu.sh
scripts/btgps/gps/abi_adapter/test/run_actual_hal_qemu.sh \
  ../../out/z1_gps_offline_candidate_20261119/system.img
python3 -B scripts/btgps/gps/contract_tests/test_gps_binary_contract.py
python3 -B scripts/btgps/gps/test_gps_scripts.py
python3 -B scripts/btgps/gps/test_init_rc_patch.py
```

The ARM qemu tests prove that the adapter maps Android 9 callback slots to the
factory layout and that the actual factory ELF dependency closure loads. The
package builder independently reads back every system payload and runs
`e2fsck -fn`. These checks do not prove a hardware satellite fix.

## Board gates

After flashing the boot/system pair together:

```sh
getprop init.svc.mnld
ls -l /dev/socket/mnld /dev/stpgps
ls /sys/class/gpsdrv/gps
dmesg | grep -E '\[z1gps\]|mt3326|GPS'
```

`mnld` must remain running beyond eight seconds. The socket must retain owner
`gps`, `/dev/stpgps` must remain present, and dmesg/logcat must show factory
HAL initialization rather than the `Z1 GPS stub` path.

Enable location outdoors with networking off. Offline GPS is accepted only
after the framework receives:

```text
satellite status
NMEA sentences
a valid, repeatable location callback
```

A loaded module, a running daemon, a visible satellite, or a status callback
is not a completed fix.

## Rollback

Use the R5 control pair recorded in the candidate manifest:

```text
boot:   out/r5_offline_candidate_20261119/boot_hsq_control_r5.img
system: out/r5_offline_candidate_20261119/system.img
```

The factory boot image remains `1/boot.img`.
