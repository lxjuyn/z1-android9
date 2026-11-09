# Z1 显示像素输出 —— 客观验证方案（不靠人眼）

- 日期：2026-11-10
- 适用对象：Z1（MT6572 / 240×320 ST7785M DSI），内核 `7.0.0-rc7-z1+`（`/home/lxj/claude/6572/fork_linux`），
  管线 `OVL0(4 planes) → RDMA0 → DSI0`，LK 保持 DSI/panel 状态（`mtk_dsi.keep_lk_dsi=1`、`panel.keep_lk_panel=1`），无 IOMMU，
  仅 CH340 串口 + 新增的 root 命令守护（`/data/local/tmp/z1stage/cmd.sh` → `out.txt`）。
- 相关文档：`out/Z1_PLAN_OVL_20261110.md`（OVL 计划）、`out/Z1_HANDOFF_20261109.md`（显示 bring-up 史）、
  `z1a9_gpt/docs/ovl/Z1_HANDOFF_20261109_HWC2.md`、`z1a9_gpt/docs/ovl/Z1_HWC2_CONTRACT_20261110.md`（HWC2 契约）、
  `z1a9_gpt/docs/ovl/Z1_DEPLOY_CHANNEL_20261110.md`（串口部署通道）。
- 本文只做一件事：**把"屏幕到底显示了什么"变成可复核的数据**，并给出每条数据能证明什么、不能证明什么。

---

## 证据等级标记（沿用 `Z1_HWC2_CONTRACT_20261110.md` 的约定）

| 标记 | 含义 |
|---|---|
| **【Q】** | 本地源码/头文件原文引用，给出 `文件:行号` |
| **【Q-EXT】** | 外部公开源码原文引用，给出 URL（本机无该文件，不能当本地事实） |
| **【V】** | 由【Q】/【Q-EXT】一步逻辑直接推出（推理链写在文中） |
| **【I】** | 推断/工程建议，无直接源码支撑 |
| **【未确认】** | 本机无法确认，并明确列出它会推翻哪些结论 |

> **硬性规则**：本文所有寄存器偏移/位域，凡标【Q】的都来自本树；标【Q-EXT】的来自公开 MT6572 厂商树，
> **必须在上板后逐条读回验证**才能当事实用。**推断一律标【I】，不许拿推断填表。**

---

## 0. TL;DR

### 0.1 技术排序（按"便宜且可靠"→"贵但最硬"）

| 序 | 技术 | 一句话结论 | 内核改动 | 工作量 |
|---|---|---|---|---|
| **T0** | VBLANK 心跳（`DRM_IOCTL_WAIT_VBLANK` 计数） | 免费、当场可用：唯一能证明"扫描链路还活着"的零成本信号 | 0 行 | 10 分钟 |
| **T1** | DRM 平面状态读回（`GETPLANE`+`GETPROPERTY`） | 免费、当场可用：证明**内核认为**提交了什么，不证明寄存器，更不证明像素 | 0 行 | 0.5 人时 |
| **T2** | 输入层内容证据（HAL 侧直方图/CRC，或 `/proc/<pid>/fd` 重开 mmap） | 便宜：证明**喂进去的**是什么，不证明合成结果 | 0 / ~40 行 | 1~2 人时 |
| **T3** | 显示寄存器直读（`/dev/mem`） | **当前被内核封死**（`STRICT_DEVMEM`+`IO_STRICT_DEVMEM`，5 个 DISP 块全被 BUSY 占用 → `-EPERM`）；加 `iomem=relaxed` 一行 cmdline 即可解锁 | 0 行（改 cmdline）或 ~60 行 | 0.5 人时 / 2 人时 |
| **T4** | fb0 像素读回（`MAP_DUMB`+`mmap`） | 便宜：只对 **fbdev 基线路径**有意义；HWC2 时代读 fb0 是无效证据 | 0 行 | 0.5 人时 |
| **T5** | **WDMA 写回捕获（唯一像素级证明）** | **可行**。内核完全缺 WDMA 驱动/节点/路由，需新增 ~300 行；用**旁路捕获**（不改主路径） | ~300 行 | 2~4 人日 |
| **T6** | CPU 参考合成（golden image）对比 T5 | 必需：把 T5 的字节变成"对/错"的判决；纯用户态 | 0 行 | 1~2 人日 |
| **T7** | `screencap` | **低置信度**：走 SF/RenderEngine 重新合成，不是读 HWC 设备输出，且依赖当前未验证的 GPU | 0 行 | 0.5 人时 |
| **T8** | DSI 读回（LPRD / 读面板 GRAM） | **不可行**（两重原因：命令引擎出 Idle；面板是 video mode 无 GRAM） | — | — |
| **T9** | 已评估并**淘汰**的其它候选（BLS 图案/校验和、COLOR CRC、MDP_WDMA/WROT、G2D、`/dev/mem` 读 RAM、读 fb0 当证据、只用 vblank-timeout WARN） | 每条都有明确的淘汰理由，见 §2 T9；**列出来是为了不再重走** | — | — |

### 0.2 推荐的端到端验证链（最小充分集）

```
T0 心跳（基线 60 Hz + 捕获后立即恢复）
  └─ T1 DRM 状态（fb_id/位置/alpha/blend 与 HWC 日志逐字段一致）
       └─ T2 输入层内容（每层 CRC32/直方图，与 golden 合成器的输入是同一份）
            └─ T3 寄存器（SRC_CON/ADDR/PITCH/OFFSET + MOUT + MUTEX_MOD 与 T1 一致）
                 └─ T5 WDMA 捕获（一帧合成结果的真实字节）
                      └─ T6 golden 逐像素比较（0 差异 = 像素级通过）
```

**为什么这条链是充分的**：`T1+T3` 一致 ⇒ 内核确实把预期地址/几何写进了 OVL；
`T2` 一致 ⇒ 那些地址里确实是预期内容；`T5` 与 `T6` 一致 ⇒ OVL **实际算出来**的合成结果与预期逐像素相同；
`T0` 在捕获前后都正常 ⇒ 面板链路未被破坏。剩下唯一的缺口是 **RDMA→DSI→面板这一段**，
它的置信度继承自 `Z1_HANDOFF_20261109.md §2.4` 的"左红右绿"涂色实验（OVL 平面内容 → 面板呈现，
已经用人眼一次性标定过）。**这个"一次性标定 + 之后全程免人眼"是本方案的核心思想**：
把不可测的一段用一次已知图案钉死，之后所有不确定性都收敛到可测的 OVL 输出侧。

### 0.3 内核工作量结论（像素级写回）

- **必要且充分**：新增一个 **DISP_WDMA0 捕获旁路**（`OVL_MOUT_EN bit1` + `MUTEX_MOD bit6` + WDMA 寄存器编程 + 一个私有 DRM ioctl 把缓冲给用户态）。
  `OVL_MOUT_EN bit1 = WDMA0` 有外部厂商源码原注释支撑【Q-EXT】，`MUTEX_MOD bit6 = WDMA` 本树已定义【Q】。
- **不需要**动 DRM 组件框架（WDMA 不进 `main_path`，否则路径里缺 `WDMA0→DSI0` 路由会让整个 bind 失败）。
- **不需要** `drm_writeback_connector`（虽然 `drm_writeback.o` 已编进内核【Q】）；那是"让 SF 用写回"的正道，
  对"验证"而言是过度设计且会碰主路径。
- **不需要** CMA/dma-heap 新分配器：捕获缓冲就是一张 `dumb buffer`（`dma_alloc_wc` 必然连续【Q】），
  用户态用 `MAP_DUMB`+`mmap` 直接读。
- 估算：`~300 行新增（1 个新文件 + 3 处小改）`，与"可回滚、掉电安全、不破主路径"三个要求兼容。

---

## 1. 事实基线（本轮逐条核实）

### 1.1 地址与寄存器：本地确证的部分

| 块 | 物理基址 | 本地证据 |
|---|---|---|
| MMSYS | `0x14000000`（+0x1000） | `arch/arm/boot/dts/mediatek/mt6572.dtsi:564-566` |
| OVL | `0x14007000`（+0x1000） | `mt6572.dtsi:581-583` |
| RDMA0 | `0x14008000`（+0x1000） | `mt6572.dtsi:589-591` |
| BLS | `0x1400a000` | `mt6572.dtsi:597-599` |
| COLOR | `0x1400b000` | `mt6572.dtsi:607-609` |
| DSI | `0x1400c000` | `mt6572.dtsi:614-616` |
| MUTEX | `0x1400e000` | `mt6572.dtsi:627-629` |
| MIPI-TX(PHY) | `0x10011000` | `mt6572.dtsi:374`（`mipi_tx0: dsi-phy@10011000`） |
| **WDMA** | **无 DT 节点**（全 dtsi grep 无 `wdma`） | 【Q】反证：本树根本没有 WDMA 节点 |

**OVL 寄存器**（`drivers/gpu/drm/mediatek/mtk_disp_ovl.c`）：

| 偏移 | 宏 | 行号 |
|---|---|---|
| `0x0004` | `DISP_REG_OVL_INTEN` | `:23` |
| `0x0008` | `DISP_REG_OVL_INTSTA` | `:31` |
| `0x000c` | `DISP_REG_OVL_EN` | `:38` |
| `0x0014` | `DISP_REG_OVL_RST` | `:39` |
| `0x0020` | `DISP_REG_OVL_ROI_SIZE` | `:40` |
| `0x0024` | `DISP_REG_OVL_DATAPATH_CON` | `:41` |
| `0x0028` | `DISP_REG_OVL_ROI_BGCLR` | `:45` |
| `0x002c` | `DISP_REG_OVL_SRC_CON` | `:46` |
| `0x30+0x20n` | `DISP_REG_OVL_CON(n)` | `:47` |
| `0x38+0x20n` | `DISP_REG_OVL_SRC_SIZE(n)` | `:48` |
| `0x3c+0x20n` | `DISP_REG_OVL_OFFSET(n)` | `:49` |
| `0x40+0x20n` | `DISP_REG_OVL_ADDR(n)` | `:56`（`DISP_REG_OVL_ADDR_MT2701 0x0040`）、`:63` |
| `0x44+0x20n` | `DISP_REG_OVL_PITCH(n)` | `:52` |
| `0xc0+0x20n` | `DISP_REG_OVL_RDMA_CTRL(n)` | `:54` |
| `0xc8+0x20n` | `DISP_REG_OVL_RDMA_GMC(n)` | `:55` |

> **陷阱（本树自身的一个坑）**：MT6572 用 `.addr = DISP_REG_OVL_ADDR_MT2701`（`:702`），于是
> `DISP_REG_OVL_ADDR(n) == DISP_REG_OVL_PITCH_MSB(n) == 0x0040+0x20n` **是同一个寄存器**。
> 因此 pitch 只能是 16 位（`:527` `pitch_lsb = pending->pitch & GENMASK(15,0)`），
> 且**任何写 `PITCH_MSB` 的代码都会踩掉 ADDR**。MT6572 `supports_afbc=false`（`:706`），
> `mtk_ovl_afbc_layer_config()` 不会被调用（`:579`、`:594`），所以当前安全；
> 但**新增写回代码时绝不要碰 `PITCH_MSB`**。【V】

**MUTEX 寄存器**（`drivers/soc/mediatek/mtk-mutex.c`）：

| 偏移 | 宏 | 行号 |
|---|---|---|
| `+0x20+0x20n` | `DISP_REG_MUTEX_EN(n)` | `:26` |
| `+0x24+0x20n` | `DISP_REG_MUTEX(n)`（bit1=`INT_MUTEX`） | `:27`、`:49` |
| `+0x2c+0x20n` | `DISP_REG_MUTEX_MOD`（MT6572: `mutex_mod_reg = MT2701_MUTEX0_MOD0 = 0x2c`） | `:18`、`:41-47`、`:766` |
| `+0x30+0x20n` | `DISP_REG_MUTEX_SOF`（`MT2701_MUTEX0_SOF0 = 0x30`） | `:19`、`:48`、`:767` |

**MT6572 的 MUTEX 模块位**：`MT6572_MUTEX_MOD_DISP_OVL 3` / **`MT6572_MUTEX_MOD_DISP_WDMA 6`** /
`COLOR 7` / `BLS 9` / `RDMA0 10`（`mtk-mutex.c:333-338`）。
且 `mt6572_mutex_mod[]` **已经登记了 `[DDP_COMPONENT_WDMA0] = 6`**（`:407-413`）——mainline 已经把位表准备好了，
只是没有 WDMA 驱动去用它。【Q】

