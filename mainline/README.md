# Z1 Linux 7 Android 9 mainline work

This directory records the Linux 7 patch set and the Z1 boot packaging gates. A static package pass is not proof that the device boots.

## Apply the Linux 7 patch set

Apply `patches/0001` and `patches/0002` to the matching kernel source/config. Apply `patches/0003` and then `patches/0004` to that kernel tree. The latter two update `tools/z1_make_bootimg.sh`; neither patch edits a kernel driver.

`0001` removes only `console=tty0` from the Z1 chosen bootargs. It keeps `console=ttyS0,921600n8` and `earlycon=mtk8250,0x11005000`.

`0002` records the Android 9 Linux 7 kernel configuration from revision `c6501138e`. Its captured input SHA-256 is `8b0e88cd7a301d016a2f144710e044c9adb436a6ea8d97b33bf59dd7df9e7747`. It enables Android cgroups, modular FUSE, IPv6, and netfilter; it does not claim audio, Wi-Fi, or Bluetooth support.

`0003` makes the legacy MTK packer reject an invalid ROOTFS header or a DTB initrd range that does not exactly cover the compressed body. It needs Python 3 and `fdtget`.

`0004` changes the legacy packer's default boot-header command line to match the Z1 DTS and checks both it and `/chosen/bootargs`. Each must contain exactly one `console=ttyS0,921600n8` and one `earlycon=mtk8250,0x11005000`; any `console=tty0`, alternate console baud, or conflicting earlycon stops packaging. `Z1_CMDLINE` may still change other arguments, but cannot bypass the serial checks.

## Keep the two boot packaging paths separate

`scripts/build_z1.sh` prepares the LOS environment and runs `m` with the requested targets. `BoardConfig.mk` sets `TARGET_NO_KERNEL := true`: LOS supplies the Android system and ramdisk, while a separate project packer creates the MTK boot image. The build wrapper does not call `z1_add_bootdiag.py`, `z1_make_android9_bootimg.py`, or `z1_verify_linux7_candidate.py`. Both kernel variants share the same LOS output directory, so the wrapper takes a per-checkout lock and the stock/Linux 7 image builds must run one at a time.

The kernel-side `tools/z1_make_bootimg.sh` is a separate legacy Linux/CONNSYS path. Its default input is `out/ramdisk_mainline_connsys.gz`, an MTK ROOTFS busybox ramdisk. The inspected existing copy has a `/dev/ttyGS0` ACM shell/getty, but no `/z1_bootdiag`, `init.z1.rc`, or Android log relay. Its previously built `boot_mainline_connsys.img` also records the old `console=tty0 console=ttyS0,115200n8` header command line. Do not treat this rescue image as an Android 9 candidate; apply `0004` before using the legacy packer again. The patch retains the ramdisk's existing USB ACM shell and does not add or remove ramdisk files.

The Android 9 path uses `scripts/z1_make_android9_bootimg.py`. It takes an MTK ROOTFS ramdisk and the appended-DTB inputs, copies `/chosen/bootargs` into both boot-header command-line fields, and rejects a DTB with tty0 or missing Z1 UART/earlycon. `scripts/z1_add_bootdiag.py` must explicitly transform the LOS gzip-cpio ramdisk with a supplied static ARM32 helper and `--usb-acm --logcat-relay`; it adds the ACM and logcat services, removes the generic `init.usb*.rc` imports and files, and strips the legacy Z1 ADB gadget setup. Android 9 loads persisted USB properties after the early init actions, so setting `persist.sys.usb.config=none` alone is insufficient if generic USB rules remain. The injector, boot packer, and candidate verifier independently reject those imports, files, triggers, ADB services, and gadget owners. USB ACM enumeration and operation still require board testing.

`build_z1.sh` still does not inject the ACM logger or package the Android boot image. The helper source is `tools/z1_android9_bootdiag.c`; build a fresh static ARMv7 hard-float binary with the repository script. The script checks ELF32/ARM/ET_EXEC, hard-float ABI, and absence of `PT_INTERP` or shared-library dependencies, then writes a source/compiler/binary hash manifest beside the ignored output artifact. Linux7 init must invoke only `--acm` and `--logcat`; the helper also contains separate stock-only modes. Pass that manifest to the candidate verifier so it can match the injected ramdisk helper hash and the checked-in C source hash.

The Linux 7 Mesa Android makefiles install `libGLES_mesa` at `system/lib/egl/libGLES_mesa.so` and the Gallium DRI target at `system/lib/dri/gallium_dri.so`. The candidate verifier requires `ro.hardware.egl=mesa`, both installed artifacts, a Lima driver marker in the Gallium module, and includes the Mesa libraries plus `/system/bin/logcat` in the ARM ELF dependency closure. This confirms package contents only; it does not prove Lima rendering or display performance on the Z1 board.

Use this gated sequence with paths from one build:

```sh
REPO=/path/to/z1-android9
LOS_ROOT=/path/to/los16
PRODUCT_OUT="$LOS_ROOT/out/target/product/z1"
KERNEL=/path/to/fork_linux
OUT=/path/to/new-linux7-candidate-dir
Z1_BOOTDIAG="$REPO/out/z1_bootdiag"
Z1_BOOTDIAG_MANIFEST="$Z1_BOOTDIAG.manifest.json"
SYSTEM_RAW="$OUT/system.raw.img"
SYSTEM_TREE="$PRODUCT_OUT/system"

"$REPO/scripts/build_z1_bootdiag.sh" "$Z1_BOOTDIAG"
python3 "$REPO/scripts/z1_add_bootdiag.py" \
  "$PRODUCT_OUT/ramdisk.img" "$Z1_BOOTDIAG" "$OUT/ramdisk-acm.img" \
  --usb-acm --logcat-relay
python3 "$REPO/scripts/z1_prepare_android9_ramdisk.py" \
  "$OUT/ramdisk-acm.img" "$OUT/mtk-inputs" \
  --dtb "$KERNEL/arch/arm/boot/dts/mediatek/mt6572-z1.dtb"
python3 "$REPO/scripts/z1_make_android9_bootimg.py" \
  "$OUT/mtk-inputs/ramdisk-mtk.img" "$OUT/boot.img" \
  --zimage "$KERNEL/arch/arm/boot/zImage" \
  --dtb "$OUT/mtk-inputs/mt6572-z1.dtb"
"$LOS_ROOT/out/host/linux-x86/bin/simg2img" "$PRODUCT_OUT/system.img" "$SYSTEM_RAW"
python3 "$REPO/scripts/z1_verify_linux7_candidate.py" \
  --boot "$OUT/boot.img" --system "$SYSTEM_RAW" \
  --system-tree "$SYSTEM_TREE" \
  --zimage "$KERNEL/arch/arm/boot/zImage" \
  --dtb "$OUT/mtk-inputs/mt6572-z1.dtb" \
  --ramdisk "$OUT/mtk-inputs/ramdisk-mtk.img" \
  --module-manifest "$REPO/network/modules.json" \
  --helper-manifest "$Z1_BOOTDIAG_MANIFEST" \
  --report "$OUT/verification.json"
```

Only `STATIC_ARTIFACT_PASS` from the final verifier establishes that this exact ramdisk contains the manifest-matched executable helper, ACM/logger services, expected boot arguments, and the required system files. It does not establish successful board boot, USB ACM operation, Lima GPU rendering, or working hardware.

Use the `ramdisk.img`, sparse `system.img`, and `system/` staging tree from the same LOS build. The verifier consumes a raw ext4 conversion of `system.img` and checks it against that staging tree. Do not substitute the separate legacy BusyBox ramdisk or a stale helper binary.
