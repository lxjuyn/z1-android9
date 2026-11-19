# Z1 R6 output-audio candidate (2026-11-19)

This candidate is the host-verified response to the earlier silent-speaker
trial. The R5 system image still contained the 20,844-byte placeholder at both
`audio.primary.default.so` paths. The R6 system keeps the R5 kernel, codec
module, HWC, Mesa, input, and radio files, and replaces those two placeholder
copies with the 8,196-byte tinyalsa output HAL already covered by the R5G ABI
tests.

## Candidate pair

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `out/r6_audio_candidate_20261119/boot_hsq_control_r6.img` | 5,984,256 | `b175077f8ce3b2322335f4b10f64f0186281242a00cb17dd267cf16d432168b1` |
| `out/r6_audio_candidate_20261119/boot_no_hsq_r6.img` | 5,984,256 | `33cab90475df68eb1c3f4d1a87911f50b417712e8c625fe7c3816c47e0017419` |
| `out/r6_audio_candidate_20261119/system.img` | 943,718,400 | `f117ff23608e65502a86083836722680bc6b13242f5e45649f5d1e0c44945385` |

The boot images are byte-identical copies of the R5 HSQ control and no-HSQ
variants. Use the HSQ control boot for the audio test unless storage
diagnostics explicitly require the no-HSQ variant. The system image must be
used as the matching R6 system.

## Host evidence

- Both deployed HAL paths hash to
  `73d22b253acce390caecbf6bb1cb8d2b34ea85b07cc982b8c406ce2873f08e2e`.
- The Android 9 linker loads each deployed copy under qemu and opens the HAL
  interface. A 44.1 kHz stereo request is negotiated to 48 kHz stereo PCM16
  without opening a PCM device.
- The source-level suite passes 12/12 cases covering lazy opening, ten-write
  recovery from a late sound-card probe, standby, stream lifecycle, software
  volume fallback, write errors, unsupported presentation timestamps, policy
  closure, and explicit unsupported capture.
- `e2fsck -fn` passes after installation.
- Both HAL replacements retain the original `0644 root:root` metadata and the
  NUL-terminated `u:object_r:vendor_file:s0` label.
- The output policy still exposes Speaker, Earpiece, wired headset, and wired
  headphones with Speaker as the default device.

## Device gates

After boot, first run the read-only identity gate:

```sh
/system/bin/z1_runtime_manifest --r6
```

A successful host identity is not an audible-output test. Continue with:

```sh
cat /proc/asound/cards
dumpsys media.audio_flinger
dmesg | grep -i -E 'z1audio|mt6323|mt6572-mt6323|pcm'
tinymix
```

Then play a known PCM file through Android and through `tinyplay`, record
whether sound is audible from the physical speaker, and check whether volume
changes affect output. Inspect the loaded HAL path and hash from the audio HAL
process maps before diagnosing a lower layer.

## Remaining boundary

This candidate implements playback only. `open_input_stream()` deliberately
returns `-ENOSYS`, and the audio policy publishes no capture device. Android
recording therefore remains unadapted; a silent microphone result cannot be
used to reject this output-only candidate.

The next input stage must verify the AWB capture DAI, microphone bias and route,
and the Android speech/audio-source callback bridge before exposing a capture
profile.
