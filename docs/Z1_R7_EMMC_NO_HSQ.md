# Z1 R7 eMMC read diagnostic

On the November 19 run, the R4 boot finished, touch reported complete swipes, and SurfaceFlinger later faulted in `Layer::commitChildList()`. Its replacement started the boot animation during the same kernel boot. After the animation, `system_server` repeatedly faulted at `android.hardware.tv.cec@1.0.so` PC `0x177ee`, with fault address `0x50000008`.

The running R4 file read for the CEC library returned SHA256 `8b8a450a4bee04040cfddceb59fd46d70ee5ce902f63e118a4d4007e4badf8ba`. The verified R4 system image returns `f09271b0d250292088c5222a61fa80c7d20aa2f6cd94ced14ebac5dda2de47fd`. Only file page 22 of 35 differed. Its SHA256 was `27b1500848a034a6cd818ff8b26ec03ba96cf275a7dee3517347578918664240` and replacing the canonical page with those bytes reproduced the full board hash. It maps to ext4 block 14291. The first 4,068 bytes of the differing page match an unrelated image region beginning at `0x2e79901c`. The other 28 bytes differ. All five previously discrepant `services.odex` pages returned the canonical bytes in this boot, though the full file hash still differed. The full `libsurfaceflinger.so` hash matched R4 in the same session.

The UART evidence is in `z1_boot_mainline_20261119_233106.log` around lines 11206 and `z1_boot_mainline_20261119_234452.log` around lines 1167, 2431, 3223 and 3548. The UART console has UID 2000. The raw block device is mode `0600`, so the live console only supplied filesystem reads. The fault can still be in the MMC read path, caching or memory. The observations do not establish which caused the bad page.

The package is `out/r7_speaker_no_hsq_candidate/`. Its boot image retains the R7 speaker image's exact ARM kernel and MTK ramdisk. The kernel already includes `b09fdd195` for error recovery ordering and `b45b25ff5` for the MT6572 eMMC queue diagnostic. The boot header and the appended DTB each add `mtk_sd.z1_disable_emmc_hsq=1` to their respective original command lines. The system image is the byte-identical R7 speaker system. Only `bootimg` and `android` are enabled in the scatter.

| Payload | Bytes | SHA256 |
| --- | ---: | --- |
| `boot.img` | 5,984,256 | `4fde12b18f2f0ee7423d63a0165847a0075f2c67148100e77c09d01de08b33bd` |
| `system.img` | 943,718,400 | `21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920` |

The split image passed the MTK header and 6 MiB checks. The DTB differs from the speaker candidate only in bootargs. Its initrd range matches the verified gzip body and the speaker GPIO remains active-high GPIO140. `e2fsck -fn` passed on the paired system. After deployment, look for `Z1 diagnostic: eMMC Host Software Queue disabled`. Repeat hashes for the CEC, ODEX and Gallium files, watch whether SurfaceFlinger and `system_server` PIDs stay stable, and verify physical speaker output and display blanking. Use a privileged diagnostic boot to compare raw eMMC reads with file reads if corruption returns. The queue setting and R7 fixes still need board verification.

## November 20 board result

SP Flash Tool reported a successful download of the no-HSQ boot and the
byte-identical R7 speaker system. That flash log does not include a
post-write partition readback.
`z1_boot_mainline_20261120_000340.log` records image SHA
`4fde12b18f2f0ee7423d63a0165847a0075f2c67148100e77c09d01de08b33bd`
and begins at uptime 11.819 seconds. `/proc/cmdline` contains
`mtk_sd.z1_disable_emmc_hsq=1`; the runtime module parameter is `Y`, and
the normal boot did not print `Host Software Queue enabled`.

Independent extraction confirms that the exact control pair is
`out/r7_speaker_gpio_candidate_20261119/boot.img` SHA
`e761299886d61bd5cfca6ff0876cf36d0355a3216c7cbb050a9cf08016f56a1d`.
The two boot images contain the same zImage
`b6b5174e8f394a70e8bde22bf2ff472a60aeb500d31ed1088090a0abb654c63e`
and MTK ROOTFS body
`fed8a6d7b8e1a2fc3b2d3b0da750e6066132923d4d12045ac3d56308bd3037bf`.
Their decompiled DTBs differ only by the HSQ-disable boot argument. The
paired system image is SHA
`21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920`
for both runs.

The no-HSQ boot reached `sys.boot_completed=1` at uptime 249.201 seconds.
At uptime 369.8 seconds, the full file hash of
`/system/framework/oat/arm/services.odex` still matched the image:
`750554a7f8e6b07fe36b4f22562a282a0eabf602ca7f0fcf7e87f1d6af83dc08`.
The capture covered uptime 896 seconds with zero `SIGSEGV`, EXT4 errors,
or generic I/O errors. This crosses the roughly 544-second point at which
the earlier SurfaceFlinger corruption appeared.

That was not a clean framework stability run. Android's watchdog killed
zygote five times in the same boot while
`GnssLocationProvider.native_init()` was blocked. The captured kernel
stack also briefly showed `mmc_blk_rw_wait`, although no MMC timeout or
data error followed. The GNSS restarts are an independent system image
problem and obscure PID-stability checks. The boot also found `/data`
dirty and ran `e2fsck`, which repaired inode and block accounting before
Android started.

The result proves that the opt-in probe selects the normal request path on
the exact A/B pair, and provides preliminary evidence that the old
page-cache failure did not recur within 896 seconds. It does not prove
that HSQ caused the original corruption. The no-HSQ run needs a matching
long HSQ-control run with the same system, repeated cold boots, cached and
raw-block readbacks, and no GNSS watchdog restarts before that causal
claim can be made.

Static review found the `b45b25ff5` gate correctly scoped to the
non-removable, non-SDIO MT6572 eMMC host. `b09fdd195` moves controller
recovery ahead of HSQ finalization and no obvious ordering defect was
found, but this boot disabled HSQ and exercised no MMC command or data
error. Its claimed race fix therefore remains compile-tested rather than
board-validated, and it should not be used to narrow the original
corruption root cause until the HSQ control path is run.

Keep HSQ enabled by default. Retain
`mtk_sd.z1_disable_emmc_hsq=1` as a diagnostic switch, and do not promote
the no-HSQ path as the permanent storage fix from this one run.
