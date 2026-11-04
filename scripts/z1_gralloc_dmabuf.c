/*
 * gralloc.z1.so -- gralloc0 module for the Z1 (MT6572) Android 9 bring-up.
 *
 * Why this exists
 * ---------------
 * hw_get_module("gralloc") prefers gralloc.<ro.hardware>.so over
 * gralloc.default.so, and ro.hardware is "z1" here, so this module is picked
 * up with no environment variables, no LD_PRELOAD and no namespace games.
 *
 * The stock gralloc.default.so allocates every buffer with ashmem (shmem).
 * mesa's Android EGL platform imports each gralloc buffer as a dma-buf via
 * DRM_IOCTL_PRIME_FD_TO_HANDLE, which an ashmem fd cannot satisfy:
 *
 *     EGL-MAIN: failed to create DRI image from FD
 *     SurfaceFlinger: failed to create EGLImage: 0x3003   (EGL_BAD_ALLOC)
 *
 * So this module does not reimplement gralloc at all: it dlopen()s
 * gralloc.default.so, copies its gralloc_module_t into our own HMI symbol
 * (so registerBuffer/lock/unlock/perform and the fb0 device keep working),
 * and only wraps alloc_device_t::alloc to replace the freshly created ashmem
 * fd with a /dev/dma_heap/system dma-buf of the same size.  dma-bufs are
 * mmap-able, so gralloc's own lock()/unmap() and fb post path are unchanged,
 * but the fd is now importable by lima / kms_swrast through PRIME.
 *
 * Freestanding: no libc, raw syscalls; dlopen/dlsym via weak references.
 * Every step is reported to /dev/kmsg (logcat is not guaranteed to be up, and
 * a dlsym'd __android_log_print may not resolve inside a HAL).
 *
 * If gralloc.default.so cannot be loaded, the exported module degrades to a
 * valid but non-functional module (open() returns -EINVAL) instead of a zeroed
 * struct -- a zeroed struct makes hw_get_module() strcmp() against a NULL id
 * and SIGSEGVs the allocator HAL in a restart loop.
 *
 * Build:
 *   arm-linux-gnueabihf-gcc -O2 -fPIC -shared -nostdlib -fno-stack-protector \
 *       -march=armv7-a -mfloat-abi=softfp -mfpu=vfpv3-d16 \
 *       -Wl,-soname,gralloc.z1.so -o out/gralloc.z1.so tools/z1_gralloc_dmabuf.c
 */

typedef unsigned int u32;
typedef unsigned long ulong;
typedef unsigned char u8;
typedef unsigned short u16;

#define SYS_write  4
#define SYS_open   5
#define SYS_close  6
#define SYS_ioctl  54
#define SYS_getpid 20

#define O_WRONLY   1
#define O_RDWR     2
#define O_CLOEXEC  02000000
#define PAGE_SIZE  4096UL

#define DMA_HEAP_IOC_ALLOC 0xc0184800UL
#define ASHMEM_GET_SIZE    0x00007704UL

#define GRALLOC_HARDWARE_MODULE_ID "gralloc"
#define GRALLOC_HARDWARE_GPU0      "gpu0"
#define GRALLOC_HARDWARE_FB0       "fb0"

#define RTLD_NOW_LOCAL 2   /* RTLD_NOW, no RTLD_GLOBAL: keep its HMI private */

/* 'HWMT' / 'HWDT' */
#define HARDWARE_MODULE_TAG 0x48574d54u

struct dma_heap_allocation_data {
	unsigned long long len;
	u32 fd;
	u32 fd_flags;
	unsigned long long heap_flags;
};

/* --- hardware/libhardware ABI (hand-declared, ABI only) --- */

struct hw_module_t;
struct hw_device_t;

struct hw_module_methods_t {
	int (*open)(const struct hw_module_t *module, const char *id,
		    struct hw_device_t **device);
};

struct hw_module_t {
	u32 tag;
	u16 module_api_version;
	u16 hal_api_version;
	const char *id;
	const char *name;
	const char *author;
	struct hw_module_methods_t *methods;
	void *dso;
	u32 reserved[32 - 7];
};

struct hw_device_t {
	u32 tag;
	u32 version;
	struct hw_module_t *module;
	u32 reserved[12];
	int (*close)(struct hw_device_t *device);
};

typedef const void *buffer_handle_t;

struct alloc_device_t {
	struct hw_device_t common;
	int (*alloc)(struct alloc_device_t *dev, int w, int h, int format,
		     int usage, buffer_handle_t *handle, int *stride);
	int (*free)(struct alloc_device_t *dev, buffer_handle_t handle);
	void *reserved_proc[7];
};

/* --- libc bits we need, without libc --- */

extern void *dlopen(const char *name, int flag) __attribute__((weak));
extern void *dlsym(void *handle, const char *symbol) __attribute__((weak));
typedef const char *(*dlerror_fn)(void);

