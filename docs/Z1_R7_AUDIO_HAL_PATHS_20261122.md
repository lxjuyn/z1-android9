# Z1 R7 audio HAL loader-path repair - 2026-11-22

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`.

## Finding

Android's libhardware probes `audio.primary.<ro.hardware>.so` before
`audio.primary.default.so`. The board already provides direct evidence that it
selects the MT6572 variant:

```text
mainline_recon/z1_boot_r14_verify.log:161118
HAL: load: module=/vendor/lib/hw/audio.primary.mt6572.so
```

The R7 speaker base did not place the complete HAL there:

| Path | Bytes | SHA256 | Capability |
| --- | ---: | --- | --- |
| `/vendor/lib/hw/audio.primary.mt6572.so` | 8,132 | `029ed847bb14...` | output only; no `pcm_read` |
| `/vendor/lib/hw/audio.primary.default.so` | 13,012 | `3f1b3a841aea...` | output and AWB input |
| `/vendor/lib/libaudio.primary.default.so` | 13,012 | `3f1b3a841aea...` | output and AWB input |

The 8,132-byte mt6572 module is not a dlopen shim. It exports its own `HMI`,
depends directly on tinyalsa, and contains `pcm_open`/`pcm_write` without
`pcm_read`. Therefore the selected module bypassed the complete `default`
implementation. The boot ramdisk also contained the same output-only file and
rewrote it after `/system` mounted.

## Repair

The complete A9 HAL is now installed identically at all three loader paths:

| Item | Bytes | SHA256 |
| --- | ---: | --- |
| complete HAL | 13,040 | `670cd3e97d07bf342d1635771b21b755f6c35b257949c959e481055e450176b6` |

`z1stage_boot.sh` seeds the complete HAL into the selected `mt6572` path and
both fallback paths. The corrected `MIC Route Switch` route is also packaged;
the previous boot helper used the nonexistent control name `MIC Route`.

## Candidate

| Artifact | Bytes | SHA256 |
| --- | ---: | --- |
| `out/r7_audio_hal_paths_20261122/boot.img` | 5,986,304 | `2cd3b00dd76b546179d835314971403a51211c8ae9c1803fefb21fb7b0af61e1` |
| target system | 943,718,400 | `2b49d9a5e82c1d6fc79e12fc457aebaf1d6dd92383bbbda0bbe3b66b01447150` |
| system delta from R7 speaker | 106,888 | `39ea03ab4d88cc4a27d12c07e20986ed6fccf5eee7d078be7c6783ac266480e7` |

The boot is below the 6 MiB limit. Its appended DTB has
`linux,initrd-start=0x84100000` and `linux,initrd-end=0x8429124b`. Independent
CPIO readback found the embedded HAL and updated stage script at the expected
hashes.

## Host validation

- Applying the delta to the R7 speaker base reproduced the target system hash.
- `e2fsck -fn` passes on both the build output and the replayed image.
- All three HAL files have the same bytes, ELF build ID, imports and `HMI`
  export. Each contains `pcm_open`, `pcm_read`, `pcm_write` and `pcm_close`.
- The 13/13 host HAL suite passes, including lazy playback, lazy AWB capture,
  microphone mute and late sound-card recovery.
- The deployed-image QEMU loader could not run in this host session because
  host PID 87903 exceeds 32-bit bionic's `pid <= 65535` constraint. It aborted
  before loading the candidate. Identical-bytes loader tests remain valid from
  earlier sessions, but this specific replay is not claimed as executed.

## Board gate

Materialize the full system from the included delta:

```sh
python3 out/r7_audio_hal_paths_20261122/materialize_system.py \
    out/r7_speaker_gpio_candidate_20261119/system.img \
    /dev/shm/z1_audio_system.img
sha256sum /dev/shm/z1_audio_system.img
# require 2b49d9a5e82c1d6fc79e12fc457aebaf1d6dd92383bbbda0bbe3b66b01447150
```

After flashing the paired boot and system and booting headphones-out, Bluetooth
off:

```sh
/system/bin/z1_runtime_manifest --r7-audio
cat /proc/asound/cards
dumpsys media.audio_flinger
```

Inspect the audio HAL process maps and require
`/vendor/lib/hw/audio.primary.mt6572.so`, not a stale inode. Then run
`z1_diag_audio.sh trigger` for the speaker-only direct PCM differential and an
Android `AudioRecord` test for the microphone. These remain physical board
gates; the runtime manifest alone does not establish sound or capture.
