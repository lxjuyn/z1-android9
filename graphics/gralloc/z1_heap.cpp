/*
 * z1_heap.cpp — physically contiguous dma-buf allocation for the Z1 gralloc HAL
 * (Android 9 / LineageOS 16, armv7, MT6572, mainline Linux 7.0-rc7).
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The display controller only accepts physically contiguous framebuffers —
 * fork_linux/drivers/gpu/drm/mediatek/mtk_gem.c:
 *
 *     mtk_gem_prime_import_sg_table(struct drm_device *dev,
 *                                   struct dma_buf_attachment *attach,
 *                                   struct sg_table *sgt)
 *     {
 *             if (drm_prime_get_contiguous_size(sgt) < attach->dmabuf->size) {
 *                     DRM_ERROR("sg_table is not contiguous");
 *                     return ERR_PTR(-EINVAL);
 *             }
 *             ...
 *
 * The old gralloc allocated every buffer from lima
 * (DRM_IOCTL_LIMA_GEM_CREATE on /dev/dri/renderD128).  lima's GEM is
 * drm_gem_shmem (fork_linux/drivers/gpu/drm/lima/lima_gem.c), i.e. a list of
 * unrelated pages, so OVL composition could never import those buffers.
 *
 * This file hands out dma-bufs that ARE physically contiguous and keeps the
 * exact same buffer size/stride contract, so Mesa/lima can still render into
 * them: lima declares .gem_prime_import_sg_table =
 * drm_gem_shmem_prime_import_sg_table (lima_drv.c), which accepts the sg_table
 * of any dma-buf.
 *
 * BACKEND CHAIN (a step is only tried when the previous one is unavailable)
 * ------------------------------------------------------------------------
 *  1. CMA dma-buf heap: /dev/dma_heap/default_cma_region
 *       DMA_HEAP_IOCTL_ALLOC.  cma_heap.c backs it with a single cma_alloc()
 *       block and builds the attachment sg_table with
 *       sg_alloc_table_from_pages(), which collapses to one entry, so
 *       drm_prime_get_contiguous_size() == dmabuf->size.  CONTINUOUS.
 *  2. mediatek-drm dumb buffer: DRM_IOCTL_MODE_CREATE_DUMB on /dev/dri/card1
 *       then DRM_IOCTL_PRIME_HANDLE_TO_FD + DRM_IOCTL_GEM_CLOSE.
 *       mtk_gem_dumb_create() -> mtk_gem_create() -> dma_alloc_wc():
 *       CONTIGUOUS by construction.  Late fallback only: card1 is a component
 *       master and appears ~33 s after boot (OVL/RDMA/DSI probe order).
 *  3. lima GEM (lima_allocator.cpp): NOT contiguous.  Kept only so the display
 *       keeps a working buffer during the first seconds of boot (and so a board
 *       without CMA/drm nodes behaves exactly like before).
 *  4. gralloc.cpp keeps its ashmem fallback for the case where all of the above
 *       fail (no /dev/dri at all).
 *
 * The 64-byte private_handle_t ABI and the 16-pixel/16-row buffer layout
 * (pixel_copy.h) are not touched here.
 */

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#include <log/log.h>

#include <drm/drm.h>
#include <drm/drm_mode.h>

#include "z1_heap.h"

#ifdef Z1_USE_LIMA
#include "lima_allocator.h"
#endif

/*
 * DMA-BUF heaps UAPI.  Prefer the toolchain header; the local copy below is a
 * verbatim transcription of fork_linux/include/uapi/linux/dma-heap.h
 * (CONFIG_DMABUF_HEAPS=y) for toolchains that do not ship it.
 */
#if defined(__has_include)
#if __has_include(<linux/dma-heap.h>)
#include <linux/dma-heap.h>
#define Z1_HAVE_UAPI_DMA_HEAP_H 1
#endif
#endif

#ifndef Z1_HAVE_UAPI_DMA_HEAP_H
#define DMA_HEAP_IOC_MAGIC 'H'
struct dma_heap_allocation_data {
    uint64_t len;
    uint32_t fd;
    uint32_t fd_flags;
    uint64_t heap_flags;
};
#define DMA_HEAP_IOCTL_ALLOC \
    _IOWR(DMA_HEAP_IOC_MAGIC, 0x0, struct dma_heap_allocation_data)
#endif

