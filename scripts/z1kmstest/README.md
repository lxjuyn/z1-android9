# z1kmstest — mediatek-drm atomic/KMS test tool for the Z1 (MT6572)

`z1kmstest` is a standalone ARM (armv7) tool that drives `/dev/dri/card1`
(mediatek-drm) **directly** through the DRM atomic/KMS API, with no Android
framework, no SurfaceFlinger and no drm_hwcomposer in the path. It exists to
answer one class of question from a serial shell:

> does multi-plane OVL composition actually work on this board — positions,
> per-plane alpha, blend modes, pixel formats, z-order, and explicit plane
> disable?

Everything the tool prints is a **kernel verdict** (validation accepted /
commit accepted) plus a `look for on the panel:` line describing what a human
should see. There is no display readback path on this board (no DP/HDMI, the
DSI link is owned by LK), so visual results are reported for a human to judge,
never machine-asserted.

Files:

| file | role |
|---|---|
| `z1kmstest.c` | the whole tool, one C translation unit, ~3180 lines |
| `build.sh` | NDK cross build + post-build ELF/NEEDED/symbol checks |
| `README.md` | this file |

---

## 1. Build

```sh
cd /home/lxj/claude/6572/z1a9_gpt/scripts/z1kmstest
./build.sh /tmp/z1kmstest          # -> /tmp/z1kmstest/z1kmstest
```

Defaults (all overridable by environment variable):

| var | default |
|---|---|
| `NDK` | `/home/lxj/claude/6572/android-ndk-r26d` |
| `API` | `28` (Android 9) |
| `CC` | `$NDK/.../bin/armv7a-linux-androideabi28-clang` |
| `LIBDRM_INC` | `/home/lxj/claude/6572/mesa_android/libdrm-install/include` |
| `LIBDRM_A` | `/home/lxj/claude/6572/mesa_android/libdrm-static-build/libdrm.a` |
| `STRIP` | `1` |

The build is deliberately minimal on dependencies: plain C, `libdrm.a` linked
statically, so the only `NEEDED` entries are `libc.so` and `libdl.so` (the
latter comes from the NDK startup objects, no `dl*` call is made).

`-Wl,--export-dynamic` is used on purpose: it puts `main` and the statically
linked libdrm entry points into `.dynsym` so `nm -D` can *prove* after
stripping that libdrm was resolved at link time. It costs ~9 KiB.

For a musl/glibc rootfs (Alpine) the same source builds unchanged:

```sh
CC=arm-linux-gnueabihf-gcc LDFLAGS=-static ./build.sh /tmp/z1kms-glibc
```

---

## 2. Prerequisites on the board

Run from the **serial shell** (`console=ttyMT0,921600n8` or the C3 bridge).

1. **Be root.** `run` needs `CAP_SYS_ADMIN` to become the DRM master; `regs`
   needs uid 0 for `/dev/mem`. `probe` needs no privileges, only read access
   to the node (on the board `/dev/dri/card1` is normally `root:graphics
   0660`, so being root also covers that).

2. **Nothing else may hold DRM master.** This is the single most common
   reason for a cryptic failure. `DRM_IOCTL_MODE_ATOMIC` is one of the few
   ioctls declared `DRM_MASTER`:

   ```
   fork_linux/drivers/gpu/drm/drm_ioctl.c:701
       DRM_IOCTL_DEF(DRM_IOCTL_MODE_ATOMIC, drm_mode_atomic_ioctl, DRM_MASTER),
   fork_linux/drivers/gpu/drm/drm_ioctl.c:607-610
       if (unlikely((flags & DRM_MASTER) &&
                    !drm_is_current_master(file_priv)))
               return -EACCES;
   ```

   `drm_setmaster_ioctl()` never evicts an existing master — it returns
   `-EBUSY` (`drivers/gpu/drm/drm_auth.c:262-263`), so z1kmstest trying to
   take master can never disturb a running compositor. But it also means that
   if SurfaceFlinger / `z1hwc2` is running, every commit would return
   `-EACCES`. Stop the display stack first:

   ```sh
   stop
   setprop ctl.stop surfaceflinger
   pkill -f z1hwc2
   ```

3. **Wait for the node.** mediatek-drm is a component master (it binds once
   OVL + RDMA + DSI have all probed), so `/dev/dri/card1` appears ~33 s after
   boot. `--wait` (default 40 s) polls for it.

