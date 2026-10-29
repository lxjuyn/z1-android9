# WMT EMI 契约裁决 —— `EMI mapping fail` 阻断 WiFi

> 裁决工程师报告 · 2026-10-29 · 只读复核 + 取件，未刷写、未碰设备、未改任何既有交付件
> 证据分级：**[板上实证]** = 有上板日志；**[二进制实证]** = 从 .ko/.dtb/Image 反汇编或字节级确认；**[源码实证]** = 在本机 fork_linux 树内读到的源码；**[未验证]** = 无法在本机确认

---

## 0. 一句话裁决

**B 版（`CONSYS_EMI_AP_PHY_BASE = 0xbe080000`）正确，置信度 ~99%（非理论推断 —— 该值已在 2026-08-14 于 Z1 板上跑通完整 WiFi 链路：固件下载 160464B → `WMT turn on WIFI success!` → wlan0 up → `iw scan` 扫到 12 个真实 AP）。A 版（上游 0x80080000）在 Z1 上不可行。**

真正的阻断点**不是**"缺 no-map 配套"，而是：**R5 交付件里装的是上游原值 0x80080000 的 A 版 .ko，把 2026-08-14 已经跑通的 B 版修复回退了。**

回归链条（**[二进制实证]**）：

| 日期 | 文件 | EMI 基址 | 判定 |
|---|---|---|---|
| 2026-08-14 | `out/boot_mainline_z1_connsys_mod.img` 内 | — | **[板上实证] WiFi 完整跑通** |
| 2026-08-16 | `out/alpine_rootfs/lib/modules/.../mtk_stp_wmt_soc.ko` | **0xbe080000** | B 版（可用） |
| 2026-10-06 | `out/z1_r5_system/system.img.r5d_final` 内 `/lib/modules/z1-radio/mtk_stp_wmt_soc.ko` | **0x80080000** | **A 版 ← 回归** |
| 2026-10-25 | 板上实测（r3c） | 0x80080000 | `EMI mapping fail` |
| 2026-10-27 | `out/z1_wifi_stage/modules/mtk_stp_wmt_soc.ko` | **0x80080000** | A 版 |

**决定性证据**：`out/z1_wifi_stage/modules/mtk_stp_wmt_soc.ko` 与 R5 交付件里那枚**sha256 完全相同**（`786e9ab4…ae30`，`cmp` 零差异）——即 R5 板上跑的正是报 `EMI mapping fail` 的那一枚。

---

## 1. 失败机理（已从本机源码逐字复核，不靠字面猜）

`__arm_ioremap_pfn_caller()`（本机 `fork_linux/arch/arm/mm/ioremap.c:314-320`，**[源码实证]**）：

```c
	/*
	 * Don't allow RAM to be mapped with mismatched attributes - this
	 * causes problems with ARMv6+
	 */
	if (WARN_ON(memblock_is_map_memory(PFN_PHYS(pfn)) &&
		    mtype != MT_MEMORY_RW))
		return NULL;
```

- `memblock_is_map_memory()`（`mm/memblock.c:1965`）对 **`memblock.memory` 里带 `MEMBLOCK_NOMAP` 区域返回 `false`** → 这正是 dts `no-map` 的作用。
- 触发条件是**双重的**：该 pfn 必须①在 `memblock.memory` 内（即内核管理的 RAM），②是 map_memory（非 no-map），③`mtype != MT_MEMORY_RW`。
- 为什么普通 `ioremap()` 必然踩中③：`__arm_ioremap_caller` 在 `pfn` 指向 RAM 时把属性改成 `MT_MEMORY_RW`（`ioremap.c:500-520` 一带），而 `__arm_ioremap_pfn_caller` 收到的 `mtype` 仍是调用者传的 `MT_DEVICE`，两者不等 → WARN + `return NULL`。
- 所以 **"EMI mapping fail" 的准确触发条件 = `ioremap()` 目标落在内核管理的 RAM（非 no-map）**，`NULL` 来自上面那条 `return NULL`，**不是** `get_vm_area_caller` 之类的分配失败。

板载日志与该机理**逐行吻合**（`mainline_recon/z1_r3c_replay_20261025.log:535-562`，**[板上实证]**）：

