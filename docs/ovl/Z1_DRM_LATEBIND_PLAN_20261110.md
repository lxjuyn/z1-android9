# Z1：mediatek-drm 延迟 35 秒 bind —— 根因定案与实验计划

- 日期：2026-11-10
- 日志：`/home/lxj/claude/6572/mainline_recon/z1_boot_20261109_1838.log`（8,248,193 B / 76,735 行 / **11 次完整启动**，内核 `7.0.0-rc7-z1+ #155`）
- 源码树：`/home/lxj/claude/6572/fork_linux`（当前 HEAD `856599921`，比日志内核新：日志里那批 `Z1RET/Z1DUMP/Z1ROUTE/Z1DSI: port1 lookup` 打印在当前树里**已不存在**）
- 任务：把 `mediatek-drm` 的 bind（`/dev/dri/card1` 出现）从 ~35 s 提前到 <5 s

**证据等级标记（全文强制区分）**

| 标记 | 含义 |
|---|---|
| `[源码]` | 直接读到 `fork_linux` 源码，给出 `file:line` |
| `[日志]` | 日志原文，给出日志行号与内核时间戳 |
| `[推导]` | 由上面两类事实推出，逻辑链写明 |
| `[未证实]` | 明确不知道，需要实验 |

---

## 0. 结论摘要（只读这段也能行动）

**定案结论：这不是竞态，也不是 DSI/PHY/面板/时钟/regulator 的问题。是 OVL 和 RDMA 两个平台设备的 probe 被 IOMMU 的 `-EPROBE_DEFER` 挡在门外，而这个内核根本没有编译 IOMMU 驱动；于是只能等 `drivers/base/dd.c` 的 deferred-probe 超时（基准 10 s，之后每注册一个 driver 就顺延 10 s）到点强制放行，才走 `component_add()` → 组件聚合完成 → `mtk_drm_bind()` → `drm_dev_register()` → `/dev/dri/card1`。**

三条决定性证据（细节见 §1）：

1. **[日志]** 11 次启动里，`deferred probe timeout, ignoring dependency` 与 `bound 14007000.ovl` 的间隔是 **4–42 ms**，11/11 全部如此。bind 时刻 == 超时时刻。
2. **[源码]** `drivers/iommu/iommu.c:3005-3006`：找不到 IOMMU provider 时直接 `return driver_deferred_probe_check_state(dev)`，而 `dev` 是**消费者**（OVL/RDMA），所以告警前缀是 `mediatek-disp-ovl 14007000.ovl`。
3. **[源码+日志]** `.config:5602` `# CONFIG_MTK_IOMMU is not set`，而 `mt6572.dtsi:586/594` 给 ovl/rdma0 写了 `iommus`；日志里 `mtk-power-controller ...: sync_state() pending due to 10203000.iommu` 是 IOMMU 设备从未 probe 的铁证。

**可行性与代价**：所有组件在 **0.29 s** 时就已经就绪（面板 `[0.294780]`、DSI 的 `next_bridge ok` `[0.295423]`、master 注册 match `[0.289809]`）。因此去掉 IOMMU 这道坎后，预期 bind 落在 **0.3 s 量级**，<5 s 的目标大幅超额完成。改动量：**DTS 一行**（+ 一条必须一起做的 IRQ 加固）。

**但要说清楚**：这条路**不能**修好当前的 composer / 黑屏问题。日志显示 composer 在 card1 出现**之后**仍然每 5 秒 `exited with status 1`（一路到 1671 s）。早 bind 的真实收益是"确定性（29.28–35.68 s 的 6.4 s 抖动 → 常量）+ 消掉 24–35.8 s 这个 card1 必然缺席的 11.8 s 窗口 + 省掉诊断工具 16 s 的空等"；**那个 1/11 的启动 panic 要靠 §3 E6 的 IRQ 加固单独修，早 bind 本身不修它**（详见 §1.6）。

---

## 1. 证据

### 1.1 11 次启动实测：bind 时刻恒等于 deferred-probe 超时时刻

`[日志]` 用 `grep -a` 逐条取时间戳（11 组，行号为日志行号）：

| # | 日志行 | `deferred probe timeout, ignoring dependency` | `bound 14007000.ovl` | 差值 | 之后 |
|---|---|---|---|---|---|
| 1 | 1726→1728 | 29.920543 | 29.924820 | +4.3 ms | **panic**（30.040779） |
| 2 | 3513→3515 | 30.570860 | 30.582272 | +11.4 ms | `Initialized mediatek` 30.683652 |
| 3 | 18216→18218 | 33.117827 | 33.122050 | +4.2 ms | 33.156473 |
| 4 | 28333→28335 | 31.197753 | 31.201869 | +4.1 ms | 31.211011 |
| 5 | 32815→32818 | 33.769262 | 33.811747 | +42.5 ms | 33.853553 |
| 6 | 37327→37329 | 35.038739 | 35.043026 | +4.3 ms | 35.062901 |
| 7 | 45325→45327 | 31.838146 | 31.842951 | +4.8 ms | 31.884173 |
| 8 | 50884→50886 | 29.278006 | 29.282074 | +4.1 ms | 29.294763 |
| 9 | 52977→52980 | 33.127799 | 33.134825 | +7.0 ms | 33.202270 |
| 10 | 55277→55279 | 33.757769 | 33.761841 | +4.1 ms | 33.789609 |
| 11 | 64431→64433 | 35.678189 | 35.682621 | +4.4 ms | 35.763015 |

计数核对：`bound 14007000.ovl` = 11 次、`bound 14008000.rdma` = 11 次、`bound 1400c000.dsi` = 11 次、`deferred probe timeout` = 22 行（11 设备×2）、`Initialized mediatek` = 10 次（启动 #1 panic 掉了）。

> **判读**：如果延迟是"某个 race 或 workqueue 抖动"，不可能 11/11 都恰好卡在同一条告警后 4 ms。这条告警就是放行开关。

### 1.2 最后一次启动（= 任务描述里 35.68 s 那次）的精确有序事件

`[日志]` 全部取自日志 63309 行之后：

```
[    0.287385] mediatek-drm mediatek-drm.2.auto: Adding component match for /soc/ovl@14007000
[    0.288602] mediatek-drm mediatek-drm.2.auto: Adding component match for /soc/rdma@14008000
[    0.289809] mediatek-drm mediatek-drm.2.auto: Adding component match for /soc/dsi@1400c000
[    0.294780] st7785m 1400c000.dsi.0: Sitronix ST7785M 240x320 1-lane DSI panel probed
[    0.628339] Run /init as init process
[   20.546472] init: starting service 'exec 13 (/z1drmtest)'...
[   20.647754] z1drm: ==== z1drmtest start ====
[   20.566243] init: starting service 'exec 14 (/system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so)'...
[   23.991621] init: starting service 'vendor.hwcomposer-2-1'...
[   24.035584] init: starting service 'surfaceflinger'...
[   34.098798] init: Service 'vendor.hwcomposer-2-1' (pid 478) exited with status 1
[   35.678189] mediatek-disp-ovl 14007000.ovl: deferred probe timeout, ignoring dependency
[   35.680369] mediatek-disp-rdma 14008000.rdma: deferred probe timeout, ignoring dependency
[   35.682621] mediatek-drm mediatek-drm.2.auto: bound 14007000.ovl (ops 0xc0a77fcc)
[   35.683648] mediatek-drm mediatek-drm.2.auto: bound 14008000.rdma (ops 0xc0a78f6c)
[   35.684704] mediatek-drm mediatek-drm.2.auto: bound 1400c000.dsi (ops 0xc0a7da58)
[   35.763015] [drm] Initialized mediatek 1.0.0 for mediatek-drm.2.auto on minor 1
[   35.773024] mediatek-drm mediatek-drm.2.auto: [drm] fb0: mediatekdrmfb frame buffer device
[   35.774581] mtk-dsi 1400c000.dsi: Z1DSI: route B -- keeping LK video stream; no DSI reconfiguration from Linux
[   35.775918] st7785m 1400c000.dsi.0: Z1DSI: route B -- panel already initialised by LK, skipping reset + DCS init
[   37.060210] z1drm: waited 16s for the KMS node
[   37.066371] z1drm: ---- /dev/dri/card1 fd=4 driver=mediatek ----
```

