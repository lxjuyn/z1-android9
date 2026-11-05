# GPU 路径逐环节核实 — 2026-11-05

> 结论：`in-tree gralloc(真 dma-buf) + Mesa 19.1.8 + egl=mesa` 这条链路
> **内核到用户态每一环都有源码或板上证据**，且**此前从未被测试过**
> （前几版全部用错了 gralloc）。

## 0. 先纠正两个我自己的错误

1. **"dma-buf 是黑屏解药"** —— 错，那次真因是 ashmem 缺失。
2. **"内核缺 `DRM_IOCTL_LIMA_GEM_CREATE_Z1`"** —— 错。`lima_drm.h:157`
   `#define DRM_LIMA_GEM_CREATE 0x01`，而 `lima_allocator.cpp:20-28` 本地声明的
   `struct drm_lima_gem_create_z1` 是 4×u32 = 16 字节，与内核 `drm_lima_gem_create`
   逐字段相同 → `DRM_IOWR(DRM_COMMAND_BASE + 0x01, ...)` 编码出的 ioctl 号一致。
   只是换了个名字，**能用**。
3. **"我的 dma-buf 包装库可以上"** —— 错。它有 5 个缺陷（HW_FB 按 fb 尺寸分配
   163,840 B 而非 307,200 B、fb_post 无格式感知、fd 换掉后 base 仍指 ashmem、
   close 释放 .bss、无 cache sync）。**已弃用**。

## 1. 用户态：in-tree gralloc 就是 dma-buf 实现

`z1a9_gpt/graphics/gralloc/`：
- `gralloc.cpp:153` `fd = z1_lima_alloc_dmabuf(size);`（`#ifdef Z1_USE_LIMA`）
- `gralloc.cpp:163` 失败才回退 `ashmem_create_region`
- `mapper.cpp:183/204` lock/unlock 里 `z1_lima_sync_dmabuf`
- `Android.mk:33` `-DZ1_USE_LIMA=1`

**镜像里那个 25,116 B 的 `gralloc.z1.so` 确实编进了这条路径**：
二进制里有 `Lima dma-buf allocation failed (%s); using ashmem fallback`
和 `/dev/dri/renderD128` 字面量。

**framebuffer 目标不会被错分配**（这正是它比我包装库强的地方）：
`gralloc_alloc` 里 HW_FB 走 `gralloc_alloc_framebuffer`，后者注释写明
"allocate the requested pixel layout, never the physical fb size"，
改用 **shadow buffer** → 240×320 RGBA8888 = **307,200 B**（不是 fb 的 163,840 B）。
Mesa 要求 16 字节对齐：stride = 240×4 = **960**，960 % 16 == 0 ✅

## 2. 内核侧：lima GEM → PRIME → mmap 全通

| 环节 | 结论 | 证据 |
|---|---|---|
| 7 个 ioctl 在 render node 注册 | ✅ `DRM_RENDER_ALLOW` | `drivers/gpu/drm/lima/lima_drv.c:247-255` |
| `GEM_CREATE` 是否需要 ctx | ✅ **不需要**，只要 open 过 | `lima_drv.c:79-93`、`lima_gem.c:108-151` |
| 参数约束 | ✅ pad=0/flags=0/size>0 合法 | `lima_drv.c:83-89` |
| 新对象直接 `PRIME_HANDLE_TO_FD` | ✅ `handle_count != 0` | `drivers/gpu/drm/drm_prime.c:375-407` |
| 随后 `GEM_CLOSE` 安全 | ✅ export 已持引用 | `drm_prime.c:249-250`、`:266` |
| dma-buf `mmap` | ✅ | `lima_gem.c:201-209` → `drm_gem_shmem_helper.c:668-707` |
| `DMA_BUF_IOCTL_SYNC` | ✅ 无操作但返回成功 | `drivers/dma-buf/dma-buf.c:1469-1490` |
| 配置 | ✅ LIMA/GEM_SHMEM/SCHED/DMA_SHARED_BUFFER 全 y | `fork_linux/.config` |

**板上历史证据**：`mainline_recon/z1_lima_uapi_stream_20260717_222242.log:21` `OPEN renderD128 PASS`；
`z1_r3b_boot2_20261025.log:2694-2709` `[drm] Initialized lima 1.1.0 for 13010000.gpu on minor 0`。

## 3. 待验证的唯一硬风险

Android 9 的**动态链接器 namespace 规则**（`ld.config.txt`）是否会挡住
`surfaceflinger` 加载 `/vendor/lib/egl/libGLES_mesa.so`，以及后者再加载
`/vendor/lib/dri/gallium_dri.so`。正在专项审计中。
若被挡，日志里会是明确的 linker 报错（不是"没有 GPU"）。

## 4. 产物（已校验，未上板）

