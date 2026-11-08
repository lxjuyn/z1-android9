# Z1 Mesa 26.1.1 GPU stack — files to drop into the system image

Source: Mesa 26.1.1 (`/home/lxj/claude/6572/mesa_src_26/mesa-mesa-26.1.1`, VERSION = 26.1.1,
tarball `mesa-26.1.1.tar.gz`, md5 `e86369c7df523d86490a5fa3f7131eb9`, gzip -t OK)
Build : `/home/lxj/claude/6572/mesa_android/lima-build-26-z1` (meson 1.12.1 + NDK r26d clang 17.0.2,
        Android armv7, API 28)
Target: Z1 / MT6572 / Mali-400 MP1 (`lima` DRM driver), Android 9 userspace

## Files in this directory

| local file | size (B) | sha256 | target path in the image | mode | SELinux label |
|---|---|---|---|---|---|
| `libGLES_mesa.so`   | 323,696      | `5af837b6…afd45` | `/vendor/lib/egl/libGLES_mesa.so` | 0644 | `u:object_r:same_process_hal_file:s0` |
| `libgallium_dri.so` | 17,417,404   | `4e5e2e92…b45b7` | `/vendor/lib/libgallium_dri.so`     | 0644 | `u:object_r:vendor_file:s0` |

`SHA256SUMS` holds the full hashes. Unstripped copies (for symbolising crashes) are in
`../z1_stage_26_unstripped/` and are **not** meant for the image.

**Two files only.** Mesa 26 removed everything the 24.0.9 bundle needed besides these:
no separate `libglapi.so`, no `/vendor/lib/dri/*_dri.so` symlink (see below).

## Why the shape changed vs. the 24.0.9 bundle

Mesa 26 refactored the DRI loader. This is not a packaging choice — the source no longer
contains the old mechanism:

1. **`src/egl/drivers/dri2/egl_dri2.c:dri2_create_screen()` now calls `driCreateNewScreen3()`
   directly.** There is no `dlopen()` of a megadriver anywhere in the EGL/Android path
   (`grep -rn dlopen src/egl src/gallium/frontends/dri` → one hit, and it is a comment;
   `loader_open_driver_lib()` is only called from `src/gbm/main/backend.c`, and gbm is
   disabled). The gallium drivers are
   statically linked into `libgallium_dri.so` and selected by name through
   `pipe_loader_drm.c:get_driver_descriptor()` → `lima_driver_descriptor`, which is a local
   symbol in the library we ship.
2. Therefore **`-Ddri-search-path` no longer exists** in `meson.options` (Mesa 24 had it;
   26 has only `dri-drivers-path`, an install path). Nothing looks at `/vendor/lib/dri` at
   runtime: `strings libGLES_mesa.so | grep /vendor/lib/dri` → **0**, and
   `/usr/local/lib/dri` → **0**. The existing dangling
   `/vendor/lib/dri/lima_dri.so -> gallium_dri.so` in v20 is inert and can stay.
3. **`libglapi` is now a static library** (`src/mesa/glapi/shared-glapi/meson.build` →
   `static_library('glapi')`), linked into `libgallium_dri.so`. There is no `libglapi.so`
   to ship and no `DT_NEEDED: libglapi.so` any more. The old 19.1.8
   `/vendor/lib/libglapi.so` (173 KB) is now unused; leaving it is harmless.
4. **`__driDriverGetExtensions_lima` no longer exists.** The symbol was moved to the new
   `src/gallium/targets/dril/` target, which meson only builds for
   `glx == 'dri' or platform_x11/xcb` (`src/meson.build:156-159`). On Android it is not
   built, and it is not needed: EGL resolves `driCreateNewScreen3` from
   `libgallium_dri.so` (exported, see below). `nm -D gallium_dri.so | grep
   __driDriverGetExtensions_lima` → **0**, by design.