4. **Optional but recommended — stop /dev/kmsg rate limiting.**
   Userspace `/dev/kmsg` writes are rate-limited to 10 records per 5 s by
   default (`kernel/printk/printk.c` `devkmsg_open()` →
   `ratelimit_default_init()`; `include/linux/ratelimit_types.h:9-10`). To
   keep *every* line in `dmesg`:

   ```sh
   echo on > /sys/module/printk/parameters/devkmsg_log
   ```

   The serial console (stdout) is never rate-limited, so you can also just
   read the shell output. Each line is written to `/dev/kmsg` as exactly one
   `write()` of at most 511 bytes, prefixed `z1kms: `.

5. **Push the binary** to any directory on the board (it is fully static
   except libc/libdl, so no extra `.so` needs to be present).

---

## 3. Commands

### 3.1 `probe` — enumerate the KMS state

```sh
z1kmstest probe --device /dev/dri/card1 --wait 40
```

Expected shape of the output:

```
==== z1kmstest probe ====
node /dev/dri/card1 fd=3
driver=mediatek date=... desc=...
cap DRM_CLIENT_CAP_UNIVERSAL_PLANES -> 0 (ok)
cap DRM_CLIENT_CAP_ATOMIC -> 0 (ok)
resources: crtcs=1 encoders=1 connectors=1 fbs=N
crtc[0] id=31
encoder[0] id=32 type=0x... crtc_id=31 possible_crtcs=0x1
connector[0] id=33 type=16 status=connected encoder_id=32 modes=1 encoders=1
  mode[0] "240x320" 240x320@... clock=... htotal=298 vtotal=337 flags=0x...
connector 33 prop CRTC_ID      id=34
crtc 31 prop MODE_ID           id=35 (blob)
crtc 31 prop ACTIVE            id=36
crtc 31 prop OUT_FENCE_PTR     id=...
crtc 31 prop GAMMA_LUT         id=...
planes: count=4
plane[0] id=.. type=PRIMARY(1) possible_crtcs=0x1 crtc_id=.. fb_id=.. zpos=0 (immutable)
  formats(11): XR24(0x34325258) AR24(0x34325241) BX24(0x34324242) ...
  alpha id=.. cur=65535   pixel_blend_mode id=.. cur=0(Pre-multiplied)
  blend enums: None=2 Pre-multiplied=0 Coverage=1
  props: fb=.. crtc=.. cx=.. cy=.. cw=.. ch=.. sx=.. sy=.. sw=.. sh=..
plane[1] ... type=OVERLAY(0)   zpos=1 (immutable)
plane[2] ... type=OVERLAY(0)   zpos=2 (immutable)
plane[3] ... type=CURSOR(2)    zpos=3 (immutable)
plane order, bottom -> top (by immutable zpos):
  layer 0: plane id=.. PRIMARY zpos=0
  ...
screen: connector 33 "240x320" 240x320
RESULT probe: PASS -- ...
```

What to check here:

* `type=PRIMARY/OVERLAY/OVERLAY/CURSOR` and `zpos=0,1,2,3` — this is
  `mtk_crtc_plane_type()` + `drm_plane_create_zpos_immutable_property()`.
  **The list order is the compositing order and cannot be changed from
  userspace.**
* `formats(11)` — exactly the `mt6572_ovl_driver_data.formats` list.
* `blend enums: None=2 Pre-multiplied=0 Coverage=1` — matches
  `include/drm/drm_blend.h:30-32`; the tool resolves these at runtime rather
  than hardcoding them.
* `crtc .. prop MODE_ID ... (blob)` — **the property is called `MODE_ID`, not
  `MODE`** (see §6.7). If you ever see `MODE` there, the kernel is older.
* `connector .. modes=1` and a 240x320 mode — if it says `modes=0` the panel
  is not bound and nothing else will work.

Exit codes: `0` ok, `3` node missing/unopenable, `4` no atomic support,
`5` resources incomplete.

`--device auto` scans `/dev/dri/card0..card9` and picks the first non-lima
node that accepts `DRM_CLIENT_CAP_ATOMIC`.

### 3.2 `run` — the sub-test suite

```sh
# everything, 1 s per frame
z1kmstest run --mode all

# one test
z1kmstest run --mode layers

# validate every configuration without touching the panel (recommended first)
z1kmstest run --mode all --test-only

# leave the last frame up while you look at the panel, then Ctrl-C
z1kmstest run --mode layers --hold

# same, but exit on its own after 30 s
z1kmstest run --mode layers --hold --hold-seconds 30

# force the page-flip event path (NONBLOCK | PAGE_FLIP_EVENT + poll)
z1kmstest run --mode basic --event
```

