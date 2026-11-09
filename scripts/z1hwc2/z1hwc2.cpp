/*
 * z1hwc2 — HWC2 hardware composer for the Z1 (MT6572 + mediatek-drm).
 *
 * Rewrite 2026-11-10.  The previous revision could never be loaded: it did not
 * implement HWC2_FUNCTION_CREATE_VIRTUAL_DISPLAY, and the HIDL passthrough
 * implementation (vendor/lib/hw/android.hardware.graphics.composer@2.1-impl.so)
 * rejects the module at ComposerHal::init() when any of the functions it
 * dispatches is NULL ("failed to get hwcomposer2 function 3").  This revision
 * implements every HWC2 2.1 function descriptor.
 *
 * Data path
 * ---------
 *   SurfaceFlinger --HIDL composer 2.1--> this HAL --DRM atomic--> MT6572 OVL
 *   plane 0 : client target (GPU-composited bottom layer)
 *   plane 1..N : HWC2_COMPOSITION_DEVICE layers, bottom-to-top, zero copy
 *
 * Hard kernel constraints (verified against fork_linux/drivers/gpu/drm/mediatek):
 *  - zpos is immutable and equals the plane index; compositing order == plane order.
 *  - no scaling: src(crop) size must equal dst(display frame) size.
 *  - per-plane position IS supported (OVL_Lx_OFFSET gets (y << 16) | x).
 *  - a plane that is not part of a commit keeps scanning its old buffer, so
 *    unused planes must be disabled explicitly (FB_ID = 0, CRTC_ID = 0).
 *  - scanout buffers must be physically contiguous dma-bufs: the GEM import
 *    path rejects non-contiguous sg_tables (mtk_gem_prime_import_sg_table()).
 *    Buffers that cannot be imported degrade to CLIENT composition; the client
 *    target itself can additionally be copied into a local dumb buffer, which
 *    keeps the screen alive even with a non-contiguous gralloc.
 *
 * Composition policy
 * ------------------
 * The client target is always the bottom-most plane, therefore CLIENT layers
 * must form the bottom of the z order and DEVICE layers a contiguous top
 * suffix.  validateDisplay() walks from the top of the stack downwards and
 * keeps layers DEVICE while (a) they are deviceable, (b) no CLIENT layer is
 * above them and (c) planes remain; everything below is demoted to CLIENT.
 * Per the HWC2 contract the device may only *demote* DEVICE/CURSOR layers, and
 * must never touch layers the client already marked CLIENT.
 *
 * Layers that arrive with HWC2_COMPOSITION_INVALID (first frame) are assigned
 * a type and reported through getChangedCompositionTypes(): Android 9
 * SurfaceFlinger defaults every layer to INVALID and relies on the HAL to make
 * the first decision (Layer.h: HWCInfo::compositionType(Composition::Invalid)).
 *
 * Fences
 * ------
 * Phase 1 (this build): release/present fences are -1, exactly like the
 * goldfish HWC1 that currently drives this panel, so no deadlock is possible.
 * Real sync fences need libsync's sw_sync timeline and are a later step; the
 * hooks are present but disabled.
 *
 * Diagnostics: klog() writes to /dev/kmsg, which is the only log channel that
 * reliably reaches the board's UART.  Verbosity follows the system property
 * debug.z1.hwc.verbose (0=errors, 1=summary, 2=per layer, 3=per plane state).
 */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <poll.h>
#include <pthread.h>
#include <time.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <algorithm>
#include <map>
#include <vector>

#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
/*
 * The build shadows <log/log.h> with a no-op stub (scripts/hal_stub/log/log.h)
 * that redefines the ANDROID_LOG_* enum, so including <android/log.h> here
 * clashes.  Declare liblog's entry point directly: it is exported by
 * /system/lib/liblog.so, which is already in NEEDED.
 */
extern "C" int __android_log_write(int prio, const char *tag, const char *text);

/* android/log.h priorities; spelled out because this tree builds against a
 * reduced <log/log.h> shim. */
#define Z1_LOG_DEBUG 3
#define Z1_LOG_INFO 4
#define Z1_LOG_WARN 5
#define Z1_LOG_ERROR 6
#include <sys/types.h>
#include <sys/system_properties.h>

#include <hardware/hardware.h>
#include <hardware/hwcomposer2.h>
#include <hardware/hwcomposer_defs.h>

#include <cutils/native_handle.h>

#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

#include <linux/dma-buf.h>

#include "z1_policy.h"

#define LOG_TAG "z1hwc2"
#include <log/log.h>

#define Z1_HWC_VERSION "2026-11-10-r5"

/* ------------------------------------------------------------ constants --- */

/* android_pixel_format_t — the stub header tree does not carry
 * <system/graphics.h>, so name the values we care about locally. */
#ifndef HAL_PIXEL_FORMAT_RGBA_8888
#define HAL_PIXEL_FORMAT_RGBA_8888 1
#define HAL_PIXEL_FORMAT_RGBX_8888 2
#define HAL_PIXEL_FORMAT_RGB_888   3
#define HAL_PIXEL_FORMAT_RGB_565   4
#define HAL_PIXEL_FORMAT_BGRA_8888 5
#endif

/* hwc2_blend_mode_t (hwcomposer2.h already defines these). */
#ifndef HWC2_BLEND_MODE_INVALID
#define HWC2_BLEND_MODE_INVALID      0
#define HWC2_BLEND_MODE_NONE         1
#define HWC2_BLEND_MODE_PREMULTIPLIED 2
#define HWC2_BLEND_MODE_COVERAGE     3
#endif

/* drm_blend.h mode values (stable kernel ABI). */
#define Z1_DRM_BLEND_PREMULTI    0
#define Z1_DRM_BLEND_COVERAGE    1
#define Z1_DRM_BLEND_PIXEL_NONE  2

#define Z1_HANDLE_MAGIC 0x3141592
#define Z1_MAX_PLANES   8
#define Z1_MAX_LAYERS   32

/* gralloc private_handle_t — must stay byte-identical to
 * z1a9_gpt/graphics/gralloc/gralloc_priv.h (64 bytes, numFds=1, numInts=12). */
struct z1_handle {
	int version;
	int numFds;
	int numInts;
	int fd;
	int magic;
	int flags;
	int size;
	int offset;
	uint64_t base __attribute__((aligned(8)));
	int pid;
	int width;
	int height;
	int stride;	/* pixels */
	int format;	/* HAL_PIXEL_FORMAT_* */
	int reserved;
};

/*
 * ABI self-checks.  A wrong struct layout would be invisible at compile time
 * but fatal at run time: the composer HIDL adapter calls getFunction() at
 * byte offset 68 of our hwc2_device_t and close() at 60 of hw_device_t (both
 * confirmed in android.hardware.graphics.composer@2.1-impl.so), and the HAL
 * parses gralloc's private_handle_t by hand.
 */
static_assert(sizeof(hw_device_t) == 64, "hw_device_t must be 64 bytes");
static_assert(offsetof(hw_device_t, close) == 60, "close() must be at 60");
static_assert(offsetof(hwc2_device_t, getCapabilities) == 64,
	      "getCapabilities() must be at 64");
static_assert(offsetof(hwc2_device_t, getFunction) == 68,
	      "getFunction() must be at 68");
static_assert(sizeof(struct z1_handle) == 64,
	      "gralloc private_handle_t must be 64 bytes");
static_assert(offsetof(struct z1_handle, fd) == 12, "handle.fd must be at 12");
static_assert(offsetof(struct z1_handle, width) == 44,
	      "handle.width must be at 44");
static_assert(offsetof(struct z1_handle, height) == 48,
	      "handle.height must be at 48");
static_assert(offsetof(struct z1_handle, stride) == 52,
	      "handle.stride must be at 52");
static_assert(offsetof(struct z1_handle, format) == 56,
	      "handle.format must be at 56");

/* --------------------------------------------------------------- logging --- */

static int g_verbose = 1;

/*
 * /dev/kmsg applies the printk ratelimit to userspace writes as well
 * (~10 messages per 5 s), and a dropped message is gone for good -- which is
 * how a chatty HAL ends up blind in the field.  So the HAL keeps its own,
 * stricter budget (5 lines/s) and reports how many lines it had to drop.
 */
static void klog_raw(int fd, const char *buf)
{
	size_t n = strlen(buf);

	if (n == 0)
		return;
	if (n > 900)
		n = 900;
	if (write(fd, buf, n) < 0) {
		/* kmsg full or unavailable: stay silent */
	}
}

static void klogf(int level, const char *fmt, ...)
{
	static int fd = -2;
	static long last_sec;
	static int used;
	static int dropped;
	struct timespec ts;
	char buf[512];
	va_list ap;
	int n;

	if (level > g_verbose)
		return;
	/*
	 * The composer service runs as `system` and /dev/kmsg is root-only, so
	 * the kernel log is only a bonus (it works when the HAL is run by hand
	 * as root).  logd is the channel that always works, and this image's
	 * /z1_klog_relay.sh mirrors logcat to the serial console, so `logcat -s
	 * z1hwc2` and the serial capture both see every line.
	 */
	if (fd == -2)
		fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
	clock_gettime(CLOCK_MONOTONIC, &ts);
	if (ts.tv_sec != last_sec) {
		last_sec = ts.tv_sec;
		used = 0;
		if (dropped) {
			char note[96];

			snprintf(note, sizeof(note),
				 "z1hwc2: (%d earlier line(s) dropped by klog budget)",
				 dropped);
			__android_log_write(Z1_LOG_WARN, "z1hwc2", note);
			dropped = 0;
		}
	}
	if (used >= 5) {
		dropped++;
		return;
	}
	used++;
	va_start(ap, fmt);
	n = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	if (n > 0) {
		if (n > (int)sizeof(buf) - 1)
			n = (int)sizeof(buf) - 1;
		buf[n] = '\0';
		if (buf[n - 1] == '\n')
			buf[n - 1] = '\0';
		__android_log_write(level == 0 ? Z1_LOG_ERROR
				   : level == 1 ? Z1_LOG_INFO
						: Z1_LOG_DEBUG,
				    "z1hwc2", buf);
		if (fd >= 0) {
			buf[n - 1] = '\n';
			buf[n] = '\0';
			klog_raw(fd, buf);
		}
	}
}

static int prop_int(const char *name, int def)
{
	char buf[PROP_VALUE_MAX];

	memset(buf, 0, sizeof(buf));
	if (__system_property_get(name, buf) <= 0)
		return def;
	return atoi(buf);
}

/* --------------------------------------------------------------- formats --- */

static int hal_bpp(int fmt)
{
	switch (fmt) {
	case HAL_PIXEL_FORMAT_RGBA_8888:
	case HAL_PIXEL_FORMAT_RGBX_8888:
	case HAL_PIXEL_FORMAT_BGRA_8888:
		return 4;
	case HAL_PIXEL_FORMAT_RGB_888:
		return 3;
	case HAL_PIXEL_FORMAT_RGB_565:
		return 2;
	default:
		return 0;
	}
}

/*
 * HAL -> DRM fourcc.
 *
 * The HAL formats describe the byte order in memory, e.g. RGBA_8888 stores
 * R,G,B,A at increasing addresses, which little-endian is a 32-bit word
 * 0xAABBGGRR = DRM_FORMAT_ABGR8888.
 *
 * RGB_565 is deliberately DRM_FORMAT_RGB565 (red in the high bits), NOT
 * BGR565: the kernel's mt8173_formats[] (used by mt6572_ovl_driver_data)
 * contains RGB565 and would reject BGR565 with -EINVAL.  The old revision
 * mapped it to BGR565, which could never have displayed correctly.
 */
static uint32_t hal_to_drm(int fmt)
{
	switch (fmt) {
	case HAL_PIXEL_FORMAT_RGBA_8888:
		return DRM_FORMAT_ABGR8888;
	case HAL_PIXEL_FORMAT_RGBX_8888:
		return DRM_FORMAT_XBGR8888;
	case HAL_PIXEL_FORMAT_BGRA_8888:
		return DRM_FORMAT_ARGB8888;
	case HAL_PIXEL_FORMAT_RGB_888:
		return DRM_FORMAT_BGR888;
	case HAL_PIXEL_FORMAT_RGB_565:
		return DRM_FORMAT_RGB565;
	default:
		return 0;
	}
}

