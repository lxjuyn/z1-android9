# Z1 shared storage on Linux 7

The M5 live capture `mainline_recon/z1_android9_acm_20261003_142934_898076.log`
contains new sdcard processes aborting at `failed to sdcardfs_setup`, because
Linux 7 has neither Android sdcardfs nor esdfs. Pie's sdcard has removed its
FUSE implementation; `ro.sys.sdcardfs=false` alone cannot repair it.

## Source and licensing

The complete FUSE daemon is restored from **AOSP android-8.1.0_r81**:
https://android.googlesource.com/platform/system/core/+/android-8.1.0_r81/sdcard/

Fixed upstream tree: `a4475d388f53898f8e0c728385e43aac2335e83f`.
Original Apache 2.0 copyright/license headers remain in all source files.
`UPSTREAM.json` records the original blob IDs and SHA256 values, before local
adaptation. It does not claim the adapted files are byte-identical upstream.

Files restored: `sdcard.cpp`, `fuse.cpp`, `fuse.h`. Local changes:

- Remove sdcardfs selection and mounting code: this is a FUSE-only device module.
- Accept Pie's `-i` default_normal option, applying the user-qualified GID to
  the default view instead of the legacy global sdcard_rw GID. The default
  view's mode mask and Android-directory restrictions remain unchanged.
- Zero the FUSE INIT reply, including reserved fields. Negotiate protocol 7.15,
  which upstream Linux supports; no Android-only kernel extension is required
  for ordinary filesystem operations.
- Emit the identifying log string `Z1 legacy FUSE`.

## Product and vold integration

`sdcard.z1_fuse` installs as `/system/bin/sdcard`, with
`LOCAL_OVERRIDES_MODULES := sdcard`; only the Z1 product selects this module.
The real parent `device/jiabo/Android.mk` explicitly registers its Make module.
Dependencies are Pie's libbase/libcutils/libminijail/libpackagelistparser and
normal C/C++ runtime libraries. The existing `sdcard_exec` label and sdcardd
transition therefore apply; no alternate unrestricted execution domain is used.

`ro.vold.z1_fuse=true` gates a small EmulatedVolume argument change. Standard
products continue passing `-G -i` to standard Pie sdcard. Z1 passes `-m -w -i`
and media_rw UID/GID to this backend. PublicVolume already passes only the
legacy-supported `-u/-g/-U/-w` options and needs no change.

**Known missing capability:** sdcardfs `-G` rewrites ownership on the lower
filesystem, including external app/cache quota GIDs. Legacy FUSE does not
implement this lower-file quota ownership; vold deliberately does not request
that feature and logs the limitation. This does not promise correct Android
external-storage per-app quota accounting. The daemon does not silently accept
or discard `-G`: passing that option directly fails argument validation.

FUSE keeps three independently mounted default/read/write runtime views,
AOSP masks, per-user GIDs, packages.list-derived application owner UIDs,
Android-directory access restrictions, and daemon privilege dropping to
media_rw. It does not replace storage with an unrestricted bind mount or
chmod the source tree to grant app access. OBB sharing remains the standard
legacy AOSP graft behavior. This patch does not format /data or edit fstab.

The required kernel module is CONFIG_FUSE_FS=m, staged and loaded before
vold attempts mounting shared storage. `/dev/fuse` must exist. The existing
Pie sdcardd policy already permits the fuse device and sdcard-type mounts;
bringup remains permissive, not a claim of enforcing-policy validation.

Vold still waits for the runtime/write mount's device number to change and
returns its normal mount failure/timeout. This patch does not turn failed
mounts into success or suppress failures.

## Validation and board gates

Both sources pass direct ARM32 Pie clang object compilation with the existing
sdcard flags and libpackagelistparser headers. No Soong regeneration, full
system build, flash, or filesystem operation was performed by this subtask.
Linking/install collision checks are handled in the coordinated product build.

The next live board test must show a resident sdcard FUSE daemon, three FUSE
runtime mounts, a usable /storage/emulated/0, no sdcardfs_setup abort and no
vold shared-storage timeout. Test app write permission changes, isolation
between two user IDs, per-app Android/data access, packages.list updates,
rename/readdir, disconnect/unmount/restart, and cross-user default-view access
before declaring shared storage complete. Quota accounting remains a documented
legacy limitation, not a verified capability.

The bounded host permission matrix directly includes production fuse.cpp and
links the real libcutils multiuser helpers. Run `tests/run_host.sh` from any
working directory. It checks users 0/10, all three runtime views, default_normal
on/off, package owners, read/write masks, cross-user/app denial and package-owner
refresh. It does not mount FUSE or claim real SELinux/kernel access validation.

## M7 audit: real daemon mount and I/O test

`tests/run_mount_qemu.sh` runs the installed ARM32 daemon through
qemu-arm-static inside private mount and PID namespaces, with a disposable
chroot, read-only installed system tree, and test-only data/runtime directories.
It needs root, qemu-arm-static and a host `/dev/fuse`; it stops within 30 seconds.
No device partitions are accessed. Run `sudo tests/run_mount_qemu.sh` from this
directory, or pass an installed system tree as its first argument.

On 2026-10-03 this test passed three real FUSE mounts and kernel INIT, lookup,
write/read roundtrip, rename, readdir and unlink using the M6 installed daemon.
Runtime view modes/GIDs were default 0771/1015, read 0750/9997, write 0770/9997.
Non-root UIDs 10001 and 10002 with supplementary GID 9997 both passed ordinary
shared-root file write-view/read-view roundtrips. Read-view modification and
creation were denied. Both UIDs without external-storage groups were denied
write-view read/write/create and read-view read access. These are legacy AOSP
external-storage permission semantics: authorized apps can access ordinary
shared files; this does not claim Android scoped-storage isolation.
The production permission matrix also passed all 75 assertions. Evidence:
`mainline_recon/m7_fuse_live_host_20261003.log`. These results validate the actual
daemon on the host kernel, not Linux 7 on Z1 or SELinux enforcement. Board
mount timing, package refresh, Android-framework namespace/group integration and unmount/restart still
require live validation. No further production storage fix was justified by
this audit.