typedef int (*android_log_fn)(int prio, const char *tag, const char *fmt, ...);
static android_log_fn alog;
static int alog_tried;

static long sys3(long n, long a, long b, long c)
{
	register long r0 __asm__("r0") = a;
	register long r1 __asm__("r1") = b;
	register long r2 __asm__("r2") = c;
	register long r7 __asm__("r7") = n;

	__asm__ __volatile__("svc 0" : "+r"(r0) : "r"(r1), "r"(r2), "r"(r7)
			     : "memory");
	return r0;
}

static int slen(const char *s)
{
	int n = 0;

	while (s[n])
		n++;
	return n;
}

static void memcpy_(void *dst, const void *src, unsigned n)
{
	u8 *d = dst;
	const u8 *s = src;

	while (n--)
		*d++ = *s++;
}

static void memset_(void *dst, int c, unsigned n)
{
	u8 *d = dst;

	while (n--)
		*d++ = (u8)c;
}

/* append "0123456789abcdef" representation of v; returns new length */
static int put_hex(char *b, int o, ulong v)
{
	char t[16];
	int i = 0;

	if (v == 0) {
		b[o++] = '0';
		return o;
	}
	while (v) {
		t[i++] = "0123456789abcdef"[v & 0xf];
		v >>= 4;
	}
	while (i)
		b[o++] = t[--i];
	return o;
}

/* One line to /dev/kmsg: "<label>" or "<label>=<hex>" or "<label> <str>" */
static void klog(const char *label, const char *str, ulong val, int has_val)
{
	char buf[160];
	int o = 0, fd, i;

	buf[o++] = 'z';
	buf[o++] = '1';
	buf[o++] = 'g';
	buf[o++] = ':';
	buf[o++] = ' ';
	for (i = 0; label[i] && o < 100; i++)
		buf[o++] = label[i];
	if (has_val) {
		buf[o++] = '=';
		o = put_hex(buf, o, val);
	}
	if (str) {
		buf[o++] = ' ';
		for (i = 0; str[i] && o < 150; i++)
			buf[o++] = str[i];
	}
	buf[o++] = '\n';

	fd = (int)sys3(SYS_open, (long)"/dev/kmsg", O_WRONLY | O_CLOEXEC, 0);
	if (fd < 0)
		return;
	sys3(SYS_write, fd, (long)buf, o);
	sys3(SYS_close, fd, 0, 0);
}

static void zlog(const char *fmt, unsigned a, const void *b)
{
	if (!alog_tried) {
		alog_tried = 1;
		if (dlsym)
			alog = (android_log_fn)dlsym((void *)0, "__android_log_print");
	}
	if (alog)
		alog(4, "z1gralloc", fmt, a, b);
}

static int streq(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return *a == *b;
}

static int dma_heap_alloc(ulong size)
{
	struct dma_heap_allocation_data data;
	long heap, r;

	size = (size + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1);

	heap = sys3(SYS_open, (long)"/dev/dma_heap/system", O_RDWR | O_CLOEXEC, 0);
	if (heap < 0)
		return -1;

	data.len = size;
	data.fd = 0;
	data.fd_flags = O_RDWR | O_CLOEXEC;
	data.heap_flags = 0;
	r = sys3(SYS_ioctl, heap, DMA_HEAP_IOC_ALLOC, (long)&data);
	sys3(SYS_close, heap, 0, 0);

	return r < 0 ? -1 : (int)data.fd;
}

/* --- the module --- */

/* hw_get_module() dlsym()s "HMI" and casts it to hw_module_t*, so the
 * exported symbol *is* the storage: it holds a byte-copy of
 * gralloc.default's gralloc_module_t (256 bytes covers hw_module_t plus its
 * eight function pointers) with only .methods swapped for ours. */
u8 HMI[512] __attribute__((aligned(8), visibility("default")));

/* gralloc.default.so's HMI object is 448 bytes (readelf --dyn-syms).  Copy
 * the whole thing, not just the 128-byte hw_module_t: the blob carries its
 * own extra state after the standard gralloc_module_t. */
#define REAL_HMI_BYTES 448

#define HMI_AS_MODULE ((struct hw_module_t *)HMI)

static struct hw_module_t *real_mod;
static struct alloc_device_t *real_alloc_dev;
static struct alloc_device_t our_alloc_dev;
static int logged;

