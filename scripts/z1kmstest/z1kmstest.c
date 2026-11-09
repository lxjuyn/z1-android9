/*
 * z1kmstest -- standalone ARM tool that drives the mediatek-drm atomic/KMS
 * interface on the Z1 (MT6572) directly, with no Android framework and no
 * SurfaceFlinger in the way.
 *
 * PURPOSE
 * -------
 * Validate multi-plane OVL composition on the real hardware from a serial
 * shell: plane positions, per-plane alpha, blend modes, pixel formats,
 * z-order (immutable zpos), and explicit plane disable.
 *
 * Build: see build.sh (NDK clang, static libdrm from
 *        mesa_android/libdrm-static-build/libdrm.a).
 *
 *
 * ---------------------------------------------------------------------------
 * KERNEL FACTS THIS TOOL IS DESIGNED AROUND
 * (all verified by reading /home/lxj/claude/6572/fork_linux; file:line given
 *  so every claim below can be re-checked)
 * ---------------------------------------------------------------------------
 *
 * 1) Plane count / types / z-order
 *      mtk_disp_ovl.c:701-712  mt6572_ovl_driver_data.layer_nr = 4
 *      mtk_crtc.c:934-949      mtk_crtc_num_comp_planes():
 *                                - comp_idx > 1 -> 0
 *                                - comp_idx == 1 && !bgclr_in_on -> 0
 *                              => only OVL0 (ddp_comp[0]) contributes planes
 *      mtk_crtc.c:951-962      mtk_crtc_plane_type(): idx 0 -> PRIMARY,
 *                              idx == num_planes-1 -> CURSOR, else OVERLAY
 *      => plane[0]=PRIMARY, plane[1..2]=OVERLAY, plane[3]=CURSOR
 *      mtk_plane.c:340         drm_plane_create_zpos_immutable_property(plane,
 *                                                                 plane_idx)
 *      => "zpos" is READ-ONLY and equals the plane index.  The plane list
 *         order *is* the compositing order; userspace can never reorder
 *         layers, only choose what each layer shows.
 *      mtk_plane.c:354         drm_plane_create_alpha_property()
 *      mtk_plane.c:358-362     drm_plane_create_blend_mode_property()
 *
 * 2) No scaling at all
 *      mtk_plane.c:255-259     drm_atomic_helper_check_plane_state(
 *                                  ..., DRM_PLANE_NO_SCALING,
 *                                       DRM_PLANE_NO_SCALING,
 *                                       can_position      = true,
 *                                       can_update_disabled = true)
 *      include/drm/drm_atomic_helper.h:44   DRM_PLANE_NO_SCALING = (1<<16)
 *      drm_rect.c:152-186      drm_rect_calc_hscale():
 *                                  hscale = (src_w_px << 16) / dst_w_px
 *                                  if (hscale < min || hscale > max)
 *                                          return -ERANGE;
 *                              with min == max == 1<<16 => src size MUST equal
 *                              dst size in pixels or the commit fails -ERANGE
 *
 * 3) Per-layer position IS supported
 *      mtk_plane.c:187-188     pending.x = new_state->dst.x1;
 *                              pending.y = new_state->dst.y1;
 *      mtk_disp_ovl.c:530-531, 589-590
 *                              offset   = (y << 16) | x -> OVL_OFFSET(idx)
 *                              src_size = (h << 16) | w -> OVL_SRC_SIZE(idx)
 *      mtk_plane.c:139-142     the source crop is folded into the buffer
 *                              address: addr += (src.x1>>16)*cpp
 *                                        + (src.y1>>16)*pitch
 *
 * 4) Formats
 *      mtk_disp_ovl.c:116-128  mt6572 uses mt8173_formats:
 *          XRGB8888 ARGB8888 BGRX8888 BGRA8888 ABGR8888 XBGR8888
 *          RGB888 BGR888 RGB565 UYVY YUYV
 *      mtk_disp_ovl.c:707-709  blend_modes = PREMULTI | COVERAGE | PIXEL_NONE
 *      mtk_disp_ovl.c:93-94    OVL_CON_AEN = BIT(8), OVL_CON_ALPHA = 0xff
 *      mtk_disp_ovl.c:543      con |= state->base.alpha & 0xff
 *      => the plane alpha is 8 bits in hardware: DRM alpha 0x8080 is
 *         programmed as 0x80.  The tool always prints both numbers.
 *
 * 5) A plane that is not part of an atomic commit keeps its OVL SRC_CON bit
 *    and keeps scanning out the old buffer:
 *      mtk_crtc.c:509-531      mtk_crtc_ddp_config():
 *                                  if (!plane_state->pending.config) continue;
 *      mtk_crtc.c:582-587      pending.config is set only for planes whose
 *                              pending.dirty was set;
 *      mtk_plane.c:296         pending.dirty = true in atomic_update()
 *      mtk_plane.c:271-273     pending.dirty = true in atomic_disable()
 *      Both only run for planes present in the commit.
 *      => z1kmstest always disables unused planes explicitly, with FB_ID=0
 *         AND CRTC_ID=0 in the same request.
 *      (Zeroing only one of the two is rejected: drm_atomic.c
 *       drm_atomic_plane_check() returns -EINVAL for "CRTC set but no FB"
 *       and for "FB set but no CRTC".)
 *
 * 6) A plane that clips to nothing is auto-disabled by the driver:
 *      mtk_plane.c:289-291     if (!new_state->visible) {
 *                                      mtk_plane_atomic_disable(...); return;
 *                              }
 *      and `visible` comes from drm_rect_clip_scaled() against the CRTC rect
 *      (drm_atomic_helper.c:942-945).  So a fully off-screen plane is
 *      ACCEPTED by the kernel but silently not displayed.
 *
 * 7) Framebuffer size limits
 *      mtk_drm_drv.c:528-529, 536-537
 *                              mode_config.min_width  = 64
 *                              mode_config.min_height = 64
 *                              mode_config.max_width  = 4096
 *                              mode_config.max_height = 4096
 *      (mt6572_mmsys_driver_data does not override them, mtk_drm_drv.c:255)
 *      drm_framebuffer.c:267-275  drm_internal_framebuffer_create() rejects
 *                              width < min_width / height < min_height with
 *                              -EINVAL
 *      => a 1x1 or 8x8 *framebuffer* cannot exist on this driver at all.
 *         The smallest legal FB is 64x64; the smallest legal *destination
 *         rectangle* is 1x1 (that is a different check).
 *
 * 8) Contiguous scanout memory is mandatory
 *      mtk_gem.c mtk_gem_prime_import_sg_table():
 *                              if (drm_prime_get_contiguous_size(sgt) <
 *                                  attach->dmabuf->size)
 *                                      return ERR_PTR(-EINVAL);
 *      mtk_gem.c mtk_gem_dumb_create() -> mtk_gem_create() -> dma_alloc_wc(),
 *      which is physically contiguous.  So every scanout buffer here comes
 *      from DRM_IOCTL_MODE_CREATE_DUMB on the mediatek node.
 *      The Z1 DT reserves a 96 MiB CMA default pool
 *      (mt6572-z1.dts:173-178, size = <0x6000000>), so allocation will not
 *      run dry.
 *
 * 9) The first commit must look like a full modeset
 *      mtk_dsi.c:879-880       module param z1_keep_lk_dsi (default true)
 *      mtk_dsi.c:961-964       mtk_dsi_poweron() returns immediately
 *                              ("route B -- keeping LK video stream")
 *      so the tool reproduces the HWC2 first-commit recipe
 *      (z1hwc2.cpp:823-835): connector CRTC_ID = crtc,
 *                            crtc MODE_ID  = fresh property blob,
 *                            crtc ACTIVE   = 1,
 *                            flags |= DRM_MODE_ATOMIC_ALLOW_MODESET
 *      After that, plain plane commits are enough.
 *
 *      *** The CRTC mode property is named "MODE_ID", NOT "MODE" ***
 *      drm_mode_config.c:333-340
 *          drm_property_create(dev, DRM_MODE_PROP_ATOMIC | DRM_MODE_PROP_BLOB,
 *                              "MODE_ID", 0);
 *      drm_crtc.c:318-325
 *          if (drm_core_check_feature(dev, DRIVER_ATOMIC)) {
 *                  drm_object_attach_property(&crtc->base,
 *                                             config->prop_active, 0);
 *                  drm_object_attach_property(&crtc->base,
 *                                             config->prop_mode_id, 0);
 *                  drm_object_attach_property(&crtc->base,
 *                                             config->prop_out_fence_ptr, 0);
 *          }
 *      Looking up "MODE" returns property id 0, and
 *      drmModeAtomicAddProperty(req, crtc, 0, blob) then makes the whole
 *      commit fail with -ENOENT (drm_atomic_uapi.c resolves the property id
 *      per object).  z1hwc2.cpp:270 looks up "MODE" and therefore has this
 *      bug; z1kmstest tries "MODE_ID" first and falls back to "MODE" for
 *      pre-5.x kernels, and always prints which name it used.
 *
 * 10) Error codes, decoded (these are the real ones, do not guess):
 *      -ERANGE  scaling: src size != dst size (drm_atomic_helper.c:934-940)
 *      -EINVAL  unsupported pixel format / plane, FB smaller than 64x64, bad
 *               object or property id, CRTC_ID without FB_ID (or the
 *               reverse), or a modeset attempted without ALLOW_MODESET
 *      -ENOSPC  "invalid source coordinates": SRC_x/SRC_w lies outside the
 *               framebuffer (drm_atomic.c:770-781).  It is **not** a
 *               plane-count error on this kernel.
 *      This driver has no crtc ->atomic_check(), so "too many layers" can
 *      never even be submitted: there are only 4 planes.
 *
 *
 * WHAT THIS TOOL DELIBERATELY DOES NOT DO
 * ---------------------------------------
 * - No scaling / format-modifier / rotation coverage beyond "does the kernel
 *   accept it".  Rotation is left at ROTATE_0.
 * - The panel content is never read back.  Every PASS/FAIL printed here is a
 *   *kernel-result* verdict (validation accepted, commit accepted).  The
 *   accompanying "look for on the panel:" lines describe what a human should
 *   see; they are not machine-checked, because there is no display readback
 *   path on this board (no DP/HDMI, the DSI link is owned by LK).
 * - Nothing is written anywhere except the DRM card node, /dev/kmsg, and (for
 *   `regs`) a read-only mmap of /dev/mem.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>

#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

/* ------------------------------------------------------------------ misc --- */

#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

#define EXIT_OK           0	/* everything asked for passed                  */
#define EXIT_FAIL         1	/* at least one sub-test was rejected           */
#define EXIT_NODEVMEM     2	/* `regs`: /dev/mem not usable                  */
#define EXIT_NODEV        3	/* DRM node missing / cannot open               */
#define EXIT_NOTATOMIC    4	/* no universal-planes / atomic support         */
#define EXIT_NOKMS        5	/* no CRTC / connector / mode / plane usable    */
#define EXIT_USAGE        6	/* bad command line                             */
#define EXIT_NOBUF        7	/* dumb buffer allocation or AddFB2 failed      */
#define EXIT_COMMIT       8	/* the first (modeset) commit failed            */
#define EXIT_NOMASTER     9	/* not DRM master: not root (EACCES) or someone
				 * else holds it (EBUSY)                        */

#define DEFAULT_DEVICE    "/dev/dri/card1"	/* card0 is lima, render only   */
#define DEFAULT_WAIT_SEC  40
#define DEFAULT_DELAY_MS  1000
#define EVENT_TIMEOUT_MS  2000

#define KMSG_MAX          512	/* one line per write(), strictly < 512 B     */
#define KMSG_MSG_MAX      (KMSG_MAX - 2)

#define MAX_PLANES        8
#define MAX_BUFS          40

#define OVL_BASE_DEFAULT  0x14007000u	/* see cmd_regs() + README          */

static int g_kmsg_fd = -1;
static volatile sig_atomic_t g_stop;

/* ---------------------------------------------------------------- logging --- */

/*
 * Every line goes to stdout and (if available) to /dev/kmsg with the
 * "z1kms: " prefix, as one write() per line.
 *
 * /dev/kmsg accepts at most PRINTKRB_RECORD_MAX = 1024 bytes per write()
 * (kernel/printk/internal.h:51) and by default rate-limits userspace writes to
 * DEFAULT_RATELIMIT_BURST = 10 records per DEFAULT_RATELIMIT_INTERVAL = 5 s
 * (kernel/printk/printk.c devkmsg_open() -> ratelimit_default_init(), see
 * include/linux/ratelimit_types.h:9-10).  We therefore emit exactly one
 * write() per line and never more than 511 bytes; the README tells the
 * operator how to lift the rate limit.
 */
static void klog(const char *fmt, ...)
{
	char buf[KMSG_MAX];
	char out[KMSG_MAX];
	va_list ap;
	int n, m;

	va_start(ap, fmt);
	n = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	if (n < 0)
		return;

	/* exactly one trailing newline, and never a line longer than the
	 * kmsg record we are about to write */
	while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r'))
		buf[--n] = '\0';
	if (n > KMSG_MSG_MAX) {
		n = KMSG_MSG_MAX;
		buf[n] = '\0';
	}

	m = snprintf(out, sizeof(out), "%s\n", buf);
	if (m > 0)
		(void)!write(STDOUT_FILENO, out, (size_t)m);

	if (g_kmsg_fd >= 0) {
		m = snprintf(out, sizeof(out), "z1kms: %s\n", buf);
		if (m > 0) {
			if (m > KMSG_MAX - 1)
				m = KMSG_MAX - 1;
			out[m] = '\0';
			(void)!write(g_kmsg_fd, out, (size_t)m);
		}
	}
}

/* stdout only (used by usage(), which is too long for kmsg) */
static void kout(const char *fmt, ...)
{
	char buf[KMSG_MAX];
	va_list ap;
	int n, m;

	char out[KMSG_MAX];

	va_start(ap, fmt);
	n = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	if (n < 0)
		return;
	m = snprintf(out, sizeof(out), "%s\n", buf);
	if (m > 0)
		(void)!write(STDOUT_FILENO, out, (size_t)m);
}

/* ------------------------------------------------------------ small utils --- */

static double now_ms(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts))
		return 0.0;
	return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
}

static void msleep(int ms)
{
	while (ms > 0 && !g_stop) {
		int step = ms > 50 ? 50 : ms;

		usleep((useconds_t)step * 1000u);
		ms -= step;
	}
}

static void on_signal(int sig)
{
	(void)sig;
	g_stop = 1;
}

/*
 * libdrm in this tree mixes two return conventions: drmIoctl()-based helpers
 * return -1 with errno set (e.g. drmSetClientCap), while the newer wrappers
 * return -errno.  Verified by disassembling libdrm.a: drmModeAtomicCommit,
 * drmModeAddFB2 and drmModeCreatePropertyBlob all end in
 * "ldr r0,[__errno]; rsb r0,r0,#0", i.e. they return -errno.  Normalise both
 * to "-errno".
 */
static int norm_ret(int ret)
{
	if (ret == 0)
		return 0;
	if (errno > 0)
		return -errno;
	return ret < 0 ? ret : -ret;
}

