# R7 stability test with satellite positioning deferred — 2026-11-20

R7 completed boot around 249 s. The first framework restart followed a main-thread
Watchdog: `GnssGeofenceProvider.runOnHandlerThread()` was waiting on `FutureTask.get()`
while `android.bg` was blocked in GNSS HAL `setCallback()`. The touchscreen was still
listed as `/dev/input/event2`; the input service was gone. See board capture
`z1_boot_mainline_20261120_000340.log` at lines 2117, 2293 and 2785.

The stability profile omits the optional vendor GNSS service and VINTF entry,
removes the GPS feature declaration while retaining network location, and removes
the automatic ramdisk GPS prep trigger. The stock libraries and firmware remain
available to a future GPS build. The original Android 9 service initializes GNSS
only when `GnssLocationProvider.isSupported()` is true; network location is
created independently. The shipped framework compatibility matrices 1, 2, and 3
all mark GNSS optional. HAL and framework behavior still need board verification.

The only other system change is `MIC Route` → `MIC Route Switch`, as named by
the live MT6323 controls. This correction enables the microphone route and does
not prove physical playback. The kernel and display HAL are unchanged. The
boot builder verified the new ramdisk and recomputed `linux,initrd-end`. The
system builder verified the four changed files, inode metadata, SELinux xattrs,
and both original and final filesystems with read-only `e2fsck`.

Local build:

```sh
python3 z1a9_gpt/scripts/z1_r7_stability_profile.py \
    out/r7_speaker_no_hsq_candidate out/r7_stability_nognss_stage
```

The local staged package includes `MT6572_Z1_R7_STABILITY_scatter.txt` copied
from the hash-pinned R7 base layout, with only `bootimg` and `android` selected.
Its `boot.img` refers to `boot-package/boot.img`. Verify `SHA256SUMS` before
Download Only. Supersedes the unflashed `r7_mnld_first_start_candidate` for
this priority round.

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
