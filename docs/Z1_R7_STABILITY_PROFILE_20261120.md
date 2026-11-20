# R7 stability test with satellite positioning deferred — 2026-11-20

R7 completed boot around 249 s. The first framework restart followed a main-thread
Watchdog: `GnssGeofenceProvider.runOnHandlerThread()` was waiting on `FutureTask.get()`
while `android.bg` was blocked in GNSS HAL `setCallback()`. The touchscreen was still
listed as `/dev/input/event2`; the input service was gone. See board capture
`z1_boot_mainline_20261120_000340.log` at lines 2117, 2293 and 2785.

The stability profile omits the optional vendor GNSS service and VINTF entry,
removes the GPS feature declaration while retaining network location, and removes
the automatic ramdisk GPS prep trigger. Android 9 libhidl also allows a legacy
passthrough fallback when the transport is empty, so the profile replaces only
the GNSS implementation's factory with an ARM shared object that returns null.
The original R7 image retains the full HAL and firmware for future GPS work. The original Android 9 service initializes GNSS
only when `GnssLocationProvider.isSupported()` is true; network location is
created independently. The shipped framework compatibility matrices 1, 2, and 3
all mark GNSS optional. HAL and framework behavior still need board verification.

The only other system change is `MIC Route` → `MIC Route Switch`, as named by
the live MT6323 controls. This correction enables the microphone route and does
not prove physical playback. The kernel and display HAL are unchanged. The
boot builder verified the new ramdisk and recomputed `linux,initrd-end`. The
system builder verified the five changed files, inode metadata, SELinux xattrs,
and both original and final filesystems with read-only `e2fsck`.

Local build:

```sh
python3 z1a9_gpt/scripts/z1_r7_stability_profile.py \
    out/r7_speaker_no_hsq_candidate out/r7_stability_nognss_v2
```

The local staged package includes `MT6572_Z1_R7_STABILITY_scatter.txt` copied
from the hash-pinned R7 base layout, with only `bootimg` and `android` selected.
Its `boot.img` refers to `boot-package/boot.img`. Verify `SHA256SUMS` before
Download Only. Supersedes the unflashed `r7_mnld_first_start_candidate` for
this priority round. The first stability stage without the passthrough guard
is superseded as well.

Once the stability system is on the board, the separate package
`out/r7_stability_r19_boot_only/` tests the `7908d35` lock-screen change with
that exact system, without restoring the old GNSS implementation. Its SHA256
is `59465663f397726d2ffac23e622fba3dfc9f7cd52c6108c4418c8caf68d636a1`.
The only ramdisk change from the stability boot is `hwcomposer.z1.so`; the
only DT change is `linux,initrd-end`. The manifest in that directory can be
rebuilt with `scripts/z1_build_adaptation_boot.py`. Use the `BOOT_ONLY`
scatter only after validating the paired stability system. Do not use the
standalone R7 r19 pair to test this stability profile because that system
still contains the live GNSS HAL.

Acceptance order:

1. Cold boot with serial capture; verify input service and one `system_server`
   PID at repeated samples over at least 10 minutes. Confirm `mnld` and
   `vendor.gnss_service` stay stopped; look for no GNSS-triggered Watchdog and
   physically swipe both home and an app.
2. Bluetooth power cycle and discover a known device. R7 reached a WMT RX
   timeout and `bt_on` failure before the HCI callback and subsequent crashes.
   Bluetooth needs independent verification after correcting the transport.
3. Play a known tone with headphones disconnected. Verify Android creates a
   playback stream, `pcmC0D0p` runs, and the speaker amplifier turns on. Verify
   audibility and two volume levels; separately check microphone samples.
4. Tap the physical power key; confirm LCD blanking and backlight off, then
   repeat wake/lock. Verify no visible frozen previous frame and no late
   backlight ON from the Lights HAL.
5. After system stabilization, measure the same 15 automated launcher swipes
   before and after any performance change. Record `dumpsys gfxinfo` totals and
   `dumpsys SurfaceFlinger --latency`, with the CPU/GPU policy and loaded HWC
   hash recorded. Keep graphics timing experiments separate from stability.

For a repeatable UI comparison, open the same unlocked portrait screen on both
runs. It must be scrollable in both directions. With `Z1LOG` set to the active
serial capture, upload the probe and run it against the foreground package:

```sh
z1a9_gpt/scripts/z1deploy/z1push.sh \
    z1a9_gpt/scripts/diag/z1_diag_ui_swipes.sh z1_diag_ui_swipes.sh
z1a9_gpt/scripts/z1deploy/z1push.sh --wait 120 --cmd \
    'sh /data/local/tmp/z1stage/z1_diag_ui_swipes.sh com.android.launcher3 baseline'
```

Use the resolved foreground package if different. Compare only measurements
ending with `Z1_UI_END valid=1`; the probe rejects a missing input service,
restarted framework/app, wrong foreground app, or no rendered frames. Match
the screen, display size, battery/thermal state and applied HWC build for
both runs unless one of those is the variable being tested. This measures the
app's reported rendering statistics; use SurfaceFlinger and display timing
separately to identify a scanout or compositor bottleneck.

## Bluetooth comparison on the stability system

The standalone Bluetooth package (`7345bcd`) identified two missing contracts:
factory combo chip ID `0x6572` and `nvram_init=Ready` with valid per-device NVRAM.
R7's radio init ran at 21.8 s; Android loaded persistent properties around
37.5 s. An older stored chip ID may overwrite the early value. The optional
`--bt-late` stability profile adds an init action after persistent properties
are loaded, repeats only Bluetooth identity preparation, and keeps module
insertion and Wi-Fi handling independent from property failures. GNSS remains
isolated and the microphone spelling correction is preserved.