static const char *hal_fmt_name(int fmt)
{
	switch (fmt) {
	case HAL_PIXEL_FORMAT_RGBA_8888: return "RGBA_8888";
	case HAL_PIXEL_FORMAT_RGBX_8888: return "RGBX_8888";
	case HAL_PIXEL_FORMAT_BGRA_8888: return "BGRA_8888";
	case HAL_PIXEL_FORMAT_RGB_888:   return "RGB_888";
	case HAL_PIXEL_FORMAT_RGB_565:   return "RGB_565";
	default:                         return "other";
	}
}

/* ----------------------------------------------------------- drm objects --- */

struct z1_plane {
	uint32_t id;
	uint32_t zpos_val;
	bool has_zpos;
	bool has_alpha;
	bool has_blend;
	/* property ids, 0 = missing */
	uint32_t p_fb, p_crtc, p_cx, p_cy, p_cw, p_ch;
	uint32_t p_sx, p_sy, p_sw, p_sh;
	uint32_t p_zpos, p_alpha, p_blend, p_rotation;
};

struct z1_fb {
	buffer_handle_t handle;
	int src_fd;
	uint32_t w, h, pitch, fourcc;
	uint32_t gem;		/* prime-imported handle, 0 if none */
	uint32_t fb_id;
};

struct z1_dumb {
	uint32_t handle;
	uint32_t fb_id;
	uint32_t pitch;
	uint32_t w, h;
	uint32_t fourcc;
	void *map;
	size_t size;
};

struct z1_layer {
	hwc2_layer_t id;
	buffer_handle_t handle;
	int acquire_fence;		/* dup()ed, -1 = none */
	hwc2_composition_t composition;
	hwc_rect_t frame;
	hwc_frect_t crop;
	float alpha;
	int32_t blend;
	int32_t dataspace;
	uint32_t z;
	uint32_t transform;
	int visible_rects;
	bool have_crop;
	int release_fence;	/* sw_sync fence, ownership transfers to SF */
	z1_fb fb;
};

struct z1_hwc2 : public hwc2_device_t {
	/* callbacks */
	hwc2_callback_data_t cb_data;
	hwc2_function_pointer_t cb_hotplug;
	hwc2_function_pointer_t cb_vsync;
	hwc2_function_pointer_t cb_refresh;

	/* layers */
	std::map<hwc2_layer_t, z1_layer> layers;
	hwc2_layer_t next_layer_id;
	std::vector<std::pair<hwc2_layer_t, int32_t> > changed;

	/* client target */
	buffer_handle_t client_target;
	int client_acquire_fence;
	int32_t client_dataspace;
	z1_fb client_fb;
	int client_copy_slot;
	z1_dumb shadow[2];

	/* drm */
	int drm_fd;
	const char *drm_path;
	uint32_t crtc_id;
	uint32_t connector_id;
	uint32_t connector_type;
	drmModeModeInfo mode;
	uint32_t width;
	uint32_t height;
	z1_plane planes[Z1_MAX_PLANES];
	int plane_count;
	uint32_t connector_crtc_prop;
	uint32_t crtc_mode_prop;
	uint32_t crtc_active_prop;
	uint32_t mode_blob;
	bool modeset_done;
	bool powered_on;
	bool validated;
	bool client_expected;	/* SurfaceFlinger will supply a client target */

	/* deferred framebuffer release: an fb that was replaced this frame may
	 * still be referenced by the state the display is scanning, so it is
	 * only destroyed on the next present. */
	std::vector<std::pair<uint32_t, uint32_t> > pending_release; /* fb, gem */
	int pending_frame;

	/* software sync fences (optional) */
	bool fences_on;
	int timeline_fd;
	uint32_t timeline_val;	/* last signalled timeline value */
	bool fences_pending;	/* fences at timeline_val+1 are outstanding */
	uint64_t last_present_ms;
	pthread_mutex_t fence_lock;
	volatile int wd_stop;
	pthread_t wd_thread;
	bool wd_started;

	/* stats */
	uint64_t frames;
	uint64_t commit_fail;
	uint32_t last_device_layers;
	uint32_t last_client_layers;
	bool first_commit_logged;
	int mode_force_client;		/* debug.z1.hwc.mode == 0 */
};

/* ------------------------------------------------ software sync fences --- */

/*
 * Release/present fences need a fence object that we can signal later, which
 * is exactly what the kernel's sw_sync driver provides (CONFIG_SW_SYNC,
 * exposed as <debugfs>/sync/sw_sync).  Everything here is optional: when the
 * property debug.z1.hwc.fences is 0 (default) or the node is missing, the HAL
 * keeps returning -1 like the goldfish HWC1 does.
 */
struct z1_sw_sync_data {
	uint32_t value;
	char name[32];
	int32_t fence;
};
#define Z1_SW_SYNC_IOC_MAGIC 'W'
#define Z1_SW_SYNC_IOC_CREATE_FENCE \
	_IOWR(Z1_SW_SYNC_IOC_MAGIC, 0, struct z1_sw_sync_data)
#define Z1_SW_SYNC_IOC_INC _IOW(Z1_SW_SYNC_IOC_MAGIC, 1, uint32_t)

static uint64_t now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static int sw_fence_create(int tl, const char *name, uint32_t value)
{
	struct z1_sw_sync_data d;

	memset(&d, 0, sizeof(d));
	d.value = value;
	strncpy(d.name, name, sizeof(d.name) - 1);
	d.fence = -1;
	if (ioctl(tl, Z1_SW_SYNC_IOC_CREATE_FENCE, &d) < 0)
		return -1;
	return d.fence;
}

static int sw_timeline_inc(int tl, uint32_t by)
{
	return ioctl(tl, Z1_SW_SYNC_IOC_INC, &by) < 0 ? -errno : 0;
}

/*
 * If the display goes idle right after a frame, that frame's release fences
 * would never be signalled and a producer could block forever waiting for its
 * buffer.  This watchdog signs the timeline off after 250 ms of silence.
 */
static void *fence_watchdog(void *arg)
{
	z1_hwc2 *h = static_cast<z1_hwc2 *>(arg);

	while (!h->wd_stop) {
		usleep(100 * 1000);
		if (!h->fences_pending)
			continue;
		if (now_ms() - h->last_present_ms < 250)
			continue;
		pthread_mutex_lock(&h->fence_lock);
		if (h->fences_pending) {
			if (sw_timeline_inc(h->timeline_fd, 1) == 0)
				h->timeline_val++;
			h->fences_pending = false;
			klogf(1, "z1hwc2: fence watchdog: released idle frame "
			     "(timeline=%u)\n", h->timeline_val);
		}
		pthread_mutex_unlock(&h->fence_lock);
	}
	return nullptr;
}

static void fences_init(z1_hwc2 *h)
{
	pthread_mutex_init(&h->fence_lock, nullptr);
	h->timeline_fd = -1;
	h->last_present_ms = now_ms();
	if (prop_int("debug.z1.hwc.fences", 0) == 0) {
		klogf(1, "z1hwc2: fences disabled (debug.z1.hwc.fences=0)\n");
		return;
	}
	h->timeline_fd = open("/sys/kernel/debug/sync/sw_sync",
			      O_RDWR | O_CLOEXEC);
	if (h->timeline_fd < 0) {
		klogf(0, "z1hwc2: fences requested but "
		     "/sys/kernel/debug/sync/sw_sync is unavailable (%s); "
		     "is debugfs mounted and CONFIG_SW_SYNC enabled?\n",
		      strerror(errno));
		return;
	}
	h->fences_on = true;
	if (pthread_create(&h->wd_thread, nullptr, fence_watchdog, h) == 0)
		h->wd_started = true;
	klogf(1, "z1hwc2: sw_sync fences enabled (timeline fd=%d)\n",
	      h->timeline_fd);
}

/* ---------------------------------------------------------------- helpers --- */

static inline z1_hwc2 *H(hwc2_device_t *d)
{
	return static_cast<z1_hwc2 *>(d);
}

static uint32_t prop_id(int fd, uint32_t obj, uint32_t type, const char *name,
			uint64_t *value, bool *found)
{
	drmModeObjectProperties *props = drmModeObjectGetProperties(fd, obj, type);
	uint32_t id = 0;

	if (found)
		*found = false;
	if (!props)
		return 0;
	for (uint32_t i = 0; i < props->count_props; i++) {
		drmModePropertyRes *p = drmModeGetProperty(fd, props->props[i]);

		if (!p)
			continue;
		if (!strcmp(p->name, name)) {
			id = p->prop_id;
			if (value)
				*value = props->prop_values[i];
			if (found)
				*found = true;
			drmModeFreeProperty(p);
			break;
		}
		drmModeFreeProperty(p);
	}
	drmModeFreeObjectProperties(props);
	return id;
}

static void fb_forget(z1_hwc2 *h, z1_fb *fb)
{
	if (fb->fb_id) {
		/* Deferred: keep the fb alive for one more frame. */
		h->pending_release.push_back(std::make_pair(fb->fb_id, fb->gem));
	}
	memset(fb, 0, sizeof(*fb));
	fb->src_fd = -1;
}

/*
 * Import a gralloc buffer as a DRM framebuffer.
 *
 * Requires the buffer to be a physically contiguous dma-buf: mediatek's GEM
 * import rejects anything else.  Returns 0 on success, negative errno
 * otherwise (the caller then has to fall back to CLIENT composition).
 */
static int fb_import(z1_hwc2 *h, buffer_handle_t handle, z1_fb *out)
{
	struct z1_handle *gh = (struct z1_handle *)handle;
	uint32_t gem = 0, fb = 0, fourcc, pitch;
	int bpp;

	if (!handle || gh->fd < 0) {
		klogf(1, "z1hwc2: import refused: no handle/fd\n");
		return -EINVAL;
	}
	if (gh->magic != Z1_HANDLE_MAGIC) {
		klogf(0, "z1hwc2: import refused: bad gralloc magic %#x "
		     "(fd=%d %dx%d stride=%d fmt=%d)\n",
		     gh->magic, gh->fd, gh->width, gh->height, gh->stride,
		     gh->format);
		return -EINVAL;
	}
	if (gh->width <= 0 || gh->height <= 0 || gh->width > 4096 ||
	    gh->height > 4096 || gh->stride <= 0 || gh->stride > 8192) {
		klogf(0, "z1hwc2: import refused: implausible geometry fd=%d "
		     "%dx%d stride=%d\n", gh->fd, gh->width, gh->height,
		     gh->stride);
		return -EINVAL;
	}
	bpp = hal_bpp(gh->format);
	fourcc = hal_to_drm(gh->format);
	if (!bpp || !fourcc) {
		klogf(1, "z1hwc2: import refused: unsupported format %d\n",
		      gh->format);
		return -EINVAL;
	}

	if (drmPrimeFDToHandle(h->drm_fd, gh->fd, &gem)) {
		int err = errno;

		klogf(1, "z1hwc2: drmPrimeFDToHandle(%d) failed: %s "
		     "(buffer is not a dma-buf, or not physically contiguous)\n",
		     gh->fd, strerror(err));
		return -err;
	}

	pitch = (uint32_t)gh->stride * (uint32_t)bpp;
	{
		uint32_t handles[4] = { gem, 0, 0, 0 };
		uint32_t pitches[4] = { pitch, 0, 0, 0 };
		uint32_t offsets[4] = { 0, 0, 0, 0 };

		if (drmModeAddFB2(h->drm_fd, (uint32_t)gh->width,
				  (uint32_t)gh->height, fourcc, handles, pitches,
				  offsets, &fb, 0)) {
			int err = errno;

			klogf(0, "z1hwc2: drmModeAddFB2 failed: %s "
			     "(%dx%d stride=%dp pitch=%u fourcc=%c%c%c%c)\n",
			     strerror(err), gh->width, gh->height, gh->stride,
			     pitch, (int)(fourcc & 0xff),
			     (int)((fourcc >> 8) & 0xff),
			     (int)((fourcc >> 16) & 0xff),
			     (int)((fourcc >> 24) & 0xff));
			drmCloseBufferHandle(h->drm_fd, gem);
			return -err;
		}
	}

	out->handle = handle;
	out->src_fd = gh->fd;
	out->w = (uint32_t)gh->width;
	out->h = (uint32_t)gh->height;
	out->pitch = pitch;
	out->fourcc = fourcc;
	out->gem = gem;
	out->fb_id = fb;
	return 0;
}