/*
 * Compile-time cross-check against the running kernel's UAPI.  All of these
 * were computed from fork_linux/include/uapi/{linux/dma-heap.h,drm/drm.h} with
 * the same armv7 EABI the HAL is built for:
 *
 *   DMA_HEAP_IOCTL_ALLOC         _IOWR('H', 0x00, 24 bytes) = 0xC0184800
 *   DRM_IOCTL_MODE_CREATE_DUMB   _IOWR('d', 0xB2, 32 bytes) = 0xC02064B2
 *   DRM_IOCTL_PRIME_HANDLE_TO_FD _IOWR('d', 0x2D, 12 bytes) = 0xC00C642D
 *   DRM_IOCTL_GEM_CLOSE          _IOW ('d', 0x09,  8 bytes) = 0x40086409
 *
 * If any of these fire, the struct layout/ABI drifted and every allocation
 * would silently fail with -EINVAL on the board.
 */
static_assert(sizeof(struct dma_heap_allocation_data) == 24,
              "dma_heap_allocation_data must be 24 bytes on arm EABI");
static_assert(offsetof(struct dma_heap_allocation_data, len) == 0,
              "dma_heap_allocation_data.len offset");
static_assert(offsetof(struct dma_heap_allocation_data, fd) == 8,
              "dma_heap_allocation_data.fd offset");
static_assert(offsetof(struct dma_heap_allocation_data, fd_flags) == 12,
              "dma_heap_allocation_data.fd_flags offset");
static_assert(offsetof(struct dma_heap_allocation_data, heap_flags) == 16,
              "dma_heap_allocation_data.heap_flags offset");
static_assert(DMA_HEAP_IOCTL_ALLOC == 0xC0184800u,
              "DMA_HEAP_IOCTL_ALLOC must match fork_linux uapi/linux/dma-heap.h");
static_assert(sizeof(struct drm_mode_create_dumb) == 32,
              "drm_mode_create_dumb must be 32 bytes on arm EABI");
static_assert(DRM_IOCTL_MODE_CREATE_DUMB == 0xC02064B2u,
              "DRM_IOCTL_MODE_CREATE_DUMB must match fork_linux uapi/drm/drm.h");
static_assert(sizeof(struct drm_prime_handle) == 12,
              "drm_prime_handle must be 12 bytes on arm EABI");
static_assert(DRM_IOCTL_PRIME_HANDLE_TO_FD == 0xC00C642Du,
              "DRM_IOCTL_PRIME_HANDLE_TO_FD must match fork_linux uapi/drm/drm.h");
static_assert(sizeof(struct drm_gem_close) == 8,
              "drm_gem_close must be 8 bytes on arm EABI");
static_assert(DRM_IOCTL_GEM_CLOSE == 0x40086409u,
              "DRM_IOCTL_GEM_CLOSE must match fork_linux uapi/drm/drm.h");

/* ------------------------------------------------------------------ logging --- */

#ifndef LOG_TAG
#define LOG_TAG "z1_heap"
#endif

enum { Z1_LOG_INFO = ANDROID_LOG_INFO, Z1_LOG_WARN = ANDROID_LOG_WARN };

/*
 * z1a9_gpt/scripts/hal_stub/log/log.h (used by z1_build_hal_stubs.sh) compiles
 * every ALOG* macro to ((void)0), so a plain ALOGI() here would be invisible in
 * logcat on the board.  __android_log_print() is the stable liblog entry point
 * (present in the device's liblog.so and declared by both the shim and AOSP),
 * so call it through this thin wrapper and the backend selection is really
 * observable with `logcat -s gralloc`.
 */
static void z1_log(int prio, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));

static void z1_log(int prio, const char *fmt, ...)
{
    char buf[320];
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    __android_log_print(prio, LOG_TAG, "%s", buf);
}

/* --------------------------------------------------------------- global state --- */

enum Backend {
    kBackendNone = 0,
    kBackendDmaHeap,
    kBackendDrmDumb,
    kBackendLima,
    kBackendCount
};

static const char *const kBackendName[kBackendCount] = {
    "none", "dma-heap", "drm-dumb", "lima"
};

/* How long to wait before re-probing a missing node (/dev/dma_heap appears via
 * ueventd, /dev/dri/card1 only ~33 s after boot). */
static const uint64_t kRetryIntervalMs = 2000;

