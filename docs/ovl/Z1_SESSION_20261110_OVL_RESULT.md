# 2026-11-10 会话结论：满血 OVL 达成 + 后续 vsync 试验与板子供电问题

> 本文只记录**有板上证据**的事实，以及截至本文写作时**尚未验证**的部分。
> 上位文档：`Z1_STATUS_OVL_20261110.md`（r11 阶段状态）。

---

## 1. 已达成并上板验证：4 层全部 OVL 硬件合成

镜像 `out/z1_v83/boot.img`（r14，sha256 `6c85b6a0a705f75401ed0b7867b1dd5d80ca30bcc7d93b47b182a5bbf73e0af4`）
在真机上跑出：

```
z1hwc2 2026-11-10-r14: 240x320 crtc=59 conn=34 planes=4 layers=4
                       frames=11009 commit_fail=0 device=4 client=0 modeset=1
```

`/sys/kernel/debug/dri/0/state`（root 通道读回）：

```
plane[35] plane-0  fb=61  AB24 240x320  obj.size=311296  dma_addr=0x9a300000  zpos=0
plane[41] plane-1  fb=62  AB24 240x320  obj.size=311296  dma_addr=0x9a200000  zpos=1
plane[47] plane-2  fb=65  AB24 240x320  obj.size=311296  dma_addr=0x9a400000  zpos=2
plane[53] plane-3  fb=66  AB24 240x64   obj.size=61440   dma_addr=0x9a150000  zpos=3
                   crtc-pos=240x48+0+272  src-pos=240.000000x48.000000+0+0
```

四个 `dma_addr` 都在 96 MiB CMA 池 `0x9a000000-0x9fffffff` 内 ⇒ 物理连续、零拷贝、
零 GPU 合成。用户确认**屏幕不闪、状态栏/导航栏正常**。

### 关键修复（相对 r11 单层可用）

状态栏 240x24 / 导航栏 240x48 小于内核 `mode_config.min_width/min_height = 64`
（`drivers/gpu/drm/mediatek/mtk_drm_drv.c:528`），`drmModeAddFB2` 直接 `-EINVAL`。
而 HWC2 的 client 层必须位于 z 最底层，于是**一个小层被降级就把它下面所有层拖成 client**。

修法（r12 引入、r14 修好）：`small_layer_sync()` 把任一维 < 64 的层拷进高度补齐到
≥64 的 dumb buffer（240x48 ≈ 45 KiB），平面指向补齐后的 fb，`src` 只覆盖真实内容；
拷贝前后对源 dma-buf 做 `DMA_BUF_SYNC_START/END(READ)`。

**r12 的 bug（重要教训）**：present 里"先普通 import、后小层分支"的顺序错，普通 import
必然失败并置 `lost_fb`，下一帧 validate 又把该层降级，修补从未生效。r14 把小层分支
提到 import 之前才真正工作。

---

## 2. r15/r16：vsync 与 fence（**未完成验证**）

### 做了什么

* release fence 复用既有 sw_sync timeline（`debug.z1.hwc.fences` 默认改为 1）；
* 新增 present(retire) fence：第二条 sw_sync timeline，值在下一次 vblank 推进；
* 新增 vsync 线程：`DRM_IOCTL_WAIT_VBLANK` + `HWC2_CALLBACK_VSYNC`，时间戳用内核返回的
  `CLOCK_MONOTONIC`；
* 诊断日志：`fences enabled (release fd=… present fd=… vsync=…)`、`first vsync ts=…`、
  `WAIT_VBLANK failed: …`（一次性）。

### 已定位并修掉的 bug

r15 用 `DRM_VBLANK_RELATIVE` + `sequence = 0` —— 内核 `drm_wait_vblank_is_query()`
（`drm_vblank.c:1685`）判定这是**查询**：`req_seq == seq`，立即返回，线程空转，SF 拿不到节拍，
串口每 ~46 秒出现一次 `Timed out waiting for hw vsync; faking it`。
r16 改为 `sequence = 1`（真正等下一个 vblank）。

### 仍未验证的原因

