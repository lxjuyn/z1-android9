# MT6572 audio HAL

The stock profile can stage the factory `audio.primary.mt6572.so` with the
fixed-whitelist extractor:

```sh
python3 scripts/extract_z1_audio_hal.py --rom /path/to/local-z1-rom.zip
```

The ROM file is installed as a 32-bit ARM legacy primary HAL under
`/vendor/lib/hw/`, and `ro.hardware.audio=mt6572` selects it. Android 9's
in-process audio HIDL implementation then loads the legacy module. Static ELF
inspection found its declared and strong undefined symbols in the Android 9
system libraries used by this build.

The stock product uses a Z1-specific Audio Policy XML instead of AOSP's
speaker-only primary profile. Its 44.1 kHz stereo PCM output and PCM capture
profiles follow the same-device Android 7 policy. The staged
`audio.primary.mt6572.so` SHA-256 matches that reference ROM's binary. Routes
cover the speaker, earpiece, wired headphones/headset and built-in/headset
microphones. Bluetooth SCO and FM routes remain excluded until their drivers
work on this Android 9 build.

This does not copy NVRAM or audio calibration. The factory calibration remains
on the device and is never part of the repository or image inputs. Actual
speaker, earpiece, microphone, wired-headset, volume and suspend/resume audio
tests on the Z1 are still required; the policy and HAL must not be called
runtime-verified until those checks pass.