/* One-time (per process) diagnostic messages. */
enum {
    WARN_HEAP_DEFAULT_OPEN = 1u << 0,
    WARN_HEAP_DIR_OPEN = 1u << 1,
    WARN_HEAP_NONCMA = 1u << 2,
    WARN_HEAP_OPEN_SCANNED = 1u << 3,
    WARN_HEAP_SCAN_OPEN = 1u << 10,
    WARN_HEAP_UNVERIFIED = 1u << 11,
    WARN_DUMB_FAIL = 1u << 12,
    WARN_CARD_OPEN = 1u << 4,
    WARN_CARD_NO_DUMB = 1u << 5,
    WARN_LIMA_FAIL = 1u << 6,
    WARN_ALL_FAILED = 1u << 7,
    WARN_FORCE = 1u << 8,
    WARN_DUMB_CLOSE = 1u << 9
};

static pthread_mutex_t g_state_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_log_lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned int g_warned = 0;

/* CMA dma-heap node cache. */
static int g_heap_fd = -1;
static uint64_t g_heap_retry_at = 0;
static int g_heap_last_err = -ENOENT;
static char g_heap_name[40] = "";
static int g_heap_contig = 0;

/* mediatek-drm card cache. */
static int g_card_fd = -1;
static int g_card_idx = -1;
static unsigned int g_card_bad = 0; /* bit i: kCardPaths[i] has no dumb_create */
static uint64_t g_card_retry_at = 0;
static int g_card_last_err = -ENOENT;

/* Statistics / selection state. */
static uint64_t g_total_bytes = 0;
static int g_last_backend = kBackendNone;
static int g_selection_logged = 0;
static int g_forced = -2; /* -2 = not parsed yet, -1 = auto */

static const char *const kCardPaths[] = { "/dev/dri/card1", "/dev/dri/card0" };
static const char *const kDefaultHeapPath = "/dev/dma_heap/default_cma_region";
static const char *const kHeapDir = "/dev/dma_heap";

static uint64_t z1_now_ms()
{
    struct timespec ts;

    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return 0;
    return uint64_t(ts.tv_sec) * 1000 + uint64_t(ts.tv_nsec) / 1000000;
}

static size_t z1_page_size()
{
    long page = sysconf(_SC_PAGESIZE);

    return page > 0 ? size_t(page) : size_t(4096);
}

/* gralloc.cpp already rounds to PAGE_SIZE; be defensive for other callers. */
static size_t z1_page_align(size_t n)
{
    const size_t page = z1_page_size();

    return (n + page - 1) & ~(page - 1);
}

/* Serialised one-time warning.  Takes g_log_lock (leaf lock, safe to call with
 * g_state_lock held). */
static void z1_log_once(unsigned int bit, int prio, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));

static void z1_log_once(unsigned int bit, int prio, const char *fmt, ...)
{
    char buf[320];
    va_list ap;

    pthread_mutex_lock(&g_log_lock);
    if (g_warned & bit) {
        pthread_mutex_unlock(&g_log_lock);
        return;
    }
    g_warned |= bit;
    pthread_mutex_unlock(&g_log_lock);

    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    z1_log(prio, "%s", buf);
}

/* --------------------------------------------------------------- small helpers --- */

static int z1_ioctl(int fd, unsigned long request, void *arg)
{
    int ret;

    do {
        ret = ioctl(fd, request, arg);
    } while (ret < 0 && errno == EINTR);
    return ret < 0 ? -errno : 0;
}

static int z1_name_copy(char *dst, size_t dst_len, const char *src)
{
    const size_t n = strlen(src);

    /* Refuse rather than truncate: a cut-off heap/node name would silently
     * select the wrong backend.  memcpy keeps this warning free on GCC too
     * (-Wformat-truncation flags any bounded "%s" copy). */
    if (!dst_len || n >= dst_len)
        return -ENAMETOOLONG;
    memcpy(dst, src, n + 1);
    return 0;
}

/* Case-insensitive "cma" substring search (avoids strcasestr/_GNU_SOURCE). */
static int z1_name_has_cma(const char *name)
{
    const char *p;

    for (p = name; *p; ++p) {
        if ((p[0] == 'c' || p[0] == 'C') &&
            (p[1] == 'm' || p[1] == 'M') &&
            (p[2] == 'a' || p[2] == 'A'))
            return 1;
        if (!p[1])
            break;
    }
    return 0;
}

/* --------------------------------------------------------------- dma-buf heap --- */

