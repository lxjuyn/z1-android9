/*
 * z1contigtest — on-board proof that the Z1 gralloc hands out physically
 * contiguous dma-bufs, i.e. that mediatek-drm OVL can really scan them out.
 *
 * Background: fork_linux/drivers/gpu/drm/mediatek/mtk_gem.c
 *
 *     mtk_gem_prime_import_sg_table()
 *         if (drm_prime_get_contiguous_size(sgt) < attach->dmabuf->size) {
 *                 DRM_ERROR("sg_table is not contiguous");
 *                 return ERR_PTR(-EINVAL);
 *         }
 *
 * so DRM_IOCTL_PRIME_FD_TO_HANDLE on /dev/dri/card1 is itself the contiguity
 * oracle: it fails with EINVAL for a scattered buffer (lima's drm_gem_shmem
 * pages) and succeeds for a contiguous one (CMA dma-heap / dma_alloc_wc).  This
 * tool allocates buffers through the very same z1_heap.cpp the gralloc HAL uses
 * and runs that import check on each of them.
 *
 * Self-contained: libc + z1_heap.cpp only, no Android framework, no libdrm
 * (the two DRM ioctls it needs are defined locally and _Static_assert'ed against
 * their uapi values).
 *
 * ---------------------------------------------------------------------------
 * Build (NDK r26d, armv7, fully static — just adb push the binary):
 *
 *   NDK=/home/lxj/claude/6572/android-ndk-r26d
 *   TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
 *   G=/home/lxj/claude/6572/z1a9_gpt/graphics/gralloc
 *   SHIM=/home/lxj/claude/6572/z1a9_gpt/scripts/hal_stub
 *   # -DZ1_USE_LIMA also lets Z1_HEAP_FORCE=lima reproduce the old scattered
 *   # (drm_gem_shmem) buffers so the EINVAL import failure can be shown.
 *   $TC/armv7a-linux-androideabi28-clang++ -c -O2 -Wall -fno-exceptions \
 *       -fno-rtti -nostdlib++ -DZ1_USE_LIMA=1 -I"$SHIM" -I"$G" \
 *       -o /tmp/z1_heap.o "$G/z1_heap.cpp"
 *   $TC/armv7a-linux-androideabi28-clang++ -c -O2 -Wall -fno-exceptions \
 *       -fno-rtti -nostdlib++ -DZ1_USE_LIMA=1 -I"$SHIM" -I"$G" \
 *       -o /tmp/z1_lima_allocator.o "$G/lima_allocator.cpp"
 *   $TC/armv7a-linux-androideabi28-clang -c -O2 -Wall -I"$G" \
 *       -o /tmp/z1contigtest.o \
 *       /home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2/z1contigtest.c
 *   $TC/armv7a-linux-androideabi28-clang -static -o /tmp/z1contigtest \
 *       /tmp/z1contigtest.o /tmp/z1_heap.o /tmp/z1_lima_allocator.o
 *
 * On the board (root, serial console or adb):
 *
 *   adb push /tmp/z1contigtest /data/local/tmp/ && adb shell chmod 755 ...
 *   /data/local/tmp/z1contigtest                 # 3 buffers, automatic chain
 *   Z1_HEAP_FORCE=dumb /data/local/tmp/z1contigtest
 *   Z1_HEAP_FORCE=lima /data/local/tmp/z1contigtest   # expect EINVAL on import
 *   /data/local/tmp/z1contigtest --fd 42          # inspect an existing fd
 *   /data/local/tmp/z1contigtest --size 311296    # allocate one buffer
 *
 * Output goes to stdout *and* to /dev/kmsg ("z1contig: ..." prefix), because
 * this board is usually driven from the serial console where `dmesg` is the
 * reliable log.  It also re-implements the two liblog calls used by z1_heap.cpp
 * so the HAL's own backend-selection messages appear in this tool's output.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "z1_heap.h"

/* ---------------------------------------------------------------- raw DRM UAPI --- */

/*
 * Copied from fork_linux/include/uapi/drm/drm.h (identical to the NDK/libdrm
 * headers); DRM_IOCTL_BASE is 'd' there.  armv7 EABI struct sizes are asserted
 * below, so a mismatch with the kernel UAPI cannot slip through silently.
 */
struct z1_drm_prime_handle {
    uint32_t handle;
    uint32_t flags;
    int32_t fd;
};