同一窗口里 `[日志]` 还显示 composer **重复崩**（card1 已经在 35.77 出现了）：

```
[   39.110731] init: starting service 'vendor.hwcomposer-2-1'...
[   39.221457] init: Service 'vendor.hwcomposer-2-1' (pid 815) exited with status 1
[   44.115180] ... (pid 834) exited with status 1
[   49.122825] ... (pid 847) exited with status 1
[ 1671.370308] ... (pid 14703) exited with status 1
```

### 1.3 每个组件的 probe 到底做了什么（源码级）

| 设备 | 何时 probe | probe 里最关键的事 | 证据 |
|---|---|---|---|
| `14000000.syscon` (mmsys) | ~0.28 s | ioremap；注册 `clk-mt6572-mm` 子 pdev；注册 `mediatek-drm` 子 pdev | `[源码]` `drivers/soc/mediatek/mtk-mmsys.c:394-453`（clks pdev = :436，drm pdev = :445） |
| `mediatek-drm.N.auto` (master) | 0.287 s | 遍历 mmsys 兄弟节点，`drm_of_component_match_add()` 加 3 个 match，`component_master_add_with_match()` | `[源码]` `mtk_drm_drv.c:1144-1302`；match 添加在 `:1266-1269`，master 注册在 `:1289`；`[日志]` 0.287/0.288/0.289 三行 |
| `14007000.ovl` | **0.29 s 被 defer**；真正 probe 在 29.2–35.7 s | `devm_clk_get(dev,NULL)`（`&mmsys CLK_MM_DISP_OVL`）、ioremap、`devm_request_irq()`、`component_add()` | `[源码]` `mtk_disp_ovl.c:637-684`（clk `:652`，request_irq `:670`，component_add `:677`） |
| `14008000.rdma` | **0.29 s 被 defer**；真正 probe 在 29.2–35.7 s | 同上 + probe 里主动清中断（`RDMA_INT_ENABLE/INT_STATUS` 写 0） | `[源码]` `mtk_disp_rdma.c:354-412`（清中断 `:391-392`，request_irq `:394`，component_add `:405`） |
| `1400c000.dsi` | 0.293 s（正常） | `devm_clk_get`×3、`devm_phy_get(dev,"dphy")`、`mipi_dsi_host_register()`；**component_add 不在 probe 里**，在 `host_attach` 里 | `[源码]` `mtk_dsi.c:1592-1656`（phy `:1625`，host_register `:1640`）；`mtk_dsi.c:1264-1299`（next_bridge `:1274`，component_add `:1291`） |
| `1400c000.dsi.0` (panel) | 0.293 s（正常） | `devm_regulator_get_optional("vcc")`、`devm_gpiod_get_optional("reset")`、`mipi_dsi_attach()` → 触发 DSI 的 `host_attach` → DSI 才 `component_add` | `[源码]` `panel-sitronix-st7785m.c:395-454`（regulator `:418`，attach `:449`）；`[日志]` `[0.294780] Sitronix ST7785M ... probed` |
| `10203000.iommu` | **永不 probe** | 没有驱动 | `[源码]` `.config:5602`；`[日志]` `sync_state() pending due to 10203000.iommu` |
| `1000e000.smi` / `14010000.larb` | **永不 probe** | `.config` 里没有对应 config 符号 | `[日志]` 同上两行的 `.smi` / `.larb` |

`[源码]` `mediatek-drm.2.auto` 这个名字不是 DT 节点名，而是 `platform.c:726` 的 `dev_set_name(dev, "%s.%d.auto", ...)`（`PLATFORM_DEVID_AUTO`），由 `mtk-mmsys.c:445` 创建 —— 这解释了为什么日志里 master 的设备名前缀和 DT 里找不到对应节点。

### 1.4 精确的因果链（每一跳都有 file:line）

```
OVL/RDMA 平台设备被 of_platform 创建后首次 probe
  → drivers/base/dd.c:703-704   dev->bus->dma_configure(dev)      ← 注意：在 probe 之前
  → drivers/base/platform.c:1451/1497   platform_dma_configure → of_dma_configure（`:1497` 把它挂在 platform_bus_type.dma_configure）
  → drivers/of/device.c:152     ret = of_iommu_configure(dev, np, id)
  → drivers/iommu/of_iommu.c:62-79   解析消费者 DT 的 "iommus"（ovl/rdma 有，dsi 没有）
  → drivers/iommu/of_iommu.c:31  iommu_fwspec_init(dev, of_fwnode_handle(np))
  → drivers/iommu/iommu.c:3002   iommu = iommu_from_fwnode(...)  == NULL（CONFIG_MTK_IOMMU 没编，没人注册）
  → drivers/iommu/iommu.c:3005-3006  return driver_deferred_probe_check_state(dev)
  → drivers/base/dd.c:289-302    超时未到 → -EPROBE_DEFER；超时已到 → dev_warn("deferred probe timeout, ignoring dependency") + -ETIMEDOUT
  → drivers/of/device.c:153-158  只有 -EPROBE_DEFER 才中止；-ETIMEDOUT 被当作"没有 IOMMU，继续"
  → drivers/base/dd.c:721        call_driver_probe() ← 到这里 OVL/RDMA 的 probe 函数体才第一次被调用
  → mtk_disp_ovl.c:677 / mtk_disp_rdma.c:405   component_add()
  → try_to_bring_up_aggregate_device()（组件聚合，见 boot#1 栈回溯）
  → mtk_drm_drv.c:688-745        mtk_drm_bind → drm_dev_register(745) → card1
```

`[日志]` boot#1 的栈回溯把这条链的**后半段**完整打出来了（这是唯一一次 panic 的副产品，但对定案极有价值）：

```
[   29.943107] Workqueue: events_unbound deferred_probe_work_func
[   29.987773]  mtk_crtc_create from mtk_drm_bind+0x548/0x6a8
[   29.988505]  mtk_drm_bind from try_to_bring_up_aggregate_device+0x168/0x1c4
[   29.989422]  try_to_bring_up_aggregate_device from __component_add+0x98/0x13c
[   29.990358]  __component_add from mtk_disp_rdma_probe+0x144/0x1a4
[   29.991163]  mtk_disp_rdma_probe from platform_probe+0x5c/0x90
[   29.998335]  bus_probe_device from deferred_probe_work_func+0x78/0xa4
[   29.999179]  deferred_probe_work_func from process_one_work+0x174/0x304
```

> 这段证明：**bind 是在 `deferred_probe_work_func` 里、由 RDMA 的 `component_add()` 同步触发的**，触发者就是 deferred-probe 机制本身。

### 1.5 为什么是 29–36 s，而不是别的数（算术）

`[源码]` `drivers/base/dd.c`：