/*
 * Open the CMA heap node once and cache the fd.
 *
 * The direct hit is /dev/dma_heap/default_cma_region (DEFAULT_CMA_NAME in
 * fork_linux/drivers/dma-buf/heaps/cma_heap.c).  If that name is missing we scan
 * /dev/dma_heap/ and prefer names containing "cma".  On this kernel the default
 * DT pool (mt6572-z1.dts "default-pool", linux,cma-default) is registered twice
 * by kernel/dma/contiguous.c (once as "default_cma_region", once under the DT
 * node name "default-pool"), so a second CMA node without "cma" in its name is
 * expected; anything that is not the known non-contiguous "system" heap is
 * treated as a CMA heap.  /dev/dma_heap/system is deliberately NOT used: it is
 * a page-array heap (system_heap.c), i.e. exactly as non-contiguous as lima, so
 * using it during the ~33 s before card1 exists would be no better than the
 * proven lima path and would silently defeat this file's purpose.
 *
 * Returns the fd or a negative errno; *contig_out is set for a CMA node.
 */
static int dma_heap_open_locked(int *contig_out, char *name_out, size_t name_len)
{
    int fd;
    int err;
    DIR *dir = NULL;
    struct dirent *de;
    char cma_name[40] = "";
    char other_name[40] = "";
    char non_cma_name[40] = "";
    const char *chosen;
    char path[96];

    *contig_out = 0;

    fd = open(kDefaultHeapPath, O_RDWR | O_CLOEXEC);
    if (fd >= 0) {
        if (z1_name_copy(name_out, name_len, "default_cma_region") != 0) {
            close(fd);
            return -ENAMETOOLONG;
        }
        *contig_out = 1;
        z1_log_once(WARN_HEAP_OPEN_SCANNED, Z1_LOG_INFO,
                    "dma-heap: using %s (fd %d)", kDefaultHeapPath, fd);
        return fd;
    }
    err = errno;
    z1_log_once(WARN_HEAP_DEFAULT_OPEN, Z1_LOG_WARN,
                "dma-heap: cannot open %s (%s); scanning %s",
                kDefaultHeapPath, strerror(err), kHeapDir);

    dir = opendir(kHeapDir);
    if (!dir) {
        err = errno;
        z1_log_once(WARN_HEAP_DIR_OPEN, Z1_LOG_WARN,
                    "dma-heap: cannot scan %s (%s)", kHeapDir, strerror(err));
        return -ENOENT;
    }

    while ((de = readdir(dir)) != NULL) {
        const char *n = de->d_name;

        if (n[0] == '.')
            continue;
        if (z1_name_has_cma(n)) {
            if (cma_name[0] == 0 || strcmp(n, "default_cma_region") == 0) {
                if (z1_name_copy(cma_name, sizeof(cma_name), n) != 0)
                    cma_name[0] = 0;
            }
        } else if (strncmp(n, "system", strlen("system")) != 0) {
            if (other_name[0] == 0)
                (void)z1_name_copy(other_name, sizeof(other_name), n);
        } else if (non_cma_name[0] == 0) {
            (void)z1_name_copy(non_cma_name, sizeof(non_cma_name), n);
        }
    }
    closedir(dir);

    chosen = cma_name[0] ? cma_name : (other_name[0] ? other_name : NULL);
    if (!chosen) {
        if (non_cma_name[0])
            z1_log_once(WARN_HEAP_NONCMA, Z1_LOG_WARN,
                        "dma-heap: only non-CMA heaps in %s (%s); not contiguous, ignoring",
                        kHeapDir, non_cma_name);
        return -ENOENT;
    }

    {
        int n = snprintf(path, sizeof(path), "%s/%s", kHeapDir, chosen);

        if (n <= 0 || size_t(n) >= sizeof(path))
            return -ENAMETOOLONG;
    }
    fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        err = errno;
        z1_log_once(WARN_HEAP_SCAN_OPEN, Z1_LOG_WARN,
                    "dma-heap: cannot open %s (%s)", path, strerror(err));
        return -err;
    }
    if (z1_name_copy(name_out, name_len, chosen) != 0) {
        close(fd);
        return -ENAMETOOLONG;
    }
    /*
     * Only a "cma"-named node is reported as contiguous.  A non-system node
     * without "cma" in its name is still used (on this kernel the DT default
     * pool is registered a second time under its DT node name) but its
     * contiguity is not claimed: the HWC must not be told a lie just because a
     * heap filename looked plausible.
     */
    *contig_out = z1_name_has_cma(chosen) ? 1 : 0;
    if (*contig_out)
        z1_log_once(WARN_HEAP_OPEN_SCANNED, Z1_LOG_INFO,
                    "dma-heap: using %s (fd %d)", path, fd);
    else
        z1_log_once(WARN_HEAP_UNVERIFIED, Z1_LOG_WARN,
                    "dma-heap: using %s (fd %d); name has no \"cma\", "
                    "contiguity unverified",
                    path, fd);
    return fd;
}