static int dumb_ensure(z1_hwc2 *h, z1_dumb *d, uint32_t w, uint32_t hgt,
		       uint32_t fourcc)
{
	struct drm_mode_create_dumb create;
	struct drm_mode_map_dumb map;

	if (d->fb_id && d->w == w && d->h == hgt && d->fourcc == fourcc)
		return 0;
	if (d->fb_id) {
		if (d->map && d->map != MAP_FAILED)
			munmap(d->map, d->size);
		d->map = nullptr;
		h->pending_release.push_back(std::make_pair(d->fb_id, 0u));
		d->fb_id = 0;
	}
	if (d->handle) {
		struct drm_mode_destroy_dumb destroy;

		memset(&destroy, 0, sizeof(destroy));
		destroy.handle = d->handle;
		ioctl(h->drm_fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
		d->handle = 0;
	}

	memset(&create, 0, sizeof(create));
	create.width = w;
	create.height = hgt;
	create.bpp = hal_bpp(fourcc == DRM_FORMAT_RGB565 ? HAL_PIXEL_FORMAT_RGB_565
							 : HAL_PIXEL_FORMAT_RGBA_8888) * 8;
	if (!create.bpp)
		return -EINVAL;
	if (ioctl(h->drm_fd, DRM_IOCTL_MODE_CREATE_DUMB, &create)) {
		klogf(0, "z1hwc2: CREATE_DUMB %ux%u bpp=%u failed: %s\n", w, hgt,
		      create.bpp, strerror(errno));
		return -errno;
	}

	memset(&map, 0, sizeof(map));
	map.handle = create.handle;
	if (ioctl(h->drm_fd, DRM_IOCTL_MODE_MAP_DUMB, &map)) {
		klogf(0, "z1hwc2: MAP_DUMB failed: %s\n", strerror(errno));
		return -errno;
	}
	d->map = mmap(nullptr, create.size, PROT_READ | PROT_WRITE, MAP_SHARED,
		      h->drm_fd, (off_t)map.offset);
	if (d->map == MAP_FAILED) {
		klogf(0, "z1hwc2: mmap dumb %u bytes failed: %s\n", create.size,
		      strerror(errno));
		d->map = nullptr;
		return -errno;
	}
	{
		uint32_t handles[4] = { create.handle, 0, 0, 0 };
		uint32_t pitches[4] = { create.pitch, 0, 0, 0 };
		uint32_t offsets[4] = { 0, 0, 0, 0 };

		if (drmModeAddFB2(h->drm_fd, w, hgt, fourcc, handles, pitches,
				  offsets, &d->fb_id, 0)) {
			klogf(0, "z1hwc2: AddFB2(dumb %ux%u fourcc=%#x) failed: %s\n",
			      w, hgt, fourcc, strerror(errno));
			munmap(d->map, create.size);
			d->map = nullptr;
			return -errno;
		}
	}
	d->handle = create.handle;
	d->pitch = create.pitch;
	d->size = create.size;
	d->w = w;
	d->h = hgt;
	d->fourcc = fourcc;
	klogf(1, "z1hwc2: dumb shadow %ux%u fourcc=%#x pitch=%u fb=%u\n", w, hgt,
	      fourcc, create.pitch, d->fb_id);
	return 0;
}

/*
 * Copy a client target into a local dumb buffer.
 *
 * Only used when the gralloc buffer cannot be imported (lima GEM/ashmem), so a
 * HAL update never leaves the panel black during bring-up.  The source is read
 * through the dma-buf CPU access path so GPU writes are visible.
 */
static int copy_into_dumb(z1_hwc2 *h, z1_dumb *d, buffer_handle_t handle)
{
	struct z1_handle *gh = (struct z1_handle *)handle;
	int bpp = hal_bpp(gh->format);
	uint32_t fourcc = hal_to_drm(gh->format);
	uint32_t src_pitch, aw;
	uint8_t *src;
	struct dma_buf_sync sync;
	bool synced = false;
	int ret;

	if (!bpp || !fourcc)
		return -EINVAL;
	/* OVL wants an aligned pitch; keep the dumb buffer aligned the same
	 * way gralloc aligns its strides (16 pixels). */
	aw = ((uint32_t)gh->width + 15u) & ~15u;
	if (dumb_ensure(h, d, aw, (uint32_t)gh->height, fourcc))
		return -EINVAL;

	src_pitch = (uint32_t)gh->stride * (uint32_t)bpp;
	if (gh->size <= 0 || src_pitch * (uint32_t)gh->height >
				    (uint32_t)gh->size + src_pitch) {
		klogf(0, "z1hwc2: copy: implausible source size %d\n", gh->size);
		return -EINVAL;
	}
	src = (uint8_t *)mmap(nullptr, (size_t)gh->size, PROT_READ, MAP_SHARED,
			      gh->fd, 0);
	if (src == MAP_FAILED) {
		klogf(0, "z1hwc2: copy: mmap(fd=%d, %d bytes) failed: %s\n",
		      gh->fd, gh->size, strerror(errno));
		return -errno;
	}

	memset(&sync, 0, sizeof(sync));
	sync.flags = DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ;
	if (ioctl(gh->fd, DMA_BUF_IOCTL_SYNC, &sync) == 0)
		synced = true;

	{
		uint32_t rows = (uint32_t)gh->height;
		uint32_t bytes = (uint32_t)gh->width * (uint32_t)bpp;
		uint32_t y;

		if (bytes > src_pitch)
			bytes = src_pitch;
		for (y = 0; y < rows; y++)
			memcpy((uint8_t *)d->map + (size_t)y * d->pitch,
			       src + (size_t)y * src_pitch, bytes);
	}

	if (synced) {
		sync.flags = DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ;
		(void)ioctl(gh->fd, DMA_BUF_IOCTL_SYNC, &sync);
	}
	munmap(src, (size_t)gh->size);
	ret = 0;
	return ret;
}

/* ------------------------------------------------------------------ probe --- */

static int drm_open(z1_hwc2 *h)
{
	char prop[PROP_VALUE_MAX];
	const char *path = "/dev/dri/card1";
	drmModeRes *res = nullptr;
	drmModePlaneRes *pres = nullptr;
	int waited_ms = 0;
	int i;

	memset(prop, 0, sizeof(prop));
	__system_property_get("hwc.drm.device", prop);
	if (prop[0])
		path = strdup(prop);
	h->drm_path = path;

	/*
	 * The mediatek-drm component master binds late (~35 s on Z1: ovl/rdma/
	 * dsi probe at 0.3 s but the aggregate device registers afterwards),
	 * while the composer service starts at ~24 s.  Wait for the node.
	 */
	for (i = 0; i < 120; i++) {
		h->drm_fd = open(path, O_RDWR | O_CLOEXEC);
		if (h->drm_fd < 0) {
			if (errno != ENOENT && errno != ENXIO) {
				klogf(0, "z1hwc2: open(%s) failed: %s\n", path,
				      strerror(errno));
				return -1;
			}
			if (i == 0 || i % 20 == 19)
				klogf(1, "z1hwc2: waiting for %s (%d ms)\n",
				      path, waited_ms);
			usleep(500 * 1000);
			waited_ms += 500;
			continue;
		}
		/*
		 * DRM_IOCTL_MODE_ATOMIC is DRM_MASTER-gated (drm_ioctl.c), so
		 * this process must own the master.  The kernel hands master to
		 * the FIRST opener without requiring CAP_SYS_ADMIN
		 * (drm_auth.c: drm_master_open() -> "if there is no current
		 * master make this fd it"), and the composer service only has
		 * CAP_SYS_NICE, so the only way to (re)acquire it is to reopen.
		 * Another short-lived opener (the z1drmtest probe, gralloc's dumb
		 * backend) may win the race; retry until it is released.
		 */
		if (drmSetMaster(h->drm_fd) == 0)
			break;
		{
			int err = errno;

			klogf(1, "z1hwc2: %s open but not DRM master yet (%s); "
			     "reopening to acquire it\n", path, strerror(err));
		}
		close(h->drm_fd);
		h->drm_fd = -1;
		usleep(250 * 1000);
		waited_ms += 250;
	}
	if (h->drm_fd < 0) {
		klogf(0, "z1hwc2: cannot open %s as DRM master: %s\n", path,
		      strerror(errno));
		return -1;
	}

	{
		drmVersion *v = drmGetVersion(h->drm_fd);

		if (v) {
			klogf(1, "z1hwc2: opened %s fd=%d driver=%s (waited %d ms)\n",
			      path, h->drm_fd, v->name ? v->name : "?", waited_ms);
			drmFreeVersion(v);
		}
	}

	if (drmSetClientCap(h->drm_fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1) ||
	    drmSetClientCap(h->drm_fd, DRM_CLIENT_CAP_ATOMIC, 1)) {
		klogf(0, "z1hwc2: %s has no atomic modesetting (%s)\n", path,
		      strerror(errno));
		return -1;
	}

	res = drmModeGetResources(h->drm_fd);
	if (!res) {
		klogf(0, "z1hwc2: drmModeGetResources failed: %s\n",
		      strerror(errno));
		return -1;
	}
	klogf(1, "z1hwc2: resources crtcs=%d encoders=%d connectors=%d\n",
	      res->count_crtcs, res->count_encoders, res->count_connectors);
	if (res->count_crtcs < 1) {
		klogf(0, "z1hwc2: no CRTC\n");
		drmModeFreeResources(res);
		return -1;
	}
	h->crtc_id = res->crtcs[0];

	for (i = 0; i < res->count_connectors; i++) {
		drmModeConnector *c = drmModeGetConnector(h->drm_fd,
							  res->connectors[i]);

		if (!c)
			continue;
		if (c->connection == DRM_MODE_CONNECTED && c->count_modes > 0) {
			h->connector_id = c->connector_id;
			h->connector_type = (uint32_t)c->connector_type;
			h->mode = c->modes[0];
			h->width = (uint32_t)h->mode.hdisplay;
			h->height = (uint32_t)h->mode.vdisplay;
			drmModeFreeConnector(c);
			break;
		}
		drmModeFreeConnector(c);
	}
	if (!h->connector_id) {
		klogf(0, "z1hwc2: no connected connector with modes\n");
		drmModeFreeResources(res);
		return -1;
	}
	klogf(1, "z1hwc2: crtc=%u connector=%u mode=%s %ux%u@%u\n", h->crtc_id,
	      h->connector_id, h->mode.name, h->width, h->height,
	      h->mode.vrefresh);

	h->connector_crtc_prop = prop_id(h->drm_fd, h->connector_id,
					 DRM_MODE_OBJECT_CONNECTOR, "CRTC_ID",
					 nullptr, nullptr);
	/* The DRM core creates this blob property as "MODE_ID"
	 * (drm_mode_config.c: drm_property_create(..., "MODE_ID", 0)), not
	 * "MODE".  Looking up the wrong name yields property id 0 and
	 * drmModeAtomicAddProperty(req, crtc, 0, blob) then fails the whole
	 * commit.  Keep "MODE" as a fallback for older kernels. */
	h->crtc_mode_prop = prop_id(h->drm_fd, h->crtc_id, DRM_MODE_OBJECT_CRTC,
				    "MODE_ID", nullptr, nullptr);
	if (!h->crtc_mode_prop)
		h->crtc_mode_prop = prop_id(h->drm_fd, h->crtc_id,
					    DRM_MODE_OBJECT_CRTC, "MODE", nullptr,
					    nullptr);
	h->crtc_active_prop = prop_id(h->drm_fd, h->crtc_id,
				      DRM_MODE_OBJECT_CRTC, "ACTIVE", nullptr,
				      nullptr);

	pres = drmModeGetPlaneResources(h->drm_fd);
	if (!pres) {
		klogf(0, "z1hwc2: drmModeGetPlaneResources failed: %s\n",
		      strerror(errno));
		drmModeFreeResources(res);
		return -1;
	}
	for (uint32_t p = 0; p < pres->count_planes && h->plane_count < Z1_MAX_PLANES;
	     p++) {
		drmModePlane *pl = drmModeGetPlane(h->drm_fd, pres->planes[p]);
		z1_plane *zp;
		uint64_t val = 0;
		bool found = false;

		if (!pl)
			continue;
		/* The Z1 has a single CRTC (index 0). */
		if (!(pl->possible_crtcs & 1)) {
			drmModeFreePlane(pl);
			continue;
		}
		zp = &h->planes[h->plane_count];
		memset(zp, 0, sizeof(*zp));
		zp->id = pl->plane_id;
		zp->p_fb = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "FB_ID", nullptr, nullptr);
		zp->p_crtc = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				     "CRTC_ID", nullptr, nullptr);
		zp->p_cx = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "CRTC_X", nullptr, nullptr);
		zp->p_cy = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "CRTC_Y", nullptr, nullptr);
		zp->p_cw = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "CRTC_W", nullptr, nullptr);
		zp->p_ch = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "CRTC_H", nullptr, nullptr);
		zp->p_sx = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "SRC_X", nullptr, nullptr);
		zp->p_sy = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "SRC_Y", nullptr, nullptr);
		zp->p_sw = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "SRC_W", nullptr, nullptr);
		zp->p_sh = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				   "SRC_H", nullptr, nullptr);
		zp->p_zpos = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				     "zpos", &val, &found);
		zp->has_zpos = found;
		zp->zpos_val = found ? (uint32_t)val : (uint32_t)h->plane_count;
		zp->p_alpha = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				      "alpha", nullptr, nullptr);
		zp->has_alpha = zp->p_alpha != 0;
		zp->p_blend = prop_id(h->drm_fd, zp->id, DRM_MODE_OBJECT_PLANE,
				      "pixel blend mode", nullptr, nullptr);
		zp->has_blend = zp->p_blend != 0;
		zp->p_rotation = prop_id(h->drm_fd, zp->id,
					 DRM_MODE_OBJECT_PLANE, "rotation",
					 nullptr, nullptr);
		h->plane_count++;
		drmModeFreePlane(pl);
	}
	drmModeFreePlaneResources(pres);
	drmModeFreeResources(res);

	/* zpos is immutable; the plane array order must follow it. */
	for (i = 0; i + 1 < h->plane_count; i++) {
		for (int j = 0; j + 1 < h->plane_count - i; j++) {
			if (h->planes[j].zpos_val > h->planes[j + 1].zpos_val) {
				z1_plane t = h->planes[j];

				h->planes[j] = h->planes[j + 1];
				h->planes[j + 1] = t;
			}
		}
	}

	if (h->plane_count < 2) {
		klogf(0, "z1hwc2: need >= 2 planes, found %d\n", h->plane_count);
		return -1;
	}
	for (i = 0; i < h->plane_count; i++) {
		z1_plane *zp = &h->planes[i];

		klogf(1, "z1hwc2: plane[%d] id=%u zpos=%u alpha=%d blend=%d\n", i,
		      zp->id, zp->zpos_val, zp->has_alpha ? 1 : 0,
		      zp->has_blend ? 1 : 0);
		if (!zp->p_fb || !zp->p_crtc || !zp->p_cx || !zp->p_cy ||
		    !zp->p_cw || !zp->p_ch || !zp->p_sx || !zp->p_sy ||
		    !zp->p_sw || !zp->p_sh) {
			klogf(0, "z1hwc2: plane %u is missing atomic properties "
			     "(fb=%u crtc=%u cw=%u sw=%u)\n", zp->id, zp->p_fb,
			      zp->p_crtc, zp->p_cw, zp->p_sw);
			return -1;
		}
	}
	/* The first commit only needs planes when the CRTC is already up (the
	 * DRM fbdev has done a full modeset), but check the props anyway. */
	if (!h->connector_crtc_prop || !h->crtc_mode_prop ||
	    !h->crtc_active_prop)
		klogf(1, "z1hwc2: crt/connector modeset props incomplete "
		     "(conn_crtc=%u mode=%u active=%u); plane-only commits only\n",
		      h->connector_crtc_prop, h->crtc_mode_prop,
		      h->crtc_active_prop);

	klogf(1, "z1hwc2: probe done: %ux%u planes=%d\n", h->width, h->height,
	      h->plane_count);
	return 0;
}