```
535:4,416,...;------------[ cut here ]------------
536:4,417,...;WARNING: arch/arm/mm/ioremap.c:318 at __arm_ioremap_pfn_caller+0x18c/0x1ac, CPU#1: insmod/177
546:4,427,...; warn_slowpath_fmt from __arm_ioremap_pfn_caller+0x18c/0x1ac
547:4,428,...; __arm_ioremap_pfn_caller from __arm_ioremap_caller+0x44/0x54
548:4,429,...; __arm_ioremap_caller from mtk_wcn_consys_hw_init+0x60/0xf8 [mtk_stp_wmt_soc]
562:3,443,...;[WMT-CONSYS-HW][E]mtk_wcn_consys_hw_init(663):EMI mapping fail
```
`arch/arm/mm/ioremap.c:318` 与行号 `663` 均与上面 `return NULL` 行、与 .ko 内硬编码行号**完全对齐**。

---

## 2. 复核记录：前人哪条确认、哪条推翻

### ✅ 确认（3 条）

| # | 前人结论 | 我的独立证据 |
|---|---|---|
| C1 | 上游 EMI 基址硬编码在 `mtk_wcn_consys_hw.h:166` = `0x80080000`，不是 dts reg | 归档头文件 `z1a9_gpt/wifi/conn_soc_wmt_emi/mtk_wcn_consys_hw.h` 第 **166** 行确为 `#define CONSYS_EMI_AP_PHY_BASE (0x80080000)`，旁有 `CONSYS_EMI_AP_PHY_OFFSET (0x80000)` / `CONSYS_EMI_FW_PHY_BASE (0xf0080000)` / `CONSYS_EMI_MEM_SIZE (343*KBYTE)` **[源码实证]** |
| C2 | 失败发生在 `mtk_wcn_consys_hw_init`，`ioremap` 返回 NULL 走 fail 分支 | 反汇编在役 .ko：`movt r0,#0x8008` → `bl ioremap` → `cmp r0,#0` → `beq` fail 分支，fail 分支 `movw r2,#0x297`（**=663**）后 `_printk` **[二进制实证]** |
| C3 | 在役 .ko 的 `memset_io` 会清零 343KB，且 `gConEmiPhyBase` 不写给 CONSYS 寄存器 | 反汇编 `bl mmioset`，参数 `r1=0`、`r2=0x55c00`；全函数只有 `of_find_compatible_node` / 2×`of_iomap` / `ioremap` / `mmioset` / `platform_driver_register`，**无任何 EMI 重映射寄存器写入** **[二进制实证]** |

### ❌ 推翻（2 条）

| # | 前人结论 | 为何推翻 |
|---|---|---|
| **R1** | "Z1 上失败的真因是**缺 no-map 配套**，不是基址错" | **板上实证直接否定**：2026-08-14 Z1 在 **0xbe080000**（即"改基址"路线）下，共享 EMI 映射成功、固件下载完成、WiFi 完整工作。既然该路线在 Z1 上实际跑通，"真因是缺 no-map"就不成立。见 §3 日志原文。 |
| **R2** | "B 版（改 0xbe080000）在上游**无依据**，存在 AP/FW 共享窗错位风险" | ①"无上游依据"属实但**无关紧要** —— Z1 的 carveout 本来就**被 Z1 自己刻意搬走了**（dts 注释：原先在 RAM base，为不遮挡 Z1 LK/preloader 用的低 RAM 才搬到 0xbe000000）。②"错位风险"**已被实验证伪**：若 FW 仍透视 0x80080000，则 8-14 那次不可能出现 `Open FW image done` + `WMT turn on WIFI success!` + 12 个 AP 扫到。实测结论：MT6572 的共享 EMI 窗就是**普通物理 RAM**，AP 映射到哪里、FW 就看到哪里 —— 本报告 §5 的 Image 测量也独立支持这一点。 |

### ⚠️ 前人提到但**我无法复核**的（诚实标注）

- `mtk_wcn_consys_hw.c:604-608` 的 "Reserved memory by device tree!" 注释**原文**：该 `.c` 全盘已不在本机（全盘 `find` 无 `mtk_wcn_consys_hw.c`；唯一 conn_soc 全量树 `products_connsys_modules_20260812_022121` 已清理；`2026-08-14` 的 /tmp clone 也没了），**且网络不通**（`git clone` 300s 超时）。注释不进编译产物，无法从二进制反推 → **[未验证]**。
  但注意：**该注释属于上游生态，与 Z1 的结论无关** —— 上游参考板（Prestigio PAP5500）保留在 0x80000000 是因为它没搬走，Z1 搬走了。
- `:663` 行号 → **已确认**（见 C2，来自二进制硬编码行号），无需读 .c。

---

## 3. 决定性板上实证：8-14 那次是**完整成功**

