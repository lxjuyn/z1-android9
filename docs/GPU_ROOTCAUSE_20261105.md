# GPU「彩虹条」根因重定位 — 2026-11-05 深夜（6 个 agent 深挖后的结论）

## 0. 先推翻项目里那条旧结论

`out/STATUS_20261031.md:12,77` 写的是：**「GPU 416MHz 未配 VGPU 电压 → lima 渲染输出坏像素 → 彩虹条」**。
这条**不成立**，三条独立证据：

1. **同一块板子、同一条内核路径，Mali-400 已经成功算对过**：
   `mainline_recon/z1_lima_offscreen_command_submit_20260717_231356.log`
   `:23 before_runtime=suspended` → GP/PP 中断计数 0→1 → `:24 GL_RENDERER=Mali400`
   → `:26-30 CORNER_RGBA=17,34,51,255 / CENTER_RGBA=60,65,130,255 / CORNER_CLEAR_CHECK=PASS /
   TRIANGLE_CENTER_DIFF_CHECK=PASS`。像素是对的。
2. **lima 根本不调电压**：`drivers/gpu/drm/lima/lima_device.c:189`
   `devm_regulator_get_optional(dev, "mali")`，拿不到 `-ENODEV` 就 `return 0`（`:163-177` 使能也是 no-op）。
   MT6572 的 Mali-400 **没有独立 VGPU 轨**，走 `buck_vproc`，而 Z1 的 dts 已经把它抬到
   `regulator-min-microvolt = <1150000>`（`mt6572-z1.dts:615-617`）。
3. 没有 `operating-points-v2`，所以 lima 的 devfreq 整个跳过（`lima_devfreq.c:152-154`），
   GPU 恒在 416 MHz —— 这是**性能上限**，不是功能故障。

## 1. 板上真正观测到的 GPU 故障

`mainline_recon/z1_android9_acm_20261020_190823_409363.log` 里 20 条：
`17 × gp error irq state=4000 status=3` + `3 × gp error irq state=4001 status=1`。
bit 14 = `LIMA_GP_IRQ_PLB_INVALID_CMD` —— **GP 执行到一条非法命令流被中止**，
不是 MMU 错、不是超时、不是掉电。也就是说：Mesa 19.1.8 交给 GPU 的命令流本身有问题。

## 2. 更可能的直接原因：**GPU→CPU 缓存不一致**

- `lima_allocator.cpp:79-93` 的 `DMA_BUF_IOCTL_SYNC` 参数是对的（START/END + READ/WRITE），
  但内核侧 **`drm_gem_prime_dmabuf_ops` 没注册 `begin_cpu_access`/`end_cpu_access`**
  （`drivers/gpu/drm/drm_prime.c:828-837` 只有 attach/detach/map/unmap/mmup）。
- 于是 `__dma_buf_begin_cpu_access()`（`drivers/dma-buf/dma-buf.c:1433-1449`）**只做一次 fence wait，
  不做任何 cache 维护**。
- 后果：GPU 写进 dma-buf 的像素留在 GPU/缓存层次里，**CPU 侧（fb_post 的 memcpy、SurfaceFlinger 读回）
  读到的是过期的缓存行** → 屏幕上是彩色噪声条（"彩虹"）。
- 这正好解释"为什么只有装 Mesa 才彩虹"：SwiftShader 是 CPU 自己写的，缓存天然一致；
  换 GPU 写就露馅。

## 3. 第二个候选：gralloc 拒绝 `HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED`(34)

`z1a9_gpt/graphics/gralloc/gralloc.cpp:196-215` 的 switch 只接受 1/2/3/4/0x16/0x20，
其他一律 `-EINVAL`。而 Android 9 常用 34 给 app/video 层，Mesa 自己把它映射成 RGBX_8888
（`platform_android.c:113-118,141-146`）。两边对不上 → 分配失败 → 黑/坏。

## 4. 已核实**不是**问题的

- Mesa 19.1.8 的 droid 后端只调 `hw_get_module("gralloc")`，然后**直接从 `native_handle.data[0]` 取 fd**
  （`platform_android.c:170-181`）；我们的 `private_handle_t` 是 fd 在首位、`sNumFds=1` ✅
- stride 语义：Mesa 用 `pitch = stride * bpp`（`:918`），要的是**像素 stride**，我们返回的就是像素 stride ✅
- `perform()`：19.1.8 只在 `#ifdef HAVE_DRM_GRALLOC` 下调用，我们这份没编进去，二进制里 `nm -D -u | grep gralloc` 为空 ✅
  （composer/mapper/allocator 三个 HAL 也都不碰 perform 槽位）
- UAPI：7 个 ioctl 号 0xc0106440/41/42、0x40306443、0x40106444、0x80086445、0x40086446，
  结构体 16/16/16/48/16/8/8 字节，内核头与 Mesa 用的完全一致；且内核**不按版本号 gate ioctl**，
  不存在"版本不匹配返回 -EINVAL"这回事 ✅
- 内核侧齐全：lima 内建（169 符号）、MFG 电源域在 probe 时上电两次、寄存器窗口 0x10000 覆盖全部 IP ✅

## 5. 结论 / 下一步

彩虹条 = **Mesa/lima 这条路径输出到 dma-buf 后，CPU 侧读到不一致的数据**（以及 34 号格式被拒这个隐患），
**不是电压**。所以"降频试电压"那条实验没必要做。

排序后的修法：
1. **给 lima 的 GEM dma-buf ops 补 `begin_cpu_access`/`end_cpu_access`**（`lima_gem.c` /
   `drm_prime.c` 的通用实现），或让 gralloc 用 `mmap` + 显式 cache 操作。这是最可能一击命中的。
   → 属内核改动，需要重编 zImage。
2. gralloc 接受 `HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED`(34)，按 RGBX_8888 处理。
3. 若要重编 Mesa：仓库里 `mesa_src` 是 **24.0.9**（不是出货的 19.1.8），重编必须加
   `-Ddri-search-path=/vendor/lib/dri`（否则会去找 `/usr/local/lib/dri`，这是 r5h 那次的翻车原因），
   并且要保留 `swrast`/`kms_swrast` 才有软回退。

## NOT VERIFIED

- 没有任何一次板测直接证明"GPU 写完后 CPU 读到旧数据"。这是由代码（缺少 begin/end_cpu_access）
  推出的机制，不是实测。验证方法：写一个最小程序，GPU 渲染已知图案到 dma-buf，CPU 立刻读回比对；
  在**不**调用 DMA_BUF_IOCTL_SYNC 与调用后各测一次，差异即证据。
- `PLB_INVALID_CMD` 与彩虹是不是同一件事，未确认。
