# Z1 满血 OVL / DRM 合成 —— 首个上板验证通过的镜像（r11）

> 这是**第一版在真机上跑通"SurfaceFlinger 用 HWC2 DEVICE 合成、OVL 平面直接扫描
> 连续 dma-buf"**的 boot 镜像，连同当时运行的 HAL/gralloc 二进制一起存档。
> 后续（r12–r14）修的是"多层时被小尺寸层拖回 client"的问题，尚在验证中；
> 本目录保存的是**已被板上日志证明可用**的那一份。

---

## 1. 产物与身份

| 文件 | 大小 | sha256 |
|---|---|---|
| `boot.img` | 5,898,240 B | `10e92e2a2536c40b1ec7fa8b10b8542eb32b5db2cb296c9135100c1b10f7da11` |
| `hwcomposer.z1.so` | 145,916 B | `8966bc6618d76666b48c0faed044fadffe7ee0316ee2bd36dfd98b30035090af` |
| `gralloc.z1.so` | 28,548 B | `0ed9fb2b573bfe4bf2e533b7f709ad054a1dc95482d420aca184d6c95823b77d` |
| `z1kmstest` | 122,976 B | `1954132627e18d96cc5fe4d2c2baa652d122aa0c2df75618c94fa57b7820ccaf` |

镜像结构：`kernel_size 4,331,019 = MTK KERNEL 4,330,507 + 512`，
`ramdisk_size 1,563,914 = MTK ROOTFS 1,563,402 + 512`，ramdisk 85 条 cpio 记录，
详见同目录 `pack_boot_report.txt`（打包器的 6 类校验全 PASS；本镜像是**确定性重建**的，
重建结果与当时刷入板子的字节完全一致，sha256 相同）。

## 2. 板上验证到的结果（证据）

启动日志（`z1hwc2` tag 走 logcat，里程碑级映射为 WARN 以便串口 relay 转发）：

```
z1hwc2: open(composer) version 2026-11-10-r11
z1hwc2: opened /dev/dri/card0 fd=7 driver=mediatek (waited 0 ms)
z1hwc2: resources crtcs=1 encoders=1 connectors=1
z1hwc2: crtc=59 connector=34 mode=240x320 240x320@60
z1hwc2: plane[0] id=35 zpos=0 alpha=1 blend=1
z1hwc2: hotplug callback registered, reporting display 0 connected
z1hwc2: setPowerMode 2 -> ON
z1hwc2: all 1 layer(s) on hardware planes, no client target
z1hwc2: validate: layers=1 device=1 client=0 changes=0 frame=0
z1hwc2: first commit done with modeset: 240x320
z1hwc2: frame 2400: device=1 client=unused (all layers on hardware planes) plane0=none
```

`/sys/kernel/debug/dri/0/state`（root 通道读取）：

```
plane[35]: plane-0    crtc=crtc-0    fb=61
    allocated by = composer@2.1-se   format=XB24 little-endian   size=240x320
    pitch[0]=960   offset[0]=0
    obj[0]: size=311296  dma_addr=0x9a280000   (落在 CMA 池 0x9a000000 内)
    crtc-pos=240x320+0+0   src-pos=240.000000x320.000000+0.000000+0.000000
    normalized-zpos=0
```

结合 `/sys/class/power_supply/*`、`dumpsys SurfaceFlinger` 与整机日志另外确认：

* `sys.boot_completed=1`；SurfaceFlinger 只启动一次，composer **零次**异常退出；
* `atomic commit failed` 计数 **0**；长跑 6000+ 帧（≈60 fps）无报错；
* 合成类型 `device=1 client=0`，即**没有 client 合成帧**；
* 平面缓冲 `dma_addr=0x9a280000` 落在 96 MiB CMA 池内，`pitch=960`、`format=XB24`
  与 Android `RGBX_8888` 240x320 完全对应 ⇒ **零拷贝扫描**。

## 3. 这一版做了什么（与旧方案的区别）