static const char *fourcc_name(uint32_t f, char out[5])
{
	out[0] = (char)(f & 0xff);
	out[1] = (char)((f >> 8) & 0xff);
	out[2] = (char)((f >> 16) & 0xff);
	out[3] = (char)((f >> 24) & 0xff);
	out[4] = '\0';
	return out;
}

static int fourcc_bpp(uint32_t f)
{
	switch (f) {
	case DRM_FORMAT_XRGB8888:
	case DRM_FORMAT_ARGB8888:
	case DRM_FORMAT_XBGR8888:
	case DRM_FORMAT_ABGR8888:
	case DRM_FORMAT_RGBX8888:
	case DRM_FORMAT_RGBA8888:
	case DRM_FORMAT_BGRX8888:
	case DRM_FORMAT_BGRA8888:
		return 32;
	case DRM_FORMAT_RGB888:
	case DRM_FORMAT_BGR888:
		return 24;
	case DRM_FORMAT_RGB565:
	case DRM_FORMAT_BGR565:
	case DRM_FORMAT_UYVY:
	case DRM_FORMAT_YUYV:
		return 16;
	default:
		return 0;
	}
}

static int fourcc_is_yuv_packed(uint32_t f)
{
	return f == DRM_FORMAT_UYVY || f == DRM_FORMAT_YUYV;
}

static const char *plane_type_name(uint32_t t)
{
	switch (t) {
	case DRM_PLANE_TYPE_PRIMARY:
		return "PRIMARY";
	case DRM_PLANE_TYPE_CURSOR:
		return "CURSOR";
	case DRM_PLANE_TYPE_OVERLAY:
		return "OVERLAY";
	default:
		return "?";
	}
}

/*
 * Human explanation of an errno returned by an atomic commit.  Only the codes
 * this driver can actually produce are listed; everything else falls through
 * to strerror().
 */
static const char *errno_hint(int err)
{
	switch (err) {
	case EINVAL:
		return "EINVAL: unsupported format/plane, FB smaller than "
		       "mode_config.min_width(64)/min_height(64), bad object or "
		       "property id, CRTC_ID without FB_ID (or the reverse), or a "
		       "modeset attempted without DRM_MODE_ATOMIC_ALLOW_MODESET";
	case ERANGE:
		return "ERANGE: out of range -- on this driver that means "
		       "SCALING: SRC_W/SRC_H (16.16) must equal CRTC_W/CRTC_H in "
		       "pixels";
	case ENOSPC:
		return "ENOSPC: invalid source coordinates -- SRC_X/SRC_Y/SRC_W/"
		       "SRC_H lie outside the framebuffer. This is NOT a "
		       "plane-count error (drm_atomic.c:770-781)";
	case ENOENT:
		return "ENOENT: unknown object id (stale plane/crtc/property/fb)";
	case EBUSY:
		return "EBUSY: the DRM modeset lock is held by another master "
		       "(is SurfaceFlinger still running? try `stop`)";
	case EAGAIN:
		return "EAGAIN: retry (modeset lock contention)";
	case EACCES:
		return "EACCES: DRM_IOCTL_MODE_ATOMIC is declared DRM_MASTER "
		       "(drm_ioctl.c:701) and drm_ioctl_permit() "
		       "(drm_ioctl.c:598-618) rejects clients that are not the "
		       "current DRM master. Another process (SurfaceFlinger / "
		       "z1hwc2) holds master: stop it and re-run. The same errno "
		       "also comes out of drm_atomic_uapi.c:557 when a plane's "
		       "CRTC_ID does not resolve, so check the ids in this log.";
	case EPERM:
		return "EPERM: not permitted";
	case ENOMEM:
		return "ENOMEM: out of memory (dumb buffer / CMA)";
	default:
		return NULL;
	}
}

static void log_errno(int err, const char *what)
{
	const char *h = errno_hint(-err);

	klog("%s: %d (%s)", what, err, strerror(-err));
	if (h)
		klog("    -> %s", h);
}

/* ---------------------------------------------------------------- objects --- */

typedef struct {
	uint32_t id;
	uint32_t type;		/* raw "type" property: 0/1/2 */
	uint32_t possible_crtcs;
	int zpos;
	uint32_t zpos_prop;

	uint32_t p_fb, p_crtc, p_cx, p_cy, p_cw, p_ch;
	uint32_t p_sx, p_sy, p_sw, p_sh;
	uint32_t p_alpha, p_blend;
	uint32_t cur_alpha, cur_blend;

	/* blend-mode enum values as advertised by the kernel; -1 if absent */
	int v_none, v_premulti, v_coverage;

	uint32_t count_formats;
	uint32_t *formats;
} zplane;

typedef struct {
	int fd;
	const char *path;
	char driver[64];
	char date[32];
	char desc[128];

	uint32_t crtc_id;
	uint32_t crtc_p_mode;
	char mode_prop_name[16];
	uint32_t crtc_p_active;
	uint32_t crtc_p_out_fence;
	uint32_t crtc_p_gamma_lut;

	uint32_t conn_id;
	uint32_t conn_p_crtc;
	uint32_t enc_id;

	drmModeModeInfo mode;
	int width;
	int height;

	zplane planes[MAX_PLANES];
	int plane_count;

	uint32_t mode_blob;
	int modeset_done;
} zctx;

static zctx g;

/* Property id of `name` on object (obj,type); optionally its current value. */
static uint32_t find_prop(int fd, uint32_t obj, uint32_t type, const char *name,
			  uint64_t *out_val)
{
	drmModeObjectProperties *props;
	uint32_t id = 0;
	uint32_t i;

	props = drmModeObjectGetProperties(fd, obj, type);
	if (!props)
		return 0;
	for (i = 0; i < props->count_props; i++) {
		drmModePropertyRes *p = drmModeGetProperty(fd, props->props[i]);

		if (!p)
			continue;
		if (!strcmp(p->name, name)) {
			id = p->prop_id;
			if (out_val)
				*out_val = props->prop_values[i];
			drmModeFreeProperty(p);
			break;
		}
		drmModeFreeProperty(p);
	}
	drmModeFreeObjectProperties(props);
	return id;
}

/*
 * Enum value of `enum_name` inside property `prop_name`; -1 if either is
 * absent.  Blend-mode values are resolved at runtime instead of hardcoding
 * DRM_MODE_BLEND_*, so the tool keeps working if the kernel renumbers them.
 */
static int find_prop_enum_value(int fd, uint32_t obj, uint32_t type,
				const char *prop_name, const char *enum_name)
{
	uint32_t pid = find_prop(fd, obj, type, prop_name, NULL);
	drmModePropertyRes *p;
	int val = -1;
	int i;

	if (!pid)
		return -1;
	p = drmModeGetProperty(fd, pid);
	if (!p)
		return -1;
	for (i = 0; i < p->count_enums; i++) {
		if (!strcmp(p->enums[i].name, enum_name)) {
			val = (int)p->enums[i].value;
			break;
		}
	}
	drmModeFreeProperty(p);
	return val;
}

static const char *blend_enum_name(int v, const zplane *pl)
{
	if (v < 0)
		return "?";
	if (v == pl->v_none)
		return "None";
	if (v == pl->v_premulti)
		return "Pre-multiplied";
	if (v == pl->v_coverage)
		return "Coverage";
	return "?";
}

/* ------------------------------------------------------------------ open --- */

/*
 * Wait for the KMS node.  mediatek-drm is a component master: /dev/dri/card1
 * only appears once OVL/RDMA/DSI have all probed, which is ~33 s after boot,
 * so the tool polls instead of failing immediately.
 */
static int open_waited(const char *path, int wait_sec, int *out_fd)
{
	double t0 = now_ms();
	int announced = 0;

	for (;;) {
		int fd = open(path, O_RDWR | O_CLOEXEC);

		if (fd >= 0) {
			*out_fd = fd;
			return 0;
		}
		if (errno != ENOENT && errno != ENXIO) {
			klog("open(%s) failed: %s", path, strerror(errno));
			return -1;
		}
		if ((now_ms() - t0) / 1000.0 >= (double)wait_sec) {
			klog("timed out after %d s waiting for %s: %s", wait_sec,
			     path, strerror(errno));
			return -1;
		}
		if (!announced) {
			klog("waiting up to %d s for %s (mediatek-drm appears "
			     "~33 s after boot)", wait_sec, path);
			announced = 1;
		}
		usleep(200 * 1000);
	}
}

static int read_version(zctx *c)
{
	drmVersion *v;

	errno = 0;
	v = drmGetVersion(c->fd);
	if (!v) {
		klog("drmGetVersion failed: %s", strerror(errno));
		return -1;
	}
	snprintf(c->driver, sizeof(c->driver), "%s", v->name ? v->name : "?");
	snprintf(c->date, sizeof(c->date), "%s", v->date ? v->date : "?");
	snprintf(c->desc, sizeof(c->desc), "%s", v->desc ? v->desc : "?");
	drmFreeVersion(v);
	return 0;
}

static int set_caps(zctx *c)
{
	int ret;

	errno = 0;
	ret = drmSetClientCap(c->fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1);
	klog("cap DRM_CLIENT_CAP_UNIVERSAL_PLANES -> %d (%s)", ret,
	     ret ? strerror(errno) : "ok");
	if (ret) {
		klog("%s: cannot enumerate planes, not a usable KMS node",
		     c->path);
		return -1;
	}

	errno = 0;
	ret = drmSetClientCap(c->fd, DRM_CLIENT_CAP_ATOMIC, 1);
	klog("cap DRM_CLIENT_CAP_ATOMIC -> %d (%s)", ret,
	     ret ? strerror(errno) : "ok");
	if (ret) {
		klog("%s: no atomic modesetting", c->path);
		return -1;
	}
	return 0;
}

/*
 * DRM_IOCTL_MODE_ATOMIC is one of the few ioctls declared DRM_MASTER:
 *   drivers/gpu/drm/drm_ioctl.c:701
 *       DRM_IOCTL_DEF(DRM_IOCTL_MODE_ATOMIC, drm_mode_atomic_ioctl, DRM_MASTER),
 *   drivers/gpu/drm/drm_ioctl.c:607-610
 *       if (unlikely((flags & DRM_MASTER) && !drm_is_current_master(file_priv)))
 *               return -EACCES;
 * The *first* client to open the primary node becomes master automatically
 * (drm_auth.c drm_master_open()), so on a quiet board this call is a no-op.
 * If SurfaceFlinger/z1hwc2 got there first, drm_setmaster_ioctl() returns
 * -EBUSY (drm_auth.c:262-263: `if (dev->master) return -EBUSY;`) -- it never
 * evicts the existing master, so trying is always safe and never disturbs a
 * running compositor.
 */
static int master_acquire(zctx *c)
{
	int ret;

	errno = 0;
	ret = norm_ret(drmSetMaster(c->fd));
	if (!ret) {
		klog("drmSetMaster -> ok (this process is now DRM master)");
		return 0;
	}
	klog("drmSetMaster -> %d (%s)", ret, strerror(-ret));
	if (ret == -EBUSY) {
		/* drm_auth.c:262-263: `if (dev->master) return -EBUSY;` */
		klog("another process already holds DRM master on %s", c->path);
		klog("DRM_IOCTL_MODE_ATOMIC is a DRM_MASTER ioctl (drm_ioctl.c:701)"
		     ", so every commit would fail with -EACCES. Stop the "
		     "display stack first, e.g. as root:");
		klog("    stop; setprop ctl.stop surfaceflinger; "
		     "pkill -f z1hwc2");
		klog("then re-run. `probe` and `regs` do not need master and keep "
		     "working.");
		return -EBUSY;
	}
	if (ret == -EACCES) {
		/*
		 * drm_auth.c:233-241 drm_master_check_perm(): you may only
		 * set/drop master if you were master before, or if you have
		 * CAP_SYS_ADMIN.
		 */
		klog("drm_master_check_perm() refused: becoming DRM master needs "
		     "CAP_SYS_ADMIN (drm_auth.c:233-241), i.e. run z1kmstest as "
		     "root.");
		klog("without master, DRM_IOCTL_MODE_ATOMIC returns -EACCES "
		     "(drm_ioctl.c:607-610).");
		return -EACCES;
	}
	klog("-> this node cannot give us DRM master; commits would be "
	     "rejected with -EACCES");
	return ret;
}