static int our_alloc(struct alloc_device_t *dev, int w, int h, int format,
		     int usage, buffer_handle_t *handle, int *stride)
{
	int *nh, oldfd, newfd, size, err;

	(void)dev;
	err = real_alloc_dev->alloc(real_alloc_dev, w, h, format, usage,
				    handle, stride);
	if (err != 0 || handle == 0 || *handle == 0)
		return err;

	/* native_handle_t: version, numFds, numInts, data[0] = fd */
	nh = (int *)*handle;
	if (nh[1] != 1)
		return 0;

	oldfd = nh[3];
	size = (int)sys3(SYS_ioctl, oldfd, ASHMEM_GET_SIZE, 0);
	if (size <= 0)
		return 0;	/* not ashmem (fb path): leave it alone */

	newfd = dma_heap_alloc((ulong)size);
	if (newfd < 0) {
		if (!logged) {
			logged = 1;
			klog("dma_heap_alloc FAILED size", 0, (ulong)size, 1);
			zlog("ERR dma_heap alloc failed size=%u", (unsigned)size,
			     (const void *)0);
		}
		return 0;	/* keep the ashmem buffer: never break alloc */
	}

	sys3(SYS_close, oldfd, 0, 0);
	nh[3] = newfd;
	if (!logged) {
		logged = 1;
		klog("wrapped alloc size", 0, (ulong)size, 1);
		zlog("wrapped alloc: %ux%u ashmem->dmabuf", (unsigned)w,
		     (const void *)(ulong)size);
	}
	return 0;
}

static int our_open(const struct hw_module_t *module, const char *id,
		    struct hw_device_t **device)
{
	int err;

	(void)module;

	if (!real_mod || !real_mod->methods || !real_mod->methods->open) {
		klog("open FAILED no real module id", id ? id : "(null)", 0, 0);
		return -22;	/* -EINVAL: fail cleanly, never crash the HAL */
	}

	if (id != 0 && streq(id, GRALLOC_HARDWARE_GPU0)) {
		err = real_mod->methods->open(real_mod, id,
					      (struct hw_device_t **)&real_alloc_dev);
		if (err != 0 || real_alloc_dev == 0)
			return err;
		memcpy_(&our_alloc_dev, real_alloc_dev, sizeof(our_alloc_dev));
		our_alloc_dev.alloc = our_alloc;
		*device = (struct hw_device_t *)&our_alloc_dev;
		return 0;
	}

	/* fb0 (used by HWC2OnFbAdapter) and anything else: untouched */
	return real_mod->methods->open(real_mod, id, device);
}

static struct hw_module_methods_t our_methods = {
	.open = our_open,
};

/* Valid-but-inert module: keeps hw_get_module() from dereferencing a NULL id. */
static void set_fallback(void)
{
	struct hw_module_t *m = HMI_AS_MODULE;

	memset_(HMI, 0, sizeof(HMI));
	m->tag = HARDWARE_MODULE_TAG;
	m->module_api_version = 1;
	m->hal_api_version = 0;
	m->id = GRALLOC_HARDWARE_MODULE_ID;
	m->name = "z1 gralloc (no real module)";
	m->author = "z1";
	m->methods = &our_methods;
	klog("FALLBACK module installed (real gralloc NOT loaded)", 0, 0, 0);
}

__attribute__((constructor)) static void z1gralloc_init(void)
{
	static const char *const real_paths[] = {
		"/vendor/lib/hw/gralloc.default.so",
		"/system/vendor/lib/hw/gralloc.default.so",
		"gralloc.default.so",
	};
	void *h = 0;
	const char *why;
	dlerror_fn d;
	int i;

	klog("ctor pid", 0, (ulong)sys3(SYS_getpid, 0, 0, 0), 1);
	klog("dlopen ptr", 0, (ulong)(ulong)dlopen, 1);
	klog("dlsym ptr", 0, (ulong)(ulong)dlsym, 1);

	set_fallback();		/* valid module even if everything below fails */

	if (!dlopen || !dlsym) {
		klog("ABORT dlopen/dlsym unresolved", 0, 0, 0);
		return;
	}

	for (i = 0; i < 3; i++) {
		h = dlopen(real_paths[i], RTLD_NOW_LOCAL);
		klog("try", real_paths[i], (ulong)h, 1);
		if (h)
			break;
		d = (dlerror_fn)dlsym((void *)0, "dlerror");
		why = d ? d() : (const char *)0;
		if (why)
			klog("dlerror", why, 0, 0);
	}
	if (!h) {
		klog("dlopen gralloc.default FAILED", 0, 0, 0);
		zlog("ERR dlopen gralloc.default.so failed", 0, (const void *)0);
		return;
	}
	klog("dlopen gralloc.default OK handle", 0, (ulong)h, 1);

	real_mod = (struct hw_module_t *)dlsym(h, "HMI");
	if (!real_mod) {
		klog("dlsym HMI FAILED", 0, 0, 0);
		zlog("ERR dlsym HMI failed", 0, (const void *)0);
		return;
	}
	klog("real HMI id", real_mod->id, 0, 0);

	memcpy_(HMI, real_mod, REAL_HMI_BYTES);
	HMI_AS_MODULE->methods = &our_methods;
	klog("ready id", HMI_AS_MODULE->id, 0, 0);
	zlog("ready: wrapping gralloc.default alloc (pid logged at first alloc)",
	     0, (const void *)0);
}
