# Z1 display and graphics bring-up

The board modules in `gralloc/` and `hwcomposer/` are based on the Apache-2.0 AOSP Pie `device/generic/goldfish` fbdev gralloc and HWC1 modules. Their license headers are preserved. S1L builds `gralloc.z1.so` and `hwcomposer.z1.so` under `/system/lib/hw`, selects the Android graphics HIDL adapters, and uses SwiftShader for EGL/GLES rendering.

The current composition path copies rendered scan lines to `/dev/fb0` on the CPU. The board configuration expects 240 x 320 RGB565, a 512-byte framebuffer line and a 480-byte source row. The post path copies 480 bytes per row into the padded framebuffer pitch. HWC waits for acquire fences and uses no release or retire fence because the copy is synchronous. The stock kernel's actual framebuffer geometry still needs confirmation on the device.

The factory ROM contains MT6572 Mali EGL, gralloc and HWC binaries. Static inspection found unresolved Android 4.4 private `libgui`/`libui` symbols in `libMali.so`, plus MTK ION calls absent from the Android 9 `libion.so`. Those binaries are not included in S1L because their current ABI closure is incomplete. GPU acceleration remains outstanding.

The framebuffer and software graphics modules are present in the image, but the display has not been runtime-verified on this S1L build. Device validation still needs `/dev/fb0` geometry, `FBIOGET_FSCREENINFO.line_length`, visible test-pattern refresh, SurfaceFlinger composition and a producer/consumer buffer queue check.