static int find_resources(zctx *c)
{
	drmModeRes *res;
	drmModePlaneRes *pres;
	int i, usable = 0;

	errno = 0;
	res = drmModeGetResources(c->fd);
	if (!res) {
		klog("drmModeGetResources failed: %s", strerror(errno));
		return -1;
	}
	klog("resources: crtcs=%d encoders=%d connectors=%d fbs=%d",
	     res->count_crtcs, res->count_encoders, res->count_connectors,
	     res->count_fbs);
	if (res->count_crtcs < 1) {
		klog("no CRTC on this node");
		drmModeFreeResources(res);
		return -1;
	}

	for (i = 0; i < res->count_crtcs; i++)
		klog("crtc[%d] id=%u", i, res->crtcs[i]);
	for (i = 0; i < res->count_encoders; i++) {
		drmModeEncoder *e = drmModeGetEncoder(c->fd, res->encoders[i]);

		if (!e) {
			klog("encoder[%d] id=%u (drmModeGetEncoder failed)",
			     i, res->encoders[i]);
			continue;
		}
		klog("encoder[%d] id=%u type=0x%x crtc_id=%u possible_crtcs=0x%x",
		     i, e->encoder_id, e->encoder_type, e->crtc_id,
		     e->possible_crtcs);
		drmModeFreeEncoder(e);
	}

	c->conn_id = 0;
	for (i = 0; i < res->count_connectors; i++) {
		drmModeConnector *k = drmModeGetConnector(c->fd,
							  res->connectors[i]);
		int m;

		if (!k) {
			klog("connector[%d] id=%u (drmModeGetConnector failed)",
			     i, res->connectors[i]);
			continue;
		}
		klog("connector[%d] id=%u type=%u status=%s encoder_id=%u "
		     "modes=%d encoders=%d", i, k->connector_id,
		     k->connector_type,
		     k->connection == DRM_MODE_CONNECTED ? "connected" :
		     k->connection == DRM_MODE_DISCONNECTED ? "disconnected" :
		     "unknown", k->encoder_id, k->count_modes,
		     k->count_encoders);
		for (m = 0; m < k->count_modes && m < 4; m++)
			klog("  mode[%d] \"%s\" %ux%u@%u clock=%u htotal=%u "
			     "vtotal=%u flags=0x%x", m, k->modes[m].name,
			     k->modes[m].hdisplay, k->modes[m].vdisplay,
			     k->modes[m].vrefresh, k->modes[m].clock,
			     k->modes[m].htotal, k->modes[m].vtotal,
			     k->modes[m].flags);
		if (!c->conn_id && k->count_modes > 0) {
			c->conn_id = k->connector_id;
			c->enc_id = k->encoder_id;
			c->mode = k->modes[0];
			c->width = (int)k->modes[0].hdisplay;
			c->height = (int)k->modes[0].vdisplay;
		}
		drmModeFreeConnector(k);
	}
	if (!c->conn_id) {
		klog("no connector with a usable mode -- is the panel bound? "
		     "(sitronix,st7785m + &dsi status=okay)");
		drmModeFreeResources(res);
		return -1;
	}
	c->crtc_id = res->crtcs[0];

	c->conn_p_crtc = find_prop(c->fd, c->conn_id, DRM_MODE_OBJECT_CONNECTOR,
				   "CRTC_ID", NULL);
	/*
	 * The CRTC mode property is called "MODE_ID" in this kernel:
	 *   drivers/gpu/drm/drm_mode_config.c:333-340
	 *       drm_property_create(dev, DRM_MODE_PROP_ATOMIC | DRM_MODE_PROP_BLOB,
	 *                           "MODE_ID", 0);
	 *   drivers/gpu/drm/drm_crtc.c:318-325
	 *       if (drm_core_check_feature(dev, DRIVER_ATOMIC))
	 *               drm_object_attach_property(&crtc->base,
	 *                                          config->prop_mode_id, 0);
	 * Older descriptions (and the current z1hwc2.cpp) say "MODE", which
	 * resolves to property id 0 and makes the commit fail with -ENOENT.
	 * Try the real name first, then the legacy one, and always print which
	 * one was used.
	 */
	c->crtc_p_mode = find_prop(c->fd, c->crtc_id, DRM_MODE_OBJECT_CRTC,
				   "MODE_ID", NULL);
	if (c->crtc_p_mode)
		snprintf(c->mode_prop_name, sizeof(c->mode_prop_name), "MODE_ID");
	else {
		c->crtc_p_mode = find_prop(c->fd, c->crtc_id,
					   DRM_MODE_OBJECT_CRTC, "MODE", NULL);
		snprintf(c->mode_prop_name, sizeof(c->mode_prop_name), "MODE");
	}
	c->crtc_p_active = find_prop(c->fd, c->crtc_id, DRM_MODE_OBJECT_CRTC,
				     "ACTIVE", NULL);
	c->crtc_p_out_fence = find_prop(c->fd, c->crtc_id, DRM_MODE_OBJECT_CRTC,
					"OUT_FENCE_PTR", NULL);
	c->crtc_p_gamma_lut = find_prop(c->fd, c->crtc_id, DRM_MODE_OBJECT_CRTC,
					"GAMMA_LUT", NULL);
	klog("connector %u prop CRTC_ID      id=%u", c->conn_id, c->conn_p_crtc);
	klog("crtc %u prop %-17s id=%u (blob)", c->crtc_id,
	     c->mode_prop_name[0] ? c->mode_prop_name : "MODE_ID",
	     c->crtc_p_mode);
	klog("crtc %u prop ACTIVE            id=%u", c->crtc_id,
	     c->crtc_p_active);
	klog("crtc %u prop OUT_FENCE_PTR     id=%u%s", c->crtc_id,
	     c->crtc_p_out_fence,
	     c->crtc_p_out_fence ? "" : "  (absent -- no explicit out-fence)");
	klog("crtc %u prop GAMMA_LUT         id=%u", c->crtc_id,
	     c->crtc_p_gamma_lut);

	if (!c->conn_p_crtc || !c->crtc_p_mode || !c->crtc_p_active) {
		klog("missing mandatory atomic properties (conn_crtc=%u mode=%u "
		     "active=%u) -- cannot modeset", c->conn_p_crtc,
		     c->crtc_p_mode, c->crtc_p_active);
		drmModeFreeResources(res);
		return -1;
	}

	errno = 0;
	pres = drmModeGetPlaneResources(c->fd);
	if (!pres) {
		klog("drmModeGetPlaneResources failed: %s", strerror(errno));
		drmModeFreeResources(res);
		return -1;
	}
	klog("planes: count=%u", pres->count_planes);
	c->plane_count = 0;
	for (i = 0; i < (int)pres->count_planes && c->plane_count < MAX_PLANES;
	     i++) {
		drmModePlane *p = drmModeGetPlane(c->fd, pres->planes[i]);
		zplane *pl;
		uint64_t v = 0;
		int k;

		if (!p)
			continue;
		if (!(p->possible_crtcs & 1)) {
			klog("plane %u possible_crtcs=0x%x cannot drive crtc0, "
			     "skipping", p->plane_id, p->possible_crtcs);
			drmModeFreePlane(p);
			continue;
		}
		pl = &c->planes[c->plane_count];
		memset(pl, 0, sizeof(*pl));
		pl->id = p->plane_id;
		pl->possible_crtcs = p->possible_crtcs;
		pl->cur_alpha = 0xffff;
		pl->v_none = pl->v_premulti = pl->v_coverage = -1;

		pl->p_fb = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "FB_ID", NULL);
		pl->p_crtc = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				       "CRTC_ID", NULL);
		pl->p_cx = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "CRTC_X", NULL);
		pl->p_cy = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "CRTC_Y", NULL);
		pl->p_cw = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "CRTC_W", NULL);
		pl->p_ch = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "CRTC_H", NULL);
		pl->p_sx = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "SRC_X", NULL);
		pl->p_sy = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "SRC_Y", NULL);
		pl->p_sw = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "SRC_W", NULL);
		pl->p_sh = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
				     "SRC_H", NULL);
		pl->p_alpha = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
					"alpha", NULL);
		pl->p_blend = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
					"pixel blend mode", NULL);

		if (find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE, "type", &v))
			pl->type = (uint32_t)v;
		else
			pl->type = DRM_PLANE_TYPE_OVERLAY;

		v = 0;
		pl->zpos_prop = find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
					  "zpos", &v);
		pl->zpos = pl->zpos_prop ? (int)v : c->plane_count;

		if (pl->p_alpha) {
			v = 0;
			(void)find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
					"alpha", &v);
			pl->cur_alpha = (uint32_t)v;
		}
		if (pl->p_blend) {
			v = 0;
			(void)find_prop(c->fd, pl->id, DRM_MODE_OBJECT_PLANE,
					"pixel blend mode", &v);
			pl->cur_blend = (uint32_t)v;
		}
		pl->v_none = find_prop_enum_value(c->fd, pl->id,
						  DRM_MODE_OBJECT_PLANE,
						  "pixel blend mode", "None");
		pl->v_premulti = find_prop_enum_value(c->fd, pl->id,
						      DRM_MODE_OBJECT_PLANE,
						      "pixel blend mode",
						      "Pre-multiplied");
		pl->v_coverage = find_prop_enum_value(c->fd, pl->id,
						      DRM_MODE_OBJECT_PLANE,
						      "pixel blend mode",
						      "Coverage");

		pl->count_formats = p->count_formats;
		if (p->count_formats) {
			pl->formats = calloc(p->count_formats, sizeof(uint32_t));
			if (!pl->formats)
				pl->count_formats = 0;
		}
		for (k = 0; k < (int)pl->count_formats; k++)
			pl->formats[k] = p->formats[k];

		{
			char fn[5];
			char line[KMSG_MSG_MAX + 1];
			int off = 0;

			line[0] = '\0';
			for (k = 0; k < (int)pl->count_formats; k++) {
				int w = snprintf(line + off,
						 sizeof(line) - (size_t)off,
						 "%s%s(%#x)", k ? " " : "",
						 fourcc_name(pl->formats[k], fn),
						 pl->formats[k]);

				if (w < 0 || off + w >= (int)sizeof(line) - 1) {
					line[off] = '\0';
					break;
				}
				off += w;
			}
			klog("plane[%d] id=%u type=%s(%u) possible_crtcs=0x%x "
			     "crtc_id=%u fb_id=%u zpos=%d%s", c->plane_count,
			     pl->id, plane_type_name(pl->type), pl->type,
			     pl->possible_crtcs, p->crtc_id, p->fb_id, pl->zpos,
			     pl->zpos_prop ? " (immutable)" : " (n/a)");
			klog("  formats(%u): %s", pl->count_formats, line);
			klog("  alpha id=%u cur=%u   pixel_blend_mode id=%u "
			     "cur=%d(%s)", pl->p_alpha, pl->cur_alpha,
			     pl->p_blend, (int)pl->cur_blend,
			     blend_enum_name((int)pl->cur_blend, pl));
			klog("  blend enums: None=%d Pre-multiplied=%d Coverage=%d",
			     pl->v_none, pl->v_premulti, pl->v_coverage);
			klog("  props: fb=%u crtc=%u cx=%u cy=%u cw=%u ch=%u "
			     "sx=%u sy=%u sw=%u sh=%u", pl->p_fb, pl->p_crtc,
			     pl->p_cx, pl->p_cy, pl->p_cw, pl->p_ch, pl->p_sx,
			     pl->p_sy, pl->p_sw, pl->p_sh);
		}

		if (pl->p_fb && pl->p_crtc && pl->p_cx && pl->p_cy && pl->p_cw &&
		    pl->p_ch && pl->p_sw && pl->p_sh)
			usable++;
		else
			klog("  WARNING: plane %u is missing mandatory properties",
			     pl->id);
		drmModeFreePlane(p);
		c->plane_count++;
	}
	drmModeFreePlaneResources(pres);
	drmModeFreeResources(res);

	if (c->plane_count == 0) {
		klog("no usable plane on this CRTC");
		return -1;
	}
	if (usable != c->plane_count) {
		klog("only %d/%d planes have the full property set", usable,
		     c->plane_count);
		return -1;
	}

	/* order by the immutable zpos, which is the compositing order */
	for (i = 1; i < c->plane_count; i++) {
		zplane t = c->planes[i];
		int j = i - 1;

		while (j >= 0 && c->planes[j].zpos > t.zpos) {
			c->planes[j + 1] = c->planes[j];
			j--;
		}
		c->planes[j + 1] = t;
	}
	klog("plane order, bottom -> top (by immutable zpos):");
	for (i = 0; i < c->plane_count; i++)
		klog("  layer %d: plane id=%u %s zpos=%d", i, c->planes[i].id,
		     plane_type_name(c->planes[i].type), c->planes[i].zpos);
	klog("screen: connector %u \"%s\" %dx%d", c->conn_id, c->mode.name,
	     c->width, c->height);

	if (c->width <= 0 || c->height <= 0) {
		klog("connector reports a %dx%d mode -- refusing to modeset",
		     c->width, c->height);
		return -1;
	}
	return 0;
}

static void free_planes(zctx *c)
{
	int i;

	for (i = 0; i < c->plane_count; i++) {
		free(c->planes[i].formats);
		c->planes[i].formats = NULL;
	}
	c->plane_count = 0;
}

/* --------------------------------------------------------------- buffers --- */

/*
 * One scanout buffer.  Allocated with DRM_IOCTL_MODE_CREATE_DUMB on the
 * mediatek node so the backing memory is physically contiguous
 * (mtk_gem_dumb_create -> dma_alloc_wc), which this driver requires for
 * scanout.  pitch and size come back from the kernel; we never compute them.
 */
typedef struct {
	int used;
	uint32_t handle;
	uint32_t fb_id;
	uint32_t fourcc;
	uint32_t w;
	uint32_t h;
	uint32_t bpp;
	uint32_t pitch;
	uint64_t size;
	uint64_t map_off;
	void *map;
} zbuf;

static zbuf g_bufs[MAX_BUFS];

static zbuf *buf_by_fb(uint32_t fb)
{
	int i;

	for (i = 0; i < MAX_BUFS; i++)
		if (g_bufs[i].used && g_bufs[i].fb_id == fb)
			return &g_bufs[i];
	return NULL;
}

static zbuf *zbuf_alloc(uint32_t w, uint32_t h, uint32_t fourcc,
			const char *tag)
{
	struct drm_mode_create_dumb creq;
	struct drm_mode_map_dumb mreq;
	int bpp = fourcc_bpp(fourcc);
	int idx, ret;
	zbuf *b;
	char fn[5];

	for (idx = 0; idx < MAX_BUFS; idx++)
		if (!g_bufs[idx].used)
			break;
	if (idx == MAX_BUFS) {
		klog("%s: out of buffer slots (%d)", tag, MAX_BUFS);
		return NULL;
	}
	if (bpp == 0 || w == 0 || h == 0) {
		klog("%s: bad buffer request %ux%u bpp=%d", tag, w, h, bpp);
		return NULL;
	}

	memset(&creq, 0, sizeof(creq));
	creq.width = w;
	creq.height = h;
	creq.bpp = (uint32_t)bpp;
	errno = 0;
	ret = norm_ret(drmIoctl(g.fd, DRM_IOCTL_MODE_CREATE_DUMB, &creq));
	if (ret) {
		log_errno(ret, "CREATE_DUMB failed");
		return NULL;
	}

	memset(&mreq, 0, sizeof(mreq));
	mreq.handle = creq.handle;
	errno = 0;
	ret = norm_ret(drmIoctl(g.fd, DRM_IOCTL_MODE_MAP_DUMB, &mreq));
	if (ret) {
		struct drm_mode_destroy_dumb dreq;

		log_errno(ret, "MAP_DUMB failed");
		dreq.handle = creq.handle;
		errno = 0;
		(void)norm_ret(drmIoctl(g.fd, DRM_IOCTL_MODE_DESTROY_DUMB,
					&dreq));
		return NULL;
	}

	b = &g_bufs[idx];
	memset(b, 0, sizeof(*b));
	errno = 0;
	b->map = mmap(NULL, creq.size, PROT_READ | PROT_WRITE, MAP_SHARED, g.fd,
		      (off_t)mreq.offset);
	if (b->map == MAP_FAILED) {
		struct drm_mode_destroy_dumb dreq;

		klog("%s: mmap(%llu B) failed: %s", tag,
		     (unsigned long long)creq.size, strerror(errno));
		b->map = NULL;
		dreq.handle = creq.handle;
		errno = 0;
		(void)norm_ret(drmIoctl(g.fd, DRM_IOCTL_MODE_DESTROY_DUMB,
					&dreq));
		return NULL;
	}

	{
		uint32_t handles[4] = { creq.handle, 0, 0, 0 };
		uint32_t pitches[4] = { creq.pitch, 0, 0, 0 };
		uint32_t offsets[4] = { 0, 0, 0, 0 };
		uint32_t fb = 0;

		errno = 0;
		ret = norm_ret(drmModeAddFB2(g.fd, w, h, fourcc, handles,
					     pitches, offsets, &fb, 0));
		if (ret) {
			struct drm_mode_destroy_dumb dreq;

			klog("%s: AddFB2 %s %ux%u pitch=%u REJECTED: %d (%s)",
			     tag, fourcc_name(fourcc, fn), w, h, creq.pitch, ret,
			     strerror(-ret));
			if (ret == -EINVAL)
				klog("    -> the format must be in the plane's "
				     "format list, and width >= "
				     "mode_config.min_width(64), height >= "
				     "min_height(64)");
			munmap(b->map, creq.size);
			b->map = NULL;
			dreq.handle = creq.handle;
			errno = 0;
			(void)norm_ret(drmIoctl(g.fd,
						DRM_IOCTL_MODE_DESTROY_DUMB,
						&dreq));
			return NULL;
		}
		b->fb_id = fb;
	}

	b->used = 1;
	b->handle = creq.handle;
	b->fourcc = fourcc;
	b->w = w;
	b->h = h;
	b->bpp = (uint32_t)bpp;
	b->pitch = creq.pitch;
	b->size = creq.size;
	b->map_off = mreq.offset;

	klog("%s: buf fb=%u handle=%u %s %ux%u bpp=%u pitch=%u size=%llu",
	     tag, b->fb_id, b->handle, fourcc_name(fourcc, fn), w, h, b->bpp,
	     b->pitch, (unsigned long long)b->size);
	return b;
}