/* Cached heap fd (>= 0) or negative errno.  Retries every 2 s while missing. */
static int dma_heap_fd(int *contig_out)
{
    int fd;

    pthread_mutex_lock(&g_state_lock);
    if (g_heap_fd >= 0) {
        fd = g_heap_fd;
        *contig_out = g_heap_contig;
        pthread_mutex_unlock(&g_state_lock);
        return fd;
    }
    if (g_heap_retry_at && z1_now_ms() < g_heap_retry_at) {
        /* Still backing off: replay the original cause so callers/logs do not
         * see a bare -EAGAIN. */
        fd = g_heap_last_err;
        pthread_mutex_unlock(&g_state_lock);
        return fd;
    }
    fd = dma_heap_open_locked(&g_heap_contig, g_heap_name, sizeof(g_heap_name));
    if (fd < 0) {
        g_heap_last_err = fd;
        g_heap_retry_at = z1_now_ms() + kRetryIntervalMs;
        pthread_mutex_unlock(&g_state_lock);
        return fd;
    }
    g_heap_fd = fd;
    *contig_out = g_heap_contig;
    pthread_mutex_unlock(&g_state_lock);
    return fd;
}

static void dma_heap_forget(int fd)
{
    pthread_mutex_lock(&g_state_lock);
    if (g_heap_fd >= 0 && g_heap_fd == fd) {
        close(g_heap_fd);
        g_heap_fd = -1;
        g_heap_contig = 0;
        g_heap_name[0] = 0;
        g_heap_retry_at = 0;
    }
    pthread_mutex_unlock(&g_state_lock);
}

/*
 * Allocate `len` bytes from the cached heap fd.
 *
 * Kernel constraints, verified in fork_linux:
 *   dma_heap_ioctl_allocate() (drivers/dma-buf/dma-heap.c)
 *     - `fd` must be 0 on input (it is also the output field),
 *     - `fd_flags & ~(O_CLOEXEC | O_ACCMODE)` must be 0, so O_RDWR|O_CLOEXEC
 *       is accepted,
 *     - `heap_flags & ~0` must be 0,
 *     - len is PAGE_ALIGN()ed by dma_heap_buffer_alloc() and 0 is rejected.
 *   cma_heap_allocate() (drivers/dma-buf/heaps/cma_heap.c)
 *     - size = PAGE_ALIGN(len); pagecount = size >> PAGE_SHIFT,
 *     - aligns to min(get_order(size), CONFIG_CMA_ALIGNMENT=8) and calls
 *       cma_alloc(), so len does NOT have to be page aligned from userspace,
 *     - zeroes the buffer and exports it with exp_name = heap name.
 */
static int dma_heap_alloc_from(int heap_fd, size_t len)
{
    struct dma_heap_allocation_data data;
    int ret;

    memset(&data, 0, sizeof(data));
    data.len = uint64_t(len);
    data.fd = 0;
    data.fd_flags = uint32_t(O_RDWR | O_CLOEXEC);
    data.heap_flags = 0;

    ret = z1_ioctl(heap_fd, (unsigned long)DMA_HEAP_IOCTL_ALLOC, &data);
    if (ret < 0)
        return ret;
    if (data.fd == 0 || int32_t(data.fd) < 0) {
        /* The kernel always installs a real fd; fd 0 would mean the caller
         * closed stdin, which we cannot hand to gralloc (validate() would
         * accept it, but the handle's fd==0 is indistinguishable from "unset"
         * in several Android call sites). */
        if (data.fd != 0)
            close(int32_t(data.fd));
        return -EIO;
    }
    return int32_t(data.fd);
}

/* ----------------------------------------------------------------- DRM dumb --- */

/* Create one 32bpp dumb buffer of >= len bytes on `card_fd`, export it as a
 * dma-buf and drop the GEM handle.  Returns the dma-buf fd or negative errno. */
