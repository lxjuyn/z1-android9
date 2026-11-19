# Z1 R7 combined audio and GPS candidate (2026-11-19)

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`.

The R7 pair combines two independently verified offline candidates:

- the R6 real output audio HAL and runtime identity gate;
- the GPS Android 9 callback adapter, factory MTK HAL, `mnld` init contract,
  and `gpsdrv` compatibility module.

Both layers are additive changes over the R5 kernel and base system. R7 uses
the GPS-modified boot ramdisk because audio module loading and routing reside
in the system image.

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `out/r7_combined_candidate_20261119/boot_hsq_control_r7.img` | 5,984,256 | `b2865e43d8be640ae1e9dccbcbc21af60d656cde1a5b9b233264961ae31a3145` |
| `out/r7_combined_candidate_20261119/system.img` | 943,718,400 | `25753e641a11be75eca316f2fcabe44c21af3cbd270c7a00d99f3d8d4a72dada` |

## Host checks

- The Android 9 linker loads both installed copies of the output audio HAL and
  negotiates a 44.1 kHz stereo request to 48 kHz stereo PCM16 without opening
  a PCM device.
- The 12-case audio HAL suite passes, including late sound-card recovery,
  stream lifecycle, software volume fallback, error pacing, policy closure,
  and explicit unsupported capture.
- The GPS adapter maps the factory callback order and the actual factory HAL
  dependency closure loads under ARM qemu without initializing `mnld`.
- The GPS adapter, factory HAL, and `gpsdrv.ko` were read back from the system
  image with matching hashes, `0644 root:root`, and the expected SELinux
  labels.
- The boot ramdisk contains exactly one `mnld` service declaration, the
  `gps:system` socket contract, the patched prep script, and the expected
  `initrd-end`.
- `e2fsck -fn` passes.

## Board sequence

Flash the boot and system together. After boot, start the read-only identity
gate:

```sh
/system/bin/z1_runtime_manifest --r7
```

Then test audio independently:

```sh
cat /proc/asound/cards
dumpsys media.audio_flinger
dmesg | grep -i -E 'z1audio|mt6323|mt6572-mt6323|pcm'
tinymix
```

Play a known PCM signal through Android. Require audible physical-speaker
output and an observable response to volume changes.

Test GPS independently:

```sh
getprop init.svc.mnld
ls -l /dev/socket/mnld /dev/stpgps
ls /sys/class/gpsdrv/gps
dmesg | grep -E '\[z1gps\]|mt3326|GPS'
```

`mnld` must remain running after the eight-second stabilization window. With
networking off and an outdoor sky view, require satellite status, NMEA data,
and a valid repeatable location callback.

## Boundaries

The archived R7 adapter exposes raw factory GNSS extension tables. Source
now withholds those tables pending ABI validation. Treat R7 GPS as an
experimental bring-up artifact and rebuild the adapter before testing GPS.

Audio capture remains unimplemented. The input stream deliberately returns
`-ENOSYS`, and the audio policy exposes no capture device. A microphone test
therefore belongs to the next input-audio stage and must not be used to reject
the R7 output path.

Bluetooth, Wi-Fi, GPU 3D, storage stability, backlight blanking, physical key
mapping, and touch navigation boundaries retain their existing board gates.
The read-only manifest covers their file and basic runtime identities but
cannot replace physical tests.