| 分区 | 镜像 | sha256 |
|---|---|---|
| `bootimg` | `out/z1_fix3k_boot/boot.img` | `6ab413d0a9dc769b3c2ed67826b34d8e4c2ff066e30968cd0eadbc8fe7b1cdbf` |
| `android` | `out/z1_v14_gpu_system/system_v14.img` | `f5501e709c4d05e14ea6af11c9a1c7a4544bbfc731219f1eee2da1a6d2c1ea15` |

## 5. 上板判据（顺序不能换）

1. **先看有没有 `Lima dma-buf allocation failed`** —— 有就说明静默回退 ashmem，GPU 等于没开。
   这是"真启用还是假启用"的唯一硬指标，比看 `renderer: Mali400` 可靠。
2. `libEGL: loaded /vendor/lib/egl/libGLES_mesa.so`（而不是 swiftshader）
3. `SurfaceFlinger: vendor=lima / renderer=Mali400`
4. 无 `failed to create DRI image from FD`
5. 稳定不崩（历史教训：换 gralloc 曾导致 SF 每帧 SIGSEGV）

---

# 5. 动态链接器审计结论（2026-11-05 晚，独立复核后定稿）

> 审计 + 独立反驳各跑一轮。反驳方自己去 android.googlesource.com 拉了 r61 源码、
> 自己用 debugfs 从镜像里 dump 了库、自己写了符号解析器。结论：

## 5.1 结论：**不挡**，但机制和原先以为的不一样

镜像 `/etc/ld.config.txt` 与 AOSP r61
`rootdir/etc/ld.config.legacy.txt` **逐字节相同**
（sha256 `f26fd0c967ecc7036a6732059f832ab3e56628c4ef9d831934de4b66fe302ec9`，1588 B）。
`namespace.default.isolated = false`（第 17 行），`is_accessible()` 在非隔离命名空间下
直接返回 true（A9 `linker_namespaces.cpp:36-39`）。镜像里 `/etc` **只有**这一个
ld.config，没有 `.arm.` / `.28.` / `.vndk_lite.` 变体。

`libGLES_mesa.so` 82 个 UND、`gallium_dri.so` 267 个 UND，在镜像自带库的闭包里
**0 个未解析符号**；`e_flags 0x5000200` EABI5 与 ROM 的 `libEGL.so` 一致；无 `DT_TEXTREL`。

## 5.2 **重要纠正：`ro.hardware.egl` 在这台机器上根本不起作用**

A9 `frameworks/native/opengl/libs/EGL/Loader.cpp:483-497`：

```cpp
android_namespace_t* ns = android_getDriverNamespace();
if (ns) dso = load_updated_driver(kind, ns);   // 这条路才读 ro.hardware.egl
if (!dso) dso = load_system_driver(kind);      // 实际走的是这条
```

`android_getDriverNamespace()`（`GraphicsEnv.cpp:85-102`）第一步就是：

```cpp
auto sphalNamespace = android_get_exported_namespace("sphal");
if (!sphalNamespace) return;      // mDriverNamespace 保持 null
```

而 `ld.config.txt` 里**既没有 `namespace.sphal`，也没有任何 `visible` 行**
（命名空间只有 `.visible = true` 才导出，`linker.cpp:3808-3835`）。
⇒ `android_getDriverNamespace()` 恒返回 NULL ⇒ `load_updated_driver()` **永远不会执行**
⇒ **`ro.hardware.egl` 写 mesa 还是 swiftshader 完全不影响选谁。**

真正的选择机制在 `load_system_driver()`（`Loader.cpp:320-461`）：

1. `checkGlesEmulationStatus()` 返回 2（模拟器）才用 `lib<kind>_swiftshader.so`——真机不是。
2. 先找精确名 `/vendor/lib/egl/libGLES.so` —— **不存在**。
3. 再按前缀 **glob** `/vendor/lib/egl`（然后 `/system/lib/egl`），pattern = `libGLES_`：
   - `libGLES_mesa.so` ✅ 命中（`libGLESv1_CM_*` / `libEGL_*` 不匹配该前缀）
4. `load_driver("GLES")` 拿到 dso 后就不再试别的，**只有一条路**。

**实测镜像内容（v14）**：`/vendor/lib/egl/` = `libGLES_mesa.so`(94,488 B) +
`libGLESv1_CM_swiftshader.so` + `libGLESv2_swiftshader.so` + `libEGL_swiftshader.so`。
v16（非 GPU 版）：**没有** `libGLES_mesa.so` → 步骤 3 落空 → 回退到
`load_driver("EGL")` glob `libEGL_` 命中 `libEGL_swiftshader.so`，再用
`libGLESv1_CM_swiftshader.so` / `libGLESv2_swiftshader.so`。这就是它一直能跑的原因。