struct z1_drm_gem_close {
    uint32_t handle;
    uint32_t pad;
};

#define Z1_DRM_IOCTL_PRIME_FD_TO_HANDLE \
    _IOWR('d', 0x2e, struct z1_drm_prime_handle)
#define Z1_DRM_IOCTL_GEM_CLOSE _IOW('d', 0x09, struct z1_drm_gem_close)

_Static_assert(sizeof(struct z1_drm_prime_handle) == 12,
               "drm_prime_handle must be 12 bytes on arm EABI");
_Static_assert(sizeof(struct z1_drm_gem_close) == 8,
               "drm_gem_close must be 8 bytes on arm EABI");
_Static_assert(Z1_DRM_IOCTL_PRIME_FD_TO_HANDLE == 0xC00C642Eu,
               "DRM_IOCTL_PRIME_FD_TO_HANDLE must match uapi/drm/drm.h");
_Static_assert(Z1_DRM_IOCTL_GEM_CLOSE == 0x40086409u,
               "DRM_IOCTL_GEM_CLOSE must match uapi/drm/drm.h");
_Static_assert(Z1_PRIV_FLAGS_CONTIG == 0x00000004u,
               "PRIV_FLAGS_CONTIG must stay bit 2 of private_handle_t::flags");

/* ---------------------------------------------------------------------- output --- */

static int g_kmsg = -1;

static void zlog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static void zlog(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    ssize_t written;
    int n;

    va_start(ap, fmt);
    n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n <= 0)
        return;
    if (g_kmsg >= 0) {
        written = write(g_kmsg, buf, (size_t)n);
        (void)written;
    }
    written = write(STDOUT_FILENO, buf, (size_t)n);
    (void)written;
}

#define ZLOG(...) zlog("z1contig: " __VA_ARGS__)

/*
 * Minimal liblog stand-in.  A static binary has no liblog.so, and z1_heap.cpp
 * calls __android_log_print() on purpose (the ALOG* macros of the gralloc build
 * shim are no-ops, so they would hide the backend selection).  Print the same
 * messages here instead of dropping them.
 */
int __android_log_print(int prio, const char *tag, const char *fmt, ...)
{
    char buf[512];
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    zlog("%c/%s: %s\n", prio >= 5 ? 'W' : 'I', tag ? tag : "?", buf);
    return 0;
}

/* -------------------------------------------------------------------- helpers --- */

static const char *errno_name(int err)
{
    switch (err) {
    case 0: return "ok";
    case EINVAL: return "EINVAL";
    case ENOMEM: return "ENOMEM";
    case ENOSYS: return "ENOSYS";
    case ENODEV: return "ENODEV";
    case ENOENT: return "ENOENT";
    case EACCES: return "EACCES";
    case EPERM: return "EPERM";
    case EBADF: return "EBADF";
    case EIO: return "EIO";
    default: return "errno";
    }
}

static void dump_fdinfo(int fd)
{
    char path[64];
    char line[256];
    FILE *fp;

    snprintf(path, sizeof(path), "/proc/self/fdinfo/%d", fd);
    fp = fopen(path, "r");
    if (!fp) {
        ZLOG("    fdinfo: cannot open %s (%s)\n", path, strerror(errno));
        return;
    }
    while (fgets(line, sizeof(line), fp))
        ZLOG("    fdinfo: %s", line);
    fclose(fp);
}

/* Write/read back the first, middle and last 4 KiB of the mapping. */
static int mmap_test(int fd, size_t size)
{
    unsigned char *base;
    size_t offsets[3];
    size_t off_count = 0;
    size_t i;
    int failed = 0;

    if (size == 0) {
        ZLOG("    mmap: skipped (size 0)\n");
        return -1;
    }
    base = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) {
        ZLOG("    mmap: FAILED (%s)\n", strerror(errno));
        return -1;
    }
    ZLOG("    mmap: %p size %zu (MAP_SHARED)\n", (void *)base, size);

    offsets[off_count++] = 0;
    if (size > 4096)
        offsets[off_count++] = (size / 2) & ~(size_t)4095;
    if (size > 8192)
        offsets[off_count++] = (size - 4096) & ~(size_t)4095;

    for (i = 0; i < off_count; ++i) {
        const size_t off = offsets[i];
        const size_t len = (size - off) < 4096 ? (size - off) : 4096;
        size_t j;

        for (j = 0; j < len; ++j)
            base[off + j] = (unsigned char)(0x5A ^ (unsigned)((off + j) & 0xFF));
        for (j = 0; j < len; ++j) {
            const unsigned char want =
                (unsigned char)(0x5A ^ (unsigned)((off + j) & 0xFF));

            if (base[off + j] != want) {
                ZLOG("    mmap: write/readback MISMATCH at %zu: got %#x want %#x\n",
                     off + j, base[off + j], want);
                failed = 1;
                break;
            }
        }
    }
    if (!failed)
        ZLOG("    mmap: write/readback PASS at %zu offset(s)\n", off_count);

    if (munmap(base, size) != 0)
        ZLOG("    munmap failed (%s)\n", strerror(errno));
    return failed ? -1 : 0;
}