/* ----------------------------------------------------------------- fills --- */

typedef struct {
	uint32_t r, g, b, a;
} zrgb;

static void yuv_from_rgb(zrgb c, int *y, int *u, int *v)
{
	int r = (int)c.r, g = (int)c.g, b = (int)c.b;

	/* BT.601 limited range */
	*y = 16 + (66 * r + 129 * g + 25 * b + 128) / 256;
	*u = 128 + (-38 * r - 74 * g + 112 * b + 128) / 256;
	*v = 128 + (112 * r - 94 * g - 18 * b + 128) / 256;
	if (*y < 16) *y = 16;
	if (*y > 235) *y = 235;
	if (*u < 0) *u = 0;
	if (*u > 255) *u = 255;
	if (*v < 0) *v = 0;
	if (*v > 255) *v = 255;
}

/*
 * Write one pixel (one 4-byte macro pixel for UYVY/YUYV).
 *
 * DRM packed formats are little-endian words whose most significant bits hold
 * the left-most channel, e.g.
 *   include/uapi/drm/drm_fourcc.h:193  XRGB8888  [31:0] x:R:G:B little endian
 *   include/uapi/drm/drm_fourcc.h:189  RGB888    [23:0] R:G:B   little endian
 * so the in-memory byte order is B,G,R,x  and  B,G,R  respectively (and
 * BGR888 / and so on are the byte-swapped mirror image).
 */
static void put_pixel(uint8_t *p, uint32_t fourcc, zrgb c)
{
	uint8_t r = (uint8_t)c.r, g = (uint8_t)c.g, b = (uint8_t)c.b;
	uint8_t a = (uint8_t)c.a;
	/* the "X" channel of an XRGB/BGRX format is ignored by the hardware
	 * (mtk_disp_ovl.c:549-559 enables CONST_BLEND when the format has no
	 * alpha), so write an explicit 0 rather than an ambiguous 0xff */
	uint8_t x = 0;

	switch (fourcc) {
	case DRM_FORMAT_XRGB8888:
	case DRM_FORMAT_ARGB8888: {
		uint32_t v = ((uint32_t)(fourcc == DRM_FORMAT_XRGB8888 ? x : a)
			      << 24) | ((uint32_t)r << 16) |
			     ((uint32_t)g << 8) | b;

		memcpy(p, &v, 4);
		break;
	}
	case DRM_FORMAT_XBGR8888:
	case DRM_FORMAT_ABGR8888: {
		uint32_t v = ((uint32_t)(fourcc == DRM_FORMAT_XBGR8888 ? x : a)
			      << 24) | ((uint32_t)b << 16) |
			     ((uint32_t)g << 8) | r;

		memcpy(p, &v, 4);
		break;
	}
	case DRM_FORMAT_BGRX8888:
	case DRM_FORMAT_BGRA8888: {
		uint32_t v = ((uint32_t)b << 24) | ((uint32_t)g << 16) |
			     ((uint32_t)r << 8) |
			     (fourcc == DRM_FORMAT_BGRX8888 ? x : a);

		memcpy(p, &v, 4);
		break;
	}
	case DRM_FORMAT_RGBX8888:
	case DRM_FORMAT_RGBA8888: {
		uint32_t v = ((uint32_t)r << 24) | ((uint32_t)g << 16) |
			     ((uint32_t)b << 8) |
			     (fourcc == DRM_FORMAT_RGBX8888 ? x : a);

		memcpy(p, &v, 4);
		break;
	}
	case DRM_FORMAT_RGB888:
		p[0] = b;
		p[1] = g;
		p[2] = r;
		break;
	case DRM_FORMAT_BGR888:
		p[0] = r;
		p[1] = g;
		p[2] = b;
		break;
	case DRM_FORMAT_RGB565:
	case DRM_FORMAT_BGR565: {
		uint16_t v = (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) |
					((b >> 3) << 0));

		memcpy(p, &v, 2);
		break;
	}
	case DRM_FORMAT_UYVY:
	case DRM_FORMAT_YUYV: {
		int y, u, v;

		yuv_from_rgb(c, &y, &u, &v);
		if (fourcc == DRM_FORMAT_UYVY) {
			p[0] = (uint8_t)u;
			p[1] = (uint8_t)y;
			p[2] = (uint8_t)v;
			p[3] = (uint8_t)y;
		} else {
			p[0] = (uint8_t)y;
			p[1] = (uint8_t)u;
			p[2] = (uint8_t)y;
			p[3] = (uint8_t)v;
		}
		break;
	}
	default:
		break;
	}
}

static void fill_rect(zbuf *b, int x0, int y0, int w, int h, zrgb c)
{
	int bpp = (int)b->bpp / 8;
	int yuv = fourcc_is_yuv_packed(b->fourcc);
	int x, y;

	if (x0 < 0) {
		w += x0;
		x0 = 0;
	}
	if (y0 < 0) {
		h += y0;
		y0 = 0;
	}
	if (x0 + w > (int)b->w)
		w = (int)b->w - x0;
	if (y0 + h > (int)b->h)
		h = (int)b->h - y0;
	if (w <= 0 || h <= 0)
		return;

	for (y = y0; y < y0 + h; y++) {
		uint8_t *row = (uint8_t *)b->map + (size_t)y * b->pitch;

		if (bpp == 4 || (bpp == 2 && !yuv)) {
			uint8_t tmp[4];
			uint32_t v32 = 0;
			uint16_t v16 = 0;

			memset(tmp, 0, sizeof(tmp));
			put_pixel(tmp, b->fourcc, c);
			memcpy(&v32, tmp, 4);
			memcpy(&v16, tmp, 2);
			for (x = x0; x < x0 + w; x++) {
				if (bpp == 4)
					memcpy(row + (size_t)x * 4, &v32, 4);
				else
					memcpy(row + (size_t)x * 2, &v16, 2);
			}
		} else {
			/* 24bpp, or UYVY/YUYV where one macro pixel is 2 px */
			int step = yuv ? 2 : 1;

			for (x = x0; x < x0 + w; x += step)
				put_pixel(row + (size_t)x * (size_t)bpp,
					  b->fourcc, c);
		}
	}
}

static void fill_checker(zbuf *b, int cell, zrgb c1, zrgb c2)
{
	int x, y;

	for (y = 0; y < (int)b->h; y += cell)
		for (x = 0; x < (int)b->w; x += cell)
			fill_rect(b, x, y, cell, cell,
				  (((x / cell) + (y / cell)) & 1) ? c2 : c1);
}

static void fill_quadrants(zbuf *b, zrgb tl, zrgb tr, zrgb bl, zrgb br)
{
	int hw = (int)b->w / 2, hh = (int)b->h / 2;

	fill_rect(b, 0, 0, hw, hh, tl);
	fill_rect(b, hw, 0, (int)b->w - hw, hh, tr);
	fill_rect(b, 0, hh, hw, (int)b->h - hh, bl);
	fill_rect(b, hw, hh, (int)b->w - hw, (int)b->h - hh, br);
}

#define RGB(r, g, b) (zrgb) { (r), (g), (b), 0xff }

/* ----------------------------------------------------------------- commit --- */

typedef struct {
	int plane;		/* index into g.planes[]          */
	int enabled;
	uint32_t fb;
	int32_t x, y;		/* CRTC_X / CRTC_Y                */
	uint32_t w, h;		/* CRTC_W / CRTC_H, pixels        */
	uint32_t sx, sy;	/* SRC_X / SRC_Y, 16.16           */
	uint32_t sw, sh;	/* SRC_W / SRC_H, 16.16           */
	int set_alpha;
	uint32_t alpha;		/* 0..0xffff                      */
	int blend;		/* -1 = leave alone               */
} zlayer;

typedef struct {
	zlayer l[MAX_PLANES];
	int n;
	/*
	 * When set, planes that are NOT listed in this scene are explicitly
	 * disabled (FB_ID=0 + CRTC_ID=0).  The `disable` sub-test clears it on
	 * purpose to demonstrate the stale-layer behaviour.
	 */
	int disable_unlisted;
} zscene;

static void scene_reset(zscene *s)
{
	memset(s, 0, sizeof(*s));
	s->disable_unlisted = 1;
}

static zlayer *scene_add(zscene *s, int plane)
{
	zlayer *l;

	if (s->n >= MAX_PLANES)
		return NULL;
	l = &s->l[s->n++];
	memset(l, 0, sizeof(*l));
	l->plane = plane;
	l->enabled = 1;
	l->blend = -1;
	return l;
}

/* Convenience: show a whole buffer at (x,y) with no scaling and no crop. */
static zlayer *scene_put(zscene *s, int plane, const zbuf *b, int32_t x,
			 int32_t y)
{
	zlayer *l = scene_add(s, plane);

	if (!l)
		return NULL;
	l->fb = b->fb_id;
	l->x = x;
	l->y = y;
	l->w = b->w;
	l->h = b->h;
	l->sx = 0;
	l->sy = 0;
	l->sw = b->w << 16;
	l->sh = b->h << 16;
	return l;
}

enum commit_mode {
	COMMIT_DRY,		/* DRM_MODE_ATOMIC_TEST_ONLY: validate only      */
	COMMIT_SYNC,		/* blocking commit                               */
	COMMIT_EVENT,		/* NONBLOCK + PAGE_FLIP_EVENT, then poll         */
};

static int g_event_mode;
static int g_test_only;

static enum commit_mode cmode(void)
{
	if (g_test_only)
		return COMMIT_DRY;
	return g_event_mode ? COMMIT_EVENT : COMMIT_SYNC;
}

static int g_flip_events;
static unsigned int g_flip_seq;

static void on_page_flip(int fd, unsigned int seq, unsigned int sec,
			 unsigned int usec, void *ud)
{
	(void)fd;
	(void)sec;
	(void)usec;
	(void)ud;
	g_flip_seq = seq;
	g_flip_events++;
}

static void on_page_flip2(int fd, unsigned int seq, unsigned int sec,
			  unsigned int usec, unsigned int crtc_id, void *ud)
{
	(void)crtc_id;
	on_page_flip(fd, seq, sec, usec, ud);
}

static int wait_flip_event(int fd, int timeout_ms)
{
	struct pollfd pfd;
	drmEventContext ev;
	int ret;

	pfd.fd = fd;
	pfd.events = POLLIN;
	pfd.revents = 0;

	ret = poll(&pfd, 1, timeout_ms);
	if (ret < 0)
		return -errno;
	if (ret == 0)
		return -ETIMEDOUT;

	memset(&ev, 0, sizeof(ev));
	/*
	 * libdrm.a in this tree was checked by disassembly: drmHandleEvent()
	 * performs the read() itself and dispatches on evctx->version -->
	 *   version >= 3 : page_flip_handler2 when non-NULL, otherwise
	 *   version == 2 : page_flip_handler
	 * so we set version = DRM_EVENT_CONTEXT_VERSION and supply both.
	 */
	ev.version = DRM_EVENT_CONTEXT_VERSION;
	ev.page_flip_handler = on_page_flip;
	ev.page_flip_handler2 = on_page_flip2;
	ev.vblank_handler = NULL;

	errno = 0;
	ret = drmHandleEvent(fd, &ev);
	if (ret) {
		klog("drmHandleEvent failed: %s", strerror(errno));
		return -EIO;
	}
	return g_flip_events ? 0 : -EAGAIN;
}

/*
 * Build and submit one atomic request.
 *
 * Every real commit is preceded by a DRM_MODE_ATOMIC_TEST_ONLY pass: the
 * kernel runs all ->atomic_check() callbacks but touches no hardware, so a
 * rejection there is a pure validation verdict with zero risk, and it lets the
 * log separate "rejected by validation" from "valid but the commit failed".
 */