static int drm_dumb_create_and_export(int card_fd, const char *card_path,
                                      size_t len)
{
    struct drm_mode_create_dumb dumb;
    struct drm_prime_handle prime;
    struct drm_gem_close close_arg;
    uint64_t width;
    int ret;
    int err;

    /*
     * The buffer is only a memory carrier: gralloc tracks the real scanout
     * stride itself (private_handle_t::stride, pixels), so a 1-row 32bpp dumb
     * buffer of ceil(len/4) pixels is fine.
     *
     *   drm_mode_create_dumb(): rejects width/height/bpp == 0 and u32 overflow,
     *     then calls mtk_gem_dumb_create().
     *   mtk_gem_dumb_create(): pitch = DIV_ROUND_UP(width * bpp, 8);
     *     size = pitch * height; mtk_gem_create() rounds it up to PAGE_SIZE,
     *     so the dma-buf is >= len bytes (verified again below).
     */
    width = (uint64_t(len) + 3) / 4;
    if (width == 0 || width * 32 > 0xFFFFFFFFull)
        return -EINVAL;

    memset(&dumb, 0, sizeof(dumb));
    dumb.width = uint32_t(width);
    dumb.height = 1;
    dumb.bpp = 32;

    ret = z1_ioctl(card_fd, (unsigned long)DRM_IOCTL_MODE_CREATE_DUMB, &dumb);
    if (ret < 0)
        return ret;
    if (dumb.handle == 0)
        return -EIO;
    if (dumb.size < uint64_t(len)) {
        memset(&close_arg, 0, sizeof(close_arg));
        close_arg.handle = dumb.handle;
        (void)z1_ioctl(card_fd, (unsigned long)DRM_IOCTL_GEM_CLOSE, &close_arg);
        return -ENOSPC;
    }

    memset(&prime, 0, sizeof(prime));
    prime.handle = dumb.handle;
    prime.flags = DRM_CLOEXEC | DRM_RDWR;
    prime.fd = -1;
    ret = z1_ioctl(card_fd, (unsigned long)DRM_IOCTL_PRIME_HANDLE_TO_FD, &prime);
    err = ret < 0 ? ret : 0;

    /* The dma-buf keeps the GEM object alive; the handle itself is not needed. */
    memset(&close_arg, 0, sizeof(close_arg));
    close_arg.handle = dumb.handle;
    if (z1_ioctl(card_fd, (unsigned long)DRM_IOCTL_GEM_CLOSE, &close_arg) < 0) {
        z1_log_once(WARN_DUMB_CLOSE, Z1_LOG_WARN,
                    "drm-dumb: DRM_IOCTL_GEM_CLOSE(handle=%u) failed; the handle "
                    "stays alive until %s is closed",
                    dumb.handle, card_path);
    }

    if (err < 0)
        return err;
    if (prime.fd < 0)
        return -EIO;
    return prime.fd;
}

/* Path of the cached display node (static string; safe without the lock). */
static const char *drm_card_path()
{
    const char *path;

    pthread_mutex_lock(&g_state_lock);
    path = kCardPaths[(g_card_idx >= 0 && g_card_idx <
                       int(sizeof(kCardPaths) / sizeof(kCardPaths[0])))
                          ? g_card_idx
                          : 0];
    pthread_mutex_unlock(&g_state_lock);
    return path;
}

/* Cached display DRM fd (>= 0) or negative errno.  Tries card1, then card0. */
static int drm_card_fd()
{
    int fd;
    int err = -ENOENT;
    size_t i;

    pthread_mutex_lock(&g_state_lock);
    if (g_card_fd >= 0) {
        fd = g_card_fd;
        pthread_mutex_unlock(&g_state_lock);
        return fd;
    }
    if (g_card_retry_at && z1_now_ms() < g_card_retry_at) {
        fd = g_card_last_err;
        pthread_mutex_unlock(&g_state_lock);
        return fd;
    }
    for (i = 0; i < sizeof(kCardPaths) / sizeof(kCardPaths[0]); ++i) {
        if (g_card_bad & (1u << i))
            continue;
        fd = open(kCardPaths[i], O_RDWR | O_CLOEXEC);
        if (fd < 0) {
            err = -errno;
            continue;
        }
        /*
         * Never keep DRM master on this cached fd.  The kernel hands master
         * to the FIRST opener of a DRM node (drm_auth.c: drm_master_open()),
         * and DRM_IOCTL_MODE_ATOMIC is master-gated, so the HWC2 composer --
         * which runs with only CAP_SYS_NICE and therefore cannot take master
         * back -- would fail every commit with -EACCES.  We only need
         * GEM/dumb ioctls here, which do not require master.  If another
         * opener owns master already, the ioctl just returns EINVAL.
         */
        (void)ioctl(fd, DRM_IOCTL_DROP_MASTER);
        g_card_fd = fd;
        g_card_idx = int(i);
        pthread_mutex_unlock(&g_state_lock);
        z1_log_once(WARN_CARD_OPEN, Z1_LOG_INFO,
                    "drm-dumb: opened %s (fd %d, master dropped)",
                    kCardPaths[i], fd);
        return fd;
    }
    g_card_last_err = err;
    g_card_retry_at = z1_now_ms() + kRetryIntervalMs;
    pthread_mutex_unlock(&g_state_lock);
    return err;
}