**MMSYS 路由寄存器**（`drivers/soc/mediatek/mtk-mmsys.h`）：

| 偏移 | 本树宏名 | 行号 |
|---|---|---|
| `0x030` | `DISP_REG_CONFIG_DISP_OVL_MOUT_EN` | `:21` |
| `0x04c` | `DISP_REG_CONFIG_OUT_SEL` | `:22` |
| `0x050` | `DISP_REG_CONFIG_DSI_SEL` | `:23` |
| `0x064` | `DISP_REG_CONFIG_DPI_SEL` | `:24` |
| `0x100` | `DISP_REG_CONFIG_MMSYS_CG_CON0` | `:19` |

MT6572 路由表（`drivers/soc/mediatek/mt6572-mmsys.h:9-29`，原文）：`OVL0→RDMA0` 用 `0x030,BIT(0)`；
`RDMA0→COLOR0` 用 `0x050/0x054,BIT(0)`；`RDMA0→DSI0` 用 `0x050=BIT(0)`、`0x058=BIT(0)`、`0x04c=0x3→0`；
`OVL0→COLOR0` 用 `0x030,BIT(2)` 与 `0x054,BIT(0)`。

> **表里没有任何 WDMA 相关条目**（`grep -n WDMA drivers/soc/mediatek/mt6572-mmsys.h` = 0 命中；`mt6572_mtk_ddp_main[]` 里也没有 `DDP_COMPONENT_WDMA0`）。
> 也就是说：**寄存器语义（bit1=WDMA）存在，但 mainline 的 MT6572 路由表没把它写进去**。
> 若要走 route 表，只需加一行 `MMSYS_ROUTE(OVL0, WDMA0, 0x030, BIT(1), BIT(1))`；
> **但本方案不这么做**——因为 `mtk_mmsys_ddp_connect()` 只在 `main_path` 的**相邻对**之间被调用（`mtk_crtc.c:393-397`），而 WDMA 进 `main_path` 会让 `mtk_ddp_path_available()` 要求一条不存在的 `WDMA0→DSI0` 路由，整个 bind 直接失败。所以捕获路径**绕开 route 表，直接写 MMSYS 寄存器**（§4.3）。【V】

> **⚠ 命名冲突（必须记录，否则会写错寄存器）**【Q vs Q-EXT】：
> 本树把 `0x050` 叫 `DSI_SEL`、把 `0x058` 留成裸地址；而厂商寄存器表（§1.2 外部表）是
> **`0x050 = DISP_RDMA0_OUT_SEL`、`0x054 = DISP_COLOR_SEL`、`0x058 = DISP_DSI_SEL`**。
> 二者对 **MT6572 而言厂商表才是对的**（`out/PLAN_DISP_ALIGN_20261108.md:131-137` 也是这么列的，
> 且与 `mt6572-mmsys.h` 实际写的位值完全对得上：`0x030=1`(OVL→RDMA) + `0x050=1`(RDMA→DSI) + `0x058=1`(DSI←RDMA) + `0x04c=0`）。
> ⇒ **功能是对的，宏名是错的**。新增路由时**用裸地址写注释**，不要相信 `DISP_REG_CONFIG_DSI_SEL` 这个名字。

### 1.2 WDMA：外部厂商寄存器表（必须上板逐条读回验证）

> 来源：`https://github.com/drsami/mt6572_kernel`（公开 MT6572 Android 4.2.2 GPL 内核树）
> `mediatek/platform/mt6572/kernel/drivers/dispsys/ddp_reg.h`、`ddp_wdma.h`、`ddp_wdma.c`。
> 该树是 **3.4.5**，与本板原厂 **3.4.67** 不同版本，因此**只能当"待验证的外部线索"**，不能当本地事实【Q-EXT】。

**块基址**（`ddp_reg.h` 原文：`DDP_REG_BASE_*`）：MMSYS `0xf4000000`、OVL `0xf4007000`、RDMA `0xf4008000`、
**WDMA `0xf4009000`**、BLS `0xf400A000`、COLOR `0xf400B000`、DSI `0xf400C000`、MUTEX `0xf400E000`。
（厂商用 `0xf4…` 是 ioremap 后的虚拟基址；物理基址减 `0xE0000000` = 本文的 `0x14…`。与本地 dtsi 逐块吻合。）

⇒ **WDMA 物理基址 = `0x14009000`（推断成立度很高）**：本地 `mt6572.dtsi` 里 `RDMA0=0x14008000`、
`BLS=0x1400a000`，中间正好空出 `0x14009000`；`mainline_recon/S6_DISPLAY_RESEARCH_WAVE_20260831.md:26`
（基于真 MT6572 LK `mt_reg_base.h` 的调研）明确写着 `WDMA 0x14009000`。**【I，两路独立旁证；仍须上板确认】**

**WDMA 寄存器偏移**（`ddp_reg.h` 原文，基址 `0x14009000`）【Q-EXT】：

| 偏移 | 名称 | 用途 |
|---|---|---|
| `+0x00` | `DISP_REG_WDMA_INTEN` | 中断使能（bit0 Frame_Complete，bit1 Underrun） |
| `+0x04` | `DISP_REG_WDMA_INTSTA` | 中断状态（W1C） |
| `+0x08` | `DISP_REG_WDMA_EN` | 使能 bit0 |
| `+0x0c` | `DISP_REG_WDMA_RST` | 软复位 bit0（自清） |
| `+0x10` | `DISP_REG_WDMA_SMI_CON` | SMI 慢速/阈值（默认即可） |
| `+0x14` | `DISP_REG_WDMA_CFG` | `In_Format[3:0]`、`Out_Format[7:4]`、`Byte_Swap[8]`、`RGB_Swap[9]`、`UV_Swap[10]`、`DNSP_SEL[15]`、`Dither_EN[24]`、`ERR_DIF_EN[25]` |
| `+0x18` | `DISP_REG_WDMA_SRC_SIZE` | `h<<16 \| w` |
| `+0x1c` | `DISP_REG_WDMA_CLIP_SIZE` | `h<<16 \| w` |
| `+0x20` | `DISP_REG_WDMA_CLIP_COORD` | `y<<16 \| x` |
| `+0x24` | `DISP_REG_WDMA_DST_ADDR` | 目标**物理**地址 |
| `+0x28` | `DISP_REG_WDMA_DST_W_IN_BYTE` | 目标行字节数（= pitch） |
| `+0x2c` | `DISP_REG_WDMA_ALPHA` | `A_Sel[31]`、`A_Value[7:0]` |
| `+0x30` | `DISP_REG_WDMA_BUF_ADDR` | 内部缓冲基址（默认 0） |
| `+0x34` | `DISP_REG_WDMA_STA` | 状态 |
| `+0x40..0x54` | `C00/C02/C10/C12/C20/C22` | 3×3 颜色矩阵（默认单位阵） |
| `+0x58..0x64` | `PRE/POST_ADD0/2` | 矩阵前/后加 |
| `+0x70..0x78` | `DST_U_ADDR/DST_V_ADDR/DST_UV_PITCH` | YUV 平面（RGB 不用） |
| `+0x90` | `DITHER_CON` | 抖动（**像素精确必须关**） |
| `+0xa0/0xa4/0xa8` | `FLOW_CTRL_DBG` / `EXEC_DBG` / `CLIP_DBG` | 调试读回（**很有用**：可证明引擎真的在跑） |

**WDMA 关键位域**（`ddp_wdma.h` 原文）【Q-EXT】：
`WDMA_CFG.In_Format = 0x00`（= ARGB，注释 "from overlay"）；
`Out_Format`：`RGB565=0x00 / RGB888=0x01 / ARGB=0x02 / XRGB=0x03 / UYVY=0x04 / YUV444=0x05 / YUV420_P=0x06`；
`WDMAStart()` = `INTEN=0x03` 然后 `EN=0x01`；`WDMAStop()` = `INTEN=0` 然后 `EN=0`；
`WDMAConfig(idx, inFormat, srcW, srcH, clipX, clipY, clipW, clipH, outputFormat, dstAddress, dstWidth, useSpecifiedAlpha, alpha)`
——注意**参数里没有 pitch**，行宽由 `dstWidth`×bpp 推出（`WDMAConfigUV()` 里 `dstWidth * bpp/2` 可旁证）【V】。

**路由语义（这是全篇最关键的一条外部证据）**【Q-EXT】：

```c
/* ddp_path.c 原文 */
DISP_REG_SET(DISP_REG_CONFIG_DISP_OVL_MOUT_EN, 0x2);   // ovl_mout output to wdma0
```
（同一份文件里还有 `disp_path_switch_ovl_mode()` 的 `DISP_REG_SET(DISP_REG_CONFIG_DISP_OVL_MOUT_EN, 1<<1); // ovl_mout output to wdma0`）

并配 `DISP_REG_SET(DISP_REG_CONFIG_MUTEX_MOD(gMutexID), (1<<3)|(1<<6)); //ovl, wdma0`。
⇒ **`OVL_MOUT_EN` bit1 = WDMA0，bit0 = RDMA0，bit2 = COLOR0；`MUTEX_MOD` bit3=OVL、bit6=WDMA**【Q-EXT +
本地 `mtk-mutex.c:334` 的 `MT6572_MUTEX_MOD_DISP_WDMA 6` 独立吻合】。

**厂商 mem-out 的完整步骤**（`dissys/ddp_path.c` → `disp_path_config_mem_out()`）【Q-EXT】：
`WDMAInit(0)` → `WDMAReset(0)` → `WDMAConfig(0, WDMA_INPUT_FORMAT_ARGB, w, h, 0, 0, w, h, outFmt, dstAddr, w, 1, 0)` →
`WDMAStart(0)` → `MUTEX_MOD = (1<<3)|(1<<6)` → `MUTEX_SOF = 0`(single mode) → `OVL_MOUT_EN = 0x2`。
关闭路径：`WDMAStop` → `WDMAReset` → `OVLStop` → `OVLReset` → 恢复 `MUTEX_MOD`/`MUTEX_SOF`/`OVL_MOUT_EN` 的**保存值**。

**厂商自带的 golden image 先例**：同目录存在 `data_rgb888_64x64.c` 与 `data_rgb888_64x64_golden.c`
（64×64 RGB888 测试图 + 期望图，正是给写回自测用的）【Q-EXT】。⇒ **T6 "golden image 对比"不是我方拍脑袋，
是厂商自己的做法**。

### 1.3 提交路径：无 CMDQ，全是直接 writel

- `.config:5670` `# CONFIG_MTK_CMDQ is not set`，`.config:5579` `# CONFIG_MTK_CMDQ_MBOX is not set`【Q】
- `mtk_ddp_write*()` 在 `CONFIG_MTK_CMDQ` 不可达时**就是 `writel`/`readl`**（`drivers/gpu/drm/mediatek/mtk_ddp_comp.c:69-112`）【Q】
- MT6572 走 `.shadow_register = true`（`mtk_drm_drv.c:268`，结构体 `:255-270`）⇒ `mtk_crtc_update_config()` 的分支是
  `mtk_mutex_acquire()` → `mtk_crtc_ddp_config(crtc, NULL)` → `mtk_mutex_release()`
  （`mtk_crtc.c:596-600`），提交点是 **mutex 的 SOF**。
- `mtk_mutex_acquire()`：`MUTEX_EN=1`、`MUTEX=1`，轮询 `INT_MUTEX`（`mtk-mutex.c:1040-1051`）；
  `release`：`MUTEX=0`（`:1054-1061`）【Q】
- 主路径定义：`mt6572_mtk_ddp_main[] = { OVL0, RDMA0, DSI0 }`（`mtk_drm_drv.c:249-253`），
  且**只在相邻两件之间**调 `mtk_mmsys_ddp_connect()`（`mtk_crtc.c:393-397`）【Q】

> **推论**：给写回加一条 `OVL0→WDMA0` 路由，**不能靠往 `main_path` 里塞 WDMA**——
> 那样 `mtk_ddp_path_available()`（`mtk_ddp_comp.c:585`）会要求 `WDMA0→DSI0` 的路由存在，而
> `mt6572-mmsys.h` 里没有，整个 bind 会失败。**【V】** 写回必须**自成一路**（见 §4）。

### 1.4 buffer：扫描输出必须物理连续

- `mtk_gem_prime_import_sg_table()`：`drm_prime_get_contiguous_size(sgt) < attach->dmabuf->size` → `-EINVAL`
  （`drivers/gpu/drm/mediatek/mtk_gem.c:212-221`）【Q】
