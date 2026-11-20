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