Options: `--device`, `--wait`, `--mode`, `--delay MS` (default 1000),
`--test-only`, `--event`, `--hold`, `--hold-seconds N`.

**Every real commit is preceded by a `DRM_MODE_ATOMIC_TEST_ONLY` pass.** The
kernel runs all `->atomic_check()` callbacks but touches no hardware, so a
rejection there is a pure validation verdict with zero risk. The log therefore
always shows the request, then `validation OK`, then the real commit result.
`--test-only` stops after the validation pass.

`run` tears the display down safely at the end: it first commits an
"all planes off" request (`FB_ID=0` + `CRTC_ID=0` for all four planes) and only
then calls `RmFB` / `munmap` / `DESTROY_DUMB`, so the OVL never scans freed DMA
memory. The same teardown happens at the end of each sub-test. `--hold` skips
it for the last sub-test only — note that the dumb buffers die with the
process, so the picture survives only while `z1kmstest` is alive.

#### What each mode proves

| mode | what it proves | what you should see |
|---|---|---|
| **basic** | A full modeset from scratch works at all: connector `CRTC_ID` + CRTC `MODE_ID` blob + `ACTIVE=1` + `ALLOW_MODESET`, on a `240x320 RGB565` full-screen buffer. Also prints whether the page-flip event arrives. | 4 quadrants: red top-left, green top-right, blue bottom-left, white bottom-right. |
| **layers** | 2 → 3 → 4 simultaneous planes with per-layer `CRTC_X/Y` positioning, overlapping rectangles, mixed formats (`XRGB8888` background, `XRGB8888`, `RGB565`, `RGB888` layers), and the immutable z-order actually stacking correctly. | A dark grid background, then red, then green overlapping red, then blue overlapping green — bottom→top = plane0→plane3. |
| **formats** | Every format the plane advertises survives `AddFB2` + a real commit at 64×64. | A 64×64 solid square at (40,40) in each format. Channel order: `RGB888` stores bytes B,G,R in memory; `BGR888` stores R,G,B. |
| **alpha** | Per-plane alpha and all three blend modes on an `ARGB8888` top layer over a black/white checkerboard: `Pre-multiplied`, `Coverage`, `None`, then plane alpha `0x0000` (invisible) and `0xffff` (opaque). | Blending differences between modes; the square vanishes at alpha 0 and is flat opaque red at 0xffff. |
| **offscreen** | What the kernel really enforces about geometry: partial off-screen clipping, fully off-screen planes, tiny destination rectangles, and the minimum framebuffer size. | Clipped squares at the corners; the fully-off-screen layer must not appear at all. |
| **disable** | The stale-layer rule: commit 4 layers, then commit only 2 **without** listing planes 2/3 (they keep scanning out the old buffers), then commit again with them explicitly disabled (`FB_ID=0`, `CRTC_ID=0`). | Step 2: green and blue squares are still there. Step 3: they disappear. |

The `disable` mode is the one that documents a real driver behaviour rather
than a wish: because `mtk_crtc_ddp_config()` skips any plane whose
`pending.config` is clear (`mtk_crtc.c:516-517`) and `pending.config` is only
set from `mtk_plane_atomic_update()` / `atomic_disable()`
(`mtk_plane.c:271-273,296`), a plane that is simply omitted from a commit keeps
its OVL `SRC_CON` bit and its old buffer address. **You must disable unused
planes explicitly with both `FB_ID=0` and `CRTC_ID=0` in the same request.**
Zeroing only one of the two is rejected with `-EINVAL` by
`drm_atomic_plane_check()` ("CRTC set but no FB" / "FB set but no CRTC").

#### Exit codes

| code | meaning |
|---|---|
| 0 | everything the tool checked passed |
| 1 | at least one sub-test was rejected by the kernel |
| 2 | `regs`: `/dev/mem` unusable |
| 3 | DRM node missing / cannot open |
| 4 | node has no universal-planes / atomic support (lima?) |
| 5 | KMS resources incomplete (no CRTC / no connector with a mode / no planes) |
| 6 | bad command line |
| 7 | a dumb buffer or `AddFB2` failed |
| 8 | nothing ever reached the panel (the first modeset commit failed) |
| 9 | could not obtain DRM master: not root (`EACCES`) or another process holds it (`EBUSY`) |

### 3.3 `regs` — read-only OVL register dump

```sh
z1kmstest regs                 # base 0x14007000, 0x300 bytes, decoded
z1kmstest regs --raw           # + full hex dump
z1kmstest regs --size 0x100 --raw
z1kmstest regs --base 0x14008000   # override (0x14008000 is RDMA0, see below)
```