- dumb buffer 走 `dma_alloc_wc()`（`mtk_gem.c:122-124`）⇒ **一定物理连续**，且 `dma_obj->dma_addr` 就是 OVL/WDMA 要的地址【Q】
- 用户态可 `MAP_DUMB` + `mmap` 直接读写（`mtk_gem.c:190-208`，`dma_mmap_wc` + `pgprot_writecombine`）⇒ **WC 映射，CPU 读不需要 cache 维护**【Q】
- `fb → 物理地址` 的标准写法就在驱动里：`gem = fb->obj[0]; dma_obj = to_drm_gem_dma_obj(gem); addr = dma_obj->dma_addr;`
  （`drivers/gpu/drm/mediatek/mtk_plane.c:126-128`）【Q】——写回补丁照抄这四行。

### 1.5 调试通道现状：`/dev/mem` 已被双重封死（本轮最重要的发现）

配置（`fork_linux/.config`）：

| 项 | 值 | 行号 |
|---|---|---|
| `CONFIG_DEVMEM` | `y` | `:2248` |
| **`CONFIG_STRICT_DEVMEM`** | **`y`** | **`:7364`** |
| **`CONFIG_IO_STRICT_DEVMEM`** | **`y`** | **`:7365`** |
| `CONFIG_GENERIC_LIB_DEVMEM_IS_ALLOWED` | `y` | `:7152` |

判定逻辑（**逐行读的，不是猜**）：

```c
/* lib/devmem_is_allowed.c:21-30 —— ARM 用这个通用实现 */
int devmem_is_allowed(unsigned long pfn)
{
        if (iomem_is_exclusive(PFN_PHYS(pfn)))
                return 0;                /* ① 被内核独占认领的 MMIO → 拒绝 */
        if (!page_is_ram(pfn))
                return 1;                /* ② 非 RAM 且没人认领 → 允许 */
        return 0;                        /* ③ 系统 RAM → 拒绝 */
}
```
```c
/* kernel/resource.c:1908-1925 摘录 */
if (!strict_iomem_checks || !(p->flags & IORESOURCE_BUSY))
        continue;
if (IS_ENABLED(CONFIG_IO_STRICT_DEVMEM) || p->flags & IORESOURCE_EXCLUSIVE) {
        err = true; break;
}
```
```c
/* drivers/char/mem.c:350-351 —— mmap 失败即 -EPERM */
if (!range_is_allowed(desc->pgoff, size))
        return -EPERM;
```

**哪些块被"独占认领"（→ 被拒），逐个查过**【Q】：

| 块 | 认领方式 | 是否 BUSY | `/dev/mem` |
|---|---|---|---|
| OVL | `devm_platform_ioremap_resource()`（`mtk_disp_ovl.c:657`）→ `devm_request_mem_region()`（`lib/devres.c:154`） | 是 | **拒绝** |
| RDMA | `devm_platform_ioremap_resource()`（`mtk_disp_rdma.c:374`） | 是 | **拒绝** |
| DSI | `devm_platform_ioremap_resource()`（`mtk_dsi.c:1621`） | 是 | **拒绝** |
| MUTEX | `devm_platform_get_and_ioremap_resource()`（`mtk-mutex.c:1137`） | 是 | **拒绝** |
| MMSYS | `devm_platform_ioremap_resource()`（`mtk-mmsys.c:406`） | 是 | **拒绝**（注：`syscon` 也匹配同一节点，若由它绑定则用裸 `of_iomap()` 不认领（`drivers/mfd/syscon.c:61`）⇒ 那时会被允许。但本板有 `Z1ROUTE` 路由打印（`Z1_HANDOFF_20261109.md §2.3`）证明**是 mtk-mmsys 驱动在跑** ⇒ 认领 ⇒ 拒绝） |
| MIPI-TX | 裸 `ioremap()`（`mtk_dsi.c:776`、`:932`），**不认领** | 否 | 允许（但我们用不上） |
| **CMA / 扫描缓冲** | 系统 RAM（`page_is_ram()==1`） | — | **无论如何都拒绝** |

**⇒ 结论【V】**：
1. **`z1kmstest regs` 在本内核上必然失败**，退出码 `2`（`EXIT_NODEVMEM`），错误是 `mmap: Operation not permitted`。
   这不是工具 bug，是内核策略。**在跑 T3 之前必须先把这一条解释清楚，否则会被误判成"寄存器读不出→没有证据链"。**
2. **想从 `/dev/mem` 读扫描缓冲内容是不可能的**（RAM 页永远被拒），所以候选技术 (b) 的"从 `/dev/mem` 读 OVL 的 buffer"
   **路子是死的**；只能走 `/proc/<pid>/fd` 重开 mmap 或 HAL 侧统计（见 §2 T2）。
3. **有一行 cmdline 的解药**：`iomem=relaxed`。本树**支持该参数**：
   `kernel/resource.c:2205` `__setup("iomem=", strict_iomem);`，`:2155` 把 `strict_iomem_checks=0`。
   置 0 后 `resource_is_exclusive()` 对 BUSY 资源直接 `continue`（`:1917`），于是 ①② 只剩 `!page_is_ram` 判断。
   **MMIO 全部可读，RAM 依然不可读。** 这正是我们要的。
   （旁证：本树 `arch/arm/configs/z1_defconfig:498` 也写着 `CONFIG_IO_STRICT_DEVMEM=y`，可见是有意配置。）
   **代价**：降低内核自我保护，属于开发板可接受的取舍；写进 boot cmdline 而非改默认配置。
4. **没有 debugfs 的 KMS state**：`drivers/gpu/drm/drm_state.o` 未编（文件不存在），
   `drm_debugfs.c` 只建 `name`/`clients`/`clients/client-N/proc_info`
   （`drm_debugfs.c:368`、`:480`）。`drm_state_dump()` 存在于 `drm_atomic.c:2101` 但**没人调用它建文件**。
   ⇒ 想读"KMS 全量状态"只能走 libdrm 的 `GETPLANE`/`GETPROPERTY`（T1）。
5. **上一轮的 `z1_disp_dump.c` 已经从树里删掉了**：文件不存在，只剩 `z1_disp_dump.o`（11-09 18:20 编译产物），
   `Makefile` 里也已无该目标（`drivers/gpu/drm/mediatek/Makefile` 18 个 .o 全部核对过）。
   `mtk_crtc.c` 里的 `Z1PAINT`/`Z1RET`/`Z1DUMP` 也全部清干净了（grep 0 命中）。
   ⇒ **现在树是干净的，任何验证仪器都要重新加**（这直接影响 §4 的工作量估算）。

### 1.6 面板是 video mode ⇒ 没有 GRAM 可读

`drivers/gpu/drm/panel/panel-sitronix-st7785m.c:381-382`【Q】：
```c
.mode_flags = MIPI_DSI_MODE_VIDEO | MIPI_DSI_MODE_VIDEO_BURST |
              MIPI_DSI_MODE_LPM | MIPI_DSI_CLOCK_NON_CONTINUOUS,
```
⇒ 视频流式面板，**没有帧缓存可读回**。模式：240×320、htotal 298、vtotal 337、`.clock = 6026`(kHz)
（`:346-356`）⇒ 名义 **60.0 Hz**，但驱动注释明说这是 **"60Hz placeholder"**（`:343`），
真实时序由 LK 决定 ⇒ **帧率判据必须是"与基线一致"，不能是绝对值 60**。【V】

### 1.7 三处对既有文档的更正

| 既有说法 | 更正 | 依据 |
|---|---|---|
| 「用 RDMA 抓帧」这个说法（`Z1_HANDOFF_20261109.md:180` 的原话就是“**一旦要让 RDMA 抓帧**”；本轮任务描述进一步写成“vendor 有 RDMA to memory 路径”） | **没有"RDMA to memory"这种引擎**。`RDMA_MODE_MEMORY` 是 RDMA **从内存读**（当源），出现在厂商 decouple 模式里：`OVL→WDMA→(buffer)` 之后 `RDMAConfig(0, RDMA_MODE_MEMORY, …)` 把这个 buffer 送回 DSI。**唯一的"写内存"捕获引擎是 DISP_WDMA0**。 | 【Q-EXT】`ddp_path.c` `disp_path_config_wdma()` 注释 "reconfigure next output buffer to OVL-->WDMA" + `RDMAConfig(0, RDMA_MODE_MEMORY, …)` |
| 「`DISP_RDMA_MEM_START_ADDR` 本树写死 `0x0f00`（MT8183 值），MT6572 应为 `0x28`」 | **成立，且现在有外部硬证据**：厂商 `ddp_reg.h` 明写 `DISP_REG_RDMA_MEM_START_ADDR = RDMA0_BASE + 0x28`，紧邻本树已定义的 `MEM_CON 0x24`(`mtk_disp_rdma.c:38`) 与 `SRC_PITCH 0x2c`(`:50`)。而且 `mtk_disp_rdma.c:57` 的 `0x0f00` 恰好是 **COLOR 块**的 `COLOR_START`（厂商表 `COLOR_BASE+0x0F00`）⇒ 是抄串行了。 | 【Q】`mtk_disp_rdma.c:57`；【Q-EXT】`ddp_reg.h` |
| 「RDMA 现在不可达，所以这个错值无害」 | 仍然成立（`mtk_rdma_layer_config()` 只在 RDMA 当图层源时被调），**但只要做 decouple 镜像就必须先改**。 | 【Q】`mtk_crtc.c:934-951` |

---

## 2. 技术排序表（逐条：证明什么 / 不能证明什么 / 命令 / 工作量 / 失败模式）

> 排序原则：**先"零成本且不会说谎的信号"，再"间接证据"，最后"唯一能到像素的那条"**。
> 注意：证据强度与技术成本**不是单调关系**，所以下面同时给"能证明的层级"。

### T0 — VBLANK 心跳（成本 0，可靠性高）

| 项 | 内容 |
|---|---|
| **做法** | 对 `/dev/dri/card1` 循环 `DRM_IOCTL_WAIT_VBLANK`（`request.type = DRM_VBLANK_RELATIVE, sequence = 1`），跑 1 s，数次数；`rep.passed[0].sequence` 的差值同时给出"有没有跳帧"。 |
| **证明** | CRTC 仍在出帧 ⇒ **RDMA 还在收到 OVL 的输入** ⇒ 主链路未被写回实验破坏。这是判断"我有没有把屏弄死"的最快信号。 |
| **不能证明** | 像素、图层内容、路由方向（BLS 空转也能产生 vblank——理论上；实测本板 vblank 来自 RDMA frame-end，见 `mtk_disp_rdma.c:98-111` 的 `vblank_cb`）。 |
| **命令** | `$Z --rootcmd '/data/local/tmp/z1kmstest vblank --device /dev/dri/card1 --seconds 1'`（`vblank` 子命令**需新增**，约 40 行，`z1kmstest.c` 里已有 `drmIoctl` 封装可复用） |
| **工作量** | 0 行内核 + ~40 行工具 = **10 分钟** |
| **失败模式** | ① `EBUSY`：SF 持有 master 时 `WAIT_VBLANK` 仍然可用（它不需要 master），但若节点被独占打开会 `EACCES` ⇒ 用 root。② 静态画面时 Android 会关 vsync ⇒ **必须先 `stop surfaceflinger`** 再测，否则测到的是"SF 关掉了 vsync"而不是链路死。 |
| **通过判据** | 基线（未做任何实验时）测一次记下 `count_baseline`；之后每次实验后再测，**要求 `\|count - count_baseline\| ≤ 3`**（1 s 内的抖动）。 |

### T1 — DRM 平面状态读回（成本 0.5 人时）

