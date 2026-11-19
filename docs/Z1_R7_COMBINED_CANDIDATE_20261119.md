# Z1 R7 combined audio and GPS candidate (2026-11-19)

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`.

The R7 pair combines two independently verified offline candidates:

- the R6/R8 real playback and AWB microphone HAL plus runtime identity gate;
- the GPS Android 9 callback adapter, factory MTK HAL, `mnld` init contract,
  and `gpsdrv` compatibility module.

Both layers are additive changes over the R5 kernel and base system. R7 uses
the GPS-modified boot ramdisk because audio module loading and routing reside
in the system image.

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `out/r7_combined_candidate_20261119/boot_hsq_control_r7.img` | 5,984,256 | `b2865e43d8be640ae1e9dccbcbc21af60d656cde1a5b9b233264961ae31a3145` |
| `out/r7_combined_candidate_20261119/system.img` | 943,718,400 | `4bf875d0da00cda02eddb5ecfdb2f854d08bf29f2b4be6bac20f24fd641d5aad` |

## Host checks

- Both installed copies carry the same 13,012-byte Android ARM32 audio HAL.
  Its dynamic imports resolve `HMI`, `pcm_open`, `pcm_read` and `pcm_write`
  through `libtinyalsa.so`; the ABI suite negotiates playback and microphone
  streams without opening a PCM device during open.
- The 12-case audio HAL suite passes, including late sound-card recovery,
  stream lifecycle, software volume fallback, error pacing, policy closure,
  lazy `pcmC0D1c` microphone capture, and explicit failure for unsupported
  capture extensions.
- The GPS adapter maps the factory callback order and the actual factory HAL
  dependency closure loads under ARM qemu without initializing `mnld`. A
  separate 32-bit ARM host regression also verifies that unadapted factory
  extension tables are withheld.
- The GPS adapter, factory HAL, and `gpsdrv.ko` were read back from the system
  image with matching hashes, `0644 root:root`, and the expected SELinux
  labels.
- `wlan_gen2.ko` is the MAC API repair rebuilt from pinned source commit
  `e9961becdc889266c3a8b5a488ff741159b6ad80`. All six original modules
  reproduce byte-for-byte, and the repaired module carries three
  `dev_addr_mod` relocations.
- The boot ramdisk contains exactly one `mnld` service declaration, the
  `gps:system` socket contract, the patched prep script, and the expected
  `initrd-end`.
- A static ARM helper and root wrapper are installed for the five known
  `services.odex` blocks. The wrapper records cached and `O_DIRECT` hashes
  before any cache cleanup.
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
Record a short built-in microphone sample and require a non-silent result with
the requested 8/16/32/48 kHz mono or stereo format.

Before dropping caches or restarting framework processes after a suspected
storage fault, run:

```sh
/system/bin/z1_odex_probe
```

`DIRECT_MATCH_IMAGE` with a mismatching cached hash identifies page-cache
corruption. A direct mismatch requires a cold partition read before attributing
it to the storage device.

Test GPS independently:

```sh
getprop init.svc.mnld
ls -l /dev/socket/mnld /dev/stpgps
ls /sys/class/gpsdrv/gps
dmesg | grep -E '\[z1gps\]|mt3326|GPS'
```

`mnld` must remain running after the eight-second stabilization window.
Confirm `getprop ro.radio.noril` returns `yes`, and record the absence of a
running RIL and `/dev/ccci*` nodes. With Wi-Fi off and an outdoor sky view,
request a fresh GPS fix without using cached location. Record WMT GPS power-on,
satellite status, NMEA data, and a valid repeatable location callback. A fix
under these conditions proves standalone GPS works without the cellular modem
stack. If it fails, record which stage failed before changing the modem state.

The factory ramdisk declares `mnld` and `agpsd` separately. The factory `mnld`
and `libmnlp_mt6572` binaries reference `/dev/stpgps`. The factory `mtk_agpsd`
binary contains open success and failure messages for `/dev/ccci_ipc_1220_0`.
The mainline GPS character device requests `WMTDRV_TYPE_GPS` and reads via
`GPS_TASK_INDX`. This establishes the transport used by the two paths. The
factory `WMT_SOC.cfg` sets `co_clock_flag=1`. The physical clock and power
requirements still need board verification.

The original 2G modem firmware and CCCI/RIL binaries are available in the
factory system. A functional cellular port also needs the MT6572 CCIF mailbox
and interrupt, modem power and reset, and verified EMI and shared memory
ranges. After the modem reaches a stable ready state, test the Android 9
Radio HAL independently. The R7 GPS test can proceed with `ro.radio.noril=yes`.

## Boundaries

R7 withholds the factory GNSS extension tables pending separate ABI adapters.
The core standalone GPS interface remains active; assisted GPS and other
proprietary extensions are unavailable until their payload and lifetime
contracts are adapted.

The policy now exposes a built-in microphone and the HAL captures from
`pcmC0D1c` at 8, 16, 32, or 48 kHz PCM16, mono or stereo. The boot-time route
script selects the MT6323 MIC1 uplink and the AFE AWB source. Physical
microphone bias, board MIC1/MIC2 labeling and audible recording quality still
require board verification.

Bluetooth, Wi-Fi, GPU 3D, storage stability, backlight blanking, physical key
mapping, and touch navigation boundaries retain their existing board gates.
The read-only manifest covers their file and basic runtime identities but
cannot replace physical tests.
