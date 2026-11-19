# Z1 R7 board test runbook

Use this runbook with
[`Z1_R7_COMBINED_CANDIDATE_20261119.md`](Z1_R7_COMBINED_CANDIDATE_20261119.md).
The candidate is host-verified and awaits its first board test. The board is
currently on R4. When it is available, capture the five ODEX pages against
that existing system first, then run the HSQ A/B sequence documented in
[`Z1_R4_FRAMEWORK_READBACK_20261119.md`](Z1_R4_FRAMEWORK_READBACK_20261119.md).
R7 overwrites the R4 Android partition, so that comparison must precede it.

## Before flashing

From the workspace:

```sh
z1a9_gpt/scripts/z1deploy/z1_flash_r7.sh
```

The default is a dry run. It checks the exact boot/system hashes, boot magic
and capacity, ext4 integrity, runtime gate, audio HAL, GPS module, and repaired
Wi-Fi module. Use `--execute` only after that passes and the download cable is
ready.

Open the CH340 UART capture before the first normal power-on:

```sh
Z1_CAPTURE_BAUD=921600 ./z1_capture_mainline.sh 900
```

Select the CH340 by USB serial identity. Do not select the ESP32 ACM port when
both serial devices are present.

## First boot

Keep the download cable disconnected after flashing. Power on and wait for
`sys.boot_completed=1`. Then run:

```sh
/system/bin/z1_runtime_manifest --r7
```

Require `summary pass=59 fail=0 status=PASS`. This checks installed identities,
audio policy, microphone/AWB registration, GPS service and transport, Wi-Fi
module identity, display power gate, keys, touch, and navigation policy.

## Storage

Before dropping caches or restarting framework services:

```sh
/system/bin/z1_odex_probe
```

Require `DIRECT_MATCH_IMAGE`. Compare `cached_sha256` with the expected image
hash. A cached mismatch with a direct match identifies page-cache corruption.
If direct read also differs, power off and re-read the partition from the host
before doing another write.

Do not run `e2fsck` against mounted `/data`. Preserve `/data` for evidence
unless a separate, explicit recovery decision has been made.

## Audio output and input

Inspect the card and controls:

```sh
cat /proc/asound/cards
cat /proc/asound/pcm
tinymix
dumpsys media.audio_flinger
```

Require both `DL1 Playback` and `AWB Capture` to appear. Play a known PCM signal
through Android and confirm audible speaker output plus a response to volume
changes.

Run a built-in microphone capture:

```sh
tinycap /data/local/tmp/z1diag/mic.pcm -D 0 -d 1 -c 2 -r 48000 -b 16 -n 3
```

Require a non-silent 48 kHz stereo PCM16 file and a successful 16 kHz mono
`AudioRecord` test. Check DAPM state for `MIC Route`, `MIC UL`, `AIF1 Capture`,
`AWB Source=MIC`, and `MIC Input Mux=MIC1`.

## Bluetooth

Run passive evidence first:

```sh
sh /system/bin/z1diag/z1_diag_bt.sh
```

Then run the active trigger:

```sh
sh /system/bin/z1diag/z1_diag_bt.sh trigger
```

Require a live `/dev/stpbt`, `BT_open` success, HCI command/event traffic,
adapter ON, discovery, pairing, disconnect, and reconnect. A2DP audio is
a separate gate. The current adapter intentionally reports SCO unsupported.

Record the first WMT power-on result and `6620_launcher` process state. In
`z1_boot_r14_verify.log`, lines 231021–231038 record a separate failure where
`BT_open` was reached, the factory driver failed to obtain NVRAM readiness,
and the adapter returned no HCI descriptor. Capture `getprop nvram_init`
and the first `bt_read_nvram` error in the same attempt. Keep the factory
configuration untouched during evidence capture.

## Wi-Fi

```sh
sh /system/bin/z1diag/z1_diag_wifi.sh
```

Require no `Incorrect netdev->dev_addr` warning, `wlan0` present, scan,
association, DHCP, DNS, and reconnect. AP and P2P are outside the current
STA-only HAL scope.

## GPU and display

```sh
dumpsys SurfaceFlinger
/system/bin/z1_gpu_gate
```

Run the GLES2 offscreen probe and a sustained on-screen workload. Record EGL
libraries from `/proc/<pid>/maps`, `GL_RENDERER`, pixel output, frame times,
and `gp error irq`. Physical proof requires 60 Hz-capable UI animation and no
static frame reuse.

Lock and unlock the screen repeatedly. Require the panel to turn physically
black and recover the current frame. A retained previous frame is a display
power failure even if SurfaceFlinger reports display OFF. Sample both
`/sys/class/leds/lcd-backlight/display_blank` and `brightness` while awake,
locked and awake again. The gate should read `0`, `1`, then `0`. If it stays
at `0` while locked, trace the HWC power callback and its sysfs write. If it
reaches `1` while the screen still visibly retains the frame, check physical
backlight output and panel blanking separately.

## Keys, touch, and navigation

Capture complete `getevent` DOWN/UP sequences for:

- play/confirm -> `DPAD_CENTER`;
- repeat -> `VOLUME_DOWN`;
- follow -> `VOLUME_UP`;
- four directions unchanged;
- power key wake/sleep.

Touch a normal screen area near the bottom and each of the three physical
navigation targets. Screen touches must not trigger navigation; the three
targets must remain `BACK`, `HOME`, `APP_SWITCH`. Confirm the virtual navigation
bar remains hidden.

## Rollback

The immediate rollback pair is the R5 control boot and system recorded in the
R7 candidate document. The factory boot image remains `1/boot.img`. Preserve
the R7 package and logs before attempting any rollback or partition repair.