| 项 | 内容 |
|---|---|
| **做法** | `GETPLANE` + `GETPROPERTY(FB_ID / CRTC_ID / CRTC_X,Y,W,H / SRC_X,Y,W,H / alpha / pixel blend mode / zpos / type / IN_FENCE_FD)`。`z1kmstest probe` 已经在做资源枚举（README §3.1），需扩一列"逐 plane 打印上面 12 个属性"。 |
| **证明** | **DRM core 眼里的已提交状态**：哪个 `fb_id`、在哪个 CRTC、什么几何、什么 alpha/blend、z 序。这是"内核认为它提交了什么"。 |
| **不能证明** | ① 寄存器**真的被写了**（`GETPLANE` 读的是 `plane->state`，不是硬件）；② buffer 里的字节；③ 合成结果。 |
| **命令** | `$Z --rootcmd '/data/local/tmp/z1kmstest probe --device /dev/dri/card1'`，日志 `z1kms:` 前缀同时进 `/dev/kmsg`。 |
| **工作量** | 0 行内核 + ~80 行工具（扩 probe）= **0.5 人时** |
| **失败模式** | ① 若在 SF 正在提交的瞬间读，可能读到"已经提交但还没 latch 到硬件"的状态 ⇒ **必须与 T3 交叉核对**，二者不一致就说明是内核的锅（`pending` 没被 flush）。② `drmModeGetPlane` 的 `crtc_id` 在 plane 已 disable 时是 0，容易误判"图层丢了" ⇒ 判据要连同 `FB_ID=0` 一起看。 |
| **通过判据** | HWC 的 klog（`debug.z1.hwc.verbose=3`）里每层的 `z/size/format/结论`，与 `probe` 读出的 12 个属性**逐字段相等**。 |

### T2 — 输入层内容证据（成本 1~2 人时）

两条实现路径，**推荐 (a)+(b) 都做，互相当交叉验证**。

**(a) HAL 侧统计（推荐，最稳）**

| 项 | 内容 |
|---|---|
| **做法** | `z1hwc2.cpp` 加一个属性 `debug.z1.hwc.dump`（0=关，N=每 N 帧打一次）：对每个 `setLayerBuffer` 导入成功的 buffer，`mmap` 后算 `CRC32` + `R/G/B` 三通道的 16-bin 直方图 + 采样 4 个角点像素，输出 `z1hwc2: dump layer=<i> z=<z> fb=<w>x<h>/<fourcc> crc=0x%08x hist=... corner=...`。HAL 已经在 CLIENT 回退路径里 `mmap` 源 buffer（`klogf(0,"z1hwc2: copy: mmap(fd=%d, ...)")`），代码路径现成。 |
| **证明** | **喂进 OVL 的那几个 buffer 里的字节**：CRC 与 golden 合成器的输入完全对得上，就排除了"import 错了 buffer / 几何错 / stride 错 / 格式错"。 |
| **不能证明** | 合成结果；也无法证明 OVL 读的是同一块内存（要靠 T3 的 ADDR 对比）。 |
| **工作量** | ~40 行 C++（`z1hwc2.cpp`）= **1 人时** |
| **失败模式** | ① 相位问题：buffer 可能正在被 SF/GPU 写 ⇒ 必须**只在静态画面**取样（或连续取 3 帧要求相同）。② 带 cache 的 buffer（lima `drm_gem_shmem`）CPU 直读可能是**旧数据**，没有 fence 就没法同步 ⇒ 这一条本身也是一个客观测试：**如果 CPU 读到的与预期不符，很可能不是"内容错"而是"这 buffer 本来就不是给 CPU 看的"**，此时以 T5 为准。 |

**(b) `/proc/<pid>/fd` 重开 + mmap（不改 HAL）**

| 项 | 内容 |
|---|---|
| **做法** | root 下：`ls -l /proc/$(pidof surfaceflinger)/fd` 找到 dmabuf 匿名 inode，`open("/proc/<pid>/fd/<n>")` 后 `fstat` 比 `st_size`，再 `mmap` 读。工具可写成 `z1fdpeek <pid> <fd> <w> <h> <fourcc>`。 |
| **证明** | 与 (a) 相同，但**不需要改 HAL、不需要重启合成器**。 |
| **不能证明** | 同上；另外**无法证明这个 fd 就是 HWC 用的那个**（要通过 `st_size` + T2(a) 的 CRC 对齐来确认）。 |
| **工作量** | ~120 行工具 = **1 人时** |
| **可行性的残余风险** | dma-buf 是 `anon_inode`，`/proc/pid/fd/N` 重开在多数内核上可行，但**本树未实测** ⇒ **【未确认】**。若失败，退回 (a)。 |
| **通过判据** | 同一帧，T2(a) 与 T2(b) 的 `crc32` 必须相等；且与 golden 合成器的输入（同一份文件/同一份生成参数）相等。 |

### T3 — 显示寄存器直读（成本 0.5 人时 / 2 人时）

| 项 | 内容 |
|---|---|
| **做法 A（便宜，推荐先做）** | 在 boot cmdline 加 `iomem=relaxed`，然后 `z1kmstest regs --base 0x14007000 --size 0x300 --raw`。补充读 MMSYS/MUTEX 的三处：`0x14000030`(OVL_MOUT_EN)、`0x14000048`(WDMA0_SEL)、`0x1400e02c`(MUTEX_MOD)、`0x1400e030`(MUTEX_SOF)。`z1kmstest regs` 目前只 dump OVL（`--base` 默认 OVL），**需加 `--all` 一次读 5 块**（约 60 行）。 |
| **做法 B（干净，不加 cmdline 参数）** | 内核加一个只读 dump 通道：模块参数或 debugfs，`ioremap` 5 个块各读 0x1000 打 kmsg/`seq_file`。**~60 行**，不削弱内核保护。形状可照抄上一轮删掉的 `z1_disp_dump.c`（`Z1_HANDOFF_20261109.md §3` 有它的职责清单：MMSYS/OVL/RDMA/DSI/MIPI-TX/SMI/LARB/MUTEX 全块 dump）。 |
| **证明** | **硬件里真正 latch 住的值**：`SRC_CON` 哪几层开、`ADDR(n)` 指向哪个物理地址、`PITCH/OFFSET/SRC_SIZE/ROI_SIZE`、`OVL_MOUT_EN` 的位、`MUTEX_MOD`/`SOF`。这是把 T1（内核视角）锚到硬件（真实视角）的唯一零成本手段。 |
| **不能证明** | 那些地址里装的是什么；引擎算出了什么。 |
| **工作量** | A = 0 行内核 + 60 行工具 + 一次重刷 boot（cmdline 烘进 boot.img）；B = 60 行内核 + 一次重刷 boot |
| **失败模式** | ① **忘了 `iomem=relaxed` 就以为是工具坏了**（本文 §1.5 已解释）；② `ADDR(n)` 是物理地址，必须与 T2/T1 的 buffer `dma_addr` 对上，**直接比数字是没意义的**——要先算出 fb 的 `dma_addr`（`z1kmstest` 里 dumb buffer 有 `dma_addr` 吗？没有 ⇒ 需要用 `--hold` 时从 `probe` 的 `FB_ID` 反查，或让 HAL 把 `dma_addr` 打出来）。**建议：让 `z1hwc2` 在 verbose=3 时把每层 `fb_id + dma_addr` 一起打，这是把 T1↔T3 对上的一句话补丁。** |
| **通过判据** | `SRC_CON` 的 bit 集合 == `probe` 里 enable 的 plane 集合；`ADDR(n)` == HWC 打印的 `dma_addr`；`OFFSET(n)` == `(dst.y<<16)\|dst.x`；`PITCH(n)` == `fb->pitches[0]`；`SRC_SIZE(n)` == `(h<<16)\|w`；`ROI_SIZE` == `(320<<16)\|240`；`OVL_MOUT_EN & 1` == 1（正常路径）；`MUTEX_MOD & (1<<3\|1<<10)` == 0x408。 |

### T4 — fb0 像素读回（成本 0.5 人时）

| 项 | 内容 |
|---|---|
| **做法** | `open("/dev/graphics/fb0")` → `FBIOGET_VSCREENINFO`/`FSCREENINFO` → `mmap` → 按 bpp/stride 读像素 → 直方图/CRC/`sha256`。工具约 80 行（`z1kmstest` 的 dumb+mmap 代码可复用）。 |
| **证明** | **fbdev 扫描缓冲里的内容**。只对"原厂 HWC1 路径"和"自绘 fb0 基线"有意义——也就是 `Z1_HANDOFF_20261109.md §2.4` 那个左红右绿实验的可自动化版本。 |
| **不能证明** | **HWC2/OVL 多平面时代的一切**：那时没人往 fb0 写，读出来的是死数据；读了还会误以为"屏幕是黑的"。 |
| **工作量** | 0 行内核 + 80 行工具 = 0.5 人时 |
| **失败模式** | ① `preferred_depth = 16`（`mtk_drm_drv.c:526`）⇒ bpp=16/pitch=480，**别按 32bpp 解析**（`Z1_HANDOFF_20261109.md §2.5` 踩过一次）。② fbcon 被 `z1_fbcon_takeover.sh` unbind 后，fb0 可能已不存在 ⇒ 先 `ls /dev/graphics/`。 |
| **通过判据** | 四象限图案（red/green/blue/white）四角像素精确匹配；整幅 `sha256` 与工具自己写进去的图案一致。 |

### T5 — WDMA 写回捕获（**唯一能到像素的那条**；2~4 人日）

| 项 | 内容 |
|---|---|
| **证明** | **OVL 合成后的真实像素字节**（= 送进 RDMA 的那一帧）。这是整条链上唯一"不依赖任何用户态自述"的证据。 |
| **不能证明** | ① RDMA→DSI 的传输（但见 §0.2：这一段由"左红右绿"一次性标定继承）；② 面板的 565 量化与 gamma（WDMA 抓的是 **DSI 之前**的 RGB888 精度，见 §4.5 开放问题 O5）。 |
| **命令** | 见 §3 的 S7；工具是 `z1wbtest capture --device /dev/dri/card1 --w 240 --h 320 --frames 4 --out /data/local/tmp/wb_%d.bin`。 |
| **工作量** | 内核 ~300 行（1 新文件 + 4 处小改）+ 工具 ~150 行 = **2~4 人日**（含上板调试） |
| **失败模式** | 见 §5 风险表 R1~R9、R12。 |
| **通过判据** | 连续 4 帧里**后两帧逐字节相同**（证明稳态、无撕裂）且 `== T6 golden`（0 字节差异）。 |

### T6 — CPU 参考合成（golden image，1~2 人日）

| 项 | 内容 |
|---|---|
| **做法** | ① **确定性阶段**：工具自己生成图层（纯色块 + 已知 alpha 的方块），按 OVL 的规则在 CPU 上合成 → 直接与 T5 比。② **SF 阶段**：用 T2 拿到的每层真实 buffer + HWC 报告的每层 `alpha/blend/z/几何`，在 CPU 上做同样合成。 |
| **证明** | 把 T5 的字节变成"对/错"的判决；并且**能定位错在哪**——逐层累加重放（只放 plane0 → 再放 plane1 …）可以把差异归因到具体某一层。 |
| **不能证明** | 若 CPU 模型本身错（比如对 OVL 的 PREMULTI 语义理解错），会把正确输出判成错误。⇒ **必须先标定 blend 语义**（见 §6 的 U6 + §5 的 R8）：四种情形（不透明 / `PIXEL_NONE` / `PIXEL_ALPHA` / plane alpha）各实测一次再定模型。 |
| **工作量** | ~200-400 行（C，整数运算，无浮点 = 可精确复现）= 1~2 人日 |
| **失败模式** | ① 忘记"OVL 内部是 8bit 定点混合，且 ROI_BGCLR 是不透明黑"（`mtk_disp_ovl.c:354` 写 `OVL_COLOR_ALPHA = GENMASK(31,24)`）⇒ 背景必须是黑色而不是透明；② 忘记 `OVL_CONST_BLEND`(bit28) 对无 alpha 格式的处理（`mtk_disp_ovl.c:558-559`）；③ 直接用 sRGB/gamma 友好的浮点 → 差 1 个 LSB 就报错。**全部用整数、全部 >=8bit 精度**。 |

### T7 — `screencap`（0.5 人时，**低置信度，列出来是为了防止误用**）

| 项 | 内容 |
|---|---|
| **现状** | `/mnt/z1sys/bin/screencap`（20,264 B）存在于 system 镜像。 |
| **证明** | 安卓框架**认为**的屏幕内容（图层栈 + SF 的合成决策），适合抓"UI 逻辑对不对/有没有画面"。 |
| **不能证明** | **HWC 的设备输出**。A9 的 `screencap` 走 SF 的 `captureDisplay` → `RenderEngine`（GPU）**重新合成**一遍，不是读 OVL 的输出；而且依赖当前未验证的 GPU（Mesa 彩虹条问题）。⇒ 它最多是 T6 的一个 **GPU 版参考图**，不能当"面板像素"证据。**【I：实现细节未逐行核对 AOSP 源码 ⇒ 这条结论的置信度本身也只有 HWC 层面】** |
| **工作量** | 0 行内核 + 0.5 人时（含拷出 png 的串口链路开销） |
| **交叉校验** | 只在"T5+T6 都通过"之后跑一次：如果 `screencap` 与 T5 差异巨大，**说明 SF 的合成决策与 HWC 的报告不一致**（这是 HWC bug 的信号，不是显示通路的 bug）。 |

