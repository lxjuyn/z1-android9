# 应用侧 GPU 崩溃的根因：gralloc stride 只对齐 2 像素，lima 要求 16

## 链条（每层都有源码/日志定位）

1. **AOSP 自己的坑**：`ShadowKeyDrawable.java:174`
   `bitmap = bitmap.copy(Bitmap.Config.HARDWARE, false);` 可能返回 **null**，
   第 175 行原样存进 `mLastDrawnBitmap`，第 88 行 `canvas.drawBitmap(null, ...)` → **NPE**。
   所以不是内存问题、不是 nav bar 新开，是"硬件位图创建失败"。

2. **HWUI 的失败点**：`SkiaOpenGLPipeline::allocateHardwareBitmap`（`:404-410`）
   `AutoEglImage` 拿不到 EGLImage → `ALOGW("Could not create EGL image, err =%s")` → 返回 nullptr。
   板上日志正是这一行，且 `err = EGL_SUCCESS` —— 说明**失败路径没设置任何 EGL 错误**，
   排除了 `createGraphicBuffer() failed` 那条。

3. **驱动为什么返回 NULL**：Mesa 26 的 lima 在
   `lima_resource.c:371-401` 对"被当作 render target 导入的线性 buffer"做检查：

   ```c
   width  = align(pres->width0, 16);
   stride = util_format_get_stride(pres->format, width);   // = align(w,16)*bpp
   if (!res->tiled && res->levels[0].stride < stride) {
       fprintf(stderr, "linear imported buffer stride is smaller than minimal: ...");
       goto err_out;                                       // -> NULL
   }
   ```
   **`fprintf(stderr)` 在 Android 应用进程里没人读**，所以 logcat 完全静默 ——
   这解释了"err=EGL_SUCCESS、没有任何 MESA 错误日志"的诡异现象。

## 数字对得上

| buffer | 宽度 | 旧 gralloc stride（对齐 2px） | lima 要求 align(w,16)*bpp | 结果 |
|---|---|---|---|---|
| 导航键图标 28dp@160dpi | 28 px | 28 px = 112 B | align(28,16)=32 → 128 B | **112 < 128 → 拒绝** |
| home 图标 14dp | 14 px | 14 px = 56 B | 16 → 64 B | **拒绝** |
| 全屏 window | 240 px | 240 px = 960 B | align(240,16)=240 → 960 B | **通过** |

这正好解释"只有小位图死、系统界面本身还能显示"。

## 修法

`z1a9_gpt/graphics/gralloc/pixel_copy.h` 的 `z1_buffer_layout()`：

```c
- const uint64_t columns = (uint64_t(width) + 1) & ~uint64_t(1);      // 对齐 2 像素
+ const uint64_t columns = (uint64_t(width) + 15) & ~uint64_t(15);    // 对齐 16 像素
```

- 主机回归测试同步更新并通过：**5,121,415 个检查全过**
  （新增一条"任意宽度 1..300 都必须满足 lima 下限"的回归断言）。
- 重编 `gralloc.z1.so`（NDK r26d，18,432 B，SONAME 正确，NEEDED 与原版一致）。
  构建所需的最小 AOSP 头在 `/tmp/gpsstub/`（hardware/gralloc.h、fb.h、cutils/*、log/log.h shim、system/graphics*）。

## 产物

`out/z1_v29_gralloc16/system_v29.img`
sha256 `b06f85a5c0046b78a165a725c6ba067a7f78cc3c444fc6fc9f4d49ddb771437a`，e2fsck 干净。

= v25（Mesa26 + gate + 模块修复）
+ 修好 xImage 的 `libGLES_mesa.so`
+ **开 GLES1 的 `libgallium_dri.so`**（二屏）
+ **GPS stub HAL**（不再阻塞 system_server）
+ 修好的 `z1_gpu_watch.sh`
+ **16 像素对齐的 `gralloc.z1.so`**（应用侧 EGLImage）

## NOT VERIFIED
整条链一次都没上板跑过。判据：
- 二屏出现 → GLES1 生效
- 不再有 `WATCHDOG KILLING ... GnssLocationProvider` → GPS stub 生效
- launcher/SystemUI 不再 NPE、能进桌面 → gralloc 对齐生效
- `dumpsys SurfaceFlinger` 仍是 Mesa/Mali400，且 `gp error irq` 保持 0

## 交叉验证（两个独立 agent）

- `bootanim-egl`：全日志 0 条 `Failed to open ... DRM device`，且 app 进程的 RenderThread 有
  `open /dev/dri` + `read /sys/devices/platform/soc/13010000.gpu/uevent` 的审计记录
  ⇒ **应用进程确实拿到了 render node**，"拿不到 GPU"这条不成立。
- `systemui-crash`：独立复核后同样指向 lima 的 stride 检查，并给出板上决定性验法：
  ```
  setprop wrap.com.android.systemui logwrapper
  killall com.android.systemui
  logcat -d | grep -iE "stride|import|dma.?buf|lima"
  ```
  预期读到 `linear imported buffer stride is smaller than minimal: 112 (BO) < 128 (min)`。
  验证完 `setprop wrap.com.android.systemui ""`。

## 另一个独立问题：launcher 的图标 NPE 不完全是 GPU

`LoaderCursor.java:189` 崩的是
`BitmapFactory.decodeByteArray(data, 0, data.length)`，其中 `data = getBlob(iconIndex)`
是 **launcher 数据库里的 byte[] blob 为 null**，不是 Bitmap。
最可能是"硬件位图创建失败 → 图标加载返回 null → 被写进 DB"的后续效应；
所以刷完 v29 后，**如果桌面还崩，需要在串口 shell 里清一次 launcher 数据**：
```
pm clear com.android.launcher3
```
（这条也解释了为什么 `mFocusedApp` 一直是 FallbackHome。）

## 留待转 enforcing 前修的两处 SELinux（现在是 permissive，只是隐患）

- `platform_app → gpu_device:dir { read }` 41 条 —— enforcing 下 Mesa 的 `drmGetDevices2`
  会拿不到设备，那时"应用没有 GPU"才真的成立。
- `hal_graphics_allocator_default → gpu_device` 46 条 —— enforcing 下 gralloc 的
  `z1_lima_alloc_dmabuf()` 打开 `/dev/dri/renderD128` 会失败。