r16（sha256 `ad3eed1a1b7ff4b05269088571c57412077753634a654d4c6dead9373e5c69d1`）
已刷入板子并读回校验一致，但**后续板子因电量耗尽无法稳定启动**，
`first vsync ts=` 这条里程碑**从未在板上出现过**，所以 r16 的 vsync 是否真的工作
**没有证据**。下次板子能开机时必须先确认这一条。

---

## 3. 板子起不来的真实原因（已排除软件）

### 证据链

| 观察 | 数据 |
|---|---|
| 电池电压 | 全天 4.0–4.29 V（满电时正常），死亡前几次仍是 4.27–4.30 V |
| 放电状态 | `POWER_SUPPLY_STATUS=Discharging`（一直没充进去） |
| USB 侧电压 | `POWER_SUPPLY_VOLTAGE_NOW=520000`（0.52 V，远低于 5 V） |
| 充电驱动 | `charging ENABLED` @2.5s → `charging DISABLED` + `charger unplugged (RGD_CHRDET→0)` @61.9s |
| 启动模式 | 早期日志大量 `[PLFM] USB/charger boot!` → LK 判成插充启动，Android 不启动、黑屏 |
| 刷写失败 | `DeviceClass - USBError(5, 'Input/Output Error')` + `Couldn't send sdmmc_write_data header`，且**每次精确停在 DA stage2 的 4096 字节（2.5%）** |

**结论**：电池被放空 + USB 供电/线缆不良。DA 一旦开始初始化 eMMC，电流抬升导致 USB 掉链。

**软件不可能刷砖**：全程只写 `boot` 分区（偏移 `0x2980000`），每次写完都逐字节读回一致；
preloader / LK / recovery 未写入。板子能反复枚举 `0e8d:2000`（preloader）本身就证明
eMMC 里的 preloader 完好。

### 恢复步骤

1. 换 5V/2A 充电头 + 好数据线，**充 1~2 小时**（板子会进 MTK 关机充电模式，串口静默是正常的）
2. 充好后拔 USB，按电源键正常启动
3. 再刷写（`z1a9_gpt/scripts/z1deploy/z1flash_image.sh <img> --minutes 20`）

### 刷写踩坑记录

* 插着 USB 上电会被 LK 判成 charger boot → 黑屏不启动；**正常开机必须拔掉板子 USB**
* `0e8d:0003` + CDC 接口 = 关机充电/引导器 VCOM，**mtkclient 连不上**（它要 BROM 或 preloader）
* `0e8d:2000` = preloader，是唯一可靠的刷写窗口
* 之前一次"每次 60 秒超时"的假死，换新 preloader 会话后 attempt 1 即成功

---

## 4. 已入库的发布产物

| 目录 | 镜像 sha256 | 状态 |
|---|---|---|
| `releases/ovl_v83_r11/` | `10e92e2a…` | 单层 DEVICE 可用（首个上板验证通过） |
| `releases/ovl_v83_r14/` | `6c85b6a0…` | **4 层全部硬件合成，已验证**（本文 §1） |

两个目录都含 `boot.img` + `hwcomposer.z1.so` + `gralloc.z1.so` + `z1kmstest` +
`SHA256SUMS.txt` + README（证据与限制）。

---

## 5. 下一步（按优先级）

1. **板子充好电后**：先确认 r16 的 `first vsync ts=` 是否出现、`Timed out waiting for hw vsync`
   是否停止增长；同时确认 `device=4 client=0` 仍成立。
2. 若 vsync 仍不工作：改回 r14 行为（不启 vsync 线程），只保留 present fence，
   或改用"轮询 vblank 计数"的方式（不用阻塞 WAIT_VBLANK）。
3. release fence 的 A/B：`debug.z1.hwc.fences=0` vs `1`，看是否有 tearing / 生产者饥饿。
4. 恢复计划：原厂 `1/boot.img`（sha `2dd41702…`，6 MiB）→ 充电 → `out/z1_v79/boot.img`
   （sha `d332b5e9…`，今晚之前一直能进 Android 的那版）。