/*
 * The contiguity oracle: import the buffer into the display DRM device.  For a
 * dma-buf exported by another driver (CMA heap) this runs
 * mtk_gem_prime_import_sg_table() and therefore the contiguous-size check; for a
 * dumb buffer exported by card1 itself the kernel takes the same-device path and
 * reuses the GEM object (dma_alloc_wc() is contiguous by construction).
 */
static void drm_import_test(int fd, const char *backend)
{
    const char *path = "/dev/dri/card1";
    struct z1_drm_prime_handle prime;
    struct z1_drm_gem_close close_arg;
    int card;
    int ret;

    card = open(path, O_RDWR | O_CLOEXEC);
    if (card < 0) {
        ZLOG("    %s: open failed (%s); import check skipped "
             "(mediatek-drm appears ~33 s after boot)\n",
             path, strerror(errno));
        return;
    }

    memset(&prime, 0, sizeof(prime));
    prime.fd = fd;
    do {
        ret = ioctl(card, Z1_DRM_IOCTL_PRIME_FD_TO_HANDLE, &prime);
    } while (ret < 0 && errno == EINTR);

    if (ret == 0) {
        ZLOG("    %s: PRIME_FD_TO_HANDLE OK (gem handle %u)\n", path,
             prime.handle);
        if (backend && strncmp(backend, "dma-heap", 8) == 0)
            ZLOG("        -> mtk_gem_prime_import_sg_table() accepted it: "
                 "sg_table IS contiguous, OVL can scan this buffer out\n");
        else if (backend && strncmp(backend, "drm-dumb", 8) == 0)
            ZLOG("        -> same DRM device export (GEM object reused); "
                 "dma_alloc_wc() is contiguous by construction\n");
        else
            ZLOG("        -> import accepted\n");
        memset(&close_arg, 0, sizeof(close_arg));
        close_arg.handle = prime.handle;
        (void)ioctl(card, Z1_DRM_IOCTL_GEM_CLOSE, &close_arg);
    } else {
        const int err = errno;

        ZLOG("    %s: PRIME_FD_TO_HANDLE failed: %s (%s)\n", path,
             errno_name(err), strerror(err));
        if (err == EINVAL)
            ZLOG("        -> mtk_gem_prime_import_sg_table() rejected the "
                 "buffer: sg_table is NOT contiguous, OVL cannot scan it out\n");
    }
    close(card);
}

/* ----------------------------------------------------------------------- main --- */

static const size_t kSizes[] = {
    240 * 320 * 4 + 4,  /* Z1 client target RGBA8888: z1_buffer_layout() size */
    1024 * 600 * 4 + 4, /* ~2.4 MB layer: needs a high-order CMA block         */
    4096,               /* single page: smallest legal dma-heap allocation     */
};