| 行 | 内容 | 含义 |
|---|---|---|
| `:261` | `static int driver_deferred_probe_timeout = 10;`（`#ifdef CONFIG_MODULES`） | 基准 10 s；`.config:744` `CONFIG_MODULES=y` 成立 |
| `:274` | `__setup("deferred_probe_timeout=", ...)` | 可用内核 cmdline 覆盖；日志 cmdline 里**没有**这一项 |
| `:345-376` | `late_initcall(deferred_probe_initcall)`；`:354` `initcalls_done = true`；`:363-364` 再 trigger+flush 一次；`:366-369` `schedule_delayed_work(timeout*HZ)` | 定时器**从 late_initcall 起算**，不是从 0 s 起算 |
| `:324-336` | `deferred_probe_extend_timeout()`：只要 work 还在排队，就 `schedule_delayed_work(..., timeout*HZ)` 重排 | **每调一次就把超时点推到"现在 + 10 s"** |
| `drivers/base/driver.c:258` | `driver_register()` 末尾调用 `deferred_probe_extend_timeout()` | 所以**每注册一个 driver（含每个 insmod）都把定时器顺延 10 s** |
| `:305-321` | 超时 work：`:311` `driver_deferred_probe_timeout = 0`；`:312` trigger；`:313` flush；`:320` `fw_devlink_probing_done()` | 到点后把 timeout 清零 → 此后 `check_state()` 一律返回 -ETIMEDOUT |

**所以 35 s = "最后一次 `driver_register()` 之后 10 s"**。日志显示 userspace 在 13–30 s 之间一直在 `insmod`（例如 `[29.567940] mtk_stp_GPS_chrdev driver(major 191) installed.`、`[29.654358] [z1gps] insmod OK`），每次 insmod 里的 `platform_driver_register()` 都会续期，于是一路滑到 29–36 s。

> `[未证实]`：**每一次启动里究竟是哪一个 `driver_register()` 最后续的期**，我没有逐条证实。这不影响根因定案（C1 已由 §1.4 的链证明），只影响"为什么这次是 35.68 而不是 32"。§3 的 E2 可以把这条完全钉死。

### 1.6 附带发现 A：boot#1 的 panic 是**第二个、独立**的 bug

`[日志]`（boot#1，29.92–30.07）：

```
[   29.928664] 8<--- cut here ---
[   29.929095] Unable to handle kernel NULL pointer dereference at virtual address 00000024 when read
[   29.943882] PC is at mtk_crtc_ddp_irq+0x18/0x100
[   29.944494] LR is at mtk_disp_ovl_irq_handler+0x64/0xc4
[   29.950737] Register r0 information: NULL pointer
[   30.005689] Code: e34c50ef e1a04000 e5900000 e5952004 (e5906024)
[   30.040779] Kernel panic - not syncing: Fatal exception in interrupt
```

反汇编 `e5900000` = `ldr r0,[r0]`、`(e5906024)` = `ldr r6,[r0,#0x24]`，配合 `r0` 为 NULL 和 `r4 = c1a30840`（kmalloc-2k，即 `struct mtk_crtc`）：

- `[源码]` `mtk_crtc.c:648-650`：`struct drm_crtc *crtc = data; ... struct mtk_drm_private *priv = crtc->dev->dev_private;`
- `[源码]` `struct drm_crtc.dev` 是首成员 → `ldr r0,[r0]` 就是 `crtc->dev`，为 NULL → 下一条 `dev_private`（偏移 0x24）炸。
- `[源码]` 为什么 `crtc->dev` 是 NULL：`mtk_crtc_create()` 在 **`:1091-1092`** 就把 vblank 回调注册成 `mtk_crtc_ddp_irq / &mtk_crtc->base`，而真正调 `drm_crtc_init_with_planes()` 设置 `crtc->dev` 的 `mtk_crtc_init()` 在 **`:1116`**；中间 `:1104` 的 `mtk_crtc_init_comp_planes()` → `mtk_plane_init()` 是慢操作（建 property）。
- `[源码]` `mtk_disp_ovl.c:182-207`：IRQ 处理函数先读 `INTSTA`、**无条件清 `INTSTA`**（`:199`），再 `if (!priv->vblank_cb) return IRQ_NONE;`（`:201`）。因为 LK 把 OVL 留在 60 Hz 跑着，frame-done 中断每 ~16.7 ms 来一次，只要有一次落进 `:1091`～`:1116` 这个窗口就 panic。
- `[源码]` `mtk_drm_drv.c:255-270` 的注释认为 `shadow_register = true` 已经修掉了这个崩溃 —— 它只避免了 `mtk_crtc_ddp_config()` 访问"无 state 的 crtc"，**没有覆盖 `crtc->dev == NULL` 这条更早的路径**。

概率：11 次启动 1 次（~9%）。这个概率**与 bind 早/晚无关**（同一个窗口、同一段代码）。所以任何"提前 bind"的改动都必须把它一起修，否则只是把 9% 的 panic 从 30 s 挪到 0.3 s。

### 1.7 附带发现 B：日志里**找不到**"HWC2 HAL 阻塞等 card1"的证据

任务描述里有一条前提"the HWC2 HAL already blocks up to 50 s waiting for card1，SurfaceFlinger 容忍这个等待"。用日志核对：

| 检查项 | 结果 |
|---|---|
| `grep -ac "z1hwc2"` | **0**（v79 源码里 `klog()` 写了 13 处，包括 `z1_open(%s)`，若我们的 .so 被加载过必定有输出） |
| 实际加载的 HWC | `[日志]` `E hwcomposer: ranchu_hw_composer hwc_open could not open framebuffer`（ranchu = 模拟器那套，非我们的） |
| composer 首次实例存活时长 | 23.991621 起 → 34.098798 退出，**10.1 s** |
| 之后每次重试存活时长 | 0.11–0.16 s（34.37 / 39.22 / 44.22 / 49.23 / 54.25 … 一直到 1671.37） |
| card1 出现之后是否恢复 | **没有**，`exited with status 1` 一直持续到 1671 s |
| 唯一被证实"在等 card1"的进程 | `z1drmtest`：`[37.060210] z1drm: waited 16s for the KMS node`（20.65 开始等，16.4 s） |

`[推导]`：如果 v79 源码里那个 `for (i<60) { open(); usleep(500ms); }` 的 30 s 重试真的在跑，那么 card1 出现前的每次重启都该活 30 s；实测是 0.11 s。**结论：那段重试在板上没有生效（要么没编进当时刷的那版 .so，要么模块根本没被加载）。**

`[未证实]`：`ranchu_hw_composer` 那行到底由哪个进程打印（pid 476）、以及我们的 `hwcomposer.z1.so` 为什么没被 `hw_get_module("hwcomposer")` 选中（`ro.hardware=z1` 已由 cmdline `androidboot.hardware=z1` 保证）。**这条和内核延迟是两件事，不要混在一起修。**

> 另注：`klog()` 直接写 `/dev/kmsg`，而该 service 是 `user system`；`/dev/kmsg` 是 `0600 root:root`。**即使 .so 被加载，klog 也会静默失败**——这是"日志里什么都没有"的一个高概率解释，跟内核无关。见 §3 E7。

---

## 2. 根因候选排序

排序标准：先按"能否解释 11/11 的 4 ms 级一致性"，再看证据强度。

### C1 —— `CONFIG_MTK_IOMMU=n` + ovl/rdma0 声明 `iommus` → 永久 `-EPROBE_DEFER`（**已证实，唯一 gate**）

- **机制**：§1.4 的 9 跳链。DT 里有 `iommus`，provider 永远不注册 → `iommu_fwspec_init()` 每次返回 `-EPROBE_DEFER`，而 `dd.c:703-704` 在 `call_driver_probe()`（`:721`）**之前**跑，所以 OVL/RDMA 的 probe 函数体根本没被执行过。
- **支持证据**：
  - `[源码]` `.config:5602` `# CONFIG_MTK_IOMMU is not set`；`.config:5583/5597` `CONFIG_IOMMU_SUPPORT=y` / `CONFIG_OF_IOMMU=y`（所以 core 会解析 `iommus`，只是没人注册 provider）。
  - `[源码]` `mt6572.dtsi:581-587` ovl、`:589-595` rdma0 各有一行 `iommus`；**全 DT 只有这两个节点有**（`:586`、`:594`）——恰好就是日志里 defer 的两个设备，DSI 没有所以没 defer。
  - `[日志]` 11/11 的 4 ms 一致性（§1.1）；`sync_state() pending due to 10203000.iommu`（§1.5）；boot#1 栈回溯（§1.4）。
  - `[源码]` `drivers/of/device.c:152-158`：只有 `-EPROBE_DEFER` 才中止 probe，这解释了"超时一到 probe 立刻成功"。
