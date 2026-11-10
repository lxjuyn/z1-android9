# Z1 Bluetooth vendor offline regressions

These tests compile the **current** `../libbt-vendor-mtk.c` and synthetic driver
fixtures in a new `mktemp` directory on every run. No archived `.so`, proprietary
blob, device node, firmware, NVRAM, service, USB connection, or hardware is used.
The temporary directory is removed on exit. Run from any working directory.

```sh
/home/lxj/claude/6572/z1a9_gpt/bluetooth/libbt-vendor-mtk/tests/run_host.sh
SANITIZE=1 /home/lxj/claude/6572/z1a9_gpt/bluetooth/libbt-vendor-mtk/tests/run_host.sh
/home/lxj/claude/6572/z1a9_gpt/bluetooth/libbt-vendor-mtk/tests/run_host.sh --probe-cleanup-failure
```

Requirements: Linux, Bash, a GNU-compatible C compiler/linker (`CC`, default `cc`),
coreutils (`mktemp`, `sha256sum`, `timeout`), and libdl. `SANITIZE=1` adds AddressSanitizer
and UndefinedBehaviorSanitizer. The source hash printed by each run is checked
again at the end to detect a concurrent production edit. Each case gets a fresh
process with a 20-second timeout.

## Cases and observed results

Verified on 2026-11-10 against production SHA-256
`29e69c03beec09f47215e92a4882a774ce889aa3ee1f2c0cbd88567b1f817e1b`.
Normal, ASan/UBSan, explicit probe, and ASan/UBSan explicit probe all exited 0.

| Case | Assertions | What it checks | Result |
| --- | ---: | --- | --- |
| `lifecycle` | 138 | Callback size/null validation; pre-init operations; duplicate init/open; all HCI channel slots; synchronous firmware result; enable failure/retry; repeated power cycles; close/power-off/cleanup ordering; no double-disable; callback cleanup; unsupported SCO/audio; optional callbacks; LPM/epilog | Pass |
| `fd-zero` | 33 | fd=0 is a valid controller fd, shared by all four slots; FW_CFG succeeds; close and cleanup both disable fd=0 once | Pass |
| `close-retry` | 368 | Explicit close failure plus the real-fd cleanup regression below, included in the default suite | Pass |
| `missing-enable` | 34 | Missing `mtk_bt_enable` yields `-ENOSYS`; handle unload; no stale callbacks; healthy init retry without cleanup | Pass |
| `missing-disable` | 34 | Missing `mtk_bt_disable` yields `-ENOSYS`; handle unload; healthy init retry without cleanup | Pass |
| `dlopen-missing` | 34 | Nonexistent fixture yields `-ENOENT`; no acquired handle; healthy retry | Pass |
| `dlopen-invalid` | 34 | Invalid ELF yields `-ENOENT`; no acquired handle; healthy retry | Pass |
| `cleanup-disable-failure` (explicit probe) | 343 | Real pipe fd remains open through repeated `-EIO`/`-1`; no driver unload or stale callback; cleanup/close/power-off retries; successful release followed by reinit; reused fd not closed by stale teardown | Pass |

Default suite: 7 cases, 675 assertions, 0 failures. Explicit probe: 8 cases,
1,018 assertions, 0 failures. Counts reflect the verified host fd allocation.

## Cleanup ownership and recovery contract

The original production source SHA-256
`542410c9ba3b9eb2ce822398f45be62394b7bb1bcfdc56baaac8350290e5ae6f`
reproduced the reported probe failure before modification: exit 1, reinit
`-EALREADY` (-114). Cleanup unloaded the factory driver and erased its disable
function while retaining `hci_fd`. Recompiling that original source with the new
regression also exits 1: premature unload, lost retry, and unreleased pipe fd are
assertion failures. This is not an expected-failure test.

The repair clears callbacks when cleanup starts. If disable returns negative,
it retains the descriptor, factory driver handle, and release function, and logs
that cleanup is deferred. `init()` remains `-EALREADY`; `USERIAL_OPEN` returns 0
and fills the channel array with -1. A later `cleanup()` retries disable. Explicit
`USERIAL_CLOSE` or power-off may also retry, followed by `cleanup()` to unload.
Only after successful release and cleanup may a new init/open succeed. Closing
the retained fd does not reactivate the old session; the open callback check
blocks that interval until a fresh init. No raw `close()` was added to production,
and failure never resets `hci_fd` to pretend the resource was consumed.

Factory ownership evidence, independently regenerated with
`arm-linux-gnueabihf-objdump -d` from the archived ELF files:

- `/home/lxj/claude/6572/bridge_backups/bt_audit_20261110_205049/libbluetoothdrv.so.dis`,
  lines 250-286: `mtk_bt_disable` tests its internal library handle at
  `0x8aa-0x8ac`; when null it returns -1 at `0x8bc` without invoking restore.
  Otherwise `0x8ca` calls the restore function with the fd, `0x8ce` unloads the
  internal library, and `0x8d2-0x8d4` clears the handle and returns 0.
- The same archive's `libbluetooth_mtk.so.dis`, lines 709-730: `bt_restore`
  calls `BT_DeinitDevice` at `0xfdc`, calls `close(fd)` at `0xfe2`, and returns 0
  at `0xfe6`, irrespective of the close result.
- Both archived `image/lib__libbluetoothdrv.so` and
  `image/vendor__lib__libbluetoothdrv.so` have SHA-256
  `98e05e23ad6fe97f18bd41ad4caf3d486e1ed1f51a03c27cb16f9fad2cc4fcfb`.

