# GPU 版开机的两个阻塞点（2026-11-08 上板实测）

## 一、好消息：GPU 真的生效了

板上用串口 console shell 执行 `dumpsys SurfaceFlinger`：

```
GLES: Mesa, Mali400, OpenGL ES 2.0 Mesa 26.1.1
EGL implementation : 1.4
Sync configuration: [using: EGL_ANDROID_native_fence_sync EGL_KHR_wait_sync]
```

并且 **`gp error irq` 从 19.1.8 的 20 次降到 0**。这与"19.1.8 的 PLBU scissor 下溢被
Mesa 26 的上游修复（ab49a0e7 删掉那条 clear 路径）消掉"完全一致。

## 二、阻塞点 1：GPS HAL 在 system_server 主线程里阻塞 → Watchdog 反复杀系统

```
Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: Blocked in handler on main thread (main)
  at com.android.server.location.GnssLocationProvider.class_init_native(Native Method)
  at com.android.server.location.GnssLocationProvider.<clinit>(GnssLocationProvider.java:2716)
  at com.android.server.location.LocationManagerService.loadProvidersLocked
  at com.android.server.location.LocationManagerService.systemRunning
  at com.android.server.SystemServer.lambda$startOtherServices$4(SystemServer.java:1917)
```

- 这一次开机里 **6 次** Watchdog 杀进程，其中 **5 次**是这条栈。
- 机制：`gps.default.so` 打开时要连 mnld 的 socket；mnld 早已因为
  `/sys/class/gpsdrv/gps/status` 不存在而退出，于是这个 native 初始化**阻塞**，
  30 秒后 Watchdog 把 system_server 整个杀掉，框架重启，再阻塞……
- **这解释了历史上"开机 1172 秒"的怪现象**：不是单纯没有 boot image，是 system_server 被反复杀。
- 修法（已做，未上板）：`out/z1_v26_nognss/system_v26.img`（sha256 `0b734967…`）删掉了
  `gps.default.so`、`gnss@1.0-service`、`gnss@1.0-impl.so`、gnss service rc。
  没有 HAL 时 `hw_get_module("gps")` 立即失败，provider 直接禁用，不再阻塞。
  代价：GPS 不可用（本来也不可用）。

## 三、阻塞点 2（新发现）：SystemUI 在 `ShadowKeyDrawable.draw()` NPE 崩溃

```
java.lang.NullPointerException: Attempt to invoke virtual method
  'boolean android.graphics.Bitmap.isRecycled()' on a null object reference
    at android.graphics.BaseCanvas.throwIfCannotDraw(BaseCanvas.java:61)
    at android.view.DisplayListCanvas.throwIfCannotDraw(DisplayListCanvas.java:226)
    at android.view.RecordingCanvas.drawBitmap(RecordingCanvas.java:98)
    at com.android.systemui.statusbar.phone.ShadowKeyDrawable.draw(ShadowKeyDrawable.java:88)
```

- 出现 17 次，全部是 `com.android.systemui`，从 221 s 开始（**早于**第一次 Watchdog 杀）。
- **只在 GPU 真正生效的这次开机出现**：之前的三个镜像里 `ShadowKeyDrawable` 命中 0 次。
- 现有数据：内存不是瓶颈（GPU 那次 free 358 MB，比无 GPU 时的 260 MB 还多，因为不再跑 dex2oat）。

## 四、其它已知的 EGL 异常（待判）

- `EGL-MAIN: failed to get driver name for fd -1` 出现 18 次，涉及 pid 475(SurfaceFlinger)、
  850(bootanimation)、3497(launcher)、5598(systemui) 等。**SurfaceFlinger 后来还是拿到了 lima**
  （dumpsys 为证），说明这个失败是启动早期的某次尝试，不是终态。
- `bootanimation` 最终没起来（用户没看到二屏），`eglMakeCurrent ... EGL_BAD_MATCH`。
- `Typeface: Unable to load Family: null` 20 次（字体加载失败，待判是否影响）。
- `hwservicemanager: Cannot find entry android.hardware.vibrator@1.0` /
  `audio.effect@4.0` / `vendor.lineage.trust@1.0` —— 镜像里没有这些 HAL，属预期噪音。

## NOT VERIFIED
- v26 是否真的消除了 Watchdog 杀（未上板）。
- SystemUI 崩溃是否只是 GPU 打开的次生现象（两个 agent 在查）。
- GPU 生效后"是不是明显更流畅"只有用户主观判断。

## 五、为什么"Phone is starting…"退不掉（补充）

`mFocusedApp` 一直是 `com.android.settings/.FallbackHome`，而 FallbackHome 只在真正的
launcher 可用后才退出。launcher3 自己崩在 workspace 加载：

```
OpenGLRenderer: Device claims wide gamut support, cannot find matching config, error = EGL_SUCCESS
OpenGLRenderer: Could not create EGL image, err = EGL_SUCCESS
LoaderCursor: Failed to load icon for info ShortcutInfo(...)
LoaderCursor: java.lang.NullPointerException: Attempt to get length of null array
```

串起来就是：**应用进程拿不到 GPU → `eglCreateImageKHR` 失败 → 图标/阴影位图为 null →
launcher 与 SystemUI 双双 NPE**。

而 `failed to get driver name for fd -1` 在这一次开机里出现 **18 次**，牵涉
pid 475(SurfaceFlinger)、850(bootanimation)、3497(launcher)、5598(systemui) 等；
但 SF 最终仍在 `dumpsys SurfaceFlinger` 里报 `GLES: Mesa, Mali400`。

⇒ 真正的待解问题变成了一个：**为什么 SurfaceFlinger 能拿到 DRM 设备，普通应用进程拿不到。**
这是当前 GPU 版"能用"与"不能用"的分水岭。

## 六、应用侧失败的技术线索（Mesa 26 的 gralloc 抽象层）

- Mesa 26 把 Android 的 gralloc 访问重构到了 `src/util/u_gralloc/`，后端依次尝试
  imapper5 → imapper4 → cros → libdrm → **fallback**。
- 板上日志里的 `MESA: Gralloc doesn't support lock_ycbcr` 来自
  `u_gralloc_fallback.c:60` ⇒ **实际用的是最后的 fallback 后端**（前面的都没成功）。
- fallback 是通用实现：`data[0]` 当 fd、`pixel_stride`/`hal_format` 由调用方给，
  所以**不依赖我们 private_handle_t 的具体布局**；`lock_ycbcr` 缺失只是警告。
- 之后 `eglCreateImageKHR` 仍然失败（`OpenGLRenderer: Could not create EGL image, err = EGL_SUCCESS`），
  说明问题在**下一跳**：lima 的 DRI 前端对 dma-buf 导入的支持（format/modifier）。
- 另注：Mesa 24.0.9 同样有 u_gralloc，且 `lima_pack_clear_plbu_cmd` 那条 bug 在 24 里也已删除，
  所以"退回 24"不会改变应用侧这个失败，只会换来更老的驱动。

待验证：lima 是否支持 `EGL_EXT_image_dma_buf_import`（`eglQueryString` 里有没有这个扩展），
以及 `dri2_create_image_khr` 在 lima 上返回什么错误。
