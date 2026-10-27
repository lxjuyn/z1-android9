# 参考仓 gabin8/mt6572-connsys-modules 契约摘录（2026-10-28 核查）

来源：`git clone --depth 1 https://github.com/gabin8/mt6572-connsys-modules.git`（main 分支，
clone 于 2026-10-28，raw.githubusercontent.com 直连被墙、api.github.com 与 github.com 443 可达）。
本文件所有行号以该克隆为准。

## 1. EMI 基址：硬编码，不是 dts reg

`conn_soc/mt6572/include/mtk_wcn_consys_hw.h:163-168`：

```c
/* EMI part mapping & ctrl*/
#define KBYTE								(1024*sizeof(char))
#define CONSYS_EMI_AP_PHY_OFFSET			(0x80000)
#define CONSYS_EMI_AP_PHY_BASE				(0x80080000)
#define CONSYS_EMI_FW_PHY_BASE				(0xf0080000)
#define CONSYS_EMI_MEM_SIZE					(343*KBYTE)	/*coredump space , 343K is enough */
```

`conn_soc/mt6572/mtk_wcn_consys_hw.c`（mtk_wcn_consys_hw_init 内，632-666 行）：

```c
	gConEmiPhyBase = CONSYS_EMI_AP_PHY_BASE - CONSYS_EMI_AP_PHY_OFFSET;   /* = 0x80000000 */
	...
		(void)addrPhy; /* no CONSYS EMI remap register on mt6572 */

#if 1
		pEmibaseaddr = ioremap(gConEmiPhyBase + CONSYS_EMI_AP_PHY_OFFSET, CONSYS_EMI_MEM_SIZE);
#else
		pEmibaseaddr = ioremap(CONSYS_EMI_AP_PHY_BASE, CONSYS_EMI_MEM_SIZE);
#endif
		if (pEmibaseaddr) {
			WMT_PLAT_INFO_FUNC("EMI mapping OK(0x%p)\n", pEmibaseaddr);
			memset_io(pEmibaseaddr, 0, CONSYS_EMI_MEM_SIZE);
			iRet = 0;
		} else {
			WMT_PLAT_ERR_FUNC("EMI mapping fail\n");       /* ← hw.c:663，R3c 板上日志行号完全一致 */
		}
```

即 `ioremap(0x80000000 + 0x80000) = ioremap(0x80080000, 0x55c00)`，成功后 `memset_io` 清零
343KB。`mtk_wcn_consys_hw_restore()`（632 行段，CONFIG_MTK_HIBERNATION 才注册）同宏。
DT 侧驱动只 `of_iomap(node, 0/1)` 取 MCU（0x18070000）与 TOPCKGEN（0x10000000）两个 reg 段，
**EMI 基址不来自 dts reg，dts 里没有可覆盖入口**。
`gConEmiPhyBase` 仅用于 ioremap 地址计算与 debug 打印，不写给 CONSYS 寄存器（全仓 grep 证据）。

## 2. 配套 DT 契约：reserved-memory no-map（"Reserved memory by device tree!"）

`conn_soc/mt6572/mtk_wcn_consys_hw.c:604-608`（mtk_wcn_consys_hw_init 上方注释）：

```c
/*Reserved memory by device tree!*/
/* mt6572: EMI diagnostics window is at a fixed AP-phys address that the
 * FW-side base (0xf0080000) falls through to; there is no remap register
 * on this chip and the region lies outside kernel-managed RAM. */
```

三句话即契约：(a) 该窗由 device tree 保留；(b) FW 侧固定从 0xf0080000 透视到 AP 物理
0x80080000（片上无 remap 寄存器，`CONSYS_EMI_MAPPING_OFFSET 0x310` 在本驱动未被使用）；
(c) 窗必须"lies outside kernel-managed RAM"——即 dts reserved-memory no-map，
否则 mainline arm `__arm_ioremap_pfn_caller`（ioremap.c:318 的 pfn_valid 拦截）拒绝映射。
参考平台 Prestigio PAP5500 DUO（同为 MT6572）在该组合下 BT/WiFi/FM/GPS 全部 working
（README 状态表）。

## 3. DT 节点前提（README "The kernel tree the modules build against must provide"，50-80 行）

- `connectivity@18070000`（`mediatek,mt6572-consys`）：MCU + TOPCKGEN 两个 reg 段、
  conn2ap + WDT 中断、CONN power domain、TOPRGU connsys reset、四个 vcn* 供电。
  （Z1 dts 该节点已齐备，含 reg 两段 0x18070000/0x1000 + 0x10000000/0x2000。）
- `btif@1100e000`、`wifi@180f0000` 同 README。
- 无 dts 文件随仓分发——DT 契约就是上面两节文字。

## 4. wmt_dbg quick-sleep（README BT 前提段 250-260 行）

> 2. **Quick-sleep is disabled** (`echo '1 0' > /proc/driver/wmt_dbg`): the
>    driver defaults to the Android screen-off policy (immediate re-sleep
>    after every wake) because the notifiers that would clear it don't exist
>    on mainline. The firmware does not survive that policy under load.

驱动侧语义（conn_soc/linux/pri/wmt_dev.c）：命令表 `[1] = wmt_dbg_quick_sleep_ctrl`
（340 行）；实现（388-393 行）`en_flag = par2; wmt_lib_quick_sleep_ctrl(en_flag)` ——
par1=1 选 quick-sleep 控制、par2=0 = 禁用。

## 5. 构建契约（README "Building" 段）

`make KDIR=/path/to/tree`，产物 `conn_soc/mtk_stp_wmt_soc.ko` 等 7 个 .ko；
wlan_gen2 需先编 cfg80211 并喂 `net/wireless/Module.symvers`（conn_soc 不需要）。
README 自述面向 linux-next 7.2.0-rc4-next-20260721，旧树需回退三个 API
（cfg80211 wireless_dev 参数、eth_hw_addr_set、kernel_read_file_from_path_initns）。