### T8 — DSI 读回（LPRD / 读面板 GRAM）：**不可行**

| 层面 | 结论 | 依据 |
|---|---|---|
| 面板 | **无 GRAM 可读**（video mode burst） | `panel-sitronix-st7785m.c:381-382`【Q】 |
| 控制器 | MT6572 DSI **命令引擎从不离开 Idle**：LP 与 HS 各发一条 DCS 0x11 都 `TIMEOUT after 21ms INTSTA=80000000 DBG6=00010001`，`probe verdict=-1` | `Z1_HANDOFF_20261109.md §2.2`（v64 板上实测）【Q 于本项目日志】 |
| 寄存器 | RX 通路寄存器**存在**（`DSI_RX_DATA0..3` = `0x74/0x78/0x7c/0x80`，`DSI_RACK 0x84`，`LPRX_RD_RDY_INT_FLAG BIT(0)`） | `mtk_dsi.c:102-107`、`:40`【Q】 |
| 结论 | 寄存器在、状态机不在 ⇒ **LPRD 永远等不到 `LPRX_RD_RDY`**。且驱动已经把"切命令模式"整段短路（`z1_keep_lk_dsi`），`mtk_dsi_switch_to_cmd_mode()`（`:687`）在 keep-LK 模式下不可达。 | 【V】 |

> 如果将来真的需要"面板收到了什么"这一级证据：唯一的路是**换一块 command-mode 面板**（有 GRAM、支持 0x2E 读回），
> 或者**外挂 MIPI 分析仪**。**在这块板 + 这块屏上不可行，不要在这上面花时间。**

### T9 — 已评估并**淘汰**的候选（记录理由，避免下一个人重走）

| 候选 | 想法 | 淘汰理由 | 依据 |
|---|---|---|---|
| **BLS 图案发生器 + 校验和** | BLS 块有 `BLS_PATTERN`(`+0xb4`)、`BLS_CHKSUM`(`+0xb8`)、`PWM_DUTY`(`+0xa0`)，想用硬件产生图案/校验 | ① BLS **不在我们的路径里**（v67 起就是 `OVL→RDMA→DSI`，去掉了 COLOR/BLS）；② 本板背光是 **MT6323 ISINK**（`mt6572-z1.dts:914-922`），根本不经过 BLS；③ `CHKSUM` 是**亮度统计**（背光直方图用），不是帧哈希，无法逐像素比对；④ 就算能注入图案，**也读不回来**（无 DSI 读回，见 T8） | 【Q-EXT】`ddp_reg.h` BLS 段；【Q】`mt6572-z1.dts:914-922` |
| **COLOR 块 CRC** | COLOR 有 `COLOR_CRC(n)`(`+0x7e0+4n`)、`COLOR_CRC_EN`(`+0xf14`)，想用硬件 CRC 当指纹 | ① COLOR 不在路径里；② 塞回路径就要改 `mt6572_mtk_ddp_main`（`mtk_drm_drv.c:249-253`）并补 `COLOR0→DSI0` 路由，而 `mt6572-mmsys.h:9-29` **没有这条**；③ 它的 CRC 是块内自检用途，**覆盖范围/多项式未知** ⇒ 无法与软件 golden 对齐，等于又引入一个未知量 | 【Q】`mt6572-mmsys.h:9-29`；【Q-EXT】`ddp_reg.h` COLOR 段 |
| **MDP_WDMA / MDP_WROT**（`0x14004000` / `0x14005000`） | 用 MDP 子系统的另一个写回引擎 | 输入只能来自 **MDP_RDMA 读内存**：本树 `mtk_mdp_rdma.c` 只匹配 `mediatek,mt8195-vdo1-rdma`（MT6572 无节点），`drivers/media/platform/mediatek/mdp/mtk_mdp_core.c:39-40` 的 `mediatek,mt8173-mdp-wdma` 是视频编解码 m2m 通路 ⇒ **都接不到 OVL 的 MOUT** | 【Q】`mtk_mdp_rdma.c` of_match 表、`mtk_mdp_core.c:39-40` |
| **G2D（硬件 2D）** | 用 2D 加速块做合成/搬运 | 厂商 `ddp_reg.h` 里有 `DISP_REG_G2D_*` 宏，但**该文件的块基址表里没有 `DDP_REG_BASE_*_G2D`**（`DISPSYS_G2D_BASE` 未在该头定义）⇒ **无法确认 MT6572 有 G2D**；且用另一个合成引擎**不能证明 OVL 对不对**（还多一个未知量） | 【Q-EXT】`ddp_reg.h` |
| **`/dev/mem` 读扫描缓冲** | 从物理内存读 OVL 正在扫的图像 | **RAM 页永远被拒**（`lib/devmem_is_allowed.c:27-30` 的第三个 `return 0`），与有没有 `iomem=relaxed` 无关 | 【Q】见 §1.5 |
| **读 fb0 当 HWC2 证据** | 读 `/dev/graphics/fb0` | HWC2 走 OVL 多平面，**没人再写 fb0**；读出来是死数据，且极易误判"屏幕是黑的" | 【V】见 T4 |
| **只用 `dmesg` 的 `vblank wait timed out`** | 零成本判据 | 它只在 `drm_atomic_helper_wait_for_vblanks()` 里检查，**超时阈值只有 100 ms**（`msecs_to_jiffies(100)`，`drm_atomic_helper.c:1917`），而且**只在有 atomic 提交时才检查** ⇒ 没有提交就没有监测，不能当持续心跳 | 【Q】`drm_atomic_helper.c:1908-1922` |
| **让 HAL 自己 dump 整帧** | 在 HWC 里 dump | HAL 手里只有**输入层**和（CLIENT 回退时的）client target 拷贝；`presentDisplay` 之后它**没有任何手段看到合成结果**。硬要说"我 dump 了整帧"就是**假信心**的典型来源 | 【V】 |

---

## 3. 分阶段测试序列（每步失败都能定位到"内核 / HWC / SF"三者之一）

设计原则：**每一步只允许一个变量**；每步的失败**必须**对应一个明确的责任方。
`$Z` = `z1a9_gpt/scripts/z1deploy/z1push.sh`；root 命令走 `$Z --rootcmd '…'`（守护进程执行，`out.txt` 末尾 `###RC=… ###DONE`）。
板上工具约定：所有工具/脚本放 `/data/local/tmp/z1stage/`，**一律先 `sha256sum` 再执行**。

### S0 — 基线建档（板子先量一遍）

```bash
$Z --rootcmd 'setprop debug.z1.hwc.verbose 3; dmesg -c >/dev/null
  sha256sum /vendor/lib/hw/hwcomposer.z1.so /vendor/lib/hw/gralloc.z1.so
  getprop ro.hardware; getprop ro.hardware.egl
  ls -l /dev/dri/ /dev/graphics/ /sys/class/leds/ 2>&1
  cat /proc/uptime; echo BASELINE_DONE'
```

| 项 | 内容 |
|---|---|
| 目的 | 拿到"已知好状态"的数字基线，后面每一步都跟它比 |
| **要采集的证据** | ① 两个 `.so` 的 `sha256`（**身份锚**：不记 sha 就无法证明"我测的是哪一版"）；② `/dev/dri/` 里 `card*` 与 `renderD128` 的实际编号；③ `/dev/graphics/fb0` 是否存在；④ `/sys/class/leds/` 是否有 `lcd-backlight`（决定能不能在捕获时关背光，见 §4.7）；⑤ 板上工具文件的 `sha256` |
| **通过判据** | 全部命令 `###RC=0`；`card1` 存在（mediatek-drm ≥33 s 才注册，见 `z1kmstest --wait` 默认值注释） |
| **失败定位** | `card1` 不在 ⇒ 内核 bind 失败（`dmesg \| grep -i mediatek-drm`），与 HWC 无关 |

### S1 — 内核链路存活（T0，零成本）

| 项 | 内容 |
|---|---|
| 目的 | 证明"屏是活的"，把"屏死了"从后面所有失败原因里排除掉 |
| 前置 | `setprop ctl.stop surfaceflinger`（否则 SF 会关 vsync，测出来是假阴性） |

```bash
$Z --rootcmd '/data/local/tmp/z1kmstest vblank --device /dev/dri/card1 --seconds 1'
$Z --rootcmd 'dmesg | grep -c "vblank wait timed out"'
```

| 项 | 内容 |
|---|---|
| **证据** | 一行 `z1kms: vblank count=NN/s seq_delta=NN`（`seq_delta` 由两次 `WAIT_VBLANK` 的 `rep.passed[0].sequence` 相减得到，`seq_delta-count` 即丢帧数）；以及 `vblank wait timed out` 的**计数**（该 WARN 在 `drm_atomic_helper.c:1921`） |
| **通过判据** | 记下 `count_baseline`（名义 ~60/s，见 §1.6）；丢帧 = 0；`vblank wait timed out` 计数 = 0 |
| **失败定位** | 0 次 ⇒ 内核链路死（RDMA 没帧）：看 `mtk_disp_ovl.c:187-196` 的 underflow 打印、`MUTEX_MOD`、时钟门。与 HWC/SF 无关 |

### S2 — 内核 plane 编程能力（`z1kmstest run`，不经 SF）

| 项 | 内容 |
|---|---|
| 目的 | 把"内核能不能正确编程 OVL 四层"与"HWC 决策对不对"彻底分开 |
| 前置 | SF 已停；先跑一遍 `--test-only` 收集拒绝码，再跑真提交 |

```bash
$Z --rootcmd '/data/local/tmp/z1kmstest run --device /dev/dri/card1 --mode all --test-only'
$Z --rootcmd '/data/local/tmp/z1kmstest run --device /dev/dri/card1 --mode all --event --delay 800 --hold-seconds 20'
```

| 项 | 内容 |
|---|---|
| **证据** | ① 每个子测试一行 verdict（`basic/layers/formats/alpha/offscreen/disable`）；② 每帧 `commit rc`；③ `--event` 下 page-flip 事件是否到达；④ 退出码（0..9，定义见 usage） |
| **通过判据** | 退出码 `0`；`--event` 每帧都收到；`disable` 子测试的"第 2 步残留 / 第 3 步消失"两种现象都被工具自判 PASS |
| **失败定位** | 退出码 `4`/`5` ⇒ KMS 不全；`7` ⇒ buffer/AddFB2；`8` ⇒ 首个 modeset 提交失败（**内核的锅**）；`9` ⇒ 拿不到 master（SF 没停干净）。**这一步过了，后面所有显示错误都不可能是"内核不会编程 OVL"** |

### S3 — 内核寄存器 ↔ DRM 状态一致性（T1 + T3）

| 项 | 内容 |
|---|---|
| 目的 | 证明"内核说的"和"硬件里存的"是同一件事 |
| 前置 | `iomem=relaxed` 已在 cmdline（一次重刷 boot），或已合入 §4 的 dump 通道；`z1kmstest regs` 已支持 `--all`（新增 ~60 行） |

```bash
$Z --rootcmd '/data/local/tmp/z1kmstest probe --device /dev/dri/card1 > /data/local/tmp/p1.txt
  /data/local/tmp/z1kmstest regs --all --raw > /data/local/tmp/r1.txt
  cat /data/local/tmp/p1.txt /data/local/tmp/r1.txt
  sha256sum /data/local/tmp/p1.txt /data/local/tmp/r1.txt'
```

| 项 | 内容 |
|---|---|
| **证据** | `p1.txt`：12 个属性 × 4 个 plane；`r1.txt`：`0x002c`(SRC_CON)、`0x0030+0x20n`(CON)、`0x0038`(SRC_SIZE)、`0x003c`(OFFSET)、`0x0040`(ADDR)、`0x0044`(PITCH)、`0x0020`(ROI_SIZE)、`0x14000030`(OVL_MOUT_EN)、`0x1400e02c`(MUTEX_MOD)、`0x1400e030`(MUTEX_SOF)；**两个文件的 sha256**（证明证据没被改过） |
| **通过判据** | 逐字段相等（对照表见 T3）。**`ADDR(n)` 必须等于 HWC/probe 报的那个 fb 的 `dma_addr`**——拿不到就先做 T3 失败模式②那句一句话补丁 |
| **失败定位** | 不一致 ⇒ **内核 bug**（`pending` 没 flush / 掩码写错 / shadow 没提交），与 HWC 无关。这是本方案唯一能抓到"内核悄悄没写寄存器"的检查 |