日志 `mainline_recon/z1_boot_mainline_20260814_202853.log`（3102 行，CR 行尾；规范化副本 `/tmp/success_norm.log`）**[板上实证]**：

```
5788: [MTK-WIFI] WIFI_write: WIFI_write 1
5806: glSetHifInfo:(INIT INFO)[WiFi/HIF]HifInfo->HifRegBaseAddr=0x(ptrval), ...
5808: glSetHifInfo:(INIT INFO)[WiFi/HIF] ChipID = 0x6572                     ← CONN MCU 认出 0x6572
5810: glSetHifInfo:(INIT ERROR)[WiFi/HIF][CCF]cannot get HIF clk_wifi_dma clock.
5814: kalFirmwareOpen:(INIT INFO)Open FW image done, 160464 bytes            ← ★ 固件下载成功
5826: wlanUpdateNetworkAddress:(INIT INFO)Using host-supplied MAC address
5834: wlanProbe:(INIT INFO)MAC address: 20:72:0d:39:0d:1f                    ← 真实出厂 MAC
5840: [MTK-WIFI] WIFI_write: WMT turn on WIFI success!                        ← ★★ func-on 成功
5850: [OK]   wlan0 接口注册 (0s)
5852: 无线接口: wlan0
5858: mt-wifi 180f0000.wifi wlan0: Current addr:  20 72 0d 39 0d 1f ...
5918: [OK]   ifconfig wlan0 up
5938/5950/5966/5982/5998/6014/6030/6052/6072/6094/6106/6122: BSS ... (on wlan0)   ← ★ 12 个 AP
6154: [OK]   WiFi 链路完整: insmod → launcher → func-on → wlan0 → ifconfig up → iw scan
```
内核：`Linux version 7.0.0-rc7-z1+ ... #91 SMP Fri Aug 14 01:46:36 CST 2026`。

配套文档 `mainline_recon/WIFI_FUNC_ON_SUCCESS_20260814.md` / `WIFI_SCAN_SUCCESS_20260814.md` 记录该轮修复链，其中：
> `3 | EMI base 0x80080000→0xbe080000 | e3198bbc3 | 共享内存映射正确，固件握手成功`

git `e3198bbc3`（`fork_linux`，2026-08-14 02:44）commit message **[源码实证]**：
> `mtk_wcn_consys_hw.h: CONSYS_EMI_AP_PHY_BASE 0x80080000 -> 0xbe080000 to match Z1 dts connsys carveout relocated to 0xbe000000 (was ioremap landing on RAM -> EMI mapping fail)`

> ⚠️ 注意 `e3198bbc3` 只提交了 dts（conn_soc .ko 是外编、不入此树），所以**该 .ko 不在 git 里** —— 这是它能被悄悄回退的原因。

**一个关键的操作性坑（我实测发现）**：**成功时不会打印 `EMI mapping OK`**。全部 5 枚 .ko 二进制（R5 交付 / Oct27-A / Aug16-B / Oct28-B / Oct28-A）的字符串表里 `EMI mapping fail` 各 1 份、**`EMI mapping OK` 各 0 份**（debug 级被编译掉）。8-14 成功日志里也 **0 次**出现 `EMI mapping OK`。所以**判据是"没有 fail 行"，不是"出现 OK 行"** —— 别等 OK。

---

## 4. 地址关系图（MT6572 / Z1，1GB DDR）

RAM 节点：`memory@80000000 { reg = <0x80000000 0x40000000>; }` → **0x80000000–0xBFFFFFFF**。`CONFIG_PAGE_OFFSET=0xC0000000`、`CONFIG_ARM_PATCH_PHYS_VIRT=y`、`CONFIG_AUTO_ZRELADDR=y` → `__virt_to_phys(v) = v - 0xC0000000`，`_text` 虚址 `0xC0008000` = **物理 0x80008000**。