1. **HWC2 全函数表**（43 个 descriptor，0 capability）：旧实现死在
   `getFunction(HWC2_FUNCTION_CREATE_VIRTUAL_DISPLAY)`。
2. **DEVICE 决策**：client target 在底平面，DEVICE 层取 z 顶部连续段；
   无 client 层时**用满 4 个 OVL 平面**（`client=0`）。
3. **按能力选 DRM 节点**：打开 `card0..card3`，要求 universal-planes + atomic，
   再取 DRM master（本板 `mediatek = card0`，`lima = card1`）。
4. **连续内存**：gralloc 走 CMA dma-heap（`/dev/dma_heap/default_cma_region`）→
   mediatek dumb → lima → ashmem；并且打开 `/dev/dri/card1` 后立刻 `DROP_MASTER`，
   绝不和 composer 抢。
5. **内核侧**：IOMMU 节点 `status="disabled"` 消除 deferred-probe 超时，
   `mediatek-drm` 从 **35.7 s** 提前到 **0.296 s** 绑定（card1 不再等半分钟）；
   修正 `mtk_crtc` 的 vblank 回调注册顺序（原会以 1/11 概率开机空指针崩溃）。

## 4. 已知限制（这一版没解决的）

* **多层时会退回 client**：状态栏 240x24、导航栏 240x48 小于内核
  `mode_config.min_width/min_height = 64`（`drivers/gpu/drm/mediatek/mtk_drm_drv.c`），
  `drmModeAddFB2` 直接 `-EINVAL`；由于 client 层必须位于 z 最底层，它一被降级
  就把下面所有层一起拖成 client（板上表现为 `device=0 client=4`）。
  r12–r14 用"补齐到 64 行的暂存 buffer"解决，**尚未在板上确认**。
* present/retire fence 仍返回 `-1`；`debug.z1.hwc.fences` 默认 0。
* 未实现 vsync 上报（SurfaceFlinger 用软件模型，日志有
  `Timed out waiting for hw vsync; faking it`）。
* `/dev/mem` 读 OVL 寄存器需要 cmdline `iomem=relaxed`；本镜像把它放在 cmdline
  末尾，被 LK 截断了（后续镜像已挪到最前面）。

## 5. 怎么用

```sh
# 刷 boot 分区（偏移 0x2980000，6 MiB），脚本会逐字节读回校验
z1a9_gpt/scripts/z1deploy/z1flash_image.sh releases/ovl_v83_r11/boot.img --minutes 10

# 单独更新 HAL/gralloc（root 通道，不刷机）
z1a9_gpt/scripts/z1deploy/z1_install_hal.sh install releases/ovl_v83_r11/hwcomposer.z1.so hwcomposer
z1a9_gpt/scripts/z1deploy/z1_install_hal.sh install releases/ovl_v83_r11/gralloc.z1.so gralloc

# 首启取证
Z1LOG=<串口抓取文件> z1a9_gpt/scripts/z1deploy/z1_firstboot_check.sh
```

回滚：普通重启一次（镜像里的一次性安装已消费，`apply` 会删掉 `/vendor` 覆盖），
或 `z1_install_hal.sh rollback both`，或刷回 `out/z1_v79/boot.img`。

## 6. 源码对应关系

HAL 源码在本仓库 `scripts/z1hwc2/`（`z1hwc2.cpp` + `z1_policy.h` + 15 条主机单测）。
本镜像对应的功能集合 = commit `80a2e66` 中的"按能力选 DRM 节点"部分；
该 commit 同时包含之后 r12 的"小层补齐"路径（`small_layer_sync()` / `use_small`），
把这两处去掉即得到本镜像的源码。二进制以此目录内的 `hwcomposer.z1.so` 为准
（sha256 已在上表列出，与板上 `getprop z1stage.hwcomposer.sha` 一致）。

内核侧对应 commit：`1d7b807ae`（IOMMU 早 bind + vblank 注册顺序）与
`856599921`（CONFIG_SW_SYNC）。