The injected `-EIO` is synthetic; the audited negative branch returns -1. Both
are tested as pre-consumption failures. The real-fd mock owns anonymous pipe
descriptors and explicitly leaves them open on an injected failure. On success
it closes the owned fd once. The regression checks `fcntl(F_GETFD)`, mock ownership,
release counts, loader reference counts, and a guard pipe deliberately reusing
the released fd number. It exercises recovery through cleanup, explicit close,
and power-off, then a complete new session. Original control-flow cases still
use synthetic numeric fds, including fd 0.

**Limits:** this establishes retryability while the adapter remains loaded, not
that the deployed factory's missing internal handle can recover. A persistent
failure keeps resources pending and rejects reinit. The archived AOSP
`ref/vendor_interface.cc:292-302` calls close, power-off, cleanup, then unloads the
adapter without checking cleanup (which returns void). Unloading at that point
can lose the retained static state; this patch cannot promise recovery across
that boundary or a leak-free persistent-failure teardown. The factory also hides
the result of its own `close(fd)`; zero means its restore path ran, not proof of
hardware power-off or successful underlying close. No reachable board failure,
controller shutdown, or runtime descriptor ownership has been established.

Verification logs and original-file backups are under
`/home/lxj/claude/6572/bridge_backups/bt_cleanup_fix_20261110_215901/`:
`normal.log`, `sanitize.log`, `probe.log`, `sanitize_probe.log`, and
`original_source_new_regression.log`.

## Loader isolation and limits

`loader_fixture.c` uses GNU ld `--wrap=dlopen` / `--wrap=dlclose` in the host build
only. It asserts that production requested exactly `libbluetoothdrv.so`, then
redirects that request to an absolute test fixture path. The real system loader
performs ELF loading and symbol lookup; acquisition/release counts and `RTLD_NOLOAD`
check failure cleanup. A missing fixture cannot accidentally load a real driver
from the machine's search path. Failed drivers are not preloaded by the harness;
normal mocks are retained only to read counters after production unloads them.

This does not test Android linker namespaces, bionic behavior, factory controller
initialization, NVRAM contents, real runtime library mappings, HCI traffic,
Bluetooth scanning/pairing/audio, or board operation. The original mock cases use
numeric fds; cleanup regressions additionally open and close anonymous host pipe
fds. These are not HCI device fds. Host execution validates adapter control flow
and the stated host descriptor contract, not 32-bit execution. Separate compile-time ARM ABI checks
cover the official callback/interface sizes and selected field offsets.

## ARM API28 candidate

```sh
/home/lxj/claude/6572/z1a9_gpt/scripts/btgps/bt/build_candidate.sh
```

This script uses Android NDK **r26d / 26.3.11579264**, defaults to
`/home/lxj/claude/6572/android-ndk-r26d`, and supports `ANDROID_NDK_HOME` and
`BT_ARCHIVE_ROOT` overrides. The archive root must already exist. It snapshots the
current source/tests, runs the default host regressions, and builds an unstripped
ARMv7 API28 `libbt-vendor.so` with `--no-undefined`, RELRO and immediate binding.
The product is copied immediately into a unique timestamped directory under
`bridge_backups/products/`. The archive includes source snapshots, compiler/version,
exact command, host log, `readelf` output, JSON metadata, and SHA-256 manifests.
It checks ELF32 ARM, API28/r26d Android note, 16-byte exported interface, SONAME,
and the exact NEEDED set `liblog.so`, `libdl.so`, `libc.so`; it rejects GLIBC version
tags, host loader names, RPATH/RUNPATH/TEXTREL, and test-hook exports. The ARM build
uses no host loader wrapper or mock code. `--no-undefined` means link-time symbol
resolution against NDK libraries; normal imported dynamic symbols remain UND.

The candidate has **not** been deployed or run on the board. The cleanup repair
and regression are included in the current source snapshot. The unchanged build
script still emits a historical `known_limitations` string saying the probe is
expected to fail on `d020ef5`; that hard-coded statement does not describe the
source hash verified above. Use the archived source hash and actual probe logs;
do not treat the old metadata sentence as a new test result. The script itself
is outside this repair's edit scope. No old candidate is overwritten. No image,
service, Git index, commit, push, GPS, or device configuration is changed by these
scripts.

## Provenance

- `include/bt_vendor_lib.h` is an **unmodified** copy of the archived AOSP
  `android-9.0.0_r61` header, with its complete Broadcom 2009–2012 copyright and
  Apache 2.0 notice retained. SHA-256:
  `5534d626961f9b0a8c7fdc0087492b792bafd1fdb521bcb750a3f4c8f53d098b`.
  Upstream identified by the original audit's `ref/sources.txt`:
  https://android.googlesource.com/platform/system/bt/+/android-9.0.0_r61/hci/include/bt_vendor_lib.h
- Header and original `test_vendor.c` / `mock_drv.c` inputs came from
  `/home/lxj/claude/6572/bridge_backups/products/products_btvendor_20261110_210734/`.
  The test and mock were expanded here; the official header was not edited.
- `shim_host/log/log.h` and `shim_android/log/log.h` are unchanged local stand-ins
  from `bridge_backups/bt_audit_20261110_205049/shim_host/` and `shim/` respectively.
  They are **not** represented as official AOSP headers. The Android shim calls
  NDK `__android_log_print`; the host shim prints diagnostics to stderr.
- `loader_fixture.c`, `arm_abi_checks.h`, and `run_host.sh` are local test support.
  No proprietary libraries or credentials belong in this directory.