- **如何产生 35 s**：它本身不产生 35 s，它产生"永远不来"；35 s 是 C2 给的放行时刻。
- **最便宜验证实验**：**E1**（只改内核 cmdline，重打 boot.img，不重编内核/DTB）：加 `deferred_probe_timeout=0`，预期 `bound` 从 35.7 s 挪到 ~0.6 s。若挪了 → C1+C2 成立；若没挪 → C1 被证伪，去查 C4/C7。

### C2 —— deferred-probe 定时器：10 s 基准 + 每次 `driver_register()` 顺延 10 s（**已证实，解释 29–36 s 抖动**）

- **机制**：§1.5 的表。
- **支持证据**：`[源码]` `dd.c:261 / :324-336 / :345-376` + `driver.c:258`；`[日志]` 11 次超时点分散在 29.28–35.68 s，且每次都在 userspace insmod 活跃期之后（`13–30 s` 有大量 `init: Command 'insmod ...'`）。
- **为何不是固定 30 s**：因为起点是 late_initcall（≈0.6 s，见 `[0.618339] Run /init as init process`）而不是 0，且中途被 insmod 续期。
- **最便宜验证实验**：**E1**（同上）。`deferred_probe_timeout=0` 会跳过 `:366` 的排队，改为在 `:363-364` 那次显式 trigger+flush 时就返回 -ETIMEDOUT → bind 应落在 `Run /init` 之前的 ~0.55 s。

### C3 —— fw_devlink 的 supplier 链接也在同一瞬间被放开（**已证实，与 C1 同源**）

- **机制**：`[源码]` `dd.c:309` 在超时 work 里调 `fw_devlink_drivers_done()`（`drivers/base/core.c:1767-1775`），把"supplier 没有匹配驱动"的链接 `fw_devlink_relax_link()` 掉；`:320` 的 `fw_devlink_probing_done()` 再触发/打印 supplier 的 `sync_state()` 情况。
- **支持证据**：`[日志]` 每次 bind 后 0.3 s 必出现三条
  `mtk-power-controller 10006000.power-controller:power-controller: sync_state() pending due to 1000e000.smi / 10203000.iommu / 14010000.larb`
  —— 该字符串出自 `[源码]` `drivers/base/core.c:1787-1788`（`fw_devlink_dev_sync_state()` 的 STRICT 分支），**是超时 work 真正执行过的直接证据**，同时证明 iommu 设备从未 probe。
- **如何产生 35 s**：同 C2（同一把定时器、同一个 work）。
- **最便宜验证实验**：与 C1 合并做 **E1**；若要单独区分，加 `fw_devlink=permissive` 到 cmdline 观察 `10203000.iommu` 是否仍 pending。

### C4 —— OVL/RDMA probe 体内的时钟还没就绪（**未排除；是修掉 C1 之后的第一候选**）

- **机制**：`[源码]` `mtk_disp_ovl.c:652` 与 `mtk_disp_rdma.c:369` 的 `devm_clk_get(dev, NULL)`，clock 来自 `&mmsys CLK_MM_DISP_OVL/RDMA`；provider 不是 mmsys 自己，而是 mmsys probe 里注册的 `clk-mt6572-mm` 子平台设备（`[源码]` 驱动名在 `drivers/soc/mediatek/mtk-mmsys.c:42` `.clk_driver = "clk-mt6572-mm"`，注册动作在 `:436`；`clk-mt6572-mm` 的 gate 表在 `drivers/clk/mediatek/clk-mt6572-mm.c` 的 `mm_clks[]`，含 `CLK_MM_DISP_OVL/RDMA`）。OVL/RDMA 的**首次** probe 尝试发生在 `arch_initcall_sync`（`[源码]` `drivers/of/platform.c:598` `arch_initcall_sync(of_platform_default_populate_init)`），早于 mmsys 的 `device_initcall`（`module_platform_driver`），因此必定先吃一次 `-EPROBE_DEFER`。
- **支持证据（间接）**：`[日志]` `[0.287385] Adding component match` 前的 mmsys 已把自己和 clks 都注册完，说明 0.28 s 时时钟 provider 已在；`[源码]` `mtk-mmsys.c:436` 在 `:445` 之前，顺序正确。
- **反证 / 缺证**：由于 C1 让 probe 在 `dma_configure` 就中止，**我们没有任何一次日志看到 OVL/RDMA 的 probe 函数体在 0.3 s 跑过**。所以"去掉 iommus 后 probe 能一次成功"是 `[推导]`，不是事实。
- **如何产生 35 s**：不能。它是"如果 C1 修了，会不会还慢"的风险项。
- **最便宜验证实验**：**E3/E4**（DTS 改动）。若 bind 落到 0.3 s → C4 不存在；若 OVL/RDMA 又 defer 到几秒后 → 日志里会出现 `failed to get ovl clk`（`[源码]` `mtk_disp_ovl.c:654-655`）或 `failed to get rdma clk`，直接看到。

### C5 —— DSI 的 `host_attach` 在等 PHY / RACK 忙等（**已排除**）

- **机制**：`mtk_dsi_host_attach()` 要 `devm_drm_of_get_bridge()` 找 panel、然后 `drm_bridge_add()` + `component_add()`；`mtk_dsi_probe()` 要 `devm_phy_get(dev,"dphy")`、`mipi_dsi_host_register()`。理论上任一环可 defer。
- **反证**：`[日志]` `[0.294675] mtk-dsi 1400c000.dsi: Z1DSI: port1 lookup -> -19` → 回退 port0 成功 → `[0.295423] next_bridge=... ok`；panel `[0.294780] ... probed`；DSI 的 `bound 1400c000.dsi` 与 OVL/RDMA 同时出现（说明它早就 ready，只是聚合要等三缺一补齐）。`[源码]` `mtk_dsi.c:1625` 的 PHY 若不存在会打印 `Failed to get MIPI-DPHY`，日志无此行。
- **如何产生 35 s**：不能（它 0.29 s 就完成了）。
- **最便宜验证实验**：不需要。若非要验，看 `[0.29]` 那三行即可。

### C6 —— panel 的 `vcc` regulator / `reset` GPIO defer（**已排除**）

- **机制**：`[源码]` `panel-sitronix-st7785m.c:418-424` 用 `devm_regulator_get_optional("vcc")`，若返回 `-EPROBE_DEFER` 会向上抛。
- **反证**：`[日志]` `[0.294780] Sitronix ST7785M 240x320 1-lane DSI panel probed` —— 该行在 `[源码]` `:446`，位于所有 regulator/gpio 查找之后。DT 的 `panel@0` 节点（`[源码]` `mt6572-z1.dts:885-899`）里没有 `vcc-supply`，所以走 `-ENODEV → ctx->power = NULL` 分支。
- **如何产生 35 s**：不能。
- **最便宜验证实验**：不需要。

### C7 —— 组件框架 / `drm_of_component_probe` 的注册顺序（**已排除**）