### S4 — HWC 决策正确性（T0 + T1，SF 重新开起来）

| 项 | 内容 |
|---|---|
| 目的 | 证明 HWC 选对了哪些层做 DEVICE、几何/alpha/blend 传对了 |

```bash
$Z --rootcmd 'dmesg -c >/dev/null; setprop debug.z1.hwc.verbose 3
  setprop ctl.restart vendor.hwcomposer-2-1; sleep 1
  setprop ctl.restart surfaceflinger; sleep 25
  dmesg | grep -E "z1hwc2:|hwc_open|failed to get hwcomposer2" | tail -80'
$Z --rootcmd 'dumpsys SurfaceFlinger --list 2>&1 | head -40'
```

| 项 | 内容 |
|---|---|
| **证据** | ① 每帧一行 HWC 摘要（`z1hwc2:` 前缀，进 `/dev/kmsg`）；② 每层 `z/size/format/结论`；③ `dumpsys SurfaceFlinger` 里 **`Device composition`** 的计数；④ `hwc_open` 失败数；⑤ `surfaceflinger` 启动次数 |
| **通过判据** | `dumpsys` 出现 `Device composition`；HWC 日志的层数与 `probe` 的 enabled plane 数一致；`Abort message` 0 条；SF 只启动 1 次 |
| **失败定位** | `Device composition`=0 但 HWC 说 DEVICE ⇒ 提交被内核拒（回 S3 看 errno）；HWC 自己说 CLIENT ⇒ **HWC 的 import/判定错**（看 `import refused:` 那几行，`z1hwc2.cpp:561-606` 一带）；`failed to get hwcomposer2 function N` ⇒ 函数表缺项（与显示无关） |

### S5 — 输入内容一致性（T2）

```bash
$Z --rootcmd 'setprop debug.z1.hwc.dump 1; sleep 3
  dmesg | grep "z1hwc2: dump" | tail -20'
```

| 项 | 内容 |
|---|---|
| 目的 | 证明喂进 OVL 的那几个 buffer 里的字节是对的 |
| **证据** | 每层 `crc=0x… hist=R:…,G:…,B:… corner=(…)` + `fb_id` + `dma_addr`；与 S3 的 `ADDR(n)` 一一对应 |
| **通过判据** | 层数、`dma_addr`、`crc` 三者自洽；静态画面下连续 3 次采样 `crc` 相同（证明采样时 buffer 没在被写） |
| **失败定位** | `crc` 抖动 ⇒ 采样相位问题（不是 bug）；`dma_addr` 与 OVL `ADDR` 不同 ⇒ **内核 ADDR 没更新**（回 S3）；`import refused` ⇒ gralloc 连续性/格式问题 |
| **交叉校验** | 与 T2(b) 的 `/proc/<pid>/fd` 重开结果比 `crc32`（两条独立路径同值才算数） |

### S6 — 关背光（为 S7 做安全准备，可选但强烈建议）

| 项 | 内容 |
|---|---|
| 目的 | 写回捕获会短暂切断 RDMA 输入 ⇒ 屏幕上出现 ≤N 帧撕裂/花屏。先关背光，操作者看不到抖动，也避免"看到了以为是坏了" |

```bash
$Z --rootcmd 'ls /sys/class/leds/
  cat /sys/class/leds/lcd-backlight/brightness
  echo 0 > /sys/class/leds/lcd-backlight/brightness
  cat /sys/class/leds/lcd-backlight/brightness'
```

| 项 | 内容 |
|---|---|
| **证据** | brightness 前后值；`dmesg` 是否有 mt6323/leds 报错 |
| **通过判据** | 写 0 后读回 0。（不存在/无权限则**跳过 S6**，S7 改为"操作者移开视线"下执行） |
| **未确认** | 本板 `mt6323_leds` 是否真的 probe（PMIC 父中断历史问题见 `CLAUDE.md §4.0`）⇒ **可能整体不可用，属可选增强，非阻塞项** |

### S7 — 像素级捕获（T5）

```bash
$Z --rootcmd '/data/local/tmp/z1wbtest capture --device /dev/dri/card1 \
    --w 240 --h 320 --frames 4 --out /data/local/tmp/wb.bin'
```

| 项 | 内容 |
|---|---|
| 目的 | 拿到 OVL 合成后的真实字节 |
| **证据** | ① 内核 kmsg：`z1wb: start mout=0x… mutex_mod=0x… wdma0_sel=0x…` / `z1wb: done frames=4 intsta=0x…` / `z1wb: restore mout=0x…` / **`z1wb: WATCHDOG restore`（出现即失败）**；② `wb.bin` 的 `sha256` 与 `stat -c %s`；③ 四帧各自 `crc32` |
| **通过判据** | `frames_done == 4`；**后两帧 `crc32` 相同**；`sha256` 与 T6 的 golden 逐字节相同；捕获后立刻重跑 S1，`vblank` 与基线一致；`WATCHDOG` 0 次 |
| **失败定位** | `intsta` bit1（Underrun）置位 ⇒ SMI/带宽或地址错；`frames_done=0` ⇒ 路由/使能没生效（先查 `OVL_MOUT_EN`、`MUTEX_MOD` 读回）；全 0 ⇒ `DST_ADDR` 错或输出格式位错；全黑/全白 ⇒ ROI/CLIP 尺寸错 |

### S8 — golden 判决（T6）

```bash
# 串口不搬大文件：让板子自己算 sha256 + 自己重算 golden 再比
$Z --rootcmd 'sha256sum /data/local/tmp/wb.bin
  /data/local/tmp/z1wbcheck /data/local/tmp/wb.bin \
      --w 240 --h 320 --fourcc XRGB8888 --golden /data/local/tmp/golden.bin'
```

| 项 | 内容 |
|---|---|
| 目的 | 把 T5 的字节变成"对/错"的判决 |
| **证据** | 逐字节差异计数、**第一个差异的坐标与两边像素值**、每层累加重放的差异数（只放 plane0 / plane0+1 / …） |
| **通过判据** | 差异 = 0。非 0 时，"按层累加"的差异首次出现的层号 = 出错层 |
| **失败定位** | 只有顶层错 ⇒ blend/alpha 语义（开放问题 O5/U6）；只有某矩形错 ⇒ OFFSET/裁切；通道互换 ⇒ `BYTE_SWAP`/`RGB_SWAP` 或 OVL CLRFMT；全图 1 LSB 偏差 ⇒ golden 取整规则（改整数实现） |

### S9 — 回归与回滚验证

| 项 | 内容 |
|---|---|
| 目的 | 证明"加这套仪器"和"撤掉这套仪器"都不改变产品行为 |
| 命令 | ① 撤掉捕获参数，重复 S1+S2+S4；② `.so` 回滚到 S0 记的 sha256，再跑 S4 |
| **通过判据** | S1 数字回到基线；S2 退出码 0；S4 `Device composition` 仍 ≥1；回滚后 sha256 与 S0 完全一致 |
| **意义** | **这是把验证工具留在内核里的前提条件** |

## 4. 写回的内核补丁草图（**提案**，含开放问题）

### 4.1 三条路线对比（先选路线，再写代码）

| 路线 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 私有 ioctl + dumb buffer（推荐）** | `mtk_drm_driver` 加 `.ioctls`（`mtk_drm_drv.c:667` 的结构体里目前**没有** `.ioctls`），新增 1 个 `DRM_IOCTL_Z1_WB_CAPTURE`；用户态自己 `CREATE_DUMB`+`MAP_DUMB`+`mmap`，把 handle 交给内核；内核 `drm_gem_object_lookup()` → `to_drm_gem_dma_obj()` → `dma_addr`，编程 WDMA，轮询 `INTSTA` | 代码最少（~300 行）、不动主路径、不动 DT、不碰组件框架；用户态拿到的就是可 `mmap` 的连续缓冲 | 需要给驱动加 ioctls 表（一行） | **✅ 选它** |
| B. debugfs 文件 | `drm_debugfs_add_file()`（`drm_debugfs.c:465-486` 已就绪）建 `z1_wb`，`read` 返回元数据 | 不用 ioctl | **mmap 一个 debugfs 文件很别扭**（要么自己实现 fops->mmap，要么让用户态从别处拿 buffer） | ❌ |
| C. 正统 `drm_writeback_connector` | `drm_writeback_connector_init()`（`drm_writeback.o` 已编进内核，`drivers/gpu/drm/Makefile:75`）+ crtc 里处理 `drm_writeback_connector` job | 标准 ABI，将来 SF 也能用 | 要加 connector、改 `atomic_flush`、改 `mtk_crtc` 状态机；**碰主路径 ⇒ 违反"不破坏在线面板"**；工作量 500+ 行 | ⏸ 留作二期 |

### 4.2 寄存器编程序列（捕获态）

> 偏移来源：§1.2（**全部【Q-EXT】，必须逐条读回验证**）+ §1.1（MUTEX/MMSYS 的本地值）。
> 写之前先 `readl` 保存 `mout / mutex_mod / mutex_sof / wdma0_sel` 四个值。

```c
/* 1) 先看再动：把这四个值打到 kmsg，事后能还原、也能当证据 */
mout        = readl(mmsys + 0x030);   /* DISP_REG_CONFIG_DISP_OVL_MOUT_EN  [Q] mtk-mmsys.h:21 */
wdma0_sel   = readl(mmsys + 0x048);   /* DISP_REG_CONFIG_WDMA0_SEL        [Q-EXT] ddp_reg.h */
mutex_mod   = readl(mutex  + 0x02c);  /* MT2701_MUTEX0_MOD0              [Q] mtk-mutex.c:18 */
mutex_sof   = readl(mutex  + 0x030);  /* MT2701_MUTEX0_SOF0  [Q] mtk-mutex.c:19 —— 只记录，我们不改它 */

/* 2) WDMA 软复位 + 清中断（顺序照厂商 WDMAStart 前的 WDMAReset） */
/*    RST 写 1 再写 0：MTK 的 RST 寄存器本树里不是自清的 —— OVL 就是这么写的
 *    （mtk_disp_ovl.c:357-358 写 1 再写 0），WDMA 沿用同一假设【I，须读回确认】 */
writel(0x1, wdma + 0x0c);             /* WDMA_RST = 1 */
writel(0x0, wdma + 0x0c);             /* WDMA_RST = 0 */
writel(0x0, wdma + 0x00);             /* WDMA_INTEN = 0（先轮询，不接中断） */
writel(0x1, wdma + 0x04);             /* WDMA_INTSTA = 1 (W1C) */
writel(0x0, wdma + 0x08);             /* WDMA_EN = 0 */

/* 3) 几何与格式：全部关掉会破坏像素精确性的东西 */
writel((3u << 4) | 0u, wdma + 0x14);  /* CFG: Out_Format=3(XRGB8888), In_Format=0(ARGB/OVL),
                                       *      Byte_Swap=0 RGB_Swap=0 UV_Swap=0,
                                       *      Dither_EN=0 DNSP_SEL=0 ERR_DIF_EN=0   <-- 关键 */
writel(((u32)h << 16) | w, wdma + 0x18);          /* SRC_SIZE  = OVL ROI */
writel(((u32)h << 16) | w, wdma + 0x1c);          /* CLIP_SIZE */
writel(0u,                 wdma + 0x20);          /* CLIP_COORD = (0,0) */
writel((u32)dma_addr,      wdma + 0x24);          /* DST_ADDR */
writel((u32)(w * 4),       wdma + 0x28);          /* DST_W_IN_BYTE = pitch */
writel(0x80000000u,        wdma + 0x2c);          /* ALPHA: A_Sel=1, A_Value=0（同厂商 mem-out 的 1,0） */

/* 4) 路由：OVL 多一路输出给 WDMA；先保留 RDMA 位（见开放问题 O1） */
writel(mout | 0x2, mmsys + 0x030);
/* 5) 让 WDMA 也挂到 mutex0 上（保留已有的 OVL(bit3)/RDMA0(bit10)） */
writel(mutex_mod | (1u << 6), mutex + 0x02c);

/* 6) 开跑 */
writel(0x3, wdma + 0x00);             /* INTEN = Frame_Complete | Underrun */
writel(0x1, wdma + 0x08);             /* EN = 1 */

/* 7) 轮询：最多 frames 次 Frame_Complete，200 ms 总超时（不要在原子上下文里）
 *    while (!(readl(wdma + 0x04) & 0x1)) { if (timeout) goto abort; usleep_range(500, 1000); }
 *    writel(0x1, wdma + 0x04);  // W1C
 */

/* 8) 收工 —— 这段必须幂等，且必须由 watchdog 兜底 */
writel(0x0, wdma + 0x08);             /* EN = 0 */
writel(0x0, wdma + 0x00);             /* INTEN = 0 */
writel(mout,      mmsys + 0x030);     /* 还原路由 */
writel(mutex_mod, mutex  + 0x02c);    /* 还原 mutex 模块表 */
/* wdma0_sel 从未被我们改过；但**必须读出来记进证据**（开放问题 O2） */
```

