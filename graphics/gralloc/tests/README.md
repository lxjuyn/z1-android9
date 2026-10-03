# Z1 Linux 7 gralloc regression tests

Run `./run_host_tests.sh` from this directory (requires g++).

2026-10-02 result: PASS, 435411 layout and pixel checks under AddressSanitizer
and UndefinedBehaviorSanitizer. All five supported display formats (RGBA8888,
RGBX8888, BGRA8888, RGB888, RGB565) produce expected red/green/blue/white/black
RGB565 pixels across 240x320 rows with a padded 242-pixel input stride and
512-byte framebuffer stride. Source contents, destination row padding and
outer guards remain unchanged. One-byte-short source/destination and short
row strides fail before writes. The same allocation helper used by gralloc
returns 307204 bytes for 240x320 RGBA8888 (311296 after page rounding), rejects
invalid dimensions and overflow, and handles odd widths/heights.

The native_handle wire test uses the production private_handle_t header with
minimal host declarations for Android headers. It proves the 64-byte layout,
12 serialized ints plus one fd, metadata round trip, and rejection of old
handle layouts. It does not emulate Binder or validate Android runtime import.
The ABI static_assert will also run in the actual target module compilation.
No host ARM C++ compiler was available; Android target compilation is left to
the coordinating build.

Implementation notes: all HW_FB allocations now use ashmem shadow buffers
sized to the requested producer format. Width, height, pixel stride and format
are serialized in the native handle; fb_post converts into the native RGB565
layout without reading/writing row padding. The ABI changed intentionally:
install one consistent gralloc module and reboot, never mix old imported
handles with the replacement. FP16 and RAW16 can still be allocated for
non-display use but fb_post rejects them. True physical display refresh,
framebuffer mapping and SurfaceFlinger startup require an on-device test.

Pre-change backup:
`bridge_backups/gralloc_pre_linux7_fix_20261002_232228/`