static int zcommit(const char *tag, zscene *s, enum commit_mode mode)
{
	drmModeAtomicReq *req;
	uint32_t flags = 0;
	int ret, i;
	double t0, t1, t2;

	t0 = now_ms();
	req = drmModeAtomicAlloc();
	if (!req) {
		klog("%s: drmModeAtomicAlloc failed", tag);
		return -ENOMEM;
	}

	/* ---- the first commit must look like a full modeset (header note 9) */
	if (!g.modeset_done) {
		if (!g.mode_blob) {
			errno = 0;
			ret = norm_ret(drmModeCreatePropertyBlob(g.fd, &g.mode,
								 sizeof(g.mode),
								 &g.mode_blob));
			if (ret) {
				log_errno(ret, "drmModeCreatePropertyBlob(MODE)");
				drmModeAtomicFree(req);
				return ret;
			}
			klog("%s: created MODE blob id=%u (%ux%u htotal=%u "
			     "vtotal=%u)", tag, g.mode_blob, g.mode.hdisplay,
			     g.mode.vdisplay, g.mode.htotal, g.mode.vtotal);
		}
		drmModeAtomicAddProperty(req, g.conn_id, g.conn_p_crtc,
					 g.crtc_id);
		drmModeAtomicAddProperty(req, g.crtc_id, g.crtc_p_mode,
					 g.mode_blob);
		drmModeAtomicAddProperty(req, g.crtc_id, g.crtc_p_active, 1);
		flags |= DRM_MODE_ATOMIC_ALLOW_MODESET;
	}

	/* ---- planes ------------------------------------------------------ */
	for (i = 0; i < g.plane_count; i++) {
		zplane *pl = &g.planes[i];
		zlayer *l = NULL;
		int j;

		for (j = 0; j < s->n; j++)
			if (s->l[j].plane == i)
				l = &s->l[j];

		if (!l || !l->enabled) {
			if (!l && !s->disable_unlisted)
				continue;	/* deliberately leave it alone */
			/* explicit disable: BOTH ids must be zeroed, note 5 */
			if (drmModeAtomicAddProperty(req, pl->id, pl->p_fb, 0) < 0 ||
			    drmModeAtomicAddProperty(req, pl->id, pl->p_crtc, 0) < 0) {
				klog("%s: AddProperty(disable plane %u) failed",
				     tag, pl->id);
				drmModeAtomicFree(req);
				return -ENOMEM;
			}
			continue;
		}

		if (drmModeAtomicAddProperty(req, pl->id, pl->p_fb, l->fb) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_crtc,
					     g.crtc_id) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_cx,
					     (uint64_t)(int64_t)l->x) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_cy,
					     (uint64_t)(int64_t)l->y) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_cw, l->w) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_ch, l->h) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_sx, l->sx) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_sy, l->sy) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_sw, l->sw) < 0 ||
		    drmModeAtomicAddProperty(req, pl->id, pl->p_sh, l->sh) < 0) {
			klog("%s: AddProperty(plane %u) failed", tag, pl->id);
			drmModeAtomicFree(req);
			return -ENOMEM;
		}
		if (l->set_alpha && pl->p_alpha &&
		    drmModeAtomicAddProperty(req, pl->id, pl->p_alpha,
					     l->alpha) < 0) {
			klog("%s: AddProperty(alpha plane %u) failed", tag,
			     pl->id);
			drmModeAtomicFree(req);
			return -ENOMEM;
		}
		if (l->blend >= 0 && pl->p_blend &&
		    drmModeAtomicAddProperty(req, pl->id, pl->p_blend,
					     (uint32_t)l->blend) < 0) {
			klog("%s: AddProperty(blend plane %u) failed", tag,
			     pl->id);
			drmModeAtomicFree(req);
			return -ENOMEM;
		}
	}

	/* ---- describe the request --------------------------------------- */
	klog("%s: request%s", tag,
	     mode == COMMIT_DRY ? " (TEST_ONLY, validation only)" : "");
	for (i = 0; i < s->n; i++) {
		zlayer *l = &s->l[i];
		zplane *pl = &g.planes[l->plane];
		zbuf *b = buf_by_fb(l->fb);
		char fn[5] = "?";

		if (!l->enabled) {
			klog("   layer %d plane=%u zpos=%d DISABLED "
			     "(FB_ID=0 CRTC_ID=0)", l->plane, pl->id, pl->zpos);
			continue;
		}
		if (b)
			fourcc_name(b->fourcc, fn);
		klog("   layer %d plane=%u zpos=%d fb=%u %s %ux%u "
		     "crtc=(%d,%d) %ux%u src=+%u,%u %upx", l->plane, pl->id,
		     pl->zpos, l->fb, fn, b ? b->w : l->sw >> 16,
		     b ? b->h : l->sh >> 16, l->x, l->y, l->w, l->h,
		     l->sx >> 16, l->sy >> 16, l->sw >> 16);
		if (l->set_alpha)
			klog("   layer %d plane=%u alpha=0x%04x (hardware keeps the "
			     "low 8 bits -> 0x%02x)", l->plane, pl->id, l->alpha,
			     l->alpha & 0xff);
		if (l->blend >= 0)
			klog("   layer %d plane=%u blend=%d (%s)", l->plane, pl->id,
			     l->blend, blend_enum_name(l->blend, pl));
	}
	for (i = 0; i < g.plane_count; i++) {
		int listed = 0, j;

		for (j = 0; j < s->n; j++)
			if (s->l[j].plane == i)
				listed = 1;
		if (listed)
			continue;
		if (s->disable_unlisted)
			klog("   plane %u zpos=%d not listed -> explicit DISABLE "
			     "(FB_ID=0 CRTC_ID=0)", g.planes[i].id,
			     g.planes[i].zpos);
		else
			klog("   plane %u zpos=%d not listed -> LEFT RUNNING "
			     "(SRC_CON bit and buffer address kept, see README)",
			     g.planes[i].id, g.planes[i].zpos);
	}

	/* ---- 0. --test-only: validate and stop --------------------------- */
	if (mode == COMMIT_DRY) {
		errno = 0;
		ret = norm_ret(drmModeAtomicCommit(
			g.fd, req, flags | DRM_MODE_ATOMIC_TEST_ONLY, NULL));
		if (ret) {
			log_errno(ret, "TEST_ONLY validation REJECTED");
			klog("%s: FAIL (validation only, nothing was committed)",
			     tag);
			drmModeAtomicFree(req);
			return ret;
		}
		klog("%s: validation OK (TEST_ONLY, %0.1f ms) -- nothing was "
		     "committed to hardware", tag, now_ms() - t0);
		drmModeAtomicFree(req);
		return 0;
	}

	/* ---- 1. dry run: validation only --------------------------------- */
	{
		uint32_t df = flags | DRM_MODE_ATOMIC_TEST_ONLY;

		errno = 0;
		ret = norm_ret(drmModeAtomicCommit(g.fd, req, df, NULL));
		if (ret) {
			log_errno(ret, "TEST_ONLY validation REJECTED");
			klog("%s: FAIL", tag);
			drmModeAtomicFree(req);
			return ret;
		}
		klog("%s: validation OK", tag);
	}

	/* ---- 2. the real commit ----------------------------------------- */
	if (mode == COMMIT_EVENT) {
		flags |= DRM_MODE_ATOMIC_NONBLOCK | DRM_MODE_PAGE_FLIP_EVENT;
		g_flip_events = 0;
	}
	errno = 0;
	ret = norm_ret(drmModeAtomicCommit(g.fd, req, flags, NULL));
	t1 = now_ms();
	if (ret) {
		if (ret == -EINVAL && !(flags & DRM_MODE_ATOMIC_ALLOW_MODESET)) {
			/*
			 * An update can legitimately need a modeset.  Retry once
			 * with ALLOW_MODESET so the log says which it was instead
			 * of just "EINVAL".
			 */
			klog("%s: commit rejected with -EINVAL without "
			     "ALLOW_MODESET -- retrying with ALLOW_MODESET", tag);
			errno = 0;
			ret = norm_ret(drmModeAtomicCommit(
				g.fd, req,
				(flags & ~DRM_MODE_ATOMIC_NONBLOCK) |
					DRM_MODE_ATOMIC_ALLOW_MODESET, NULL));
			if (!ret)
				klog("%s: NOTE the kernel required a modeset for "
				     "this update", tag);
			t1 = now_ms();
		}
		if (ret) {
			log_errno(ret, "atomic commit REJECTED");
			klog("%s: FAIL", tag);
			drmModeAtomicFree(req);
			return ret;
		}
	}
	drmModeAtomicFree(req);
	t2 = now_ms();

	g.modeset_done = 1;

	if (mode == COMMIT_EVENT) {
		int wret = wait_flip_event(g.fd, EVENT_TIMEOUT_MS);

		t2 = now_ms();
		if (!wret) {
			klog("%s: commit accepted in %0.1f ms, PAGE_FLIP event "
			     "received (seq=%u) %0.1f ms later -- the CRTC really "
			     "reached vblank", tag, t1 - t0, g_flip_seq, t2 - t1);
		} else {
			klog("%s: commit accepted in %0.1f ms, but NO page-flip "
			     "event within %d ms (%s)", tag, t1 - t0,
			     EVENT_TIMEOUT_MS, strerror(-wret));
			klog("   -> the atomic ioctl succeeded; only the vblank "
			     "notification is missing. mtk_crtc_atomic_begin() "
			     "arms it with WARN_ON(drm_crtc_vblank_get(crtc) != 0) "
			     "(mtk_crtc.c:856-866), which fails when the CRTC is "
			     "not scanning out -- e.g. mtk_crtc_atomic_enable() "
			     "bailed out before drm_crtc_vblank_on()");
		}
	} else {
		klog("%s: commit accepted (%0.1f ms; a blocking commit already "
		     "waited for a vblank, drm_atomic_helper.c:1883-1927)", tag,
		     t2 - t0);
	}
	return 0;
}

/* ----------------------------------------------------------------- results --- */

static int g_pass;
static int g_fail;
static int g_delay_ms = DEFAULT_DELAY_MS;
static int g_teardown = 1;

static void result(const char *name, int ok, const char *lookfor)
{
	if (ok) {
		g_pass++;
		klog("RESULT %s: PASS", name);
	} else {
		g_fail++;
		klog("RESULT %s: FAIL", name);
	}
	if (lookfor && *lookfor)
		klog("   look for on the panel: %s", lookfor);
}

/* Free every live buffer.  Only safe once nothing scans out of them. */
static void bufs_free_all(void)
{
	int i;

	for (i = 0; i < MAX_BUFS; i++) {
		zbuf *b = &g_bufs[i];

		if (!b->used)
			continue;
		if (b->fb_id) {
			errno = 0;
			if (norm_ret(drmModeRmFB(g.fd, b->fb_id)))
				klog("RmFB(%u) failed: %s", b->fb_id,
				     strerror(errno));
			b->fb_id = 0;
		}
		if (b->map) {
			munmap(b->map, b->size);
			b->map = NULL;
		}
		if (b->handle) {
			struct drm_mode_destroy_dumb dreq;

			dreq.handle = b->handle;
			errno = 0;
			if (norm_ret(drmIoctl(g.fd, DRM_IOCTL_MODE_DESTROY_DUMB,
					      &dreq)))
				klog("DESTROY_DUMB(%u) failed: %s", b->handle,
				     strerror(errno));
			b->handle = 0;
		}
		b->used = 0;
	}
}

/*
 * End-of-sub-test teardown.  First stop every OVL layer from scanning out our
 * memory (an explicit all-planes-off commit), then free the buffers.  Doing it
 * in this order is what makes RmFB + DESTROY_DUMB safe; freeing while the OVL
 * still points at the memory would leave the display engine reading freed DMA
 * pages.
 */
static void test_teardown(const char *tag)
{
	zscene s;
	int ret;

	if (!g.plane_count || !g.modeset_done || g_test_only) {
		/* nothing was ever handed to the display engine */
		bufs_free_all();
		return;
	}
	scene_reset(&s);
	ret = zcommit(tag, &s, COMMIT_SYNC);
	if (ret)
		klog("%s: all-planes-off commit failed (%d) -- freeing the "
		     "buffers anyway, the OVL may hold stale pointers", tag, ret);
	else
		klog("%s: all planes disabled, buffer memory can be released "
		     "safely", tag);
	bufs_free_all();
}

/* -------------------------------------------------------------- sub-tests --- */

/* ---- basic: modeset + one full-screen RGB565 4-quadrant pattern --------- */
static int test_basic(void)
{
	zbuf *b;
	zscene s;
	int ret, ok;

	klog("---- basic: first modeset + full-screen RGB565 4-quadrant "
	     "pattern ----");

	b = zbuf_alloc((uint32_t)g.width, (uint32_t)g.height, DRM_FORMAT_RGB565,
		       "basic/bg");
	if (!b) {
		result("basic", 0, NULL);
		return -EXIT_NOBUF;
	}

	fill_quadrants(b, RGB(255, 0, 0), RGB(0, 255, 0), RGB(0, 0, 255),
		       RGB(255, 255, 255));

	scene_reset(&s);
	(void)scene_put(&s, 0, b, 0, 0);

	ret = zcommit("basic/modeset", &s, cmode());
	ok = (ret == 0);

	/*
	 * Second commit on an already-enabled CRTC, explicitly asking for the
	 * page-flip event: this is where we find out whether the driver really
	 * delivers vblank notifications, independently of the modeset.
	 * Skipped under --test-only, which must not commit anything.
	 */
	if (ok && !g_test_only) {
		ret = zcommit("basic/flip-event", &s, COMMIT_EVENT);
		if (ret)
			klog("basic: note the second (non-modeset) commit failed");
	}

	result("basic", ok,
	       "4 quadrants: RED top-left, GREEN top-right, BLUE "
	       "bottom-left, WHITE bottom-right. This is the first modeset; if "
	       "the panel keeps showing the frozen LK frame then nothing in the "
	       "pipeline is being updated (check mtk_crtc_atomic_enable / "
	       "mtk_crtc_ddp_hw_init failures in dmesg)");

	msleep(g_delay_ms);
	if (g_teardown)
		test_teardown("basic-end");
	return ok ? 0 : -1;
}

/* ---- layers: 2/3/4 planes, positioned, overlapping, in zpos order ------- */
static int test_layers(void)
{
	zbuf *bg, *b1, *b2, *b3;
	zscene s;
	int ret, ok = 1;
	int w1, h1, x1, y1, x2, y2, x3, y3;

	klog("---- layers: commit 2, then 3, then 4 planes in zpos order ----");

	if (g.plane_count < 4) {
		klog("layers: needs 4 planes, this node has %d", g.plane_count);
		result("layers", 0, NULL);
		return -1;
	}

	w1 = g.width * 2 / 5;
	h1 = g.height * 2 / 5;
	x1 = g.width / 15;
	y1 = g.height / 16;
	x2 = x1 + w1 / 3;
	y2 = y1 + h1 / 3;
	x3 = x2 + w1 / 3;
	y3 = y2 + h1 / 3;

	klog("layers: screen %dx%d, L1 (%d,%d) %dx%d, L2 (%d,%d) %dx%d, "
	     "L3 (%d,%d) %dx%d", g.width, g.height, x1, y1, w1, h1, x2, y2, w1,
	     h1, x3, y3, w1, h1);
	if (x1 < 0 || y1 < 0 || w1 < 64 || h1 < 64 || x3 + w1 > g.width ||
	    y3 + h1 > g.height) {
		klog("layers: screen %dx%d cannot host the planned rectangles "
		     "(need >= 240x320)", g.width, g.height);
		result("layers", 0, NULL);
		return -1;
	}

	bg = zbuf_alloc((uint32_t)g.width, (uint32_t)g.height,
			DRM_FORMAT_XRGB8888, "layers/bg");
	b1 = zbuf_alloc((uint32_t)w1, (uint32_t)h1, DRM_FORMAT_XRGB8888,
			"layers/p1");
	b2 = zbuf_alloc((uint32_t)w1, (uint32_t)h1, DRM_FORMAT_RGB565,
			"layers/p2");
	b3 = zbuf_alloc((uint32_t)w1, (uint32_t)h1, DRM_FORMAT_RGB888,
			"layers/p3");
	if (!bg || !b1 || !b2 || !b3) {
		result("layers", 0, NULL);
		bufs_free_all();
		return -EXIT_NOBUF;
	}

	/* dark background with a 1px grid so overlaps are easy to judge */
	fill_rect(bg, 0, 0, (int)bg->w, (int)bg->h, RGB(20, 20, 20));
	{
		int x, y;

		for (y = 0; y < (int)bg->h; y += 32)
			fill_rect(bg, 0, y, (int)bg->w, 1, RGB(0, 90, 90));
		for (x = 0; x < (int)bg->w; x += 32)
			fill_rect(bg, x, 0, 1, (int)bg->h, RGB(0, 90, 90));
	}
	fill_rect(b1, 0, 0, (int)b1->w, (int)b1->h, RGB(255, 0, 0));
	fill_rect(b2, 0, 0, (int)b2->w, (int)b2->h, RGB(0, 255, 0));
	fill_rect(b3, 0, 0, (int)b3->w, (int)b3->h, RGB(0, 0, 255));

	/* --- step 1: 2 planes --------------------------------------------- */
	scene_reset(&s);
	(void)scene_put(&s, 0, bg, 0, 0);
	(void)scene_put(&s, 1, b1, x1, y1);
	ret = zcommit("layers/N2", &s, cmode());
	result("layers.N2", ret == 0,
	       ret ? NULL : "dark grid background plus one free-standing solid "
	       "RED rectangle");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	/* --- step 2: 3 planes -------------------------------------------- */
	(void)scene_put(&s, 2, b2, x2, y2);
	ret = zcommit("layers/N3", &s, cmode());
	result("layers.N3", ret == 0,
	       ret ? NULL : "a GREEN rectangle that overlaps RED and must be ON "
	       "TOP of it (plane2 zpos=2 > plane1 zpos=1)");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	/* --- step 3: 4 planes, the CURSOR plane used as a normal layer ---- */
	(void)scene_put(&s, 3, b3, x3, y3);
	ret = zcommit("layers/N4", &s, cmode());
	result("layers.N4", ret == 0,
	       ret ? NULL : "a BLUE rectangle on top of GREEN on top of RED. "
	       "plane[3] is the CURSOR-type plane and composites like any other "
	       "OVL layer on this driver");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	if (g_teardown)
		test_teardown("layers-end");
	return ok ? 0 : -1;
}