```
物理地址          内容                                        归属 / 状态
──────────────────────────────────────────────────────────────────────────────────────────────
0x80000000  ┬ RAM base
            │  ← 480 KiB 缺口（_text 之前；Z1 LK/preloader 早期使用区）
0x80008000  ┤ _text（内核 Image 起点）
            │     .head.text 4KB + 对齐填充
0x80080000  ┤ ┌─ EMI 窗 343 KiB 起点  ← A 版 ioremap 目标（0x80080000..0x800D5C00）
            │ │  ★ 落在内核 Image 的【对齐填充区】内
            │ │  实测：Image 文件 [0x78000,0xCDC00) 区间【全 0 字节】[二进制实证]
            │ │  但地址 < 相邻 .text 区段（0x80100000 起）的物理基址
0x800D5C00  ┤ └─ EMI 窗终点
0x80100000  ┤ 第一个 .text 4096 字节区段（非 0，真正的代码/只读数据）
   …        │  内核 Image 一直到 0x80108000+0xEAA338 ≈ 0x80FB2338
            │
0x90000000  ┤ default-pool CMA alloc-ranges 起点（96 MiB，reusable）
0x9A000000  ┤ CMA 实际落点 0x9A000000..0x9FFFFFFF（board log: "created CMA memory pool at 0x9a000000, size 96 MiB"）
0xA0000000  ┤ Normal zone 上界（board log: Normal [mem 0x80000000-0xAFFFFFFF]）
0xB0000000  ┤ HighMem 区起点
0xBE000000  ┤ ┌─ reserved-memory: connsys@be000000（1 MiB，no-map）★ Z1 刻意搬来的 CONN carveout
0xBE080000  ┤ │  ┌─ B 版 ioremap 目标（0xBE080000..0xBE0D5C00，343 KiB）
            │ │  │  距 carveout 底 512 KiB（与上游 0x80000000→0x80080000 的相对间隔一致）
            │ └──┘  尾部余量 169 KiB（0x2A400）
0xBE100000  ┤ carveout 上界
0xBE200000  ┤ ramoops@be200000（1 MiB，no-map）—— 在 carveout 之外、无重叠
0xBE300000  ┤ ramoops 上界
0xBF400000  ┤ framebuffer@bf400000（12 MiB，no-map）
0xC0000000  ┴ RAM 上界
```

三处数值闭环（**[二进制实证]**，从 R5 交付的 **boot.img 内 DTB** 反编译）：

```
reserved-memory { connsys@be000000 { reg = <0xbe000000 0x100000>; no-map; }; ... }
connsys@18070000 { compatible = "mediatek,mt6572-connsys";
                   reg = <0x18070000 0x1000  0xbe080000 0x55c00>; ... }   ← 与 B 版宏一致
```

| 地址 | 归属 | 结论 |
|---|---|---|
| `0x80080000` | **RAM 内**，位于 `_text`(0x80008000) 之后 480 KiB，落在内核 Image 的**链接器对齐填充区** | ioremap 必被 RAM 检查拦下（A 版死因） |
| `0xBE000000` | reserved-memory `connsys@be000000` carveout **起点**，1 MiB，**no-map** | 不在 `memblock.memory`（map_memory=false）→ ioremap 的 RAM 检查不触发 |
| `0xBE080000` | **carveout 内部**（= 起点 +0x80000） | 不是"撞进预留区"，而是**正是给 connsys 用的 EMI 窗**；dts `connsys reg[1]` 就是这个值 |
| `0xBE100000` | carveout **上界**（1 MiB 处） | EMI 窗 0xBE0D5C00 未越界 ✓ |

**"0x80080000 是否 LK/DRAM 已占用区"的明确回答**：它是正常 RAM（在 `memory` 节点内）；同时从内核视角它在 Image 的填充区、并不与任何已映射设备窗重叠。所以 A 版只需 dts 加 no-map **理论上也能让 ioremap 成功** —— 但那是把一块**活跃 RAM** 从内核手里挖走，且 Z1 的 dts 注释明确记录"当年正因它遮挡 LK/preloader 低 RAM 才搬到 0xbe000000"。**Z1 已经做出了选择并跑通，不要倒退回去。**

---

## 5. 独立佐证：为什么"改基址"在 MT6572 上物理上就是对的路

**[二进制实证]** 对本机 `fork_linux/arch/arm/boot/Image`（构建产物，`_text` 物理 0x80008000）逐 4 KiB 扫描前 1 MiB：

```
phys 0x80008000  nonzero=615      ← .head.text
phys 0x80100000  nonzero=3808     ← 第一个 .text 区段
phys 0x80101000  nonzero=3567
...（0x80100000 起连续 9 个区段非 0）
EMI 窗 phys 0x80080000..0x800D5C00 -> Image 偏移 0x78000..0xCDC00 len=351232
nonzero bytes in EMI window = 0    ALL-ZERO
```

