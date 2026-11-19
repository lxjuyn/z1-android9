# Z1 R4 framework crash and storage A/B, November 19, 2026

R4 still restarts zygote and system_server after the compositor recovers. UART
log `z1_boot_mainline_20261118_230902.log:38029–38061` records recurring
system_server faults in `PackageManagerService$29.onStatusChanged` and the ART
read barrier it calls. Both library and ODEX frames are present. The board was
still running the same kernel session while these faults occurred.

The mounted `/system` file `framework/oat/arm/services.odex` was 20,876,652
bytes on both board and image. Full SHA256 was `53521bef610b9e914598efcb684435d283b9391d064eedaa88a1867653bc9d1c`
on the board and `750554a7f8e6b07fe36b4f22562a282a0eabf602ca7f0fcf7e87f1d6af83dc08`
in the verified R4 image. The 1 MiB comparison in
`z1_boot_mainline_20261118_230902.log:38525` localized discrepancies to MiB
indices 4 and 8. The subsequent 4 KiB comparison in
`z1_boot_mainline_20261118_235944.log:115–158` found five discrepant pages.
All five board hashes equal SHA256 of 4096 zero bytes. Substituting exactly
those zero pages reproduces the full board hash.

| File page | File offset | Image ext4 block |
| ---: | ---: | ---: |
| 1212 | `0x4bc000` | 114932 |
| 2201 | `0x899000` | 115921 |
| 2206 | `0x89e000` | 115926 |
| 2207 | `0x89f000` | 115927 |
| 2208 | `0x8a0000` | 115928 |

`libart.so`, `boot.oat`, `SystemUI.apk` and the previously recovered Gallium
library matched R4 when sampled. A SHA-verified ARM helper performed a real
`O_DIRECT` read of the Gallium block (partition offset `0x320c8000`). It returned
4096 bytes and SHA256 `7aef88f8fa6c493eb2ad625bc5a8d53ee2528c43d17e2674a319ba699c6531e0`,
matching the verified image (`z1_boot_mainline_20261118_230902.log:37596`).
The analogous direct comparison for the five ODEX blocks is pending.
The ARM helper was rebuilt and matched the expected SHA256 `af36ef18…`.
An emulated ARM build differing only in its input path read all 20,480 bytes
directly from the host R4 image and matched the five archived pages byte for
byte. Compare the confirmed board upload against that same 20,480-byte sample
with SHA256 `60bfb8d0911b1f9a3be899507f4e6cf2e36c44fae4d5413221eacbf77279a49a`.

RescueParty reached `FACTORY_RESET` during earlier framework restarts, then
reported `Reboot failed (no permissions?)` (same log, line 34342). The live
`persist.sys.disable_rescue` property was set temporarily to `1` and read back
at lines 35860–35863 to preserve evidence and avoid repeated reset attempts.
Prior values of both rescue properties were empty. Restore the prior behavior
with an empty value for `persist.sys.disable_rescue` after the diagnostic run
and once reset attempts are no longer a concern. The system_server restart
loop continued after setting the property.

Kernel commit `b45b25ff5` adds an opt-in `mtk_sd.z1_disable_emmc_hsq=1` parameter
for non-removable MT6572 eMMC. The two host-verified boot images in
`out/r4_hsq_diagnostic_20261118` use one shared rebuilt zImage and the exact R4
ramdisk. DTB decompilation differs only by the boot argument. Both images fit
the 6 MiB boot partition. The archived artifacts include the prior sources,
build outputs and expected ODEX pages. Use R4 boot SHA `632d790d…` for rollback.

First boot the no-HSQ image with the existing R4 system. Check the kernel
reports `Z1 diagnostic: eMMC Host Software Queue disabled`, then compare cold
and warm hashes for Gallium and services.odex while tracking SurfaceFlinger and
system_server PIDs and fresh crash logs. Re-run with the HSQ control when
needed to isolate the variable. The comparison addresses the Linux storage
path. The source of the original wrong bytes and the board result remain open.

The first board run uses the two existing images in
`out/r4_hsq_diagnostic_20261118/manifest.json`. Bring up the CH340 UART
capture at 921600 before the flash and power-on sequence. Write and read back
only `bootimg`, then disconnect the download cable before normal power-on.
Capture the file hashes and five-page direct read before dropping caches.
The rebuilt helper and expected bytes are under
`bridge_backups/products/products_r4_odex_direct_probe_20261119_203544/`.

A separate kernel change, `b09fdd195`, fixes the recovery order in
`msdc_request_done()`. HSQ finalization can synchronously dispatch the next
request, whose setup clears `host->error`. The driver now recovers the
controller before finalization. The ARM driver and zImage build passed.
The two diagnostic images predate this fix and remain the first A/B pair.
The order fix has no board result yet, and the original five zero pages
have not been attributed to this path.