- **机制**：若 master 的 match 注册得比组件晚，或组件框架要求"全部 `component_add` 后才评估"，也会推迟 bind。
- **反证**：`[日志]` master 在 `0.287–0.289 s` 就把三个 match 加完；`[源码]` `mtk_drm_drv.c:1289` 紧接着 `component_master_add_with_match()`；DSI 在 0.295 s 完成 `component_add`（经 panel 的 `mipi_dsi_attach`）。缺的只有 OVL/RDMA。
- **如何产生 35 s**：不能解释 11/11 与超时告警 4 ms 的绑定关系。
- **最便宜验证实验**：不需要（若 C1 被 E1 证伪，再回来做 E2 看 `component_add` 时间戳）。

### C8 —— 某个 10/20/30 s 的硬编码 `msleep`/超时/`schedule_delayed_work`（**已排除**）

- **机制**：任务提示里怀疑的方向。
- **反证**：`[源码]` 对 `mtk_dsi.c / mtk_drm_drv.c / mtk_crtc.c / mtk_disp_ovl.c / mtk_disp_rdma.c / panel-sitronix-st7785m.c` 全量 grep `msleep(1000|5000|10000`、`msecs_to_jiffies`、`schedule_delayed_work`、`wait_for_completion_timeout`、`N*HZ`：**0 命中**。真正的 10 s 常量在 `drivers/base/dd.c:261`（框架层，已归入 C2）。
- **如何产生 35 s**：不能。
- **最便宜验证实验**：不需要（grep 已闭环）。

### C9 —— DIS 电源域（genpd provider）缺失或未起来（**已排除**）

- **机制**：若 `[源码]` `mt6572.dtsi:249-282` 的 `mediatek,mt6572-power-controller` 没人匹配，则 `iommu`（`:403-412`，`:411` 有 `power-domains=<&spm DIS>`）会卡在 genpd，进而影响下游。
- **反证**：`[源码]` `drivers/pmdomain/mediatek/mtk-pm-domains.c:1122-1123` **确有** `mediatek,mt6572-power-controller` 支持，`.config:5726-5727` 也开了；`[日志]` `mtk-power-controller 10006000.power-controller` 的 `sync_state()` 行证明 genpd provider 已注册。
- 另注：OVL/RDMA **没有** `power-domains` 属性（`[源码]` `mt6572.dtsi:581-595`），所以它们根本不走 genpd 路径。
- **如何产生 35 s**：不能。
- **最便宜验证实验**：不需要。

### C10 —— workqueue 饥饿 / `async_schedule` 拖慢（**已排除**）

- **机制**：若 `deferred_probe_work` 排在饥饿的 workqueue 上，超时后也要很久才跑。
- **反证**：`[日志]` 11/11 的"告警 → bound"间隔 4–42 ms，其中 10 次 <12 ms。`[日志]` boot#1 `Workqueue: events_unbound deferred_probe_work_func` 也说明工作线程正常在执行。
- **如何产生 35 s**：不能（它只能解释毫秒级抖动）。
- **最便宜验证实验**：不需要。

### 候选汇总

| 候选 | 状态 | 能解释 35 s？ | 一句话 |
|---|---|---|---|
| C1 iommu defer | **已证实（决定性）** | 是（作为 gate） | `CONFIG_MTK_IOMMU=n` + `iommus` on ovl/rdma0 |
| C2 dd.c 定时器 | **已证实** | 是（给出时刻） | 10 s 基准，`driver_register` 每次 +10 s |
| C3 fw_devlink 放行 | **已证实（同源）** | 是（同一 work） | `sync_state() pending due to ...` 是证据 |
| C4 时钟未就绪 | 未排除（修 C1 后的下一坎） | 否 | 需 E3/E4 观察 |
| C5 DSI/PHY/RACK | 已排除 | 否 | 0.2947 s 就 attach 完成 |
| C6 panel regulator | 已排除 | 否 | 0.2948 s 已 probed |
| C7 组件框架顺序 | 已排除 | 否 | master 0.287 s 就注册好 |
| C8 硬编码 10/20/30 s | 已排除 | 否 | 显示树里 grep 0 命中 |
| C9 DIS genpd | 已排除 | 否 | 驱动存在且已 probe |
| C10 workqueue 饥饿 | 已排除 | 否 | 抖动只有 4–42 ms |

---

## 3. 实验计划

原则：**先做"不改代码"的，再做"只改 DTB"的，最后才上 printk**。全部实验只靠串口日志判读（本板没有 adb/shell，`/sys/kernel/debug` 读不到）。

### E0（0 成本，必做）：固化判读基线

- **动作**：不刷机。抓一次新启动日志，用下面 4 条命令判读（都在 PC 侧）：

```bash
L=/home/lxj/claude/6572/mainline_recon/z1_boot_XXXX.log
grep -ac "deferred probe timeout"        $L
grep -an "bound 14007000.ovl"            $L | tr -d '\000'
grep -an "Initialized mediatek"          $L | tr -d '\000'
grep -ac "vblank wait timed out"         $L
```

- **判据**：基线应为 `22 / 11 条 bound / 10 次 Initialized / 0`，bind 时刻 29–36 s。
- **代价**：0。**没有这条基线，后面所有"提前了"的说法都不算证据。**

### E1（最高性价比，先做这个）：内核 cmdline 加 `deferred_probe_timeout=0`

- **改动**：**不碰源码**。只重打 boot.img：

```bash
cd /home/lxj/claude/6572
python3 tools/z1_pack_mtk_bootimg.py \
  --template out/z1_v79/boot.img \
  --zimage  fork_linux/arch/arm/boot/zImage \
  --dtb     fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb \
  --rootfs  /tmp/rootfs_v79.bin \
  --cmdline "console=ttyS0,921600n8 root=/dev/ram panic=10 loglevel=7 clk_ignore_unused regulator_ignore_unused pd_ignore_unused oops=panic sysrq_always_enabled rdinit=/init earlycon=mtk8250,0x11005000 androidboot.hardware=z1 androidboot.selinux=permissive firmware_class.path=/system/etc/firmware printk.devkmsg=on deferred_probe_timeout=0" \
  --name z1_pt0 -o out/z1_pt0/boot.img
```

（`tools/z1_pack_mtk_bootimg.py:42` 有 `--cmdline`，`:68-70` 只要求 <512 B；原串 ~330 B，加 24 B 无压力。）

- **假设成立（C1+C2 正确）时的观察**：串口里 `deferred probe timeout, ignoring dependency` 与 `bound 14007000.ovl` 提前到 **`Run /init as init process` 之前**（~0.55–0.62 s，参照 `[0.618339]`）。`Initialized mediatek` / `fb0: mediatekdrmfb` 也在 1 s 内。
- **假设不成立时的观察**：
  - 仍在 29–36 s 才 `bound` → **C1/C2 被证伪**（value 没生效或未生效在那个路径），转去 E2 看 `check_state()` 的调用者。
  - 报 `-517`/`Failed to get ovl clk` 后延迟几百毫秒才 bind → C4 成立（时钟还没好），但**仍然达标**（几百 ms）。
  - 0.5 s 就 bind 但 **panic** → 命中 §1.6 的陈旧中断 bug（先去修它，这也是一次有效结果）。
- **代价**：**只重打 boot.img，不重编内核、不重编 DTB**（~1 min + 刷写）。
- **副作用警告**：`deferred_probe_timeout=0` 是**全局策略**，会让所有走 `driver_deferred_probe_check_state()` 的设备（genpd / iommu / mdio 消费者）在 late_initcall 就被强制放行。作为"诊断启动"完全可接受，**不建议长期作为修复**。

### E2（一条 printk 钉死全部机理）—— 建议作为"最值得加的仪表"

- **改动**：`drivers/base/dd.c`，函数 `driver_deferred_probe_check_state()`（`:289`）开头插一句：

