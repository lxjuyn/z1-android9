# Z1 Settings null-USB compatibility package

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`

This package fixes the Android Settings crash captured in
`mainline_recon/z1_boot_mainline_20261121_225400.log` (stack start line 4853,
`UsbBackend.updatePorts()` line 4867):

```text
java.lang.RuntimeException: Unable to start activity
ComponentInfo{com.android.settings/com.android.settings.Settings$ConnectedDeviceDashboardActivity}
java.lang.NullPointerException: Attempt to invoke virtual method
'android.hardware.usb.UsbPort[] android.hardware.usb.UsbManager.getPorts()'
on a null object reference
```

`UsbManager` is null because Pie's `SystemServer` does not publish
`UsbService` unless the product advertises USB host/accessory support. The
Settings source patch therefore treats an unavailable manager as no USB ports
or functions. The source change and its base/result hashes are in commit
`c19a954476f5f6a8eb4a41e592408b0858d6e301`.

The patch was rechecked against the pinned LineageOS Settings revision
`4ea4fd761402587c4da2273652552368c8f0a0d7`. The input SHA256 is
`8d93920c963e3e6dc37870de1cf2c362ce8becd6328bdb9286002d7cabce4ecf`;
applying the stored patch produces the manifest's exact output SHA256
`433ea84b76ae8c1a53264c9fc7d21115f2ce55011cb54375bcf2c698931ff5d5`.

## Artifacts

The package retains the last host-verified R7 boot and uses that R7 system as
the base. Only `/priv-app/Settings/Settings.apk` differs.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `boot.img` | 5,986,304 | `0d19730f915bece3e2dd1110baeadcfd8a03ca58f7e371dfd3cb2492c6903d18` |
| `system.img` | 943,718,400 | `2a435e733e791bd9b00f47b12679717325a15d88127a6d3f9af5ee588ac64e97` |
| `Settings.apk` | 49,832,908 | `2f742c0eac5b06ba1c9798a12ac2a9b27b689e4d20ccb29879d62527900e4240` |
| `system_from_r7.img.z1delta` | 49,917,471 | `bddf62a188afd860ed0cde15c56de81c645dc815c884c03183c57b32ff79cde0` |

Base system SHA256:
`a3fc264d50e9b8e9a8024622637517b70d031cb1d5fe985e3da50b04ce982565`.

The delta changes 12,169 4-KiB blocks. Replaying it from the pinned base
produced the exact `system.img` SHA256 above.

## Verification

- The APK keeps the original AOSP platform certificate:
  `C8:A2:E9:BC:CF:59:7C:2F:B6:DC:66:BE:E2:93:FC:13:F2:FC:47:EC:77:BC:6B:2B:0D:52:C1:1F:51:19:2A:B8`.
- `apksigner` verifies v1 for API 1-23, v2 for API 24-27, and v3 for API 28.
- Java class identity is unchanged: package `com.android.settings`, version
  code `28`, version name `9`, target SDK `28`.
- `unzip -t` passes.
- Excluding regenerated signature files, the shipped and repaired APKs differ
  only in `classes.dex`.
- The repaired image returns the APK with matching SHA256, mode `0644`,
  uid/gid `0:0`, and SELinux label `u:object_r:system_file:s0`.
- `e2fsck -fn` passes.
- The full verification transcript is `APK_VERIFICATION.txt`; filesystem and
  replay evidence is in `e2fsck-system.txt` and `delta-replay.json`.

No complete on-device test has run. The device gate is to open the connected
devices page with the USB service absent and confirm that Settings remains
resumed, then repeat after a Bluetooth enable attempt.

## Flash and test

Only the system partition changed. The boot image is included unchanged for a
complete R7 pair.

```bash
cd /home/lxj/claude/6572
pkg=/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/z1-archive/settings-usb-nullfix_20261122_005617
(
  cd "$pkg"
  sha256sum -c SHA256SUMS
  e2fsck -fn system.img
)
tools/z1_flash_chunked.sh android "$pkg/system.img" 48 8
```

The download cable is only for flashing. Boot normally with it disconnected.
After Android starts:

```sh
service check usb
am start -n 'com.android.settings/.Settings$ConnectedDeviceDashboardActivity'
dumpsys activity activities | grep -E 'mResumedActivity|ConnectedDeviceDashboard'
logcat -d -b crash | grep -E 'com.android.settings|UsbBackend|FATAL EXCEPTION'
```

Expected before the fix: `service check usb` is `not found` and the activity
crashes in `UsbBackend.updatePorts()`. Expected after the fix: the activity
remains resumed and the crash buffer has no new Settings/UsbBackend fatal
exception.

Rollback is the base R7 package at
`/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/z1-archive/r7-r7-bt-stack-compat-20261121_234156/`.
