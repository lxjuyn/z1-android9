# Z1 speaker playback differential - 2026-11-22

Status: `HOST_VALIDATED / SERVICE_DUMP_GUARDED / BOARD_PENDING`.

The R7 priority boot reset before any tone ran, so the existing mixer dump did
not identify whether a failure belongs to PCM, DAPM, GPIO140, the MT6323
analog path or the physical speaker. Its boot route also enabled both the
headphone and speaker endpoints, which is not a clean speaker differential.

`scripts/diag/z1_diag_audio.sh trigger` now performs one bounded output test:

1. saves the current Headphone and Speaker mixer values;
2. forces `Headphone Switch=0`, `Speaker Route Switch=1`,
   `Speaker Switch=1` and `Speaker Volume=2`;
3. plays the generated 3-second, 48 kHz stereo PCM16 tone through
   `tinyplay -D 0 -d 0`;
4. samples PCM state, `Speaker`, `Speaker Driver`, GPIO140, and optional
   MT6323/AFE registers while playback is running;
5. restores every changed mixer control on normal exit, failure, or signal.

The script reports the first observable failure through
`PLAYBACK_DIFFERENTIAL_FAIL`. It always leaves the physical result as
`user_confirmation_required`; a running PCM and powered DAPM widget do not
prove that the speaker is audible.

## Live service-dump guard

Android 9 on this image wraps every `IAudioFlinger` binder transaction with a
five-second `TimeCheck`. The full `dumpsys media.audio_flinger` transaction
crossed that limit on the 2026-11-23 live boot: the command began at uptime
119.57 s, and `audioserver` received SIGABRT at 125.84 s.

The tombstone names `TimeCheckThread` and reports:

```text
Abort message: 'TimeCheck timeout for IAudioFlinger: 1598311760'
```

The service was restarted immediately. This failure is caused by the
diagnostic dump path, so it is not evidence that direct PCM playback or the
speaker failed.

`z1_diag_audio.sh` and `z1_diag_all.sh` therefore use `service check` for
AudioFlinger and AudioPolicy liveness. They collect `/proc/asound`, PCM state,
mixer state, HAL identity and logcat without issuing those service dumps.
Treat a manual service dump as a separate diagnostic that may restart
`audioserver`, and capture the preloader, kmsg and crash buffers around it.

## Host validation

The script passes `dash -n`, `bash -n`, and `/bin/sh -n`. The route/evidence
contract checks the exact speaker-only controls, PCM sampling, both DAPM
endpoints, GPIO140, register values, route restoration and explicit operator
gate. Its host SHA256 is recorded in the commit and archive.

## Board run

With the reproduced R7 speaker system booted, headphones physically
unplugged and Bluetooth off:

```sh
z1a9_gpt/scripts/z1deploy/z1push.sh --mode 0755 \
    z1a9_gpt/scripts/diag/z1_diag_audio.sh z1_diag_audio.sh
z1a9_gpt/scripts/z1deploy/z1push.sh --wait 60 --rootcmd \
    'sh /data/local/tmp/z1stage/z1_diag_audio.sh trigger'
```

The operator must record whether the tone was audible. Interpretation:

- PCM not `RUNNING`: AFE IRQ, clock or DMA failed first.
- PCM running but `Speaker`, `Speaker Driver` or GPIO140 not active:
  machine DAPM or GPIO140 failed first.
- PCM, DAPM, GPIO140, `PMIC 0x070a=0x1114`, `SPK_CON0=0x3001` and AFE
  `0x010` bits 0/1/10 all active but still silent: MT6323 analog or the
  physical speaker path remains the failing stage.
- Direct tone audible but Android media silent: AudioFlinger, AudioPolicy or
  the primary HAL remains the failing stage.

Do not change the audio driver until this differential reports the first
missing stage.