```c
int driver_deferred_probe_check_state(struct device *dev)
{
	/* Z1 debug (临时): 谁在什么时刻、以什么理由被卡住 */
	pr_info("z1probe: %s check_state from %pS (timeout=%d initcalls_done=%d j=%lu)\n",
		dev_name(dev), __builtin_return_address(0),
		driver_deferred_probe_timeout, initcalls_done, jiffies);
	...
```

- **为什么是这一条而不是别的**：它是**所有**延迟源的唯一汇合点（iommu / genpd / mdio 都调它），一条打印同时给出"哪个设备 / 哪个调用者 / 还剩多少超时 / 第几次尝试"。`%pS` 会直接打出 `iommu_fwspec_init` 还是 `__genpd_dev_pm_attach`。
- **假设成立时的观察**（C1）：每次启动出现且仅出现两组，早期是 `iommu_fwspec_init`，`timeout=10`；随后每隔 ~10 s（被 insmod 一次次续期）再打一次；最后 `timeout=0` 那次之后紧接 `deferred probe timeout, ignoring dependency`。**这同时把 §1.5 里"[未证实] 谁最后续的期"补成事实**。
- **假设不成立时的观察**：若 `%pS` 打出 `__genpd_dev_pm_attach` → 走 C9 路线；若打出别的函数 → 重新排序候选。
- **代价**：**要重编内核**（`make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- zImage -j$(nproc)`，单文件改动 ≈ 2–10 min）+ 重打 boot.img。
- **可选的配套第二条**（同一次编译里一起做，几乎零成本）：`drivers/base/dd.c:311` 加 `pr_info("z1probe: timeout work fired j=%lu\n", jiffies);`，用来把"超时真的触发了"和"触发后谁被放行"分成两行。

### E3（真正的修复验证）：DTB 里把 iommu 节点关掉

- **改动**（`arch/arm/boot/dts/mediatek/mt6572.dtsi:403`，`iommu: iommu@10203000` 节点体内加一行）：

```dts
		iommu: iommu@10203000 {
			compatible = "mediatek,mt6572-m4u";
			reg = <0x10203000 0x1000>;
			...
			power-domains = <&spm MT6572_POWER_DOMAIN_DIS>; /* probably */
			status = "disabled";	/* Z1: CONFIG_MTK_IOMMU=n，没有 provider */
		};
```

- **为什么这样能修**：`[源码]` `drivers/iommu/of_iommu.c:28-29` 第一句就是 `if (!of_device_is_available(iommu_spec->np)) return -ENODEV;` —— 节点被 disable 后直接返回 `-ENODEV`，再经 `of/device.c:152-162` 被当作"没有 IOMMU，继续"，**probe 一路走到底**。
- **假设成立时的观察**：`bound 14007000.ovl` 在 **0.3 s 量级**（与 `Adding component match` 同一毫秒级窗口），`deferred probe timeout` **完全消失**（0 次），`sync_state() pending due to 10203000.iommu` 仍会出现（该设备依然没驱动），`Initialized mediatek` + `fb0` 也都在 1 s 内。
- **假设不成立时的观察**：
  - bind 在 0.3 s 但日志出现 `mtk-disp-ovl ...: failed to get ovl clk` → C4，需要把 mmsys/clk 的时序再拉一下（此时已不阻塞达标）。
  - 仍然 29–36 s → 说明还有第二个 `iommus`/`power-domains` 消费者，回到 E2。
- **代价**：**只重编 DTB + 重打 boot.img**（`make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- mt6572-z1.dtb`，数秒），不重编内核。

### E4（E3 的等价对照）：删掉 ovl/rdma0 的 `iommus` 属性

- **改动**：`mt6572.dtsi:586` 与 `:594` 两行 `iommus = <...>;` 删除。
- **用途**：区分"关 iommu 节点"与"删 iommus"是否等价（应当等价）。如果 E3 成功而 E4 失败，说明还有别的地方在用 `iommu` 节点（例如 `dma-ranges`）。
- **代价**：同 E3。**建议只做 E3，把 E4 作为出问题时的对照，不要两个一起刷**（否则分不清是哪个起的作用）。

### E5（可选，回答"延迟是否可再缩短到 0.1 s"）：`mtk_mmsys` 之前有没有更早的钩子

- **改动**：`mtk_drm_drv.c:1266` 的 `dev_info(dev, "Adding component match for %pOF\n", node)` 前加一行时间戳 `dev_info(dev, "%s: j=%lu\n", __func__, jiffies);`（或者直接用 E2 的打印）。
- **目的**：确认 master 注册 match 的时刻就是 0.287 s（已是日志事实），据此判断 0.3 s 是不是物理下限。
- **代价**：重编内核。**优先级最低**，因为 0.3 s 已经远超 <5 s 目标。

### E6（必须与 E3 同批做）：修 §1.6 的陈旧中断 panic

- **改动（首选 ②，①只作防御，别动 INTEN）**：
  1. **（可选，防御性）** `drivers/gpu/drm/mediatek/mtk_disp_ovl.c`，在 `devm_request_irq()`（`:670`）**之前**只清状态、**不要清 INTEN**（清 INTEN 会把 vblank 关掉，`Z1RET frames=` 就不涨了，等于换一个 bug）：
     ```c
     /* Z1: LK 把 OVL 留在跑，INTSTA 里有陈旧 frame-done；只清状态，不动 INTEN */
     writel(0x0, priv->regs + DISP_REG_OVL_INTSTA);   /* 宏在 :31，值 0x0008 */
     ```
  2. **（首选，根治）** `drivers/gpu/drm/mediatek/mtk_crtc.c`：把 `:1091-1092` 的
     ```c
     mtk_ddp_comp_register_vblank_cb(comp, mtk_crtc_ddp_irq, &mtk_crtc->base);
     ```
     从那个 for 循环里挪出来，放到 `:1116` `mtk_crtc_init()` 返回成功**之后**（用同一个 for 循环补一遍）。理由：`mtk_crtc_ddp_irq` 第一句就是 `crtc->dev->dev_private`（`:650`），而 `crtc->dev` 是 `mtk_crtc_init()` 里的 `drm_crtc_init_with_planes()`（`:920`）才赋值的。回调注册晚于它 ⇒ 窗口消失，且**不改变任何中断使能状态**（rdma 的 `:390-392` 只是清状态，可作参照）。
- **假设成立时的观察**：所有启动都不再出现 `Unable to handle kernel NULL pointer dereference ... mtk_crtc_ddp_irq`；同时 `bound` / `Initialized mediatek` / `Z1ROUTE` / `frames=` 全部照旧增长（这一条很重要：证明 vblank 没被顺手关掉）。
- **假设不成立时的观察**：仍 panic，但 PC 换到别处 → 说明还有第三条陈旧中断路径（例如 RDMA 的 handler 在 `mtk_crtc` 里注册前就能回调）。
- **代价**：重编内核（可与 E2 合并成一次编译）。

### E7（与内核无关，但决定 composer 那条线怎么走）：证明 `hwcomposer.z1.so` 到底有没有被加载

- **改动**：`z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp` 的 `klog()`（v79 版在 `:41-56`）增加 logcat 旁路（`__android_log_print`），因为该 service 以 `user system` 运行而 `/dev/kmsg` 是 `0600 root:root`，`open()` 会 EACCES 后静默 return。
- **判据**：串口里出现 `z1hwc2: z1_open(...)` = 我们的 .so 被加载；只有 `ranchu_hw_composer` = 没被加载，去查 `ro.hardware` / `hw_get_module` 的变体链。
- **代价**：只重打 boot.img（.so 在 ramdisk 里）。

### 实验顺序与预算

