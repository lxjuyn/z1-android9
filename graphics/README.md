# Z1 software display bring-up

The files in `gralloc/` and `hwcomposer/` are board copies of the Apache-2.0
AOSP Pie `device/generic/goldfish` fbdev gralloc and HWC1 modules from this
workspace's LOS16 tree. Copyright and license headers are preserved. The
board modules are `gralloc.z1.so` and `hwcomposer.z1.so` under `/system/lib/hw`;
the HIDL graphics services and SwiftShader packages are selected in
`../device.mk`.

This is for the current `FB_SIMPLE` kernel route only. Z1's device tree declares
240 x 320 RGB565, a 512-byte framebuffer line, and an LK-owned display scanout.
The allocated source buffer has a 480-byte row. `fb_post` copies 480 bytes per
row into the 512-byte destination pitch. HWC waits for every acquire fence,
closes it, and uses no release or retire fence because the copy is synchronous.

Before calling this display working, build the modules against the synced Pie
tree, check `/dev/fb0` geometry and `FBIOGET_FSCREENINFO.line_length` on Z1,
write a test pattern and confirm visible refresh, then exercise SurfaceFlinger
and a producer/consumer buffer queue. This code has not yet been compiled
against the full LOS16 tree or tested on the board.
