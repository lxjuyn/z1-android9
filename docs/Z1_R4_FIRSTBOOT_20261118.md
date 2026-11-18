# R4 first boot observation, 2026-11-18

The paired boot/system transfer passed full byte readback before this boot. This
record does not certify peripheral functionality.

## Observed boot

- Initial log: `mainline_recon/z1_boot_mainline_20261118_224601.log`.
- Kernel command line uses `ttyS0,921600n8`; one Linux boot banner was observed.
- `/data` e2fsck reports clean at 11.25 s. The stage checks found no error/journal
  replay flags on data/cache; storage gate became ready at 12.97 s.
- Stage installed HWC r18 from ramdisk and reported runtime destination SHA256
  `35176357f81284ffc11d56d2811cab52c77a0cdcfaf4678866e54bf00d579912`.
  This is installation evidence; process maps and display behavior still need
  checking.
- Audio loader reported 16 modules and zero load failures. Playback is untested.
- WiFi firmware started and scan returned 6 BSS. `dev_addr_check` warned earlier;
  that warning did not prevent this scan and is not proof of registration failure.
- Continuation: `mainline_recon/z1_boot_mainline_20261118_225746.log`.
  Init processed `sys.boot_completed=1` at 203.39 s.
- At 214.69 s, system_server requested `shutdown,battery`. This is an explicit
  framework shutdown request; the excerpt does not establish whether the battery
  was actually low. The early heartbeat voltage was 4289000 uV.
- The CH340 reader later received EOF and exited normally. Capture was restarted
  on user request at `mainline_recon/z1_boot_mainline_20261118_225911.log`, with
  image metadata and 921600 baud, for 7200 seconds.

## Capture latency repair

The old `tr -d '\000'` reader fully buffered stdout. Small shell reply markers
were visible in the saved log only after the transport's ping timeout. The
updated reader uses `stdbuf -o0 tr` and is also saved in `scripts/` for versioning;
the deployed outer script and tracked copy have identical hashes.

Validation: shell syntax passed. A pipe probe reproduced the old failure (no
short response visible within 350 ms), while the new reader published the exact
NUL-filtered response in approximately 4 ms. The next real serial ping produced
both markers in the new capture. The larger command timed out and yielded no
validated snapshot; its echoed contents must not be treated as returned facts.

Next boot: obtain bounded, paced shell queries for battery sysfs/health state,
actual loaded file identities, touch parameters and keymaps. Then verify audio,
backlight blanking, Bluetooth, GPU performance and GPS separately. Do not claim
any of these pass from module-load or image metadata alone.
