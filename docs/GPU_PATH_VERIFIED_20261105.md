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