/* ------------------------------------------------------------- composition --- */

static const char *comp_name(hwc2_composition_t c)
{
	switch (c) {
	case HWC2_COMPOSITION_INVALID: return "INVALID";
	case HWC2_COMPOSITION_CLIENT: return "CLIENT";
	case HWC2_COMPOSITION_DEVICE: return "DEVICE";
	case HWC2_COMPOSITION_SOLID_COLOR: return "SOLID";
	case HWC2_COMPOSITION_CURSOR: return "CURSOR";
	case HWC2_COMPOSITION_SIDEBAND: return "SIDEBAND";
	default: return "?";
	}
}

/* ---------------------------------------------------------------- present --- */

static bool add_prop(drmModeAtomicReq *req, const char *name, uint32_t obj,
		     uint32_t prop, uint64_t val)
{
	if (!prop) {
		klogf(0, "z1hwc2: no DRM property for %s (object %u)\n", name,
		      obj);
		return false;
	}
	if (drmModeAtomicAddProperty(req, obj, prop, val) < 0) {
		klogf(0, "z1hwc2: atomic add %s(obj=%u prop=%u val=%" PRIu64
		     ") failed: %s\n", name, obj, prop, val, strerror(errno));
		return false;
	}
	return true;
}

static int wait_fence(int fd, int timeout_ms)
{
	struct pollfd pfd;
	int ret;
	int tries;

	if (fd < 0)
		return 0;
	pfd.fd = fd;
	pfd.events = POLLIN;
	for (tries = 0; tries < 3; tries++) {
		ret = poll(&pfd, 1, timeout_ms << tries);
		if (ret > 0)
			return 0;
		if (ret == 0)
			continue;
		if (errno != EINTR)
			return -errno;
	}
	return -ETIME;
}

static void program_plane(z1_hwc2 *h, drmModeAtomicReq *req, int idx,
			  uint32_t fb_id, int x, int y, int w, int hgt,
			  const hwc_frect_t *crop, float alpha, int32_t blend,
			  bool has_alpha_channel)
{
	z1_plane *p = &h->planes[idx];
	int32_t sx, sy, sw, sh;
	uint32_t a8;
	uint32_t bmode;

	if (!fb_id) {
		/* Explicitly disable: a plane that is not committed keeps its
		 * previous SRC_CON bit and would keep scanning the old
		 * buffer. */
		add_prop(req, "FB_ID", p->id, p->p_fb, 0);
		add_prop(req, "CRTC_ID", p->id, p->p_crtc, 0);
		return;
	}

	sx = crop ? (int32_t)crop->left : 0;
	sy = crop ? (int32_t)crop->top : 0;
	sw = crop ? (int32_t)(crop->right - crop->left) : w;
	sh = crop ? (int32_t)(crop->bottom - crop->top) : hgt;
	if (sw <= 0)
		sw = w;
	if (sh <= 0)
		sh = hgt;

	add_prop(req, "FB_ID", p->id, p->p_fb, fb_id);
	add_prop(req, "CRTC_ID", p->id, p->p_crtc, h->crtc_id);
	add_prop(req, "CRTC_X", p->id, p->p_cx, (uint64_t)(uint32_t)x);
	add_prop(req, "CRTC_Y", p->id, p->p_cy, (uint64_t)(uint32_t)y);
	add_prop(req, "CRTC_W", p->id, p->p_cw, (uint32_t)w);
	add_prop(req, "CRTC_H", p->id, p->p_ch, (uint32_t)hgt);
	/* SRC_* are 16.16 fixed point */
	add_prop(req, "SRC_X", p->id, p->p_sx, (uint32_t)(sx << 16));
	add_prop(req, "SRC_Y", p->id, p->p_sy, (uint32_t)(sy << 16));
	add_prop(req, "SRC_W", p->id, p->p_sw, (uint32_t)(sw << 16));
	add_prop(req, "SRC_H", p->id, p->p_sh, (uint32_t)(sh << 16));

	/*
	 * Plane alpha: the MT6572 OVL driver takes the LOW 8 BITS of the
	 * 0..0xffff DRM alpha value (mtk_disp_ovl.c: `con |= state->base.alpha
	 * & OVL_CON_ALPHA` with OVL_CON_ALPHA == 0xff), so the value must be
	 * quantised to 0..255 or a 50% layer would become fully transparent.
	 */
	a8 = (uint32_t)(alpha * 255.0f + 0.5f);
	if (a8 > 255)
		a8 = 255;
	(void)has_alpha_channel;
	if (p->has_alpha)
		add_prop(req, "alpha", p->id, p->p_alpha, a8);

	if (p->has_blend) {
		switch (blend) {
		case HWC2_BLEND_MODE_NONE:
			bmode = Z1_DRM_BLEND_PIXEL_NONE;
			break;
		case HWC2_BLEND_MODE_COVERAGE:
			bmode = Z1_DRM_BLEND_COVERAGE;
			break;
		case HWC2_BLEND_MODE_PREMULTIPLIED:
		default:
			bmode = Z1_DRM_BLEND_PREMULTI;
			break;
		}
		add_prop(req, "pixel blend mode", p->id, p->p_blend, bmode);
	}
}

/* Return a fb_id for the client target, importing it or (fallback) copying
 * it into a local dumb buffer.  0 on success. */
static int client_target_fb(z1_hwc2 *h, uint32_t *out_fb, uint32_t *out_w,
			    uint32_t *out_h, const char **how)
{
	struct z1_handle *gh = (struct z1_handle *)h->client_target;
	int ret;

	*out_w = h->width;
	*out_h = h->height;
	if (!h->client_target) {
		*out_fb = 0;
		*how = "none";
		return 0;
	}
	if (gh->magic == Z1_HANDLE_MAGIC && gh->width > 0 && gh->height > 0) {
		*out_w = (uint32_t)gh->width;
		*out_h = (uint32_t)gh->height;
	}
	if (h->client_fb.fb_id && h->client_fb.handle == h->client_target) {
		*out_fb = h->client_fb.fb_id;
		*how = "cached";
		return 0;
	}
	if (h->client_fb.fb_id)
		fb_forget(h, &h->client_fb);

	ret = fb_import(h, h->client_target, &h->client_fb);
	if (ret == 0) {
		*out_fb = h->client_fb.fb_id;
		*how = "imported";
		return 0;
	}

	/* Fallback: CPU copy into a dumb buffer (double buffered so the frame
	 * being scanned is not overwritten). */
	{
		z1_dumb *d = &h->shadow[h->client_copy_slot];

		if (copy_into_dumb(h, d, h->client_target) == 0) {
			h->client_copy_slot ^= 1;
			*out_fb = d->fb_id;
			*how = "copied";
			if (h->frames < 3 || h->frames % 600 == 0)
				klogf(1, "z1hwc2: client target COPIED (slow "
				     "path): fd=%d %dx%d stride=%d fmt=%s -> "
				     "dumb fb=%u frame=%" PRIu64 "\n", gh->fd,
				      gh->width, gh->height, gh->stride,
				      hal_fmt_name(gh->format), d->fb_id,
				      h->frames);
			return 0;
		}
	}
	*out_fb = 0;
	*how = "unusable";
	return -EINVAL;
}

static int32_t z1_present_display(hwc2_device_t *d, hwc2_display_t display,
				  int32_t *retire_fence);

/* ------------------------------------------------------------ HWC2 device --- */

static void z1_get_capabilities(hwc2_device_t *d, uint32_t *out_count,
				int32_t *out_caps)
{
	(void)d;
	(void)out_caps;
	/* No capabilities: SKIP_VALIDATE in particular is unsafe for a HAL that
	 * decides composition types itself, because SurfaceFlinger would then
	 * never call validateDisplay() and every layer would stay INVALID. */
	*out_count = 0;
}