static int test_one(int fd, size_t requested, const char *backend,
                    unsigned int caps)
{
    struct stat st;
    off_t end;
    size_t page = 4096;

    ZLOG("---- fd %d ----\n", fd);
    if (requested)
        ZLOG("    requested %zu bytes -> z1_heap page-aligned result\n",
             requested);
    if (backend)
        ZLOG("    backend: %s%s\n", backend,
             (caps & Z1_HEAP_CAP_CONTIGUOUS) ? " (CONTIGUOUS)" : " (NOT contiguous)");

    if (fstat(fd, &st) == 0) {
        ZLOG("    fstat: mode=%#o rdev=%u:%u size=%lld\n", (unsigned)st.st_mode,
             (unsigned)((st.st_rdev >> 8) & 0xFFF), (unsigned)(st.st_rdev & 0xFF),
             (long long)st.st_size);
    } else {
        ZLOG("    fstat failed (%s)\n", strerror(errno));
    }

    end = lseek(fd, 0, SEEK_END);
    if (end < 0) {
        ZLOG("    lseek(SEEK_END): FAILED (%s) -- not a dma-buf?\n",
             strerror(errno));
        return -1;
    }
    if (requested)
        ZLOG("    lseek(SEEK_END): %lld bytes%s\n", (long long)end,
             (size_t)end >= requested ? " (>= requested, OK)"
                                      : " (< requested!)");
    else
        ZLOG("    lseek(SEEK_END): %lld bytes\n", (long long)end);
    if (requested && (size_t)end >= page && (size_t)end > requested)
        ZLOG("    note: %zu bytes of page/CMA alignment padding\n",
             (size_t)end - requested);

    dump_fdinfo(fd);

    if (mmap_test(fd, (size_t)end) != 0)
        return -1;
    drm_import_test(fd, backend);
    return 0;
}

static int usage(const char *argv0)
{
    zlog("z1contig: usage: %s            allocate 3 buffers through z1_heap and test them\n",
         argv0);
    zlog("z1contig:        %s --fd N     inspect existing fd N\n", argv0);
    zlog("z1contig:        %s --size N   allocate one N-byte buffer\n", argv0);
    zlog("z1contig: env:   Z1_HEAP_FORCE=cma|dumb|lima   force one backend\n");
    return 2;
}

int main(int argc, char **argv)
{
    int failures = 0;

    g_kmsg = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);

    zlog("z1contig: z1contigtest: contiguous dma-buf proof for the Z1 gralloc\n");
    ZLOG("handle flag contract: PRIV_FLAGS_DMABUF=0x2, "
         "PRIV_FLAGS_CONTIG=0x%x (set by gralloc for contiguous buffers)\n",
         Z1_PRIV_FLAGS_CONTIG);

    if (argc == 3 && strcmp(argv[1], "--fd") == 0) {
        const int fd = atoi(argv[2]);

        ZLOG("inspecting existing fd %d\n", fd);
        if (fd < 0 || test_one(fd, 0, NULL, 0) != 0)
            ++failures;
    } else if (argc == 3 && strcmp(argv[1], "--size") == 0) {
        const size_t want = (size_t)strtoul(argv[2], NULL, 0);
        char backend[64] = {0};
        const char *name = NULL;
        unsigned int caps = 0;
        int fd;

        ZLOG("allocating one %zu-byte buffer\n", want);
        fd = z1_heap_alloc_ex(want, "z1contigtest", &caps, &name);
        if (fd < 0) {
            ZLOG("z1_heap_alloc_ex(%zu) failed: %s (%s)\n", want,
                 errno_name(-fd), strerror(-fd));
            ++failures;
        } else {
            snprintf(backend, sizeof(backend), "%s", name ? name : "?");
            if (test_one(fd, want, backend, caps) != 0)
                ++failures;
            close(fd);
        }
    } else if (argc == 1) {
        size_t i;

        for (i = 0; i < sizeof(kSizes) / sizeof(kSizes[0]); ++i) {
            const size_t want = kSizes[i];
            char backend[64] = {0};
            const char *name = NULL;
            unsigned int caps = 0;
            int fd;

            ZLOG("==== allocation %zu/%zu: %zu bytes ====\n", i + 1,
                 sizeof(kSizes) / sizeof(kSizes[0]), want);
            fd = z1_heap_alloc_ex(want, "z1contigtest", &caps, &name);
            if (fd < 0) {
                ZLOG("z1_heap_alloc_ex(%zu) failed: %s (%s)\n", want,
                     errno_name(-fd), strerror(-fd));
                ++failures;
                continue;
            }
            snprintf(backend, sizeof(backend), "%s", name ? name : "?");
            if (test_one(fd, want, backend, caps) != 0)
                ++failures;
            close(fd);
        }
    } else {
        return usage(argv[0]);
    }

    ZLOG("%s: %d failure(s)\n", failures ? "FAIL" : "PASS", failures);
    if (g_kmsg >= 0)
        close(g_kmsg);
    return failures ? 1 : 0;
}