**为什么这个顺序是安全的【V】**：
- OVL 的 `SRC_CON/ADDR/PITCH/...` 一个字节都不动 ⇒ 主路径的图层配置不受影响；
- 只动"输出多路选择"和"多挂一个 mutex 模块"，两者都是**读-改-写**（保留原值），不覆盖；
- 失败路径与成功路径共用同一段收尾代码；再叠一层 `delayed_work` watchdog（250 ms 后无条件还原 + `WARN_ON`），
  保证"捕获线程崩了也不会把屏留在黑洞里"。

### 4.3 文件与函数清单（可立即开工）

| 文件 | 改动 | 行数 |
|---|---|---|
| `drivers/gpu/drm/mediatek/z1_wb.c`（**新建**） | `z1_wb_capture()` 主函数（上面的序列 + 轮询 + 还原）、`z1_wb_ioctl()`、`z1_wb_watchdog()`、WDMA 与 MMSYS 的寄存器宏**一律加 `Z1_` 前缀**（`Z1_DISP_REG_WDMA_EN`、`Z1_DISP_REG_CONFIG_DISP_OVL_MOUT_EN` …）。原因：`mtk-mmsys.h:21-24` 里已经有同名但**语义相反**（见 §1.1 的命名冲突）的 `DISP_REG_CONFIG_DSI_SEL` 等宏，`#ifndef` 挡不住"名字一样、含义不同"这种错 | ~230 |
| `drivers/gpu/drm/mediatek/z1_wb.h`（**新建**） | 一个函数原型 + 一个 ioctl 结构体 | ~35 |
| `drivers/gpu/drm/mediatek/Makefile` | `mediatek-drm-y += z1_wb.o`（挂进 18 项之后） | +1 |
| `drivers/gpu/drm/mediatek/mtk_drm_drv.c` | ① `#include "z1_wb.h"`；② `mtk_drm_driver`（`:667`）加 `.ioctls = z1_wb_ioctls, .num_ioctls = ARRAY_SIZE(z1_wb_ioctls)`；③ `mtk_drm_bind()` 成功路径里 `z1_wb_init(drm, priv)` 拿 mmsys/mutex/wdma 三个 `void __iomem *` | +12 |
| `drivers/gpu/drm/mediatek/mtk_drm_drv.c` | `WDMA` 需要的 ioremap：**不建 DT 节点**，直接用 `devm_ioremap(&pdev->dev, 0x14009000, 0x1000)`（`【I】`：绕开 DT/组件框架，因为这个块**只给验证用**；若将来要转正，再补 DT 节点 + `mediatek,mt6572-disp-wdma` 匹配项，参照 `mtk_drm_drv.c:887` 的 mt8173 条目与 `mtk_ddp_comp.c:499` 的 `{ MTK_DISP_WDMA, 0, NULL }`） | +6 |
| **合计** | | **~285 行新增 + 13 行改动** |

> **不需要**改：`mt6572-mmsys.h`（我们直接写 MMSYS 寄存器，不走 route 表——因为 route 表只在
> `mtk_crtc_ddp_hw_init()` 按 `main_path` 相邻对调用，`mtk_crtc.c:393-397`，我们根本不进那条路）、
> `mtk_disp_ovl.c`、`mtk_crtc.c`、`mtk_disp_rdma.c`。**这是本方案最重要的性质：零侵入主路径。**

### 4.4 用户态侧

```
z1wbtest capture  --device /dev/dri/card1 --w 240 --h 320 --frames 4
                  --fourcc XRGB8888 --out /path/wb.bin [--golden G.bin]
```
内部：`CREATE_DUMB(w=240,h=320,bpp=32)` → `MAP_DUMB` → `mmap`（`PROT_READ`，WC 映射）→
`ioctl(DRM_IOCTL_Z1_WB_CAPTURE)` → 读映射 → `crc32` × frames → 落盘 → 与 `--golden` 逐字节比。
**dumb buffer 的 pitch = 240×4 = 960**，正好是 OVL 在 RGB565 时代的 480 的两倍——
注意别和 fbdev 的 480 搞混（`Z1_HANDOFF_20261109.md §2.5` 踩过一次）。

配套的**判决工具** `z1wbcheck`（T6，纯用户态，~200-400 行整数 C）：

```
z1wbcheck <wb.bin> --w 240 --h 320 --fourcc XRGB8888 [--golden G.bin] [--layers N]
```

职责：① 算 `crc32`/逐字节差异；② 打印**第一个差异的坐标与两边像素值**；③ `--layers` 时做"逐层累加重放"
（只放 plane0 → 再放 plane1 …），把差异归因到具体某一层；④ 没给 `--golden` 时按内置图案生成器
自己算期望值（确定性阶段的用法，见 T6 ①）。**它必须与内核的像素格式假设严格一致**（`BYTE_SWAP`/`RGB_SWAP`），
否则会把通道顺序问题报成"颜色对不上"（§5 R8 的例子）。

### 4.5 开放问题（**实现前必须逐条钉死，否则会写出"看起来对"的错代码**）

| # | 问题 | 为什么重要 | 怎么钉死 |
|---|---|---|---|
| **O1** | `OVL_MOUT_EN` 是**独热选择**还是**使能位图**？厂商每次都只写一个位（`0x1`/`0x2`），但寄存器名字带 `_EN`，且 `mt6572-mmsys.h` 用 `BIT(2)` 给 COLOR ⇒ **两种解释都成立** | 决定"捕获时面板会不会黑"：位图 ⇒ `mout\|0x2` 面板继续正常；独热 ⇒ RDMA 断流，面板冻结 ≤4 帧 | 设 `z1_wb_keep_rdma=1` 试 `mout\|0x2`，同时在捕获期间跑 T0 的 vblank 计数：**vblank 不掉 ⇒ 位图（面板没断）**；掉了 ⇒ 独热，改 `0x2` 并配 S6 关背光 |
| **O2** | `DISP_REG_CONFIG_WDMA0_SEL`(0x048) 是什么？厂商代码**只 dump 从不写** ⇒ 它的 LK 默认值就是"可用值"，但含义未知（可能是 WDMA 的输入选择：MOUT / 直连 / 其它） | 若它选错，WDMA 会去抓一个不存在的源，结果全 0 且**没有任何报错** | ① 读出来记进证据；② 若捕获全 0 而其它都正常，就把它设成 0/1 各试一次（这是唯一需要"试着改"的寄存器，因为它没有本地语义） |
| **O3** | WDMA 各寄存器偏移（`ddp_reg.h` 是 **3.4.5**，本板是 **3.4.67**） | 版本差可能改过偏移 | 上板后按 §5 R1 的"读回探测法"逐条验证：先只读（全 0x400）、再写 `WDMA_RST`、再写 `DST_ADDR` 后读回比对 |
| **O4** | `DST_W_IN_BYTE` 是"像素数"还是"字节数"？ | 差 4 倍 ⇒ 图像斜切 | 厂商 `WDMAConfigUV()` 里 `dstWidth * bpp/2` 旁证是**字节数**，但必须一次读回 + 一次实测（用纯色图看是否斜切） |
| **O5** | WDMA 抓的是 **OVL 输出**（RGB888 内部精度）还是 DSI 之前的 565 量化后？ | 决定 golden 要不要先做 565 量化 | 用一张**只在低 3 bit 有差异**的测试图（如 0x00 与 0x07 相邻色块）：若捕获保留差异 ⇒ RGB888；若被抹平 ⇒ 565。**这一步同时标定了"面板看到的是不是捕获到的"** |
| **O6** | `WDMA_CFG` 的 `In_Format` 到底该填几？厂商 `WDMA_INPUT_FORMAT_ARGB = 0x00`，注释 "from overlay" | 填错 ⇒ 通道错位 | 与 O5 用同一张测试图（R/G/B 三个纯色块）判定；如果颜色互换，说明需要 `RGB_Swap`/`Byte_Swap` 而不是改 In_Format |
| **O7** | 共享 mutex0 是否会与 CRTC 的 `mtk_mutex_acquire()` 打架？ | 可能导致 `could not acquire mutex 0`（`mtk-mutex.c:1050`）或满屏撕裂 | 保守做法：捕获只写 `MUTEX_MOD`（RMW），**不碰 `MUTEX_EN`/`MUTEX(0x24)`**；若出现 acquire 失败，改为"停止 SF + CRTC 已 disable 时再捕获"（离线模式） |

### 4.6 如果只想"最省事地拿到像素"，最简可用子集

若时间只够做一件事，做**离线模式**：`stop surfaceflinger` → `z1kmstest run --mode layers --hold` 把图案钉在 OVL 上 →
捕获（此时面板冻结无所谓）→ 对比。这样可**完全绕开 O1/O7**（不重要了，反正屏已经是冻结的），
只需解决 O3/O4/O5/O6。**建议第一版就这样交付**，在线/镜像模式作为二期。

### 4.7 安全与回滚（写回实验的硬约束）

1. **watchdog 是强制的**：`schedule_delayed_work(&wb->restore, msecs_to_jiffies(250))`，工作项里若发现"仍在捕获态"就无条件还原并 `WARN_ON(1)`，让 `dmesg` 留下证据。
2. **还原值来自 `readl` 而不是常量**（`mout`/`mutex_mod` 都是从硬件读的），所以不存在"我猜错默认值把屏搞死"。
3. **`WDMA_EN=0` 后再还原路由**，顺序不能反（否则会在 WDMA 还在写的时候把源切走）。
4. **缓冲必须 ≥ ROI**：240×320×4 = 307200 B；`CREATE_DUMB` 的 `size` 由内核算，工具只校验 `>= 307200` 就 abort。
5. **失败恢复**：若还原后 T0 的 vblank 掉 0，用 `z1kmstest run --mode basic` 重新做一次完整 modeset（它会重新走 `mtk_crtc_ddp_hw_init` → `mtk_mutex_add_comp`），这是已验证可用的恢复手段（S2 通过过的路径）。
6. **掉电安全**：所有状态都在寄存器里，重启由 LK 重新初始化显示，**不存在持久化损坏**（这是我们敢做写回实验的根本原因）。
7. **背光**：见 S6；不可用时改为"操作者不看屏"。

---

## 5. 风险清单（每条：如何产生**假信心** + 如何交叉校验）