static void z1_register_callback(hwc2_device_t *d, int32_t descriptor,
				 hwc2_callback_data_t data,
				 hwc2_function_pointer_t fn)
{
	z1_hwc2 *h = H(d);

	switch (descriptor) {
	case HWC2_CALLBACK_HOTPLUG:
		h->cb_hotplug = fn;
		h->cb_data = data;
		if (fn) {
			typedef void (*hotplug_fn)(hwc2_callback_data_t,
						  hwc2_display_t, int32_t);

			klogf(1, "z1hwc2: hotplug callback registered, "
			     "reporting display 0 connected\n");
			((hotplug_fn)fn)(data, 0, HWC2_CONNECTION_CONNECTED);
		}
		break;
	case HWC2_CALLBACK_REFRESH:
		h->cb_refresh = fn;
		h->cb_data = data;
		break;
	case HWC2_CALLBACK_VSYNC:
		h->cb_vsync = fn;
		h->cb_data = data;
		break;
	default:
		klogf(1, "z1hwc2: register_callback: unknown descriptor %d\n",
		      descriptor);
		break;
	}
}

static int32_t z1_accept_display_changes(hwc2_device_t *d,
					 hwc2_display_t display)
{
	z1_hwc2 *h = H(d);

	(void)display;
	/* Our validateDisplay() already committed the decisions to the layer
	 * objects; accepting just forgets about the pending change list. */
	h->changed.clear();
	h->validated = true;
	klogf(2, "z1hwc2: acceptDisplayChanges\n");
	return HWC2_ERROR_NONE;
}

static int32_t z1_create_layer(hwc2_device_t *d, hwc2_display_t display,
			       uint32_t *out_layer)
{
	z1_hwc2 *h = H(d);

	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	if (h->layers.size() >= Z1_MAX_LAYERS)
		return HWC2_ERROR_NO_RESOURCES;

	hwc2_layer_t id = h->next_layer_id++;

	z1_layer l;
	memset(&l, 0, sizeof(l));
	l.id = id;
	l.acquire_fence = -1;
	l.composition = HWC2_COMPOSITION_INVALID;
	l.blend = HWC2_BLEND_MODE_INVALID;
	l.alpha = 1.0f;
	l.fb.src_fd = -1;
	h->layers[id] = l;
	*out_layer = id;
	klogf(2, "z1hwc2: createLayer -> %" PRIu64 "\n", (uint64_t)id);
	return HWC2_ERROR_NONE;
}

static z1_layer *layer_get(z1_hwc2 *h, hwc2_layer_t id)
{
	std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.find(id);

	if (it == h->layers.end())
		return nullptr;
	return &it->second;
}

static int32_t z1_destroy_layer(hwc2_device_t *d, hwc2_display_t display,
				hwc2_layer_t layer)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	if (l->fb.fb_id)
		fb_forget(h, &l->fb);
	if (l->acquire_fence >= 0)
		close(l->acquire_fence);
	if (l->release_fence >= 0)
		close(l->release_fence);
	h->layers.erase(layer);
	klogf(2, "z1hwc2: destroyLayer %" PRIu64 "\n", (uint64_t)layer);
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_display_attribute(hwc2_device_t *d,
					hwc2_display_t display, int32_t config,
					int32_t attribute, int32_t *out_value)
{
	z1_hwc2 *h = H(d);

	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	if (config != 0)
		return HWC2_ERROR_BAD_CONFIG;
	switch (attribute) {
	case HWC2_ATTRIBUTE_WIDTH:
		*out_value = (int32_t)h->width;
		return HWC2_ERROR_NONE;
	case HWC2_ATTRIBUTE_HEIGHT:
		*out_value = (int32_t)h->height;
		return HWC2_ERROR_NONE;
	case HWC2_ATTRIBUTE_VSYNC_PERIOD:
		*out_value = h->mode.vrefresh ?
			     (int32_t)(1000000000 / h->mode.vrefresh) : 16666667;
		return HWC2_ERROR_NONE;
	case HWC2_ATTRIBUTE_DPI_X:
		*out_value = 160 * 1000;
		return HWC2_ERROR_NONE;
	case HWC2_ATTRIBUTE_DPI_Y:
		*out_value = 160 * 1000;
		return HWC2_ERROR_NONE;
	default:
		return HWC2_ERROR_BAD_PARAMETER;
	}
}

