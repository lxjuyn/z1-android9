# Z1 满血 OVL / DRM 合成 —— 4 层全部硬件合成（r14）

> 本目录是**第一个在真机上做到 `device=4 client=0` 的镜像**：SurfaceFlinger 的四层
> （壁纸/桌面、应用窗、状态栏、虚拟导航栏）全部由 MT6572 的 OVL 平面直接扫描
> 物理连续的 dma-buf，**没有 GPU 合成、没有 CPU 拷贝**。

---

## 1. 产物与身份

| 文件 | 大小 | sha256 |
|---|---|---|
| `boot.img` | 5,900,288 B | `6c85b6a0a705f75401ed0b7867b1dd5d80ca30bcc7d93b47b182a5bbf73e0af4` |
| `hwcomposer.z1.so` | 147,176 B | `03d2f200f4ea8f8fd1b4bc8d8dd06c190ef2a23f69811fad875b56296acffa95` |
| `gralloc.z1.so` | 28,548 B | `0ed9fb2b573bfe4bf2e533b7f709ad054a1dc95482d420aca184d6c95823b77d` |
| `z1kmstest` | 122,976 B | `1954132627e18d96cc5fe4d2c2baa652d122aa0c2df75618c94fa57b7820ccaf` |

## 2. 板上证据

`dumpsys SurfaceFlinger` 里的 HAL 摘要：

```
z1hwc2 2026-11-10-r14: 240x320 crtc=59 conn=34 planes=4 layers=4
                        frames=10997 commit_fail=0 device=4 client=0 modeset=1
```

`/sys/kernel/debug/dri/0/state`（root 通道读回，KMS 自己记录的状态）：

```
plane[35] plane-0  fb=61  AB24 240x320  obj.size=311296  dma_addr=0x9a300000  zpos=0
                   crtc-pos=240x320+0+0  src-pos=240.000000x320.000000+0+0
plane[41] plane-1  fb=62  AB24 240x320  obj.size=311296  dma_addr=0x9a200000  zpos=1
plane[47] plane-2  fb=65  AB24 240x320  obj.size=311296  dma_addr=0x9a400000  zpos=2
plane[53] plane-3  fb=66  AB24 240x64   obj.size=61440   dma_addr=0x9a150000  zpos=3
                   crtc-pos=240x48+0+272  src-pos=240.000000x48.000000+0+0
```

* 四个 `dma_addr` 全部落在 **96 MiB CMA 池** `0x9a000000–0x9fffffff` 内 ⇒ 物理连续，
  OVL 直接扫描，**零拷贝**；
* `zpos` 0..3 与平面序号一一对应，叠放顺序正确；
* `plane-3` 是**虚拟导航栏**：它只有 240×48，低于内核
  `mode_config.min_width/min_height = 64`（`mtk_drm_drv.c`）的帧缓冲下限，
  `drmModeAddFB2` 会直接 `-EINVAL`。本版用"补齐到 240×64 的暂存 buffer +
  source 只覆盖 48 行"绕过，所以它也能留在硬件平面上；
* `commit_fail=0`，长跑 10000+ 帧无一次原子提交失败。

## 3. 关键实现（相对 r11）

r11 已经能单层 DEVICE（开机动画），但一进桌面就 `device=0 client=4`。板上日志给出的根因：

```
drmModeAddFB2 failed: Invalid argument (240x24 stride=240p pitch=960 fourcc=AB24)
drmModeAddFB2 failed: Invalid argument (240x48 ...)
```

状态栏 240×24、导航栏 240×48 都小于内核 64×64 的 framebuffer 下限；而 HWC2 的
client 层必须位于 z 最底层（client target 在 plane 0），**一个小层被降级就会把
它下面所有层一起拖成 client**，于是整屏回到 GPU 合成。

r12–r14 的修法：
1. `small_layer_sync()`：把任一维 < 64 的层拷进高度补齐到 ≥64 的 dumb buffer
   （240×48 ≈ 45 KiB），平面指向补齐后的 fb，`src` 只覆盖真实内容 ⇒ 不缩放、
   不露多余像素；拷贝前后对源 dma-buf 做 `DMA_BUF_SYNC_START/END(READ)`；
2. present 里**必须先走小层分支、再尝试普通 import**（r12 的 bug 是顺序反了：
   普通 import 先失败并置 `lost_fb`，下一帧 validate 又把该层降级，修补从未生效）；
3. 无 client 层时用满 4 个 OVL 平面（`client_expected=false`）。

## 4. 仍然存在的限制

* present/retire fence 仍返回 `-1`，`debug.z1.hwc.fences` 默认 0（release fence
  代码已在，需上板开启验证）；
* 未实现 vsync 上报，SurfaceFlinger 用软件模型（日志有
  `Timed out waiting for hw vsync; faking it`）；
* `/dev/mem` 直读 OVL 寄存器需要 cmdline `iomem=relaxed`（本镜像已放在 cmdline
  最前面；LK 会截断尾部，之前放末尾失效）；
* 只有 4 个平面：若某帧层数 > 4，最底下的层会被降级进 client target（预期行为）。

## 5. 用法

```sh
# 刷 boot（偏移 0x2980000，6 MiB），逐字节读回校验
z1a9_gpt/scripts/z1deploy/z1flash_image.sh releases/ovl_v83_r14/boot.img --minutes 10

# 首启取证（自动判读）
Z1LOG=<串口抓取> z1a9_gpt/scripts/z1deploy/z1_firstboot_check.sh

# 在线换 HAL / 回滚
z1a9_gpt/scripts/z1deploy/z1_install_hal.sh install releases/ovl_v83_r14/hwcomposer.z1.so hwcomposer
z1a9_gpt/scripts/z1deploy/z1_install_hal.sh rollback both
```

注意：串口 console 在日志量大时会因 **UART 输入溢出**（`ttyS ttyS0: 1 input overrun(s)`）
而卡住；在线传大文件请用 `z1push.sh` 并放慢节奏，或直接重刷 boot。

## 6. 源码对应

HAL：本仓库 `scripts/z1hwc2/`（`z1hwc2.cpp` 版本串 `2026-11-10-r14`、`z1_policy.h`、
`test/z1hwc2_policy_test.cpp` 15 条主机单测）。
内核：`z1-mt6572-mainline` 仓库 commit `1d7b807ae`（IOMMU 节点禁用让 mediatek-drm
从 35.7 s 提前到 0.296 s 绑定；修 vblank 回调注册顺序）与 `856599921`（CONFIG_SW_SYNC）。