说明两点：
1. 该窗在内核 Image 里**全零**，说明它落在 `_text` 与第一个 `.text` 区段之间的**链接器对齐填充**（`.text` 起点 `0xC0100000` 被 `ALIGN(1<<SECTION_SHIFT)` 对齐到 1 MiB 边界）—— **位置与"EMI 是厂家约定的固定物理窗、落在 AP 镜像之前"这一设计意向自洽**。
2. 但地址仍 **< 0x80100000 的真实物理基址**，且 `.text`/`.data` 的虚拟地址被固定在 0xC0100000 等（`__virt_to_phys` 只减 0xC0000000，不能再偏）→ 内核**无法把自己挪到 0x800D5C00 以上**去给这个窗让位。所以"用 no-map 把 0x80080000 那 343 KiB 从内核手里划走"在这颗 SoC 上是**结构性别扭**的；搬窗才是干净解法，而 Z1 已经把窗搬好了。

> 附带发现（供维护者留意，非本次裁决必需）：正因为填充区被算进"内核 section"，`kernel_sec_end = round_up(__pa(_end), SECTION_SIZE)`（`arch/arm/mm/mmu.c:1760`）会把 kernel 映射区下界压到 0x80000000，从而**把 4 MiB 段内位于 `_text` 之前的 RAM（0x80000000..0x80008000）卷进 `memblock_reserve`**。这与 8-14 之前"EMI 在 0x80000000 附近会 shadow 低 RAM"的历史现象方向一致，也与 `/proc/iomem` 只显示 `80000000-bf3fffff` 相符。**不建议**为这 2 MiB 去动 `SECTION_SIZE`/`CONFIG_ARM_MPU` —— 高风险、非本议题。

---

## 6. A 版 vs B 版逐项判定

| 路线 | 动作 | 上游契约 | 上板预判 | 风险 | 我的判定 |
|---|---|---|---|---|---|
| **A**（上游 0x80080000 + 补 no-map dts） | 改 dts 加 `0x80080000..0x800d5c00` no-map + **重编 dtb** | ✓ 符合上游 | ioremap 应能成功，但把活跃 RAM 挖走 | 与 Z1 LK/preloader 低 RAM 冲突（dts 注释明示）；需重编 dtb 并重新校准 initrd-sha 校验链；**推翻了 8-14 的实证结论** | **否决** |
| **B**（改基址 0xbe080000） | 换 `mtk_stp_wmt_soc.ko`（无需动 dts/dtb） | ✗ 上游无此值（但 Z1 已自定布局） | **已实测成功（8-14 完整链路）** | 低：仅 2 条 `movt` 指令差；carveout/dts reg[1]/宏 三处已数值闭环 | **采纳** ★ |
| 第三种 | 把 `CONSYS_EMI_FW_PHY_BASE`(0xf0080000) 也一起迁 | — | 无必要 | 32 位表达不了 0xbe080000+0xf0000000；且 8-14 已证明 FW 侧无需迁 | **不需要** |

**为什么 B 不需要动 dts/dtb**：B 的目标 0xBE080000 已落在**现网已交付**的 `connsys@be000000` no-map carveout 内 —— 我已从 R5 `boot.img` 内 DTB 反编译确认该 carveout 与 `connsys reg[1]` **都在**（§4）。所以 **R5 的 dtb 无需任何修改**，只换一枚 .ko 即可。

**B 的正确性论证（三层，互不依赖）**：
1. **板上实证**：8-14 该值跑通完整 WiFi（§3）。
2. **机理**：0xBE080000 是 no-map → `memblock_is_map_memory()` = false → ioremap 的 RAM 检查不触发（§1）。
3. **物理自洽**：MT6572 的共享 EMI 是普通物理 RAM 窗，AP 映射到哪里 FW 就看到哪里（FW 侧 0xf0080000 是 MCU 自己的视图，与 AP 侧基址无固定换算）；A 版在 Z1 上被 RAM 检查拦下正是"它落在 AP RAM 里"的必然结果（§5）。

---

## 7. 可执行动作

### 7.1 编什么 / 要不要重编

**不需要重编，也不需要网络。** conn_soc 全量源码树与网络均不可用（已核实），但**所需的 B 版 .ko 已在本机**，且 A/B 字节级 diff 证明两者**只差 build-id + 2 条 `movt` 指令**：

```
len A == len B == 373204
differing bytes: 24，仅 3 段：
  off 0x000044..0x000057  build-id
  off 0x01e8f5..0x01e8f6  A=0048  B=0e4b     ← movt r0,#0x8008 → #0xbe08 (init)
  off 0x01e999..0x01e99a  A=0048  B=0e4b     ← 同上 (restore, hibernate 路径)
```
即 **B = A 的单宏替换**，语义干净，无其它改动。