Decoded registers (offsets are the driver's own constants, so the dump and the
driver can never drift apart):

| offset | name | source |
|---|---|---|
| `0x0004` | `OVL_INTEN` | `mtk_disp_ovl.c:23` |
| `0x0008` | `OVL_INTSTA` | `mtk_disp_ovl.c:31` |
| `0x000c` | `OVL_EN` | `mtk_disp_ovl.c:38` |
| `0x0014` | `OVL_RST` | `mtk_disp_ovl.c:39` |
| `0x0020` | `OVL_ROI_SIZE` (`h<<16\|w`) | `mtk_disp_ovl.c:40` |
| `0x0024` | `OVL_DATAPATH_CON` | `mtk_disp_ovl.c:41` |
| `0x0028` | `OVL_ROI_BGCLR` | `mtk_disp_ovl.c:45` |
| `0x002c` | `OVL_SRC_CON` (per-layer enable bits) | `mtk_disp_ovl.c:46` |
| `0x30+0x20n` | `OVL_CON(n)` | `mtk_disp_ovl.c:47` |
| `0x38+0x20n` | `OVL_SRC_SIZE(n)` (`h<<16\|w`) | `mtk_disp_ovl.c:48` |
| `0x3c+0x20n` | `OVL_OFFSET(n)` (`y<<16\|x`) | `mtk_disp_ovl.c:49` |
| `0x40+0x20n` | `OVL_ADDR(n)` (`DISP_REG_OVL_ADDR_MT2701 = 0x0040`) | `mtk_disp_ovl.c:56,63` |
| `0x44+0x20n` | `OVL_PITCH(n)` | `mtk_disp_ovl.c:52` |
| `0xc0+0x20n` | `OVL_RDMA_CTRL(n)` | `mtk_disp_ovl.c:54` |

**The OVL base is `0x14007000`, not `0x14008000`.** `0x14008000` is RDMA0:

```
fork_linux/arch/arm/boot/dts/mediatek/mt6572.dtsi:581-587
        ovl: ovl@14007000 {
                compatible = "mediatek,mt6572-disp-ovl";
                reg = <0x14007000 0x1000>;
        };
fork_linux/arch/arm/boot/dts/mediatek/mt6572.dtsi:589-595
        rdma0: rdma@14008000 {
                compatible = "mediatek,mt6572-disp-rdma";
                reg = <0x14008000 0x1000>;
        };
```

The compiled `mt6572-z1.dtb` contains both `0x14007000` (offset 14976) and
`0x14008000` (offset 15124) as big-endian words, so the DT is the authority.
`mt6572-z1.dts` does not override the node, and the OVL node has no
`status` property, so it is enabled.

#### `/dev/mem` and `CONFIG_STRICT_DEVMEM`

Kernel config findings:

```
fork_linux/.config:2248   CONFIG_DEVMEM=y
fork_linux/.config:7364   CONFIG_STRICT_DEVMEM=y
fork_linux/.config:7365   CONFIG_IO_STRICT_DEVMEM=y
```

`CONFIG_STRICT_DEVMEM=y` does **not** block this window. With
`CONFIG_GENERIC_LIB_DEVMEM_IS_ALLOWED=y` (`.config:7152`),
`lib/devmem_is_allowed.c` returns 0 only for `iomem_is_exclusive()` ranges and
for pages where `page_is_ram()` is true; `0x14007000` is neither, so MMIO reads
are allowed. `drivers/char/mem.c` `mmap_mem_prepare()` gates mmap through
`range_is_allowed()` → `devmem_is_allowed()` (mem.c:350), and the mmap offset
must be page-aligned (`0x14007000` is).

So the correct message when `regs` fails is *not* "turn off
CONFIG_STRICT_DEVMEM" — the tool prints the accurate diagnosis instead:

* `/dev/mem` open fails with `EACCES/EPERM` → run as root.
* `/dev/mem` missing → the kernel was built without `CONFIG_DEVMEM`.
* mmap fails → the range is reserved `IORESOURCE_EXCLUSIVE` or lies in RAM.

`regs` exits `2` in all of those cases.

> **Hazard.** This reads an MMIO block. If the DIS power domain or the OVL
> clock is gated at that moment, the read can hang the AXI bus and the SoC will
> reset. Read the registers while the display is actually scanning out — the
> easiest way is to run `z1kmstest run --hold --hold-seconds 120` in one shell
> and `z1kmstest regs --raw` in another.

