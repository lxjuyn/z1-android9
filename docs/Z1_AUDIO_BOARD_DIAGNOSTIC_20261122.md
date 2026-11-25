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

## R7 Sound Settings and speaker gain

The core-wake system `10decdbb…` contains HAL `4bc207d5…` at both
`/vendor/lib/hw/audio.primary.{mt6572,default}.so` paths and Settings
`85ea8215…`. Both boot `09b2c74d…` and its Bluetooth cold-start derivative
`9386a4ac…` contain the same HAL and stage codec `254ce8f8…` over the
system image's `10f0d2a8…` codec. The November 24 serial log
`mainline_recon/z1_boot_mainline_20261124_234757.log:95` records the resulting
gain switch in the off state, playback gain 8251, and the staged codec hash.
The saved playback gain can differ by board state.

Factory code uses the MT6323 headphone PGA and GPIO140 external amplifier
for the speaker. Codec commit `a47e89755` changes `AUDTOP_CON5 & 0x7700` to
`0x5500` when the Z1 speaker gain switch is active. The Android HAL sets
playback gain 61440 and enables that switch on exclusive speaker playback
in both modes. Normal mode halves each PCM16 sample. High mode passes the
samples through. Comparing the mixer values alone cannot distinguish these
two modes. The factory ROM volume-table default is known, while the
original board's userdata volume-table record is unavailable.

Use `z1_diag_audio.sh snapshot PHASE` over the root serial dispatcher while
operating the UI. The snapshots read mixer controls, PCM, DAPM, GPIO140,
process IDs, and the mapped primary HAL. They leave the Android route and
playback under framework control.

1. With media stopped, headphones unplugged and Bluetooth off, take an
   `idle` snapshot. Record the saved playback gain and file identities.
2. Start a steady tone in an Android media app at low media volume. Set the
   Sound Settings high-volume switch off and capture `speaker-normal`. Then
   turn it on during the same playback and capture `speaker-high`. Both
   snapshots should show progressing PCM, the gain switch on and playback
   gain 61440. The high snapshot should show `speaker_high=1`. Record actual
   speaker audibility and compare amplitude at a fixed microphone position
   and capture gain with no clipping or AGC. An approximately 6 dB change
   is expected in the linear range. Record the PID identities across the
   switch operation.
3. Insert a wired headset while media is playing and capture `wired`.
   Confirm audio at the headset and silence at the speaker, speaker gain
   off, and playback gain back at its idle value. Unplug and confirm speaker
   playback resumes. Pause until output standby and capture `standby`.
   Require PCM stopped, speaker gain off, and the saved playback gain
   restored. Return the switch to its original setting.
4. To investigate a Settings restart, take `settings-before` after opening
   Settings, then take `settings-after` immediately after the UI operation.
   The latter prints recent crash records. Match new records to timestamp
   and PID. A change in boot ID indicates a device restart. A change in
   system server and audio PIDs can indicate a framework restart.

The crash captured November 24 at `23:55:42` names
`PrintSettingPreferenceController.onStart` as a null `PrintManager` exception
(log lines 316–317). The November 25 capture `000913` shows Android's
watchdog issuing SysRq, zygote dying at line 176, and init then killing
audioserver at line 180. The active Sound Settings path still requires a
same-boot playback and crash capture.