`objdump` 复核（B 版 `mtk_wcn_consys_hw_init`）：
```
1e660: e34b0e08  movt r0, #48648   @ 0xbe08      ← ioremap(0xbe080000, 0x55c00)
1e668: ebfffffe  bl   0 <ioremap>
```

### 7.2 产物（已就位）

目录 `out/z1_wmt_emi_stage/`：

| 文件 | sha256 | 说明 |
|---|---|---|
| `mtk_stp_wmt_soc.ko` | `c9c62c26bc6027982f522ccba99ce0183ce976506e6432a311c682b895c7b07e` | ★ **推荐换入件**（Oct-28 B 版；与当前 HEAD 同轮源码编出） |
| `mtk_stp_wmt_soc.ko.A_ref` | `c2be5759778fbb976d796f4b3cc6ef407de89e0f411cc7760acd6e936d5b02cf` | A 版对照（上游原值，176 B 以上为唯一差异） |
| `r5_extracted/r5_mtk_stp_wmt_soc.ko` | `786e9ab4d9ba0905be2b98f9d908ba9aeb196e69ebf58ec79a529fd1fc14ae30` | 从 R5 交付件取回的**现网 A 版**（与 `out/z1_wifi_stage/...` 逐字节相同）—— 换入前的回滚件 |

**备用（若 Oct-28 B 因 ABI 不符被内核拒收）**：`out/alpine_rootfs/lib/modules/7.0.0-rc7-z1+/mtk_stp_wmt_soc.ko`，sha256 `90c9dc9eee841c2d48224c36814ab3248bb0ac315c7f89b9a9cc91be1503ec5c`，这是 **8-14 成功那一轮的确切同源件**（8-16 装进 Alpine rootfs）。

### 7.3 验证结果（`modinfo` / `nm`）

**vermagic（两枚均正确）**：
```
7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8      ← 与在役 .ko 一致
```
> `CONFIG_MODVERSIONS is not set`（**[源码实证]**）= **无 MODVERSIONS CRC 检查**，因此 ABI 兼容性由 vermagic + 符号可解析性决定，风险低。

**未定义符号：A 与 B 的未定义符号集合完全相同**（`diff` 空输出）→ B 换入不会引入新依赖。

**`mtk_wcn_*` 依赖可满足性**：
```
U mtk_btif_rxd_be_blocked_flag_get   ┐
U mtk_wcn_btif_close                 │ 全部由镜像内既有 mtk_btif_drv.ko 提供
U mtk_wcn_btif_open                  │ (反汇编确认 T mtk_wcn_btif_open @0x5780 等)
U mtk_wcn_btif_write                 ┘
```
（`.ko` 的 `depends:` 字段为空是**上游原本如此** —— 该栈历史上靠显式 insmod 顺序 + launcher 加载，非本次引入。）

**两处版本漂移（均已验证可解析，非阻断）**：Oct-28 B 比 Aug-16 备用件多依赖 `kernel_read_file_from_path_initns` / `irq_get_irqchip_state`，少依赖 `kernel_read`。当前内核 `Module.symvers` 中 `kernel_read`(1589, `EXPORT_SYMBOL`)、`kernel_read_file_from_path_initns`(2098, `EXPORT_SYMBOL_GPL`)、`irq_get_irqchip_state` 均在 → 两者都能解析；license 双方均 `GPL`，GPL-only 符号可用。

### 7.4 需改的 dts

**不需要改**（§6 已论证：B 的目标窗已落在现网 carveout 内，`connsys reg[1]` 已是 0xbe080000）。
**initrd-end / reserved-memory 不受影响**：不重编 dtb、不动 ramdisk → `linux,initrd-end = <0x842e2a64>` 与 ramdisk 的 sha 校验链**完全不动**。

### 7.5 换入方式

改 `out/z1_r5_system/system.img.r5d_final` 里的 `/lib/modules/z1-radio/mtk_stp_wmt_soc.ko`（inode 273，368556 B）。**我不动该交付件**（受纪律约束）；替换由主会话在确认后执行，例如：