| 顺序 | 实验 | 代价 | 回答什么 | 若成功的下一步 |
|---|---|---|---|---|
| 1 | E0 | 0 | 基线 | E1 |
| 2 | **E1** | boot.img only | C1+C2 是否就是 gate | E3（真修复）+ E6 |
| 3 | E2 | 内核重编 | 谁在卡、谁在续期（同批把 E6 一起编进去） | — |
| 4 | **E3** | DTB only | 能否 0.3 s bind | 验收 §5 |
| 5 | E6 | （并入 E2 的编译） | panic 是否消失 | 连开 20 次不 panic |
| 6 | E4/E5/E7 | 低 | 对照 / 下限 / composer 归属 | — |

**建议的最小路径**：E0 → E1 → E3+E6。三次刷机就能定案并拿到 <1 s 的 bind。

---

## 4. 候选修复（按风险/收益排序）+ 早 bind 到底值不值

### F1（推荐，风险低，收益高）：DTB 关掉 iommu 节点 + 同步加固 OVL 陈旧中断

- **内容**：E3 的 `status = "disabled"`（1 行）+ E6 ② 把 vblank 回调注册挪到 `mtk_crtc_init()` 之后（移动 2 行）+ E6 ① 可选只清 `INTSTA`（1 行）。
- **风险：低**。当前内核 `CONFIG_MTK_IOMMU=n`，IOMMU 路径本来就是"没有"，改前改后 DMA 行为完全一致 —— 唯一变化是**从 35 s 后生效变成 0.3 s 生效**。
- **收益**：bind ~0.3 s；`deferred probe timeout` 消失；把"boot 时间依赖 insmod 时序"变成确定值；**（因为打包了 E6 ②）顺带消掉 1/11 的启动 panic**。
- **验证**：§5。
- **依赖**：E1 先证明方向。

### F2（零内核改动，只能当诊断/止血）：cmdline `deferred_probe_timeout=0`

- **内容**：E1。**不碰源码和 DTB**。
- **风险：中**。全局策略：所有 `driver_deferred_probe_check_state()` 消费者都在 late_initcall 被强制放行，会掩盖"真的缺 provider"的问题；以后 debug 别的问题时这个 flag 会误导人。
- **收益**：bind ~0.6 s；**一次性证明机理**。
- **建议**：只用于 E1 实验那一刷，**不要进长期 boot.img**。

### F3（长期正确方向，本轮不要做）：开 `CONFIG_MTK_IOMMU=y`

- **内容**：让 `mt6572-m4u` 真正 probe、`iommu_device_register()` 注册 provider，从根上消除 defer，并给显示走真 IOVA。
- **风险：高**。会改变整条显示链路的 DMA 地址空间（`mediatek-drm` 用的 GEM 全是 CMA/物理地址），而且 `mtk_iommu` 需要 `mediatek,mt6572-m4u` 匹配、DIS 电源域、larb/smi（`CONFIG_MTK_SMI` 当前也**没有**配置符号）——一次性引入多个新变量，可能把已经能出图的显示搞坏。
- **收益**：正确性/DMA 隔离，**不是 boot 时间**。
- **结论**：单独立项，**不要和"提前 bind"打包**。

### F4（用户态，无内核改动）：把 composer 的等待改成"可观测 + 有界"

- **内容**：`hwcomposer.z1.so` 的 `drm_open()` 保留有界重试，但（a）加 logcat 旁路让等待可见（§3 E7），（b）把 `ENOENT` 与 `ENODEV` 分开报，（c）把等待上限压到 ~5 s。
- **收益**：修好 F1 之后这个等待会变成 0（card1 在 composer 起来之前就在了），但**它现在不是阻塞点**。
- **风险**：低。
- **警告**：见下面"值不值"。

#### 关于"HWC2 HAL 已阻塞最多 50 s / SF 能容忍这个等待"这个前提

这个前提**在 `z1_boot_20261109_1838.log` 里找不到支持证据**，而且有两处与之矛盾（详见 §1.7）：

1. **没有任何一次 50 s 级阻塞**：composer 首次实例 23.991621 起、34.098798 退出 = **10.1 s**；此后每次重启只活 **0.11–0.16 s**。若 30 s（或 50 s）重试真的在跑，card1 出现（35.77）之前的那些重启必然各活 30 s 以上 —— 实测 0.11 s。
2. **它连 card1 出现后都没恢复**：39.2 / 44.2 / 49.2 / 54.2 … 一直到 1671 s 仍然 `exited with status 1`。所以"早 bind 让 HAL 等到了就成功"这个因果链在板上没有观测到。
3. 日志里**唯一**被证实"在等 card1"的进程是 `z1drmtest`：`[37.060210] z1drm: waited 16s for the KMS node` —— 但它只是诊断工具，不参与开机关键路径。

**结论**：把用户态措施定位成"让等待可见 + 有界"，而不是"把 50 s 缩短成 5 s"；真正让 composer 起来是**另一条独立战线**（先按 §3 E7 证明 .so 到底有没有被 `hw_get_module("hwcomposer")` 选中）。若确实要保留"等 card1"的逻辑，在 F1 之后再等就是纯延迟，应直接去掉。

### 早 bind 到底值不值？——量化

| 维度 | 现状（35 s bind） | F1 之后（0.3 s bind） | 用户可见差异 |
|---|---|---|---|
| 内核启动时间 | 不受影响（bind 不在 critical path 上） | 不受影响 | **无** |
| 首帧 | route B 保持 LK 的视频流，bind 前后屏上内容是同一帧 | 同 | **无**（面板没被重新配置，见 `[日志] Z1DSI: route B`）|
| `z1drmtest` | 阻塞 **16 s**（`waited 16s for the KMS node`） | 0 s | 无（诊断工具，但省 16 s） |
| composer（HWC2） | card1 在 35.77 才在 → **24–35.8 s 这 11.8 s 窗口内不可能成功** | 窗口消失 | **仍然不生效**：日志显示 card1 出现后 composer 依旧每 5 s `exited with status 1`（一直到 1671 s）。**早 bind 不是 composer 的解药。** |
| SurfaceFlinger | 用 client 合成，不依赖 card1 | 同 | **无** |
| 确定性 | boot 结果依赖 insmod 时序（29.28–35.68 s 抖动 6.4 s） | 固定 ~0.3 s | 间接（可复现性） |
| 启动 panic | 1/11（`mtk_crtc_ddp_irq` NULL，**与 bind 时刻无关**） | F1（含 E6 ②）→ 0 | **有**，但这是 E6 ② 的功劳，**不是"早 bind"的功劳** |

**结论（明确回答"值得吗"）**：
- **值得做，但定位要摆正**：它是**启动卫生/可复现性**的修复（+ 顺手消掉一个 16 s 的诊断等待），不是黑屏/composer 的修复。成本是 1 行 DTS + 6 行 IRQ 加固，风险极低，收益是"把 6.4 s 的不确定抖动 + 一个 11.8 s 的不可用窗口"变成常量。
- **不要指望它改善开机时间或首帧** —— 日志证明这两项都不受 bind 时刻影响。
- **如果只允许做一件事**：做 F1（含 E6），跳过 F3。

---

## 5. 回滚与验证

### 5.1 改动面（本次计划最多碰 3 个文件）

| 文件 | 改动 | 回滚 |
|---|---|---|
| `arch/arm/boot/dts/mediatek/mt6572.dtsi` | `iommu@10203000` 加 `status = "disabled"`（1 行） | 删掉该行 |
| `drivers/gpu/drm/mediatek/mtk_crtc.c` | vblank 回调注册从 `:1091` 挪到 `mtk_crtc_init()`（`:1116`）之后（移动 2 行） | 挪回 |
| `drivers/gpu/drm/mediatek/mtk_disp_ovl.c` | （可选）probe 里只清 `DISP_REG_OVL_INTSTA`（1 行，**不要动 INTEN**） | 删掉 |
| （仅 E1）boot.img cmdline | 加 `deferred_probe_timeout=0` | 刷回 `out/z1_v79/boot.img` |