static int32_t z1_get_display_configs(hwc2_device_t *d,
				      hwc2_display_t display,
				      uint32_t *out_num_configs,
				      hwc2_config_t *out_configs)
{
	(void)d;
	(void)display;
	if (out_configs)
		out_configs[0] = 0;
	*out_num_configs = 1;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_active_config(hwc2_device_t *d, hwc2_display_t display,
				    hwc2_config_t *out_config)
{
	(void)d;
	(void)display;
	*out_config = 0;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_active_config(hwc2_device_t *d, hwc2_display_t display,
				    hwc2_config_t config)
{
	(void)d;
	(void)display;
	return config == 0 ? HWC2_ERROR_NONE : HWC2_ERROR_BAD_CONFIG;
}

static int32_t z1_get_display_type(hwc2_device_t *d, hwc2_display_t display,
				   int32_t *out_type)
{
	(void)d;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	*out_type = HWC2_DISPLAY_TYPE_PHYSICAL;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_display_name(hwc2_device_t *d, hwc2_display_t display,
				   uint32_t *out_size, char *out_name)
{
	static const char name[] = "Z1 DSI (mediatek-drm)";
	uint32_t cap = out_size ? *out_size : 0;

	(void)d;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	if (out_name && cap > 0) {
		uint32_t n = (uint32_t)sizeof(name);

		if (n > cap)
			n = cap;
		memcpy(out_name, name, n);
		if (cap >= (uint32_t)sizeof(name))
			out_name[sizeof(name) - 1] = '\0';
		else
			out_name[cap - 1] = '\0';
		*out_size = n;
	} else {
		*out_size = (uint32_t)sizeof(name);
	}
	return HWC2_ERROR_NONE;
}

static uint32_t z1_get_max_virtual_display_count(hwc2_device_t *d)
{
	(void)d;
	return 0;
}

static int32_t z1_create_virtual_display(hwc2_device_t *d, uint32_t width,
					 uint32_t height, int32_t *format,
					 hwc2_display_t *out_display)
{
	(void)d;
	(void)width;
	(void)height;
	(void)format;
	(void)out_display;
	return HWC2_ERROR_UNSUPPORTED;
}

static int32_t z1_destroy_virtual_display(hwc2_device_t *d,
					  hwc2_display_t display)
{
	(void)d;
	(void)display;
	return HWC2_ERROR_UNSUPPORTED;
}

static int32_t z1_get_doze_support(hwc2_device_t *d, hwc2_display_t display,
				   int32_t *out_support)
{
	(void)d;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	*out_support = 0;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_hdr_capabilities(hwc2_device_t *d,
				       hwc2_display_t display,
				       uint32_t *out_num_types,
				       int32_t *out_types, float *max_lum,
				       float *max_avg_lum, float *min_lum)
{
	(void)d;
	(void)out_types;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	*out_num_types = 0;
	if (max_lum)
		*max_lum = 100.0f;
	if (max_avg_lum)
		*max_avg_lum = 100.0f;
	if (min_lum)
		*min_lum = 0.0f;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_color_modes(hwc2_device_t *d, hwc2_display_t display,
				  uint32_t *out_num_modes, int32_t *out_modes)
{
	(void)d;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	if (out_modes)
		out_modes[0] = 0; /* HAL_COLOR_MODE_NATIVE */
	*out_num_modes = 1;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_color_mode(hwc2_device_t *d, hwc2_display_t display,
				 int32_t mode)
{
	(void)d;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	return mode == 0 ? HWC2_ERROR_NONE : HWC2_ERROR_UNSUPPORTED;
}

static int32_t z1_set_color_transform(hwc2_device_t *d, hwc2_display_t display,
				      const float *matrix, int32_t hint)
{
	(void)d;
	(void)matrix;
	(void)hint;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	/* We never apply a colour transform; layers stay device-composable
	 * because SurfaceFlinger only skips the client transform when the
	 * SKIP_CLIENT_COLOR_TRANSFORM capability is advertised (we do not). */
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_cursor_position(hwc2_device_t *d, hwc2_display_t display,
				      hwc2_layer_t layer, int32_t x, int32_t y)
{
	(void)d;
	(void)layer;
	(void)x;
	(void)y;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	return HWC2_ERROR_UNSUPPORTED;
}

static int32_t z1_set_client_target(hwc2_device_t *d, hwc2_display_t display,
				    buffer_handle_t target,
				    int32_t acquire_fence, int32_t dataspace,
				    hwc_region_t damage)
{
	z1_hwc2 *h = H(d);
	struct z1_handle *gh = (struct z1_handle *)target;

	(void)damage;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;

	/* The device must be able to close the client's fd at any time, so
	 * take our own reference. */
	if (h->client_acquire_fence >= 0)
		close(h->client_acquire_fence);
	h->client_acquire_fence = acquire_fence >= 0 ? dup(acquire_fence) : -1;
	h->client_target = target;
	h->client_dataspace = dataspace;

	if (target) {
		klogf(2, "z1hwc2: setClientTarget handle=%p fd=%d %dx%d "
		     "stride=%d fmt=%s acquire=%d dataspace=%d\n", (void *)target,
		      gh ? gh->fd : -1, gh ? gh->width : -1,
		      gh ? gh->height : -1, gh ? gh->stride : -1,
		      gh ? hal_fmt_name(gh->format) : "?", acquire_fence,
		      dataspace);
	} else {
		klogf(2, "z1hwc2: setClientTarget null (no client layers)\n");
	}
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_buffer(hwc2_device_t *d, hwc2_display_t display,
				   hwc2_layer_t layer, buffer_handle_t buffer,
				   int32_t acquire_fence)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;

	if (l->fb.handle != buffer && l->fb.fb_id)
		fb_forget(h, &l->fb);
	l->handle = buffer;
	if (l->acquire_fence >= 0)
		close(l->acquire_fence);
	l->acquire_fence = acquire_fence >= 0 ? dup(acquire_fence) : -1;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_surface_damage(hwc2_device_t *d,
					   hwc2_display_t display,
					   hwc2_layer_t layer,
					   hwc_region_t damage)
{
	(void)d;
	(void)display;
	(void)layer;
	(void)damage;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_blend_mode(hwc2_device_t *d, hwc2_display_t display,
				       hwc2_layer_t layer, int32_t mode)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->blend = mode;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_color(hwc2_device_t *d, hwc2_display_t display,
				  hwc2_layer_t layer, hwc_color_t color)
{
	(void)d;
	(void)display;
	(void)layer;
	(void)color;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_composition_type(hwc2_device_t *d,
					     hwc2_display_t display,
					     hwc2_layer_t layer, int32_t type)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->composition = (hwc2_composition_t)type;
	klogf(3, "z1hwc2: layer %" PRIu64 " requested %s\n", (uint64_t)layer,
	      comp_name(l->composition));
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_dataspace(hwc2_device_t *d, hwc2_display_t display,
				      hwc2_layer_t layer, int32_t dataspace)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->dataspace = dataspace;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_display_frame(hwc2_device_t *d,
					  hwc2_display_t display,
					  hwc2_layer_t layer, hwc_rect_t frame)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->frame = frame;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_plane_alpha(hwc2_device_t *d,
					hwc2_display_t display,
					hwc2_layer_t layer, float alpha)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->alpha = alpha;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_sidestream(hwc2_device_t *d, hwc2_display_t display,
				       hwc2_layer_t layer,
				       const native_handle_t *stream)
{
	(void)d;
	(void)display;
	(void)layer;
	(void)stream;
	return HWC2_ERROR_UNSUPPORTED;
}

static int32_t z1_set_layer_source_crop(hwc2_device_t *d,
					hwc2_display_t display,
					hwc2_layer_t layer, hwc_frect_t crop)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->crop = crop;
	l->have_crop = true;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_transform(hwc2_device_t *d, hwc2_display_t display,
				      hwc2_layer_t layer, int32_t transform)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->transform = transform;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_visible_region(hwc2_device_t *d,
					   hwc2_display_t display,
					   hwc2_layer_t layer,
					   hwc_region_t visible)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	/* OVL has no occlusion support; we composite the full layer and let
	 * the layers above cover it.  Only the count is kept for diagnostics. */
	l->visible_rects = (int)visible.numRects;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_layer_z(hwc2_device_t *d, hwc2_display_t display,
			      hwc2_layer_t layer, uint32_t z)
{
	z1_hwc2 *h = H(d);
	z1_layer *l = layer_get(h, layer);

	(void)display;
	if (!l)
		return HWC2_ERROR_BAD_LAYER;
	l->z = z;
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_output_buffer(hwc2_device_t *d, hwc2_display_t display,
				    buffer_handle_t buffer, int32_t fence)
{
	(void)d;
	(void)display;
	(void)buffer;
	(void)fence;
	return HWC2_ERROR_UNSUPPORTED;
}

static int32_t z1_set_power_mode(hwc2_device_t *d, hwc2_display_t display,
				 int32_t mode)
{
	z1_hwc2 *h = H(d);

	(void)display;
	klogf(1, "z1hwc2: setPowerMode %d -> %s\n", mode,
	      mode == HWC2_POWER_MODE_ON ? "ON" : "OFF/DOZE");
	h->powered_on = (mode == HWC2_POWER_MODE_ON);
	/* Route B keeps the DSI streaming; blanking would leave the panel
	 * frozen with the last frame.  Presenting is skipped while off. */
	return HWC2_ERROR_NONE;
}

static int32_t z1_set_vsync_enabled(hwc2_device_t *d, hwc2_display_t display,
				    int32_t enabled)
{
	z1_hwc2 *h = H(d);

	(void)display;
	(void)enabled;
	/* Swallowed for now; stored so a future vsync thread can honour it. */
	(void)h;
	/* No vsync reporting yet: SurfaceFlinger falls back to its software
	 * vsync model ("Timed out waiting for hw vsync; faking it"). */
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_display_requests(hwc2_device_t *d,
				       hwc2_display_t display,
				       int32_t *out_display_requests,
				       uint32_t *out_num_elements,
				       hwc2_layer_t *out_layers,
				       int32_t *out_layer_requests)
{
	(void)d;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	*out_display_requests = 0;
	*out_num_elements = 0;
	(void)out_layers;
	(void)out_layer_requests;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_changed_composition_types(hwc2_device_t *d,
						hwc2_display_t display,
						uint32_t *out_num_elements,
						hwc2_layer_t *out_layers,
						int32_t *out_types)
{
	z1_hwc2 *h = H(d);
	uint32_t n;

	(void)display;
	if (!h->validated)
		return HWC2_ERROR_NOT_VALIDATED;
	n = (uint32_t)h->changed.size();
	if (!out_layers || !out_types) {
		*out_num_elements = n;
		return HWC2_ERROR_NONE;
	}
	if (*out_num_elements < n) {
		*out_num_elements = n;
		return HWC2_ERROR_NONE;
	}
	for (uint32_t i = 0; i < n; i++) {
		out_layers[i] = h->changed[i].first;
		out_types[i] = h->changed[i].second;
	}
	*out_num_elements = n;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_release_fences(hwc2_device_t *d, hwc2_display_t display,
				     uint32_t *out_num_elements,
				     hwc2_layer_t *out_layers,
				     int32_t *out_fences)
{
	z1_hwc2 *h = H(d);
	uint32_t n = 0;

	(void)display;
	if (!out_layers || !out_fences) {
		*out_num_elements = (uint32_t)h->layers.size();
		return HWC2_ERROR_NONE;
	}
	for (std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.begin();
	     it != h->layers.end(); ++it) {
		z1_layer *l = &it->second;

		if (n >= *out_num_elements)
			break;
		/* Only device layers that were scanned out this frame carry a
		 * fence; the rest are "ready immediately" (-1) as the spec
		 * allows.  Ownership of the fd transfers to SurfaceFlinger. */
		out_layers[n] = it->first;
		out_fences[n] = l->release_fence;
		l->release_fence = -1;
		n++;
	}
	*out_num_elements = n;
	return HWC2_ERROR_NONE;
}

static int32_t z1_get_client_target_support(hwc2_device_t *d,
					    hwc2_display_t display,
					    uint32_t width, uint32_t height,
					    int32_t format, int32_t dataspace)
{
	z1_hwc2 *h = H(d);

	(void)dataspace;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	if (width == h->width && height == h->height &&
	    (format == HAL_PIXEL_FORMAT_RGBA_8888 ||
	     format == HAL_PIXEL_FORMAT_RGBX_8888))
		return HWC2_ERROR_NONE;
	/* Anything else may still be handled through the copy fallback. */
	if (hal_bpp(format) && width > 0 && height > 0)
		return HWC2_ERROR_NONE;
	return HWC2_ERROR_UNSUPPORTED;
}

/* -------------------------------------------------------------- validate --- */

/*
 * True when this layer can be the bottom-most OVL plane with no client target
 * underneath: it must cover the whole display and be fully opaque.  Otherwise
 * whatever the OVL shows outside its rectangle is undefined, and the safe
 * choice is to keep plane 0 for the client target.
 */
static bool layer_can_be_background(const z1_layer *l, uint32_t w, uint32_t h)
{
	if (!l || !l->handle)
		return false;
	if (l->frame.left > 0 || l->frame.top > 0)
		return false;
	if (l->frame.right < (int)w || l->frame.bottom < (int)h)
		return false;
	if (l->alpha < 0.999f)
		return false;
	return true;
}

static int32_t z1_validate_display(hwc2_device_t *d, hwc2_display_t display,
				   uint32_t *out_num_types,
				   uint32_t *out_num_requests)
{
	z1_hwc2 *h = H(d);
	std::vector<z1_layer *> order;
	std::vector<z1_policy_layer> pl;
	std::vector<z1_policy_result> pr;
	int device_slots;
	int used;

	*out_num_types = 0;
	*out_num_requests = 0;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;

	h->changed.clear();
	h->mode_force_client = prop_int("debug.z1.hwc.mode", 1) == 0;
	g_verbose = prop_int("debug.z1.hwc.verbose", 1);

	/* ascend by z, ties by creation order (stable) */
	for (std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.begin();
	     it != h->layers.end(); ++it)
		order.push_back(&it->second);
	std::sort(order.begin(), order.end(),
		  [](const z1_layer *a, const z1_layer *b) {
			  if (a->z != b->z)
				  return a->z < b->z;
			  return a->id < b->id;
		  });

	pl.resize(order.size());
	pr.resize(order.size());

	for (size_t i = 0; i < order.size(); i++) {
		z1_layer *l = order[i];
		struct z1_handle *gh = (struct z1_handle *)l->handle;
		z1_policy_layer *p = &pl[i];

		memset(p, 0, sizeof(*p));
		p->id = l->id;
		p->z = l->z;
		p->requested_type = (int)l->composition;
		p->transform = (int)l->transform;
		p->frame_l = l->frame.left;
		p->frame_t = l->frame.top;
		p->frame_r = l->frame.right;
		p->frame_b = l->frame.bottom;
		p->have_crop = l->have_crop;
		p->crop_l = (int)l->crop.left;
		p->crop_t = (int)l->crop.top;
		p->crop_r = (int)l->crop.right;
		p->crop_b = (int)l->crop.bottom;
		p->display_w = (int)h->width;
		p->display_h = (int)h->height;

		if (l->handle && gh->magic == Z1_HANDLE_MAGIC && gh->fd >= 0 &&
		    gh->width > 0 && gh->height > 0 && gh->stride > 0) {
			p->handle_ok = 1;
			p->buf_w = gh->width;
			p->buf_h = gh->height;
		}
		p->format_ok = hal_bpp(gh ? gh->format : -1) &&
			       hal_to_drm(gh ? gh->format : -1);

		/*
		 * Only attempt the (expensive) dma-buf import when every other
		 * hardware constraint is already satisfied; the policy header
		 * owns those checks.
		 */
		if (p->handle_ok && p->format_ok) {
			z1_policy_layer probe = *p;

			probe.importable = 1;
			if (z1_policy_device_block_reason(&probe) == 0) {
				if (!l->fb.fb_id || l->fb.handle != l->handle ||
				    l->fb.src_fd != gh->fd) {
					if (l->fb.fb_id)
						fb_forget(h, &l->fb);
					(void)fb_import(h, l->handle, &l->fb);
				}
				p->importable = l->fb.fb_id ? 1 : 0;
			}
		}
	}

	/*
	 * How many planes may the DEVICE layers use?
	 *
	 * The point of this HAL is to put every layer on the OVL, so plane 0 is
	 * only reserved for the client target when a client target will actually
	 * exist and be visible:
	 *
	 *   - First try all planes.  If every layer fits as DEVICE *and* the
	 *     bottom layer covers the whole display opaquely, SurfaceFlinger has
	 *     no client composition at all, never calls setClientTarget(), and
	 *     plane 0 is free for a device layer.
	 *   - Otherwise reserve plane 0 for the client target and decide again
	 *     (the demoted layers are always the bottom of the stack, which is
	 *     exactly what the client target shows).
	 *
	 * Note that SurfaceFlinger calls setClientTarget() *after*
	 * validateDisplay(), so h->client_target is still the previous frame's
	 * buffer at this point; that is why the decision is made here and
	 * remembered in h->client_expected instead of being inferred later.
	 */
	{
		int n = (int)pl.size();

		device_slots = h->plane_count;
		if (device_slots < 0)
			device_slots = 0;
		used = z1_policy_decide(pl.data(), n, device_slots,
					h->mode_force_client, pr.data());
		if (n > 0 && used == n &&
		    layer_can_be_background(order[0], h->width, h->height)) {
			h->client_expected = false;
			if (g_verbose >= 1 &&
			    (h->frames < 3 || h->frames % 600 == 0))
				klogf(1, "z1hwc2: all %d layer(s) on hardware "
				     "planes, no client target\n", n);
		} else {
			device_slots = h->plane_count - 1;
			if (device_slots < 0)
				device_slots = 0;
			used = z1_policy_decide(pl.data(), n, device_slots,
						h->mode_force_client, pr.data());
			h->client_expected = true;
		}
	}

	for (size_t i = 0; i < order.size(); i++) {
		z1_layer *l = order[i];
		z1_policy_result *r = &pr[i];
		hwc2_composition_t want =
			(r->type == Z1_POLICY_DEVICE) ? HWC2_COMPOSITION_DEVICE
						      : HWC2_COMPOSITION_CLIENT;

		if (want != HWC2_COMPOSITION_DEVICE && l->fb.fb_id) {
			/* Do not pin dma-bufs of layers that the GPU will
			 * composite. */
			fb_forget(h, &l->fb);
		}
		if (l->composition != want && r->report) {
			h->changed.push_back(std::make_pair(l->id, (int32_t)want));
			if (g_verbose >= 1)
				klogf(1, "z1hwc2: report layer %" PRIu64
				      " %s -> %s (%s)\n", (uint64_t)l->id,
				      comp_name(l->composition), comp_name(want),
				      r->why ? r->why : "deviceable");
		}
		l->composition = want;

		if (g_verbose >= 2 && (h->frames < 3 || !h->changed.empty())) {
			struct z1_handle *gh = (struct z1_handle *)l->handle;

			if (want == HWC2_COMPOSITION_DEVICE)
				klogf(2, "z1hwc2: layer %" PRIu64 " z=%u DEVICE "
				     "(%dx%d+%d+%d src=%.0fx%.0f+%.0f+%.0f "
				     "fmt=%s alpha=%.2f blend=%d vis=%d)\n",
				      (uint64_t)l->id, l->z,
				      l->frame.right - l->frame.left,
				      l->frame.bottom - l->frame.top,
				      l->frame.left, l->frame.top,
				      l->crop.right - l->crop.left,
				      l->crop.bottom - l->crop.top, l->crop.left,
				      l->crop.top,
				      gh ? hal_fmt_name(gh->format) : "?",
				      l->alpha, l->blend, l->visible_rects);
			else
				klogf(2, "z1hwc2: layer %" PRIu64 " z=%u CLIENT "
				     "(%s)\n", (uint64_t)l->id, l->z,
				      r->why ? r->why : "?");
		}
	}

	h->validated = true;
	*out_num_types = (uint32_t)h->changed.size();
	*out_num_requests = 0;
	if (g_verbose >= 1 &&
	    (h->frames < 3 || !h->changed.empty() || h->frames % 600 == 0))
		klogf(1, "z1hwc2: validate: layers=%zu device=%d client=%zu "
		     "changes=%zu frame=%" PRIu64 "\n", order.size(), used,
		      order.size() - (size_t)used, h->changed.size(), h->frames);
	return h->changed.empty() ? HWC2_ERROR_NONE : HWC2_ERROR_HAS_CHANGES;
}

/* --------------------------------------------------------------- present --- */

static int32_t z1_present_display(hwc2_device_t *d, hwc2_display_t display,
				  int32_t *out_retire_fence)
{
	z1_hwc2 *h = H(d);
	drmModeAtomicReq *req;
	std::vector<z1_layer *> device_layers;
	std::vector<z1_layer *> scanned;
	uint32_t client_fb = 0;
	uint32_t client_w = 0, client_h = 0;
	const char *client_how = "none";
	uint32_t flags = 0;
	int slot = 0;
	int ret;

	*out_retire_fence = -1;
	if (display != 0)
		return HWC2_ERROR_BAD_DISPLAY;
	if (!h->powered_on) {
		klogf(3, "z1hwc2: present skipped (display off)\n");
		return HWC2_ERROR_NONE;
	}

	h->frames++;

	/* A framebuffer replaced during the previous frame can be destroyed
	 * now; the display can no longer be scanning it after this commit. */
	if (!h->pending_release.empty()) {
		for (size_t i = 0; i < h->pending_release.size(); i++) {
			if (h->pending_release[i].first)
				drmModeRmFB(h->drm_fd, h->pending_release[i].first);
			if (h->pending_release[i].second)
				drmCloseBufferHandle(h->drm_fd,
						     h->pending_release[i].second);
		}
		h->pending_release.clear();
	}

	/* Wait for producers before touching their memory. */
	for (std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.begin();
	     it != h->layers.end(); ++it) {
		z1_layer *l = &it->second;

		if (l->composition != HWC2_COMPOSITION_DEVICE)
			continue;
		if (l->acquire_fence >= 0) {
			if (wait_fence(l->acquire_fence, 100) < 0)
				klogf(0, "z1hwc2: layer %" PRIu64
				      " acquire fence timed out\n",
				      (uint64_t)l->id);
			close(l->acquire_fence);
			l->acquire_fence = -1;
		}
	}
	if (h->client_acquire_fence >= 0) {
		if (wait_fence(h->client_acquire_fence, 100) < 0)
			klogf(0, "z1hwc2: client target acquire fence timed out\n");
		close(h->client_acquire_fence);
		h->client_acquire_fence = -1;
	}

	/* The previous frame's client target must not be re-shown on a frame
	 * where the validate pass decided that every layer goes to hardware. */
	if (!h->client_expected) {
		client_fb = 0;
		client_how = "unused (all layers on hardware planes)";
	} else if (client_target_fb(h, &client_fb, &client_w, &client_h,
				    &client_how) != 0) {
		client_fb = 0;
	}

	/* Collect device layers bottom-to-top. */
	for (std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.begin();
	     it != h->layers.end(); ++it) {
		z1_layer *l = &it->second;

		if (l->composition == HWC2_COMPOSITION_DEVICE)
			device_layers.push_back(l);
	}
	std::sort(device_layers.begin(), device_layers.end(),
		  [](const z1_layer *a, const z1_layer *b) {
			  if (a->z != b->z)
				  return a->z < b->z;
			  return a->id < b->id;
		  });

	req = drmModeAtomicAlloc();
	if (!req)
		return HWC2_ERROR_NO_RESOURCES;

	if (!h->modeset_done && h->connector_crtc_prop && h->crtc_mode_prop &&
	    h->crtc_active_prop) {
		uint32_t blob = 0;

		if (drmModeCreatePropertyBlob(h->drm_fd, &h->mode,
					      sizeof(h->mode), &blob) == 0) {
			h->mode_blob = blob;
			add_prop(req, "connector CRTC_ID", h->connector_id,
				 h->connector_crtc_prop, h->crtc_id);
			add_prop(req, "crtc MODE", h->crtc_id, h->crtc_mode_prop,
				 blob);
			add_prop(req, "crtc ACTIVE", h->crtc_id, h->crtc_active_prop,
				 1);
			flags |= DRM_MODE_ATOMIC_ALLOW_MODESET;
		}
	}

	if (client_fb && slot < h->plane_count) {
		hwc_frect_t crop;

		memset(&crop, 0, sizeof(crop));
		crop.right = (float)client_w;
		crop.bottom = (float)client_h;
		program_plane(h, req, slot, client_fb, 0, 0, (int)client_w,
			      (int)client_h, &crop, 1.0f,
			      HWC2_BLEND_MODE_NONE, false);
		slot++;
	}
	for (size_t i = 0; i < device_layers.size() &&
			   slot < h->plane_count; i++) {
		z1_layer *l = device_layers[i];

		if (!l->fb.fb_id || l->fb.handle != l->handle ||
		    (l->handle &&
		     l->fb.src_fd != ((struct z1_handle *)l->handle)->fd)) {
			if (l->fb.fb_id)
				fb_forget(h, &l->fb);
			if (!l->handle || fb_import(h, l->handle, &l->fb) != 0) {
				klogf(0, "z1hwc2: layer %" PRIu64
				      " lost its framebuffer, skipping\n",
				      (uint64_t)l->id);
				continue;
			}
		}
		program_plane(h, req, slot, l->fb.fb_id,
			      l->frame.left, l->frame.top,
			      l->frame.right - l->frame.left,
			      l->frame.bottom - l->frame.top,
			      l->have_crop ? &l->crop : nullptr, l->alpha,
			      l->blend,
			      l->fb.fourcc == DRM_FORMAT_ABGR8888 ||
				      l->fb.fourcc == DRM_FORMAT_ARGB8888);
		if (g_verbose >= 3)
			klogf(3, "z1hwc2: plane[%d] layer=%" PRIu64 " fb=%u "
			     "dst=%d,%d %dx%d src=%dx%d alpha=%.3f blend=%d\n",
			      slot, (uint64_t)l->id, l->fb.fb_id,
			      l->frame.left, l->frame.top,
			      l->frame.right - l->frame.left,
			      l->frame.bottom - l->frame.top,
			      (int)(l->crop.right - l->crop.left),
			      (int)(l->crop.bottom - l->crop.top),
			      l->alpha, l->blend);
		scanned.push_back(l);
		slot++;
	}
	for (int p = slot; p < h->plane_count; p++)
		program_plane(h, req, p, 0, 0, 0, 0, 0, nullptr, 1.0f, 0, false);

	ret = drmModeAtomicCommit(h->drm_fd, req, flags, h);
	if (ret) {
		int err = errno;

		h->commit_fail++;
		klogf(0, "z1hwc2: atomic commit failed: %s "
		     "(flags=%#x planes_used=%d device=%zu client_fb=%u "
		     "first=%d)\n", strerror(err), flags, slot,
		      device_layers.size(), client_fb, h->modeset_done ? 0 : 1);
		if (!h->modeset_done) {
			/* Retry as a plain plane commit: the CRTC may already
			 * be active from the fbdev. */
			drmModeAtomicFree(req);
			req = drmModeAtomicAlloc();
			if (req) {
				slot = 0;
				if (client_fb && slot < h->plane_count) {
					hwc_frect_t crop;

					memset(&crop, 0, sizeof(crop));
					crop.right = (float)client_w;
					crop.bottom = (float)client_h;
					program_plane(h, req, slot, client_fb,
						      0, 0, (int)client_w,
						      (int)client_h, &crop, 1.0f,
						      HWC2_BLEND_MODE_NONE, false);
					slot++;
				}
				for (size_t i = 0;
				     i < device_layers.size() &&
				     slot < h->plane_count;
				     i++) {
					z1_layer *l = device_layers[i];

					if (!l->fb.fb_id)
						continue;
					program_plane(h, req, slot, l->fb.fb_id,
						      l->frame.left,
						      l->frame.top,
						      l->frame.right - l->frame.left,
						      l->frame.bottom - l->frame.top,
						      l->have_crop ? &l->crop : nullptr,
						      l->alpha, l->blend,
						      l->fb.fourcc == DRM_FORMAT_ABGR8888 ||
							      l->fb.fourcc == DRM_FORMAT_ARGB8888);
					slot++;
				}
				for (int p = slot; p < h->plane_count; p++)
					program_plane(h, req, p, 0, 0, 0, 0, 0,
						      nullptr, 1.0f, 0, false);
				ret = drmModeAtomicCommit(h->drm_fd, req, 0, h);
				if (ret == 0) {
					klogf(1, "z1hwc2: plane-only commit "
					     "accepted (no modeset)\n");
					err = 0;
				}
			}
		}
		if (ret) {
			drmModeAtomicFree(req);
			h->last_device_layers = (uint32_t)device_layers.size();
			return HWC2_ERROR_NONE; /* keep SurfaceFlinger alive */
		}
	}
	drmModeAtomicFree(req);

	/*
	 * Release fences.  A device layer's buffer may only be written again
	 * once the display has stopped scanning it, which is when the *next*
	 * frame is committed.  sw_sync gives us a fence we can signal later:
	 * one frame per timeline point, signalled right after the following
	 * commit.  Property gated because it needs CONFIG_SW_SYNC plus a
	 * mounted debugfs.
	 *
	 * The present/retire fence is still -1: sf's DispSync treats -1 as
	 * "unreliable" and falls back to its software model, which is the state
	 * this panel has always run in.  (It also keeps the HAL from feeding
	 * zero timestamps from sw_sync fences, which would poison the model.)
	 */
	if (h->fences_on) {
		uint32_t next;

		pthread_mutex_lock(&h->fence_lock);
		if (h->fences_pending) {
			if (sw_timeline_inc(h->timeline_fd, 1) == 0)
				h->timeline_val++;
			h->fences_pending = false;
		}
		next = h->timeline_val + 1;
		for (size_t i = 0; i < scanned.size(); i++) {
			z1_layer *l = scanned[i];

			if (l->release_fence >= 0)
				close(l->release_fence);
			l->release_fence =
				sw_fence_create(h->timeline_fd, "z1rel", next);
		}
		h->fences_pending = true;
		pthread_mutex_unlock(&h->fence_lock);
	}
	h->last_present_ms = now_ms();

	if (!h->modeset_done) {
		h->modeset_done = true;
		if (flags & DRM_MODE_ATOMIC_ALLOW_MODESET)
			klogf(1, "z1hwc2: first commit done with modeset: "
			     "%ux%u\n", h->width, h->height);
		else
			klogf(1, "z1hwc2: first commit done (crtc already "
			     "active): %ux%u\n", h->width, h->height);
	}
	if (!h->first_commit_logged ||
	    h->frames % 600 == 0) {
		h->first_commit_logged = true;
		klogf(1, "z1hwc2: frame %" PRIu64 ": device=%zu client=%s "
		     "plane0=%s\n", h->frames, device_layers.size(), client_how,
		      client_fb ? "used" : "none");
	}
	h->last_device_layers = (uint32_t)device_layers.size();
	{
		uint32_t client_layers = 0;

		for (std::map<hwc2_layer_t, z1_layer>::iterator it =
			     h->layers.begin();
		     it != h->layers.end(); ++it)
			if (it->second.composition ==
			    HWC2_COMPOSITION_CLIENT)
				client_layers++;
		h->last_client_layers = client_layers;
	}
	return HWC2_ERROR_NONE;
}

static void z1_dump(hwc2_device_t *d, uint32_t *out_size, char *out_buffer)
{
	z1_hwc2 *h = H(d);
	int n = 0;

	if (out_buffer && out_size && *out_size > 0) {
		n = snprintf(out_buffer, *out_size,
			     "z1hwc2 %s: %ux%u crtc=%u conn=%u planes=%d "
			     "layers=%zu frames=%" PRIu64 " commit_fail=%" PRIu64
			     " device=%u client=%u modeset=%d\n",
			     Z1_HWC_VERSION, h->width, h->height, h->crtc_id,
			     h->connector_id, h->plane_count, h->layers.size(),
			     h->frames, h->commit_fail, h->last_device_layers,
			     h->last_client_layers, h->modeset_done ? 1 : 0);
		*out_size = (uint32_t)n;
	} else if (out_size) {
		*out_size = 256;
	}
}

/* ------------------------------------------------------------- getFunction --- */

static hwc2_function_pointer_t z1_get_function(hwc2_device_t *d,
					       int32_t descriptor)
{
	(void)d;
	switch (descriptor) {
	case HWC2_FUNCTION_ACCEPT_DISPLAY_CHANGES:
		return (hwc2_function_pointer_t)z1_accept_display_changes;
	case HWC2_FUNCTION_CREATE_LAYER:
		return (hwc2_function_pointer_t)z1_create_layer;
	case HWC2_FUNCTION_CREATE_VIRTUAL_DISPLAY:
		return (hwc2_function_pointer_t)z1_create_virtual_display;
	case HWC2_FUNCTION_DESTROY_LAYER:
		return (hwc2_function_pointer_t)z1_destroy_layer;
	case HWC2_FUNCTION_DESTROY_VIRTUAL_DISPLAY:
		return (hwc2_function_pointer_t)z1_destroy_virtual_display;
	case HWC2_FUNCTION_DUMP:
		return (hwc2_function_pointer_t)z1_dump;
	case HWC2_FUNCTION_GET_ACTIVE_CONFIG:
		return (hwc2_function_pointer_t)z1_get_active_config;
	case HWC2_FUNCTION_GET_CHANGED_COMPOSITION_TYPES:
		return (hwc2_function_pointer_t)z1_get_changed_composition_types;
	case HWC2_FUNCTION_GET_CLIENT_TARGET_SUPPORT:
		return (hwc2_function_pointer_t)z1_get_client_target_support;
	case HWC2_FUNCTION_GET_COLOR_MODES:
		return (hwc2_function_pointer_t)z1_get_color_modes;
	case HWC2_FUNCTION_GET_DISPLAY_ATTRIBUTE:
		return (hwc2_function_pointer_t)z1_get_display_attribute;
	case HWC2_FUNCTION_GET_DISPLAY_CONFIGS:
		return (hwc2_function_pointer_t)z1_get_display_configs;
	case HWC2_FUNCTION_GET_DISPLAY_NAME:
		return (hwc2_function_pointer_t)z1_get_display_name;
	case HWC2_FUNCTION_GET_DISPLAY_REQUESTS:
		return (hwc2_function_pointer_t)z1_get_display_requests;
	case HWC2_FUNCTION_GET_DISPLAY_TYPE:
		return (hwc2_function_pointer_t)z1_get_display_type;
	case HWC2_FUNCTION_GET_DOZE_SUPPORT:
		return (hwc2_function_pointer_t)z1_get_doze_support;
	case HWC2_FUNCTION_GET_HDR_CAPABILITIES:
		return (hwc2_function_pointer_t)z1_get_hdr_capabilities;
	case HWC2_FUNCTION_GET_MAX_VIRTUAL_DISPLAY_COUNT:
		return (hwc2_function_pointer_t)z1_get_max_virtual_display_count;
	case HWC2_FUNCTION_GET_RELEASE_FENCES:
		return (hwc2_function_pointer_t)z1_get_release_fences;
	case HWC2_FUNCTION_PRESENT_DISPLAY:
		return (hwc2_function_pointer_t)z1_present_display;
	case HWC2_FUNCTION_REGISTER_CALLBACK:
		return (hwc2_function_pointer_t)z1_register_callback;
	case HWC2_FUNCTION_SET_ACTIVE_CONFIG:
		return (hwc2_function_pointer_t)z1_set_active_config;
	case HWC2_FUNCTION_SET_CLIENT_TARGET:
		return (hwc2_function_pointer_t)z1_set_client_target;
	case HWC2_FUNCTION_SET_COLOR_MODE:
		return (hwc2_function_pointer_t)z1_set_color_mode;
	case HWC2_FUNCTION_SET_COLOR_TRANSFORM:
		return (hwc2_function_pointer_t)z1_set_color_transform;
	case HWC2_FUNCTION_SET_CURSOR_POSITION:
		return (hwc2_function_pointer_t)z1_set_cursor_position;
	case HWC2_FUNCTION_SET_LAYER_BLEND_MODE:
		return (hwc2_function_pointer_t)z1_set_layer_blend_mode;
	case HWC2_FUNCTION_SET_LAYER_BUFFER:
		return (hwc2_function_pointer_t)z1_set_layer_buffer;
	case HWC2_FUNCTION_SET_LAYER_COLOR:
		return (hwc2_function_pointer_t)z1_set_layer_color;
	case HWC2_FUNCTION_SET_LAYER_COMPOSITION_TYPE:
		return (hwc2_function_pointer_t)z1_set_layer_composition_type;
	case HWC2_FUNCTION_SET_LAYER_DATASPACE:
		return (hwc2_function_pointer_t)z1_set_layer_dataspace;
	case HWC2_FUNCTION_SET_LAYER_DISPLAY_FRAME:
		return (hwc2_function_pointer_t)z1_set_layer_display_frame;
	case HWC2_FUNCTION_SET_LAYER_PLANE_ALPHA:
		return (hwc2_function_pointer_t)z1_set_layer_plane_alpha;
	case HWC2_FUNCTION_SET_LAYER_SIDEBAND_STREAM:
		return (hwc2_function_pointer_t)z1_set_layer_sidestream;
	case HWC2_FUNCTION_SET_LAYER_SOURCE_CROP:
		return (hwc2_function_pointer_t)z1_set_layer_source_crop;
	case HWC2_FUNCTION_SET_LAYER_SURFACE_DAMAGE:
		return (hwc2_function_pointer_t)z1_set_layer_surface_damage;
	case HWC2_FUNCTION_SET_LAYER_TRANSFORM:
		return (hwc2_function_pointer_t)z1_set_layer_transform;
	case HWC2_FUNCTION_SET_LAYER_VISIBLE_REGION:
		return (hwc2_function_pointer_t)z1_set_layer_visible_region;
	case HWC2_FUNCTION_SET_LAYER_Z_ORDER:
		return (hwc2_function_pointer_t)z1_set_layer_z;
	case HWC2_FUNCTION_SET_OUTPUT_BUFFER:
		return (hwc2_function_pointer_t)z1_set_output_buffer;
	case HWC2_FUNCTION_SET_POWER_MODE:
		return (hwc2_function_pointer_t)z1_set_power_mode;
	case HWC2_FUNCTION_SET_VSYNC_ENABLED:
		return (hwc2_function_pointer_t)z1_set_vsync_enabled;
	case HWC2_FUNCTION_VALIDATE_DISPLAY:
		return (hwc2_function_pointer_t)z1_validate_display;
	default:
		/* HWC2 2.2+ descriptors (float colour, per-frame metadata,
		 * readback, render intents).  The device's composer is 2.1 and
		 * never asks for them. */
		if (descriptor >= HWC2_FUNCTION_SET_LAYER_FLOAT_COLOR)
			return nullptr;
		klogf(0, "z1hwc2: getFunction(%d): not implemented\n",
		      descriptor);
		return nullptr;
	}
}

/* ----------------------------------------------------------------- module --- */

static int z1_close(hw_device_t *dev)
{
	z1_hwc2 *h = reinterpret_cast<z1_hwc2 *>(dev);

	klogf(1, "z1hwc2: close (frames=%" PRIu64 " commit_fail=%" PRIu64 ")\n",
	      h->frames, h->commit_fail);
	h->wd_stop = 1;
	if (h->wd_started) {
		pthread_join(h->wd_thread, nullptr);
		h->wd_started = false;
	}
	if (h->fences_on) {
		pthread_mutex_lock(&h->fence_lock);
		if (h->fences_pending) {
			(void)sw_timeline_inc(h->timeline_fd, 1);
			h->fences_pending = false;
		}
		pthread_mutex_unlock(&h->fence_lock);
	}
	for (std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.begin();
	     it != h->layers.end(); ++it) {
		if (it->second.fb.fb_id)
			fb_forget(h, &it->second.fb);
		if (it->second.acquire_fence >= 0)
			close(it->second.acquire_fence);
	}
	if (h->client_fb.fb_id)
		fb_forget(h, &h->client_fb);
	if (h->client_acquire_fence >= 0)
		close(h->client_acquire_fence);
	for (size_t i = 0; i < h->pending_release.size(); i++) {
		if (h->pending_release[i].first)
			drmModeRmFB(h->drm_fd, h->pending_release[i].first);
		if (h->pending_release[i].second)
			drmCloseBufferHandle(h->drm_fd,
					     h->pending_release[i].second);
	}
	for (int i = 0; i < 2; i++) {
		if (h->shadow[i].map && h->shadow[i].map != MAP_FAILED)
			munmap(h->shadow[i].map, h->shadow[i].size);
		if (h->shadow[i].fb_id)
			drmModeRmFB(h->drm_fd, h->shadow[i].fb_id);
		if (h->shadow[i].handle) {
			struct drm_mode_destroy_dumb destroy;

			memset(&destroy, 0, sizeof(destroy));
			destroy.handle = h->shadow[i].handle;
			ioctl(h->drm_fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
		}
	}
	for (std::map<hwc2_layer_t, z1_layer>::iterator it = h->layers.begin();
	     it != h->layers.end(); ++it)
		if (it->second.release_fence >= 0)
			close(it->second.release_fence);
	if (h->timeline_fd >= 0)
		close(h->timeline_fd);
	if (h->mode_blob)
		drmModeDestroyPropertyBlob(h->drm_fd, h->mode_blob);
	if (h->drm_fd >= 0)
		close(h->drm_fd);
	delete h;
	return 0;
}

static int z1_open(const hw_module_t *module, const char *name,
		   hw_device_t **device)
{
	z1_hwc2 *h;

	klogf(1, "z1hwc2: open(%s) version %s\n", name ? name : "(null)",
	      Z1_HWC_VERSION);

	if (!name || strcmp(name, HWC_HARDWARE_COMPOSER)) {
		klogf(0, "z1hwc2: invalid module name %s\n",
		      name ? name : "(null)");
		return -EINVAL;
	}

	h = new z1_hwc2();
	if (!h)
		return -ENOMEM;
	h->drm_fd = -1;
	h->client_expected = true;
	h->timeline_fd = -1;
	h->client_acquire_fence = -1;
	h->client_copy_slot = 0;
	h->next_layer_id = 1;
	h->powered_on = true;
	h->mode_force_client = 0;

	h->common.tag = HARDWARE_DEVICE_TAG;
	h->common.version = HWC_DEVICE_API_VERSION_2_0;
	h->common.module = const_cast<hw_module_t *>(module);
	h->common.close = z1_close;
	h->getCapabilities = z1_get_capabilities;
	h->getFunction = z1_get_function;

	g_verbose = prop_int("debug.z1.hwc.verbose", 1);

	if (drm_open(h) < 0) {
		if (h->drm_fd >= 0)
			close(h->drm_fd);
		delete h;
		return -ENODEV;
	}

	fences_init(h);

	*device = &h->common;
	return 0;
}

static struct hw_module_methods_t z1_module_methods = {
	.open = z1_open,
};

hw_module_t HAL_MODULE_INFO_SYM = {
	.tag = HARDWARE_MODULE_TAG,
	.module_api_version = HARDWARE_MODULE_API_VERSION(2, 1),
	.hal_api_version = 0,
	.id = HWC_HARDWARE_MODULE_ID,
	.name = "z1 HWC2 (mediatek-drm)",
	.author = "z1",
	.methods = &z1_module_methods,
	.dso = nullptr,
	.reserved = {0},
};