**推论（可省一次刷写）**：`out/z1_fix3j_boot/boot.img` 与 `out/z1_fix3k_boot/boot.img`
解包后 ramdisk 里**只有 `default.prop` 一行不同**（`ro.hardware.egl=swiftshader` vs `mesa`），
kernel 段逐字节相同。而这一行已被证明无作用 ⇒ **两个 boot.img 功能完全等价**，
GPU 开关 100% 在 system 镜像（有没有 `/vendor/lib/egl/libGLES_mesa.so`）。
以后切 GPU 只需要刷一个分区。

## 5.3 Mesa 侧下一跳也已核实

`libGLES_mesa.so` 里写死 `DRI_DRIVER_DIR = "/vendor/lib/dri"`
（文件偏移 0x11D5C，与 `%.*s/%s_dri.so` 同段），另有 `LIBGL_DRIVERS_PATH` 可覆盖。
镜像里 `/vendor/lib/dri/lima_dri.so` 是指向 `gallium_dri.so`(4,662,060 B) 的符号链接
（fast symlink，dest 长度 14，已 `debugfs stat` 确认）。
驱动名由 `drmGetVersion()` 反查，内核 `lima_drv.c` `.name = DRIVER_NAME = "lima"`
⇒ `loader_get_driver_for_fd()` 得 `"lima"` ⇒ `dlopen("/vendor/lib/dri/lima_dri.so")`。
（`MESA_LOADER_DRIVER_OVERRIDE` 的变量名字符串在库里，但 `lima` 这个值**不在**——
驱动名是运行时查出来的，不是编进去的。）

## 5.4 gralloc 选谁也已核实（原先是个硬币）

镜像里有两个 gralloc：

| 路径 | inode | 大小 | 实现 |
|---|---|---|---|
| `/system/lib/hw/gralloc.z1.so` | 468 | 25,116 | **dma-buf**（有 `z1_lima_alloc_dmabuf`） |
| `/vendor/lib/hw/gralloc.default.so` | 1011 | 25,024 | 旧 ashmem（无 z1_lima_* 符号） |

A9 `hardware.c:171-226` 顺序：`ro.hardware.gralloc` → 变体 `ro.hardware`,`ro.product.board`,
`ro.board.platform`,`ro.arch` → 最后才 `default`；每个变体按 `/odm/lib/hw`→`/vendor/lib/hw`→
`/system/lib/hw` 找。
板上 `ro.product.board=z1`（`/vendor/build.prop` 实测）、`ro.hardware=z1`
（`androidboot.hardware=z1`）⇒ 命中 `gralloc.z1.so`（dma-buf 那版）✅，
`gralloc.default.so` 只有在这些 prop 全失效时才会被选中——它是个隐患，不是当前路径。

## 5.5 剩下的风险点（都在"能不能真的初始化成功"，不在加载）

1. `/dev/dri/renderD128` 必须存在且 `0666 root graphics`
   —— `ueventd.rc:68` `/dev/dri/* 0666 root graphics` ✅，内核 `CONFIG_DRM=y DRM_LIMA=y`
   且 `System.map` 有 169 个 lima 符号、历史日志有 `[drm] Initialized lima 1.1.0 for 13010000.gpu`。
2. `eglInitialize()` 是否成功。**注意**：A9 `egl_display.cpp:145-283` 里 `eglInitialize` 失败
   只打 `ALOGW`，然后照样 `return EGL_TRUE`；真正 fatal 在后面
   `eglChooseConfig`/`eglCreateContext` 返回 0 → `RenderEngine::create()` 得 null →
   SF `LOG_ALWAYS_FATAL`。所以**排障看 `eglInitialize(...) failed` 这行 W**。
3. **没有 SwiftShader 回退**：`egl.cpp` 只缓存一次 `cnx->dso`，任何地方都不重试。
   mesa 挂了就是 SF 死循环，只能重刷 system。这是已知且已接受的代价。
4. `gl_unimplemented` 缺失入口**会**打 `ALOGE "called unimplemented OpenGL ES API"`（限流 1/s），
   不是静默——日志能看出来。

## 5.6 顺带纠正的两条旧认识

- `/system/etc/prop.default`（inode 2691，181 B）里写的是 `ro.hardware.egl=swiftshader`，
  `/vendor/build.prop`、`/build.prop` 也是。它们**全都不生效**：init 的
  `property_service.cpp:681-686` 是 `if/else` 链，先读 `/system/etc/prop.default`——
  但那时 `/system` 还没挂（`property_load_boot_defaults()` 早于所有 trigger），
  所以读到的是 ramdisk 的 `/default.prop`。旁证：`ro.zygote` 只存在于 ramdisk
  `default.prop:34`，而 zygote 确实起来了。
  ⇒ 结论仍然成立（ramdisk 的 default.prop 赢），但**赢的那一行的内容无所谓**。
- 我之前把 `ro.hardware.egl` 当成"GPU 开关的关键"，这是错的。真正的开关是
  **`/vendor/lib/egl/` 里有没有 `libGLES_mesa.so` 这个文件**。