| # | 风险 | 会给出的**假信心** | 交叉校验 |
|---|---|---|---|
| **R1** | WDMA 偏移取自 **3.4.5** 外部树，本板是 3.4.67 | "捕获成功、数据是某种东西" —— 但写的是**别的寄存器**，甚至改了时钟/复位位 | **先读后写**：对 0x14009000 起 0x400 做一次全读（只读是安全的），记下基线；写完 `RST` 后再全读，**只有预期位置变化**才继续。任何"非预期位置变化"⇒ 立即停手 |
| **R2** | OVL_MOUT 语义猜错（O1） | 以为"面板还在正常显示"，其实捕获那几帧面板是黑的/花的 —— 若同时有人在看屏，会得到错误结论 | **T0 vblank 计数**在捕获期间连续采样：独热模式下 vblank 会掉（这是可测量的），不会静默 |
| **R3** | 捕获到的是**上一帧**（mutex/SOF 延迟） | 用 A 帧的 golden 去比 B 帧的捕获，得到"差几个像素"的假失败 | **要求连续 2 帧逐字节相同**（静态画面）；动态画面只做"层数/颜色直方图"级比较，不做逐像素 |
| **R4** | **cache 一致性**：golden 的输入 buffer 由 GPU(lima) 写，CPU 侧读到旧数据 | T6 的 golden 本身就是错的，于是把正确捕获判成错误（或反之） | ① 捕获缓冲是 `WC` 映射（`pgprot_writecombine`，`mtk_gem.c:201`）⇒ 设备写入 CPU 可直读；② golden 的输入走 **T2 的 HAL 侧路径**（HAL 必然做了 fence 等待或本身就在 CPU copy 路径上）；③ **纯色块阶段完全不用 GPU**（工具自己画）⇒ 先把 GPU 从等式里去掉 |
| **R5** | 把 `/dev/mem` 读不出来（`-EPERM`）当成"硬件没反应" | 误判"寄存器是空的/链路没通"，浪费大量时间去查不存在的硬件问题 | 本文 §1.5 已给出判定树：**先看 `CONFIG_STRICT_DEVMEM`**，再确认该块是否被 `devm_request_mem_region` 认领；用 `cat /proc/iomem \| grep 1400` 看一眼（这个文件本身可读）就能看到 `mediatek-disp-ovl` 等占位 |
| **R6** | `GETPLANE` 读到的是"已提交但未 latch"的状态 | 以为寄存器已经写好了（T1 单独使用时最危险） | **T1 必须与 T3 成对使用**；只做 T1 的结论一律降级为"内核意图" |
| **R7** | 采样时 buffer 正在被写（撕裂/半帧） | 偶发的、不可复现的"差几个像素" | 静态画面 + 连续 3 次相同 CRC；动态画面只比较统计量 |
| **R8** | golden 的混合模型错（PREMULTI vs COVERAGE） | 系统性地把正确输出判错或把错误判对 | **先用 4 个确定性图层标定 blend**：不透明色块 / alpha=0x80 的 PIXEL_NONE / PIXEL_ALPHA / plane alpha —— 四种情形各算一次，与实测对齐后再用模型去判逐像素。**标定不过就不许用 golden 下结论** |
| **R9** | `screencap` 被当成"面板像素" | 高置信度的错误结论（因为它看起来像截图） | 明确写进报告：`screencap` 只是 SF 侧参考（T7），**不得**作为显示通路证据；与 T5 的差异只能用来查 HWC/SF，不能用来查内核 |
| **R10** | 帧率判据写成"=60" | LK 的时序一变（驱动注释说 60 是 placeholder）就全线报错 | 判据一律写成"与 S0 基线一致（±3/秒）" |
| **R11** | 用"画面看起来正常"回填证据 | 历史上已经踩过（`CLAUDE.md §4.0`：v78 的"显示正常"其实是 fbdev 残留） | 任何"正常"结论必须附一条**数字**：vblank 计数 / `SRC_CON` / CRC / 逐字节差异数。**没有数字就不算证据** |
| **R12** | 6 MB boot 分区装不下新内核 | 为了验证而不得不砍功能，导致"验证通过但产品退化" | `z1_wb.c` +285 行 ≈ 3~5 KB 文本段，对 4.11 MB 的 zImage 影响可忽略；但仍然**每次打包后核对 zImage 大小与 `ANDROID!` 头**（`CLAUDE.md §4a` 的历史教训） |

---

## 6. 未确认清单（明确列出，以及它会推翻什么）

| # | 未确认项 | 若被推翻，会失效的结论 |
|---|---|---|
| U1 | **WDMA 物理基址 = `0x14009000`**（外部 `ddp_reg.h` + 本地 dtsi 地址空隙 + S6 调研文档，三处旁证，但本树无 DT 节点/头文件） | §4 全部；T5；§0.3 的"可行"判断 |
| U2 | **WDMA 寄存器偏移**（`ddp_reg.h` 是 3.4.5，本板 3.4.67） | §4.2 的写序列；T5 的操作步骤 |
| U3 | **`OVL_MOUT_EN` 是独热还是位图**（O1） | "捕获时面板不受影响"这一说法；S7 能不能不关背光 |
| U4 | **`WDMA0_SEL`(0x048) 的语义**（厂商只读不写） | 捕获是否真的取到 OVL 输出 |
| U5 | **捕获点是否已在 565 量化之前**（O5） | T6 的 golden 是否需要先做 565 量化；"捕获 = 面板所见"的等价性 |
| U6 | **MT6572 的 OVL 是否真有 `OVL_CON_CLRFMT_MAN`(bit23)/`OVL_CON_RGB_SWAP`(bit25)**（`mtk_disp_ovl.c:707-709` 声明了 PREMULTI，但无 datasheet） | T6 的 blend 模型；ARGB 层颜色是否正确 |
| U7 | **`/proc/<pid>/fd/N` 重开 dma-buf 是否可 mmap**（本树未实测） | T2(b) 这条路；若不行只剩 T2(a) |
| U8 | **`mt6323_leds` / `lcd-backlight` 是否真的可用**（PMIC 父中断历史问题） | S6 "关背光"；不影响主链 |
| U9 | **`screencap` 在 A9 本机的具体实现路径**（走 RenderEngine 重合成，未逐行核对 AOSP） | T7 的"不能当像素证据"这一判断（但结论方向不会变：它读的不是 HWC device 输出） |
| U10 | **RDMA→DSI 这段的实际保真度**（是否有 gamma/565/带宽导致的差异） | §0.2 里"继承左红右绿实验的置信度"这一步的强度；这也是**本方案唯一需要人眼一次性标定的地方** |

---

## 附录 A — 本地证据索引（本轮逐条读过的位置）

| 主题 | 位置 |
|---|---|
| 显示块基址 | `fork_linux/arch/arm/boot/dts/mediatek/mt6572.dtsi:564,581,589,597,607,614,627,374` |
| 面板 video mode / 时序 | `fork_linux/drivers/gpu/drm/panel/panel-sitronix-st7785m.c:381-382`、`:346-356`、`:343` |
| 主路径 `OVL0→RDMA0→DSI0` | `fork_linux/drivers/gpu/drm/mediatek/mtk_drm_drv.c:249-253`、`:255-271` |
| 相邻组件才建路由 | `fork_linux/drivers/gpu/drm/mediatek/mtk_crtc.c:393-397` |
| shadow register 提交点 | `mtk_crtc.c:596-600`；`drivers/soc/mediatek/mtk-mutex.c:1040-1061` |
| OVL 寄存器表 | `mtk_disp_ovl.c:23,31,38,39,40,41,45,46,47,48,49,52,54,55,56,63` |
| OVL 层开/关（SRC_CON 位） | `mtk_disp_ovl.c:416-417`、`:425-426` |
| OVL ROI/BGCLR/RST 写入 | `mtk_disp_ovl.c:346-358` |
| OVL 层配置（CON/PITCH/SRC_SIZE/OFFSET/ADDR） | `mtk_disp_ovl.c:583-592` |
| OVL 格式→CLRFMT / blend 语义 | `mtk_disp_ovl.c:431-495`、`:536-560` |
| MT6572 OVL 驱动数据（gmc_bits=10, addr=0x40） | `mtk_disp_ovl.c:701-712` |
| RDMA 寄存器表 | `mtk_disp_rdma.c:21-57`（**`:57` 的 `0x0f00` 是错的**） |
| RDMA 层配置与 `RDMA_MODE_MEMORY` | `mtk_disp_rdma.c:300-335`、`:332-333` |
| MT6572 RDMA 驱动数据 | `mtk_disp_rdma.c:430-444` |
| MUTEX 寄存器与 MT6572 位表 | `drivers/soc/mediatek/mtk-mutex.c:18,19,26,27,41-48,333-338,407-413,764-770` |
| MUTEX RMW-OR（不会清别人位） | `mtk-mutex.c:951-957` |
| MMSYS 路由宏 | `drivers/soc/mediatek/mtk-mmsys.h:19-24,71,88-95` |
| MT6572 路由表 | `drivers/soc/mediatek/mt6572-mmsys.h:9-29` |
| cmdq 缺失 ⇒ 直接 writel | `mtk_ddp_comp.c:69-112` |
| WDMA 在组件表里只有名字/无函数 | `mtk_ddp_comp.c:445,499-500`；`mtk_drm_drv.c:887-888` |
| GEM 连续性与 dumb 分配 | `mtk_gem.c:122-124,143-160,190-208,212-221` |
| fb → 物理地址 | `mtk_plane.c:117-128` |
| plane 属性（alpha/blend/zpos/rotation） | `mtk_plane.c:340,347,354,359` |
| `preferred_depth=16` / 最小 64×64 | `mtk_drm_drv.c:526,528-529` |
| `drm_driver` 结构体（无 `.ioctls`） | `mtk_drm_drv.c:653,667-682` |
| `drm_writeback.o` 已编入 | `drivers/gpu/drm/Makefile:75` |
| `drm_state_dump()` 存在但无人建文件 | `drivers/gpu/drm/drm_atomic.c:2101-2105`；`drm_debugfs.c:368,480` |
| `/dev/mem` 判定链 | `.config:2248,7152,7364,7365`；`lib/devmem_is_allowed.c:21-30`；`kernel/resource.c:1872,1908-1925,2155,2205`；`drivers/char/mem.c:60-66,350-351`；`lib/devres.c:154` |
| 各块认领方式 | `mtk_disp_ovl.c:657`、`mtk_disp_rdma.c:374`、`mtk_dsi.c:1621,776,932`、`mtk-mutex.c:1137`、`mtk-mmsys.c:406`、`drivers/mfd/syscon.c:61` |
| DSI RX 寄存器（能力在） | `mtk_dsi.c:40,102-107` |
| vblank 超时 WARN | `drivers/gpu/drm/drm_atomic_helper.c:1921` |
| 无 IOMMU / 无 CMDQ | `.config:5602-5603,5670,5579` |
| `WAIT_VBLANK`/`GETPLANE`/`GETPROPERTY` 无需 master | `drivers/gpu/drm/drm_ioctl.c:656,675,684`（flags 均为 0） |
| vblank 超时阈值只有 100 ms | `drm_atomic_helper.c:1917,1921` |
| MDP_RDMA 只匹配 mt8195（MT6572 无节点） | `mtk_mdp_rdma.c` 的 `mtk_mdp_rdma_driver_dt_match[]` |
| MDP_WDMA 属视频 m2m 通路 | `drivers/media/platform/mediatek/mdp/mtk_mdp_core.c:39-40` |
| 背光是 MT6323 ISINK（不经 BLS） | `mt6572-z1.dts:914-922`（`/sys/class/leds/lcd-backlight`） |

## 附录 B — 外部来源（仅用于 WDMA 部分，均须上板验证）

| 内容 | URL |
|---|---|
| MT6572 厂商 DISP 寄存器表（块基址、WDMA/RDMA/OVL/MUTEX 偏移） | `https://github.com/drsami/mt6572_kernel` → `mediatek/platform/mt6572/kernel/drivers/dispsys/ddp_reg.h` |
| WDMA 位域与 `WDMAConfig()` 签名、输入/输出格式枚举 | 同上 → `.../dispsys/ddp_wdma.h` |
| `WDMAStart/Stop/Reset/Config` 实现、`dstWidth*bpp` 旁证 | 同上 → `.../dispsys/ddp_wdma.c` |
| `OVL_MOUT_EN = 0x2 → wdma0` 的原注释、mem-out 全流程 | 同上 → `.../dispsys/ddp_path.c`（本机已提取副本 `/tmp/vcheck/ddp_path_kernel.c`，其中第 689 行即该注释；**`/tmp` 不持久，正式引用以上游仓库为准**） |
| 厂商自带 golden 测试图（写回自测先例） | 同上 → `.../dispsys/data_rgb888_64x64.c`、`data_rgb888_64x64_golden.c` |

---

## 附录 C — 一页速查：我该跑哪条？

| 我想证明 | 用哪条 | 做不到的话退到 |
|---|---|---|
| 屏还活着、没被我弄死 | **T0**（vblank 计数） | 无替代 |
| 内核把预期的层/位置/alpha 提交了 | **T1**（GETPLANE） | — |
| 内核真的把地址写进了 OVL 寄存器 | **T3**（`iomem=relaxed` + `/dev/mem`） | 内核 dump 通道（60 行） |
| 喂进去的 buffer 内容对 | **T2(a)** HAL 统计 | T2(b) `/proc/fd` |
| HWC 选对了层、走的是 DEVICE 合成 | **T1 + S4 的 `dumpsys`** | HWC 自己的 klog（`verbose=3`） |
| **合成结果逐像素正确** | **T5 + T6**（WDMA + golden） | **没有替代**——这是唯一一条到像素的路 |
| 面板上真的显示了它 | 只能靠 §0.2 的"一次性人眼标定 + 之后全程数值"，或换 command-mode 面板 | 无 |