/* ---- formats: submit every format the plane advertises ----------------- */
static int test_formats(void)
{
	zbuf *bg;
	zscene s;
	int ret, ok = 1;
	uint32_t i;
	int pid;
	zplane *pl;
	static const zrgb colors[] = {
		{ 255, 0, 0, 0xff },   { 0, 255, 0, 0xff },   { 0, 0, 255, 0xff },
		{ 255, 255, 0, 0xff }, { 255, 0, 255, 0xff }, { 0, 255, 255, 0xff },
		{ 255, 255, 255, 0xff }, { 128, 128, 128, 0xff },
		{ 255, 128, 0, 0xff }, { 64, 0, 128, 0xff },  { 0, 128, 128, 0xff },
	};

	pid = (g.plane_count > 1) ? 1 : 0;
	pl = &g.planes[pid];
	klog("---- formats: submit every format advertised by plane %u "
	     "(zpos %d) on top of an XRGB8888 background ----",
	     pl->id, pl->zpos);

	if (pl->count_formats == 0) {
		klog("formats: plane %u advertises no formats at all", pl->id);
		result("formats", 0, NULL);
		return -1;
	}

	bg = zbuf_alloc((uint32_t)g.width, (uint32_t)g.height,
			DRM_FORMAT_XRGB8888, "formats/bg");
	if (!bg) {
		result("formats", 0, NULL);
		return -EXIT_NOBUF;
	}
	fill_rect(bg, 0, 0, (int)bg->w, (int)bg->h, RGB(30, 30, 30));

	for (i = 0; i < pl->count_formats; i++) {
		uint32_t fcc = pl->formats[i];
		char fn[5];
		char tag[64];
		zbuf *b;

		if (fourcc_bpp(fcc) == 0) {
			klog("formats: %s(%#x) skipped, this tool has no bpp "
			     "mapping for it", fourcc_name(fcc, fn), fcc);
			continue;
		}
		snprintf(tag, sizeof(tag), "formats/%s", fourcc_name(fcc, fn));
		b = zbuf_alloc(64, 64, fcc, tag);
		if (!b) {
			result(tag, 0, NULL);
			ok = 0;
			continue;
		}
		fill_rect(b, 0, 0, 64, 64, colors[i % ARRAY_SIZE(colors)]);

		scene_reset(&s);
		(void)scene_put(&s, 0, bg, 0, 0);
		{
			zlayer *l = scene_add(&s, pid);

			l->fb = b->fb_id;
			l->x = 40;
			l->y = 40;
			l->w = 64;
			l->h = 64;
			l->sw = 64u << 16;
			l->sh = 64u << 16;
		}
		ret = zcommit(tag, &s, cmode());
		result(tag, ret == 0,
		       ret ? NULL : "a 64x64 solid-colour square at (40,40). "
		       "Channel order: DRM_FORMAT_RGB888 stores bytes B,G,R in "
		       "memory, BGR888 stores R,G,B; the 8888 formats store "
		       "B,G,R,A/x");
		if (ret)
			ok = 0;
		msleep(g_delay_ms / 2);
		/* the format buffers are freed together at the end, so the OVL
		 * never points at freed memory between iterations */
	}

	if (g_teardown)
		test_teardown("formats-end");
	return ok ? 0 : -1;
}

/* ---- alpha: ARGB8888 + plane alpha across all three blend modes -------- */
static int test_alpha(void)
{
	zbuf *bg, *top;
	zscene s;
	zlayer *l;
	int ret, ok = 1, i, nbl = 0;
	int tw, th, tx, ty;
	struct blmode {
		int val;
		const char *name;
		const char *expect;
	} bm[3];

	klog("---- alpha: ARGB8888 top layer, plane alpha, every blend mode ----");

	if (g.plane_count < 2) {
		klog("alpha: needs 2 planes (background + ARGB8888 top), this node "
		     "has %d", g.plane_count);
		result("alpha", 0, NULL);
		return -1;
	}
	if (g.width < 160 || g.height < 200) {
		klog("alpha: screen %dx%d too small for a 128x128 test layer "
		     "(need >= 160x200)", g.width, g.height);
		result("alpha", 0, NULL);
		return -1;
	}
	tw = 128;
	th = 128;
	tx = (g.width - tw) / 2;
	ty = (g.height - th) / 2;

	bg = zbuf_alloc((uint32_t)g.width, (uint32_t)g.height,
			DRM_FORMAT_XRGB8888, "alpha/bg");
	top = zbuf_alloc((uint32_t)tw, (uint32_t)th, DRM_FORMAT_ARGB8888,
			 "alpha/top");
	if (!bg || !top) {
		result("alpha", 0, NULL);
		bufs_free_all();
		return -EXIT_NOBUF;
	}

	fill_checker(bg, 16, RGB(0, 0, 0), RGB(255, 255, 255));

	/* 50% red, with the 0x80 already multiplied into the colour part so the
	 * result is the same under PREMULTI and COVERAGE only if the hardware
	 * treats them identically -- which is exactly what we are testing */
	{
		int x, y;

		for (y = 0; y < (int)top->h; y++)
			for (x = 0; x < (int)top->w; x++) {
				uint8_t *p = (uint8_t *)top->map +
					     (size_t)y * top->pitch +
					     (size_t)x * 4;
				zrgb c = { 128, 0, 0, 0x80 };

				put_pixel(p, top->fourcc, c);
			}
	}

	if (g.planes[1].v_premulti >= 0) {
		bm[nbl].val = g.planes[1].v_premulti;
		bm[nbl].name = "premultiplied";
		bm[nbl].expect =
			"the red square is blended ~50% over the checkerboard "
			"(plane alpha 0x80); mipixel RGB is not touched, so this "
			"shows the OVL's premultiplied path";
		nbl++;
	}
	if (g.planes[1].v_coverage >= 0) {
		bm[nbl].val = g.planes[1].v_coverage;
		bm[nbl].name = "coverage";
		bm[nbl].expect =
			"COVERAGE multiplies plane alpha * pixel alpha as well, "
			"so the square should look DARKER than in "
			"premultiplied mode (0x80 * 0x80 ~ 25% effective)";
		nbl++;
	}
	if (g.planes[1].v_none >= 0) {
		bm[nbl].val = g.planes[1].v_none;
		bm[nbl].name = "none";
		bm[nbl].expect =
			"None ignores the pixel alpha and uses the plane alpha "
			"(0x80) as a constant blend, so the square is a ~50% "
			"transparent red with hard edges";
		nbl++;
	}
	if (nbl == 0) {
		klog("alpha: plane 1 has no 'pixel blend mode' property");
		result("alpha", 0, NULL);
		bufs_free_all();
		return -1;
	}

	scene_reset(&s);
	(void)scene_put(&s, 0, bg, 0, 0);
	l = scene_put(&s, 1, top, tx, ty);
	l->set_alpha = 1;
	l->alpha = 0x8080;		/* 50% -> hardware sees 0x80 */

	for (i = 0; i < nbl; i++) {
		char tag[64];

		l->blend = bm[i].val;
		snprintf(tag, sizeof(tag), "alpha.%s", bm[i].name);
		ret = zcommit(tag, &s, cmode());
		result(tag, ret == 0, ret ? NULL : bm[i].expect);
		if (ret)
			ok = 0;
		msleep(g_delay_ms);
	}

	/* the two extremes of the plane alpha range */
	l->blend = (g.planes[1].v_coverage >= 0) ? g.planes[1].v_coverage :
		   bm[0].val;
	l->alpha = 0x0000;
	ret = zcommit("alpha.zero", &s, cmode());
	result("alpha.zero", ret == 0,
	       ret ? NULL : "with plane alpha 0 the square must DISAPPEAR "
	       "completely and only the checkerboard remains");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	l->alpha = 0xffff;
	ret = zcommit("alpha.opaque", &s, cmode());
	result("alpha.opaque", ret == 0,
	       ret ? NULL : "with plane alpha 0xffff the square must be an "
	       "OPAQUE flat red block, no checkerboard showing through");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	if (g_teardown)
		test_teardown("alpha-end");
	return ok ? 0 : -1;
}

/* ---- offscreen: partial/fully off-screen and tiny sizes ---------------- */
static int test_offscreen(void)
{
	zbuf *bg, *sq;
	zscene s;
	zlayer *l;
	int ret, ok = 1, c, rejected = 0;
	struct oc {
		const char *tag;
		int32_t x, y;
		uint32_t dw, dh, sw, sh;
		int expect_accept;
		const char *note;
	} cases[4];

	klog("---- offscreen: what the kernel really enforces ----");

	if (g.plane_count < 2) {
		klog("offscreen: needs 2 planes (background + test layer), this "
		     "node has %d", g.plane_count);
		result("offscreen", 0, NULL);
		return -1;
	}

	/* (a) 1x1 and 8x8 framebuffers ------------------------------------- */
	if (!zbuf_alloc(1, 1, DRM_FORMAT_XRGB8888, "offscreen/1x1")) {
		klog("offscreen: a 1x1 framebuffer was REJECTED (expected: "
		     "mode_config.min_width=64, min_height=64, "
		     "drm_framebuffer.c:267-275)");
		rejected++;
	}
	if (!zbuf_alloc(8, 8, DRM_FORMAT_XRGB8888, "offscreen/8x8")) {
		klog("offscreen: an 8x8 framebuffer was REJECTED (expected, "
		     "same reason)");
		rejected++;
	}
	result("offscreen.tiny_fb", rejected == 2,
	       rejected == 2 ? "nothing changes on screen; the point of this "
	       "step is the log: 1x1/8x8 FBs cannot exist here, 64x64 is the "
	       "smallest legal framebuffer" : NULL);
	if (rejected != 2) {
		klog("offscreen: a sub-64x64 framebuffer was ACCEPTED -- the "
		     "kernel/driver changed, re-read mtk_drm_drv.c min_width");
		ok = 0;
	}
	bufs_free_all();

	/* (b) 64x64 is the smallest legal FB: use it for the position cases  */
	bg = zbuf_alloc((uint32_t)g.width, (uint32_t)g.height,
			DRM_FORMAT_XRGB8888, "offscreen/bg");
	sq = zbuf_alloc(64, 64, DRM_FORMAT_XRGB8888, "offscreen/64x64");
	if (!bg || !sq) {
		result("offscreen", 0, NULL);
		bufs_free_all();
		return -EXIT_NOBUF;
	}
	fill_rect(bg, 0, 0, (int)bg->w, (int)bg->h, RGB(12, 12, 12));
	fill_rect(sq, 0, 0, 64, 64, RGB(255, 160, 0));

	c = 0;
	cases[c].tag = "offscreen/x=-16,y=-16";
	cases[c].x = -16;
	cases[c].y = -16;
	cases[c].dw = 64;
	cases[c].dh = 64;
	cases[c].sw = 64;
	cases[c].sh = 64;
	cases[c].expect_accept = 1;
	cases[c].note = "ACCEPTED, but clipped: the kernel intersects dst with "
			"the CRTC rect and shrinks SRC by the same amount "
			"(drm_atomic_helper.c:942-945 drm_rect_clip_scaled), so only "
			"48x48 of the square is visible at the top-left corner";
	c++;

	cases[c].tag = "offscreen/right+top";
	cases[c].x = g.width - 32;
	cases[c].y = -32;
	cases[c].dw = 64;
	cases[c].dh = 64;
	cases[c].sw = 64;
	cases[c].sh = 64;
	cases[c].expect_accept = 1;
	cases[c].note = "ACCEPTED and visibly clipped on the right and top "
			"edges";
	c++;

	cases[c].tag = "offscreen/8x8-dst";
	cases[c].x = 32;
	cases[c].y = 160;
	cases[c].dw = 8;
	cases[c].dh = 8;
	cases[c].sw = 8;
	cases[c].sh = 8;
	cases[c].expect_accept = 1;
	cases[c].note = "ACCEPTED: SRC is 8x8 pixels of the 64x64 FB (8<<16 == "
			"8) so there is no scaling, and an 8x8 *destination* "
			"rectangle is legal even though an 8x8 *framebuffer* is "
			"not -- these are two different kernel checks";
	c++;

	cases[c].tag = "offscreen/fully-outside";
	cases[c].x = g.width + 100;
	cases[c].y = 100;
	cases[c].dw = 64;
	cases[c].dh = 64;
	cases[c].sw = 64;
	cases[c].sh = 64;
	cases[c].expect_accept = 1;
	cases[c].note = "ACCEPTED but NOTHING must appear: dst does not "
			"intersect the CRTC, so drm_rect_clip_scaled() marks the "
			"plane invisible and mtk_plane_atomic_update() calls "
			"mtk_plane_atomic_disable() (mtk_plane.c:289-291)";
	c++;

	for (c = 0; c < (int)ARRAY_SIZE(cases); c++) {
		int accepted;

		scene_reset(&s);
		(void)scene_put(&s, 0, bg, 0, 0);
		l = scene_add(&s, 1);
		l->fb = sq->fb_id;
		l->x = cases[c].x;
		l->y = cases[c].y;
		l->w = cases[c].dw;
		l->h = cases[c].dh;
		l->sw = cases[c].sw << 16;
		l->sh = cases[c].sh << 16;

		ret = zcommit(cases[c].tag, &s, cmode());
		accepted = (ret == 0);
		klog("   %s: kernel %s the commit (expected %s)", cases[c].tag,
		     accepted ? "ACCEPTED" : "REJECTED",
		     cases[c].expect_accept ? "ACCEPTED" : "REJECTED");
		klog("   why: %s", cases[c].note);
		result(cases[c].tag, accepted == cases[c].expect_accept, NULL);
		if (accepted != cases[c].expect_accept)
			ok = 0;
		msleep(g_delay_ms);
	}

	if (g_teardown)
		test_teardown("offscreen-end");
	return ok ? 0 : -1;
}