---

## 4. Interpreting failures

Every rejection is reported as `-<errno> (<strerror>)` immediately followed by
a `-> ` line decoding what that errno means **for this driver**:

| errno | real meaning here | typical cause |
|---|---|---|
| `-ERANGE` | **scaling**: `SRC_W`/`SRC_H` (16.16) must equal `CRTC_W`/`CRTC_H` in pixels | you asked for a scaled plane. `DRM_PLANE_NO_SCALING` is used for both min and max scale (`mtk_plane.c:255-259`), so `src_size == dst_size` is mandatory. Also used for CRTC coordinate overflow. |
| `-EINVAL` | unsupported pixel format or plane, FB smaller than `min_width/min_height`, bad object/property id, `CRTC_ID` without `FB_ID` (or the reverse), or a modeset attempted without `ALLOW_MODESET` | check the `formats(<n>)` list from `probe`, check that the FB is at least 64×64, check that a plane being disabled has **both** ids zeroed. |
| `-ENOSPC` | **invalid source coordinates**: `SRC_X`/`SRC_Y`/`SRC_W`/`SRC_H` lie outside the framebuffer (`drm_atomic.c:770-781`) | asking for a crop bigger than the buffer. **This is not a plane-count error** on this kernel — this driver has no CRTC `atomic_check`, and there are only 4 planes, so "too many layers" can never even be submitted. |
| `-EACCES` | `DRM_IOCTL_MODE_ATOMIC` needs DRM master (`drm_ioctl.c:701`); or a plane's `CRTC_ID` did not resolve (`drm_atomic_uapi.c:557`) | stop SurfaceFlinger / `z1hwc2`; run as root. |
| `-EBUSY` | another process already holds DRM master (`drm_auth.c:262-263`) | `stop` the framework first. |
| `-ENOENT` | unknown object / property id | a property was looked up by the wrong name — the classic one is `MODE` instead of `MODE_ID`. |

`-ENOSPC` deserves emphasis because it is widely mis-documented: in
`drivers/gpu/drm/drm_atomic.c` the *only* `-ENOSPC` is the source-coordinate
check at line 780, and `-ENOSPC` appears nowhere in
`drivers/gpu/drm/mediatek/`.

Two more things to know before blaming the tool:

* A plane that clips to **nothing** is accepted and silently not shown.
  `drm_atomic_helper_check_plane_state()` clips `dst` against the CRTC rect
  (`drm_atomic_helper.c:942-945`) and `mtk_plane_atomic_update()` turns an
  invisible plane into `mtk_plane_atomic_disable()` (`mtk_plane.c:289-291`).
  The `offscreen/fully-outside` case asserts exactly this.
* A 1×1 or 8×8 **framebuffer** cannot exist on this driver:
  `mtk_drm_drv.c:528-529` sets `min_width = min_height = 64` and
  `drm_internal_framebuffer_create()` rejects smaller ones with `-EINVAL`
  (`drm_framebuffer.c:267-275`). An 8×8 **destination rectangle** is fine —
  those are different checks. `offscreen` covers both.

---

## 5. How the first commit is constructed (and why)

