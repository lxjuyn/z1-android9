# Z1 display and graphics bring-up

The board modules in `gralloc/` and `hwcomposer/` are based on the Apache-2.0 AOSP Pie `device/generic/goldfish` fbdev gralloc and HWC1 modules. Their license headers are preserved. S1L builds `gralloc.z1.so` and `hwcomposer.z1.so` under `/system/lib/hw`, selects the Android graphics HIDL adapters, and uses SwiftShader for EGL/GLES rendering.

The current composition path copies rendered scan lines to `/dev/fb0` on the CPU. The board configuration expects 240 x 320 RGB565, a 512-byte framebuffer line and a 480-byte source row. The post path copies 480 bytes per row into the padded framebuffer pitch. HWC waits for acquire fences and uses no release or retire fence because the copy is synchronous. The stock kernel's actual framebuffer geometry still needs confirmation on the device.

The factory ROM contains MT6572 Mali EGL, gralloc and HWC binaries. They cannot be loaded directly by the Android 9 image. The factory `libMali.so` imports old `GraphicBuffer`, `BufferQueue`, `BufferItem`, `Fence`, and `CallStack` symbols that are not exported by the Android 9 `libui.so`/`libgui.so`; it also depends on `libcorkscrew.so` and `libdpframework.so`, which are absent. The factory HWC additionally imports `MTKM4UDrv` and `DpBlitStream` symbols. Android 9 includes `libion.so`, but it does not export the factory gralloc's `ion_alloc_mm` or `ion_custom_ioctl` entry points. The matching MTK M4U and display-framework libraries are also absent from the candidate image.

As a reference check, the FuQuan `libMali.so` has different bytes from the factory copy but the same 171 undefined dynamic-symbol imports. Replacing only that file therefore does not close the Android 9 ABI gap. The factory and reference Mali binaries are not included in S1R; GPU acceleration remains outstanding while the image uses SwiftShader.

The framebuffer and software graphics modules are present in the image, but the display has not been runtime-verified on this S1L build. Device validation still needs `/dev/fb0` geometry, `FBIOGET_FSCREENINFO.line_length`, visible test-pattern refresh, SurfaceFlinger composition and a producer/consumer buffer queue check.