```
# 备份
cp out/z1_r5_system/system.img.r5d_final out/z1_wmt_emi_stage/system.img.r5d_final.bak_$(date +%Y%m%d_%H%M%S)
# debugfs 替换（写前先 rm 再 write，保证大小变化安全）
debugfs -w -R "rm /lib/modules/z1-radio/mtk_stp_wmt_soc.ko"            out/z1_r5_system/system.img.r5d_final
debugfs -w -R "write out/z1_wmt_emi_stage/mtk_stp_wmt_soc.ko /lib/modules/z1-radio/mtk_stp_wmt_soc.ko" out/z1_r5_system/system.img.r5d_final
# 复盘（必须能 dump 出同 sha）
debugfs -R "dump /lib/modules/z1-radio/mtk_stp_wmt_soc.ko /tmp/chk.ko" out/z1_r5_system/system.img.r5d_final && sha256sum /tmp/chk.ko
```
> ⚠️ 历史坑（项目 CLAUDE.md/记忆已记）：**磁盘满会让 debugfs/编译静默失败**。替换前后各跑一次 `df -h /`（当前 7.4 G 可用，够）。

**更小步的替代**：若想只换 .ko 不碰 system.img，可在板上从 ramdisk/rootfs 覆盖该路径（Alpine 路线对应 `out/alpine_rootfs/lib/modules/...`，但那已是 B 版，无需动）。

---

## 8. 上板判决实验（单变量）

**变量**：只换 `mtk_stp_wmt_soc.ko`（A → B）。**dts / dtb / boot.img / ramdisk / 其余 .ko 全部不动。**

### Step 0 —— 前置条件（不满足则实验无效）
- `df -h /` ≥ 3 G 后替换，替换后 **dump 回读 sha256 必须 = `c9c62c26…b07e`**（防静默失败）。
- 确认交付 DTB 内仍有 `connsys@be000000 no-map` 与 `connsys reg[1] = <0xbe080000 0x55c00>`（我已核过 R5 的，若改用其它 boot.img 需重核）。

### Step 1 —— 加载模块，抓这一段 log
先起抓取（**Z1 拔 USB 才能正常开机；C3 桥 USB-CDC 全程在**）：
```
cd /home/lxj/claude/6572 && ./z1_capture_mainline.sh 600
```
板上：
```
sh /root/connsys/z1_modprobe_connsys.sh     # insmod 顺序：cfg80211 → mtk_btif_drv → mtk_stp_wmt_soc → mtk_stp_bt_soc → mtk_wmt_wifi_soc → wlan_gen2
```

### Step 2 —— 判据（三条 grep）

**① 失败指纹必须消失**：
```
grep -a -c "EMI mapping fail"                    # 期望 0   ← 唯一必须为 0 的硬指标
grep -a -c "arch/arm/mm/ioremap.c:318"           # 期望 0
grep -a -c "__arm_ioremap_pfn_caller"            # 期望 0
```
**不要等 `EMI mapping OK`** —— 该字符串在全部 .ko 里都不存在（§3 实测），成功是**静默**的。

**② 链路推进到固件下载 / func-on**（判成功的主证据）：
```
grep -a -nE "MTK-BTIF\[E\]hal_btif_clk_get_and_prepare"   # BTIF 时钟准备（8-14 成功日志 533-534 行有）
echo 1 > /dev/wmtWifi
grep -a -nE "ChipID = 0x6572|Open FW image done|WMT turn on WIFI success|wlan0" 
```
- **成功**：`ChipID = 0x6572` → `Open FW image done, <n> bytes` → `WMT turn on WIFI success!` → `wlan0` 出现（并可见 `MAC address: 20:72:0d:39:0d:1f`）。
- 8-14 参考值：`Open FW image done, 160464 bytes`。

**③ 端到端确认**：
```
ifconfig wlan0 up
iw dev wlan0 scan 2>&1 | grep -a "^BSS" | wc -l     # 期望 >0（8-14 实测 12）
```

### Step 3 —— 失败分叉表