/* ---- disable: prove that unused planes must be disabled explicitly ------ */
static int test_disable(void)
{
	zbuf *b[4];
	static const zrgb col[4] = {
		{ 20, 20, 60, 0xff }, { 255, 0, 0, 0xff },
		{ 0, 255, 0, 0xff }, { 0, 0, 255, 0xff },
	};
	zscene s;
	int ret, ok = 1, i;
	int bx, by, bw, bh;

	klog("---- disable: 4 layers -> 2 layers, with and without explicit "
	     "FB_ID=0/CRTC_ID=0 ----");

	if (g.plane_count < 4) {
		klog("disable: only %d planes available, this test needs 4",
		     g.plane_count);
		result("disable", 0, NULL);
		return -1;
	}

	bw = g.width / 2;
	bh = g.height / 2;
	bx = g.width / 4;
	by = g.height / 4;
	if (bw < 64 || bh < 64) {
		klog("disable: screen %dx%d too small", g.width, g.height);
		result("disable", 0, NULL);
		return -1;
	}

	b[0] = zbuf_alloc((uint32_t)g.width, (uint32_t)g.height,
			  DRM_FORMAT_RGB565, "disable/p0");
	for (i = 1; i < 4; i++) {
		char tag[32];

		snprintf(tag, sizeof(tag), "disable/p%d", i);
		b[i] = zbuf_alloc((uint32_t)bw, (uint32_t)bh,
				  DRM_FORMAT_XRGB8888, tag);
	}
	for (i = 0; i < 4; i++) {
		if (!b[i]) {
			result("disable", 0, NULL);
			bufs_free_all();
			return -EXIT_NOBUF;
		}
	}
	fill_rect(b[0], 0, 0, (int)b[0]->w, (int)b[0]->h, col[0]);
	for (i = 1; i < 4; i++)
		fill_rect(b[i], 0, 0, (int)b[i]->w, (int)b[i]->h, col[i]);

	/* --- step 1: all four layers ------------------------------------- */
	scene_reset(&s);
	(void)scene_put(&s, 0, b[0], 0, 0);
	for (i = 1; i < 4; i++)
		(void)scene_put(&s, i, b[i], bx + (i - 1) * (bw / 4),
				by + (i - 1) * (bh / 4));
	ret = zcommit("disable/4layers", &s, cmode());
	result("disable.4layers", ret == 0,
	       ret ? NULL : "dark blue background with three overlapping "
	       "squares: RED (plane1), GREEN (plane2) on top of red, BLUE "
	       "(plane3) on top of green");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	/* --- step 2: leave planes 2 and 3 out of the request ------------- */
	scene_reset(&s);
	s.disable_unlisted = 0;	/* the whole point of this step */
	(void)scene_put(&s, 0, b[0], 0, 0);
	(void)scene_put(&s, 1, b[1], bx, by);
	ret = zcommit("disable/omit23", &s, cmode());
	result("disable.omit", ret == 0,
	       ret ? NULL : "the GREEN and BLUE squares are STILL THERE. That "
	       "is the documented kernel behaviour: mtk_crtc_ddp_config() skips "
	       "planes whose pending.config is clear (mtk_crtc.c:516-517), so "
	       "the OVL SRC_CON bits and buffer addresses of planes 2 and 3 "
	       "were never rewritten. Only the RED square was updated");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	/* --- step 3: explicitly disable planes 2 and 3 ------------------- */
	s.disable_unlisted = 1;
	ret = zcommit("disable/explicit23", &s, cmode());
	result("disable.explicit", ret == 0,
	       ret ? NULL : "now GREEN and BLUE must be GONE, leaving only the "
	       "dark blue background and the RED square. This proves the "
	       "explicit FB_ID=0 + CRTC_ID=0 disable is required -- HWC2 gets "
	       "it right by calling program_plane(..., false, ...) for every "
	       "unused slot (z1hwc2.cpp:889-890)");
	if (ret)
		ok = 0;
	msleep(g_delay_ms);

	if (g_teardown)
		test_teardown("disable-end");
	return ok ? 0 : -1;
}

/* ------------------------------------------------------------------ regs --- */

/*
 * Offset and meaning of every OVL register this dump decodes, all taken
 * verbatim from the driver so the two can never drift apart silently:
 *   mtk_disp_ovl.c:23   DISP_REG_OVL_INTEN          0x0004
 *   mtk_disp_ovl.c:31   DISP_REG_OVL_INTSTA         0x0008
 *   mtk_disp_ovl.c:38   DISP_REG_OVL_EN             0x000c
 *   mtk_disp_ovl.c:39   DISP_REG_OVL_RST            0x0014
 *   mtk_disp_ovl.c:40   DISP_REG_OVL_ROI_SIZE       0x0020
 *   mtk_disp_ovl.c:41   DISP_REG_OVL_DATAPATH_CON   0x0024
 *   mtk_disp_ovl.c:45   DISP_REG_OVL_ROI_BGCLR      0x0028
 *   mtk_disp_ovl.c:46   DISP_REG_OVL_SRC_CON        0x002c
 *   mtk_disp_ovl.c:47   DISP_REG_OVL_CON(n)         0x0030 + 0x20*n
 *   mtk_disp_ovl.c:48   DISP_REG_OVL_SRC_SIZE(n)    0x0038 + 0x20*n
 *   mtk_disp_ovl.c:49   DISP_REG_OVL_OFFSET(n)      0x003c + 0x20*n
 *   mtk_disp_ovl.c:56   DISP_REG_OVL_ADDR_MT2701    0x0040  (+0x20*n)
 *   mtk_disp_ovl.c:52   DISP_REG_OVL_PITCH(n)       0x0044 + 0x20*n
 *   mtk_disp_ovl.c:54   DISP_REG_OVL_RDMA_CTRL(n)   0x00c0 + 0x20*n
 *
 * Note that DISP_REG_OVL_ADDR_MT2701 (0x0040) and DISP_REG_OVL_PITCH_MSB(n)
 * (0x0040 + 0x20*n) are the same offset: for MT2701/MT6572 the per-layer ADDR
 * lives in the layer block, there is no PITCH_MSB sub-buffer field.
 */
struct ovl_reg {
	uint32_t off;
	const char *name;
	int kind;		/* 0 hex, 1 size(h<<16|w), 2 SRC_CON bitmap */
};

static const struct ovl_reg g_ovl_regs[] = {
	{ 0x0004, "OVL_INTEN", 0 },
	{ 0x0008, "OVL_INTSTA", 0 },
	{ 0x000c, "OVL_EN", 0 },
	{ 0x0014, "OVL_RST", 0 },
	{ 0x0020, "OVL_ROI_SIZE", 1 },
	{ 0x0024, "OVL_DATAPATH_CON", 0 },
	{ 0x0028, "OVL_ROI_BGCLR", 0 },
	{ 0x002c, "OVL_SRC_CON", 2 },
};

static uint32_t rd32(const uint8_t *p)
{
	uint32_t v;

	memcpy(&v, p, 4);
	return v;
}

static void ovl_dump(const uint8_t *base, unsigned long size)
{
	unsigned i;

	klog("OVL global registers:");
	for (i = 0; i < ARRAY_SIZE(g_ovl_regs); i++) {
		uint32_t off = g_ovl_regs[i].off;
		uint32_t v;

		if ((unsigned long)off + 4 > size) {
			klog("  +0x%04x %-18s : outside the mapped window", off,
			     g_ovl_regs[i].name);
			continue;
		}
		v = rd32(base + off);
		if (g_ovl_regs[i].kind == 1)
			klog("  +0x%04x %-18s = 0x%08x  (roi %ux%u)", off,
			     g_ovl_regs[i].name, v, v & 0xffff, v >> 16);
		else if (g_ovl_regs[i].kind == 2)
			klog("  +0x%04x %-18s = 0x%08x  (layer0=%u layer1=%u "
			     "layer2=%u layer3=%u)", off, g_ovl_regs[i].name, v,
			     v & 1, (v >> 1) & 1, (v >> 2) & 1, (v >> 3) & 1);
		else
			klog("  +0x%04x %-18s = 0x%08x", off,
			     g_ovl_regs[i].name, v);
	}

	klog("OVL per-layer registers (layer n block = 0x0030 + 0x20*n):");
	for (i = 0; i < 4; i++) {
		uint32_t lb = 0x0030u + 0x20u * i;
		uint32_t con, src_size, offset, addr, pitch;
		uint32_t ro = 0x00c0u + 0x20u * i;

		if ((unsigned long)(lb + 0x18) > size) {
			klog("  layer %u: outside the mapped window", i);
			continue;
		}
		con = rd32(base + lb + 0x00);
		src_size = rd32(base + lb + 0x08);
		offset = rd32(base + lb + 0x0c);
		addr = rd32(base + lb + 0x10);
		pitch = rd32(base + lb + 0x14);

		klog("  layer %u: CON[0x%03x]=0x%08x SRC_SIZE[0x%03x]=0x%08x "
		     "(%ux%u)", i, lb + 0x00, con, lb + 0x08, src_size,
		     src_size & 0xffff, src_size >> 16);
		klog("           OFFSET[0x%03x]=0x%08x (x=%u y=%u) "
		     "ADDR[0x%03x]=0x%08x PITCH[0x%03x]=0x%08x",
		     lb + 0x0c, offset, offset & 0xffff, offset >> 16,
		     lb + 0x10, addr, lb + 0x14, pitch);
		if ((unsigned long)(ro + 4) <= size)
			klog("           RDMA_CTRL[0x%03x]=0x%08x", ro,
			     rd32(base + ro));
		klog("           decode: aen=%s const_blend=%s clr_alpha=0x%02x "
		     "clrfmt_bits=0x%x byte_swap=%s", (con & (1u << 8)) ? "y" : "n",
		     (con & (1u << 28)) ? "y" : "n", con & 0xff,
		     (con >> 12) & 0xf, (con & (1u << 24)) ? "y" : "n");
	}
}

static int cmd_regs(unsigned long base_addr, unsigned long size, int raw)
{
	int fd, ret = EXIT_OK;
	uint8_t *map;
	unsigned long off;
	unsigned long map_len;

	/*
	 * Where the OVL lives.  This is worth stating explicitly because the
	 * base is commonly mis-quoted as 0x14008000, which is RDMA0:
	 *   mt6572.dtsi:581-587   ovl:   ovl@14007000  { reg = <0x14007000 0x1000>; }
	 *   mt6572.dtsi:589-595   rdma0: rdma@14008000 { reg = <0x14008000 0x1000>; }
	 * The compiled arch/arm/boot/dts/mediatek/mt6572-z1.dtb contains both
	 * 0x14007000 (offset 14976) and 0x14008000 (offset 15124) as
	 * big-endian words, so the device tree is the authority.  `--base`
	 * overrides it.
	 */
	klog("regs: OVL base=0x%08lx size=0x%lx (device tree: mt6572.dtsi:581-587"
	     " ovl@14007000)", base_addr, size);
	klog("regs: WARNING this reads MMIO. If the DIS power domain or the OVL "
	     "clock is currently gated the read can hang the AXI bus and reset "
	     "the SoC -- run it while the display is actually scanning out.");

	if ((base_addr & 0xfffu) || size == 0) {
		klog("regs: base must be 4 KiB aligned and size non-zero "
		     "(got base=0x%lx size=0x%lx)", base_addr, size);
		return EXIT_USAGE;
	}
	/*
	 * mmap() on /dev/mem only requires the *offset* to be page aligned; the
	 * length is rounded up by the kernel, so a 0x300-byte register window is
	 * fine.  We round the mapped length ourselves anyway so munmap() gets
	 * exactly the length we passed to mmap().
	 */
	map_len = (size + 0xfffu) & ~0xffful;

	fd = open("/dev/mem", O_RDONLY | O_SYNC | O_CLOEXEC);
	if (fd < 0) {
		klog("regs: cannot open /dev/mem: %s", strerror(errno));
		klog("regs: this needs uid 0 and CONFIG_DEVMEM=y.");
		klog("regs: the Z1 kernel has CONFIG_DEVMEM=y "
		     "(fork_linux/.config:2248) and CONFIG_STRICT_DEVMEM=y "
		     "(.config:7364). CONFIG_STRICT_DEVMEM only blocks System "
		     "RAM: devmem_is_allowed() (lib/devmem_is_allowed.c) "
		     "returns 0 for iomem_is_exclusive() ranges and for pages "
		     "where page_is_ram() is true, and 0x14007000 is neither, so "
		     "STRICT_DEVMEM does NOT have to be turned off for this "
		     "window. If the open fails with EACCES/EPERM, run as root; "
		     "if /dev/mem does not exist, CONFIG_DEVMEM was off.");
		return EXIT_NODEVMEM;
	}

	map = mmap(NULL, map_len, PROT_READ, MAP_SHARED, fd, (off_t)base_addr);
	if (map == MAP_FAILED) {
		klog("regs: mmap(0x%lx, 0x%lx) on /dev/mem failed: %s",
		     base_addr, map_len, strerror(errno));
		klog("regs: the range was denied. drivers/char/mem.c "
		     "mmap_mem_prepare() calls range_is_allowed() -> "
		     "devmem_is_allowed(); a denial here means the window is "
		     "reserved IORESOURCE_EXCLUSIVE or page_is_ram() is true for "
		     "it. Sanity check with `devmem 0x%lx` and see the README.");
		close(fd);
		return EXIT_NODEVMEM;
	}

	ovl_dump(map, size);

	if (raw) {
		klog("raw dump of 0x%08lx..0x%08lx (%lu bytes), 4 words per line:",
		     base_addr, base_addr + size - 1, size);
		for (off = 0; off + 16 <= size; off += 16)
			klog("  +0x%04lx: %08x %08x %08x %08x", off,
			     rd32(map + off), rd32(map + off + 4),
			     rd32(map + off + 8), rd32(map + off + 12));
	}

	munmap(map, map_len);
	close(fd);
	klog("regs: done (read-only dump)");
	return ret;
}

/* ---------------------------------------------------------------- probe --- */

static int pick_auto_node(char *out, size_t outlen)
{
	int i;

	for (i = 0; i < 10; i++) {
		char p[32];
		int t, ok;

		snprintf(p, sizeof(p), "/dev/dri/card%d", i);
		t = open(p, O_RDWR | O_CLOEXEC);
		if (t < 0)
			continue;
		memset(&g, 0, sizeof(g));
		g.fd = t;
		ok = 0;
		if (read_version(&g) == 0 && strcmp(g.driver, "lima") != 0 &&
		    set_caps(&g) == 0)
			ok = 1;
		klog("  %s: driver=%s -> %s", p,
		     g.driver[0] ? g.driver : "?",
		     ok ? "KMS node" : "not usable");
		close(t);
		if (ok) {
			snprintf(out, outlen, "%s", p);
			return 0;
		}
	}
	return -1;
}

static int cmd_probe(const char *device, int wait_sec)
{
	int fd;
	char auto_node[32];

	klog("==== z1kmstest probe ====");

	if (!strcmp(device, "auto")) {
		if (pick_auto_node(auto_node, sizeof(auto_node))) {
			klog("auto: no non-lima atomic DRM node found");
			klog("RESULT probe: FAIL (node)");
			return EXIT_NODEV;
		}
		device = auto_node;
		klog("auto: picked %s", device);
	}

	if (open_waited(device, wait_sec, &fd)) {
		klog("RESULT probe: FAIL (node %s)", device);
		return EXIT_NODEV;
	}

	memset(&g, 0, sizeof(g));
	g.fd = fd;
	g.path = device;
	klog("node %s fd=%d", device, fd);
	if (read_version(&g)) {
		close(fd);
		klog("RESULT probe: FAIL (version)");
		return EXIT_NODEV;
	}
	klog("driver=%s date=%s desc=%s", g.driver, g.date, g.desc);

	if (set_caps(&g)) {
		close(fd);
		klog("RESULT probe: FAIL (no atomic modeset on %s)", device);
		return EXIT_NOTATOMIC;
	}
	if (find_resources(&g)) {
		free_planes(&g);
		close(fd);
		klog("RESULT probe: FAIL (incomplete KMS resources)");
		return EXIT_NOKMS;
	}
	klog("RESULT probe: PASS -- %s is an atomic KMS node: %d planes (zpos %d"
	     "..%d, immutable), crtc %u, connector %u, %dx%d", device,
	     g.plane_count, g.planes[0].zpos,
	     g.planes[g.plane_count - 1].zpos, g.crtc_id, g.conn_id, g.width,
	     g.height);
	klog("NEXT: `z1kmstest run --mode basic` performs the first modeset. To "
	     "keep every line in dmesg run: echo on > "
	     "/sys/module/printk/parameters/devkmsg_log");

	free_planes(&g);
	close(fd);
	return EXIT_OK;
}

