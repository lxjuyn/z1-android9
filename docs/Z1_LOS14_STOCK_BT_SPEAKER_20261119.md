# Z1 LOS 14.1 and factory Bluetooth/speaker comparison

`los14/system/build.prop` identifies Android SDK 25, device Z1 and board
MT6572. Stock userspace came directly from `1/system.img` via read-only
`debugfs`; stock kernel symbols came from the raw image recovered from
`1/boot.img`. The LOS 14.1 ramdisk was extracted from `los14/boot.img`.

## Bluetooth

The LOS and stock MediaTek libraries have different ELF hashes, but their
`bt_read_nvram` disassembly at `0xab8` matches byte for byte as decoded.
Both wait for `nvram_init=Ready` for up to ten iterations and then expect
one 64-byte BT record from `NVM_GetFileDesc` (LID 1). The `/data/BT_Addr`
fallback also requests 64 bytes and is only reached after the readiness gate
and a failed NVRAM open. An ASCII address and a copied LOS adapter do not
satisfy those contracts. The LOS boot ramdisk declares the root `nvram_daemon`
with groups `nvram system` and includes an explicit start in its framework
restart action; stock declares the same daemon as user `nvram`. R7 has no
such init service and its failed attempt is recorded at lines 231021–231038
of `mainline_recon/z1_boot_r14_verify.log`. The LOS vendor interface object
is 20 bytes versus the 16-byte Android 9 interface built for R7.

Next: run the isolated H4 probe documented in `scripts/btgps/bt/HCI_PROBE.md`.
A working H4 transport still requires validation of controller configuration,
address, scanning, pairing and A2DP. If factory GORM is required, retain its
initialization with a verified board-specific BT record and a scoped NVRAM
compatibility path.

## Bluetooth follow-up: R7 initialization gates

The R7 boot evidence at
`mainline_recon/z1_boot_r14_verify.log:231037` reaches
`bt_read_nvram: Get NVRAM restore ready fails!`, followed by
`mtk: Read Nvram data fails` and an HCI initialization failure. A later
attempt reaches `BT_open` and WMT BT power-on, then still fails after the
factory library polls `persist.mtk.wcn.combo.chipid`.

The stock Z1 ramdisk sets that property to `0x6572` in
`init.charging.rc:610` and `meta_init.rc:286`; R7 did not. Disassembly of
`libbluetooth_mtk.so` shows `bt_get_combo_id()` requiring the property before
`GORM_Init()`. The Bluetooth-only candidate now sets and verifies the property
after exact Z1 DT validation.

R7 also lacks the Android 4.4 `nvram_daemon`. The candidate therefore
publishes `nvram_init=Ready` only when the existing per-device
`/data/nvram/APCFG/APRDEB/BT_Addr` and `WIFI` records have their expected
64-byte and 512-byte shapes. It never copies, prints, or rewrites the records.
`sys.z1.radio.bt_nvram_ready` records whether this gate passed.

The isolated H4 probe is installed in the candidate. Board validation order
is: confirm both NVRAM gate properties, run the passive Bluetooth diagnostic,
release the framework opener, run the exclusive HCI Reset and Read Local
Version probe, then test framework enable, discovery, pairing, reconnect and
A2DP. These remain unverified until the candidate runs on the device.

## Speaker

Both legacy audio HALs route through `/dev/eac`. Their
`AudioMachineDevice::AnalogOpen` functions at stock `0x61374` and LOS
`0x61318` issue the same `0x400443a6`, `0x400443a4` and `0x400443a1`
requests in corresponding branches. The stock kernel's `AudDrv_ioctl`
at `0xc05040f4` reaches `Sound_Speaker_Turnon` through `0xc05043c0` and
`Sound_Speaker_Turnoff` through `0xc0504734`. `Speaker_Init` (`0xc0487214`)
selects GPIO mux mode for GPIO140. The on function (`0xc048729c`) sets it
as output, drives it high and sleeps 10 ms; the off function (`0xc04872e8`)
drives it low. This supersedes the earlier conclusion based on a string search
that the Z1 speaker had no board enable GPIO.

Kernel `61f30dde1` wires the GPIO to the machine driver's Speaker DAPM
endpoint. The GPIO starts low, goes high after the codec path powers on and
goes low before codec teardown. The current R7 playback HAL and mixer route
remain in this candidate. ARM module build, vermagic, import providers,
module readback, SELinux label and offline `e2fsck -fn` were checked.

Use the paired `out/r7_speaker_gpio_candidate_20261119/boot.img` and
`system.img` after preserving the R4 storage evidence. The boot keeps the
R7 HWC blank fix, kernel and ramdisk; the only DT change from the HWC blank
candidate is `/sound/speaker-enable-gpios`. The system replaces the machine
module at `/lib/modules/mt6572-mt6323.ko`. SHA256:

- Boot: `e761299886d61bd5cfca6ff0876cf36d0355a3216c7cbb050a9cf08016f56a1d`
- System: `21adb51ecb11564fcd65f0f3700469d65b0877e55a7c8fec2d5eec6214571920`
- Machine module: `23977c4fdcf139ca879161dec63add016c304d77e8a3b47569db868755716dd3`

Confirm on device that the new machine module is active, GPIO140 is owned
by the sound card and changes with speaker DAPM, and low-volume audio is
physically audible. Then exercise standby/wake, volume and wired headset
routing. No physical testing or flashing was performed for this candidate.

## First board pass

Before replacing system, preserve the five R4 ODEX pages and capture the HSQ
comparison described in [the R4 readback notes](Z1_R4_FRAMEWORK_READBACK_20261119.md).
Start the CH340 capture at 921600 before flashing or starting the board.
From the workspace root, use the matching offline preflight:

```sh
bash z1a9_gpt/scripts/z1deploy/z1_flash_r7.sh --speaker-gpio
```

On the speaker candidate, from the board shell, check all of the following:

```sh
/system/bin/z1_runtime_manifest --r7
sha256sum /system/lib/modules/mt6572-mt6323.ko
od -An -tx1 /proc/device-tree/sound/speaker-enable-gpios
cat /proc/asound/cards
```

The stock R7 manifest does not identify this variant. Match the machine
module SHA above and expect a 12-byte property with big-endian cells for
GPIO `140` (`00 00 00 8c`) and the active-high flag (`00 00 00 00`) after
its phandle. Both checks are needed. The two tested boot ramdisks contain no
copy of `mt6572-mt6323.ko`; `init.z1.rc` starts the system module loader,
which loads it from `/system/lib/modules`.

At low media volume, confirm the physical speaker is audible. If it stays
silent, compare the Speaker DAPM state and GPIO140 state in debugfs before
playback, during playback, and after standby. Also read
`/sys/class/leds/lcd-backlight/display_blank` across screen lock and wake;
expect `0 → 1 → 0` and confirm that the panel actually goes dark and shows
fresh frames after wake.