`mtk_dsi.c:879-880` has `z1_keep_lk_dsi` (default **true**) and
`mtk_dsi_poweron()` returns immediately (`mtk_dsi.c:961-964`, "route B --
keeping LK video stream"), i.e. Linux never reconfigures the DSI: it keeps
whatever LK left behind. The first commit therefore has to look like a real
modeset, exactly like the HWC2 recipe in `z1hwc2.cpp:823-835`:

```
connector CRTC_ID = <crtc>
crtc      MODE_ID = <fresh property blob made from the connector's mode>
crtc      ACTIVE  = 1
flags    |= DRM_MODE_ATOMIC_ALLOW_MODESET
```

After that, plain plane-only commits are enough (the tool still retries once
with `ALLOW_MODESET` and says so if the kernel demands it).

### Commit style: blocking, with an explicit `--event` mode

The default is a **blocking** commit (`drmModeAtomicCommit` without
`NONBLOCK`), and this is a deliberate choice:

* `drm_atomic_helper_commit_tail_rpm` (`mtk_drm_drv.c:42`) plus
  `drm_atomic_helper_wait_for_vblanks()` (`drm_atomic_helper.c:1883-1927`)
  already wait for the next vblank inside the ioctl, so a blocking commit
  proves the flip was sampled by ~100 ms later without any event plumbing.
* The non-blocking alternative requires `DRM_MODE_PAGE_FLIP_EVENT`, and
  `mtk_crtc_atomic_begin()` arms that with
  `WARN_ON(drm_crtc_vblank_get(crtc) != 0)` (`mtk_crtc.c:856-866`). If
  `mtk_crtc_atomic_enable()` bailed out before `drm_crtc_vblank_on()` — quite
  possible on this board where the DIS power domain and the DSI are not fully
  under Linux control — that is a kernel WARN plus a vblank refcount imbalance
  in `mtk_crtc_finish_page_flip()` (`mtk_crtc.c:96-107`). Not something a
  diagnostic tool should provoke by default.

`--event` opts into `DRM_MODE_ATOMIC_NONBLOCK | DRM_MODE_PAGE_FLIP_EVENT` plus
`poll()` + `drmHandleEvent()` with a 2 s timeout, and reports whether the
event actually arrived. `basic` always does one `--event`-style commit after
the modeset, so you get the "does vblank delivery work?" answer for free.

`drmHandleEvent()` in this libdrm was checked by disassembly: it does the
`read()` itself and dispatches on `evctx->version` (`>=3` prefers
`page_flip_handler2`, `==2` uses `page_flip_handler`), so the tool sets
`version = DRM_EVENT_CONTEXT_VERSION` and supplies both handlers.

---

## 6. Kernel facts this tool was built against

All verified by reading `/home/lxj/claude/6572/fork_linux`; re-check with the
given `file:line`.

| # | fact | evidence |
|---|---|---|
| 6.1 | 4 planes, `plane[0]=PRIMARY`, `plane[1..2]=OVERLAY`, `plane[3]=CURSOR` | `mtk_disp_ovl.c:701-712` (`layer_nr = 4`), `mtk_crtc.c:934-949`, `mtk_crtc.c:951-962` |
| 6.2 | `zpos` is immutable and equals the plane index; plane list order is compositing order | `mtk_plane.c:340` `drm_plane_create_zpos_immutable_property(plane, plane_idx)` |
| 6.3 | `alpha` and `pixel blend mode` properties exist; hardware alpha is 8-bit | `mtk_plane.c:354,358-362`; `mtk_disp_ovl.c:93-94` (`OVL_CON_AEN=BIT(8)`, `OVL_CON_ALPHA=0xff`), `:543` (`con |= alpha & 0xff`) |
| 6.4 | No scaling: `src` size must equal `dst` size, else `-ERANGE` | `mtk_plane.c:255-259`; `include/drm/drm_atomic_helper.h:44` (`DRM_PLANE_NO_SCALING = (1<<16)`); `drm_rect.c:152-186` |
| 6.5 | Per-layer position: `dst.x1/y1` become OVL `OFFSET`, src crop folded into the address | `mtk_plane.c:187-188,139-142`; `mtk_disp_ovl.c:530-531,589-590` |
| 6.6 | Formats = `mt8173_formats` (11 entries); blend = PREMULTI\|COVERAGE\|PIXEL_NONE | `mtk_disp_ovl.c:116-128`, `:707-709` |
| 6.7 | CRTC mode property is named **`MODE_ID`**, not `MODE` | `drm_mode_config.c:333-340`, attach at `drm_crtc.c:318-325` |
| 6.8 | A plane omitted from a commit keeps its `SRC_CON` bit and old buffer | `mtk_crtc.c:509-531` (`if (!plane_state->pending.config) continue;`), `mtk_plane.c:296`, `mtk_plane.c:271-273` |
| 6.9 | A clipping-to-nothing plane is auto-disabled | `mtk_plane.c:289-291`; `drm_atomic_helper.c:942-945` |
| 6.10 | `min_width = min_height = 64`, `max 4096`; smaller FBs → `-EINVAL` | `mtk_drm_drv.c:528-529,536-537`; `drm_framebuffer.c:267-275` |
| 6.11 | Scanout memory must be physically contiguous; dumb buffers use `dma_alloc_wc` | `mtk_gem.c` (`mtk_gem_prime_import_sg_table` rejects fragmented sg_tables; `mtk_gem_dumb_create` → `dma_alloc_wc`); 96 MiB CMA pool in `mt6572-z1.dts:173-178` |
| 6.12 | `mtk_gem_dumb_create()` computes `pitch = DIV_ROUND_UP(width*bpp, 8)`, `size = pitch*height` | `mtk_gem.c` |
| 6.13 | `DRM_IOCTL_MODE_ATOMIC` is a `DRM_MASTER` ioctl | `drm_ioctl.c:701`, `drm_ioctl.c:607-610` |
| 6.14 | `drm_setmaster_ioctl()` returns `-EBUSY` instead of evicting an existing master | `drm_auth.c:262-263`; `drm_auth.c:233-241` needs `CAP_SYS_ADMIN` |
| 6.15 | DDP main path is `OVL0 → RDMA0 → DSI0`; shadow registers on | `mtk_drm_drv.c:249-266` |
| 6.16 | `-ENOSPC` only ever means "source rect outside the framebuffer" | `drm_atomic.c:770-781`; no `ENOSPC` anywhere in `drivers/gpu/drm/mediatek/` |

Hardcoded UAPI constants were cross-checked numerically against
`fork_linux/include/uapi/drm/`:

```
DRM_IOCTL_MODE_CREATE_DUMB = 0xc02064b2   DRM_IOCTL_MODE_MAP_DUMB = 0xc01064b3
DRM_IOCTL_MODE_ATOMIC      = 0xc03864bc   DRM_IOCTL_MODE_ADDFB2   = 0xc06864b8
XRGB8888 = 0x34325258  ARGB8888 = 0x34325241  RGB565 = 0x36314752  RGB888 = 0x34324752
DRM_MODE_ATOMIC_NONBLOCK = 0x200  ALLOW_MODESET = 0x400  TEST_ONLY = 0x100
DRM_MODE_PAGE_FLIP_EVENT = 0x1    DRM_MODE_OBJECT_PLANE = 0xeeeeeeee
DRM_PLANE_TYPE_OVERLAY/PRIMARY/CURSOR = 0/1/2   DRM_CLIENT_CAP_ATOMIC = 3
DRM_FORMAT_MOD_LINEAR = 0
```

Sources: `drm.h:1264-1274` (ioctls), `drm_fourcc.h:185-258` (formats),
`drm_mode.h:1108,1297,1306,1327` (flags), `drm_mode.h:624-633` (object types),
`include/drm/drm_plane.h:601-634` (plane types = 0/1/2 as UABI),
`drm.h:843-859` (client caps), `drm_fourcc.h:544` (LINEAR = 0).

Property names used, all confirmed against the kernel and against a live
atomic driver: `type`, `FB_ID`, `CRTC_ID`, `CRTC_X`, `CRTC_Y`, `CRTC_W`,
`CRTC_H`, `SRC_X`, `SRC_Y`, `SRC_W`, `SRC_H`, `zpos`, `alpha`,
`pixel blend mode` (with spaces), and on the CRTC `MODE_ID`, `ACTIVE`,
`OUT_FENCE_PTR`. `OUT_FENCE_PTR` is created unconditionally by
`drm_mode_config.c:309-314` and attached to every CRTC by `drm_crtc.c:318-325`.

---

## 7. Things found while writing this that are worth acting on

1. **`z1hwc2.cpp:270` looks up the CRTC mode property as `"MODE"`.** In this
   kernel the property is `"MODE_ID"` (`drm_mode_config.c:333-340`), so the
   lookup returns property id 0, and
   `drmModeAtomicAddProperty(req, crtc_id, 0, blob)` makes the whole commit
   fail with `-ENOENT`. `z1kmstest` tries `MODE_ID` first and falls back to
   `MODE`; `z1hwc2` should do the same (or just use `MODE_ID`).

2. **`z1hwc2` never calls `drmSetMaster()`.** `DRM_IOCTL_MODE_ATOMIC` requires
   master (`drm_ioctl.c:701`). If SurfaceFlinger (or anything else) opened
   `/dev/dri/card1` first, every HWC2 commit returns `-EACCES`. `z1kmstest`
   calls `drmSetMaster()` and reports the result explicitly.

3. **The OVL base is `0x14007000`, not `0x14008000`.** `0x14008000` is RDMA0.

4. **`-ENOSPC` is not a plane-count error** on this kernel; only 4 planes
   exist, so "too many layers" cannot be submitted at all.

---

## 8. Limitations / deliberately not implemented

* **No display readback.** PASS/FAIL is always "the kernel accepted this", not
  "the pixels are correct". The `look for on the panel:` lines exist precisely
  because a human has to be the oracle. Do not treat a green run as proof that
  blending looks right.
* **No scaling coverage.** Scaling is impossible on this driver (6.4), so the
  tool never tries it — except that the `offscreen` test documents the
  `-ERANGE` meaning in this README rather than producing one.
* **Rotation is not exercised.** `ROTATE_0` is the default (`mtk_plane.c:346-353`
  creates the property; `mtk_ovl_layer_check()` rejects unsupported values at
  `mtk_disp_ovl.c:374-393`).
  Adding rotation coverage is straightforward but it is a separate axis from
  multi-plane composition and needs its own visual oracle.
* **No format-modifier / AFBC testing.** `mt6572_ovl_driver_data.supports_afbc
  = false` (`mtk_disp_ovl.c:706`) and
  `mtk_plane_format_mod_supported()` only accepts
  `DRM_FORMAT_MOD_LINEAR` (`mtk_plane.c:68-73`).
* **No YUV colorimetry verification.** `UYVY`/`YUYV` are included in the
  `formats` sweep (the tool converts a solid colour with BT.601 limited range),
  but the tool does not verify the resulting colours.
* **No fence handling.** `IN_FENCE_FD` / `OUT_FENCE_PTR` are neither set nor
  consumed; the commits are synchronous by design.
* **`--test-only` still needs DRM master**, because the master check happens
  before the ioctl even looks at the flags.
* **`regs` reads a single 4 KiB-ish window** at a fixed base; it does not know
  about the mutex, RDMA or MMSYS blocks. RDMA0 is at `0x14008000` and MUTEX at
  `0x1400e000` (`mt6572.dtsi:589,627`) if you need them; use `--base`.

---

## 9. Assumptions that still need on-board confirmation

1. **The first modeset actually lights the panel.** With
   `mtk_dsi.keep_lk_dsi=1` the DSI is untouched, so the CRTC/OVL must be
   programmed under a live DSI stream. The commit path is verified to be
   *accepted* on a real atomic driver (amdgpu, see §10) but only the board can
   show that `mtk_crtc_ddp_hw_init()` + `mtk_crtc_atomic_enable()` succeed and
   that `drm_crtc_vblank_on()` is reached.
2. **`drmSetMaster()` succeeds as root on the board.** It returns 0 if nothing
   else holds master; `-EBUSY` if SurfaceFlinger/`z1hwc2` does. The `-EBUSY`
   branch was verified by reading `drm_auth.c:262-263`, not by running it (it
   cannot be provoked on a machine whose compositor holds master without
   disturbing that session).
3. **Page-flip events.** `--event` / `basic` report empirically whether the
   driver delivers them. The kernel-side risk
   (`WARN_ON(drm_crtc_vblank_get() != 0)` in `mtk_crtc_atomic_begin()`) is
   documented but has not been observed on hardware.
4. **`regs` does not hang the bus.** Reading `0x14007000` is only safe while
   the DIS power domain / OVL clock are on. This cannot be verified statically.
5. **`Sitronix ST7785M` mode is 240×320.** The tool scales every test geometry
   from the connector's preferred mode, so it adapts, but the *visual*
   expectations in this document assume 240×320.
6. **`DRM_FORMAT_RGB888` byte order.** The tool writes bytes `B,G,R` in memory,
   derived from `drm_fourcc.h:189` (`[23:0] R:G:B little endian`). If the
   colours look swapped on the panel, that interpretation (or the OVL's
   `OVL_CON_CLRFMT_RGB888` handling) is the thing to look at.

---

## 10. Verification performed (no board was used)

Host-side evidence, all reproducible on the build machine:

| check | result |
|---|---|
| ARM build | 122976 bytes, ELF32 / ARM / EABI5 / LSB, `NEEDED = libdl.so, libc.so`, no `libdrm.so`, `sha256 19541326…` |
| `nm -D` | `T main` plus every `drm*` entry point the tool calls defined as `T` (203 `drm*` symbols exported in total thanks to `--export-dynamic`), 0 undefined `drm*` |
| pixel encoders | 35 assertions on `put_pixel` / `fill_rect` / `fill_quadrants` / `fill_checker` / `fourcc_bpp` / `errno_hint`, all pass |
| buffer path | real `CREATE_DUMB` → `MAP_DUMB` → `mmap` → `AddFB2` → `RmFB` → `DESTROY_DUMB` against `/dev/dri/card1` (amdgpu), including a rejected format and the 24 bpp pitch (192 = 64×3, as `mtk_gem.c` computes) |
| `probe` | full enumerate + z-order sort + blend-enum resolution against a real atomic driver |
| argument handling | `help`, no args, unknown command/option, missing/bad values, missing node, bad `--mode`, `regs` as non-root — each returns its documented exit code |
| `run` | reaches the first `DRM_IOCTL_MODE_ATOMIC` and correctly diagnoses the non-root `EACCES` / existing-master `EBUSY` cases |

Not verified: any real commit on the target hardware.