/*
 * Drop the cached card fd.  `mark_bad` is set when the node answered -ENOSYS,
 * i.e. it has no dumb_create at all (lima's card0), so it is never tried again.
 */
static void drm_card_forget(int fd, int mark_bad)
{
    pthread_mutex_lock(&g_state_lock);
    if (g_card_fd >= 0 && g_card_fd == fd) {
        if (mark_bad && g_card_idx >= 0)
            g_card_bad |= 1u << g_card_idx;
        close(g_card_fd);
        g_card_fd = -1;
        g_card_idx = -1;
        g_card_retry_at = 0;
    }
    pthread_mutex_unlock(&g_state_lock);
}

static int drm_dumb_alloc(size_t len, int *contig_out)
{
    int attempt;

    *contig_out = 0;
    for (attempt = 0; attempt < 2; ++attempt) {
        int card = drm_card_fd();
        int fd;
        int err;
        const char *card_path;

        if (card < 0)
            return card;
        card_path = drm_card_path();
        fd = drm_dumb_create_and_export(card, card_path, len);
        if (fd >= 0) {
            *contig_out = 1;
            return fd;
        }
        err = fd;
        if (err == -ENOSYS) {
            z1_log_once(WARN_CARD_NO_DUMB, Z1_LOG_WARN,
                        "drm-dumb: %s has no dumb_create (%s); not usable for "
                        "contiguous buffers",
                        card_path, strerror(-err));
            drm_card_forget(card, 1);
            continue;
        }
        if (err == -ENODEV || err == -EBADF || err == -EIO) {
            /* Cached fd went stale (display driver unbound); re-open once. */
            drm_card_forget(card, 0);
            continue;
        }
        /* ENOMEM/EPERM/EACCES/...: nothing to retry, but the operator needs to
         * see the cause in logcat because ALOG* is compiled out of this HAL. */
        z1_log_once(WARN_DUMB_FAIL, Z1_LOG_WARN,
                    "drm-dumb: CREATE_DUMB/export on %s failed for %zu bytes: %s",
                    card_path, len, strerror(-err));
        return err;
    }
    return -ENODEV;
}

/* --------------------------------------------------------------------- lima --- */

static int lima_alloc(size_t len)
{
#ifdef Z1_USE_LIMA
    int fd = z1_lima_alloc_dmabuf(len);

    if (fd < 0)
        z1_log_once(WARN_LIMA_FAIL, Z1_LOG_WARN,
                    "lima: dma-buf allocation failed (%s), not contiguous anyway",
                    strerror(-fd));
    return fd;
#else
    (void)len;
    return -ENOSYS;
#endif
}

/* ------------------------------------------------------------ backend selection --- */

/* -1 = automatic chain, otherwise a kBackend* value (Z1_HEAP_FORCE). */
static int forced_backend()
{
    int value;

    pthread_mutex_lock(&g_state_lock);
    if (g_forced == -2) {
        const char *env = getenv("Z1_HEAP_FORCE");
        int parsed = -1;

        if (env && env[0]) {
            if (strcmp(env, "cma") == 0 || strcmp(env, "heap") == 0 ||
                strcmp(env, "dma-heap") == 0) {
                parsed = kBackendDmaHeap;
            } else if (strcmp(env, "dumb") == 0 || strcmp(env, "drm") == 0) {
                parsed = kBackendDrmDumb;
            } else if (strcmp(env, "lima") == 0) {
                parsed = kBackendLima;
            } else {
                z1_log_once(WARN_FORCE, Z1_LOG_WARN,
                            "Z1_HEAP_FORCE=%s is not one of cma|dumb|lima; "
                            "using the automatic chain",
                            env);
            }
        }
        g_forced = parsed;
        if (parsed != -1) {
            z1_log_once(WARN_FORCE, Z1_LOG_INFO,
                        "Z1_HEAP_FORCE=%s: only the %s backend will be used",
                        env, kBackendName[parsed]);
        }
    }
    value = g_forced;
    pthread_mutex_unlock(&g_state_lock);
    return value;
}

static int backend_wanted(int backend, int forced)
{
    return forced == -1 || forced == backend;
}