/* ------------------------------------------------------------------ run --- */

typedef int (*ztest_fn)(void);

static const struct {
	const char *name;
	ztest_fn fn;
} g_tests[] = {
	{ "basic", test_basic },
	{ "layers", test_layers },
	{ "formats", test_formats },
	{ "alpha", test_alpha },
	{ "offscreen", test_offscreen },
	{ "disable", test_disable },
};

static int cmd_run(const char *device, const char *mode, int wait_sec,
		   int hold, int hold_seconds)
{
	int fd, ret;
	unsigned i;
	int failures = 0, alloc_failed = 0, finish;
	int matched[ARRAY_SIZE(g_tests)];
	char auto_node[32];

	klog("==== z1kmstest run --mode %s%s ====", mode,
	     g_test_only ? " --test-only (validation only, no commits)" : "");

	/* validate --mode before touching the device */
	memset(matched, 0, sizeof(matched));
	if (!strcmp(mode, "all")) {
		for (i = 0; i < ARRAY_SIZE(g_tests); i++)
			matched[i] = 1;
	} else {
		int any = 0;

		for (i = 0; i < ARRAY_SIZE(g_tests); i++)
			if (!strcmp(mode, g_tests[i].name)) {
				matched[i] = 1;
				any = 1;
			}
		if (!any) {
			klog("run: unknown --mode '%s' (valid: basic layers "
			     "formats alpha offscreen disable all)", mode);
			return EXIT_USAGE;
		}
	}

	if (!strcmp(device, "auto")) {
		if (pick_auto_node(auto_node, sizeof(auto_node))) {
			klog("auto: no non-lima atomic DRM node found");
			return EXIT_NODEV;
		}
		device = auto_node;
		klog("auto: picked %s", device);
	}

	if (open_waited(device, wait_sec, &fd)) {
		klog("RESULT run: FAIL (node %s)", device);
		return EXIT_NODEV;
	}
	memset(&g, 0, sizeof(g));
	g.fd = fd;
	g.path = device;
	if (read_version(&g)) {
		close(fd);
		return EXIT_NODEV;
	}
	klog("node %s fd=%d driver=%s", device, fd, g.driver);
	if (set_caps(&g)) {
		close(fd);
		klog("RESULT run: FAIL (%s is not an atomic KMS node -- lima? "
		     "use --device /dev/dri/card1)", device);
		return EXIT_NOTATOMIC;
	}
	if (master_acquire(&g)) {
		close(fd);
		klog("RESULT run: FAIL (no DRM master on %s -- see the diagnosis "
		     "above)", device);
		return EXIT_NOMASTER;
	}
	if (find_resources(&g)) {
		free_planes(&g);
		close(fd);
		klog("RESULT run: FAIL (incomplete KMS resources)");
		return EXIT_NOKMS;
	}
	if (g.plane_count < 4)
		klog("WARNING: only %d planes; the multi-plane tests want 4",
		     g.plane_count);

	{
		int selected = 0, seen = 0;

		for (i = 0; i < ARRAY_SIZE(g_tests); i++)
			if (matched[i])
				selected++;

		for (i = 0; i < ARRAY_SIZE(g_tests); i++) {
			if (!matched[i])
				continue;
			seen++;
			/* --hold keeps only the very last frame alive */
			g_teardown = !(hold && seen == selected);
			klog("======== sub-test %s (%d/%d) ========",
			     g_tests[i].name, seen, selected);
			ret = g_tests[i].fn();
			if (ret) {
				failures++;
				if (ret == -EXIT_NOBUF)
					alloc_failed = 1;
			}
			if (g_stop) {
				klog("interrupted, stopping after %s",
				     g_tests[i].name);
				g_teardown = 1;
				break;
			}
		}
	}

	if (g_teardown) {
		zscene s;

		if (g.plane_count && g.modeset_done && !g_test_only) {
			scene_reset(&s);
			(void)zcommit("final-teardown", &s, COMMIT_SYNC);
		}
		bufs_free_all();
		if (g.mode_blob) {
			errno = 0;
			if (norm_ret(drmModeDestroyPropertyBlob(g.fd,
								g.mode_blob)))
				klog("destroy MODE blob failed: %s",
				     strerror(errno));
			g.mode_blob = 0;
		}
	} else {
		klog("--hold: planes are NOT disabled and buffers are NOT freed");
		klog("--hold: the dumb buffers live only as long as this process, "
		     "so keep z1kmstest running while you look at the panel");
	}

	klog("==== summary: %d passed, %d failed (%d sub-test(s) returned an "
	     "error) ====", g_pass, g_fail, failures);
	if (g_fail == 0 && g_pass > 0)
		klog("RESULT run: PASS");
	else
		klog("RESULT run: FAIL (%d check(s) rejected, see above)", g_fail);

	free_planes(&g);

	if (hold) {
		if (hold_seconds > 0) {
			klog("holding the last frame for %d s", hold_seconds);
			msleep(hold_seconds * 1000);
		} else {
			klog("holding the last frame; press Ctrl-C to exit");
			while (!g_stop)
				usleep(200 * 1000);
		}
		klog("--hold over, closing the DRM node");
	}

	errno = 0;
	if (norm_ret(drmDropMaster(fd)))
		klog("drmDropMaster failed: %s", strerror(errno));
	close(fd);

	/*
	 * Pick the most specific exit code:
	 *   all green                     -> 0
	 *   a buffer could not be made    -> 7  (EXIT_NOBUF)
	 *   nothing ever reached the panel -> 8 (EXIT_COMMIT, first modeset dead)
	 *   anything else                 -> 1  (EXIT_FAIL)
	 */
	if (g_fail == 0 && g_pass > 0)
		finish = EXIT_OK;
	else if (alloc_failed)
		finish = EXIT_NOBUF;
	else if (!g_test_only && !g.modeset_done)
		finish = EXIT_COMMIT;
	else
		finish = EXIT_FAIL;
	return finish;
}

/* ----------------------------------------------------------------- usage --- */

static void usage(void)
{
	kout("z1kmstest -- mediatek-drm atomic/KMS test tool for the Z1 (MT6572)");
	kout("");
	kout("usage:");
	kout("  z1kmstest probe  [--device /dev/dri/cardN] [--wait SEC]");
	kout("  z1kmstest run    [--device /dev/dri/cardN] [--wait SEC]");
	kout("                   [--mode basic|layers|formats|alpha|offscreen|");
	kout("                           disable|all] [--delay MS] [--event]");
	kout("                   [--hold] [--hold-seconds N]");
	kout("  z1kmstest regs   [--base ADDR] [--size BYTES] [--raw]");
	kout("  z1kmstest help");
	kout("");
	kout("options:");
	kout("  --device P        DRM node. Default %s (card0 is lima,",
	     DEFAULT_DEVICE);
	kout("                    render only, no atomic). --device auto scans");
	kout("                    card0..card9 and picks the first non-lima node");
	kout("                    that accepts DRM_CLIENT_CAP_ATOMIC.");
	kout("  --wait N          poll up to N s for the node to appear "
	     "(default %d).", DEFAULT_WAIT_SEC);
	kout("                    mediatek-drm only registers ~33 s after boot.");
	kout("  --mode M          which sub-test to run (default: all).");
	kout("  --delay MS        how long to leave each frame on the panel "
	     "(default %d).", DEFAULT_DELAY_MS);
	kout("  --event           use DRM_MODE_ATOMIC_NONBLOCK|PAGE_FLIP_EVENT and");
	kout("                    wait for the page-flip event instead of using a");
	kout("                    blocking commit. Tells you whether the CRTC");
	kout("                    really reaches vblank.");
	kout("  --test-only       run every sub-test through");
	kout("                    DRM_MODE_ATOMIC_TEST_ONLY only: the kernel runs");
	kout("                    all ->atomic_check() callbacks but nothing is");
	kout("                    committed and the panel never changes. This is");
	kout("                    the safe way to collect the -EINVAL/-ERANGE/");
	kout("                    -ENOSPC verdicts for every configuration.");
	kout("  --hold            do not disable the planes / free the buffers at");
	kout("                    the end; keep the last frame until Ctrl-C.");
	kout("  --hold-seconds N  like --hold but exit after N seconds.");
	kout("  --base ADDR       regs: OVL register base (default 0x%08x, from "
	     "the DT).", OVL_BASE_DEFAULT);
	kout("  --size BYTES      regs: how much of the window to decode/dump");
	kout("                    (default 0x300 = the OVL layer block through");
	kout("                    OVL_RDMA_CTRL; max 0x1000, which is the size");
	kout("                    of the DT region <0x14007000 0x1000>).");
	kout("  --raw             regs: also hex-dump the mapped window.");
	kout("");
	kout("exit codes:");
	kout("  0 ok                          1 a sub-test was rejected");
	kout("  2 regs: /dev/mem unusable    3 DRM node missing / unusable");
	kout("  4 no atomic modeset          5 KMS resources incomplete");
	kout("  6 bad arguments              7 buffer alloc / AddFB2 failed");
	kout("  8 the first (modeset) commit failed");
	kout("  9 cannot get DRM master (run as root / stop SurfaceFlinger)");
	kout("");
	kout("error decoding:");
	kout("  -ERANGE  scaling: SRC size != CRTC size");
	kout("  -EINVAL  unsupported format/plane, FB < 64x64, bad object id, or");
	kout("           a modeset attempted without ALLOW_MODESET");
	kout("  -ENOSPC  source rect outside the framebuffer (NOT plane count)");
	kout("");
	kout("Every line is also written to /dev/kmsg with the \"z1kms: \" prefix.");
}

/* ------------------------------------------------------------------ main --- */

static int parse_ul(const char *s, unsigned long *out)
{
	char *end = NULL;
	unsigned long v;

	errno = 0;
	v = strtoul(s, &end, 0);
	if (errno || !end || *end || end == s)
		return -1;
	*out = v;
	return 0;
}

int main(int argc, char **argv)
{
	const char *cmd;
	const char *device = DEFAULT_DEVICE;
	const char *mode = "all";
	int wait_sec = DEFAULT_WAIT_SEC;
	int hold = 0, hold_seconds = 0;
	int raw = 0;
	unsigned long base_addr = OVL_BASE_DEFAULT;
	unsigned long reg_size = 0x300;
	int i, ret;

	{
		struct sigaction sa;

		memset(&sa, 0, sizeof(sa));
		sa.sa_handler = on_signal;
		sigaction(SIGINT, &sa, NULL);
		sigaction(SIGTERM, &sa, NULL);
		sigaction(SIGHUP, &sa, NULL);
	}

	/* /dev/kmsg is optional; without it we still print to stdout */
	g_kmsg_fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);

	if (argc < 2) {
		usage();
		if (g_kmsg_fd >= 0)
			close(g_kmsg_fd);
		return EXIT_USAGE;
	}
	cmd = argv[1];
	if (!strcmp(cmd, "help") || !strcmp(cmd, "-h") ||
	    !strcmp(cmd, "--help")) {
		usage();
		if (g_kmsg_fd >= 0)
			close(g_kmsg_fd);
		return EXIT_OK;
	}

	for (i = 2; i < argc; i++) {
		const char *a = argv[i];
		const char *eq;
		char name[32];
		const char *val = NULL;

		if (a[0] != '-' || a[1] != '-') {
			klog("bad argument '%s' (options start with --)", a);
			usage();
			if (g_kmsg_fd >= 0)
				close(g_kmsg_fd);
			return EXIT_USAGE;
		}
		eq = strchr(a, '=');
		if (eq) {
			size_t n = (size_t)(eq - a);

			if (n >= sizeof(name))
				n = sizeof(name) - 1;
			memcpy(name, a, n);
			name[n] = '\0';
			val = eq + 1;
		} else {
			snprintf(name, sizeof(name), "%s", a);
		}

#define NEED_VAL()                                                          \
		do {                                                        \
			if (!val) {                                         \
				if (i + 1 >= argc) {                        \
					klog("%s needs a value", name);     \
					return EXIT_USAGE;                  \
				}                                           \
				val = argv[++i];                            \
			}                                                   \
		} while (0)

		if (!strcmp(name, "--device")) {
			NEED_VAL();
			device = val;
		} else if (!strcmp(name, "--mode")) {
			NEED_VAL();
			mode = val;
		} else if (!strcmp(name, "--wait")) {
			unsigned long v;

			NEED_VAL();
			if (parse_ul(val, &v) || v > 3600) {
				klog("--wait needs 0..3600 seconds");
				return EXIT_USAGE;
			}
			wait_sec = (int)v;
		} else if (!strcmp(name, "--delay")) {
			unsigned long v;

			NEED_VAL();
			if (parse_ul(val, &v) || v > 600000) {
				klog("--delay needs 0..600000 ms");
				return EXIT_USAGE;
			}
			g_delay_ms = (int)v;
		} else if (!strcmp(name, "--event")) {
			g_event_mode = 1;
		} else if (!strcmp(name, "--test-only")) {
			g_test_only = 1;
		} else if (!strcmp(name, "--hold")) {
			hold = 1;
		} else if (!strcmp(name, "--hold-seconds")) {
			unsigned long v;

			NEED_VAL();
			if (parse_ul(val, &v) || v > 86400) {
				klog("--hold-seconds needs 0..86400");
				return EXIT_USAGE;
			}
			hold = 1;
			hold_seconds = (int)v;
		} else if (!strcmp(name, "--base")) {
			NEED_VAL();
			if (parse_ul(val, &base_addr)) {
				klog("--base needs a number (0x prefix is fine)");
				return EXIT_USAGE;
			}
		} else if (!strcmp(name, "--size")) {
			NEED_VAL();
			if (parse_ul(val, &reg_size) || reg_size == 0 ||
			    reg_size > 0x1000) {
				klog("--size must be 1..0x1000: the OVL DT node is "
				     "reg = <0x14007000 0x1000> "
				     "(mt6572.dtsi:583), reading past it would "
				     "touch whatever block follows");
				return EXIT_USAGE;
			}
		} else if (!strcmp(name, "--raw")) {
			raw = 1;
		} else {
			klog("unknown option '%s'", name);
			usage();
			if (g_kmsg_fd >= 0)
				close(g_kmsg_fd);
			return EXIT_USAGE;
		}
#undef NEED_VAL
	}

	if (!strcmp(cmd, "probe"))
		ret = cmd_probe(device, wait_sec);
	else if (!strcmp(cmd, "run"))
		ret = cmd_run(device, mode, wait_sec, hold, hold_seconds);
	else if (!strcmp(cmd, "regs"))
		ret = cmd_regs(base_addr, reg_size, raw);
	else {
		klog("unknown command '%s'", cmd);
		usage();
		ret = EXIT_USAGE;
	}

	if (g_kmsg_fd >= 0)
		close(g_kmsg_fd);
	return ret;
}