| 现象 | 判定 | 下一步（单变量，一次只动一个） |
|---|---|---|
| `EMI mapping fail` 仍在 | 换入未生效（还是 A 版） | 板上 `modinfo -F vermagic`/反查二进制那 2 字节；重新确认 dump 回读 sha。**不是 B 的问题** |
| `EMI mapping fail` 消失，但**卡在 BTIF**（`hal_btif_dma_hw_init` Oops / base NULL） | EMI 已过，BTIF 成为新卡点 | 查 dts `btif@1100e000` 的**空 `ranges;`** 是否在（8-14 修复②；这是我 8-14 成功链的另一半）。注意 in-tree 的 `mt6572-s6.dts` 已有 `ranges`，确认 Z1 交付 dtb 也有 |
| EMI 过，卡在 `nicAllocateAdapterMemory: Allocating 12528` | 8-14 已记录的"func-on 偶发不稳定"（固件下载/握手时序敏感） | 重启再试一次（8-14 文档：`重启后第二次 func-on 卡在 ...12528`，**成功过一次即可复现路径**）；仍失败则转向 STP/HIF 时序，与 EMI 无关 |
| EMI 过，`ChipID = 0x6572` 出但 `Open FW image` 不出 | 固件/配置缺失 | 查 `WMT_SOC.cfg` + `WIFI_RAM_CODE` 是否内置（8-14 修复④：`/system/etc/firmware/`） |
| EMI 过，链路全通但 scan 空白 | 射频/NVRAM | 查 NVRAM（MAC 应为 `20:72:0d:39:0d:1f`）与 `cannot get HIF clk_wifi_dma clock`（8-14 已知非致命） |
| 完全无法 insmod（`Invalid module format`） | vermagic/ABI 不符 | 改用 §7.3 的**备用 Aug-16 B 版**（`90c9dc9e…ec5c`）——它是 8-14 成功同源件 |

---

## 9. 未决风险

1. **上游注释原文无法复核**（`mtk_wcn_consys_hw.c:604-608` "Reserved memory by device tree!"）：conn_soc 源树已清理、网络不通 → **[未验证]**。**但该注释属上游生态，不影响 Z1 裁决**（Z1 已搬窗并实测跑通）。若日后恢复网络，可取 `gabin8/mt6572-connsys-modules` 复核行号。
2. **`CONSYS_EMI_FW_PHY_BASE = 0xf0080000` 未随迁** —— 前人列为"最大隐患"。**我的判定：实测已证伪**（8-14 完整成功）。残留不确定性仅在于"8-14 那次是否真的用到了 EMI 窗内的 FW 握手数据"：`Open FW image done` 证明**固件镜像下载**成功（走 STP/BTIF，不经 EMI 窗），而 EMI 窗主要承载 **coredump / paged trace**（343 KB = "coredump space"）。所以严格说：**EMI 窗的 FW 侧数据通路在 8-14 未被充分施压**。若将来要做 CONN coredump 抓取（`CONSYS_EMI_PAGED_TRACE_OFFSET` 等），需重新验证 FW 侧视图。**这不影响 WiFi 可用性。**
3. **回归为何发生**（A 版为何进了 10-06 的 R5 交付）**未能定位**：外编 .ko 不入 git，我查不到当时的构建记录；`bridge_backups/products/products_connsys_v7c_20261026_203530` 是另一个 v7c 线，未展开核对。**建议**：给外编产物补一条 sha 清单 + 源宏值记录，避免同类静默回退再发生。
4. **"EMI mapping OK" 判据陷阱**：debug 级日志被编译掉，成功静默。已写入 §8 Step 2，但这是**文档层面**的风险 —— 后续任何"期待 OK 行"的验收脚本都会假失败。
5. **两处地址巧合值得将来留意**（本次不动）：`kernel_sec_end` 会把 kernel 映射下界压到 0x80000000（把 `_text` 前 2 MiB 卷进 reserve），以及 EMI 窗恰好落在 Image 链接器填充区。两者都提示"MT6572 EMI 窗与内核镜像低区的关系很脆弱"——**这正是 Z1 把窗搬到 0xbe000000 的正当理由**，不要为了"贴合上游"再搬回去。

---

## 10. 本次改动清单

**新建**（仅在 `out/z1_wmt_emi_stage/`，未触碰任何既有交付件）：

| 文件 | 说明 |
|---|---|
| `out/WMT_EMI_RULING.md` | 本报告 |
| `out/z1_wmt_emi_stage/mtk_stp_wmt_soc.ko` | 推荐换入件（Oct-28 B 版，sha `c9c62c26…b07e`） |
| `out/z1_wmt_emi_stage/mtk_stp_wmt_soc.ko.A_ref` | A 版对照（sha `c2be5759…b02cf`） |
| `out/z1_wmt_emi_stage/r5_extracted/r5_mtk_stp_wmt_soc.ko` | 从 R5 交付取回的现网 A 版（回滚件，sha `786e9ab4…ae30`） |

**只读访问、未修改**：`out/z1_r5_system/system.img.r5d_final`（debugfs 仅 `ls`/`dump`）、`out/z1_r5c_boot/boot.img`、`out/z1_wifi_stage/`、`out/alpine_rootfs/`、`fork_linux/`、`mainline_recon/`、`bridge_backups/`。

**未做**：未编译（无需，且源码/网络均不可用）、未刷写、未碰设备、未改 dts、未改任何映像。