/*
 * Record a successful allocation and emit the one-time backend/total-bytes
 * report.  The message is emitted for the first allocation and every time the
 * backend changes (lima during the first ~33 s of boot -> dumb/CMA later).
 */
static int z1_heap_finish(int fd, size_t len, int backend, unsigned int caps,
                          const char *tag, const char *detail,
                          unsigned int *out_caps, const char **out_backend)
{
    int previous;
    int log_it;
    uint64_t total;

    pthread_mutex_lock(&g_state_lock);
    g_total_bytes += uint64_t(len);
    total = g_total_bytes;
    previous = g_last_backend;
    g_last_backend = backend;
    log_it = !g_selection_logged || previous != backend;
    g_selection_logged = 1;
    pthread_mutex_unlock(&g_state_lock);

    if (out_caps)
        *out_caps = caps;
    if (out_backend)
        *out_backend = kBackendName[backend];

    if (log_it) {
        if (previous == kBackendNone)
            z1_log(Z1_LOG_INFO,
                   "%s backend selected%s: %zu bytes (tag %s, uid %u), "
                   "total %llu bytes",
                   kBackendName[backend], detail, len, tag ? tag : "?",
                   (unsigned)getuid(), (unsigned long long)total);
        else
            z1_log(Z1_LOG_INFO,
                   "%s backend selected%s (was %s): %zu bytes (tag %s, uid %u), "
                   "total %llu bytes",
                   kBackendName[backend], detail, kBackendName[previous], len,
                   tag ? tag : "?", (unsigned)getuid(),
                   (unsigned long long)total);
    }
    return fd;
}

int z1_heap_alloc_ex(size_t size, const char *tag, unsigned int *out_caps,
                     const char **out_backend)
{
    size_t len;
    int forced;
    int fd = -ENOSYS;
    int last_err = -ENOSYS;
    int contig = 0;
    int tried = 0;
    char detail[96];

    if (out_caps)
        *out_caps = 0;
    if (out_backend)
        *out_backend = "none";
    if (!size || size > size_t(INT32_MAX) - 4096)
        return -EINVAL;
    len = z1_page_align(size);

    forced = forced_backend();

    if (backend_wanted(kBackendDmaHeap, forced)) {
        int heap_contig = 0;
        int heap_fd;

        tried = 1;
        heap_fd = dma_heap_fd(&heap_contig);
        if (heap_fd >= 0) {
            fd = dma_heap_alloc_from(heap_fd, len);
            if (fd >= 0) {
                char name[40] = "";

                pthread_mutex_lock(&g_state_lock);
                z1_name_copy(name, sizeof(name), g_heap_name);
                pthread_mutex_unlock(&g_state_lock);
                contig = heap_contig;
                snprintf(detail, sizeof(detail), " (%s)", name);
                return z1_heap_finish(fd, len, kBackendDmaHeap,
                                      contig ? Z1_HEAP_CAP_CONTIGUOUS : 0,
                                      tag, detail, out_caps, out_backend);
            }
            last_err = fd;
            /* Only a dead node is worth dropping; -ENOMEM means the CMA pool is
             * temporarily exhausted and the same fd must be reused. */
            if (fd == -EBADF || fd == -ENODEV || fd == -EIO)
                dma_heap_forget(heap_fd);
        } else {
            last_err = heap_fd;
        }
    }

    if (backend_wanted(kBackendDrmDumb, forced)) {
        tried = 1;
        fd = drm_dumb_alloc(len, &contig);
        if (fd >= 0) {
            snprintf(detail, sizeof(detail), " (%s)", drm_card_path());
            return z1_heap_finish(fd, len, kBackendDrmDumb,
                                  contig ? Z1_HEAP_CAP_CONTIGUOUS : 0, tag,
                                  detail, out_caps, out_backend);
        }
        last_err = fd;
    }

    if (backend_wanted(kBackendLima, forced)) {
        tried = 1;
        fd = lima_alloc(len);
        if (fd >= 0)
            return z1_heap_finish(fd, len, kBackendLima, 0, tag,
                                  " (not contiguous)", out_caps, out_backend);
        last_err = fd;
    }

    if (tried)
        z1_log_once(WARN_ALL_FAILED, Z1_LOG_WARN,
                    "no contiguous dma-buf backend available (%s); caller will "
                    "fall back to ashmem",
                    strerror(-last_err));
    return last_err;
}

int z1_heap_alloc(size_t size, const char *tag)
{
    return z1_heap_alloc_ex(size, tag, NULL, NULL);
}