**回滚路径**：内核侧改动只影响 `zImage`/`dtb`，所以**回滚 = 刷回上一版 boot.img**（`out/z1_v79/boot.img`），system 分区不动。刷前必须先备份产物到 `bridge_backups/products/`（项目纪律）。

### 5.2 证明"面板/DSI/RDMA 仍然工作"的日志行（按顺序全部要出现）

```
[  ~0.29] st7785m 1400c000.dsi.0: Sitronix ST7785M 240x320 1-lane DSI panel probed
[  ~0.29] mtk-dsi 1400c000.dsi: ... next_bridge=... ok
[  ~0.30] mediatek-drm mediatek-drm.N.auto: bound 14007000.ovl (ops 0x...)
[  ~0.30] mediatek-drm mediatek-drm.N.auto: bound 14008000.rdma (ops 0x...)
[  ~0.30] mediatek-drm mediatek-drm.N.auto: bound 1400c000.dsi (ops 0x...)
[  ~0.30] [drm] Initialized mediatek 1.0.0 for mediatek-drm.N.auto on minor 1
[  ~0.31] mediatek-drm mediatek-drm.N.auto: [drm] fb0: mediatekdrmfb frame buffer device
[  ~0.31] mtk-mmsys 14000000.syscon: Z1ROUTE 36->53 applied 1 route(s)
[  ~0.31] mtk-mmsys 14000000.syscon: Z1ROUTE 53->14 applied 3 route(s)
[  ~0.31] mtk-dsi 1400c000.dsi: Z1DSI: route B -- keeping LK video stream; no DSI reconfiguration from Linux
[  ~0.31] st7785m 1400c000.dsi.0: Z1DSI: route B -- panel already initialised by LK, skipping reset + DCS init
```

> 注意：`Z1ROUTE / Z1DSI: route B / Z1RET / Z1DUMP` 这批打印**在日志内核 #155 里有、在当前源码树里已被移除**（`grep -rn "Z1RET\|Z1DUMP" drivers/` 无命中）。做验证前要么把它们临时加回（`mtk_drm_drv.c` 的 `mtk_drm_bind` 末尾 + `mtk_dsi.c:962/1062` + 面板 `:227`），要么改用下面的判据。

### 5.3 `Z1RET frames=` 到底是什么意思

- `[日志]` 形如 `[   37.114403] ... Z1RET frames=120 (still scanning)`，每 ~2.05 s 一次、每次 +120 ⇒ **58.5 帧/秒**；另一次启动一路数到 `frames=43560`（≈12 分钟连续扫描）。
- `[推导]` 它是那个调试内核里挂在 `mtk_crtc_ddp_irq`（`[源码]` `mtk_crtc.c:646`）路径上的计数器，即**OVL/RDMA 的 frame-done 中断次数**。它递增 = "OVL→RDMA→DSI 这条链真的在出帧、中断真的在来"。
- 用法：**改前改后都让 `frames=` 跑到 ≥600**（≈10 s）。若 `frames=` 不涨或直接不出现，说明 DDP 中断没到 → 结合 5.4 判定面板/管线坏了。
- **重要前提**：在这个驱动里 OVL 的 frame-done 中断要先被 DRM 使能（`mtk_crtc_enable_vblank` → `mtk_disp_ovl.c:231-235` 写 `INTEN`），所以 `frames=` 一般**从第一次 KMS modeset 之后才开始涨**。因此：
  - 判定"管线活着"的**首要**证据是 §5.2 那串 `bound/Initialized/Z1ROUTE`，`frames=` 是**次要**的活性指标；
  - 如果按 E6 ① 误把 `INTEN` 清了，`frames=` 会永远是 0 —— 这正是我在 E6 里要求"只清 `INTSTA`、不动 `INTEN`"的原因。看到 `frames=` 不涨而 §5.2 全部正常，先查是不是 `INTEN` 被关掉了。

### 5.4 `vblank wait timed out = 0` 是什么意思

- `[日志]` 对整份 8.2 MB 日志 `grep -ac "vblank wait timed out"` = **0**。
- `[源码]` 该告警出自 `drivers/gpu/drm/drm_vblank.c:1320`：`drm_WARN(dev, ret == 0, "vblank wait timed out on crtc %i\n", pipe);` —— 意思是"等不到 vblank 中断"，是 DDP 中断链断掉的头号症状。
- **0 次 = 从来没有任何一次 vblank 等待超时** = 中断链健康。改动后必须继续保持 0；一旦出现，说明 bind 提前后中断没配上（大概率是 §1.6 的加固没做对）。

### 5.5 验收清单（刷机后逐条打勾）

| # | 判据（命令/日志） | 通过标准 |
|---|---|---|
| 1 | `grep -ac "deferred probe timeout"` | **0**（E1 实验时允许非 0，但 F1 之后必须 0） |
| 2 | `grep -an "bound 14007000.ovl"` 的时间戳 | **< 1.0 s** |
| 3 | `grep -an "Initialized mediatek"` 的时间戳 | < 1.0 s，且出现 1 次 |
| 4 | `grep -ac "Kernel panic"` | 0（连续 20 次启动） |
| 5 | `grep -ac "vblank wait timed out"` | 0 |
| 6 | `grep -an "Z1ROUTE 53->14 applied 3 route(s)"` | 出现 |
| 7 | `frames=` 计数 | 10 s 内 ≥600（或等价的中断计数） |
| 8 | `[drm] fb0: mediatekdrmfb` | 出现 |
| 9 | `bound 1400c000.dsi` | 出现（DSI 也进了聚合） |
| 10 | 屏上内容 | 保持 LK 画面/二屏不花屏（`route B` 语义：内核不改 DSI 寄存器） |

### 5.6 已知的两个"看起来坏但正常"现象

1. `mtk-power-controller ...: sync_state() pending due to 1000e000.smi / 10203000.iommu / 14010000.larb` —— **会继续出现，不是错误**。这三个设备在 `.config` 里没有驱动（`CONFIG_MTK_IOMMU`/SMI 相关符号均未设置），`[源码]` `core.c:1787-1788` 的 STRICT 分支就是干这个的。
2. `[z1warn] ... E ComposerHal: failed to open hwcomposer device: No such device` —— 与内核 DRM 无关（§1.7），别用它判断 F1 成功与否。

---

## 6. 未证实清单（写清楚，别当事实用）

1. `[未证实]` 每次启动里"最后一个把 deferred-probe 定时器续期的 `driver_register()`"具体是谁 —— 需要 E2 的打印。
2. `[未证实]` 去掉 iommus 后 OVL/RDMA 的 probe **函数体**能不能在 0.29 s 一次成功（C4）—— 逻辑上时钟 provider 那时已就绪（`[源码]` `mtk-mmsys.c:436` 早于 `:445`），但**没有任何一次日志见过它们的 probe 体在早期跑过**（`dd.c:703` 把它挡在 `call_driver_probe()` 之前）。
3. `[未证实]` `ranchu_hw_composer` 那行由哪个进程打印、我们的 `hwcomposer.z1.so` 为何没被选中；`klog()` 因 `/dev/kmsg` 权限静默失败只是 `[推导]`（`[源码]` `z1hwc2.cpp:41-56` 的 `open()` 失败即 return）。
4. `[未证实]` boot#1 panic 的 1/11 概率是否是稳态 —— 样本只有 11 次；E6 ② 加固后需要 ≥20 次启动才能说"修好了"。
5. `[未证实]` 日志内核 #155 的 boot.img 是否是 `out/z1_v79/boot.img`。**日志时间 18:38 早于 v79 的 mtime 20:25**，两者不可能是同一份产物；本文所有日志引用只对 #155 成立。