5. **EGL and gallium are two DSOs**: `libEGL.so` (renamed here to `libGLES_mesa.so`) has
   `DT_NEEDED: libgallium_dri.so`. `libgallium_dri.so` *must* be reachable by soname from
   the loader's namespace; `/vendor/lib` is in `namespace.default.search.paths`
   (verified in the image's `/etc/ld.config.txt`), so `/vendor/lib/libgallium_dri.so` is
   the correct location (`/vendor/lib/egl` would **not** be searched).

## Build configuration (actual command line)

```
export PKG_CONFIG_PATH=/home/lxj/claude/6572/mesa_android/z1-pkgconf:\
/home/lxj/claude/6572/mesa_android/libdrm-install/lib/pkgconfig:\
/home/lxj/claude/6572/mesa_android/ndk-pkgconf

meson setup lima-build-26-z1 /home/lxj/claude/6572/mesa_src_26/mesa-mesa-26.1.1 \
  --cross-file=/home/lxj/claude/6572/mesa_android/mesa-arm32-z1-26.cross \
  -Dgallium-drivers=lima,softpipe -Dvulkan-drivers= -Dglx=disabled -Dplatforms=android \
  -Degl=enabled -Dgbm=disabled -Dllvm=disabled -Dgles1=disabled -Dgles2=enabled \
  -Dshared-glapi=enabled -Dzstd=disabled -Dvideo-codecs= -Dbuildtype=release \
  -Dandroid-stub=true -Dandroid-libbacktrace=disabled -Dplatform-sdk-version=28 \
  -Dandroid-strict=false -Dgallium-va=disabled
ninja -C lima-build-26-z1 -j8
```

Differences from the 24.0.9 recipe, forced by the 26.1.1 source:
* `swrast` no longer exists as a `gallium-drivers` choice (`meson.options:84-92`); the
  software driver is `softpipe`, which still produces the `swrast`/`kms_swrast` DRI entry
  paths internally (`meson.build:246-249`). Used `-Dgallium-drivers=lima,softpipe`.
* `-Ddri-search-path` removed (option deleted upstream).
* `-Dshared-glapi=enabled` is accepted but deprecated in 26 (the option is a no-op now).
* meson >= 1.4 is required by 26.1.1; the host's meson 1.3.2 was too old, so a venv with
  meson 1.12.1 was used (`mesa_android/meson-venv`). The venv's python must **not** be put
  in `PATH` — Mesa's meson.build then picks it and fails on missing `packaging`/`distutils`.
* libdrm 2.4.120 static (unchanged, `z1-pkgconf/libdrm.pc` → `libdrm-static-build/libdrm.a`);
  26.1.1 requires >= 2.4.109 for our driver set.
* `atrace_get_enabled_tags` shim (unchanged) plus **`atrace_init`** — new in 26 (see below).

### Two source/config patches required (both minimal, both documented here)

1. **`-Wl,--undefined-version`** added to `c_link_args` / `cpp_link_args` in
   `mesa-arm32-z1-26.cross`. `src/gallium/targets/dri/dri.sym.in` exports
   `_mesa_glapi_tls_Context` / `_mesa_glapi_tls_Dispatch`; those names do not exist when
   clang emits emulated TLS, and `ld.lld` treats an unmatched version-script symbol as a
   hard error (GNU ld only warns). This is exactly what lld's flag is for.
2. **`src/gallium/targets/dri/dri.sym.in`**: added the two real emutls symbols
   `__emutls_v._mesa_glapi_tls_Context` and `__emutls_v._mesa_glapi_tls_Dispatch` to the
   `global:` list. Without them the `local: *` clause hides the emutls control variables and
   Mesa's own `libGLESv2.so` (built because `-Dgles2=enabled`, not shipped) fails to link.
   Original file backed up in `bridge_backups/dri.sym.in_mesa26_orig_*`.
   On the device these two symbols are then exported from `libgallium_dri.so` — this matches
   what the 24.0.9 `libglapi.so` exported (`__emutls_v._glapi_tls_*`).

### atrace shim (runtime correctness fix)

Mesa's `src/android_stub/cutils_stub.cpp` defines `atrace_init`/`atrace_begin_body`/… only
for the *link*; at runtime Android 9's real `/system/lib/libcutils.so` is used instead.
Checked against the real v20 `/lib/libcutils.so`:

* exports `atrace_begin_body`, `atrace_end_body`, `atrace_setup`, … (so those undefined
  refs are fine), but
* does **not** export `atrace_init` (new reference in Mesa 26 `src/util/perf/cpu_trace.h:254`)
  nor `atrace_get_enabled_tags`.

`mesa_android/z1-shims/atrace_stub.c` was extended to define `atrace_init()` (no-op) next to
the existing `atrace_get_enabled_tags()` returning 0, and the archive was relinked into both
DSOs. Verified: `nm libGLES_mesa.so | grep atrace` → `t atrace_get_enabled_tags`,
`t atrace_init`, only `U atrace_begin_body` / `U atrace_end_body` remain (present in the
device's libcutils).

## Post-processing applied to the files above

* `libEGL.so` → `libGLES_mesa.so` with `patchelf --set-soname libGLES_mesa.so`
  (meson names it `libEGL.so`; bionic would otherwise let `DT_NEEDED: libEGL.so` from
  `/system/lib/libGLESv2.so` bind to the vendor driver — same reasoning as the 24.0.9 bundle).
* `patchelf --remove-rpath` on both files (build-tree/host RPATH is meaningless on device;
  the leftover string in `.dynstr` is inert).
* `llvm-strip --strip-unneeded`.

## Verification on the exact files in this directory

```
$ readelf -d libGLES_mesa.so      # (NEEDED/SONAME; RUNPATH gone)
 0x00000001 (NEEDED)  [libgallium_dri.so]
 0x00000001 (NEEDED)  [libcutils.so] [libhardware.so] [liblog.so] [libnativewindow.so]
 0x00000001 (NEEDED)  [libsync.so] [libm.so] [libdl.so] [libc.so]
 0x0000000e (SONAME)  [libGLES_mesa.so]

$ readelf -d libgallium_dri.so
 0x00000001 (NEEDED)  [libcutils.so] [liblog.so] [libsync.so] [libm.so] [libz.so] [libdl.so] [libc.so]
 0x0000000e (SONAME)  [libgallium_dri.so]
```

* `nm -D --defined-only libGLES_mesa.so | awk '{print $3}' | grep -c '^egl'` → **44**
  (identical name-for-name to the 24.0.9 bundle, which was verified against the shipped
  19.1.8 `libGLES_mesa.so`; `diff` of the two sorted lists is empty).
* `… | grep -c '^gl'` → **0** — by design; GL entry points are served through
  `eglGetProcAddress` → `_mesa_glapi_get_proc_address` (exported by `libgallium_dri.so`,
  1639 local `_dispatch_stub_*` functions; `glClear`, `glDrawArrays`, `glTexImage2D`,
  `glCreateShader`, `glShaderSource`, `glGetString` all present in its name table).
* `strings libGLES_mesa.so | grep -c /usr/local/lib/dri` → **0**;
  `… | grep -c /vendor/lib/dri` → **0** (no DRI search path exists in 26; see above).
* `nm -D --defined-only gallium_dri.so | grep -c __driDriverGetExtensions_lima` → **0**
  (symbol removed upstream; not built on Android — see point 4 above).
* `driCreateNewScreen3`, `driCreateContextAttribs`, `driDestroyScreen`, … are exported
  (`driCreate*` in the version script); `lima_driver_descriptor` is present in the DSO.
* **Undefined-symbol audit against the real v20 image.** All 34 libraries of the transitive
  `DT_NEEDED` closure were extracted from `out/z1_v20_audio_system/system_v20.img` with
  debugfs and their dynamic symbol tables compared:
  `libGLES_mesa.so` 179 undefined / **0 unresolved**,
  `libgallium_dri.so` 261 undefined / **1 unresolved**, and that one is the **weak**
  `_ZTH23_mesa_glapi_tls_Context` (same benign weak TLS-init reference the 24.0.9 bundle
  had). Every `NEEDED` soname was found in the image: `libcutils.so`, `libhardware.so`,
  `liblog.so`, `libnativewindow.so`, `libsync.so`, `libm.so`, `libdl.so`, `libc.so`,
  `libz.so`.
* `libgallium_dri.so` contains `Mesa 26.1.1`, `lima_screen.c`, `lima_drm_winsys.c`, and
  softpipe (the `swrast` fallback driver).
* No symbol versioning is emitted (`--default-symver` is not applied by lld here), so
  bionic sees plain unversioned references between the two DSOs.

## Build timing

* meson setup: a few seconds
* full `ninja -j8`: **27.7 s** wall (1034 targets, 16-core host)
* relink after the atrace stub change: 0.8 s

## Install (debugfs on the ext4 system image)

`out/z1_v20_audio_system/system_v20.img` is system-as-root (`/lib` *is* `/system/lib`),
free space ≈ 28 172 blocks × 4096 = **115 MB**; the two files need 17.7 MB.

```sh
IMG=/home/lxj/claude/6572/out/z1_v20_audio_system/system_v20.img
debugfs -w -R "rm /vendor/lib/egl/libGLES_mesa.so" $IMG 2>/dev/null
debugfs -w -R "write z1_stage_26/libGLES_mesa.so   /vendor/lib/egl/libGLES_mesa.so" $IMG
debugfs -w -R "write z1_stage_26/libgallium_dri.so /vendor/lib/libgallium_dri.so"     $IMG
debugfs -w -R "ea_set /vendor/lib/egl/libGLES_mesa.so security.selinux u:object_r:same_process_hal_file:s0" $IMG
debugfs -w -R "ea_set /vendor/lib/libgallium_dri.so   security.selinux u:object_r:vendor_file:s0"          $IMG
# mode must be 0644
debugfs -w -R "sif /vendor/lib/egl/libGLES_mesa.so mode 0100644" $IMG
debugfs -w -R "sif /vendor/lib/libgallium_dri.so   mode 0100644" $IMG
```

`write` into an existing path does not replace the inode, so the `rm` first matters.
Note: `out/z1_v20_audio_system/system_v20.img` is v20 as of 2026-11-08; substitute whatever
image is current when installing.

## What to look for on the device

* `logcat -s libEGL` / dmesg: `loaded /vendor/lib/egl/libGLES_mesa.so`.
* If you see `load_driver(...): undefined symbol ...`, the loader falls back to
  `libEGL_swiftshader.so`; check the atrace/libgallium NEEDED resolution first.
* `logcat` should now show no `gp error irq state=4000 status=3`
  (`LIMA_GP_IRQ_PLB_INVALID_CMD`), which is what the Alpine/musl 26.1.1 build proved on this
  exact board (`mainline_recon/z1_lima_offscreen_command_submit_20260717_231356.log`).
* Forcing software rendering is different in 26: the driver is picked by the DRM device's
  driver name against the compiled-in `driver_descriptors[]` table, not by choosing a
  `*_dri.so` file. `libgallium_dri.so` does contain softpipe, but there is no
  `/vendor/lib/dri/*.so` to swap in.

## Caveats / risks (honest list)

* **Not yet run on the board.** Everything above is static analysis of the built ELF files
  against the v20 image; the only device-side evidence for Mesa 26.1.1 on this SoC is the
  Alpine/musl offscreen run from 2026-07-17.
* `libgallium_dri.so` is 17.4 MB (24.0.9 was 12.0 MB). Loaded by SurfaceFlinger/hwui at
  boot; the image has room, but expect a slightly larger RSS/page-cache footprint on a
  1 GB device.
* The EGL `android` platform still calls `hw_get_module("gralloc")` during display creation;
  v20 ships only `/vendor/lib/hw/gralloc.default.so`, same as with 19.1.8/24.0.9, so this is
  not a new dependency — but if display creation fails, check gralloc first.
* `libsync.so` is a `DT_NEEDED` of both DSOs; it exists in v20 (`/lib/libsync.so`). If a
  future image drops it, the load fails.
* If `/vendor/lib/egl/libGLES_mesa.so` is ever edited by hand, keep SONAME
  `libGLES_mesa.so`, and keep `libgallium_dri.so` reachable from `/vendor/lib`.