Build with `z1_r7_stability_profile.py --bt-late` using the same original R7
base above. The locally archived candidate
`out/r7_stability_bt_late_candidate/` contains boot SHA `74b699f7…` and a
12-block system delta from the verified no-GNSS system (`1ef07da1…`) to
`638e86df43bb449180f5f3b82dd83894f10fe229df04e1c84633a5a2234615c9`.
Use `z1_image_block_delta.py apply` with the no-GNSS `system.img` as `--base`,
`system_from_stability.z1delta` as `--delta` and a new `system.img` alongside
the copied boot and scatter as `--output`. SHA and e2fsck passed; an independent
apply reproduced the same system bytes. The H4 probe lives beside the package
for explicit serial deployment. The first board gate remains the original
stability build; use this candidate for the Bluetooth comparison without
bringing the known GNSS stall back into the test.

After boot read the three properties `persist.mtk.wcn.combo.chipid`,
`nvram_init`, and `sys.z1.radio.bt_nvram_ready`. If the controller still times
out when all three gates pass, collect WMT, BTIF, and H4 results before changing
kernel transport. If the readiness gate fails, preserve the unit's existing
NVRAM and diagnose the expected paths and sizes before writing any record.

## Priority pair: Bluetooth, speaker and lock screen

`out/r7_priority_bt_audio_lock/` combines the speaker-equipped, no-GNSS
Bluetooth comparison system with the r19 lock-screen fix. It is an option for
one board session when flash windows are limited. The no-GNSS v2 pair remains
the isolated fallback if the combined pair fails the boot or input gate.
GPS functionality is deferred; this pair retains only the watchdog isolation
already in the v2 profile. Keep touch cadence and performance changes separate.

| File | SHA256 |
| --- | --- |
| `boot.img` | `2d99381361466a05861af7fe17a51e5924e4e6094a47287edfdf1759e60294c3` |
| `system.img` | `638e86df43bb449180f5f3b82dd83894f10fe229df04e1c84633a5a2234615c9` |
| `MT6572_Z1_R7_STABILITY_scatter.txt` | `a1f34ae8d68924edb7275c80c0e0370016268d70928f099d6748c5b832fa713a` |

The boot builder uses the Bluetooth comparison boot as the base and replaces
only `hwcomposer.z1.so`. It verified every unchanged ramdisk record, including
the late radio init trigger. The kernel is byte-identical, and the only DT
change is the updated `linux,initrd-end`. The paired system passed read-only
`e2fsck`; GPIO140 is active-high in the appended DT and the ext4 copy of
`mt6572-mt6323.ko` matches the speaker baseline. The scatter allows only
`bootimg` at `0x2980000` and `android` at `0x4340000`; both images fit.

The path in `out/r7_priority_bt_audio_lock/staging_path` names the temporary
SP Flash Tool directory. If gone, rebuild `boot.img` from the archived
`boot-inputs.json` with `scripts/z1_build_adaptation_boot.py`, or use the
archived boot. Recover `system.img` with `scripts/z1deploy/z1_image_block_delta.py
apply`, using the v2 no-GNSS system as `--base`, the priority package delta as
`--delta`, and a fresh `system.img` path as `--output`. Check all three hashes
with `SHA256SUMS.flash` before Download Only. The standalone `--bt`/`--hwc-blank`
flash-script profiles select older pairs and should not be used for this package.
Start CH340 capture and the exact SP Flash Tool job before requesting the download
USB connection. Remove the download cable for normal boot.

Board gate for this combined pair:

1. Verify `sys.boot_completed=1`, the same live `system_server` PID, and an
   available input service over a 10-minute interval with physical swipes.
   Confirm GNSS did not start. If Android restarts or touch stops, keep the
   serial trace and use the isolated v2 pair for fault separation.
2. After the three Bluetooth identity/readiness properties pass, test enable,
   discovery, pairing and reconnect. On failure, capture WMT/BTIF before using
   the H4 probe independently; don't run a raw probe while Android owns HCI.
3. With headphones unplugged and Bluetooth off, play the bounded test tone via
   `z1_diag_audio.sh trigger`, then play it with an Android media app at two
   audible volume levels. If direct PCM is silent, check PCM RUNNING, Speaker
   DAPM and GPIO140 during the same attempt. If direct PCM works and the app
   is silent, capture AudioFlinger/AudioPolicy while the app is playing.
4. Verify the effective HWC SHA is
   `5e1294dea240ebb31a9623424ccdb27c78617dc6b929dd4b2c85096422e27ad5`.
   Use `z1_lockscreen_probe.sh` for `display_blank=0 → 1 → 0` and repeat with
   the physical power button. Observe the actual panel going dark, then a new
   responsive frame after wake. A gate transition alone does not prove either.

Status: host verified, not flashed. This does not establish Bluetooth link,
physical speaker output, or panel behavior on a board.

With `Z1LOG` pointing to the active serial capture, upload and run the
stability probe after boot. Leave the device awake, and exercise the physical
screen during the run. Do lock/wake testing after this probe finishes.

```sh
z1a9_gpt/scripts/z1deploy/z1push.sh \
    z1a9_gpt/scripts/diag/z1_diag_stability.sh z1_diag_stability.sh
z1a9_gpt/scripts/z1deploy/z1push.sh --wait 650 --cmd \
    'sh /data/local/tmp/z1stage/z1_diag_stability.sh 600'
```

Require `Z1_STABILITY_END status=PASS` and operator confirmation of swipes.
The probe compares both PID and process start time, checks a bounded live input
dispatcher dump every 15 seconds, and checks the init states of `mnld`,
`z1_mnld`, and `vendor.gnss_service`. A serial timeout, failed dump, missing
completion marker, or visible touch freeze does not pass the board gate.
