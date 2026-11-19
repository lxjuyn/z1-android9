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
